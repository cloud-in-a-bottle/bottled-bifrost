import json
import socket
import subprocess
import time

import httpx
import requests
from openhost_test_harness import OpenhostStack
from playwright.sync_api import Page
from playwright.sync_api import expect

# A service call's permissions header, as the OpenHost router injects it.
FULL_ACCESS = {"X-OpenHost-Permissions": '[{"grant": "full_access", "scope": "global"}]'}
WRONG_GRANT = {"X-OpenHost-Permissions": '[{"grant": "read_only", "scope": "global"}]'}

# Bifrost's OpenAI-compatible "list models" route, behind the service endpoint.
SERVICE_MODELS = "/service/openai/v1/models"


def test_health_ok(stack: OpenhostStack) -> None:
    response = httpx.get(f"{stack.app_url}/health")
    assert response.status_code == 200


def test_service_denied_without_grant(stack: OpenhostStack) -> None:
    response = httpx.get(f"{stack.app_url}{SERVICE_MODELS}")
    assert response.status_code == 403
    assert response.json()["error"] == "permission_required"


def test_service_denied_with_wrong_grant(stack: OpenhostStack) -> None:
    response = httpx.get(f"{stack.app_url}{SERVICE_MODELS}", headers=WRONG_GRANT)
    assert response.status_code == 403
    assert response.json()["error"] == "permission_required"


def test_service_allowed_with_full_access(stack: OpenhostStack) -> None:
    response = httpx.get(f"{stack.app_url}{SERVICE_MODELS}", headers=FULL_ACCESS)
    # The gate passes the request to Bifrost's OpenAI-compatible API.
    assert response.status_code == 200
    assert "data" in response.json()


def test_service_does_not_expose_web_ui(stack: OpenhostStack) -> None:
    # Even with full_access, the service interface exposes only the inference API
    # — never the web UI root or the management API.
    for path in ("/service/", "/service/api/providers"):
        response = httpx.get(f"{stack.app_url}{path}", headers=FULL_ACCESS)
        assert response.status_code == 404, path


def test_service_inference_needs_no_bifrost_credential(stack: OpenhostStack) -> None:
    # The full_access grant is the only auth on the service interface: Bifrost v2
    # turns inference auth on for new installs, and owner-proxy.mjs turns it off
    # again. Bifrost checks the credential only once the provider exists; with no
    # usable key the call then fails, but not with a 401.
    response = stack.owner_session.post(f"{stack.url}/api/providers", json={"provider": "openai"}, timeout=30)
    assert response.status_code in (200, 409), f"{response.status_code}: {response.text[:500]}"
    response = httpx.post(
        f"{stack.app_url}/service/openai/v1/chat/completions",
        headers=FULL_ACCESS,
        json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        timeout=30,
    )
    assert response.status_code != 401, response.text[:500]


def test_owner_is_bifrost_admin(stack: OpenhostStack) -> None:
    # The owner lands in Bifrost signed in as its admin (owner-proxy.mjs attaches
    # the admin session), and can make management writes from the dashboard's
    # origin, which is in Bifrost's allowed origins.
    session = stack.owner_session
    origin = stack.url.rstrip("/")
    state = session.get(f"{stack.url}/api/session/is-auth-enabled", timeout=30).json()
    assert state["is_auth_enabled"] and state["has_valid_token"], state
    response = session.post(
        f"{stack.url}/api/providers", json={"provider": "mistral"}, headers={"Origin": origin}, timeout=30
    )
    assert response.status_code in (200, 409), f"{response.status_code}: {response.text[:500]}"
    config = session.get(f"{stack.url}/api/config", timeout=30).json()
    assert config["client_config"]["allowed_origins"] == [origin], config["client_config"]["allowed_origins"]


def test_non_owner_is_not_bifrost_admin(stack: OpenhostStack, container_name: str) -> None:
    # Only the owner gets admin: owner-proxy.mjs strips Bifrost credentials from
    # any other request, even valid ones. Through the router, anonymous requests
    # never reach the app (no public_paths); app_url skips the router, so these
    # requests carry no X-OpenHost-Is-Owner.
    credentials_file = f"/data/app_data/{stack.manifest.name}/openhost-admin.json"
    assert _in_container(container_name, f"stat -c %a {credentials_file}") == "600"
    credentials = json.loads(_in_container(container_name, f"cat {credentials_file}"))
    login = httpx.post(f"{stack.app_url}/api/session/login", json=credentials, timeout=30)
    assert login.status_code == 200, login.status_code
    attempts = {
        "no credential": {},
        "admin password": {"auth": (credentials["username"], credentials["password"])},
        "admin session": {"cookies": {"token": login.cookies["token"]}},
    }
    for name, kwargs in attempts.items():
        response = httpx.post(f"{stack.app_url}/api/providers", json={"provider": "cohere"}, timeout=30, **kwargs)
        assert response.status_code == 401, f"{name}: {response.status_code}"
    response = requests.post(
        f"{stack.url}/api/providers", json={"provider": "cohere"}, allow_redirects=False, timeout=30
    )
    assert not response.ok, response.status_code


def test_web_ui_loads_without_auth_gate(stack: OpenhostStack, page: Page) -> None:
    # app_url hits the container directly — no OpenHost router, no auth header and
    # no permission grant. The Bifrost UI must still render (as its login page:
    # only the owner is signed in), proving the app serves the UI itself and
    # OpenHost owner-auth gates it in production.
    page.goto(stack.app_url, wait_until="domcontentloaded")
    expect(page).to_have_title("Bifrost")
    expect(page.locator("#root")).not_to_be_empty()


def _in_container(container_name: str, command: str) -> str:
    result = subprocess.run(
        ["podman", "exec", container_name, "sh", "-c", command],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_npx_available_for_stdio_mcp_servers(stack: OpenhostStack, container_name: str) -> None:
    # Bifrost runs STDIO MCP servers by exec'ing their command (usually
    # `npx -y <server>`) inside this container, so it needs a JS runtime.
    assert _in_container(container_name, "command -v npx") == "/usr/bin/npx"


def test_npm_cache_lives_in_temp_data(stack: OpenhostStack, container_name: str) -> None:
    # start.sh points npm's cache at the temp data dir, which survives container
    # boots, so npx doesn't refetch MCP servers on every start. Read it off PID 1
    # (podman exec gets the image env, not start.sh's exports); every Bifrost
    # child process inherits the same env.
    assert stack.temp_data_dir is not None
    env = _in_container(container_name, "tr '\\0' '\\n' < /proc/1/environ")
    expected = f"/data/app_temp_data/{stack.manifest.name}/npm-cache"
    assert f"npm_config_cache={expected}" in env.splitlines()
    assert (stack.temp_data_dir / "npm-cache").is_dir()


def test_config_is_stored_in_app_data(stack: OpenhostStack) -> None:
    # start.sh points Bifrost's APP_DIR at the app-data dir, so providers and keys the
    # owner configures are backed up and survive reloads. Upstream's default is
    # /app/data inside the container, which is lost on every redeploy.
    session = stack.owner_session
    response = session.post(f"{stack.url}/api/providers", json={"provider": "groq"}, timeout=30)
    assert response.status_code in (200, 409), f"{response.status_code}: {response.text[:500]}"
    response = session.get(f"{stack.url}/api/providers", timeout=30)
    assert response.status_code == 200, f"{response.status_code}: {response.text[:500]}"
    names = [provider["name"] for provider in response.json()["providers"]]
    assert "groq" in names, names
    config_db = stack.data_dir / "config.db"
    assert config_db.is_file(), (
        f"{config_db} missing; app data dir has {sorted(p.name for p in stack.data_dir.iterdir())}\n"
        f"{stack.app_logs()[-3000:]}"
    )


# The upstream image this app shipped before Bifrost v2, for the upgrade test.
V1_IMAGE = "docker.io/maximhq/bifrost:v1.6.6@sha256:0c0e6f498944d2896867fd9a5e51a79bb9cc82b845763fc4ababe0a6d2ab4402"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_healthy(url: str, container: str) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=5).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    logs = subprocess.run(["podman", "logs", container], capture_output=True, text=True).stdout
    raise AssertionError(f"{container} never became healthy:\n{logs[-3000:]}")


def test_upgrade_from_v1_keeps_config_and_signs_owner_in(stack: OpenhostStack, container_name: str, tmp_path) -> None:
    # An instance set up on Bifrost v1 has config but no admin account. Its first
    # boot on this image must create the admin, keep the config, and sign the
    # owner in. Runs this app's image (as built by the harness) by hand on a copy
    # of v1's data dir, outside the router, so the owner header is supplied here.
    image = subprocess.run(
        ["podman", "inspect", "--format", "{{.Image}}", container_name], capture_output=True, text=True, check=True
    ).stdout.strip()
    data_dir = tmp_path / "app_data"
    temp_dir = tmp_path / "app_temp_data"
    data_dir.mkdir()
    temp_dir.mkdir()
    app_data = f"/data/app_data/{stack.manifest.name}"
    app_temp = f"/data/app_temp_data/{stack.manifest.name}"
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    v1 = f"{stack.container_name}-upgrade-v1"
    v2 = f"{stack.container_name}-upgrade-v2"
    common = ["-d", "-p", f"127.0.0.1:{port}:8080", "-v", f"{data_dir}:{app_data}:U"]
    try:
        # v1, run the way this app ran it: Bifrost on the container port, APP_DIR in app data.
        subprocess.run(["podman", "run", "--name", v1, *common, "-e", f"APP_DIR={app_data}", V1_IMAGE], check=True)
        _wait_until_healthy(url, v1)
        response = httpx.post(f"{url}/api/providers", json={"provider": "groq"}, timeout=30)
        assert response.status_code == 200, response.text[:500]
        subprocess.run(["podman", "stop", "-t", "30", v1], check=True, capture_output=True)

        subprocess.run(
            [
                "podman", "run", "--name", v2, *common, "-v", f"{temp_dir}:{app_temp}:U",
                "-e", f"OPENHOST_APP_DATA_DIR={app_data}", "-e", f"OPENHOST_APP_TEMP_DIR={app_temp}",
                "-e", f"OPENHOST_APP_NAME={stack.manifest.name}", "-e", f"OPENHOST_ZONE_DOMAIN=upgrade.localhost:{port}",
                image,
            ],
            check=True,
        )  # fmt: skip
        _wait_until_healthy(url, v2)
        owner = {"X-OpenHost-Is-Owner": "true"}
        state = httpx.get(f"{url}/api/session/is-auth-enabled", headers=owner, timeout=30).json()
        assert state["is_auth_enabled"] and state["has_valid_token"], state
        response = httpx.get(f"{url}/api/providers", headers=owner, timeout=30)
        assert response.status_code == 200, response.text[:500]
        assert "groq" in [provider["name"] for provider in response.json()["providers"]]
        # v1 left inference auth off; the upgrade keeps it that way.
        config = httpx.get(f"{url}/api/config", headers=owner, timeout=30).json()["client_config"]
        assert config["enforce_auth_on_inference"] is False, config
        assert httpx.get(f"{url}/api/providers", timeout=30).status_code == 401
    finally:
        subprocess.run(["podman", "rm", "-f", v1, v2], capture_output=True)
        # The data is owned by the container user; remove it from inside its user namespace.
        subprocess.run(["podman", "unshare", "rm", "-rf", str(data_dir), str(temp_dir)], capture_output=True)
