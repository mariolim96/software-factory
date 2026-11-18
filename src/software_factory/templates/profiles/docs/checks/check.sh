#!/usr/bin/env bash
# I controlli del gate per un repository di documentazione Markdown.
#
# Il gate esegue questo file con il worktree del job come directory corrente. Variabili:
# FACTORY_BASE_REF (il branch su cui il job si unisce), FACTORY_TARGET_REF, FACTORY_TARGET_WORKTREE.
# Sul branch di integrazione da solo (sf gate --base) non c'è niente da confrontare: passa.
set -euo pipefail

base="${FACTORY_BASE_REF:-origin/main}"
if [ "$(git rev-parse HEAD)" = "$(git rev-parse "$base")" ]; then
  echo "Nessun cambiamento rispetto a $base."
  exit 0
fi

echo "== Spazi e conflitti (git diff --check)"
git diff --check "$base"...HEAD

echo "== Link relativi nei file Markdown cambiati"
git diff --name-only --diff-filter=AMR "$base"...HEAD -- '*.md' | python3 -c '
import os, re, sys
bad = 0
link = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
for path in (l.strip() for l in sys.stdin if l.strip()):
    text = open(path, encoding="utf-8").read()
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    for target in link.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            continue
        dest = os.path.normpath(os.path.join(os.path.dirname(path), target.split("#")[0]))
        if target.split("#")[0] and not os.path.exists(dest):
            print(f"{path}: link a un file che non esiste: {target}")
            bad += 1
print(f"{bad} link rotti")
sys.exit(1 if bad else 0)
'
