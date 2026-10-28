#!/usr/bin/env bash
set -euo pipefail
deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
root_dir="$(dirname -- "$deploy_dir")"
action="${1:-status}"
shift || true
env_file="${ETL_DOCKER_ENV_FILE:-$root_dir/docker.env}"
if [[ -z "${ETL_DOCKER_ENV_FILE:-}" && ! -f "$env_file" ]]; then env_file="$deploy_dir/.env.docker"; fi
[[ -f "$env_file" ]] || { echo 'Docker env file missing. Copy deploy/docker.env.example to docker.env and configure it.' >&2; exit 2; }
database_mode=same-host
media_enabled=false
mode_pattern="^[[:space:]]*DATABASE_MODE[[:space:]]*=[[:space:]]*[\"']?(same-host|remote|source-neon)[\"']?[[:space:]]*(#.*)?$"
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"
  if [[ "$line" =~ ^[[:space:]]*MEDIA_ENABLED[[:space:]]*= ]]; then
    media_pattern="^[[:space:]]*MEDIA_ENABLED[[:space:]]*=[\"']?(true|false)[\"']?[[:space:]]*(#.*)?$"
    [[ "$line" =~ $media_pattern ]] || { echo 'Invalid MEDIA_ENABLED in Docker env file.' >&2; exit 2; }
    media_enabled="${BASH_REMATCH[1]}"
  fi
  if [[ "$line" =~ ^[[:space:]]*DATABASE_MODE[[:space:]]*= ]]; then
    [[ "$line" =~ $mode_pattern ]] || { echo 'Invalid DATABASE_MODE in Docker env file.' >&2; exit 2; }
    database_mode="${BASH_REMATCH[1]}"
  fi
done < "$env_file"
database_mode="${DATABASE_MODE:-$database_mode}"
case "$database_mode" in same-host|remote|source-neon) ;; *) echo 'Invalid DATABASE_MODE.' >&2; exit 2 ;; esac
if [[ ( "${ETL_REMOTE_DB:-false}" == true && "$database_mode" == source-neon ) || ( "${ETL_SOURCE_NEON:-false}" == true && "$database_mode" == remote ) ]]; then
  echo 'Database switch conflicts with DATABASE_MODE.' >&2; exit 2
fi
[[ "$database_mode" != source-neon ]] || export ETL_SOURCE_NEON=true
[[ "$database_mode" != remote ]] || export ETL_REMOTE_DB=true
echo "Docker env: $env_file"
if [[ "${ETL_SOURCE_NEON:-false}" == true ]]; then database_mode=source-neon; fi
if [[ "${ETL_REMOTE_DB:-false}" == true ]]; then database_mode=remote; fi
echo "Database profile: $database_mode"
docker compose version >/dev/null
if [[ "${ETL_REMOTE_DB:-false}" == true && "${ETL_SOURCE_NEON:-false}" == true ]]; then
  echo 'ETL_SOURCE_NEON and ETL_REMOTE_DB are mutually exclusive.' >&2
  exit 2
fi
compose=(docker compose --env-file "$env_file" -f "$deploy_dir/compose.app.yml")
media_enabled="${MEDIA_ENABLED:-$media_enabled}"
case "$media_enabled" in true|false) ;; *) echo 'Invalid MEDIA_ENABLED.' >&2; exit 2 ;; esac
[[ "$media_enabled" == true ]] && compose+=(-f "$deploy_dir/compose.media.yml")
[[ "${ETL_WITH_DRIVE:-false}" == true ]] && compose+=(-f "$deploy_dir/compose.drive.yml")
[[ "${ETL_REMOTE_DB:-false}" == true ]] && compose+=(-f "$deploy_dir/compose.remote-db.yml")
[[ "${ETL_SOURCE_NEON:-false}" == true ]] && compose+=(-f "$deploy_dir/compose.source-neon.yml")
auth_profile="${ETL_AUTH_PROFILE:-auth}"
case "$auth_profile" in
  legacy) ;;
  auth) compose+=(-f "$deploy_dir/compose.auth.yml") ;;
  auth-tls) compose+=(-f "$deploy_dir/compose.auth.yml" -f "$deploy_dir/compose.auth-tls.yml") ;;
  db-auth) compose+=(-f "$deploy_dir/compose.db-auth.yml") ;;
  db-auth-tls) compose+=(-f "$deploy_dir/compose.db-auth.yml" -f "$deploy_dir/compose.auth-tls.yml") ;;
  *) echo 'Invalid auth profile' >&2; exit 2 ;;
esac
case "$action" in
  build) "${compose[@]}" build api frontend ;;
  network)
    network="${1:-etl-data}"
    docker network inspect "$network" >/dev/null 2>&1 || docker network create "$network"
    ;;
  check)
    "${compose[@]}" --profile workers config --quiet
    if [[ "$auth_profile" != legacy ]]; then
      "${compose[@]}" --profile workers config --format json | "${compose[@]}" run --rm --no-deps -T --entrypoint python api /opt/etl/auth_deployment.py
      "${compose[@]}" run --rm --no-deps api check-auth
    fi
    "${compose[@]}" run --rm --no-deps --entrypoint python api /opt/etl/model.py verify
    "${compose[@]}" --profile ops run --rm --no-deps preflight
    ;;
  start)
    bash "$0" check
    if [[ "${1:-}" == --workers ]]; then
      "${compose[@]}" --profile workers up -d --no-build api frontend master-sync-worker source-file-worker ocr-worker reconciliation-worker
    else
      "${compose[@]}" up -d --no-build api frontend
    fi
    echo "Application started with profile: $auth_profile. Inspect health and acceptance report."
    ;;
  status) "${compose[@]}" --profile workers ps ;;
  logs) "${compose[@]}" --profile workers logs --tail 100 ;;
  stop) "${compose[@]}" --profile workers stop ;;
  down) "${compose[@]}" --profile workers down ;; # Never removes volumes.
  db-start|db-stop)
    command=up; [[ "$action" == db-stop ]] && command=stop
    args=(); [[ "$command" == up ]] && args+=(-d)
    docker compose --env-file "$env_file" -f "$deploy_dir/compose.database.yml" "$command" "${args[@]}"
    ;;
  export)
    archive="${1:?Provide an output image archive path}"
    [[ ! -e "$archive" && ! -e "$archive.sha256" ]] || { echo 'Archive output already exists' >&2; exit 1; }
    mapfile -t images < <("${compose[@]}" config --images | sort -u)
    [[ ${#images[@]} -ge 2 ]] || { echo 'Image inventory incomplete' >&2; exit 1; }
    docker image save -o "$archive" "${images[@]}"
    sha256sum "$archive" | cut -d ' ' -f 1 > "$archive.sha256"
    ;;
  import)
    archive="${1:?Provide an image archive path}"
    expected="$(tr -d '\r' < "$archive.sha256" | awk 'NR==1 {print $1}')"
    actual="$(sha256sum "$archive" | cut -d ' ' -f 1)"
    [[ "$expected" =~ ^[a-fA-F0-9]{64}$ && "${expected,,}" == "$actual" ]] || { echo 'Archive checksum mismatch' >&2; exit 1; }
    docker image load -i "$archive"
    ;;
  verify-model)
    "${compose[@]}" run --rm --no-deps --entrypoint python api /opt/etl/model.py encode
    ;;
  prepare-model)
    directory="${1:?Provide an absolute output model directory}"
    [[ "${2:-}" == --accept-model-license ]] || { echo 'Model license approval required' >&2; exit 1; }
    mkdir -p "$directory"
    directory="$(cd -- "$directory" && pwd)"
    image="${ETL_MODEL_TOOL_IMAGE:-etl-portable-backend:local}"
    docker run --rm --user "$(id -u):$(id -g)" --tmpfs /run/etl:rw,mode=1777 \
      --mount "type=bind,source=$directory,target=/model" --entrypoint python "$image" \
      /opt/etl/model.py prepare --directory /model --manifest /model/etl-model-manifest.json --accept-model-license "${@:3}"
    ;;
  *) echo 'Actions: build network check start [--workers] status logs stop down db-start db-stop export import prepare-model verify-model' >&2; exit 2 ;;
esac
