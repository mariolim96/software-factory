# Profilo: front-end Angular

Lo stack del front-end. Le regole del singolo progetto (cartelle, componenti condivisi, stile,
come si chiama il back-end) sono in `<home>/PROJECT.md` e prevalgono su queste.

## Superfici per il triage

Per i sottotask front-end il triage può segnare:
- `ui`: aspetto, layout, componenti visivi, accessibilità;
- `ux`: flussi, stati vuoti, di caricamento e di errore, messaggi all'utente;
- `api`: chiamate al back-end, modelli dei dati, gestione degli errori HTTP;
- `security`: autenticazione, guardie delle rotte, token, HTML reso da dati.

## Contratto API

Il front-end usa il contratto API del triage (`contract.md` nella cartella del job padre) così
com'è scritto: percorsi, nomi e tipi dei campi, codici di errore. Quando il back-end dello stesso
task è già unito nel suo branch, il codice vero (controller e file OpenAPI) è la conferma. Se il
contratto non basta, il builder scrive BLOCKED: non inventa campi né endpoint.

## Build e test

- Il gate esegue `npm ci`, la build di produzione (`ng build --configuration production`: fallisce
  su errori di tipo, template non validi, budget sforati) e i test unitari (Karma su Chrome
  headless), in un container con la versione di Node di `engines` in package.json. Il builder li
  prova con `sf check <job>`.
- Ogni componente o servizio nuovo ha il suo `*.spec.ts`, con lo stile dei test vicini (TestBed,
  servizi finti, `HttpTestingController` per le chiamate).
- Niente dipendenze npm nuove senza che la spec lo chieda: cambiano il bundle e la superficie
  d'attacco.

## Test end-to-end

Il gate non esegue gli E2E (Playwright o simili): richiedono tutto lo stack in piedi. Quindi,
quando un sottotask cambia, sposta o toglie un elemento dell'interfaccia (testo, etichetta,
ruolo, `data-testid`, rotta, passo di un flusso), il builder cerca negli E2E del repo gli spec che
lo usano (`grep` sul testo, sul selettore, sulla rotta) e li aggiorna nello stesso sottotask, così
che descrivano il comportamento nuovo. Se uno spec verifica proprio il comportamento che il task
cambia, lo adegua al task, non lo cancella. In build.md elenca gli spec toccati, o scrive che
nessuno spec usava gli elementi cambiati.

## Convenzioni Angular

- Componenti standalone, `inject()` o costruttore come nel codice vicino, signals dove il progetto
  li usa, `OnPush` dove il progetto lo usa.
- Le chiamate HTTP stanno nei servizi, non nei componenti; i tipi delle risposte sono interfacce
  nei modelli, allineate al contratto.
- Nessun `subscribe` senza gestione degli errori e della distruzione del componente
  (`takeUntilDestroyed`, `async` pipe).
- Testi in italiano, coerenti con quelli già presenti; niente stringhe di debug.
- Niente `innerHTML` o `bypassSecurityTrust*` su dati del server; niente token in `localStorage`
  se il progetto non lo fa già.
