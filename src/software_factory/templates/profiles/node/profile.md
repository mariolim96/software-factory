# Profilo: back-end Node

Lo stack del back-end (Express, Fastify, NestJS o simili, in TypeScript o JavaScript). Le regole
del singolo progetto (framework, cartelle, ORM, come si scrivono i test) sono in
`<home>/PROJECT.md` e prevalgono su queste.

## Superfici per il triage

Oltre a `security`, il triage può segnare `data` (modelli, query, transazioni, migrazioni) e `api`
(endpoint, DTO e validazione, contratti OpenAPI esposti al front-end).

## Build e test

- Il gate installa le dipendenze dal lockfile e poi esegue gli script che il progetto definisce in
  package.json: `lint`, `typecheck` (o `tsc --noEmit`), `test` con `CI=true`, `build`, in un
  container con la versione di Node del progetto. Se i test vogliono un database, il gate avvia un
  Postgres usa-e-getta e passa `DATABASE_URL` (`NEEDS_POSTGRES=1` in `checks/check.sh`). Il builder
  li prova con `sf check <job>`.
- Ogni comportamento nuovo ha un test, con lo stile dei test vicini (Jest, Vitest, node:test,
  supertest per le rotte).
- Dipendenze nuove in package.json e nel lockfile, mai installate globalmente.

## Migrazioni dello schema

- Ogni cambio di schema ha una migrazione nuova (Prisma: `prisma migrate dev` genera la cartella;
  knex, TypeORM, drizzle con il loro comando); le migrazioni già nel branch di integrazione non si
  modificano.
- Le migrazioni devono funzionare su un database con dati: default o backfill per le colonne non
  nulle, niente DROP di ciò che il codice legge ancora.
- Il gate fa un test di upgrade: le migrazioni del branch di integrazione su un Postgres vuoto, poi
  quelle del branch sopra (Prisma riconosciuto da solo, altrimenti `MIGRATE_CMD`).

## Sicurezza

- Ogni rotta nuova ha autenticazione e autorizzazione esplicite (middleware, guard), coerenti con
  i ruoli del contratto; ogni oggetto letto o scritto appartiene a chi chiama.
- Input validato con lo strumento del progetto (zod, class-validator, schemi di Fastify) prima di
  usarlo.
- SQL solo parametrizzato: niente `$queryRawUnsafe`, template string con input nelle query o
  concatenazioni.
- Niente `child_process` o `eval` con input; niente `rejectUnauthorized: false`; CORS limitato.
- Segreti solo da variabili d'ambiente, mai committati.
