#!/usr/bin/env bash
# Test di upgrade dello schema con Liquibase.
#
# Applica a un Postgres vuoto il changelog del branch di integrazione (FACTORY_TARGET_WORKTREE),
# poi quello del branch del job sopra lo stesso database. Fallisce se un changeset nuovo non si
# applica sopra lo schema esistente, o se un changeset già applicato è stato modificato
# (checksum diverso). L'installazione da zero la coprono già gli integration test.
set -euo pipefail

RESOURCES="${RESOURCES:-src/main/resources}"                  # relativo alla radice del repo
CHANGELOG="${CHANGELOG:-db/changelog/db.changelog-master.yaml}" # relativo a RESOURCES
LIQUIBASE_IMAGE="${LIQUIBASE_IMAGE:-liquibase/liquibase:4.31.1}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"

if [[ ! -f "$RESOURCES/$CHANGELOG" ]]; then
  echo "Nessun changelog in $RESOURCES/$CHANGELOG: test di upgrade saltato."
  exit 0
fi

name="sf-upgrade-$(echo "$FACTORY_JOB" | tr -c 'a-zA-Z0-9-' '-')"
cleanup() {
  docker rm -f "$name-db" >/dev/null 2>&1 || true
  docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
docker network create "$name" >/dev/null
docker run -d --name "$name-db" --network "$name" \
  -e POSTGRES_PASSWORD=factory -e POSTGRES_DB=factory "$POSTGRES_IMAGE" >/dev/null

# L'immagine avvia un server temporaneo per l'inizializzazione e poi lo riavvia: aspetta la fine.
for _ in $(seq 1 90); do
  if docker logs "$name-db" 2>&1 | grep -q "PostgreSQL init process complete" &&
     docker exec "$name-db" pg_isready -U postgres -d factory >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

apply() {
  docker run --rm --network "$name" -v "$1/$RESOURCES:/changelog:ro" "$LIQUIBASE_IMAGE" \
    --search-path=/changelog --changelog-file="$CHANGELOG" \
    --url="jdbc:postgresql://$name-db:5432/factory" --username=postgres --password=factory \
    update
}

echo "== Schema di $FACTORY_TARGET_BRANCH su un database vuoto"
apply "$FACTORY_TARGET_WORKTREE"
echo "== Upgrade allo schema del branch"
apply "$PWD"
echo "Upgrade dello schema riuscito."
