#!/usr/bin/env bash
# I controlli del gate per un back-end Python (FastAPI, Django, Flask...): installazione, pre-commit
# sui file toccati, ruff, mypy e test. Ogni passo gira solo se il progetto lo configura.
#
# Tutto gira in un container con uv e la versione di Python del progetto (.python-version, default
# 3.12): non dipende da Python installato sulla macchina. Le dipendenze si installano da uv.lock,
# poetry.lock o requirements*.txt, in .venv nel worktree.
#
# Variabili per adattarlo al progetto:
#   APP              cartella con pyproject.toml, relativa alla radice del repo (default .)
#   NEEDS_POSTGRES   1 per avviare un Postgres usa-e-getta; i comandi ricevono DATABASE_URL
#   TEST_CMD         comando dei test, se non è pytest o manage.py test
#   EXTRA_ENV        variabili in più per i comandi, "A=1 B=2"
set -euo pipefail
root="$PWD"
config="$FACTORY_TARGET_WORKTREE/.pre-commit-config.yaml"
if [[ -f "$config" ]]; then
  # Sulla macchina, non nel container: pre-commit ha bisogno di git, e il .git di un worktree
  # punta fuori dalla cartella montata. La configurazione è quella del branch di integrazione.
  echo "== pre-commit sui file toccati (configurazione di $FACTORY_TARGET_BRANCH)"
  uvx pre-commit run --config "$config" --from-ref "$FACTORY_BASE_REF" --to-ref HEAD --show-diff-on-failure
fi

source "$(dirname "$0")/lib.sh"

echo "== Installazione (Python $PY_VERSION)"
in_py "$INSTALL"

if [[ -f "$config" ]]; then
  :  # ruff e gli altri linter li ha già eseguiti pre-commit
elif [[ -f ruff.toml || -f .ruff.toml ]] || grep -q '^\[tool\.ruff' pyproject.toml 2>/dev/null; then
  echo "== Ruff"
  in_py "uvx ruff check . && uvx ruff format --check ."
fi

if [[ -f mypy.ini ]] || grep -q '^\[tool\.mypy' pyproject.toml 2>/dev/null; then
  echo "== mypy"
  in_py "python -m mypy . 2>/dev/null || uvx mypy ."
fi

if [[ -n "${TEST_CMD:-}" ]]; then
  echo "== Test ($TEST_CMD)"; in_py "$TEST_CMD"
elif in_py "python -c 'import pytest'" 2>/dev/null; then
  echo "== Test (pytest)"; in_py "python -m pytest -q"
elif [[ -f manage.py ]]; then
  echo "== Test (manage.py test)"; in_py "python manage.py test"
else
  echo "Nessun runner di test trovato: test non eseguiti."
fi
