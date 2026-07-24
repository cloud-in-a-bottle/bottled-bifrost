FROM maximhq/bifrost:v1.6.6

# Caddy fronts Bifrost on the container port: it permission-gates the cross-app
# service interface and limits it to the inference API, while leaving the web UI
# ungated (OpenHost handles owner auth). Bifrost itself listens only on loopback.
USER root
RUN apk add --no-cache caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
USER appuser

EXPOSE 8080
ENTRYPOINT ["/app/start.sh"]
