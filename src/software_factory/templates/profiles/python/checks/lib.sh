#!/usr/bin/env bash
# Funzioni comuni ai controlli Python: immagine con uv, installazione, Postgres usa-e-getta.
# Si usa con `source` da check.sh e upgrade.sh, dalla cartella del repo da controllare.

APP="${APP:-.}"
NEEDS_POSTGRES="${NEEDS_POSTGRES:-0}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
cd "$APP"

PY_VERSION=$(grep -oE '^[0-9]+\.[0-9]+' .python-version 2>/dev/null | head -1 || true)
PY_VERSION="${PY_VERSION:-3.12}"
PY_IMAGE="ghcr.io/astral-sh/uv:python${PY_VERSION}-bookworm-slim"

if [[ -f uv.lock ]]; then
  INSTALL="uv sync --frozen"
elif [[ -f poetry.lock ]]; then
  INSTALL="POETRY_VIRTUALENVS_IN_PROJECT=true uvx poetry install --no-interaction"
else
  reqs=""
  for f in requirements.txt requirements-dev.txt requirements_dev.txt requirements/dev.txt; do
    [[ -f "$f" ]] && reqs="$reqs -r $f"
  done
  if [[ -n "$reqs" ]]; then INSTALL="uv venv -q .venv && uv pip install -q $reqs"
  else INSTALL="uv venv -q .venv && (uv pip install -q -e '.[dev]' || uv pip install -q -e .)"; fi
fi

PY_NAME="sf-py-$(echo "${FACTORY_JOB:-local}" | tr -c 'a-zA-Z0-9-' '-')-$$"
PY_NET=()
PY_ENV=(-e HOME=/tmp -e UV_CACHE_DIR=/py-cache -e UV_LINK_MODE=copy -e PRE_COMMIT_HOME=/py-cache/pre-commit
        -e VIRTUAL_ENV=/w/.venv -e "PATH=/w/.venv/bin:/usr/local/bin:/usr/bin:/bin" -e UV_PROJECT_ENVIRONMENT=/w/.venv)
for kv in ${EXTRA_ENV:-}; do PY_ENV+=(-e "$kv"); done

py_cleanup() {
  docker rm -f "$PY_NAME-db" >/dev/null 2>&1 || true
  docker network rm "$PY_NAME" >/dev/null 2>&1 || true
}
trap py_cleanup EXIT

py_start_db() {
  py_cleanup
  docker network create "$PY_NAME" >/dev/null
  docker run -d --name "$PY_NAME-db" --network "$PY_NAME" \
    -e POSTGRES_PASSWORD=factory -e POSTGRES_DB=factory "$POSTGRES_IMAGE" >/dev/null
  for _ in $(seq 1 90); do
    if docker logs "$PY_NAME-db" 2>&1 | grep -q "PostgreSQL init process complete" &&
       docker exec "$PY_NAME-db" pg_isready -U postgres >/dev/null 2>&1; then break; fi
    sleep 1
  done
  PY_NET=(--network "$PY_NAME")
  PY_ENV+=(-e "DATABASE_URL=postgresql://postgres:factory@$PY_NAME-db:5432/factory")
}
[[ "$NEEDS_POSTGRES" == "1" ]] && py_start_db

PY_CACHE="$HOME/.cache/software-factory/py"
mkdir -p "$PY_CACHE"

# in_py "<comando>" [cartella]: esegue il comando nel container, nella cartella data (default: quella corrente).
in_py() {
  docker run --rm -u "$(id -u):$(id -g)" "${PY_NET[@]}" "${PY_ENV[@]}" \
    -v "$PY_CACHE:/py-cache" -v "${2:-$PWD}:/w" -w /w "$PY_IMAGE" bash -c "$1"
}
