#!/usr/bin/env bash
set -euo pipefail
deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
root_dir="$(dirname -- "$deploy_dir")"
action="${1:-status}"
shift || true
env_file="${ETL_DOCKER_ENV_FILE:-$deploy_dir/.env.docker}"
docker compose version >/dev/null
compose=(docker compose --env-file "$env_file" -f "$deploy_dir/compose.app.yml")
[[ "${ETL_WITH_DRIVE:-false}" == true ]] && compose+=(-f "$deploy_dir/compose.drive.yml")
[[ "${ETL_REMOTE_DB:-false}" == true ]] && compose+=(-f "$deploy_dir/compose.remote-db.yml")
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
      "${compose[@]}" --profile workers up -d --no-build api frontend master-sync-worker source-file-worker ocr-worker
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
