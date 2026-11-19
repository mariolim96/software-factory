# Software factory per Claude Code, guidata da Odoo Project

Una software factory che prende i task da **Odoo Project** e li porta fino al merge con agenti
di **Claude Code**. Il PM scrive il task in Odoo e gli mette il tag della factory. La factory:

1. legge il task e la documentazione del progetto;
2. se manca una decisione, fa le domande al PM nel task e aspetta la risposta;
3. altrimenti scompone il lavoro in sottotask: back-end, front-end, documentazione;
4. realizza ogni sottotask con spec, sviluppo, controlli automatici (gate), review e merge;
5. fa giudicare il lavoro completo da un approver, unisce le PR quando il rischio lo consente
   e passa il task in Test al PM con le istruzioni per provarlo.

Le persone intervengono solo da Odoo e da GitHub: il PM risponde e collauda, il team leader
decide sui casi a rischio e sulle PR che la factory non unisce da sola.

Deriva da [software-factory-claude-code-v2](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2)
di ZazenCodes (vedi [NOTICE](NOTICE)). Il perché delle scelte è in [DESIGN.md](DESIGN.md).

## Indice

- [Come funziona](#come-funziona)
- [Chi fa che cosa](#chi-fa-che-cosa)
- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Uso quotidiano](#uso-quotidiano) (e [rinnovo della chiave Odoo](#rinnovare-la-chiave-api-di-odoo))
- [Agenti e profili](#agenti-e-profili)
- [Gate](#gate)
- [Review e merge automatici](#review-e-merge-automatici)
- [Domande al PM](#domande-al-pm)
- [Documentazione](#documentazione)
- [Costi, ore e metriche](#costi-ore-e-metriche)
- [Sicurezza](#sicurezza)
- [Comandi `sf`](#comandi-sf)
- [Problemi frequenti](#problemi-frequenti)
- [Sviluppo della libreria](#sviluppo-della-libreria)
- [Licenza](#licenza)

## Come funziona

### Il ciclo di un task

```
task padre in Odoo: tag della factory, To Do
  triage ── UNCLEAR → nota con le domande e attività al PM; il task resta in To Do
     │ PLAN: sottotask back-end, front-end, documentazione (con il contratto API)
     ▼
  per ogni sottotask della factory, in ordine (back-end, poi front-end, poi documentazione):
    spec-writer → builder → gate → revisori → verificatori → merge nel branch del padre
    · scelta funzionale non decisa → domande al PM, il padre torna in To Do finché risponde
    · gate rosso o finding confermati → nuovo giro del builder (al massimo max_rounds)
     ▼ tutti uniti
  approver sul lavoro completo
     │ APPROVE → la factory unisce le PR nei branch di integrazione, se il rischio lo consente;
     │           altrimenti le PR restano pronte per il team leader
     │ ESCALATE → nota e attività al team leader
     ▼
  sottotask front-end di una persona tutti Done?  no → tag "In attesa front-end"
     ▼ sì
  Test, assegnato al PM con la nota per il collaudo
     → Done, oppure di nuovo To Do con un commento: nuovo ciclo di correzione
```

### Branch e pull request

Per ogni task padre la factory crea un branch `factory/t<task>` in ogni repository toccato.
Ogni sottotask lavora su `factory/t<task>-s<sottotask>` e si unisce al branch del padre con
una PR piccola. Quando tutti i sottotask sono uniti, la PR del padre porta il lavoro completo
nel branch di integrazione (`main` o `develop`): la apre la factory, con rischio, test e come
annullarla nella descrizione.

I link alle PR sono anche in Odoo, come note interne: su ogni sottotask quando la sua PR viene
aperta e quando viene unita, e sul task padre quando le PR del padre sono pronte.

### Stadi in Odoo

| Factory | Odoo |
|---|---|
| triage, domande del triage, sottotask con domande al PM | To Do |
| building | In Progress |
| approve, pronto per il team leader | Review |
| test | Test |
| done | Done |

I sottotask vanno da To Do a In Progress, Review e Done, mai a Test: il collaudo è sul padre.

### I pezzi

| Pezzo | Dove | Ruolo |
|---|---|---|
| Coda | task Odoo con il tag della factory, in To Do | Che cosa costruire. |
| Spina | `sf`, pacchetto Python di questo repository | Macchina a stati, gate, coda, merge, scritture su Odoo e GitHub. Rifiuta le mosse non lecite e dice perché. |
| Orchestratore | skill `factory` di Claude Code | La sessione che lancia gli agenti e chiama `sf`. Non scrive codice né decisioni. |
| Agenti | `.claude/agents/` della home | Un file per ruolo. Ognuno scrive un file nella cartella del job e risponde con una riga. |
| Odoo MCP | `sf mcp` | Tre strumenti in sola lettura su Odoo, limitati al progetto. Gli agenti non scrivono mai su Odoo. |
| Profili | `templates/profiles/` | Gate, revisori e regole di uno stack. Ogni repository del progetto ha il suo. |
| Home | una cartella per progetto, fuori dai repository | Configurazione, controlli, clone dei repository, worktree, stato dei job. |

Nessun file della factory finisce nei repository del progetto, e un branch non può cambiare i
controlli con cui viene giudicato.

## Chi fa che cosa

**PM**
- Scrive il task in Odoo, nello stadio To Do, e gli mette il tag della factory. Con anche il tag
  `factory-fe`, la factory realizza pure il front-end.
- Risponde alle domande con un **commento** sul task: la factory lo legge alla run successiva.
- Collauda il task in Test seguendo la nota della factory. Se va bene lo porta in Done;
  altrimenti lo riporta in To Do con un commento che dice che cosa correggere.
- Per fermare un task toglie il tag della factory.

**Team leader**
- Riceve un'attività in Odoo quando la factory chiede una decisione: un builder bloccato, un
  approver che rifiuta, un merge che tocca percorsi ad alto rischio.
- Risponde con un commento sul task, oppure fa lui il merge delle PR su GitHub.
- Tiene verde il branch di integrazione: se il gate di `main` fallisce, la factory si ferma.

**Sviluppatore front-end** (quando il front-end non lo fa la factory)
- Riceve i sottotask front-end in Odoo, con il contratto API del back-end, e li porta in Done.

**Chi gestisce la factory**
- Lancia `/factory run` dalla home, o lo programma.
- Legge il riepilogo finale: che cosa aspetta una persona e che cosa va sistemato.

Solo i commenti del PM e del team leader configurati sono istruzioni. Il terminale serve a
lanciare e interrogare la factory, non a darle ordini sui task.

## Requisiti

- Linux o WSL2 (Ubuntu). La factory usa `fcntl` e script bash.
- Docker: il gate esegue build e test in container.
- `git` e `gh` (GitHub CLI) autenticato con diritto di push sui repository.
- [Claude Code](https://claude.com/claude-code) e [`uv`](https://docs.astral.sh/uv/).
- Un'istanza Odoo con l'app Project e una chiave API di un utente che vede il progetto.

## Installazione

```bash
uv tool install git+https://github.com/mariolim96/software-factory
sf init ~/factories/<progetto> --profile java-maven --repo <owner>/<repo-back-end> --base main
cd ~/factories/<progetto>
```

`sf init` crea la home, clona il repository in `repos/be/` e copia agenti, skill, profilo e
controlli. Per gli altri repository:

```bash
sf add-repo fe --profile angular --repo <owner>/<repo-front-end> --base main
sf add-repo docs --profile docs --repo <owner>/<repo-documentazione> --base main
```

Poi, nella home:

1. Completa `factory.json` (vedi [Configurazione](#configurazione)): gli id di Odoo e le regole
   di merge.
2. Salva la chiave API di Odoo con `sf odoo-key`: la chiede senza mostrarla, la prova su
   Odoo e la scrive nel file indicato da `odoo.credentials_file`, con permessi `600`. Il file
   può contenere anche `url`, `db` e `login`, che prevalgono su `factory.json`, e non deve mai
   finire in un repository.
3. Adatta `checks/<repo>/check.sh` alla CI del repository: deve fallire dove fallirebbe la CI.
4. Scrivi `PROJECT.md` (il progetto, l'architettura, dove sono i documenti, le aree delicate) e,
   se vuoi, `REVIEW.md` (che cosa conta come problema in review in questo progetto).
5. Se gli agenti devono leggere altri repository (sorgenti di un framework, documentazione),
   aggiungili a `references` ed esegui `sf refs`.
6. Esegui `sf doctor`: controlla credenziali, progetto, stadi, tag, utenti, `gh` e repository.
7. Esegui `sf gate --base`: il gate deve passare sul branch di integrazione così com'è.

Per aggiornare la libreria:

```bash
uv tool upgrade software-factory
sf init .        # dalla home: aggiorna agenti, skill e profili
```

`sf init` non tocca `factory.json`, `checks/`, `PROJECT.md`, `REVIEW.md` né le impostazioni.

### La home

```
~/factories/<progetto>/
  factory.json            configurazione
  PROJECT.md              il progetto, per gli agenti (scritto da voi)
  REVIEW.md               regole di review del progetto (scritto da voi)
  checks/<repo>/          controlli del gate per repository (adattati da voi)
  repos/<repo>/           clone di ogni repository
  worktrees/              un worktree per sottotask e per padre
  jobs/t<task>/           stato del padre: job.json, task.md, triage.md, decision.md
  jobs/t<task>-s<sub>/    stato del sottotask: spec.md, build.md, round-<N>/ con gate e review
  .claude/                agenti, skill, profili, regole di review, impostazioni (gestiti da sf init)
  .mcp.json               il server MCP di Odoo
```

## Configurazione

`factory.json` della home. I campi principali:

| Campo | Che cosa dice |
|---|---|
| `odoo.url`, `odoo.db` | L'istanza Odoo. |
| `odoo.credentials_file` | Il file con la chiave API, fuori dalla home e dai repository. |
| `odoo.project_id` | Il progetto i cui task sono la coda. |
| `odoo.stages` | Gli id degli stadi `todo`, `in_progress`, `review`, `test`, `done`. |
| `odoo.tags` | Gli id dei tag: `queue` (il tag della factory), `queue_fe` (anche il front-end alla factory), `frontend`, `backend`, `waiting_fe`, `escalation`. |
| `odoo.users` | Gli id utente di `pm`, `team_leader` e `frontend` (chi riceve i sottotask front-end). |
| `repos.<chiave>` | Ogni repository: `github`, `base_branch`, `profile`, e le regole che il profilo ha copiato (routing, revisori, percorsi). |
| `concurrency` | Quanti job lavorano insieme. |
| `max_rounds` | Giri di correzione di un sottotask prima di chiedere al team leader. |
| `merge_method` | `merge`, `squash` o `rebase` per le PR. |
| `merge_policy` | Per livello di rischio (`low`, `medium`, `high`): `auto` se la factory unisce da sola, `human` se lascia la PR al team leader. |
| `verify` | Verificatori dei finding importanti: `votes`, `confirm`, `model`. `votes: 0` spegne la verifica. |
| `high_risk_paths` | File che vogliono sempre una persona per il merge (migrazioni, sicurezza, dipendenze). |
| `protected_paths` | File che la factory non può modificare: il gate fallisce. |
| `routing`, `surface_reviewers`, `path_reviewers` | Quali revisori, su quale modello, per rischio, superficie e file toccati. |
| `references`, `refs_dir` | Repository in sola lettura per gli agenti. |
| `prices_usd_per_mtok` | Prezzi per il calcolo dei costi. |
| `timezone`, `timesheet_round_minutes` | Fuso orario e arrotondamento di `sf log`. |

Le regole per repository stanno in `repos.<chiave>` e prevalgono su quelle in cima al file.

Gli id di Odoo si trovano nell'URL della pagina dell'elemento (`.../project.task.type/93`) o con
la modalità sviluppatore. `sf doctor` dice quali mancano o non esistono.

## Uso quotidiano

Dalla home, in un terminale Linux o WSL:

```bash
cd ~/factories/<progetto>
claude
```

Poi, nella sessione di Claude Code:

```
/factory run          svuota la coda: task nuovi, risposte arrivate, job a metà
/factory run T<n>     un solo task
/factory <domanda>    stato, costi, ore, perché un job è fermo, che cosa aspetta qualcuno
```

`/factory run` riprende da dove si era fermata la sessione precedente: lo stato è nei file della
home. Alla fine stampa il riepilogo dei job e l'elenco di ciò che aspetta una persona.

Dopo un aggiornamento della libreria apri una **sessione nuova** (non `claude --continue`), così
Claude Code carica skill e agenti aggiornati.

### Rinnovare la chiave API di Odoo

Le chiavi API di Odoo scadono dopo la durata scelta alla creazione: su alcune istanze, un
giorno. Quando la chiave scade, `sf` risponde `HTTP 401: Invalid apikey` e la factory si
ferma. Per rinnovarla:

1. In Odoo: avatar in alto a destra → Il mio profilo → Sicurezza dell'account → Nuova chiave
   API. Scegli la durata più lunga disponibile e copia la chiave.
2. In un tuo terminale, nella home:
   ```bash
   cd ~/factories/<progetto>
   sf odoo-key                          # incolla la chiave: non viene mostrata
   sf odoo-key --also <altra copia>     # se tieni il file delle credenziali anche altrove
   ```
   `sf odoo-key` prova la chiave su Odoo prima di salvarla: una chiave sbagliata non sostituisce
   quella vecchia.
3. Rilancia `/factory run`. Non serve riavviare la sessione: `sf` e il server MCP rileggono la
   chiave al primo errore 401.

Non incollare la chiave nella sessione di Claude Code: deve restare tra te, il file delle
credenziali e `sf`. Per rinnovarla meno spesso, chiedi all'amministratore di Odoo una durata
massima più lunga per le chiavi del tuo utente.

## Agenti e profili

| Agente | Modello | Che cosa fa | Scrive |
|---|---|---|---|
| `triage` | fable | Decide se il task è chiaro, valuta rischio e superfici, pianifica i sottotask e il contratto API. | `triage.md` |
| `spec-writer` | opus | Trasforma un sottotask in una spec con criteri di accettazione verificabili, o fa domande al PM. | `spec.md` o `questions.md` |
| `builder` | opus | Realizza la spec sul branch del sottotask, con i test; prova il gate con `sf check`. | codice, `build.md` |
| `reviewer-<r>` | dal routing | Rivede il branch con finding etichettati per gravità. | `round-N/review-<r>.md` |
| `verifier` | sonnet | Conferma o respinge ogni finding importante, in modo indipendente. | `round-N/verify-<k>.md` |
| `approver` | opus | Giudica il lavoro completo del padre, il rischio, la nota per il collaudo. | `decision.md` |

Il modello dei revisori lo decide il routing di `factory.json`.

| Profilo | Stack | Revisori oltre a `code` e `security` |
|---|---|---|
| `java-maven` | Java, Maven, Spring Boot, Liquibase | `data`, `api` |
| `python` | Python (uv, poetry, pip; Alembic, Django) | `data`, `api` |
| `node` | Node (npm, pnpm, yarn; Prisma) | `data`, `api` |
| `odoo` | moduli Odoo 19/20, Community ed Enterprise | `orm`, `migrations`, `views` |
| `angular` | Angular | `ui`, `ux`, `contract`, `fe-security` |
| `react` | React | `ui`, `ux`, `contract`, `fe-security` |
| `docs` | documentazione Markdown | solo `docs` |

I revisori si scelgono da tre fonti, così uno non salta perché il triage ha sbagliato: il
rischio del sottotask, le superfici segnate dal triage (`security`, `data`, `api`, `ui`...) e i
file e le righe che il branch tocca davvero.

## Gate

Il gate è l'unico controllo deterministico prima della review:

- esegue `checks/<repo>/check.sh` nel worktree del sottotask e, se il profilo lo prevede,
  `upgrade.sh`, che installa la versione del branch di integrazione e poi aggiorna a quella del
  branch (per esempio le migrazioni del database su dati esistenti);
- fallisce se il branch modifica `protected_paths`;
- gira di nuovo prima di ogni merge, dopo aver allineato il branch alla sua base;
- a ogni `/factory run` controlla prima i branch di integrazione: se uno è rosso, la factory si
  ferma finché il team leader non lo sistema.

I controlli stanno nella home e non nel repository, quindi un branch non li può cambiare. I
profili eseguono build e test in container con la versione di Java, Node o Python del progetto;
le variabili in cima agli script li adattano.

## Review e merge automatici

La review segue le idee di [Solving code review at Anthropic](https://claude.dev/blog/solving-code-review-at-anthropic/):
il minimo di tempo umano che non aumenta revert e incidenti.

- **Gravità.** Ogni finding è `[Importante]` (blocca il merge), `[Nit]` o `[Preesistente]`
  (non bloccano). Le regole comuni sono in `.claude/review-rules.md`, quelle del progetto in
  `REVIEW.md`.
- **Verifica.** Ogni finding importante passa da `verify.votes` verificatori indipendenti e
  resta solo con almeno `verify.confirm` conferme. `sf` scrive `round-N/findings.md` per il
  builder e lo pubblica come commento sulla PR.
- **Convergenza.** Dal secondo giro i revisori controllano le correzioni e il diff dal giro
  prima; non ripropongono finding respinti; segnalano la classe di errore, non la singola
  istanza. Dopo `max_rounds` giri decide il team leader.
- **Rischio.** Il livello del padre è il più alto tra triage, sottotask e approver.
  `merge_policy` dice quali livelli la factory unisce da sola. `high_risk_paths` e un
  `NEEDS-HUMAN` dell'approver vogliono sempre una persona.
- **PR descritte.** La descrizione della PR del padre ha i sottotask, il riepilogo, il rischio,
  i test e come annullarla. I problemi preesistenti trovati in review diventano una nota e
  un'attività per il team leader.

```json
"merge_policy": { "low": "auto", "medium": "human", "high": "human" },
"verify": { "votes": 2, "confirm": 2, "model": "sonnet" }
```

`parent_merge` delle home precedenti vale ancora: `human` lascia tutto al team leader, `auto`
unisce tutto tranne il rischio alto.

## Domande al PM

La factory non decide da sola le scelte **funzionali**: che cosa vede o può fare l'utente, le
regole del dominio, i messaggi, i valori di default.

- **Triage.** Se il task non è chiaro, il triage scrive le domande (decisione UNCLEAR). La
  factory le pubblica come nota sul task, assegna un'attività al PM e il task resta in To Do.
- **Spec e sviluppo.** Se la lacuna emerge leggendo il codice, lo spec-writer (o il builder)
  scrive le domande invece della spec. La factory le pubblica sul task padre, lo riporta in To
  Do e assegna un'attività al PM.
- **Risposta.** Il PM risponde con un commento sul task. Alla run successiva la factory
  riparte da dove si era fermata, con la risposta tra le istruzioni.

Le domande sono scritte per chi non ha il codice: niente percorsi né nomi di classi, sigle
spiegate, per ogni domanda le alternative e quella proposta. Le scelte tecniche le prende la
factory; quelle tecniche che solo il team leader può prendere vanno a lui.

## Documentazione

Se il progetto ha un repository di documentazione (casi d'uso, requisiti, criteri di
accettazione, dubbi aperti), aggiungilo con il profilo `docs`:

```bash
sf add-repo docs --profile docs --repo <owner>/<repo-documentazione> --base main
```

Il triage pianifica allora anche sottotask di documentazione, realizzati dopo quelli di codice:
allineano i documenti al codice unito e alle decisioni del PM, senza rinumerare gli
identificativi. Il gate controlla spazi e link relativi, li rivede `reviewer-docs`, e il merge
segue `merge_policy` come per il codice.

Lascia il repository anche tra i `references`: gli agenti degli altri sottotask lo leggono da
lì, e `/factory run` lo aggiorna a ogni avvio con `sf refs`. Le regole del progetto (registro
delle variazioni, identificativi, file da non toccare) vanno in `PROJECT.md`.

## Costi, ore e metriche

**Costi.** `sf summary` mostra per ogni job stadio, giro, PR e costo. Il costo è l'equivalente
API dei token degli agenti, letto dalle trascrizioni di Claude Code con `prices_usd_per_mtok`.

**Ore.** `sf log` elenca per ogni ticket gli intervalli di lavoro della factory (agente o gate,
sottotask, inizio, fine, durata) e chiude con le ore del ticket e il totale:

```
T120  Esportazione CSV delle pratiche  (building)
  2026-11-18 17:24–17:25   0:01  T121    spec-writer          Spec T121
  2026-11-18 17:25–17:34   0:09  T121    builder              Build T121 round 1
  ore di lavoro 0:11 (ore agente 0:11)
```

- **Ore di lavoro**: l'unione degli intervalli, quindi i revisori in parallelo contano una volta.
  **Ore agente**: la somma.
- Per il timesheet: `sf log --timesheet --days 7` raggruppa per giorno e ticket, arrotonda per
  eccesso a `timesheet_round_minutes` (15 di default) ed elenca le attività svolte:

  ```
  2026-11-18 mer
    T120    0:15   0.25 h  Esportazione CSV delle pratiche
                           analisi, specifica, sviluppo, review, approvazione
    totale  0:15   0.25 h
  ```
- Filtri: `sf log T<n>`, `--days N`, `--since AAAA-MM-GG`, `--totals`.
- Gli intervalli degli agenti vengono dalle trascrizioni (dal primo all'ultimo messaggio; una
  pausa di più di 45 minuti li spezza) e ogni `sf advance` li copia in `job.json`, perché Claude
  Code cancella le trascrizioni vecchie. I gate li registra `sf`.
- Non contano il tempo dell'orchestratore tra un agente e l'altro né le attese di persone.

**Metriche.** `sf metrics [--days N]`: giri per arrivare al merge, finding confermati e
respinti, merge automatici per livello di rischio, revert dei task della factory. Servono a
decidere se allargare `merge_policy`.

## Sicurezza

- **Confine di fiducia.** Entrano solo i task del progetto configurato con il tag della
  factory. Togliere il tag ferma il task.
- **Istruzioni.** Sono istruzioni solo i commenti del PM e del team leader configurati.
  Descrizione, altri commenti ed email in arrivo sono dati citati, mai ordini. Il testo dei task
  arriva agli agenti in `task.md`, mai nel prompt.
- **Note della factory.** Sono note interne (mai messaggi ai follower), marcate e registrate,
  così non diventano istruzioni anche se la chiave è di una persona.
- **Odoo.** Gli agenti leggono Odoo con tre strumenti in sola lettura (`get_task`,
  `search_tasks`, `list_subtasks`), ognuno solo se il suo `tools:` lo elenca. Builder e
  revisori non hanno accesso a Odoo. Solo `sf` scrive su Odoo.
- **Credenziali.** Le impostazioni della home negano agli agenti la lettura del file delle
  credenziali. Usa una chiave dedicata, con la durata più lunga che l'istanza consente, e
  rinnovala con `sf odoo-key`: quando Odoo risponde 401, `sf` e il server MCP rileggono il file
  una volta, così le sessioni aperte continuano senza riavvio.
- **Git e GitHub.** Gli agenti fanno push solo del branch del loro job. Le impostazioni negano
  `gh pr merge`, i push forzati e i push sui branch di integrazione: i merge passano solo da
  `sf merge`.
- **Deploy.** La factory non fa deploy. Se il merge sul branch di integrazione fa partire un
  deploy, regolalo con `merge_policy` e `high_risk_paths`.

## Comandi `sf`

Li usa l'orchestratore, ma servono anche a mano per capire lo stato.

| Comando | Che cosa fa |
|---|---|
| `sf init <dir> --profile P --repo OWNER/NAME [--base B] [--key be\|fe\|docs]` | Crea o aggiorna una home. |
| `sf add-repo <chiave> --profile P --repo OWNER/NAME [--base B]` | Aggiunge un repository alla home. |
| `sf refs` | Clona o aggiorna i riferimenti in sola lettura. |
| `sf doctor` | Controlla Odoo, progetto, stadi, tag, utenti, `gh` e repository. |
| `sf odoo-key [--also FILE]` | Salva una nuova chiave API di Odoo dopo averla provata; `--also` aggiorna anche un'altra copia del file. |
| `sf queue` | Che cosa farebbe ora `/factory run`, una riga per job. |
| `sf summary [<job>...]` | Tabella dei job: stadio, giro, PR, costo. |
| `sf log [...]` | Ore di lavoro per ticket; `--timesheet` per il timesheet. |
| `sf metrics [--days N]` | Numeri di review e merge. |
| `sf task <job\|task>` | Mostra un task come lo vedono gli agenti. |
| `sf create <task>` | Crea il job di un task in coda. |
| `sf advance <job> <stadio>` | Sposta un job, o rifiuta con il motivo. |
| `sf check <job>` | Prova i controlli del gate nel worktree di un sottotask. |
| `sf gate <job>` / `sf gate --base [<repo>] [--force]` | Gate di un sottotask / del branch di integrazione. |
| `sf reviews <job>` | Aggiorna i verdetti del giro e stampa il passo successivo. |
| `sf merge <job>` | Allinea, ripete il gate e unisce le PR. |
| `sf header <job>` | La riga con cui comincia ogni prompt degli agenti. |
| `sf mcp` | Avvia il server MCP di Odoo (lo usa Claude Code). |

## Problemi frequenti

| Sintomo | Causa e rimedio |
|---|---|
| `HTTP 401: Invalid apikey` da `sf doctor` o nel riepilogo | La chiave API di Odoo è scaduta o revocata. Creane una nuova in Odoo (Il mio profilo → Sicurezza dell'account → Nuova chiave API) ed esegui `sf odoo-key` nella home. Le sessioni aperte la rileggono da sole. |
| "Il server MCP di Odoo non si è connesso" | Verifica con `claude mcp list` dalla home. Se `sf mcp` si chiude subito, aggiorna la libreria (`uv tool upgrade software-factory`); controlla anche la chiave Odoo. |
| `sf queue` risponde `pause` | Il branch di integrazione è rosso. Il report è nella cartella `jobs/_base/`: va sistemato sul branch, poi `sf gate --base`. |
| Righe `hold` | Tutti gli slot di `concurrency` sono occupati: i job ripartono quando uno finisce. |
| `<job> is busy` | Un altro comando `sf` sta lavorando sullo stesso job: aspetta che finisca. |
| Un job resta in `wait` | Aspetta una persona: la nota della riga dice chi e che cosa. |
| Skill o agenti vecchi dopo un aggiornamento | Esegui `sf init .` dalla home e apri una sessione nuova di Claude Code. |

## Sviluppo della libreria

```bash
uv run --no-project --with pytest --with mcp python -m pytest -q tests
```

I test usano un Odoo, `git` e `gh` finti e girano anche su Windows. La factory vera gira su
Linux o WSL.

- `src/software_factory/`: la spina (`jobs.py` macchina a stati, `queue.py`, `gate.py`,
  `review.py`, `hours.py`, `odoo.py`, `mcp_server.py`, `cli.py`).
- `src/software_factory/templates/core/`: skill, agenti comuni, impostazioni, regole di review.
- `src/software_factory/templates/common/` e `templates/profiles/`: revisori condivisi tra stack
  e profili.

## Licenza

[GPLv3](LICENSE), come il progetto da cui deriva.
