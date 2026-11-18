# Profilo: front-end React

Lo stack del front-end (React con Vite, Next.js o simili, in TypeScript). Le regole del singolo
progetto (cartelle, componenti condivisi, librerie di stato e di UI, come si chiama il back-end)
sono in `<home>/PROJECT.md` e prevalgono su queste.

## Superfici per il triage

Per i sottotask front-end il triage può segnare `ui`, `ux`, `api` (chiamate al back-end, tipi dei
dati, errori HTTP) e `security` (autenticazione, rotte protette, token, HTML reso da dati).

## Contratto API

Il front-end usa il contratto API del triage (`contract.md` nella cartella del job padre) così
com'è scritto. Se il back-end dello stesso task è già unito nel suo branch, il codice vero è la
conferma. Se il contratto non basta, il builder scrive BLOCKED: non inventa campi né endpoint.

## Build e test

- Il gate installa le dipendenze dal lockfile, poi esegue gli script che il progetto definisce in
  package.json: `lint`, `typecheck` (o `tsc --noEmit`), `test` con `CI=true`, `build`. Tutto in un
  container con la versione di Node del progetto. Il builder li prova con `sf check <job>`.
- Ogni componente o hook nuovo ha il suo test, con lo stile dei test vicini (Testing Library,
  Vitest o Jest; chiamate HTTP finte con MSW o con i mock del progetto). Si testa ciò che vede
  l'utente, non i dettagli interni.
- Niente dipendenze npm nuove senza che la spec lo chieda.

## Test end-to-end

Il gate non esegue gli E2E (Playwright, Cypress): richiedono tutto lo stack in piedi. Quindi,
quando un sottotask cambia, sposta o toglie un elemento dell'interfaccia (testo, etichetta,
ruolo, `data-testid`, rotta, passo di un flusso), il builder cerca negli E2E del repo gli spec che
lo usano (`grep` sul testo, sul selettore, sulla rotta) e li aggiorna nello stesso sottotask, così
che descrivano il comportamento nuovo. Se uno spec verifica proprio il comportamento che il task
cambia, lo adegua al task, non lo cancella. In build.md elenca gli spec toccati, o scrive che
nessuno spec usava gli elementi cambiati.

## Convenzioni React

- Componenti funzione e hook; stato locale con `useState`/`useReducer`, stato server con la
  libreria del progetto (React Query, SWR, RTK Query...) e mai duplicato a mano.
- Le chiamate HTTP stanno nel livello API del progetto, non nei componenti; i tipi delle risposte
  sono allineati al contratto.
- Effetti con dipendenze complete e pulizia; niente fetch in `useEffect` dove il progetto usa una
  libreria per lo stato server.
- Liste con `key` stabili; niente `index` come chiave su liste che cambiano.
- Accessibilità: elementi semantici, etichette sui campi, focus gestito nei dialoghi.
- Niente `dangerouslySetInnerHTML` su dati del server; niente token in `localStorage` se il
  progetto non lo fa già.
