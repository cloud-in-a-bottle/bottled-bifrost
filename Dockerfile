# Pinned by tag and multi-arch index digest; bump both together.
FROM docker.io/maximhq/bifrost:v1.6.6@sha256:0c0e6f498944d2896867fd9a5e51a79bb9cc82b845763fc4ababe0a6d2ab4402

# Caddy fronts Bifrost on the container port: it permission-gates the cross-app
# service interface and limits it to the inference API, while leaving the web UI
# ungated (OpenHost handles owner auth). Bifrost itself listens only on loopback.

# nodejs/npm are for STDIO MCP servers: Bifrost execs the configured command
# (typically `npx -y <server>`) inside this container, and the base image has
# no JS runtime.
USER root
RUN apk add --no-cache caddy nodejs npm
COPY Caddyfile /etc/caddy/Caddyfile
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
USER appuser

EXPOSE 8080
ENTRYPOINT ["/app/start.sh"]
