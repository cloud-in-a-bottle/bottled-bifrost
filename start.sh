#!/bin/sh
set -e

# Persist Bifrost's config + sqlite + logs in OpenHost's backed-up app-data dir
# so they survive reloads. Fail loudly if it's missing rather than silently
# falling back to ephemeral container storage.
: "${OPENHOST_APP_DATA_DIR:?OPENHOST_APP_DATA_DIR is not set; enable [data].app_data in openhost.toml}"

export APP_DIR="$OPENHOST_APP_DATA_DIR"

# Bifrost listens on loopback only; Caddy fronts it on :8080 (see Caddyfile), so
# the gateway can't be reached without going through Caddy's service gate.
export APP_PORT=3000
export APP_HOST=127.0.0.1

# Bifrost's stock entrypoint fixes data-dir permissions then starts /app/main.
/app/docker-entrypoint.sh /app/main &

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
