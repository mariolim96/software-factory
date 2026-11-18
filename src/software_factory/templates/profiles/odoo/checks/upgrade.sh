#!/usr/bin/env bash
# Test di upgrade: installa i moduli toccati col codice del branch di integrazione, poi li
# aggiorna (-u) col codice del branch sullo stesso database. Esercita gli script in
# migrations/ e i dati noupdate come un aggiornamento in produzione.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
trap odoo_cleanup EXIT

base="${FACTORY_BASE_REF:-origin/main}"
all=$(changed_modules "$base")
existing=""
for m in $all; do
  [[ -f "$FACTORY_TARGET_WORKTREE/$m/__manifest__.py" ]] && existing="$existing,$m"
done
existing="${existing#,}"
if [[ -z "$existing" ]]; then
  echo "Nessun modulo toccato esiste già sul branch di integrazione: niente da aggiornare."
  exit 0
fi

odoo_start_db
log=$(mktemp)
echo "== Installazione col codice di $FACTORY_TARGET_BRANCH: $existing"
odoo_run "$FACTORY_TARGET_WORKTREE" -i "$existing" --stop-after-init --without-demo=False 2>&1 | tee "$log" >/dev/null || true
odoo_check_log "$log"

echo "== Aggiornamento col codice del branch"
odoo_run "$PWD" -u "$existing" --stop-after-init 2>&1 | tee "$log" | grep -E " (WARNING|ERROR|CRITICAL) " || true
odoo_check_log "$log"
echo "Aggiornamento riuscito."
