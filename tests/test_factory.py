"""Tests for the spine with a fake Odoo and fake git/gh: the transitions and their side effects."""

import json

import pytest

from software_factory import jobs as jobs_mod
from software_factory import route
from software_factory.config import Home
from software_factory.odoo import html_to_text, md_to_html
from software_factory.util import Refused

CFG = {
    "concurrency": 2,
    "max_rounds": 2,
    "parent_merge": "human",
    "repos": {"be": {"github": "o/be", "base_branch": "main", "profile": "java-maven"},
              "fe": {"github": "o/fe", "base_branch": "main", "profile": "angular",
                     "routing": {"low": {"code": "haiku"}, "medium": {"code": "sonnet", "ui": "haiku"},
                                 "high": {"code": "sonnet", "ui": "sonnet"}}}},
    "odoo": {"project_id": 1},
    "routing": {"low": {"code": "haiku"}, "medium": {"code": "sonnet"}, "high": {"code": "sonnet"}},
    "surface_reviewers": {"security": {"security": "opus"}},
    "path_reviewers": {
        "data": {"model": "sonnet", "paths": ["**/db/changelog/**"], "patterns": ["@Transactional"]},
        "security": {"model": "opus", "paths": [], "patterns": ["permitAll"]},
    },
    "high_risk_paths": ["**/db/changelog/**"],
    "protected_paths": [".github/**"],
}

STAGES = {"todo": 1, "in_progress": 2, "review": 3, "test": 4, "done": 5}


class FakeOdoo:
    def __init__(self):
        self.tasks_db = {100: {"id": 100, "name": "Feature", "stage_id": [1, "TO DO"], "tag_ids": [9],
                               "parent_id": False, "child_ids": [], "project_id": [1, "P"],
                               "description": "<p>do it</p>", "user_ids": []}}
        self.notes, self.msgs, self.activities = [], [], []
        self.next_id = 200
        self.stages = STAGES
        self.tag_ids = {"queue": 9, "queue_fe": 4, "frontend": 7, "backend": 8, "waiting_fe": 6, "escalation": 5, "report": 10}
        self.users = {"pm": 24, "team_leader": 26, "frontend": 8}

    def task(self, tid):
        return self.tasks_db[tid]

    def tasks(self, domain, fields=None, limit=None, order="id"):
        ids = next((d[2] for d in domain if d[0] == "id"), list(self.tasks_db))
        return [self.tasks_db[i] for i in ids]

    def stage_key(self, stage):
        sid = stage[0] if isinstance(stage, list) else stage
        return next((k for k, v in STAGES.items() if v == sid), None)

    def task_url(self, tid):
        return f"https://x/odoo/project.task/{tid}"

    def post_note(self, tid, text, title=None):
        self.next_id += 1
        self.notes.append((tid, title, text))
        return self.next_id

    def instructions(self, tid, after_id=0, msgs=None):
        return [m for m in self.msgs if m["task"] == tid and m["id"] > after_id]

    chatter = ()

    def messages(self, tid):
        return [m for m in self.chatter if m["res_id"] == tid]

    def reported_tasks(self):
        return [t for t in self.tasks_db.values()
                if self.tag_ids["report"] in t["tag_ids"] and self.stage_key(t["stage_id"]) != "done"]

    def create_subtask(self, parent, name, text, tags, user_ids):
        self.next_id += 1
        self.tasks_db[self.next_id] = {"id": self.next_id, "name": name, "stage_id": [1, "TO DO"],
                                       "tag_ids": [self.tag_ids[t] for t in tags], "parent_id": [parent, "x"],
                                       "user_ids": list(user_ids), "description": text}
        return self.next_id

    def set_stage(self, tid, key):
        self.tasks_db[tid]["stage_id"] = [STAGES[key], key]

    def set_tags(self, tid, add=(), remove=()):
        t = self.tasks_db[tid]
        t["tag_ids"] = [i for i in t["tag_ids"] if i not in {self.tag_ids[r] for r in remove}]
        t["tag_ids"] += [self.tag_ids[a] for a in add if self.tag_ids[a] not in t["tag_ids"]]

    def assign(self, tid, keys):
        self.tasks_db[tid]["user_ids"] = [self.users[k] for k in keys]

    def activity(self, tid, user_key, summary, text):
        self.activities.append((tid, user_key, summary))


class FakeRes:
    def __init__(self, out="", code=0):
        self.stdout, self.stderr, self.returncode = out, "", code


@pytest.fixture
def factory(tmp_path, monkeypatch):
    (tmp_path / "factory.json").write_text(json.dumps(CFG))
    for k in ("be", "fe"):
        (tmp_path / "repos" / k).mkdir(parents=True)
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    for r in ("code", "security", "data", "ui"):
        (agents / f"reviewer-{r}.md").write_text("x")
    git_calls = []

    def fake_git(cwd, *args, check=True):
        git_calls.append(args)
        if args[:1] == ("ls-remote",):
            return FakeRes(code=2)
        if args[:2] == ("diff", "--name-only"):
            return FakeRes("src/db/changelog/010.sql\nsrc/App.java\n")
        if args[:1] == ("diff",):
            return FakeRes("+++ b/x\n+  @Transactional\n")
        return FakeRes()

    monkeypatch.setattr(jobs_mod, "git", fake_git)
    run_calls = []
    monkeypatch.setattr(jobs_mod, "run", lambda cmd, cwd, check=True, input=None, env=None:
                        run_calls.append((cmd, input)) or FakeRes())
    monkeypatch.setattr(jobs_mod, "cost_usd", lambda home, d: 0.0)
    monkeypatch.setattr(jobs_mod.taskdoc, "render", lambda odoo, tid, new_after=0: f"# T{tid}\n")
    f = jobs_mod.Factory(Home(tmp_path))
    f._odoo = FakeOdoo()
    f.git_calls = git_calls
    f.run_calls = run_calls
    return f


PLAN = """DECISION: PLAN
RISK: medium
SURFACES: none

## Summary
- ok

## Nota per il ticket
Si fa.

## Piano

```json
{"contract": "GET /x", "backend": [{"title": "API x", "brief": "fai x", "risk": "low"},
 {"title": "Schema", "brief": "tabella", "risk": "high", "surfaces": ["security"]}],
 "frontend": [{"title": "Pagina x", "brief": "mostra x", "contract": "GET /x"}]}
```
"""


def test_markdown_round_trip():
    html = md_to_html("# Titolo\n\n- uno\n- **due**\n\n1. a\n2. b\n\ntesto `x`\n\n```\ncodice <b>\n```")
    assert "<ul><li>uno</li><li><b>due</b></li></ul>" in html
    assert "<ol>" in html and "<pre>codice &lt;b&gt;</pre>" in html
    text = html_to_text(html)
    assert "- uno" in text and "codice <b>" in text


def test_route_by_risk_surface_path_and_pattern():
    r = route.route(CFG, "low", ["security"], ["a/db/changelog/1.sql"], "+++ b\n+ .permitAll()\n")
    assert r == {"code": "haiku", "security": "opus", "data": "sonnet"}
    assert route.route(CFG, "medium", [], ["App.java"], "") == {"code": "sonnet"}
    assert route.stronger("haiku", "opus") == "opus" and route.stronger("opus", "haiku") == "opus"


def test_triage_unclear_posts_a_note(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(
        "DECISION: UNCLEAR\nRISK: low\nSURFACES: none\n\n## Nota per il ticket\nServe sapere.\n\n## Domande\n1. Quale?\n")
    job = factory.advance(factory.locked(job["id"]), "unclear")
    assert job["stage"] == "unclear" and job["last_note"] > 0
    assert factory.odoo.notes[-1][1] == "servono chiarimenti" and "Quale?" in factory.odoo.notes[-1][2]
    with pytest.raises(Refused, match="no new comment"):
        factory.advance(job, "triage")
    factory.odoo.msgs.append({"task": 100, "id": job["last_note"] + 1})
    assert factory.advance(job, "triage")["stage"] == "triage"


def test_plan_creates_subtasks_and_sub_jobs(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    o = factory.odoo
    assert len(job["subs"]) == 2 and len(job["fe_tasks"]) == 1
    fe = o.tasks_db[job["fe_tasks"][0]]
    assert fe["user_ids"] == [8] and fe["tag_ids"] == [7] and "Contratto API" in fe["description"]
    sub = factory.load(job["subs"][1])
    assert sub["stage"] == "queued" and sub["base"] == "factory/t100" and sub["risk"] == "high"
    assert sub["repo"] == "be" and list(job["repos"]) == ["be"]
    assert o.tasks_db[100]["stage_id"][0] == STAGES["in_progress"]
    assert ("push", "origin", "origin/main:refs/heads/factory/t100") in factory.git_calls
    # retrying the same advance creates nothing twice
    n = len(o.tasks_db)
    factory._plan(job, factory.dir(job["id"]) / "triage.md")
    assert len(o.tasks_db) == n


def test_subs_run_in_order_and_illegal_moves_are_refused(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    second = factory.locked(job["subs"][1])
    with pytest.raises(Refused, match="earlier sub-task"):
        factory.advance(second, "spec")
    first = factory.locked(job["subs"][0])
    with pytest.raises(Refused, match="not a legal transition"):
        factory.advance(first, "build")
    first = factory.advance(first, "spec")
    with pytest.raises(Refused, match="spec.md"):
        factory.advance(first, "build")
    (factory.dir(first["id"]) / "spec.md").write_text("# spec")
    assert factory.advance(first, "build")["round"] == 1


def test_review_rounds_then_escalation(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    factory.cfg["verify"] = {"votes": 0}
    sub = factory.locked(job["subs"][0])
    sub.update(stage="review", round=2, round_limit=2,
               agents={"reviewers": {"code": "haiku"}}, reviews={"code": "pending"})
    factory.round_dir(sub).mkdir(parents=True)
    (factory.round_dir(sub) / "review-code.md").write_text("VERDICT: CHANGES\n")
    assert factory.review_next(sub).startswith("invalid")  # CHANGES without an Importante finding
    (factory.round_dir(sub) / "review-code.md").write_text("VERDICT: CHANGES\n\n## Findings\n1. [Importante] x\n")
    with pytest.raises(Refused, match="go to needs-human"):
        factory.advance(sub, "build")
    sub = factory.advance(sub, "needs-human")
    assert factory.odoo.activities[-1][1] == "team_leader"
    assert factory.odoo.tag_ids["escalation"] in factory.odoo.tasks_db[sub["task"]]["tag_ids"]


def test_gate_pass_routes_reviewers(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    sub = factory.locked(job["subs"][0])
    sub.update(stage="gate", round=1)
    factory.round_dir(sub).mkdir(parents=True)
    (factory.round_dir(sub) / "checks.md").write_text("GATE: PASS\nCOMMIT: \n")
    sub = factory.advance(sub, "review")
    assert sub["agents"]["reviewers"] == {"code": "haiku", "data": "sonnet"}


def test_human_merge_reasons_and_delivery(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    job["repos"]["be"]["worktree"] = str(factory.home.root)
    tier, reasons = factory._human_merge_reasons(job)
    assert tier == "high"  # a sub-task is high, and the approver wrote no RISK
    assert any("RISK" in r for r in reasons) and any("merge_policy" in r for r in reasons)
    assert any("sempre una persona" in r for r in reasons)
    job["stage"] = "merged"
    factory.deliver(job)
    assert job["stage"] == "waiting-fe"
    assert factory.odoo.tag_ids["waiting_fe"] in factory.odoo.tasks_db[100]["tag_ids"]
    factory.odoo.set_stage(job["fe_tasks"][0], "done")
    factory.deliver(job)
    t = factory.odoo.tasks_db[100]
    assert job["stage"] == "test" and t["stage_id"][0] == STAGES["test"] and t["user_ids"] == [24]
    assert factory.odoo.tag_ids["waiting_fe"] not in t["tag_ids"]


def _approved(factory, monkeypatch, decision, policy, files="src/App.java"):
    factory.cfg["merge_policy"] = policy
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN.replace('"risk": "high", ', ""))
    job = factory.advance(factory.locked(job["id"]), "building")
    job["repos"]["be"].update(worktree=str(factory.home.root), pr=7, pr_url="u")
    (factory.dir(job["id"]) / "decision.md").write_text(decision, encoding="utf-8")
    monkeypatch.setattr(factory, "changed_files", lambda wt, base: files.split())
    job["stage"] = "approve"
    return job


def test_merge_policy_lets_the_factory_merge_by_itself(factory, monkeypatch):
    job = _approved(factory, monkeypatch, "APPROVE\nRISK: low\nNEEDS-HUMAN: no\n\n## Summary\n- ok\n\n"
                    "## Come annullarla\nRevert, nessuna migrazione.\n\n## Problemi preesistenti\n- X lento\n",
                    {"low": "auto", "medium": "auto", "high": "human"})
    with pytest.raises(Refused, match="go to merging"):
        factory.advance(job, "ready")
    job = factory.advance(job, "merging")
    assert job["merge"] == {"tier": "medium", "mode": "auto", "reasons": []}  # the triage said medium
    body = json.loads(next(i for c, i in factory.run_calls if c[:2] == ["gh", "api"]))["body"]
    assert "Livello **medium**" in body and "Revert, nessuna migrazione." in body and "## Test" in body
    assert factory.odoo.notes[-1][1] == "problemi preesistenti"


def test_always_human_paths_and_the_approver_flag(factory, monkeypatch):
    allauto = {"low": "auto", "medium": "auto", "high": "auto"}
    job = _approved(factory, monkeypatch, "APPROVE\nRISK: low\nNEEDS-HUMAN: no\n", allauto,
                    files="src/db/changelog/011.sql")
    tier, reasons = factory._human_merge_reasons(job)
    assert len(reasons) == 1 and "011.sql" in reasons[0]
    (factory.dir(job["id"]) / "decision.md").write_text("APPROVE\nRISK: high\nNEEDS-HUMAN: cambia i ruoli\n")
    monkeypatch.setattr(factory, "changed_files", lambda wt, base: ["src/App.java"])
    tier, reasons = factory._human_merge_reasons(job)
    assert tier == "high" and reasons == ["l'approver chiede una persona: cambia i ruoli"]


def test_legacy_parent_merge():
    assert jobs_mod.merge_policy({"parent_merge": "human"})["low"] == "human"
    assert jobs_mod.merge_policy({"parent_merge": "auto"}) == {"low": "auto", "medium": "auto", "high": "human"}


def _in_review(factory, reviews):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    sub = factory.locked(job["subs"][0])
    sub.update(stage="review", round=1, round_limit=2, pr=5,
               agents={"reviewers": {r: "sonnet" for r in reviews}}, reviews={r: "pending" for r in reviews})
    factory.round_dir(sub).mkdir(parents=True)
    for r, text in reviews.items():
        (factory.round_dir(sub) / f"review-{r}.md").write_text(text, encoding="utf-8")
    return sub


CHANGES = ("VERDICT: CHANGES\n\n## Summary\n- x\n\n## Findings\n"
           "1. [Importante] a.java:3 null non gestito\n   seconda riga\n2. [Nit] nome\n"
           "3. [Preesistente] b.java:9 query lenta\n\n## Note\nniente\n")


def test_findings_are_verified_before_the_builder_sees_them(factory):
    sub = _in_review(factory, {"code": CHANGES, "security": "VERDICT: PASS\n\n## Findings\nNessuno.\n"})
    assert factory.review_next(sub) == "verify"
    with pytest.raises(Refused, match="verified first"):
        factory.advance(sub, "build")
    sub = factory.advance(sub, "verify")
    assert sub["agents"]["verify"] == {"votes": 2, "model": "sonnet"}
    assert factory.review_next(sub).startswith("wait")
    rd = factory.round_dir(sub)
    (rd / "verify-1.md").write_text("## Esiti\n- code-1: CONFERMATO, il null arriva da X\n", encoding="utf-8")
    (rd / "verify-2.md").write_text("## Esiti\n- code-1: RESPINTO: X non è mai null\n", encoding="utf-8")
    assert factory.review_next(sub) == "merge"  # confirm is 2 of 2 by default
    sub = factory.advance(sub, "merging")
    text = (rd / "findings.md").read_text(encoding="utf-8")
    assert "## Respinti dai verificatori" in text and "X non è mai null" in text
    assert sub["rounds_log"][0] == {"round": 1, "gate": None, "important": 1, "confirmed": 0, "rejected": 1,
                                    "nit": 1, "preexisting": 1}
    _cmd, body = next(c for c in factory.run_calls if c[0][:3] == ["gh", "pr", "comment"])
    assert "giro 1" in body and "query lenta" in body


def test_confirmed_findings_send_the_builder_back(factory):
    sub = _in_review(factory, {"code": CHANGES})
    sub = factory.advance(sub, "verify")
    rd = factory.round_dir(sub)
    for k in (1, 2):
        (rd / f"verify-{k}.md").write_text("- `code-1`: **CONFERMATO** sì\n", encoding="utf-8")
    assert factory.review_next(sub) == "build"
    with pytest.raises(Refused, match="findings to fix: code-1"):
        factory.advance(sub, "merging")
    sub = factory.advance(sub, "build")
    assert sub["round"] == 2
    text = (factory.round_dir(sub, 1) / "findings.md").read_text(encoding="utf-8")
    assert "**code-1** (2/2) a.java:3 null non gestito\n   seconda riga" in text


def test_a_verifier_must_vote_on_every_finding(factory):
    sub = _in_review(factory, {"code": CHANGES})
    sub = factory.advance(sub, "verify")
    rd = factory.round_dir(sub)
    (rd / "verify-1.md").write_text("- code-1: CONFERMATO\n")
    (rd / "verify-2.md").write_text("niente\n")
    assert factory.review_next(sub).startswith("invalid: verify-2.md has no vote for code-1")


def test_without_verification(factory):
    factory.cfg["verify"] = {"votes": 0}
    sub = _in_review(factory, {"code": "VERDICT: PASS\n\n## Findings\n1. [Nit] spazi\n"})
    assert factory.review_next(sub) == "merge"
    sub["reviews"] = {"code": "pending"}
    (factory.round_dir(sub) / "review-code.md").write_text(CHANGES, encoding="utf-8")
    assert factory.review_next(sub) == "build"
    with pytest.raises(Refused, match="verification is off"):
        factory.advance(sub, "verify")


def test_reopened_by_pm_starts_a_new_cycle(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    job["stage"] = "test"
    with pytest.raises(Refused, match="To Do"):
        factory.odoo.set_stage(100, "test")
        factory.advance(job, "triage")
    factory.odoo.set_stage(100, "todo")
    job = factory.advance(job, "triage")
    assert job["cycle"] == 2 and factory.branch_name(job) == "factory/t100-c2" and job["subs"] == []
    assert job["repos"] == {} and job["past"][0]["repos"]["be"]["branch"] == "factory/t100"
    assert (factory.dir(job["id"]) / "cycle-1" / "triage.md").exists()
    assert job["past"][0]["fe_tasks"]


def _fe_report(factory, mid=569187):
    """A front-end task T300 (person, not the factory) under T100, with a comment of the
    front-end and the report tag."""
    o = factory.odoo
    o.tasks_db[300] = {"id": 300, "name": "UC002-FE-06 Step 3", "stage_id": [2, "DEV"], "tag_ids": [7, 10],
                       "parent_id": [100, "Feature"], "child_ids": [], "description": "", "user_ids": [8]}
    o.tasks_db[100]["stage_id"] = [2, "DEV"]  # the parent is a person's task, not in the factory
    o.chatter = [{"id": mid, "res_id": 300, "author_id": [12, "Ale"], "date": "2026-11-19 10:35:10",
                  "body": "<p>Il FE non sa se chi guarda è una consorziata</p>", "message_type": "comment"}]


BACKEND_VERDICT = """VERDICT: BACKEND
RISK: high

## Titolo
BE-09 Ruolo di accesso alla pratica

## Task
GET /istanze/{id} restituisce ruoloAccesso: TITOLARE | CONSORZIATA_ACCETTATA.

## Nota per il front-end
Verificato: il dettaglio non dice come l'azienda accede.

## Perché
Il DTO non ha il campo.
"""


def test_front_end_report_opens_a_back_end_task_in_the_queue(factory, monkeypatch):
    from software_factory import queue as queue_mod
    from software_factory import reports
    monkeypatch.setattr(queue_mod, "base_report", lambda home, key: ("PASS", "abc1234", ""))
    o = factory.odoo
    o.tasks_db[301] = {"id": 301, "name": "FE senza tag", "stage_id": [2, "DEV"], "tag_ids": [7],
                       "parent_id": [100, "Feature"], "child_ids": [], "description": "", "user_ids": [8]}
    _fe_report(factory)

    rows = queue_mod.queue(factory)
    assert [r[:5] for r in rows if r[0] == "report"] == [("report", "r300", "T300", "-", "sf report start 300")]
    rep = reports.start(factory, 300)
    d = factory.home.jobs / "r300-1"
    assert rep["id"] == "r300-1" and rep["last_message"] == 569187
    assert "# T300" in (d / "report.md").read_text(encoding="utf-8")
    assert "Task front-end: T300" in reports.report_header(factory, rep)
    assert reports.start(factory, 300) == rep  # already open: same report
    rows = queue_mod.queue(factory)
    assert ("resume", "r300-1", "T300", "check", "-") in [r[:5] for r in rows]
    assert not any(r[0] == "report" for r in rows)
    assert factory.all() == []  # a report isn't a job

    with pytest.raises(Refused, match="VERDICT"):
        reports.close(factory, 300)
    (d / "verdict.md").write_text(BACKEND_VERDICT, encoding="utf-8")
    rep = reports.close(factory, 300)
    new = rep["task"]
    t = o.tasks_db[new]
    assert t["parent_id"][0] == 100 and t["name"] == "BE-09 Ruolo di accesso alla pratica"
    assert sorted(t["tag_ids"]) == sorted([o.tag_ids["queue"], o.tag_ids["backend"]])
    assert t["stage_id"][0] == STAGES["todo"] and t["user_ids"] == [26]
    assert "ruoloAccesso" in t["description"] and "T300" in t["description"]
    assert o.notes[-1][:2] == (300, "segnalazione: serve il back-end") and f"T{new}" in o.notes[-1][2]
    assert rep["stage"] == "closed" and rep["verdict"] == "BACKEND"
    assert o.tag_ids["report"] not in o.tasks_db[300]["tag_ids"]  # the tag goes: done
    with pytest.raises(Refused, match="no open report"):
        reports.close(factory, 300)

    # the queue takes the new sub-task like any tagged task; its job knows the front-end task
    rows = queue_mod.queue(factory)
    assert (f"T{new}", f"sf create {new}") in [(r[2], r[4]) for r in rows if r[0] in {"new", "hold"}]
    job = factory.create(new)
    assert job["report"] == {"id": "r300-1", "fe_task": 300}
    assert "in T300" in factory.header(job) and "\"task\": 300" in factory.header(job)

    d = factory.dir(job["id"])
    (d / "triage.md").write_text(PLAN)  # a new front-end sub-task: refused, T300 is the front-end
    with pytest.raises(Refused, match="the front-end of this task is T300"):
        factory.advance(factory.locked(job["id"]), "building")
    (d / "triage.md").write_text(PLAN.replace('"contract": "GET /x"}]}', '"contract": "GET /x", "task": "T300"}]}'))
    n = len(o.tasks_db)
    job = factory.advance(factory.locked(job["id"]), "building")
    assert len(o.tasks_db) == n + 2 and job["fe_tasks"] == []  # two back-end sub-tasks, no front-end
    assert any(t == 300 and title == "aggiornamento per il front-end" for t, title, _ in o.notes)

    # delivered: the front-end task hears that its back-end is merged
    job["stage"] = "merged"
    job["repos"]["be"]["pr_url"] = "https://github.com/o/be/pull/7"
    factory.deliver(job)
    assert job["stage"] == "test"
    assert o.notes[-1][:2] == (300, "back-end pronto") and "pull/7" in o.notes[-1][2]


@pytest.mark.parametrize("verdict, title", [
    ("VERDICT: FRONTEND\n\n## Risposta\nUsa il campo azienda di GET /me.\n", "segnalazione: il back-end c'è già"),
    ("VERDICT: DUPLICATE\nTASK: T777\n\n## Risposta\nCe n'è già uno.\n", "segnalazione: già in lavorazione"),
    ("VERDICT: QUESTION\n\n## Domande per il PM\n1. La consorziata vede l'elenco?\n", "segnalazione: domande per il PM"),
])
def test_report_answers_on_the_front_end_task(factory, verdict, title):
    from software_factory import reports
    o = factory.odoo
    _fe_report(factory)
    reports.start(factory, 300)
    (factory.home.jobs / "r300-1" / "verdict.md").write_text(verdict, encoding="utf-8")
    n = len(o.tasks_db)
    rep = reports.close(factory, 300)
    assert len(o.tasks_db) == n and o.notes[-1][:2] == (300, title)
    assert ([a[:2] for a in o.activities] == [(300, "pm")]) == ("QUESTION" in verdict)
    assert rep["task"] == (777 if "DUPLICATE" in verdict else None)
    assert o.tag_ids["report"] not in o.tasks_db[300]["tag_ids"]


def test_report_only_on_tagged_tasks_and_again_when_retagged(factory):
    from software_factory import reports
    o = factory.odoo
    _fe_report(factory)
    o.set_tags(300, remove=["report"])  # a comment without the tag: nothing
    assert reports.pending(factory) == []
    with pytest.raises(Refused, match="isn't tagged"):
        reports.start(factory, 300)

    o.set_tags(300, add=["report"])
    reports.start(factory, 300)
    (factory.home.jobs / "r300-1" / "verdict.md").write_text("VERDICT: NONE\n\n## Perché\nÈ un grazie.\n", encoding="utf-8")
    reports.close(factory, 300)
    assert o.notes == [] and o.activities == [] and reports.pending(factory) == []

    # tagged again after a new comment: a second report, the new comments marked as such
    o.chatter = list(o.chatter) + [dict(o.chatter[0], id=569300)]
    o.set_tags(300, add=["report"])
    assert [t["id"] for t in reports.pending(factory)] == [300]
    rep = reports.start(factory, 300)
    assert rep["id"] == "r300-2" and rep["last_message"] == 569300
    assert "marcati nuovo" in (factory.home.jobs / "r300-2" / "report.md").read_text(encoding="utf-8")
    assert reports.task_id("r300-2") == reports.task_id("T300") == 300

    o.set_stage(300, "done")  # a Done task is never reported
    (factory.home.jobs / "r300-2" / "verdict.md").write_text("VERDICT: NONE\n")
    reports.close(factory, 300)
    o.set_tags(300, add=["report"])
    assert reports.pending(factory) == []


def test_plan_validation(tmp_path):
    p = tmp_path / "triage.md"
    p.write_text("DECISION: PLAN\n\n## Piano\n```json\n{\"backend\": [], \"frontend\": []}\n```\n")
    with pytest.raises(Refused, match="no sub-task"):
        jobs_mod.read_plan(p, CFG)
    p.write_text("DECISION: PLAN\n\n## Piano\n```json\n{\"backend\": [{\"title\": \"x\", \"brief\": \"y\", \"risk\": \"huge\"}]}\n```\n")
    with pytest.raises(Refused, match="risk"):
        jobs_mod.read_plan(p, CFG)


def test_factory_builds_the_front_end_when_tagged(factory):
    factory.odoo.tasks_db[100]["tag_ids"].append(4)  # queue_fe
    job = factory.create(100)
    assert job["fe_mode"] == "factory"
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    subs = [factory.load(s) for s in job["subs"]]
    assert [s["repo"] for s in subs] == ["be", "be", "fe"] and job["fe_tasks"] == []
    fe = subs[2]
    assert fe["base"] == "factory/t100" and factory.odoo.tasks_db[fe["task"]]["user_ids"] == [26]
    assert sorted(job["repos"]) == ["be", "fe"]
    assert "Repo: " in factory.header(fe) and "(fe)" in factory.header(fe)
    assert "Front-end: lo realizza la factory" in factory.header(job)
    # the front-end sub-task routes with the angular repo's own table
    fe = factory.locked(fe["id"])
    fe.update(stage="gate", round=1, risk="medium")
    factory.round_dir(fe).mkdir(parents=True)
    (factory.round_dir(fe) / "checks.md").write_text("GATE: PASS\nCOMMIT: \n")
    assert factory.advance(fe, "review")["agents"]["reviewers"] == {"code": "sonnet", "ui": "haiku", "data": "sonnet"}


def test_test_note_comes_from_the_approver(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    (factory.dir(job["id"]) / "decision.md").write_text(
        "APPROVE\n\n## Summary\n- ok\n\n## Nota per il test\nApri la pagina X come referente.\n")
    factory.odoo.set_stage(job["fe_tasks"][0], "done")
    job["stage"] = "merged"
    factory.deliver(job)
    tid, title, text = factory.odoo.notes[-1]
    assert title == "pronto per il test" and text.startswith("Apri la pagina X come referente.")
    assert factory.odoo.tasks_db[100]["stage_id"][0] == STAGES["test"]


@pytest.mark.parametrize("profile", ["java-maven", "angular", "react", "python", "node", "odoo", "docs"])
def test_every_routed_reviewer_has_an_agent(tmp_path, monkeypatch, profile):
    from software_factory import scaffold
    monkeypatch.setattr(scaffold, "run", lambda *a, **k: None)  # no git clone
    root, _ = scaffold.init(tmp_path / "home", profile, "o/r", "main")
    cfg = json.loads((root / "factory.json").read_text(encoding="utf-8"))
    rc = {**cfg, **cfg["repos"]["be"]}
    named = set()
    for table in rc["routing"].values():
        named |= set(table)
    for table in rc.get("surface_reviewers", {}).values():
        named |= set(table)
    named |= set(rc.get("path_reviewers", {}))
    agents = {p.stem.removeprefix("reviewer-") for p in (root / ".claude" / "agents").glob("reviewer-*.md")}
    assert named <= agents, f"{profile}: no agent for {named - agents}"
    assert (root / ".claude" / "profiles" / f"{profile}.md").exists()
    assert (root / "checks" / "be" / "check.sh").exists()
    assert (root / ".claude" / "review-rules.md").exists() and (root / "REVIEW.md").exists()
    assert (root / ".claude" / "agents" / "verifier.md").exists()
    for p in (root / ".claude" / "agents").glob("reviewer-*.md"):
        text = p.read_text(encoding="utf-8")
        assert "review-rules.md" in text and "[Importante]" in text, p.name
    assert "agent_sets" not in cfg


def _transcript(cfg_dir, home, session, agent, job_dir, agent_type, times):
    d = cfg_dir / "projects" / __import__("re").sub(r"[^A-Za-z0-9]", "-", str(home)) / session / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "timestamp": times[0],
              "message": {"role": "user", "content": f"Job: x. Job folder: {job_dir}/. Giro 1."}}]
    lines += [{"type": "assistant", "timestamp": t, "message": {"id": t, "role": "assistant", "content": []}}
              for t in times[1:]]
    (d / f"agent-{agent}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    (d / f"agent-{agent}.meta.json").write_text(json.dumps({"agentType": agent_type, "description": f"{agent_type} T1"}))


def test_hours_per_ticket(factory, tmp_path, monkeypatch):
    from software_factory import hours
    cfg_dir = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    _transcript(cfg_dir, factory.home.root, "s1", "a1", factory.dir(job["id"]), "triage",
                ["2026-11-18T09:00:00Z", "2026-11-18T09:10:00Z"])
    job = factory.advance(factory.locked(job["id"]), "building")
    assert [w["what"] for w in job["work"]] == ["triage"]
    sub = factory.load(job["subs"][0])
    # two reviewers in parallel count once in the hours of work; a resumed agent is two intervals
    _transcript(cfg_dir, factory.home.root, "s1", "b1", factory.dir(sub["id"]), "builder",
                ["2026-11-18T10:00:00Z", "2026-11-18T10:30:00Z", "2026-11-18T12:00:00Z", "2026-11-18T12:15:00Z"])
    for a in ("r1", "r2"):
        _transcript(cfg_dir, factory.home.root, "s1", a, factory.dir(sub["id"]), "reviewer-code",
                    ["2026-11-18T11:00:00Z", "2026-11-18T11:20:00Z"])
    sub["work"] = [{"key": "gate:x", "what": "gate", "note": "giro 1: PASS",
                    "start": "2026-11-18T10:30:00Z", "end": "2026-11-18T10:45:00Z"}]
    factory.save(sub)
    out = hours.report(factory, ["100"])
    assert out[0].startswith("T100 ")
    assert sum(1 for line in out if "builder" in line) == 2
    # 10 triage + 30 builder + 15 gate + 20 reviewers + 15 builder = 1:30; agent hours add the second reviewer
    assert "  ore di lavoro 1:30 (ore agente 1:50)" in out
    assert out[-1] == "totale 1:30 ore di lavoro su 1 ticket (ore agente 1:50)"
    monkeypatch.setattr(factory.home, "get", lambda k, d=None: {"timezone": "UTC"}.get(k, d))
    ts = hours.timesheet(factory, [])
    assert ts[0] == "2026-11-18 mer"
    assert ts[1].split()[:4] == ["T100", "1:30", "1.50", "h"]
    assert ts[2].strip() == "analisi, sviluppo, controlli, review"
    assert ts[-1].split() == ["totale", "1:30", "1.50", "h"]
    assert hours.round_up(1, 15) == 15 and hours.round_up(16 * 60, 15) == 30 and hours.round_up(0, 15) == 0
    since = hours.parse("2026-11-18T11:30:00Z")
    assert hours.report(factory, [], since, totals_only=True)[1] == "  ore di lavoro 0:15 (ore agente 0:15)"


def test_gate_run_is_logged(factory, monkeypatch):
    monkeypatch.setattr(jobs_mod, "run_gate", lambda *a, **k: "PASS")
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    sub = factory.load(job["subs"][0])
    sub["stage"], sub["round"] = "gate", 1
    factory.save(sub)
    factory.gate(sub["id"])
    work = factory.load(sub["id"])["work"]
    assert [(w["what"], w["note"]) for w in work] == [("gate", "giro 1: PASS")]


def test_spec_writer_asks_the_pm(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    o = factory.odoo
    sub = factory.advance(factory.locked(job["subs"][0]), "spec")
    d = factory.dir(sub["id"])
    with pytest.raises(Refused, match="Domande per il PM"):
        factory.advance(sub, "question")
    (d / "questions.md").write_text("## Perché\nIl caso d'uso non lo dice.\n\n## Domande per il PM\n1. Campo o testo?\n",
                                    encoding="utf-8")
    sub = factory.advance(sub, "question")
    assert sub["stage"] == "question"
    assert o.notes[-1][0] == 100 and o.notes[-1][1] == "domande per il PM" and "Campo o testo?" in o.notes[-1][2]
    assert o.tasks_db[100]["stage_id"][0] == STAGES["todo"]
    assert o.activities[-1][:2] == (100, "pm")
    parent = factory.load(job["id"])
    assert parent["last_note"] > 0
    with pytest.raises(Refused, match="no answer from the PM"):
        factory.advance(sub, "spec")
    o.msgs.append({"task": 100, "id": parent["last_note"] + 1})
    sub = factory.advance(sub, "spec")
    assert sub["stage"] == "spec" and (d / "questions-1.md").exists() and not (d / "questions.md").exists()
    assert o.tasks_db[100]["stage_id"][0] == STAGES["in_progress"]
    assert factory.load(job["id"])["last_note"] == parent["last_note"] + 1


def test_triage_unclear_asks_the_pm_with_an_activity(factory):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(
        "DECISION: UNCLEAR\nRISK: low\nSURFACES: none\n\n## Nota per il ticket\nServe sapere.\n\n## Domande\n1. Quale?\n")
    factory.advance(factory.locked(job["id"]), "unclear")
    assert factory.odoo.activities[-1][:2] == (100, "pm")
    assert "Rispondi con un commento" in factory.odoo.notes[-1][2]


DOCS_PLAN = PLAN.replace('"frontend": [', '"docs": [{"title": "Aggiorna AC-01", "brief": "Riscrivi AC-01.", "risk": "low"}],\n  "frontend": [')


def test_docs_subtask_runs_after_the_code(factory):
    assert '"docs"' in DOCS_PLAN
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(DOCS_PLAN, encoding="utf-8")
    with pytest.raises(Refused, match="no `docs` repository"):
        factory.advance(factory.locked(job["id"]), "building")
    factory.home.cfg["repos"]["docs"] = {"github": "o/docs", "base_branch": "main", "profile": "docs",
                                         "routing": {"low": {"docs": "sonnet"}}}
    job = factory.advance(factory.locked(job["id"]), "building")
    subs = [factory.load(s) for s in job["subs"]]
    assert [s["repo"] for s in subs][-1] == "docs" and "docs" in job["repos"]
    docs = factory.odoo.tasks_db[subs[-1]["task"]]
    assert docs["tag_ids"] == [] and "documentazione" in factory.odoo.notes[-1][2]


def test_odoo_rereads_a_renewed_key_on_401(tmp_path, monkeypatch):
    import io
    import urllib.error

    from software_factory import odoo as odoo_mod
    creds = tmp_path / "odoo.json"
    creds.write_text(json.dumps({"api_key": "old"}), encoding="utf-8")
    cfg = {"odoo": {"url": "https://x", "db": "d", "credentials_file": str(creds), "project_id": 1,
                    "stages": {}, "tags": {}, "users": {}}}
    seen = []

    def post(url, db, key, model, method, body):
        seen.append(key)
        if key != "new":
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b'{"message": "Invalid apikey"}'))
        return {"uid": 7}

    monkeypatch.setattr(odoo_mod, "_post", post)
    o = odoo_mod.Odoo(cfg, tmp_path / "ledger.json")
    with pytest.raises(odoo_mod.OdooError, match="sf odoo-key"):
        o.call("res.users", "context_get")
    creds.write_text(json.dumps({"api_key": "new"}), encoding="utf-8")
    assert o.call("res.users", "context_get") == {"uid": 7}
    assert seen == ["old", "old", "new"]
    assert odoo_mod.check_key(cfg, "new") == 7


def test_subtask_gets_the_pr_link_in_odoo(factory, monkeypatch):
    job = factory.create(100)
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    sub = factory.load(job["subs"][0])
    sub["stage"], sub["round"] = "build", 1
    factory.save(sub)
    (factory.dir(sub["id"]) / "build.md").write_text("STATUS: READY\n")

    def git(cwd, *args, check=True):
        if args[:1] == ("rev-list",):
            return FakeRes("1\n")
        if args[:1] == ("rev-parse",):
            return FakeRes("abc\n")
        return FakeRes()

    url = "https://github.com/o/be/pull/5"

    def gh_json(cwd, *args):
        if args[:2] == ("pr", "list"):
            return [{"number": 5, "url": url, "baseRefName": sub["base"]}]
        return {"state": "MERGED"}

    monkeypatch.setattr(jobs_mod, "git", git)
    monkeypatch.setattr(jobs_mod, "gh_json", gh_json)
    sub = factory.advance(factory.locked(sub["id"]), "gate")
    tid, title, text = factory.odoo.notes[-1]
    assert (tid, title) == (sub["task"], "pull request") and url in text
    sub["stage"] = "merging"
    factory.save(sub)
    factory.advance(factory.locked(sub["id"]), "merged")
    tid, title, text = factory.odoo.notes[-1]
    assert (tid, title) == (sub["task"], "unito") and url in text


def test_factory_tasks_are_assigned_to_the_team_leader(factory):
    job = factory.create(100)
    o = factory.odoo
    assert o.tasks_db[100]["user_ids"] == [26]
    (factory.dir(job["id"]) / "triage.md").write_text(PLAN)
    job = factory.advance(factory.locked(job["id"]), "building")
    assert all(o.tasks_db[factory.load(s)["task"]]["user_ids"] == [26] for s in job["subs"])
    assert o.tasks_db[job["fe_tasks"][0]]["user_ids"] == [8]  # the front-end of a person stays to that person
    o.tasks_db[101] = dict(o.tasks_db[100], id=101, user_ids=[8], stage_id=[1, "TO DO"])
    factory.create(101)
    assert o.tasks_db[101]["user_ids"] == [8]  # an assignee chosen by a person is kept


def test_e2e_runs_before_the_approver_and_informs_the_team_leader(factory, monkeypatch):
    job = _approved(factory, monkeypatch, "APPROVE\nRISK: low\nNEEDS-HUMAN: no\n\n## Summary\n- ok\n\n"
                    "## E2E\n- login.spec.ts: TEST_BUG, alta, il titolo è cambiato come chiede il task\n",
                    {"low": "auto", "medium": "auto", "high": "auto"})
    factory.save(job)
    with pytest.raises(Refused, match="e2e.mode is off"):
        factory.e2e(job["id"])
    factory.cfg["e2e"] = {"mode": "info"}
    (factory.home.root / "checks").mkdir()
    (factory.home.root / "checks" / "e2e.sh").write_text("exit 1\n")
    with pytest.raises(Refused, match="sf e2e"):
        factory.advance(factory.locked(job["id"]), "merging")

    def script(cmd, cwd, check=True, input=None, env=None):
        if cmd[:1] == ["bash"]:
            out = jobs_mod.Path(env["FACTORY_E2E_OUT"])
            assert env["FACTORY_WORKTREE_BE"] == str(factory.home.root) and "FACTORY_WORKTREE_FE" in env
            assert (out / "changed-be.txt").read_text() == "src/App.java\n"
            (out / "summary.md").write_text("PASSED: 23\nFAILED: 1\nFLAKY: 2\n\n- login.spec.ts fallito\n")
            return FakeRes("1 failed", code=1)
        return FakeRes()

    monkeypatch.setattr(jobs_mod, "run", script)
    verdict, report = factory.e2e(job["id"])
    text = report.read_text(encoding="utf-8")
    assert verdict == "FAIL" and text.startswith("E2E: FAIL\n") and "login.spec.ts fallito" in text
    job = factory.load(job["id"])
    assert job["e2e"]["failed"] == 1 and job["e2e_runs"][-1]["flaky"] == 2
    assert job["work"][-1]["what"] == "e2e"
    factory.cfg["e2e"] = {"mode": "blocking"}
    with pytest.raises(Refused, match="blocking"):
        factory.advance(factory.locked(job["id"]), "merging")
    factory.cfg["e2e"] = {"mode": "info"}
    job = factory.advance(factory.locked(job["id"]), "merging")
    tid, title, note = factory.odoo.notes[-1]
    assert title == "test end-to-end" and "TEST_BUG" in note and "23 passati" in note
    assert factory.odoo.activities[-1][1] == "team_leader" and job["e2e"]["posted"]
