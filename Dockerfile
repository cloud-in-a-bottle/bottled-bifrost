# Pinned by tag and multi-arch index digest; bump both together.
FROM docker.io/maximhq/bifrost:v2.4.0@sha256:a1c90607dcfd13c1fb2f9476ceb3bb24407a50ade7fdf3e61a59fb42b09e5c57

# Caddy fronts Bifrost on the container port: it permission-gates the cross-app
# service interface and limits it to the inference API, and sends the web UI to
# owner-proxy.mjs, which signs the OpenHost owner in as Bifrost's admin (OpenHost
# gates the UI to the owner). Bifrost itself listens only on loopback.

# nodejs/npm are for STDIO MCP servers: Bifrost execs the configured command
# (typically `npx -y <server>`) inside this container, and the base image has
# no JS runtime.
USER root
RUN apk add --no-cache caddy nodejs npm
COPY Caddyfile /etc/caddy/Caddyfile
COPY start.sh /app/start.sh
COPY owner-proxy.mjs /app/owner-proxy.mjs
RUN chmod +x /app/start.sh
USER appuser

EXPOSE 8080
ENTRYPOINT ["/app/start.sh"]
