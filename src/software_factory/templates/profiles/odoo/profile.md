# Profilo: moduli Odoo (19.0, 20.0)

Lo stack del back-end è un insieme di moduli Odoo. La versione e l'edizione (Community o
Enterprise) sono in `odoo_target` di `<home>/factory.json`. Le regole del singolo progetto
sono in `<home>/PROJECT.md` e prevalgono su queste.

## Il sorgente di Odoo

Il prompt elenca i riferimenti in sola lettura: tra questi il sorgente di Odoo alla versione
target (e di Enterprise, se il progetto la usa). Prima di usare un'API del framework, un
campo di un modello standard o una vista da ereditare, verificala lì: le API cambiano tra
le versioni, e un nome inventato fallisce solo all'installazione.

## Superfici per il triage

Oltre a `security`, il triage può segnare:
- `migrations`: cambi a campi esistenti, dati, script di migrazione, versione del modulo;
- `views`: viste XML, report QWeb, template OWL.
Il revisore ORM gira sempre.

## Struttura e convenzioni (OCA)

- Un modulo per cartella in radice, con `__manifest__.py` (`version` nel formato
  `<serie>.x.y.z`, per esempio `19.0.1.0.0`), `models/`, `views/`, `security/`, `data/`,
  `tests/`, `readme/`.
- Le dipendenze nuove vanno in `depends` del manifest; le librerie Python in
  `external_dependencies` e in `requirements.txt`.
- Il pre-commit del repo (stile OCA) è la legge sulla formattazione: il gate lo esegue sui
  file toccati.

## Test

- Test in `tests/` del modulo, importati in `tests/__init__.py`, con `TransactionCase` (o
  `HttpCase` per controller e tour). Ogni comportamento nuovo ha un test.
- Il gate installa i moduli toccati su un database Postgres nuovo con `--test-enable`, poi
  fa un test di upgrade: installa i moduli col codice del branch di integrazione e li
  aggiorna (`-u`) col codice del branch.

## Migrazioni e dati

- Ogni cambio che tocca dati esistenti (rinomina o cambio di tipo di un campo, campo
  calcolato reso memorizzato, record XML spostati) richiede uno script in
  `migrations/<versione>/pre-` o `post-migrate.py` e l'incremento di `version` nel manifest.
- I record in `noupdate="1"` non si aggiornano con `-u`: se vanno cambiati serve uno script.

## Sicurezza

- Ogni modello nuovo ha le sue righe in `security/ir.model.access.csv`; le regole sui record
  (`ir.rule`) limitano i dati per azienda o per utente dove serve.
- `sudo()` solo dove è indispensabile, su un insieme di record ristretto, mai su dati
  passati da chi chiama senza verifica.
- Niente SQL con input concatenato: l'ORM, o `SQL()` con parametri.
- Controller `auth="public"` o `csrf=False` solo se la spec lo motiva.
