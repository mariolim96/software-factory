#!/usr/bin/env bash
# I controlli del gate per un progetto JavaScript/TypeScript (React, Node): installazione dalle
# dipendenze bloccate, lint, controllo dei tipi, test, build. Ogni passo gira solo se il progetto
# lo definisce in package.json.
#
# Tutto gira in un container node con la versione del progetto ("engines.node", .nvmrc o
# .node-version): non dipende da Node installato sulla macchina. Il gestore di pacchetti si
# riconosce dal lockfile (pnpm-lock.yaml, yarn.lock, package-lock.json).
#
# Variabili per adattarlo al progetto:
#   APP              cartella con package.json, relativa alla radice del repo (default .)
#   NEEDS_POSTGRES   1 per avviare un Postgres usa-e-getta; i comandi ricevono DATABASE_URL
#   POSTGRES_IMAGE   postgres:16-alpine
#   EXTRA_ENV        variabili in più per i comandi, "A=1 B=2"
set -euo pipefail

APP="${APP:-.}"
NEEDS_POSTGRES="${NEEDS_POSTGRES:-0}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
cd "$APP"

version=""
if [[ -f package.json ]]; then
  version=$(grep -oE '"node"[[:space:]]*:[[:space:]]*"[^"]+"' package.json | grep -oE '[0-9]+(\.[0-9]+)*' | head -1 || true)
fi
for f in .nvmrc .node-version; do
  [[ -z "$version" && -f "$f" ]] && version=$(grep -oE '[0-9]+(\.[0-9]+)*' "$f" | head -1 || true)
done
version="${version:-22}"
image="node:${version}-bookworm"

if [[ -f pnpm-lock.yaml ]]; then
  pm="corepack pnpm"; install="corepack pnpm install --frozen-lockfile"
elif [[ -f yarn.lock ]]; then
  pm="corepack yarn"
  if grep -q '__metadata' yarn.lock; then install="corepack yarn install --immutable"
  else install="corepack yarn install --frozen-lockfile"; fi
else
  pm="npm"; install="npm ci --no-audit --no-fund"
fi

name="sf-js-$(echo "${FACTORY_JOB:-local}" | tr -c 'a-zA-Z0-9-' '-')-$$"
net_args=()
env_args=(-e CI=true -e HOME=/tmp -e COREPACK_HOME=/tmp/corepack -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0
          -e npm_config_cache=/js-cache/npm -e YARN_CACHE_FOLDER=/js-cache/yarn
          -e npm_config_store_dir=/js-cache/pnpm)
for kv in ${EXTRA_ENV:-}; do env_args+=(-e "$kv"); done
cleanup() {
  docker rm -f "$name-db" >/dev/null 2>&1 || true
  docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ "$NEEDS_POSTGRES" == "1" ]]; then
  docker network create "$name" >/dev/null
  docker run -d --name "$name-db" --network "$name" \
    -e POSTGRES_PASSWORD=factory -e POSTGRES_DB=factory "$POSTGRES_IMAGE" >/dev/null
  for _ in $(seq 1 90); do
    if docker logs "$name-db" 2>&1 | grep -q "PostgreSQL init process complete" &&
       docker exec "$name-db" pg_isready -U postgres >/dev/null 2>&1; then break; fi
    sleep 1
  done
  net_args=(--network "$name")
  env_args+=(-e "DATABASE_URL=postgresql://postgres:factory@$name-db:5432/factory")
fi

cache="$HOME/.cache/software-factory/js"
mkdir -p "$cache"
in_node() {
  docker run --rm -u "$(id -u):$(id -g)" "${net_args[@]}" "${env_args[@]}" \
    -v "$cache:/js-cache" -v "$PWD:/w" -w /w "$image" bash -c "$1"
}
has_script() {
  in_node "node -e 'process.exit(require(\"./package.json\").scripts?.[\"$1\"] ? 0 : 1)'" 2>/dev/null
}

echo "== Installazione ($install, Node $version)"
in_node "$install"

if has_script lint; then echo "== Lint"; in_node "$pm run lint"; fi

if has_script typecheck; then echo "== Tipi"; in_node "$pm run typecheck"
elif has_script type-check; then echo "== Tipi"; in_node "$pm run type-check"
elif [[ -f tsconfig.json ]] && grep -q '"typescript"' package.json; then
  echo "== Tipi (tsc --noEmit)"; in_node "npx --no-install tsc --noEmit"
fi

if has_script test; then echo "== Test"; in_node "$pm test"; else echo "Nessuno script test in package.json."; fi

if has_script build; then echo "== Build"; in_node "$pm run build"; fi
