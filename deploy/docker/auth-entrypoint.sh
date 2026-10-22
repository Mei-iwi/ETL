#!/bin/sh
set -eu
if [ "${AUTH_MODE:-legacy_local}" = legacy_local ]; then exec nginx -g 'daemon off;'; fi
case "${AUTH_MODE:-}" in single_admin|db_users|master_users|local_demo) ;; *) echo AUTH_MODE_INVALID >&2; exit 1;; esac
ETL_AUTH_ORIGIN="${AUTH_ORIGIN:?AUTH_ORIGIN_REQUIRED}"
printf '%s' "$ETL_AUTH_ORIGIN" | grep -Eq '^https?://[a-zA-Z0-9.-]+(:[0-9]{1,5})?$' || { echo AUTH_ORIGIN_INVALID >&2; exit 1; }
ETL_AUTH_HOST="${ETL_AUTH_ORIGIN#*://}"
ETL_PROXY_KEY="$(cat /run/secrets/auth_proxy_key)"
printf '%s' "$ETL_PROXY_KEY" | grep -Eq '^[a-fA-F0-9]{64}$' || { echo AUTH_PROXY_KEY_INVALID >&2; exit 1; }
ETL_LISTEN='listen 8080 default_server;'
ETL_TLS=''
if [ "${AUTH_TLS:-false}" = true ]; then
    case "$ETL_AUTH_ORIGIN" in https://*) ;; *) echo AUTH_TLS_ORIGIN_REQUIRED >&2; exit 1;; esac
    [ "$AUTH_MODE" != local_demo ] || { echo AUTH_BYPASS_TLS_FORBIDDEN >&2; exit 1; }
    ETL_LISTEN='listen 8443 ssl default_server;'
    ETL_TLS='ssl_certificate /run/tls/server.crt; ssl_certificate_key /run/tls/server.key; ssl_protocols TLSv1.2 TLSv1.3;'
else
    case "$ETL_AUTH_ORIGIN" in http://127.0.0.1:*|http://localhost:*|http://127.0.0.1|http://localhost) ;; *) echo AUTH_HTTP_LOOPBACK_REQUIRED >&2; exit 1;; esac
fi
export ETL_AUTH_ORIGIN ETL_AUTH_HOST ETL_PROXY_KEY ETL_LISTEN ETL_TLS
umask 077
envsubst '${ETL_AUTH_ORIGIN} ${ETL_AUTH_HOST} ${ETL_PROXY_KEY} ${ETL_LISTEN} ${ETL_TLS}' < /opt/etl/nginx.auth.template > /tmp/nginx-auth.conf
exec nginx -c /tmp/nginx-auth.conf -g 'daemon off;'
