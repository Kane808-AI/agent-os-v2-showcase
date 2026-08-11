#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
runtime_root=${AOS_LOCAL_PILOT_DATA_DIR:-"$repository_root/data/local-pilot"}
secret_root="$runtime_root/secrets"
backup_root="$runtime_root/backups"
container_name="agent-os-local-pilot-postgres"
network_name="agent-os-local-pilot"
volume_name="agent-os-local-pilot-data"
postgres_image="postgres@sha256:4e6e670bb069649261c9c18031f0aded7bb249a5b6664ddec29c013a89310d50"
application_image="agent-os-pilot:local"
minimum_free_bytes=${AOS_LOCAL_PILOT_MINIMUM_FREE_BYTES:-53687091200}
maximum_database_bytes=${AOS_LOCAL_PILOT_MAXIMUM_DATABASE_BYTES:-1073741824}

host_free_bytes() {
  df -Pk "$repository_root" | awk 'NR == 2 { print $4 * 1024 }'
}

source_python() {
  PYTHONPATH="$repository_root/src" python3 -m agent_os.local_pilot "$@"
}

container_python() {
  docker run --rm \
    --network "$network_name" \
    --user "$(id -u):$(id -g)" \
    --volume "$secret_root:/run/agent-os-secrets:ro" \
    "$application_image" \
    python -m agent_os.local_pilot "$@"
}

require_running() {
  if [ "$(docker inspect --format '{{.State.Running}}' "$container_name" 2>/dev/null || true)" != "true" ]; then
    echo "Local pilot is not running; use: scripts/local_pilot.sh up" >&2
    exit 1
  fi
}

status() {
  require_running
  container_python status \
    --directory /run/agent-os-secrets \
    --host-free-bytes "$(host_free_bytes)" \
    --minimum-free-bytes "$minimum_free_bytes" \
    --maximum-database-bytes "$maximum_database_bytes"
}

up() {
  free_bytes=$(host_free_bytes)
  if [ "$free_bytes" -lt "$minimum_free_bytes" ]; then
    echo "Refusing local pilot: less than 50 GiB is free." >&2
    exit 2
  fi
  mkdir -p "$runtime_root" "$backup_root"
  chmod 700 "$runtime_root" "$backup_root"
  source_python init-secrets --directory "$secret_root"
  docker build \
    --file "$repository_root/deployment/container/Dockerfile.pilot" \
    --tag "$application_image" "$repository_root"
  if ! docker network inspect "$network_name" >/dev/null 2>&1; then
    docker network create --internal "$network_name" >/dev/null
  fi
  if docker container inspect "$container_name" >/dev/null 2>&1; then
    docker start "$container_name" >/dev/null
  else
    docker run --detach \
      --name "$container_name" \
      --network "$network_name" \
      --restart=no \
      --memory=512m \
      --cpus=1 \
      --shm-size=128m \
      --log-opt max-size=10m \
      --log-opt max-file=3 \
      --env-file "$secret_root/postgres.env" \
      --mount "source=$volume_name,target=/var/lib/postgresql/data" \
      "$postgres_image" >/dev/null
  fi
  attempt=0
  until docker exec "$container_name" pg_isready -U postgres -d agent_os_pilot >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
      echo "Local PostgreSQL did not become ready." >&2
      exit 1
    fi
    sleep 1
  done
  container_python bootstrap --directory /run/agent-os-secrets
  status
}

canary() {
  input=${1:-}
  if [ -z "$input" ] || [ ! -f "$input" ]; then
    echo "Usage: scripts/local_pilot.sh canary /absolute/path/to/normalized.json" >&2
    exit 1
  fi
  status >/dev/null
  input_directory=$(CDPATH= cd -- "$(dirname "$input")" && pwd)
  input_name=$(basename "$input")
  docker run --rm \
    --network "$network_name" \
    --user "$(id -u):$(id -g)" \
    --volume "$secret_root:/run/agent-os-secrets:ro" \
    --volume "$input_directory:/run/agent-os-input:ro" \
    --env AOS_POSTGRES_DSN_FILE=/run/agent-os-secrets/runtime.dsn \
    --env "AOS_CANARY_INPUT_FILE=/run/agent-os-input/$input_name" \
    "$application_image" \
    python -m agent_os.pilot_canary
  status
}

backup() {
  status >/dev/null
  mkdir -p "$backup_root"
  chmod 700 "$backup_root"
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  partial="agent-os-local-pilot-$stamp.dump.partial"
  final="agent-os-local-pilot-$stamp.dump"
  docker run --rm \
    --network "$network_name" \
    --user "$(id -u):$(id -g)" \
    --env-file "$secret_root/backup.env" \
    --volume "$backup_root:/backups" \
    "$postgres_image" \
    pg_dump --format=custom --compress=9 --no-owner --no-acl \
      --file="/backups/$partial"
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --volume "$backup_root:/backups:ro" \
    "$postgres_image" \
    pg_restore --list "/backups/$partial" >/dev/null
  mv "$backup_root/$partial" "$backup_root/$final"
  chmod 600 "$backup_root/$final"
  source_python rotate-backups --directory "$backup_root" --keep 7
  echo "$backup_root/$final"
}

down() {
  if docker container inspect "$container_name" >/dev/null 2>&1; then
    docker stop "$container_name" >/dev/null
  fi
  echo "Local pilot stopped. Its database volume and backups were preserved."
}

case "${1:-}" in
  up) up ;;
  status) status ;;
  canary) shift; canary "${1:-}" ;;
  backup) backup ;;
  down) down ;;
  *)
    echo "Usage: scripts/local_pilot.sh {up|status|canary PATH|backup|down}" >&2
    exit 1
    ;;
esac
