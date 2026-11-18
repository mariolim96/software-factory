# Profilo: back-end Python

Lo stack del back-end (FastAPI, Django, Flask o simili). Le regole del singolo progetto
(framework, cartelle, ORM, come si scrivono i test) sono in `<home>/PROJECT.md` e prevalgono su
queste.

## Superfici per il triage

Oltre a `security`, il triage può segnare `data` (modelli, query, transazioni, migrazioni) e `api`
(endpoint, schemi di richiesta e risposta, contratti OpenAPI esposti al front-end).

## Build e test

- Il gate installa le dipendenze dal lockfile del progetto (uv, poetry o requirements), esegue il
  pre-commit sui file toccati (o ruff, se non c'è pre-commit), mypy se configurato e i test
  (pytest o `manage.py test`), in un container con la versione di Python del progetto. Il builder
  li prova con `sf check <job>`.
- Ogni comportamento nuovo ha un test, con le fixture e lo stile dei test vicini. Le query si
  testano su un database vero quando il progetto lo fa già.
- Dipendenze nuove nel file del progetto (pyproject o requirements) e nel lockfile, mai installate
  a mano.

## Migrazioni dello schema

- Ogni cambio di modello che tocca il database ha una migrazione nuova (Alembic: revisione con
  `down_revision` giusto; Django: `makemigrations`); le migrazioni già nel branch di integrazione
  non si modificano.
- Le migrazioni devono funzionare su un database con dati: default o backfill per le colonne non
  nulle, niente DROP di ciò che il codice legge ancora.
- Il gate fa un test di upgrade: le migrazioni del branch di integrazione su un Postgres vuoto,
  poi quelle del branch sopra.

## Sicurezza

- Ogni endpoint nuovo dichiara chi può chiamarlo (dipendenza di autenticazione, permission class,
  decoratore), coerente con i ruoli del contratto; ogni oggetto letto o scritto appartiene a chi
  chiama.
- SQL solo con parametri (ORM, `text()` con bind, `%s` del driver), mai f-string o concatenazioni.
- Niente `eval`, `pickle` o `yaml.load` su input; niente `verify=False` nelle chiamate HTTP.
- Segreti solo da variabili d'ambiente.
