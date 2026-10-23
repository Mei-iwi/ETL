#!/usr/bin/env bash
set -euo pipefail
directory="$(realpath -m -- "${1:?Provide a private directory outside Git}")"
email="${2:?Provide the admin email}"
image="${ETL_AUTH_SETUP_IMAGE:-etl-portable-backend:local}"
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
[[ "$directory" != "$repo" && "$directory" != "$repo/"* ]] || { echo 'Keep secrets outside Git workspace' >&2; exit 1; }
[[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ && "$email" != *.invalid ]] || { echo 'A real admin email is required' >&2; exit 1; }
[[ -d "$directory" && "$(stat -c %u "$directory")" == 10001 ]] || { echo 'Prepare the private directory with owner 10001:10001 and mode 0700 (sudo install -d -o 10001 -g 10001 -m 0700 PATH).' >&2; exit 1; }
docker run --rm -it --user 10001:10001 --mount "type=bind,source=$directory,target=/output" --entrypoint python "$image" -m data_sync_etl.security.cli init --directory /output
cat <<EOF
# Add these non-secret settings to docker.env alongside deployment paths.
AUTH_MODE=single_admin
AUTH_APP_ENVIRONMENT=production
ADMIN_ENABLED=true
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
AUTH_ORIGIN=http://127.0.0.1:8080
ADMIN_LOGIN_EMAIL=$email
ADMIN_PASSWORD_HASH_SECRET_FILE=$directory/admin_password_hash
SESSION_SIGNING_KEY_SECRET_FILE=$directory/session_signing_key
AUTH_PROXY_KEY_SECRET_FILE=$directory/auth_proxy_key
EOF
