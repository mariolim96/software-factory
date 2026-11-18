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
        self.tag_ids = {"queue": 9, "queue_fe": 4, "frontend": 7, "backend": 8, "waiting_fe": 6, "escalation": 5}
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
    monkeypatch.setattr(jobs_mod, "cost_usd", lambda home, d: 0.0)
    monkeypatch.setattr(jobs_mod.taskdoc, "render", lambda odoo, tid, new_after=0: f"# T{tid}\n")
    f = jobs_mod.Factory(Home(tmp_path))
    f._odoo = FakeOdoo()
    f.git_calls = git_calls
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
    sub = factory.locked(job["subs"][0])
    sub.update(stage="review", round=2, round_limit=2,
               agents={"reviewers": {"code": "haiku"}}, reviews={"code": "pending"})
    factory.round_dir(sub).mkdir(parents=True)
    (factory.round_dir(sub) / "review-code.md").write_text("VERDICT: CHANGES\n")
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
    reasons = factory._human_merge_reasons(job)
    assert any("parent_merge" in r for r in reasons) and any("alto rischio" in r for r in reasons)
    job["stage"] = "merged"
    factory.deliver(job)
    assert job["stage"] == "waiting-fe"
    assert factory.odoo.tag_ids["waiting_fe"] in factory.odoo.tasks_db[100]["tag_ids"]
    factory.odoo.set_stage(job["fe_tasks"][0], "done")
    factory.deliver(job)
    t = factory.odoo.tasks_db[100]
    assert job["stage"] == "test" and t["stage_id"][0] == STAGES["test"] and t["user_ids"] == [24]
    assert factory.odoo.tag_ids["waiting_fe"] not in t["tag_ids"]


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
    assert fe["base"] == "factory/t100" and factory.odoo.tasks_db[fe["task"]]["user_ids"] == []
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


@pytest.mark.parametrize("profile", ["java-maven", "angular", "react", "python", "node", "odoo"])
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
    assert "agent_sets" not in cfg
