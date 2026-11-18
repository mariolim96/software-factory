# Profilo: Java, Maven, Spring Boot, Liquibase

Lo stack del back-end. Le regole del singolo progetto (architettura, cartelle, documenti)
sono in `<home>/PROJECT.md` e prevalgono su queste.

## Superfici per il triage

Oltre a `security`, il triage può segnare:
- `data`: persistenza, query, transazioni, changelog Liquibase;
- `api`: endpoint REST, contratti OpenAPI, DTO esposti al front-end.

## Build e test

- Si usa il wrapper del progetto (`./mvnw`), mai un Maven di sistema.
- Test unitari: `*Test.java`, eseguiti da Surefire (`mvn test`). Integration test: `*IT.java`,
  eseguiti da Failsafe in `mvn verify`, con Postgres usa-e-getta di Testcontainers: serve
  Docker.
- Il gate esegue i controlli di `<home>/checks/` (di solito formattazione sui file toccati,
  lint dei contratti, guardia sui changelog, `mvn verify`). Il builder li prova con
  `sf check <job>`.

## Migrazioni dello schema (Liquibase)

- I changeset già nel branch di integrazione non si modificano mai: Liquibase ne memorizza
  il checksum e l'applicazione non riparte negli ambienti dove sono già applicati. Ogni
  modifica allo schema è un changeset nuovo, in un file nuovo, con il numero successivo.
- Ogni changeset deve funzionare anche su un database che ha già i dati: colonne NOT NULL
  nuove con un default o un backfill, niente DROP di colonne ancora lette dal codice.
- Il gate fa un test di upgrade: applica il changelog del branch di integrazione a un
  Postgres vuoto, poi quello del branch sopra.

## Contratti API

- Se il progetto genera interfacce e DTO da file OpenAPI, il contratto si cambia nel file
  YAML, mai nel codice generato. Le modifiche ai contratti devono essere retrocompatibili,
  salvo che il piano dica il contrario: il front-end lavora sul contratto.

## Sicurezza (Spring)

- Ogni endpoint nuovo ha una regola di autorizzazione esplicita (configurazione di Spring
  Security o annotazione), coerente con i ruoli del contratto.
- Niente SQL costruito concatenando input: parametri legati, Criteria, jOOQ con bind.
- Nessun segreto nei file di configurazione committati: variabili d'ambiente.
