# Software factory per Claude Code, guidata da Odoo Project

Una software factory per il back-end che lavora sui task di **Odoo Project**. Metti il tag
`factory` su un task in To Do e lancia `/factory run`. Gli agenti:
- leggono il task e la documentazione;
- se manca qualcosa, chiedono nel chatter;
- altrimenti lo scompongono in sottotask back-end e front-end;
- realizzano i sottotask back-end uno per uno: spec, build, gate, review, merge sul branch
  del padre.

Quando il back-end è completo, l'approver giudica il branch intero e lo consegna per il merge.
Il task passa poi in Test al PM appena i sottotask front-end sono Done.

Deriva da [software-factory-claude-code-v2](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2)
di ZazenCodes: vedi [NOTICE](NOTICE). Il perché delle scelte è in [DESIGN.md](DESIGN.md).

Con il tag `factory-fe` sul task, la factory realizza anche i sottotask front-end, nel repo
front-end, dopo quelli back-end. Prima di passare il task in Test, pubblica sul task la nota
dell'approver per il PM: che cosa è cambiato e come provarlo. Se il PM riporta il task in
To Do, i commenti arrivati dopo quella nota sono le correzioni da cui riparte il triage.

## Il ciclo

```
task padre: tag factory, To Do
  triage (fable) ── UNCLEAR → nota interna con le domande, resta in To Do
       │ PLAN: sottotask BE (per la factory) e FE (per una persona, con il contratto API)
       ▼
  per ogni sottotask BE, in ordine:
    spec-writer → builder → gate (script) → revisori instradati → merge nel branch del padre
       ▼ tutti uniti                              (max_rounds giri, poi il team leader)
  approver sul branch completo → merge nel branch di integrazione (o PR pronta al team leader)
       ▼
  FE tutti Done? no → tag "In attesa front-end", ricontrollato a ogni run
       ▼ sì
  Test, assegnato al PM → Done, oppure To Do con commento → nuovo ciclo di correzione
```

## Pezzi

| Pezzo | Dove | Ruolo |
|---|---|---|
| Coda | task Odoo con il tag della factory, in To Do | Che cosa costruire. |
| Spina | `sf` (pacchetto Python) | Macchina a stati, gate, coda, merge, scritture su Odoo. Rifiuta le mosse illegali. |
| Orchestratore | skill `factory` | La sessione Claude Code che lancia gli agenti e chiama `sf`. |
| Agenti | `templates/core/agents`, `templates/profiles/<p>/agents` | Un file per ruolo; ognuno scrive un file e risponde con una riga. |
| Odoo MCP | `sf mcp` | Lettura di Odoo per gli agenti, limitata al progetto. Gli agenti non scrivono mai su Odoo. |
| Profili | `java-maven`, `python`, `node`, `odoo` (back-end); `angular`, `react` (front-end) | Gate, revisori e regole di uno stack. Un progetto ha uno o più repo (`be`, `fe`), ognuno con il suo profilo: `sf add-repo`. |
| Home | una cartella per progetto, fuori dal repo | `factory.json`, `checks/`, `PROJECT.md`, clone del repo, worktree, job. |

Agenti: `triage` (fable), `spec-writer`, `builder` e `approver` (opus), e i revisori, instradati
dal rischio, dalle superfici e dai file toccati:
- comuni: `code` e `security`;
- back-end (`java-maven`, `python`, `node`): `data` e `api`, con regole specifiche per Java;
- `odoo`: `orm`, `migrations` e `views`;
- front-end (`angular`, `react`): `ui`, `ux`, `contract` e `fe-security`.

I controlli di ogni profilo girano in container con la versione di Java, Node o Python del
progetto. Python e Node riconoscono da soli il gestore di pacchetti (uv, poetry, pip; npm, pnpm,
yarn) e le migrazioni (Alembic, Django, Prisma); per il resto si impostano variabili in cima a
`checks/check.sh` e `checks/upgrade.sh`.

## Installazione

Servono Linux o WSL2, Docker, `git`, `gh` autenticato con diritto di push, Claude Code e `uv`.

```bash
uv tool install git+https://github.com/<owner>/software-factory      # oppure: uv tool install -e <percorso>
sf init ~/factories/<progetto> --profile java-maven --repo <owner>/<repo> --base main
```

Poi, nella home appena creata:
1. completa `factory.json`: la sezione `odoo` con gli id di progetto, stadi, tag e utenti;
2. metti le credenziali Odoo (`{"api_key": "..."}`) nel file indicato da
   `odoo.credentials_file`, con permessi `600`;
3. adatta `checks/check.sh` alla CI del progetto e scrivi `PROJECT.md`;
4. esegui `sf refs` (riferimenti in sola lettura) e `sf doctor`;
5. esegui `sf gate --base`: deve passare sul branch di integrazione.

Lancia Claude Code dalla home e scrivi `/factory run`.

Rilanciare `sf init` sulla stessa cartella aggiorna agenti, skill e profilo. Non tocca invece
`factory.json`, `checks/`, `PROJECT.md` e le impostazioni.

## Comandi

```
/factory run          svuota la coda e riprende i job a metà
/factory run T<n>     un solo task
/factory <altro>      domande su job, costi, attese
```

Le decisioni passano da Odoo, non dal terminale. Il PM e il team leader rispondono con un
commento sul task, e la factory lo legge come istruzione alla run successiva. Per fermare
un task si toglie il tag.

## Sicurezza

- Entrano solo i task con il tag della factory: il tag è il confine di fiducia.
- Sono istruzioni solo i commenti (`message_type=comment`) del PM e del team leader
  configurati. Tutto il resto è dato citato: descrizione, altri commenti, email in arrivo.
  Le note della factory sono registrate e marcate, così non diventano istruzioni anche
  quando la chiave usata è quella del team leader.
- Su Odoo la factory scrive solo note interne, mai messaggi ai follower, e solo sui task
  del progetto configurato.
- Gli agenti leggono Odoo attraverso tre strumenti in sola lettura. La allowlist di ogni
  agente sta nel suo `tools:`. Il builder e i revisori non hanno accesso a Odoo.
- Il gate usa i controlli della home, fuori dal repo, e fallisce i branch che toccano
  `protected_paths`. Il merge sul branch di integrazione resta al team leader quando lo
  chiede `parent_merge: human`, quando il rischio è alto o quando sono toccati
  `high_risk_paths`.
- La factory fa push solo dei branch `factory/*` e fa merge solo con `sf merge`. Non fa
  deploy.

## Licenza

[GPLv3](LICENSE), come il progetto da cui deriva.
