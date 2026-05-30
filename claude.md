- read README.md and style_guide.md at the beginning of every session.
- on first init, run `just setup` — this installs the (python) dev/test dependencies and the pre-commit hooks. pre-commit runs ruff on commit.
- use uv for all python work (`uv run ...`, `uv add ...`, `uv sync`). do not use pip or pixi in this repo. note: python here is only for the test harness; the app itself is a container, not python.
- this is an OpenHost app. `openhost.toml` is the app manifest. the app wraps the upstream Bifrost LLM gateway (Go binary in the `maximhq/bifrost` image): bifrost runs on loopback :3000, and Caddy fronts it on :8080 (`Caddyfile`/`start.sh`). Caddy permission-gates the `/service/*` cross-app service interface behind a `full_access` grant and limits it to bifrost's inference APIs (the web UI/management API are NOT reachable via the service interface). the web UI itself is NOT gated by caddy — there are no public_paths, so openhost gates it to the owner. the service is registered as `openai-compat` at endpoint `/service/`. see the `openhost` skill for how to deploy/debug on OpenHost.
- tests use `openhost-test-harness`: each run builds the Dockerfile, runs the app under podman per `openhost.toml`, and fronts it with a mock router. so `just test` requires podman running on the host. `stack.url` goes through the router (owner auth injected); `stack.app_url` hits the container directly.
- please ask before doing anything that affects low level system stuff on this machine, or anything using sudo.
- readmes are human written. any ai-generated docs will be in files like readme_ai_generated.md. the ai-generated docs can be used for context but should *not* be considered necessarily up to date or as hard constraints on how the system should/must be built.


specific to this repo:
- if you run into any cases where the app test harness doesn't match the expected/real behavior of openhost, stop and mention this so that we can fix the test harness - don't just make some workaround to the issue.
- if you run into cases where openhost itself doesn't behave as expected, also stop and mention this so we can open a PR there to fix upstream.
