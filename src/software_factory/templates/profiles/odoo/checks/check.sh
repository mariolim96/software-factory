#!/usr/bin/env bash
# I controlli del gate per un repo di moduli Odoo (layout OCA: un modulo per cartella in radice).
#
# 1. pre-commit del repo (stile OCA) sui file toccati dal branch;
# 2. installazione dei moduli toccati su un Postgres nuovo, con i loro test.
#
# Il gate lo esegue nel worktree del job; vedi lib.sh per la configurazione (immagine Odoo,
# addons Enterprise e OCA). Il pre-commit gira con la configurazione del branch di
# integrazione, così un branch non può allentarla.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
trap odoo_cleanup EXIT

base="${FACTORY_BASE_REF:-origin/main}"
mods=$(changed_modules "$base" | tr '\n' ',' | sed 's/,$//')

config="$FACTORY_TARGET_WORKTREE/.pre-commit-config.yaml"
if [[ -f "$config" ]]; then
  echo "== pre-commit sui file toccati"
  uvx pre-commit run --config "$config" --from-ref "$base" --to-ref HEAD --show-diff-on-failure
fi

if [[ -z "$mods" ]]; then
  echo "Nessun modulo toccato: niente da installare."
  exit 0
fi

echo "== Installazione con test: $mods ($ODOO_IMAGE)"
odoo_start_db
log=$(mktemp)
odoo_run "$PWD" -i "$mods" --test-enable --test-tags="/${mods//,/,/}" --stop-after-init --without-demo=False \
  2>&1 | tee "$log" | grep -E " (WARNING|ERROR|CRITICAL) |Ran [0-9]+ tests|FAIL|tests? (passed|failed)" || true
odoo_check_log "$log"
echo "Installazione e test riusciti."
