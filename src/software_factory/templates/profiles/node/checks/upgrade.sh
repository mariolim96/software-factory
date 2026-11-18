#!/usr/bin/env bash
# Test di upgrade dello schema per un back-end Node: le migrazioni del branch di integrazione su un
# Postgres vuoto, poi quelle del branch sopra lo stesso database. Riconosce Prisma
# (prisma/schema.prisma); per altro (knex, TypeORM, drizzle...) imposta MIGRATE_CMD, per esempio
# "npx knex migrate:latest". Le migrazioni devono leggere l'URL da DATABASE_URL.
set -euo pipefail

APP="${APP:-.}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
if [[ -z "${MIGRATE_CMD:-}" ]]; then
  if [[ -f "$APP/prisma/schema.prisma" ]]; then MIGRATE_CMD="npx --no-install prisma migrate deploy"
  else echo "Nessuna migrazione riconosciuta (Prisma) e MIGRATE_CMD non impostato: upgrade saltato."; exit 0; fi
fi

node_version() {
  local v
  v=$(grep -oE '"node"[[:space:]]*:[[:space:]]*"[^"]+"' "$1/package.json" 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+)*' | head -1 || true)
  [[ -z "$v" && -f "$1/.nvmrc" ]] && v=$(grep -oE '[0-9]+(\.[0-9]+)*' "$1/.nvmrc" | head -1 || true)
  echo "${v:-22}"
}
install_cmd() {
  if [[ -f "$1/pnpm-lock.yaml" ]]; then echo "corepack pnpm install --frozen-lockfile"
  elif [[ -f "$1/yarn.lock" ]]; then echo "corepack yarn install"
  else echo "npm ci --no-audit --no-fund"; fi
}

name="sf-upgrade-$(echo "${FACTORY_JOB:-local}" | tr -c 'a-zA-Z0-9-' '-')-$$"
cleanup() {
  docker rm -f "$name-db" >/dev/null 2>&1 || true
  docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create "$name" >/dev/null
docker run -d --name "$name-db" --network "$name" \
  -e POSTGRES_PASSWORD=factory -e POSTGRES_DB=factory "$POSTGRES_IMAGE" >/dev/null
for _ in $(seq 1 90); do
  if docker logs "$name-db" 2>&1 | grep -q "PostgreSQL init process complete" &&
     docker exec "$name-db" pg_isready -U postgres >/dev/null 2>&1; then break; fi
  sleep 1
done

cache="$HOME/.cache/software-factory/js"
mkdir -p "$cache"
migrate() {
  local dir="$1/${APP#./}"
  docker run --rm -u "$(id -u):$(id -g)" --network "$name" \
    -e HOME=/tmp -e COREPACK_HOME=/tmp/corepack -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e npm_config_cache=/js-cache/npm -e npm_config_store_dir=/js-cache/pnpm \
    -e "DATABASE_URL=postgresql://postgres:factory@$name-db:5432/factory" \
    -v "$cache:/js-cache" -v "$dir:/w" -w /w "node:$(node_version "$dir")-bookworm" \
    bash -c "$(install_cmd "$dir") && $MIGRATE_CMD"
}

echo "== Migrazioni di $FACTORY_TARGET_BRANCH su un database vuoto"
migrate "$FACTORY_TARGET_WORKTREE"
echo "== Migrazioni del branch sopra"
migrate "$PWD"
echo "Upgrade dello schema riuscito."
