#!/usr/bin/env bash
# I controlli del gate per un front-end Angular: npm ci, build di produzione, test Karma.
#
# Girano in un container con la versione esatta di Node di "engines" in package.json e Chromium
# per Karma: non dipendono da Node o Chrome installati sulla macchina. L'immagine si costruisce
# una volta da image/Dockerfile e resta in cache.
#
# Adattalo al progetto: APP è la cartella con package.json, relativa alla radice del repo.
set -euo pipefail

APP="${APP:-.}"
here="$(cd "$(dirname "$0")" && pwd)"

node_version=$(grep -oE '"node"[[:space:]]*:[[:space:]]*"[^"]+"' "$APP/package.json" | grep -oE '[0-9]+(\.[0-9]+)*' | head -1 || true)
node_version="${node_version:-22}"
image="sf-node-chrome:$node_version"
if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "== Costruisco l'immagine $image (una volta sola)"
  docker build -q -t "$image" --build-arg NODE_VERSION="$node_version" "$here/image" >/dev/null
fi

cache="$HOME/.cache/software-factory/npm"
mkdir -p "$cache"
in_node() {
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/npm-cache \
    -v "$cache:/npm-cache" -v "$PWD:/w" -w "/w/$APP" "$image" bash -c "$1"
}

echo "== npm ci"
in_node "npm ci --no-audit --no-fund"

echo "== Build di produzione"
in_node "npm run build -- --configuration production"

if [[ -n "$(find "$APP/src" -name '*.spec.ts' -print -quit)" ]]; then
  echo "== Test unitari (Karma, Chrome headless)"
  in_node "npm test -- --watch=false --browsers=ChromeHeadless --no-progress"
else
  echo "Nessun *.spec.ts: test unitari non eseguiti."
fi
