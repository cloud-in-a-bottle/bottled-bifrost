# Service: openai-compat

**Service URL:** `github.com/imbue-openhost/openhost-bifrost-llm-gateway/services/openai-compat`
**Version:** `0.1.0`

Exposes the [Bifrost](https://github.com/maximhq/bifrost) LLM gateway's
OpenAI-compatible API to other apps on the same OpenHost compute space. The
provider app's owner configures the upstream providers and API keys in Bifrost's
web UI; consumer apps then call the gateway through the OpenHost service router
without ever seeing those keys.

## Permissions

A single global-scoped string grant:

| Grant | Meaning |
|-------|---------|
| `"full_access"` | Full access to the gateway's API. |

The provider enforces this: every proxied request must carry a `full_access`
grant in `X-OpenHost-Permissions` or it returns `403 permission_required`.

Granted at consumer install time (web UI or the CLI's `--grant-permissions-v2`),
or via the owner-facing approval page on a `403`.

The service interface exposes only the inference API below; the gateway's web UI
and management API are not reachable through it.

## API

All of Bifrost's HTTP routes are reachable under the service endpoint. The
OpenAI-compatible routes live under the `/openai` prefix, e.g.:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/openai/v1/chat/completions` | Chat completions (OpenAI schema) |
| POST | `/openai/v1/embeddings` | Embeddings |
| GET  | `/openai/v1/models` | List configured models |

Bifrost also serves `/anthropic/...` and `/genai/...` drop-in prefixes; see the
[Bifrost docs](https://docs.getbifrost.ai/). The `model` field selects the
provider/model the owner configured (e.g. `"openai/gpt-4o"`).

## Consuming this service

Declare it in the consumer app's `openhost.toml`:

```toml
[[services.v2.consumes]]
service = "github.com/imbue-openhost/openhost-bifrost-llm-gateway/services/openai-compat"
shortname = "llm"
version = ">=0.1.0"
grants = ["full_access"]
```

### Server-side calls

Call the router's service endpoint with the app token:

```
POST $OPENHOST_ROUTER_URL/api/services/v2/call/llm/openai/v1/chat/completions
Authorization: Bearer $OPENHOST_APP_TOKEN
Content-Type: application/json

{"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
```

### Using an OpenAI SDK unchanged

Point the SDK's base URL at a local reverse proxy that attaches the app token
and rewrites to the service endpoint (see OpenHost's "retrofitting existing
apps" docs):

```sh
mitmdump -p 9000 \
  --mode reverse:$OPENHOST_ROUTER_URL/api/services/v2/call/llm \
  --set "modify_headers=/~q/Authorization/Bearer $OPENHOST_APP_TOKEN"
```

Then set the OpenAI client's `base_url` to `http://localhost:9000/openai/v1`.
