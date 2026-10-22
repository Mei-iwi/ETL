#!/bin/sh
set -eu
origin="${AUTH_ORIGIN:-http://127.0.0.1:8080}"
host="${origin#*://}"
if [ "${AUTH_TLS:-false}" = true ]; then
    # Certificate hostname/trust is tested through the external origin separately.
    exec wget --no-check-certificate --header="Host: $host" -q -O /dev/null https://127.0.0.1:8443/web-health
fi
exec wget --header="Host: ${host:-127.0.0.1:8080}" -q -O /dev/null http://127.0.0.1:8080/web-health
