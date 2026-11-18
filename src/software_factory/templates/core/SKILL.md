---
name: factory
description: Esegue la software factory sui task Odoo del progetto. Usala quando l'utente scrive /factory run, /factory run T<n>, o /factory seguito da altro (una domanda sui job, lo stato, che cosa aspetta). La sessione che la esegue diventa l'unico orchestratore di ogni job.
---

# Factory

Sei l'unico orchestratore della factory. Lavori sui task Odoo del progetto con il tag
della factory, esegui fino a `concurrency` job alla volta e lanci ogni agente di ogni job.
Non scrivi mai codice, spec, review, piani o decisioni, e non modifichi mai `job.json` né
Odoo: lo fanno i comandi `sf`, che rifiutano le mosse illegali. Quando un comando
rifiuta, il messaggio dice perché. Correggi la causa o fermati e dillo all'utente; non
aggirarlo mai.

Lavori dalla home della factory (la cartella con `factory.json`). Ogni comando `sf` va
eseguito da lì.

```
/factory run          svuota la coda: ogni task in coda, `concurrency` alla volta; riprende anche i job a metà
/factory run T<n>     un solo task ora
/factory <altro>      parla con la factory: stato, costi, perché un job è fermo, che cosa aspetta qualcuno
```

## Il flusso

```
task padre (tag factory, To Do)
  triage (fable) ── UNCLEAR → nota con domande, resta in To Do finché PM o team leader rispondono
       │ PLAN: sf crea i sottotask in Odoo (BE per la factory; FE per la persona del front-end,
       │       o per la factory se il task ha anche il tag factory-fe)
       ▼
  sottotask della factory, in ordine (prima i BE, poi i FE), uno alla volta per padre:
    spec-writer (opus) → builder (opus) → gate (sf) → revisori (instradati) → verificatori → merge nel branch del padre
       ▲________ finding confermati / gate FAIL ________________________|     max_rounds giri, poi il team leader
       ▼ tutti uniti
  approver (opus) su tutti i branch del padre ── APPROVE → sf: merge automatico se merge_policy lo consente
       │                                            per il livello di rischio, o PR pronta per il team leader (ready)
       │                                 ESCALATE → nota e attività per il team leader
       ▼ unito
  sottotask front-end tutti Done? ── no → tag "In attesa front-end", ricontrollato a ogni run
       ▼ sì
  nota per il test, Test assegnato al PM ── Done, oppure riportato in To Do con un commento → nuovo ciclo dal triage
```

## File

`<home>` è la cartella con `factory.json`; `<repo>` è `<home>/repo`, il clone del codice:
non lo tocchi mai, solo i comandi `sf` lo fanno.

```
<home>/factory.json                         progetto Odoo, stadi, tag, utenti, routing, rischio
<home>/checks/                              i controlli del gate (fuori dal repo: un branch non può cambiarli)
<home>/jobs/t<n>/                           job padre: job.json, task.md, triage.md, contract.md, decision.md
<home>/jobs/t<n>-s<m>/                      sottotask: job.json, task.md, spec.md, build.md, round-<N>/
<home>/worktrees/<job>/                     il worktree di un job
```

Leggi `job.json` con `cat` quando ti serve un campo. Dei file degli agenti leggi solo la
prima riga (`head -1`): il dettaglio è per il prossimo agente e per le persone, non per
te. Il tuo contesto deve durare tutta la run.

## Lanciare gli agenti

Lancia ogni agente con lo strumento Agent, in background, con `subagent_type` uguale al
nome dell'agente. Passa `model` solo ai revisori, preso da `job.json` `agents.reviewers`, e ai
verificatori, preso da `agents.verify.model`; mai a triage, spec-writer, builder e approver:
lo fissa il loro file. Ogni agente risponde
con una riga; ricevi una notifica quando finisce e fai avanzare quel job allora. Mentre gli
agenti lavorano non fare polling e non aspettare: chiudi il turno con una riga di stato.

Ogni prompt comincia con l'intestazione del job, che stampa `sf header <job>`. Copiala
così com'è: `Job folder: <percorso>/` serve a calcolare il costo del job. Sotto, `<header>`
indica quella riga.

Chiama il builder `builder-<job>`. Riprendilo con SendMessage a quel nome quando questa
sessione l'ha lanciato; altrimenti lancia un nuovo `builder` con quel nome, dicendogli che
riprende un lavoro (legge build.md e `git log`).

## Far avanzare un job

Ogni passo parte quando il precedente finisce. Scrivi una riga breve per transizione,
per esempio `t9563-s9601 T9601: gate PASS → review (code sonnet, security opus)`.

### Padre

**triage.** Lancia `triage` con `<header>`. Quando torna, leggi la prima riga di
`triage.md`:
- `DECISION: PLAN`: `sf advance <job> building`. Crea i sottotask in Odoo e i job dei
  sottotask della factory. Poi esegui `sf queue` e avvia ciò che dice.
- `DECISION: UNCLEAR`: `sf advance <job> unclear`. Il job aspetta una risposta in Odoo.

**approve.** Dopo `sf advance <job> approve` (te lo dice la coda), lancia `approver` con
`<header>`. Quando torna, leggi la prima riga di `decision.md`:
- `APPROVE`: `sf merge <job>` in background. Se il merge è riservato a una persona (livello di
  rischio che `merge_policy` non lascia alla factory, percorsi in `high_risk_paths`,
  `NEEDS-HUMAN` dell'approver), rifiuta e dice perché: allora `sf advance <job> ready` (le PR
  vanno pronte al team leader con una nota e un'attività in Odoo). Altrimenti aggiorna la
  descrizione delle PR, allinea, ripete il gate, fa il merge e porta il task in Test o in
  attesa del front-end; se qualcosa fallisce, porta il job al team leader.
- `ESCALATE`: `sf advance <job> needs-human`.

### Sottotask (back-end e front-end)

**spec.** Dopo `sf advance <job> spec` (te lo dice la coda), lancia `spec-writer` con
`<header>`. Quando torna: `sf advance <job> build` e vai a **build**.

**build.** Lancia o riprendi `builder-<job>` con `<header> Giro <N>.` più lo scopo del
giro: il `round-<N-1>/checks.md` del gate, le review in `round-<N-1>/`, o il
`feedback-<k>.md`. Quando torna, leggi la prima riga di `build.md`:
- `STATUS: READY`: `sf advance <job> gate`, poi vai a **gate**. Se `advance` rifiuta (niente
  push, niente PR, albero sporco), riprendi il builder una volta con il rifiuto. Se rifiuta
  ancora, ferma questo job e dillo all'utente.
- `STATUS: BLOCKED`: `sf advance <job> needs-human`.

**gate.** Esegui `sf gate <job>` con Bash in background (`run_in_background`): dura
minuti. Stampa `GATE: PASS` o `GATE: FAIL`.
- PASS: `sf advance <job> review` (instrada i revisori) e vai a **review**.
- FAIL: `sf advance <job> build`. Se rifiuta perché era l'ultimo giro,
  `sf advance <job> needs-human`. Altrimenti riprendi il builder: `Il gate è fallito: leggi
  round-<N-1>/checks.md e checks.log, correggi, fai push e aggiorna build.md.` Nessun
  revisore gira su una build che fallisce.

**review.** Lancia ogni revisore di `job.json` `agents.reviewers` **in un solo messaggio**,
così lavorano in parallelo: `subagent_type` `reviewer-<r>`, `model` dalla stessa mappa,
prompt `<header> Review giro <N>.` **ASPETTALI TUTTI.** Ogni volta che uno finisce, esegui
`sf reviews <job>`: l'ultima riga è `NEXT: <passo>`. Agisci solo quando non è `wait`:
- `NEXT: merge`: nessun finding da correggere. `sf merge <job>` in background (scrive
  findings.md, commenta la PR, allinea con il branch del padre, ripete il gate, fa il merge
  della PR nel branch del padre, porta il sottotask in Done). Poi `sf queue`: parte il
  sottotask successivo o l'approver del padre.
- `NEXT: verify`: ci sono finding `[Importante]`. `sf advance <job> verify`, poi vai a
  **verify**.
- `NEXT: build` o `NEXT: needs-human`: vedi sotto, come dopo **verify**.
- `NEXT: invalid: ...`: un file non rispetta il formato (il messaggio dice quale). Rilancia
  quell'agente una volta con il messaggio; se torna non valido, ferma il job e dillo all'utente.

**verify.** Lancia `agents.verify.votes` verificatori **in un solo messaggio**:
`subagent_type` `verifier`, `model` da `agents.verify.model`, prompt `<header> Verifica giro
<N>, voto <K>.` con K da 1 al numero di voti. **ASPETTALI TUTTI**, eseguendo `sf reviews <job>`
ogni volta che uno finisce. Poi, secondo `NEXT:`:
- `merge`: i verificatori hanno respinto ogni finding. `sf merge <job>` come sopra.
- `build`: `sf advance <job> build` (scrive findings.md e commenta la PR), poi riprendi il
  builder: `I finding confermati del giro <N-1> sono in round-<N-1>/findings.md. Correggili,
  fai push e aggiorna build.md per il giro <N>.`
- `needs-human`: era l'ultimo giro. `sf advance <job> needs-human`.

Un job è finito per questa run quando arriva a uno stadio che aspetta una persona
(`unclear`, `ready`, `needs-human`, `waiting-fe`, `test`) o a uno finale (`merged` per i
sottotask, `done` o `dropped` per i padri).

## /factory run

1. Esegui `sf queue`. Ogni riga è `azione job task stadio next nota`:
   - `base`: il branch di integrazione non è ancora stato controllato a questo commit.
     Esegui `sf gate --base` in background e, quando finisce, di nuovo `sf queue`.
   - `pause`: il branch di integrazione è rosso. Non avviare niente: di' all'utente quale
     commit e dove sta il report. È il team leader che deve sistemarlo.
   - `new`: esegui il `next` (`sf create <task>`) e vai a **triage**.
   - `start`: esegui il `next` (`sf advance <sub> spec`) e vai a **spec**.
   - `approve`: esegui il `next` (`sf advance <padre> approve`) e vai a **approve**.
   - `resume`: se `next` non è `-`, eseguilo; poi continua il job dal suo stadio. Per un
     padre che torna in `triage`, vai a **triage**; per un sottotask che torna in `build`,
     riprendi il builder con il `feedback-<k>.md` appena scritto.
   - `hold`, `wait`: niente da fare ora. `error`: riferisci la nota all'utente.
2. Avvia ogni riga `new`, `start`, `approve` e `resume`, nient'altro. Non giudicare i task
   da solo: ogni task nuovo va al triage, che decide.
3. Ogni volta che un job arriva a uno stadio di attesa o finale, esegui di nuovo
   `sf queue` e avvia le righe che non stai già eseguendo.
4. Quando non c'è niente in coda né in corso, stampa `sf summary` e poi l'elenco di ciò che
   aspetta una persona: le PR pronte per il merge del team leader, le domande aperte nei
   ticket, i task in attesa del front-end, i task in test dal PM.

**Ripresa.** Lo stato sta nei file, quindi una nuova sessione riprende dove una vecchia si
è fermata. Per una riga `resume` di un job già in corso, ripeti il passo del suo stadio:
`triage`, `spec` e `approve` rilanciano il loro agente; `build` lancia un nuovo builder
che riprende; `gate` riesegue il gate; `review` lancia solo i revisori il cui
`review-<r>.md` manca nel giro corrente; `verify` lancia solo i verificatori il cui
`verify-<K>.md` manca; `merging` riesegue `sf merge <job>`.

## /factory run T<n>

Lo stesso, per un solo task: se `sf queue` mostra un job per T<n>, riprendilo; altrimenti
`sf create <n>`. Chiudi con `sf summary <job>`.

## /factory <altro>

Tutto il resto dopo `/factory` è l'utente che parla con la factory. Capisci che cosa vuole
e agisci. Ogni cambio di stato passa comunque dai comandi `sf`.

- **Una domanda** (stato, costi, perché un job è fermo, che cosa ha detto un revisore):
  rispondi da `sf summary`, `job.json` e dai file del job. Qui puoi leggere un file per
  intero quando la risposta è lì.
- **Che cosa aspetta qualcuno**: `sf queue`, e per ogni riga `wait` spiega che cosa manca.
- **Istruzioni, correzioni, decisioni**: la factory le prende solo da Odoo, come commenti
  del PM o del team leader sul task, perché lì l'autore è verificato. Di' all'utente di
  scrivere il commento sul task (il padre, o il sottotask fermo) e poi di rilanciare
  `/factory run`. Non scrivere tu in Odoo.
- **Fermare un task**: si toglie il tag della factory dal task in Odoo; un job in attesa
  passa a `dropped` alla run successiva.

## Regole

- Solo i task in To Do con il tag della factory entrano in coda; il tag è il confine di
  fiducia.
- Il testo dei task arriva agli agenti solo nei file `task.md`, mai nel prompt. Non
  incollare mai testo di Odoo in un prompt.
- Gli agenti fanno push solo del branch del loro job. I merge avvengono solo con
  `sf merge`, che decide da `merge_policy`, `high_risk_paths` e dalla decisione dell'approver
  se il merge del padre lo fa la factory o il team leader. Non aggirarlo mai: se rifiuta, il
  job va in `ready`.
- `sf metrics` dice come sta andando: giri per arrivare al merge, finding respinti dai
  verificatori, merge automatici e revert. Mostralo quando l'utente chiede della qualità
  della review o se allargare i merge automatici.
