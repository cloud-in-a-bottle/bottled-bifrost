import subprocess

import httpx
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


def test_web_ui_loads_without_auth_gate(stack: OpenhostStack, page: Page) -> None:
    # app_url hits the container directly — no OpenHost router, no auth header and
    # no permission grant. The Bifrost UI must still render, proving the app does
    # not gate the UI itself (OpenHost owner-auth does that in production).
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
    expected = f"/data/app_temp_data/{stack.manifest.app.name}/npm-cache"
    assert f"npm_config_cache={expected}" in env.splitlines()
    assert (stack.temp_data_dir / "npm-cache").is_dir()
