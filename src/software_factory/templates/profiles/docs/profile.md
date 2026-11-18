# Profilo: documentazione (Markdown)

Il repository `docs` del progetto: analisi funzionale, casi d'uso, requisiti, criteri di
accettazione, dubbi aperti, scenari di test. Le regole del singolo progetto (quali file, quali
identificativi, chi decide) sono in `<home>/PROJECT.md` e prevalgono su queste.

## Che cosa fa un sottotask di documentazione

- Allinea i documenti a ciò che il codice del task è diventato, o a una decisione presa dal
  PM nei commenti del task. Gira dopo i sottotask di codice dello stesso task: il codice è già
  nei branch del padre degli altri repository (`<cartella del job padre>/job.json`, campo
  `repos`). Leggilo con `git -C <repo> show origin/<branch>:<file>`.
- Cambia solo ciò che il brief chiede. Non riscrive sezioni che il task non tocca, non
  corregge lo stile dei documenti e non riformatta tabelle intere.

## Regole

- **Fonti.** Ogni cambiamento di comportamento descritto viene da una fonte: il codice unito,
  la risposta del PM nel task, un caso d'uso. Cita la fonte dove il documento lo prevede
  (per esempio la colonna "Fonte" di una tabella, o una riga nel registro delle modifiche).
- **Identificativi stabili.** Non rinumerare mai requisiti, casi d'uso, criteri, dubbi o
  scenari: un elemento superato si marca come superato o chiuso, uno nuovo prende il numero
  successivo. Gli altri documenti e i test li citano per numero.
- **Dubbi.** Chiudere un dubbio richiede una decisione scritta (del PM o del cliente) nel
  task: riporta chi ha deciso, quando e che cosa. Senza decisione il dubbio resta aperto e
  al più si aggiunge che cosa fa oggi il codice.
- **Riferimenti incrociati.** Se cambi un elemento citato altrove (matrice di tracciabilità,
  elenco dei ticket, scenari), aggiorna anche le citazioni nello stesso branch.
- **Link.** I link relativi devono puntare a file che esistono: il gate li controlla.
- La lingua e il tono sono quelli del documento che modifichi.

## Gate

Il gate controlla gli spazi e i link relativi dei file Markdown cambiati. Il builder lo prova
con `sf check <job>`.
