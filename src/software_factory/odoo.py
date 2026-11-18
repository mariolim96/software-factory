"""Odoo Project client for the factory, over the JSON-2 API of Odoo 19+.

Every call goes through `Odoo`, which enforces the factory's policy whoever uses
it (the job scripts or the MCP server):

- only tasks of the configured project, read or written;
- chatter writes are internal notes only (`mail.mt_note`), never messages that
  email the followers: projects are often shared with the customer's portal;
- every note the factory posts starts with MARK and its id goes in a ledger, so a
  note posted with the team leader's own key is never read back as an
  instruction from the team leader.
"""

import html
import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

MARK = "🤖 Factory"


class OdooError(Exception):
    pass


# ---- HTML <-> text ---------------------------------------------------------

_BLOCK = {"p", "div", "ul", "ol", "table", "tr", "blockquote", "pre",
          "h1", "h2", "h3", "h4", "h5", "h6"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK:
            self.out.append("\n")
        elif tag in {"td", "th"}:
            self.out.append(" | ")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(value):
    if not value:
        return ""
    p = _Text()
    p.feed(value)
    text = "".join(p.out).replace("\xa0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)


def md_to_html(text):
    """The small Markdown subset agents write: paragraphs, lists, headings, code."""
    out = []
    parts = re.split(r"^```[^\n]*\n(.*?)^```[ \t]*$", (text or "").strip(), flags=re.M | re.S)
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f"<pre>{html.escape(part.rstrip())}</pre>")
            continue
        for block in re.split(r"\n\s*\n", part.strip()):
            lines = [line for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            bullet, number = r"^\s*[-*] ", r"^\s*\d+[.)] "
            if all(re.match(bullet, line) for line in lines):
                items = "".join("<li>" + _inline(re.sub(bullet, "", line)) + "</li>" for line in lines)
                out.append(f"<ul>{items}</ul>")
            elif all(re.match(number, line) for line in lines):
                items = "".join("<li>" + _inline(re.sub(number, "", line)) + "</li>" for line in lines)
                out.append(f"<ol>{items}</ol>")
            elif lines[0].startswith("#"):
                out.append(f"<h4>{_inline(lines[0].lstrip('#').strip())}</h4>")
                if lines[1:]:
                    out.append(f"<p>{'<br>'.join(_inline(line) for line in lines[1:])}</p>")
            else:
                out.append(f"<p>{'<br>'.join(_inline(line) for line in lines)}</p>")
    return "".join(out)


# ---- Client ----------------------------------------------------------------

TASK_FIELDS = ["name", "description", "stage_id", "tag_ids", "user_ids", "parent_id",
               "child_ids", "project_id", "create_date", "write_date"]


class Odoo:
    def __init__(self, cfg, ledger_path):
        o = cfg["odoo"]
        creds = json.loads(Path(o["credentials_file"]).expanduser().read_text(encoding="utf-8"))
        self.url = (creds.get("url") or o["url"]).rstrip("/")
        self.db = creds.get("db") or o["db"]
        self._key = creds["api_key"]
        self.project = o["project_id"]
        self.stages = o["stages"]
        self.tag_ids = o["tags"]
        self.users = o["users"]
        self.ledger_path = Path(ledger_path)
        self._partners = {}
        self._checked = set()

    # -- transport

    def call(self, model, method, **body):
        req = urllib.request.Request(
            f"{self.url}/json/2/{model}/{method}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"bearer {self._key}",
                "X-Odoo-Database": self.db,
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "software-factory",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("message")
            except Exception:
                msg = e.reason
            raise OdooError(f"{model}.{method}: HTTP {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            raise OdooError(f"{model}.{method}: {e.reason}") from None

    # -- policy

    def check_tasks(self, ids):
        """Refuse any task outside the configured project."""
        todo = [i for i in set(ids) if i not in self._checked]
        if not todo:
            return
        rows = self.call("project.task", "read", ids=todo, fields=["project_id"])
        found = {r["id"]: r["project_id"] for r in rows}
        bad = [i for i in todo if not found.get(i) or found[i][0] != self.project]
        if bad:
            raise OdooError(f"task {', '.join(map(str, bad))} isn't in project {self.project}")
        self._checked.update(todo)

    def _ledger(self):
        if self.ledger_path.exists():
            return set(json.loads(self.ledger_path.read_text()))
        return set()

    def _remember(self, message_id):
        ids = self._ledger() | {message_id}
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(sorted(ids)))

    def is_factory_message(self, msg):
        return msg["id"] in self._ledger() or html_to_text(msg["body"]).startswith(MARK)

    # -- reads

    def task(self, tid):
        self.check_tasks([tid])
        return self.call("project.task", "read", ids=[tid], fields=TASK_FIELDS)[0]

    def tasks(self, domain, fields=None, limit=None, order="id"):
        kw = {"limit": limit} if limit else {}
        return self.call("project.task", "search_read",
                         domain=[["project_id", "=", self.project], *domain],
                         fields=fields or ["name", "stage_id", "tag_ids", "user_ids", "parent_id"],
                         order=order, **kw)

    def tag_names(self, ids):
        if not ids:
            return {}
        return {r["id"]: r["name"] for r in self.call("project.tags", "read", ids=ids, fields=["name"])}

    def messages(self, tid):
        self.check_tasks([tid])
        return self.call("mail.message", "search_read",
                         domain=[["model", "=", "project.task"], ["res_id", "=", tid],
                                 ["message_type", "in", ["comment", "email"]]],
                         fields=["author_id", "date", "body", "message_type", "subtype_id", "attachment_ids"],
                         order="id")

    def partner_of(self, user_key):
        uid = self.users[user_key]
        if uid not in self._partners:
            row = self.call("res.users", "read", ids=[uid], fields=["partner_id"])[0]
            self._partners[uid] = row["partner_id"][0]
        return self._partners[uid]

    def trusted_partners(self):
        """Partners whose comments are instructions: the PM and the team leader."""
        return {self.partner_of("pm"): "PM", self.partner_of("team_leader"): "team leader"}

    def instructions(self, tid, after_id=0, msgs=None):
        """Comments from the PM or the team leader, newer than `after_id`.

        Only `comment` messages count: an inbound email (`email`) carries an
        author matched from the sender address, which anyone can forge.
        """
        trusted = self.trusted_partners()
        return [m for m in (msgs if msgs is not None else self.messages(tid))
                if m["id"] > after_id and m["message_type"] == "comment"
                and m["author_id"] and m["author_id"][0] in trusted
                and not self.is_factory_message(m)]

    def stage_key(self, stage):
        sid = stage[0] if isinstance(stage, list) else stage
        return next((k for k, v in self.stages.items() if v == sid), None)

    def task_url(self, tid):
        return f"{self.url}/odoo/project.task/{tid}"

    # -- writes

    def post_note(self, tid, text, title=None):
        self.check_tasks([tid])
        head = f"<p><b>{MARK}{' · ' + html.escape(title) if title else ''}</b></p>"
        res = self.call("project.task", "message_post", ids=[tid], body=head + md_to_html(text),
                        body_is_html=True, message_type="comment", subtype_xmlid="mail.mt_note")
        mid = res[0] if isinstance(res, list) else res
        self._remember(mid)
        return mid

    def create_subtask(self, parent, name, text, tags, user_ids):
        self.check_tasks([parent])
        vals = {
            "name": name,
            "parent_id": parent,
            "project_id": self.project,
            "description": md_to_html(text),
            "tag_ids": [[6, 0, [self.tag_ids[t] for t in tags]]],
            "user_ids": [[6, 0, list(user_ids)]],
            "stage_id": self.stages["todo"],
        }
        res = self.call("project.task", "create", vals_list=[vals])
        tid = res[0] if isinstance(res, list) else res
        self._checked.add(tid)
        return tid

    def set_stage(self, tid, key):
        self.check_tasks([tid])
        self.call("project.task", "write", ids=[tid], vals={"stage_id": self.stages[key]})

    def set_tags(self, tid, add=(), remove=()):
        self.check_tasks([tid])
        cmds = [[4, self.tag_ids[t]] for t in add] + [[3, self.tag_ids[t]] for t in remove]
        if cmds:
            self.call("project.task", "write", ids=[tid], vals={"tag_ids": cmds})

    def assign(self, tid, user_keys):
        self.check_tasks([tid])
        ids = [self.users[k] for k in user_keys]
        self.call("project.task", "write", ids=[tid], vals={"user_ids": [[6, 0, ids]]})

    def activity(self, tid, user_key, summary, text):
        self.check_tasks([tid])
        self.call("project.task", "activity_schedule", ids=[tid],
                  act_type_xmlid="mail.mail_activity_data_todo", summary=summary,
                  note=md_to_html(text), user_id=self.users[user_key])
