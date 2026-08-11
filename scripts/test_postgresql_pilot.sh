#!/bin/sh
set -eu

container_name="agent-os-pilot-pg-integration"
postgres_image="postgres@sha256:4e6e670bb069649261c9c18031f0aded7bb249a5b6664ddec29c013a89310d50"
host_port="${AOS_TEST_POSTGRES_PORT:-55433}"
test_password="pilot-integration-only"

cleanup() {
  docker stop "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run --rm --detach \
  --name "$container_name" \
  --publish "127.0.0.1:${host_port}:5432" \
  --tmpfs /var/lib/postgresql/data \
  --env POSTGRES_PASSWORD="$test_password" \
  --env POSTGRES_DB=agent_os_pilot \
  "$postgres_image" >/dev/null

attempt=0
until docker exec "$container_name" pg_isready -U postgres -d agent_os_pilot >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "PostgreSQL integration container did not become ready" >&2
    exit 1
  fi
  sleep 1
done

AOS_TEST_POSTGRES_ADMIN_DSN="postgresql://postgres:${test_password}@127.0.0.1:${host_port}/agent_os_pilot" \
  python3 -m unittest tests.test_postgresql_pilot -v
