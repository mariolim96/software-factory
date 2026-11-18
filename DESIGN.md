# Design

Come funziona la factory e perché è fatta così. Ogni scelta qui sotto parte dalla
[v2 di ZazenCodes](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2/DESIGN.md)
e dice dove se ne discosta.

## L'idea

Come nel riferimento, una coda di richieste diventa codice unito attraverso una pipeline
fissa:
- gli **script** fanno le parti deterministiche: stato, gate, merge, scritture su Odoo;
- gli **agenti** fanno il giudizio: chiarezza, scomposizione, spec, codice, review,
  approvazione.

Ogni passo lascia un file con un `## Summary` iniziale.

## Le differenze dal riferimento

| Riferimento | Qui | Perché |
|---|---|---|
| Issue GitHub con label `factory` | Task Odoo Project in To Do con il tag della factory | Il team gestisce il lavoro in Odoo; PM e team leader sono già lì. |
| Intake: ACCEPT, NEEDS-INFO, REJECT | Triage: PLAN o UNCLEAR, con il piano dei sottotask | Un task Odoo è più grande di una issue: va scomposto. Il rifiuto lo decide una persona, togliendo il tag. |
| Una PR per issue verso `main` | Una PR per sottotask verso il branch del padre, poi una PR del padre verso il branch di integrazione | Il task arriva in Test intero, ma ogni pezzo è revisionato da solo. |
| Approver per issue | Revisori per sottotask, approver sul branch completo del padre | Nessun sottotask va in Test; l'approver vede l'insieme e il contratto API. |
| — | Sottotask front-end creati per una persona, con il contratto API | La factory fa solo il back-end. Il task va in Test quando il front-end è Done. |
| Script Python copiati nel repo (`factory/bin`) | Pacchetto `sf` e una **home** fuori dal repo | Si installa in ogni progetto senza aggiungere file al repo del cliente; un branch non può cambiare i controlli che lo giudicano. |
| `checks.sh` | `checks/check.sh` + `checks/upgrade.sh` del profilo | Il test di upgrade (schema o moduli) è un rischio specifico del back-end. |
| Routing da rischio e superfici | Anche dai file toccati e da pattern nel diff | Un revisore di sicurezza non si salta perché il triage ha sbagliato. |
| Gate prima della review e prima del merge | In più, il branch di integrazione da solo, prima di ogni run | Se è rosso, ogni job fallirebbe per colpe non sue: la factory si ferma. |
| Feedback dal terminale (`/factory <feedback>`) | Feedback come commenti in Odoo | L'autore del commento in Odoo è verificato; quello che si scrive nel terminale no. |
| Un repo | Uno o più repo per progetto (`be`, `fe`), ognuno con profilo, controlli e routing suoi | Con il tag `factory-fe` la factory fa anche il front-end: un branch e una PR del padre per repo, prima il back-end. |
| PR chiusa = fine | Nota per il test dell'approver, poi Test al PM | Il PM deve sapere che cosa provare e come. Se lo riporta in To Do, i suoi commenti successivi alla nota sono marcati NUOVO per il triage. |

## Stati

**Padre**: `triage → unclear | building → approve → merging | ready | needs-human → merged →
waiting-fe | test → done`. Un padre in `test` riportato in To Do dal PM riparte da `triage`
con un nuovo ciclo: i file del ciclo precedente vanno in `cycle-<n>/` e il branch diventa
nuovo. Un padre in `ready` o `needs-human` torna a `triage` quando il team leader commenta, e
il triage aggiunge i sottotask di correzione sullo stesso branch.

**Sottotask**: `queued → spec → build → gate → review → verify → merging → merged`, più
`needs-human`, da cui torna in `build` con il commento del team leader come `feedback-<k>.md`,
e `question`: lo spec-writer o il builder trovano una scelta funzionale che né il piano né la
documentazione decidono e scrivono `questions.md`. La factory pubblica le domande sul task
padre, lo riporta in To Do e assegna un'attività al PM. Il primo commento del PM o del team
leader dopo la nota riporta il sottotask in `spec` (il padre torna In Progress); le scelte
tecniche restano allo spec-writer, quelle che solo il team leader può prendere a `needs-human`.
`verify` c'è solo quando un revisore ha un finding `[Importante]`: i verificatori votano, e
il sottotask torna in `build` se resta almeno un finding confermato, altrimenti va al merge.
Lo stadio successivo lo dice `sf reviews` (`NEXT:`).

Le mosse legali sono in `PARENT` e `SUB` di `jobs.py`. `sf advance` controlla i file che le
giustificano. Esegue anche gli effetti su Odoo e GitHub: note, sottotask, stadi, tag,
attività, PR, branch.

Stadi di Odoo:

| Factory | Odoo |
|---|---|
| triage, unclear, un sottotask in question | To Do |
| building | In Progress |
| approve, ready | Review |
| test | Test |

I sottotask passano da To Do a In Progress, Review e Done, mai a Test.

## Fiducia

- **Il tag** decide che cosa entra. Lo mettono il PM o il team leader.
- **Le istruzioni** sono i commenti del PM e del team leader configurati: autore verificato
  in Odoo, tipo `comment`. Le email in arrivo sono escluse anche se sembrano scritte da loro,
  perché il mittente di un'email si falsifica. Tutto il resto arriva agli agenti come dato
  citato in `task.md`, mai nel prompt.
- **Le note della factory** finiscono in un registro (`jobs/.odoo-posted.json`) e
  cominciano con un marcatore. Così una nota scritta con la chiave del team leader non
  diventa una sua istruzione.
- **Odoo**: il client rifiuta i task fuori dal progetto. Scrive solo note interne
  (`mail.mt_note`), che non mandano email ai follower, nemmeno ai clienti del portale.
- **Gli agenti** non scrivono su Odoo. Leggono con tre strumenti MCP, elencati nel `tools:`
  di ciascuno. Il builder e i revisori non vedono Odoo.
- **Il merge** sul branch di integrazione lo fa la factory solo per i livelli di rischio che
  `merge_policy` le lascia. Il livello è il più alto tra quello del triage, dei sottotask e
  dell'approver, quindi un agente da solo non può abbassarlo. I file in `high_risk_paths` e un
  `NEEDS-HUMAN` dell'approver lo riservano sempre al team leader. Una PR non si unisce mai
  senza che il gate sia ripassato dopo l'allineamento con il branch di integrazione.

## Concorrenza

`concurrency` conta i job che stanno lavorando: padri in `triage`, `approve` o `merging`, e
sottotask in `spec`, `build`, `gate`, `review`, `verify` o `merging`. I sottotask dello stesso padre
vanno in sequenza, perché condividono il branch. Padri diversi vanno in parallelo.

Ogni modifica a un job prende un lock non bloccante sul job. `sf gate` legge senza lock e
scrive l'esito alla fine, così `sf queue` resta usabile durante un gate lungo.

## Costo

Come nel riferimento: si leggono le trascrizioni dei subagenti di Claude Code che contengono
`Job folder: <cartella>/`. È un formato non documentato: se cambia, il costo va a zero, ma
la factory continua a funzionare.

## Ore

Dalle stesse trascrizioni: ogni messaggio ha un `timestamp`, e accanto a ogni trascrizione
`agent-<id>.meta.json` dice il tipo di agente. Un agente lavora dal primo all'ultimo messaggio;
una pausa di più di 45 minuti (un agente ripreso dopo) apre un intervallo nuovo, mentre un
gate dentro l'agente (`sf check`) resta sotto la soglia. `sf advance` copia gli intervalli in
`job["work"]` con una chiave per agente e intervallo, così sopravvivono alla pulizia delle
trascrizioni; `sf gate` e `sf merge` aggiungono le loro esecuzioni del gate. `sf log` unisce
quello che è salvato con quello che le trascrizioni mostrano in quel momento, senza scrivere.

## Limiti noti

- Le regole `permissions` di Claude Code valgono per tutta la sessione, non per il singolo
  agente. Un agente con Bash potrebbe quindi lanciare un `sf advance`. Il prompt lo vieta e
  `sf` rifiuta le mosse senza i file che le giustificano, ma non è un confine forte. Lo
  stesso vale per la lettura delle credenziali da Bash. Per un confine vero serve la sandbox
  di Claude Code (bubblewrap) con regole di lettura negate.
- Il profilo `odoo` non è ancora stato provato su un progetto vero.
