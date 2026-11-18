#!/usr/bin/env bash
# Funzioni comuni ai controlli Odoo: Postgres usa-e-getta per job e l'immagine Odoo della versione target.
#
# Configurazione (variabili d'ambiente, con i default):
#   ODOO_IMAGE        immagine Docker di Odoo della versione target (odoo:19, odoo:20)
#   POSTGRES_IMAGE    postgres:16-alpine
#   EXTRA_ADDONS      percorsi aggiuntivi di addons, separati da ':' (Enterprise, dipendenze OCA)
#   SKIP_MODULES      moduli da non testare, separati da spazio

ODOO_IMAGE="${ODOO_IMAGE:-odoo:19}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
NAME="sf-$(echo "${FACTORY_JOB:-local}" | tr -c 'a-zA-Z0-9-' '-')-$$"

odoo_cleanup() {
  docker rm -f "$NAME-db" >/dev/null 2>&1 || true
  docker network rm "$NAME" >/dev/null 2>&1 || true
}

odoo_start_db() {
  odoo_cleanup
  docker network create "$NAME" >/dev/null
  docker run -d --name "$NAME-db" --network "$NAME" \
    -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo -e POSTGRES_DB=postgres "$POSTGRES_IMAGE" >/dev/null
  for _ in $(seq 1 90); do
    if docker logs "$NAME-db" 2>&1 | grep -q "PostgreSQL init process complete" &&
       docker exec "$NAME-db" pg_isready -U odoo >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Postgres non è partito" >&2
  return 1
}

# I moduli (cartelle con __manifest__.py in radice) toccati dal branch rispetto a $1.
changed_modules() {
  git diff --name-only "$1"...HEAD | cut -d/ -f1 | sort -u | while read -r m; do
    [[ -f "$m/__manifest__.py" ]] && [[ " ${SKIP_MODULES:-} " != *" $m "* ]] && echo "$m"
  done
}

# odoo_run <cartella del repo> <argomenti di odoo...>: esegue odoo-bin sul database del job.
# Installa requirements.txt del repo, se c'è, prima di partire. Il log va su stdout.
odoo_run() {
  local repo="$1"; shift
  local mounts=(-v "$repo:/mnt/repo:ro") addons="/mnt/repo" i=0
  IFS=':' read -r -a extra <<< "${EXTRA_ADDONS:-}"
  for p in "${extra[@]}"; do
    [[ -z "$p" ]] && continue
    mounts+=(-v "$p:/mnt/extra$i:ro"); addons="$addons,/mnt/extra$i"; i=$((i + 1))
  done
  docker run --rm --network "$NAME" -u root "${mounts[@]}" --entrypoint bash "$ODOO_IMAGE" -c '
    set -e
    if [ -f /mnt/repo/requirements.txt ]; then
      pip install -q --break-system-packages -r /mnt/repo/requirements.txt 2>/dev/null ||
        pip install -q -r /mnt/repo/requirements.txt
    fi
    exec su odoo -s /bin/bash -c "odoo --db_host='"$NAME-db"' --db_user=odoo --db_password=odoo \
      -d factory --addons-path='"$addons"' --http-port=0 --log-level=test $*"
  ' odoo-run "$@"
}

# Fallisce se il log di Odoo contiene errori o test falliti (odoo-bin non sempre esce diverso da zero).
odoo_check_log() {
  if grep -E " (ERROR|CRITICAL) factory " "$1" | grep -v "Some modules are not loaded" >/dev/null; then
    echo "== Errori nel log di Odoo:"
    grep -E " (ERROR|CRITICAL) factory " "$1" | head -50
    return 1
  fi
  if grep -E "(FAIL|ERROR): .*Test|failed, [0-9]+ error" "$1" | grep -v " 0 failed, 0 error" >/dev/null; then
    echo "== Test falliti:"
    grep -E "(FAIL|ERROR): |failed, [0-9]+ error" "$1" | head -50
    return 1
  fi
}
