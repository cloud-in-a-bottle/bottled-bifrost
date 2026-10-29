// Supervises Bifrost, bootstraps its admin account, and fronts its web UI and
// management API so the OpenHost owner is signed in as that admin.
//
// Since v2, Bifrost's management API needs a real admin account: with none, it
// is locked behind a one-time setup token. OpenHost already authenticates the
// owner (the router sets X-OpenHost-Is-Owner), so on boot this script creates
// an admin with a random password (kept in the app data dir, 0600), signs in
// to Bifrost with it, and attaches that session to the owner's requests. Any
// Bifrost credential a non-owner request carries is stripped, so only the
// owner gets admin.
//
// Traffic: Caddy (:8080) -> this proxy (:3001) -> Bifrost (127.0.0.1:3000).
// /service/* (cross-app inference) goes from Caddy straight to Bifrost.
//
// No dependencies: plain Node, which the image has for STDIO MCP servers.

import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";

const BIFROST_HOST = "127.0.0.1";
const BIFROST_PORT = 3000;
const LISTEN_PORT = 3001;
const APP_DIR = required("APP_DIR");
const CREDENTIALS_FILE = path.join(APP_DIR, "openhost-admin.json");
const ADMIN_USERNAME = "openhost-admin";
// Bifrost credentials a client could send. Only the owner's requests may carry
// one, and those get ours.
const SESSION_COOKIES = new Set(["token", "bifrost_setup_session"]);
const CREDENTIAL_HEADERS = ["authorization", "x-bifrost-setup-token", "x-bifrost-temp-token"];
// Owner request bodies up to this size are buffered so they can be replayed
// after re-signing in on a 401; larger ones are streamed without a retry.
const MAX_REPLAY_BODY = 8 * 1024 * 1024;

function required(name) {
	const value = process.env[name];
	if (!value) {
		console.error(`owner-proxy: ${name} is not set`);
		process.exit(1);
	}
	return value;
}

function log(message) {
	console.log(`owner-proxy: ${message}`);
}

// The app's public origin, which Bifrost should accept browser requests from.
// The zone domain carries the router's port on dev and harness routers;
// *.localhost zones are served over plain http.
function publicOrigin() {
	const zone = process.env.OPENHOST_ZONE_DOMAIN;
	if (!zone) return null;
	const appName = process.env.OPENHOST_APP_NAME || "bifrost-llm-gateway";
	const hostname = zone.split(":")[0];
	const scheme = hostname === "localhost" || hostname.endsWith(".localhost") ? "http" : "https";
	return `${scheme}://${appName}.${zone}`;
}

// --- Bifrost process ------------------------------------------------------

let bifrost = null;
let shuttingDown = false;

function startBifrost(extraEnv = {}) {
	const child = spawn("/app/docker-entrypoint.sh", ["/app/main"], {
		env: { ...process.env, ...extraEnv },
		stdio: "inherit",
	});
	child.on("exit", (code, signal) => {
		if (child !== bifrost || shuttingDown) return;
		console.error(`owner-proxy: bifrost exited (code ${code}, signal ${signal}); exiting`);
		shutdown(1);
	});
	bifrost = child;
}

function stopBifrost() {
	const child = bifrost;
	bifrost = null;
	if (!child || child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
	return new Promise((resolve) => {
		const timer = setTimeout(() => child.kill("SIGKILL"), 30_000);
		child.once("exit", () => {
			clearTimeout(timer);
			resolve();
		});
		child.kill("SIGTERM");
	});
}

async function restartBifrost(extraEnv = {}) {
	await stopBifrost();
	startBifrost(extraEnv);
	await waitHealthy();
}

async function waitHealthy() {
	const deadline = Date.now() + 180_000;
	while (Date.now() < deadline) {
		try {
			if ((await bifrostFetch("GET", "/health")).status === 200) return;
		} catch {
			// not listening yet
		}
		await sleep(500);
	}
	throw new Error("bifrost did not become healthy");
}

async function shutdown(code) {
	shuttingDown = true;
	await stopBifrost();
	const caddyPid = Number(process.env.CADDY_PID);
	if (caddyPid) {
		try {
			process.kill(caddyPid, "SIGTERM");
		} catch {
			// already gone
		}
	}
	process.exit(code);
}

process.on("SIGTERM", () => shutdown(0));
process.on("SIGINT", () => shutdown(0));

// --- Bifrost API ----------------------------------------------------------

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function bifrostFetch(method, urlPath, { body, headers = {} } = {}) {
	const init = { method, headers: { ...headers }, signal: AbortSignal.timeout(30_000) };
	if (body !== undefined) {
		init.body = JSON.stringify(body);
		init.headers["content-type"] = "application/json";
	}
	return fetch(`http://${BIFROST_HOST}:${BIFROST_PORT}${urlPath}`, init);
}

async function bifrostJSON(method, urlPath, options) {
	const response = await bifrostFetch(method, urlPath, options);
	const text = await response.text();
	if (!response.ok) throw new Error(`${method} ${urlPath}: ${response.status} ${text.slice(0, 300)}`);
	return text ? JSON.parse(text) : {};
}

function readCredentials() {
	try {
		const { username, password } = JSON.parse(fs.readFileSync(CREDENTIALS_FILE, "utf8"));
		if (username && password) return { username, password };
	} catch (error) {
		if (error.code !== "ENOENT") log(`can't read ${CREDENTIALS_FILE}: ${error.message}`);
	}
	return null;
}

function writeCredentials(credentials) {
	const tmp = `${CREDENTIALS_FILE}.tmp`;
	fs.writeFileSync(tmp, JSON.stringify(credentials), { mode: 0o600 });
	fs.chmodSync(tmp, 0o600);
	fs.renameSync(tmp, CREDENTIALS_FILE);
}

// Meets Bifrost's password policy: 12+ characters with upper, lower, digit and
// special characters.
function generatePassword() {
	return `${crypto.randomBytes(32).toString("base64url")}-Aa1`;
}

// --- Session --------------------------------------------------------------

let session = null; // { token, expires }
let loginInFlight = null;
let lastLoginFailure = 0;

async function login() {
	const credentials = readCredentials();
	if (!credentials) {
		log(`no admin credentials in ${CREDENTIALS_FILE}; the owner will see Bifrost's login page`);
		return null;
	}
	const response = await bifrostFetch("POST", "/api/session/login", { body: credentials });
	if (!response.ok) {
		log(`signing in as ${credentials.username} failed (${response.status}); the owner will see Bifrost's login page`);
		return null;
	}
	for (const cookie of response.headers.getSetCookie()) {
		const match = /^token=([^;]+)/.exec(cookie);
		if (!match) continue;
		const expiresMatch = /;\s*expires=([^;]+)/i.exec(cookie);
		const expires = expiresMatch ? Date.parse(expiresMatch[1]) : Date.now() + 24 * 3600_000;
		return { token: match[1], expires };
	}
	log("bifrost's login response set no session cookie");
	return null;
}

// Signs in again unless another request already replaced `stale`.
function refreshSession(stale) {
	if (session && session.token !== stale) return Promise.resolve(session.token);
	if (!loginInFlight) {
		loginInFlight = (async () => {
			if (Date.now() - lastLoginFailure < 5_000) return null;
			try {
				session = await login();
			} catch (error) {
				log(`signing in failed: ${error.message}`);
				session = null;
			}
			if (!session) lastLoginFailure = Date.now();
			return session?.token ?? null;
		})().finally(() => {
			loginInFlight = null;
		});
	}
	return loginInFlight;
}

async function currentSession() {
	await ready;
	// Sessions last 30 days; renew a day early rather than mid-request.
	if (session && session.expires - Date.now() > 24 * 3600_000) return session.token;
	return refreshSession(session?.token ?? null);
}

async function sessionIsValid(token) {
	try {
		const state = await bifrostJSON("GET", "/api/session/is-auth-enabled", {
			headers: { cookie: `token=${token}` },
		});
		return state.has_valid_token === true;
	} catch {
		return false;
	}
}

// --- Bootstrap ------------------------------------------------------------

// Fresh install: no config DB yet. Bifrost v2 turns inference auth on for new
// deployments, which would break the service interface's credential-less
// calls (Caddy's full_access gate is the auth there), so we turn it off.
// Upgraded instances keep whatever they had.
const freshInstall = !fs.existsSync(path.join(APP_DIR, "config.db"));

async function bootstrap() {
	startBifrost();
	await waitHealthy();
	let restart = false;

	const authState = await bifrostJSON("GET", "/api/session/is-auth-enabled");
	if (authState.setup_required) {
		// No admin account yet (fresh install, or the first boot after upgrading
		// from v1), or the admin's dashboard auth was switched off. Restart with a
		// one-time setup token, which is never stored, and use it to save our
		// admin and turn dashboard auth on.
		const setupToken = crypto.randomBytes(32).toString("base64url");
		await restartBifrost({ BIFROST_SETUP_TOKEN: setupToken });
		const headers = { "x-bifrost-setup-token": setupToken };
		const config = await bifrostJSON("GET", "/api/config", { headers });
		let credentials = readCredentials();
		if (!credentials) {
			credentials = { username: ADMIN_USERNAME, password: generatePassword() };
			writeCredentials(credentials);
		}
		const clientConfig = config.client_config ?? {};
		clientConfig.enforce_auth_on_inference = freshInstall ? false : Boolean(clientConfig.enforce_auth_on_inference);
		const origin = publicOrigin();
		if (origin) clientConfig.allowed_origins = [origin];
		await bifrostJSON("PUT", "/api/config", {
			headers,
			body: {
				client_config: clientConfig,
				framework_config: config.framework_config,
				auth_config: {
					admin_username: credentials.username,
					admin_password: credentials.password,
					is_enabled: true,
					setup_token: setupToken,
				},
			},
		});
		log(config.auth_config ? "re-enabled dashboard auth for the admin account" : "created the admin account");
		// Drop the setup token from Bifrost's environment (STDIO MCP servers
		// inherit it), and apply the allowed origins.
		restart = true;
	}

	session = await login();
	if (session) {
		const origin = publicOrigin();
		const headers = { cookie: `token=${session.token}` };
		const config = await bifrostJSON("GET", "/api/config", { headers });
		const current = config.client_config?.allowed_origins ?? [];
		if (origin && !(current.length === 1 && current[0] === origin)) {
			// Bifrost reads allowed origins for WebSockets at startup, so restart.
			await bifrostJSON("PUT", "/api/config", {
				headers,
				body: {
					client_config: { ...config.client_config, allowed_origins: [origin] },
					framework_config: config.framework_config,
				},
			});
			log(`set allowed origins to ${origin}`);
			restart = true;
		}
	}
	if (restart) await restartBifrost();
}

const ready = bootstrap().catch((error) => {
	// Keep serving: the owner can still sign in to Bifrost by hand.
	console.error(`owner-proxy: bootstrap failed: ${error.stack || error}`);
	if (!bifrost) startBifrost();
});
let isReady = false;
ready.then(() => {
	isReady = true;
	log("ready");
});

// --- Proxy ----------------------------------------------------------------

function isOwner(req) {
	return req.headers["x-openhost-is-owner"] === "true";
}

// The request's headers with every client-sent Bifrost credential removed, and
// for the owner, our session cookie added.
function upstreamHeaders(req, token) {
	const headers = { ...req.headers };
	const owner = isOwner(req);
	if (!owner) for (const name of CREDENTIAL_HEADERS) delete headers[name];
	const cookies = (headers.cookie ?? "")
		.split(";")
		.map((c) => c.trim())
		.filter((c) => c && !SESSION_COOKIES.has(c.split("=")[0].trim()));
	if (owner && token) cookies.push(`token=${token}`);
	if (cookies.length) headers.cookie = cookies.join("; ");
	else delete headers.cookie;
	return headers;
}

function forward(req, headers, body) {
	return new Promise((resolve, reject) => {
		const upstream = http.request({ host: BIFROST_HOST, port: BIFROST_PORT, method: req.method, path: req.url, headers }, resolve);
		upstream.on("error", reject);
		if (body === undefined) req.pipe(upstream);
		else upstream.end(body);
	});
}

function readBody(req) {
	return new Promise((resolve, reject) => {
		const chunks = [];
		req.on("data", (chunk) => chunks.push(chunk));
		req.on("end", () => resolve(Buffer.concat(chunks)));
		req.on("error", reject);
	});
}

async function handle(req, res) {
	const urlPath = req.url.split("?")[0];
	// Bifrost restarts during the admin bootstrap: report unhealthy until it's
	// done, and hold other requests until then.
	if (urlPath === "/health" && !isReady) {
		res.writeHead(503, { "content-type": "application/json" });
		res.end('{"status":"starting"}');
		return;
	}
	await ready;
	const owner = isOwner(req);
	let token = owner ? await currentSession() : null;
	if (owner && token && urlPath === "/api/session/is-auth-enabled" && !(await sessionIsValid(token))) {
		// eg after the owner pressed "log out" in Bifrost's UI.
		token = await refreshSession(token);
	}

	const length = Number(req.headers["content-length"] ?? 0);
	const replayable = owner && urlPath.startsWith("/api/") && !req.headers["transfer-encoding"] && length <= MAX_REPLAY_BODY;
	const body = replayable ? await readBody(req) : undefined;

	let upstream = await forward(req, upstreamHeaders(req, token), body);
	if (replayable && token && upstream.statusCode === 401 && !(await sessionIsValid(token))) {
		upstream.resume();
		token = await refreshSession(token);
		upstream = await forward(req, upstreamHeaders(req, token), body);
	}
	if (owner && urlPath === "/api/session/logout") session = null;

	const responseHeaders = { ...upstream.headers };
	delete responseHeaders.connection;
	delete responseHeaders["keep-alive"];
	res.writeHead(upstream.statusCode, upstream.statusMessage, responseHeaders);
	upstream.pipe(res);
}

const server = http.createServer((req, res) => {
	handle(req, res).catch((error) => {
		log(`${req.method} ${req.url.split("?")[0]}: ${error.message}`);
		if (!res.headersSent) {
			res.writeHead(502, { "content-type": "application/json" });
			res.end('{"error":"bad_gateway"}');
		} else {
			res.destroy();
		}
	});
});

// WebSockets (the dashboard's live logs): pass the upgrade through with the same
// header treatment, then splice the sockets.
server.on("upgrade", async (req, socket, head) => {
	socket.on("error", () => socket.destroy());
	try {
		await ready;
		const token = isOwner(req) ? await currentSession() : null;
		const headers = upstreamHeaders(req, token);
		const upstream = net.connect(BIFROST_PORT, BIFROST_HOST, () => {
			const lines = [`${req.method} ${req.url} HTTP/1.1`];
			for (const [name, value] of Object.entries(headers)) {
				for (const v of Array.isArray(value) ? value : [value]) lines.push(`${name}: ${v}`);
			}
			upstream.write(`${lines.join("\r\n")}\r\n\r\n`);
			if (head.length) upstream.write(head);
			upstream.pipe(socket);
			socket.pipe(upstream);
		});
		upstream.on("error", () => socket.destroy());
		socket.on("close", () => upstream.destroy());
	} catch {
		socket.destroy();
	}
});

server.listen(LISTEN_PORT, "127.0.0.1", () => log(`listening on 127.0.0.1:${LISTEN_PORT}`));
