FROM maximhq/bifrost:v1.6.6

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
