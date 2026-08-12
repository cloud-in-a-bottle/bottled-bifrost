# bifrost-llm-gateway

A Cloud in a Bottle app that runs the [Bifrost](https://github.com/maximhq/bifrost) LLM
gateway and exposes its OpenAI-compatible API to other apps as a Cloud in a Bottle
cross-app service.

## Before you start

Bifrost is a **router** for LLM inference, not a model itself. To make it
useful you configure it in the web UI:

- **Bring your own provider keys.** You must add your own upstream provider API
  keys (Anthropic, OpenAI, etc.) in Bifrost's web UI. The gateway forwards
  requests to those providers using the keys you supply; it does not include any
  model access of its own.
- **Some features require a Bifrost enterprise license.** A number of Bifrost's
  advanced features are gated behind a paid
  [Bifrost enterprise license](https://github.com/maximhq/bifrost). The core
  gateway — configuring providers and serving the OpenAI-compatible inference
  API to your other OpenHost apps — works without one.

## How it works

- **Bifrost** runs inside the container on loopback (`127.0.0.1:3000`), storing
  its config + sqlite DB + logs in the Cloud in a Bottle app-data dir (`[data].app_data`),
  so providers, keys, and logs persist across reloads. `start.sh` points
  Bifrost's `APP_DIR` at `$OPENHOST_APP_DATA_DIR`.
- **Caddy** fronts it on the container port (`:8080`):
  - `/health` — Cloud in a Bottle's health probe (ungated).
  - `/service/*` — the cross-app **service interface**. Requires a `full_access`
    grant in `X-OpenHost-Permissions` (injected by the Cloud in a Bottle router on service
    calls); without it, returns `403 permission_required`. It exposes **only**
    Bifrost's inference APIs (`/openai`, `/anthropic`, `/genai`) — the web UI and
    management API are not reachable through it (`404`). The `/service` prefix is
    stripped, so `/service/openai/v1/chat/completions` reaches Bifrost as
    `/openai/v1/chat/completions`.
  - everything else — Bifrost's **web UI + management API**, *not* gated by Caddy.
    The app declares no `public_paths`, so Cloud in a Bottle gates these to the logged-in
    owner.

The owner configures upstream providers and API keys in Bifrost's web UI;
consumer apps call the gateway through the router and never see those keys.

## The service

See [`services/openai-compat/`](services/openai-compat/README.md) for the
service spec: URL, version, the `full_access` permission grant, the available
OpenAI-compatible routes, and how to consume it.

## Development

```bash
just setup   # install dev deps + pre-commit hooks
just build   # build the container image
just run     # run locally on http://localhost:8080 (data persisted under ./data)
just test    # build the Dockerfile, run under podman + a mock router, run pytest
just check   # lint + format
```

Tests use `openhost-test-harness`, which builds the Dockerfile and runs the app
under **podman** (so podman must be running). They exercise the permission gate
directly via `stack.app_url` (the harness's mock router doesn't simulate service
calls, so the `X-OpenHost-Permissions` header is supplied by the test).
