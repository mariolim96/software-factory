#!/usr/bin/env bash
# I controlli del gate per un progetto Java/Maven. Exit 0 solo se il branch è pronto per la review.
#
# Il gate esegue questo file dalla home della factory con il worktree del job come
# directory corrente. Variabili disponibili: FACTORY_JOB, FACTORY_BASE_REF, FACTORY_BASE_BRANCH,
# FACTORY_TARGET_REF, FACTORY_TARGET_BRANCH, FACTORY_TARGET_WORKTREE (checkout del branch di
# integrazione: la copia fidata degli script del repo).
#
# Adattalo al progetto: replica la CI che blocca una pull request. Veloce quanto si può,
# deterministico, non interattivo, senza comandi che riscrivono file.
set -euo pipefail

# Il gate gira in shell non interattive, dove SDKMAN e nvm non si caricano: i percorsi vanno qui.
export JAVA_HOME="${JAVA_HOME:-$HOME/.sdkman/candidates/java/current}"
NODE_BIN=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)
export PATH="$JAVA_HOME/bin:${NODE_BIN:+$NODE_BIN:}$PATH"

MODULE="${MODULE:-.}"            # la cartella con pom.xml e mvnw
export MAVEN_ARGS="-B -ntp -e"

cd "$MODULE"
chmod +x ./mvnw 2>/dev/null || true
./mvnw verify
