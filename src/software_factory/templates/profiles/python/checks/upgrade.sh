#!/usr/bin/env bash
# Test di upgrade dello schema: le migrazioni del branch di integrazione su un Postgres vuoto, poi
# quelle del branch sopra lo stesso database. Riconosce Alembic (alembic.ini) e Django (manage.py);
# per altro, imposta MIGRATE_CMD. Il progetto deve leggere l'URL del database da DATABASE_URL
# (o da MIGRATE_ENV, per esempio "DJANGO_SETTINGS_MODULE=app.settings.test").
set -euo pipefail

if [[ -z "${MIGRATE_CMD:-}" ]]; then
  if [[ -f "${APP:-.}/alembic.ini" ]]; then MIGRATE_CMD="alembic upgrade head"
  elif [[ -f "${APP:-.}/manage.py" ]]; then MIGRATE_CMD="python manage.py migrate --noinput"
  else echo "Nessuna migrazione riconosciuta (alembic.ini, manage.py) e MIGRATE_CMD non impostato: upgrade saltato."; exit 0; fi
fi
export NEEDS_POSTGRES=1 EXTRA_ENV="${EXTRA_ENV:-} ${MIGRATE_ENV:-}"
branch="$PWD"
cd "$FACTORY_TARGET_WORKTREE"
source "$(dirname "$0")/lib.sh"   # cd in APP del branch di integrazione, avvia Postgres

echo "== Migrazioni di $FACTORY_TARGET_BRANCH su un database vuoto"
in_py "$INSTALL && $MIGRATE_CMD"
echo "== Migrazioni del branch sopra"
in_py "$INSTALL && $MIGRATE_CMD" "$branch/${APP#./}"
echo "Upgrade dello schema riuscito."
