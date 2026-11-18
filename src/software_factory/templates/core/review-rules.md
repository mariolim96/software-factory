# Regole comuni della review

Valgono per ogni revisore della factory. Il file del revisore dice che cosa guardare; queste
regole dicono come scrivere i finding e come comportarsi dal secondo giro. `<home>/REVIEW.md`,
scritto dal team, le completa per il progetto e prevale dove sono in disaccordo.

## Che cosa è un finding

Un finding è un problema preciso, con una prova: `percorso:riga` e, quando serve, il comando
o il caso che lo mostra. Niente finding su ciò che "potrebbe" succedere senza dire come.
Prima di scriverlo, cerca nel codice la ragione per cui potrebbe non essere un problema
(un controllo a monte, un vincolo del database, un test che lo copre): se la trovi, non è
un finding.

## Severità

Ogni finding comincia con la sua severità tra parentesi quadre:

- `[Importante]`: va corretto prima del merge. Un bug, un requisito della spec mancante, un
  problema di sicurezza, un test indebolito, una violazione chiara delle regole del progetto,
  **introdotti da questo branch**.
- `[Nit]`: migliorerebbe il codice ma non blocca. Al massimo 5 per review (o quanti ne dice
  REVIEW.md): scegli i più utili.
- `[Preesistente]`: un problema reale che c'era già prima del branch e che il branch non
  peggiora. Non blocca: l'approver lo propone come task nuovo.

`VERDICT: CHANGES` se e solo se c'è almeno un finding `[Importante]`; altrimenti
`VERDICT: PASS`. Un verdetto che non corrisponde ai finding rende la review non valida.

## La classe, non l'istanza

Quando trovi un errore che può ripetersi (lo stesso controllo mancante su più endpoint, la
stessa query senza filtro in più punti), cerca tutte le occorrenze nel branch e scrivi un
solo finding che le elenca, con la regola da applicare. Il builder deve poterle correggere
tutte in un giro.

## Dal secondo giro

Il giro precedente ha lasciato `round-<N-1>/findings.md` (che cosa il builder doveva
correggere e che cosa i verificatori hanno respinto) e `round-<N-1>/checks.md`, la cui riga
`COMMIT:` è il commit che avete rivisto.

1. Per ogni finding della sezione "Da correggere" che è tuo, controlla che sia risolto. Se
   no, ripresentalo con lo stesso testo e il motivo per cui la correzione non basta.
2. Rivedi solo ciò che è cambiato dopo: `git diff <commit del giro precedente>..HEAD`. Un
   finding `[Importante]` nuovo è ammesso solo su righe cambiate in questo intervallo; quello
   che noti nel resto del codice è `[Nit]` o `[Preesistente]`.
3. Non ripresentare i finding che i verificatori hanno respinto e non riscrivere con parole
   diverse un finding già fatto.

Così la review converge: il secondo giro conferma le correzioni e non apre fronti nuovi.

## Formato

```markdown
VERDICT: PASS | CHANGES

## Summary
- da 3 a 5 punti brevi: che cosa hai controllato e che cosa hai trovato

## Findings
1. [Importante] <percorso:riga> <il problema, la prova e la correzione che chiedi>
2. [Nit] <percorso:riga> <...>
3. [Preesistente] <percorso:riga> <...>

## Note
Osservazioni senza un punto preciso del codice. Facoltative.
```

Sotto Findings scrivi "Nessuno." quando non ce ne sono. Numera i finding da 1: la factory li
chiama `<revisore>-<numero>` (per esempio `code-2`).
