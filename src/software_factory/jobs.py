"""The only writer of jobs/<id>/job.json.

Two kinds of job:

- a **parent** job for each Odoo task tagged for the factory. The triage either
  asks (UNCLEAR, the task stays in To Do) or plans it into sub-tasks. Back-end
  sub-tasks are always built by the factory. Front-end sub-tasks go to a person,
  or, when the task also carries the `queue_fe` tag, are built by the factory in
  the front-end repository after the back-end ones. Each repository the plan
  touches gets a parent branch. When every factory sub-task is merged into its
  parent branch, the approver judges the whole change, and the parent PRs merge
  into the integration branches (by the factory, or by the team leader). The
  task goes to Test, assigned to the PM with the approver's test note, once the
  front-end sub-tasks of a person are Done; until then it waits, tagged "In
  attesa front-end". The PM closes it, or moves it back to To Do with comments:
  a new cycle.
- a **sub** job for each sub-task the factory builds: spec → build → gate →
  review → merge into the parent branch of its repository. It never goes to Test.

Every stage change goes through `advance`, which checks the files that justify
it and refuses anything else. The Odoo and GitHub side effects of a transition
happen here too, so the orchestrator never improvises them.
"""

import json
import re
from pathlib import Path

from . import hours, taskdoc
from . import review as reviewing
from . import route as routing
from .cost import cost_usd
from .gate import run_gate
from .odoo import Odoo, html_to_text
from .util import (
    Refused,
    first_line,
    gh,
    gh_json,
    git,
    header,
    now,
    require,
    run,
    section,
)

try:
    import fcntl
except ImportError:  # Windows, for the unit tests only: the factory runs on Linux/WSL
    fcntl = None

PARENT = {
    "triage": {"unclear", "building"},
    "unclear": {"triage", "dropped"},
    "building": {"approve", "waiting-fe", "test"},
    "approve": {"merging", "ready", "needs-human"},
    "merging": {"merged", "needs-human"},
    "ready": {"merged", "triage", "dropped"},
    "needs-human": {"merged", "triage", "dropped"},
    "merged": {"waiting-fe", "test"},
    "waiting-fe": {"test"},
    "test": {"done", "triage"},
}
SUB = {
    "queued": {"spec"},
    "spec": {"build"},
    "build": {"gate", "needs-human"},
    "gate": {"review", "build", "needs-human"},
    "review": {"verify", "merging", "build", "needs-human"},
    "verify": {"merging", "build", "needs-human"},
    "merging": {"merged", "needs-human"},
    "needs-human": {"build", "merged"},
}
TERMINAL = {"done", "dropped", "merged"}
RUNNING = {"parent": {"triage", "approve", "merging"},
           "sub": {"spec", "build", "gate", "review", "verify", "merging"}}
REPO_ORDER = {"be": 0, "fe": 1}
RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def riskier(*levels):
    return max((lv for lv in levels if lv in RISK_RANK), key=RISK_RANK.get, default="high")


def merge_policy(cfg):
    """Which risk tiers the factory merges into the integration branch by itself.
    `merge_policy` wins; the older `parent_merge` reads as all-human or all-but-high."""
    if "merge_policy" in cfg:
        return cfg["merge_policy"]
    if cfg.get("parent_merge") == "auto":
        return {"low": "auto", "medium": "auto", "high": "human"}
    return {"low": "human", "medium": "human", "high": "human"}


def repo_order(keys):
    return sorted(keys, key=lambda k: (REPO_ORDER.get(k, 9), k))


class Factory:
    def __init__(self, home):
        self.home = home
        self.cfg = home.cfg
        self._odoo = None
        self._locks = {}

    @property
    def odoo(self):
        if self._odoo is None:
            self._odoo = Odoo(self.cfg, self.home.jobs / ".odoo-posted.json")
        return self._odoo

    # ---- files ---------------------------------------------------------------

    def dir(self, job_id):
        return self.home.jobs / job_id

    def load(self, job_id):
        """Read a job without locking it: for display and for reading neighbours."""
        path = self.dir(job_id) / "job.json"
        if not path.exists():
            raise Refused(f"no job {job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def locked(self, job_id):
        """Lock a job until this process exits, then read it fresh. Every change starts here.

        Non-blocking: a job another `sf` process holds (a merge running its gate)
        is refused instead of hanging the caller.
        """
        if fcntl and job_id not in self._locks:
            f = open(self.dir(job_id) / ".lock", "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                f.close()
                raise Refused(f"{job_id} is busy: another sf command is working on it") from None
            self._locks[job_id] = f
        return self.load(job_id)

    def save(self, job):
        path = self.dir(job["id"]) / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)

    def all(self):
        if not self.home.jobs.exists():
            return []
        return [self.load(p.name) for p in sorted(self.home.jobs.iterdir())
                if (p / "job.json").exists()]

    def resolve(self, arg):
        """A job id (t9563, t9563-s9601) or an Odoo task number (9563, T9563)."""
        m = re.fullmatch(r"[tT]?(\d+)", arg)
        if m:
            for j in self.all():
                if j["task"] == int(m.group(1)):
                    return j
            raise Refused(f"no job for task T{m.group(1)}")
        return self.load(arg)

    def round_dir(self, job, n=None):
        return self.dir(job["id"]) / f"round-{n or job['round']}"

    def parent_of(self, sub):
        return self.load(sub["parent"])

    def subs_of(self, parent):
        return [self.load(s) for s in parent["subs"]]

    def header(self, job):
        """The line every agent prompt starts with. Keep `Job folder: <path>/`: cost is found by it."""
        h = [f"Job: {job['id']}.", f"Job folder: {self.dir(job['id'])}/.", f"Home: {self.home.root}.",
             f"Task: T{job['task']}."]
        if job["kind"] == "sub":
            key = job["repo"]
            h += [f"Repo: {self.home.repo_path(key)} ({key}).", f"Profile: {self.home.profile_md(key)}.",
                  f"Worktree: {job['worktree']}.", f"Branch: {job['branch']}.", f"Base: origin/{job['base']}.",
                  f"Parent job folder: {self.dir(job['parent'])}/."]
        else:
            for key in repo_order(self.home.repo_keys()):
                h.append(f"Repo {key}: {self.home.repo_path(key)} (profilo {self.home.profile_md(key)}).")
            for key in repo_order(job["repos"]):
                r = job["repos"][key]
                if r.get("worktree"):
                    h.append(f"Worktree {key}: {r['worktree']}. Branch {key}: {r['branch']}. "
                             f"Base {key}: origin/{self.home.base(key)}.")
            fe = "la factory" if job.get("fe_mode") == "factory" else "una persona del team"
            h.append(f"Front-end: lo realizza {fe}.")
        refs = [str(p) for p in self.home.ref_paths()]
        if refs:
            h.append(f"References (sola lettura): {', '.join(refs)}.")
        return " ".join(h)

    def write_task_md(self, job):
        (self.dir(job["id"]) / "task.md").write_text(
            taskdoc.render(self.odoo, job["task"], new_after=job.get("last_note", 0)), encoding="utf-8")

    def latest_instruction(self, job):
        msgs = self.odoo.instructions(job["task"], after_id=job["last_note"])
        return msgs[-1]["id"] if msgs else None

    def note(self, job, text, title=None):
        job["last_note"] = self.odoo.post_note(job["task"], text, title)

    # ---- slots ---------------------------------------------------------------

    def running(self, jobs=None):
        return [j for j in (jobs if jobs is not None else self.all()) if j["stage"] in RUNNING[j["kind"]]]

    def require_slot(self, job_id=None):
        busy = [j["id"] for j in self.running() if j["id"] != job_id]
        n = self.cfg["concurrency"]
        require(len(busy) < n, f"concurrency {n} reached; running: {', '.join(busy)}")

    # ---- creating a parent job ------------------------------------------------

    def fe_mode(self, task):
        tag = self.odoo.tag_ids.get("queue_fe")
        return "factory" if tag and tag in task["tag_ids"] else "person"

    def create(self, task_id):
        existing = [j for j in self.all() if j["task"] == task_id]
        require(not existing, f"T{task_id} already has job {existing[0]['id']} ({existing[0]['stage']})"
                if existing else "")
        self.require_slot()
        t = self.odoo.task(task_id)
        require(not t["parent_id"], f"T{task_id} is a sub-task: the factory takes top-level tasks")
        require(self.odoo.stage_key(t["stage_id"]) == "todo", f"T{task_id} isn't in To Do")
        require(self.odoo.tag_ids["queue"] in t["tag_ids"], f"T{task_id} isn't tagged for the factory")
        job_id = f"t{task_id}"
        self.dir(job_id).mkdir(parents=True)
        job = {
            "id": job_id, "kind": "parent", "task": task_id, "title": t["name"],
            "task_url": self.odoo.task_url(task_id), "stage": "triage", "cycle": 1,
            "fe_mode": self.fe_mode(t), "repos": {}, "subs": [], "fe_tasks": [], "created": {},
            "risk": None, "surfaces": [], "last_note": 0, "cost_usd": 0.0,
            "started": now(), "ended": None, "log": [],
        }
        self.write_task_md(job)
        self.save(job)
        return job

    # ---- advance -------------------------------------------------------------

    def advance(self, job, stage):
        old = job["stage"]
        table = PARENT if job["kind"] == "parent" else SUB
        require(stage in table.get(old, set()), f"{job['id']}: {old} → {stage} is not a legal transition")
        getattr(self, f"_{job['kind']}_advance")(job, old, stage)
        job["stage"] = stage
        job["ended"] = now() if stage in TERMINAL else None
        job.setdefault("log", []).append(f"{now()} {old} → {stage}")
        job["cost_usd"] = cost_usd(self.home, self.dir(job["id"]))
        job["work"] = hours.merge_work(job.get("work"), hours.agent_work(self.home, self.dir(job["id"])))
        self.save(job)
        return job

    @staticmethod
    def _worked(job, what, note, start):
        """Record a run of the checks done by sf itself (no agent) for `sf log`."""
        end = now()
        job.setdefault("work", []).append({"key": f"{what}:{start}:{note}", "what": what, "note": note,
                                           "start": start, "end": end})

    # -- parent

    def _parent_advance(self, job, old, stage):
        d = self.dir(job["id"])
        if old == "triage":
            h = header(d / "triage.md") if (d / "triage.md").exists() else {}
            want = {"unclear": "UNCLEAR", "building": "PLAN"}[stage]
            require(h.get("DECISION") == want, f"triage.md says DECISION: {h.get('DECISION')}, not {want}")
            job["risk"] = h.get("RISK", "medium")
            job["surfaces"] = [s.strip() for s in h.get("SURFACES", "").split(",") if s.strip() not in {"", "none"}]
            if stage == "unclear":
                text = section(d / "triage.md", "Nota per il ticket") or ""
                questions = section(d / "triage.md", "Domande") or ""
                self.note(job, f"{text}\n\n{questions}".strip(), "servono chiarimenti")
            else:
                self._plan(job, d / "triage.md")

        elif old in {"unclear", "ready", "needs-human"} and stage == "triage":
            require(self.latest_instruction(job), "no new comment from the PM or the team leader since the last factory note")
            self.require_slot(job["id"])
            k = len(list(d.glob("triage-*.md"))) + 1
            if (d / "triage.md").exists():
                (d / "triage.md").rename(d / f"triage-{k}.md")
            if old != "unclear":
                self.odoo.set_tags(job["task"], remove=["escalation"])
            self.write_task_md(job)

        elif old == "test" and stage == "triage":
            t = self.odoo.task(job["task"])
            require(self.odoo.stage_key(t["stage_id"]) == "todo", "the PM hasn't moved the task back to To Do")
            self.require_slot(job["id"])
            self.write_task_md(job)  # before archiving: the PM's comments since the test note are NEW
            self._archive_cycle(job)
            job["fe_mode"] = self.fe_mode(t)

        elif old == "building":
            subs = self.subs_of(job)
            require(all(s["stage"] == "merged" for s in subs), "not every factory sub-task is merged")
            if stage == "approve":
                require(subs, "no factory sub-task: deliver it instead")
                self.require_slot(job["id"])
                for key in repo_order(job["repos"]):
                    self._parent_worktree(job, key)
                    self._open_parent_pr(job, key, [s for s in subs if s["repo"] == key])
                self.odoo.set_stage(job["task"], "review")
            else:
                require(not subs, "the factory sub-tasks must go through the approver first")
                self._deliver(job, stage)

        elif old == "approve":
            line = first_line(d / "decision.md")
            if stage == "needs-human":
                require(line == "ESCALATE", f"decision.md starts with {line!r}, not ESCALATE")
                self._escalate_parent(job)
            else:
                require(line == "APPROVE", f"decision.md starts with {line!r}, not APPROVE")
                tier, reasons = self._human_merge_reasons(job)
                require((stage == "ready") == bool(reasons),
                        f"go to ready: {'; '.join(reasons)}" if reasons else "nothing requires a human merge: go to merging")
                job["merge"] = {"tier": tier, "mode": "human" if reasons else "auto", "reasons": reasons}
                self._describe_parent_prs(job, tier, reasons)
                self._post_preexisting(job)
                if stage == "ready":
                    self._ready_parent(job, reasons)

        elif stage == "merged":
            open_prs = [k for k, r in job["repos"].items() if r.get("pr") and self._pr_state(k, r["pr"]) != "MERGED"]
            require(job["repos"] and not open_prs, f"PRs not merged yet: {', '.join(open_prs) or 'none opened'}")
            self.odoo.set_tags(job["task"], remove=["escalation"])
            self._cleanup(job)

        elif old == "merging" and stage == "needs-human":
            self._escalate_parent(job)

        elif old in {"merged", "waiting-fe"}:
            self._deliver(job, stage)

        elif old == "test" and stage == "done":
            require(self.odoo.stage_key(self.odoo.task(job["task"])["stage_id"]) == "done", "the task isn't Done in Odoo")

        elif stage == "dropped":
            self._cleanup(job)

    def branch_name(self, job):
        return f"factory/{job['id']}" + (f"-c{job['cycle']}" if job["cycle"] > 1 else "")

    def _ensure_parent_branch(self, job, key):
        if key in job["repos"]:
            return
        require(self.home.has_repo(key), f"factory.json has no repository {key!r}")
        repo, base, branch = self.home.repo_path(key), self.home.base(key), self.branch_name(job)
        git(repo, "fetch", "origin", base)
        if git(repo, "ls-remote", "--exit-code", "origin", branch, check=False).returncode != 0:
            git(repo, "push", "origin", f"origin/{base}:refs/heads/{branch}")
        job["repos"][key] = {"branch": branch, "pr": None, "pr_url": None, "worktree": None}
        self.save(job)

    def _plan(self, job, triage):
        plan = read_plan(triage, self.cfg)
        o = self.odoo
        factory_fe = job.get("fe_mode") == "factory"
        require(not (factory_fe and plan["frontend"]) or self.home.has_repo("fe"),
                "the task asks the factory for the front-end, but factory.json has no `fe` repository")
        contract = (plan.get("contract") or "").strip()
        if contract:
            (self.dir(job["id"]) / "contract.md").write_text(contract + "\n", encoding="utf-8")
        if plan["backend"]:
            self._ensure_parent_branch(job, "be")
        if plan["frontend"] and factory_fe:
            self._ensure_parent_branch(job, "fe")
        prefix = f"c{job['cycle']}-{len(list(self.dir(job['id']).glob('triage-*.md')))}"
        lines = []
        for i, item in enumerate(plan["backend"]):
            sid = self._subtask(job, f"{prefix}-be{i}", item["title"],
                                f"{item['brief']}\n\nSottotask back-end di T{job['task']}: lo realizza la factory.",
                                ["backend"], [])
            self._add_sub(job, sid, item, "be")
            lines.append(f"- T{sid} (back-end, factory): {item['title']}")
        for i, item in enumerate(plan["frontend"]):
            text = f"{item['brief']}\n\n## Contratto API\n\n{item.get('contract') or contract or 'Nessuna API nuova.'}"
            if item.get("contract") and contract:
                text += f"\n\n## Contratto API complessivo del task\n\n{contract}"
            if factory_fe:
                sid = self._subtask(job, f"{prefix}-fe{i}", item["title"],
                                    text + f"\n\nSottotask front-end di T{job['task']}: lo realizza la factory.",
                                    ["frontend"], [])
                self._add_sub(job, sid, item, "fe")
                lines.append(f"- T{sid} (front-end, factory): {item['title']}")
            else:
                sid = self._subtask(job, f"{prefix}-fe{i}", item["title"],
                                    text + f"\n\nIl back-end lo realizza la factory nei sottotask back-end di T{job['task']}.",
                                    ["frontend"], [o.users["frontend"]])
                if sid not in job["fe_tasks"]:
                    job["fe_tasks"].append(sid)
                    self.save(job)
                lines.append(f"- T{sid} (front-end): {item['title']}")
        summary = section(triage, "Nota per il ticket") or ""
        self.note(job, f"{summary}\n\nSottotask creati:\n\n" + "\n".join(lines), "piano")
        if o.stage_key(o.task(job["task"])["stage_id"]) == "todo":
            o.set_stage(job["task"], "in_progress")

    def _subtask(self, job, key, title, text, tags, users):
        """Create an Odoo sub-task once: a retried advance finds it in job["created"]."""
        if key not in job["created"]:
            job["created"][key] = self.odoo.create_subtask(job["task"], title, text, tags, users)
            self.save(job)
        return job["created"][key]

    def _add_sub(self, parent, sid, item, key):
        sub_id = f"{parent['id']}-s{sid}"
        if sub_id in parent["subs"]:
            return
        self.dir(sub_id).mkdir(parents=True, exist_ok=True)
        self.save({
            "id": sub_id, "kind": "sub", "task": sid, "parent": parent["id"], "repo": key, "title": item["title"],
            "task_url": self.odoo.task_url(sid), "stage": "queued", "round": 0,
            "round_limit": self.cfg["max_rounds"], "branch": f"factory/{sub_id}",
            "base": parent["repos"][key]["branch"], "worktree": str(self.home.worktrees / sub_id),
            "pr": None, "pr_url": None, "risk": item.get("risk", "medium"), "surfaces": item.get("surfaces", []),
            "agents": None, "gate": None, "reviews": {}, "last_note": 0, "cost_usd": 0.0,
            "started": now(), "ended": None, "log": [],
        })
        # back-end before front-end: the front-end builds on the APIs
        parent["subs"].append(sub_id)
        parent["subs"].sort(key=lambda s: REPO_ORDER.get(self.load(s)["repo"], 9))
        self.save(parent)

    def _archive_cycle(self, job):
        d = self.dir(job["id"])
        archive = d / f"cycle-{job['cycle']}"
        archive.mkdir(exist_ok=True)
        for p in list(d.iterdir()):
            if p.name in {"job.json", ".lock", "task.md"} or p.name.startswith(("job.", "cycle-")):
                continue
            p.rename(archive / p.name)
        job.setdefault("past", []).append({k: job[k] for k in ("cycle", "repos", "subs", "fe_tasks")})
        job["cycle"] += 1
        job.update(repos={}, subs=[], fe_tasks=[])

    def _parent_worktree(self, job, key):
        r = job["repos"][key]
        repo = self.home.repo_path(key)
        wt = self.home.worktrees / f"{job['id']}-{key}"
        git(repo, "fetch", "origin", r["branch"], self.home.base(key))
        if not wt.exists():
            git(repo, "worktree", "add", "-B", r["branch"], str(wt), f"origin/{r['branch']}")
        else:
            git(wt, "reset", "--hard", f"origin/{r['branch']}")
        r["worktree"] = str(wt)

    def _open_parent_pr(self, job, key, subs):
        r = job["repos"][key]
        if r["pr"]:
            return
        repo = self.home.repo_path(key)
        body = [f"Task Odoo: {job['task_url']}", "", "Sottotask:"]
        body += [f"- T{s['task']} {s['title']} (#{s['pr']})" for s in subs]
        res = gh_json(repo, "pr", "list", "--head", r["branch"], "--state", "open", "--json", "number,url")
        if not res:
            gh(repo, "pr", "create", "--draft", "--base", self.home.base(key), "--head", r["branch"],
               "--title", f"T{job['task']} · {job['title']}", "--body", "\n".join(body))
            res = gh_json(repo, "pr", "list", "--head", r["branch"], "--state", "open", "--json", "number,url")
        r["pr"], r["pr_url"] = res[0]["number"], res[0]["url"]

    def changed_files(self, worktree, base_ref):
        return git(worktree, "diff", "--name-only", f"{base_ref}...HEAD").stdout.split()

    def _human_merge_reasons(self, job):
        """(tier, reasons a person must merge). The tier is the highest of the triage's, the
        sub-tasks', and the approver's; a path in high_risk_paths always wants a person."""
        d = self.dir(job["id"]) / "decision.md"
        h = header(d) if d.exists() else {}
        approver = h.get("RISK", "").lower()
        subs_risk = [self.load(s)["risk"] for s in job["subs"]]
        tier = riskier(job.get("risk") or "medium", *subs_risk, approver or "high")
        reasons = []
        if not approver:
            reasons.append("l'approver non ha indicato il rischio (RISK) in decision.md")
        if merge_policy(self.cfg).get(tier, "human") != "auto":
            reasons.append(f"rischio {tier}: merge_policy riserva questo livello al team leader")
        needs = h.get("NEEDS-HUMAN", "no")
        if needs.lower() not in {"", "no", "none", "nessuno"}:
            reasons.append(f"l'approver chiede una persona: {needs}")
        for key in repo_order(job["repos"]):
            r = job["repos"][key]
            files = self.changed_files(r["worktree"], f"origin/{self.home.base(key)}")
            hot = routing.high_risk_files(self.home.rconf(key), files)
            if hot:
                reasons.append(f"tocca percorsi che vogliono sempre una persona in {key}: " + ", ".join(hot[:8])
                               + (" e altri" if len(hot) > 8 else ""))
        return tier, reasons

    def _describe_parent_prs(self, job, tier, reasons):
        """The parent PR body: what it does, its risk, how it was tested, how to revert it."""
        d = self.dir(job["id"]) / "decision.md"

        def sec(name):
            return (section(d, name) or "") if d.exists() else ""

        merge = ("automatico, dopo gate e review della factory" if not reasons
                 else "team leader, perché " + "; ".join(reasons))
        subs = [self.load(s) for s in job["subs"]]
        for key in repo_order(job["repos"]):
            r = job["repos"][key]
            if not r.get("pr"):
                continue
            base = self.home.base(key)
            body = "\n".join([
                f"Task Odoo: {job['task_url']}", "",
                "## Sottotask", *[f"- T{s['task']} {s['title']} (#{s['pr']})" for s in subs if s["repo"] == key], "",
                "## Summary", sec("Summary"), "",
                "## Rischio", f"Livello **{tier}**. Merge: {merge}.", "", sec("Rischio"), "",
                "## Test", (f"Il gate della factory è passato su ogni sottotask e viene ripetuto dopo "
                            f"l'allineamento con `{base}`, prima del merge."), "", sec("Criteri di accettazione"), "",
                "## Come annullarla", sec("Come annullarla") or f"Revert del merge commit di questa PR su `{base}`.",
            ])
            res = run(["gh", "api", "-X", "PATCH", f"repos/{self.home.repos()[key]['github']}/pulls/{r['pr']}",
                       "--input", "-"], cwd=self.home.repo_path(key), check=False, input=json.dumps({"body": body}))
            if res.returncode != 0:
                job.setdefault("log", []).append(f"{now()} descrizione della PR {key}#{r['pr']} non aggiornata")

    def _post_preexisting(self, job):
        """Problems the reviewers found that predate the branch: proposed to the team leader as new tasks."""
        d = self.dir(job["id"]) / "decision.md"
        text = ((section(d, "Problemi preesistenti") or "") if d.exists() else "").strip()
        if not text or text.lower().startswith("nessuno") or job.get("preexisting_posted") == job["cycle"]:
            return
        note = ("I revisori hanno trovato problemi che esistevano già prima di questo task. Non bloccano il "
                f"merge; l'approver li propone come task nuovi:\n\n{text}")
        self.note(job, note, "problemi preesistenti")
        self.odoo.activity(job["task"], "team_leader", f"Factory: problemi preesistenti trovati su T{job['task']}", note)
        job["preexisting_posted"] = job["cycle"]

    def pr_links(self, job):
        return "\n".join(f"- {key}: {r['pr_url']}" for key, r in sorted(job["repos"].items()) if r.get("pr_url")) or "-"

    def _pr_ready(self, key, pr):
        repo = self.home.repo_path(key)
        if gh_json(repo, "pr", "view", str(pr), "--json", "isDraft")["isDraft"]:
            gh(repo, "pr", "ready", str(pr))

    def _ready_parent(self, job, reasons):
        for key, r in job["repos"].items():
            self._pr_ready(key, r["pr"])
        d = self.dir(job["id"])
        why = "\n".join(f"- {r}" for r in reasons)
        summary = section(d / "decision.md", "Summary") or ""
        text = (f"L'approver ha approvato il lavoro. Le PR sono pronte per il merge:\n\n{self.pr_links(job)}\n\n"
                f"Serve il team leader perché:\n\n{why}\n\n{summary}\n\n"
                "Per approvare, fai il merge delle PR su GitHub (prima il back-end). Per chiedere modifiche, "
                "scrivi qui un commento: la factory lo legge come istruzione e crea i sottotask di correzione.")
        self.note(job, text, "pronta per il merge")
        self.odoo.activity(job["task"], "team_leader", f"Merge delle PR di T{job['task']}", text)

    def _escalate_parent(self, job):
        d = self.dir(job["id"])
        for key, r in job["repos"].items():
            if r.get("pr"):
                self._pr_ready(key, r["pr"])
        ask = section(d / "decision.md", "Per il team leader") or section(d / "decision.md", "Perché") or ""
        text = (f"{ask}\n\nPR:\n\n{self.pr_links(job)}\n\nRispondi con un commento qui: la factory lo legge come "
                "istruzione. Oppure fai tu il merge delle PR.")
        self.note(job, text, "serve il team leader")
        self.odoo.set_tags(job["task"], add=["escalation"])
        self.odoo.activity(job["task"], "team_leader", f"Factory: T{job['task']} chiede una decisione", text)

    def _deliver(self, job, stage):
        o = self.odoo
        fe = o.tasks([["id", "in", job["fe_tasks"]]], fields=["stage_id"]) if job["fe_tasks"] else []
        pending = [t["id"] for t in fe if o.stage_key(t["stage_id"]) != "done"]
        require((stage == "waiting-fe") == bool(pending),
                f"front-end sub-tasks still open: {', '.join(f'T{i}' for i in pending)}" if pending
                else "every front-end sub-task is Done: go to test")
        if stage == "waiting-fe":
            if job["stage"] != "waiting-fe":
                o.set_tags(job["task"], add=["waiting_fe"])
                self.note(job, "Il back-end è completo"
                          + (f" e unito:\n\n{self.pr_links(job)}\n\n" if job["repos"] else ". ")
                          + "Il task passa in Test quando sono Done i sottotask front-end: "
                          + ", ".join(f"T{i}" for i in pending) + ".", "in attesa del front-end")
            return
        test_note = section(self.dir(job["id"]) / "decision.md", "Nota per il test") \
            if (self.dir(job["id"]) / "decision.md").exists() else None
        text = test_note or "Il lavoro è completo. Verifica il comportamento descritto nel task."
        if job["repos"]:
            text += f"\n\nPR:\n\n{self.pr_links(job)}"
        if job["fe_tasks"]:
            text += "\n\nSottotask front-end: " + ", ".join(f"T{i}" for i in job["fe_tasks"]) + "."
        text += ("\n\nSe va bene, porta il task in Done. Se va corretto, riportalo in To Do con un commento "
                 "per ogni correzione: la factory crea i sottotask di correzione.")
        o.set_tags(job["task"], remove=["waiting_fe"])
        self.note(job, text, "pronto per il test")
        o.set_stage(job["task"], "test")
        o.assign(job["task"], ["pm"])

    def _cleanup(self, job):
        if job["kind"] == "sub":
            targets = [(job["repo"], job.get("worktree"), job["branch"])]
        else:
            targets = [(k, r.get("worktree"), r["branch"]) for k, r in job["repos"].items()]
        for key, wt, branch in targets:
            repo = self.home.repo_path(key)
            if wt and Path(wt).exists():
                git(repo, "worktree", "remove", "--force", wt, check=False)
            git(repo, "branch", "-D", branch, check=False)

    def _pr_state(self, key, pr):
        return gh_json(self.home.repo_path(key), "pr", "view", str(pr), "--json", "state")["state"]

    def parent_prs_merged(self, job):
        prs = [(k, r["pr"]) for k, r in job["repos"].items() if r.get("pr")]
        return bool(prs) and all(self._pr_state(k, pr) == "MERGED" for k, pr in prs)

    # -- sub

    def _sub_advance(self, job, old, stage):
        d = self.dir(job["id"])
        repo = self.home.repo_path(job["repo"])
        if old == "queued":
            parent = self.parent_of(job)
            order = parent["subs"]
            before = [self.load(s) for s in order[:order.index(job["id"])]]
            require(all(s["stage"] == "merged" for s in before),
                    "an earlier sub-task of the same parent isn't merged yet")
            self.require_slot(job["id"])
            git(repo, "fetch", "origin", job["base"])
            wt = Path(job["worktree"])
            if not wt.exists():
                exists = git(repo, "rev-parse", "--verify", "--quiet", job["branch"], check=False).returncode == 0
                if exists:
                    git(repo, "worktree", "add", str(wt), job["branch"])
                else:
                    git(repo, "worktree", "add", "-b", job["branch"], str(wt), f"origin/{job['base']}")
            self.odoo.set_stage(job["task"], "in_progress")
            self.write_task_md(job)

        elif old == "spec":
            require((d / "spec.md").exists(), "spec.md doesn't exist")
            self._next_round(job)

        elif old == "build" and stage == "gate":
            status = first_line(d / "build.md")
            require(status == "STATUS: READY", f"build.md starts with {status!r}, not STATUS: READY")
            wt = job["worktree"]
            require(not git(wt, "status", "--porcelain").stdout.strip(), "the worktree has uncommitted changes")
            require(int(git(wt, "rev-list", "--count", f"origin/{job['base']}..HEAD").stdout) > 0,
                    f"{job['branch']} has no commits on top of origin/{job['base']}")
            git(wt, "fetch", "origin", job["branch"], check=False)
            head = git(wt, "rev-parse", "HEAD").stdout.strip()
            res = git(wt, "rev-parse", f"origin/{job['branch']}", check=False)
            remote = res.stdout.strip() if res.returncode == 0 else ""
            require(head == remote, f"{job['branch']} isn't pushed: local {head[:7]}, origin {remote[:7] or 'missing'}")
            if not job["pr"]:
                prs = gh_json(repo, "pr", "list", "--head", job["branch"], "--state", "open",
                              "--json", "number,url,baseRefName")
                require(len(prs) == 1, f"expected one open PR from {job['branch']}, found {len(prs)}")
                require(prs[0]["baseRefName"] == job["base"], f"the PR targets {prs[0]['baseRefName']}, not {job['base']}")
                job["pr"], job["pr_url"] = prs[0]["number"], prs[0]["url"]

        elif old == "build":  # → needs-human
            status = first_line(d / "build.md")
            require(status == "STATUS: BLOCKED", f"build.md starts with {status!r}, not STATUS: BLOCKED")
            self._escalate_sub(job, "Il builder è bloccato:\n\n" + (section(d / "build.md", "Blocked") or "(nessun dettaglio)"))

        elif old == "gate":
            checks = self.round_dir(job) / "checks.md"
            require(checks.exists(), f"run the gate first: sf gate {job['id']}")
            h = header(checks)
            require(h.get("COMMIT") == git(job["worktree"], "rev-parse", "HEAD").stdout.strip(),
                    "checks.md was written for another commit; run the gate again")
            job["gate"] = h.get("GATE")
            if stage == "review":
                require(h.get("GATE") == "PASS", "the gate didn't pass")
                self._route(job)
                job["reviews"] = {r: "pending" for r in job["agents"]["reviewers"]}
                self.odoo.set_stage(job["task"], "review")
            else:
                require(h.get("GATE") == "FAIL", "the gate passed")
                self._fail_round(job, stage, f"Il gate è fallito nel giro {job['round']}, l'ultimo consentito. "
                                             f"Vedi round-{job['round']}/checks.md.")

        elif old in {"review", "verify"}:
            findings = self._round_findings(job)
            todo = [f for f in findings if f["severity"] == "Importante" and f.get("outcome") != "respinto"]
            vcfg = reviewing.verify_cfg(self.cfg)
            if stage == "verify":
                require(vcfg["votes"], "verification is off (verify.votes is 0)")
                require(todo, "no Importante finding to verify: go to merging")
                job["agents"]["verify"] = {"votes": vcfg["votes"], "model": vcfg["model"]}
                return
            require(old == "verify" or not todo or not vcfg["votes"],
                    "Importante findings must be verified first: go to verify")
            if stage == "merging":
                require(not todo, "findings to fix: " + ", ".join(f["id"] for f in todo))
            else:
                require(todo, "no Importante finding left: go to merging")
            self._close_round(job, findings)
            if stage != "merging":
                self._fail_round(job, stage, f"Giri di review esauriti: restano i findings in "
                                             f"round-{job['round']}/findings.md. Dopo {job['round']} giri "
                                             "probabilmente serve un approccio diverso, non un'altra correzione.")
                if stage == "build":
                    self.odoo.set_stage(job["task"], "in_progress")

        elif old == "needs-human" and stage == "build":
            msgs = self.odoo.instructions(job["task"], after_id=job["last_note"])
            require(msgs, "no new comment from the team leader since the escalation")
            k = len(list(d.glob("feedback-*.md"))) + 1
            body = "\n\n".join(f"### {m['author_id'][1]}, {m['date'][:16]}\n\n{html_to_text(m['body'])}" for m in msgs)
            (d / f"feedback-{k}.md").write_text(
                f"# Feedback {k}\n\nCommenti del team leader o del PM su T{job['task']} dopo l'escalation. "
                f"Sono istruzioni.\n\n{body}\n", encoding="utf-8")
            job["round_limit"] = job["round"] + self.cfg["max_rounds"]
            self.odoo.set_tags(job["task"], remove=["escalation"])
            self.odoo.set_stage(job["task"], "in_progress")
            self.write_task_md(job)
            self._next_round(job)

        elif stage == "merged":
            require(job["pr"] and self._pr_state(job["repo"], job["pr"]) == "MERGED", "the PR isn't merged")
            self.odoo.set_tags(job["task"], remove=["escalation"])
            self.odoo.set_stage(job["task"], "done")
            self._cleanup(job)

        elif old == "merging" and stage == "needs-human":
            self._escalate_sub(job, None)  # merge() wrote decision.md

    def _round_findings(self, job):
        """Findings of the current round, with the verifiers' votes applied when in verify."""
        self.sync_reviews(job)
        missing = [r for r, v in job["reviews"].items() if v == "pending"]
        require(not missing, f"round {job['round']} is missing reviews from: {', '.join(missing)}")
        bad = [r for r, v in job["reviews"].items() if v == "invalid"]
        require(not bad, f"review-{bad[0]}.md is invalid: the first line must be a VERDICT, CHANGES if and only if "
                         "a finding is [Importante]; run that reviewer again" if bad else "")
        rd = self.round_dir(job)
        findings = reviewing.round_findings(rd, job["agents"]["reviewers"])
        vcfg = reviewing.verify_cfg(self.cfg)
        if job["stage"] == "verify":
            gone = [p.name for p in reviewing.verify_files(rd, vcfg["votes"]) if not p.exists()]
            require(not gone, f"waiting for {', '.join(gone)}")
        if job["stage"] == "verify" or not vcfg["votes"]:
            reviewing.apply_votes(findings, rd, vcfg)
        return findings

    def review_next(self, job):
        """What the orchestrator does after a reviewer or a verifier returns."""
        if job["stage"] not in {"review", "verify"}:
            return f"none: {job['id']} is in {job['stage']}"
        try:
            findings = self._round_findings(job)
        except Refused as e:
            return f"wait: {e}" if "missing" in str(e) or "waiting" in str(e) else f"invalid: {e}"
        todo = [f for f in findings if f["severity"] == "Importante" and f.get("outcome") != "respinto"]
        if not todo:
            return "merge"
        if job["stage"] == "review" and reviewing.verify_cfg(self.cfg)["votes"]:
            return "verify"
        return "build" if job["round"] < job["round_limit"] else "needs-human"

    def _close_round(self, job, findings):
        """findings.md for the builder, the round's numbers, and a comment on the PR."""
        n = job["round"]
        text = reviewing.render(findings, n)
        (self.round_dir(job) / "findings.md").write_text(text, encoding="utf-8")
        job.setdefault("rounds_log", []).append({"round": n, "gate": job.get("gate"), **reviewing.stats(findings)})
        if job.get("pr"):
            verdicts = ", ".join(f"{r} {v}" for r, v in sorted(job["reviews"].items()))
            body = f"**Review della factory, giro {n}** ({verdicts})\n\n" + text.split("\n", 2)[2]
            run(["gh", "pr", "comment", str(job["pr"]), "--body-file", "-"],
                cwd=self.home.repo_path(job["repo"]), check=False, input=body)

    def _next_round(self, job):
        job["round"] += 1
        self.round_dir(job).mkdir(parents=True, exist_ok=True)
        job["gate"] = None
        job["reviews"] = {}

    def _fail_round(self, job, stage, why):
        last = job["round"] >= job["round_limit"]
        require(last == (stage == "needs-human"),
                f"round {job['round']} of {job['round_limit']}: go to {'needs-human' if last else 'build'}")
        if last:
            self._escalate_sub(job, why)
        else:
            self._next_round(job)

    def _escalate_sub(self, job, why):
        d = self.dir(job["id"])
        if why is not None:
            (d / "decision.md").write_text(f"ESCALATE\n\n## Per il team leader\n{why}\n", encoding="utf-8")
        ask = section(d / "decision.md", "Per il team leader") or ""
        text = (f"{ask}\n\nPR: {job['pr_url'] or '-'} (verso il branch del padre)\n\n"
                "Rispondi con un commento qui: la factory lo passa al builder come istruzione per un nuovo giro.")
        self.note(job, text, "serve il team leader")
        self.odoo.set_tags(job["task"], add=["escalation"])
        self.odoo.activity(job["task"], "team_leader", f"Factory: T{job['task']} chiede una decisione", text)

    def _route(self, job):
        base_ref = f"origin/{job['base']}"
        files = self.changed_files(job["worktree"], base_ref)
        diff = git(job["worktree"], "diff", f"{base_ref}...HEAD").stdout
        reviewers = routing.route(self.home.rconf(job["repo"]), job["risk"], job["surfaces"], files, diff)
        missing = [r for r in reviewers if not (self.home.root / ".claude" / "agents" / f"reviewer-{r}.md").exists()]
        require(not missing, f"no agent file for reviewer(s): {', '.join(missing)}")
        job["agents"] = {"reviewers": reviewers}

    def sync_reviews(self, job):
        rd = self.round_dir(job)
        for r in (job.get("agents") or {}).get("reviewers", {}):
            line = first_line(rd / f"review-{r}.md")
            m = re.fullmatch(r"VERDICT:\s*(PASS|CHANGES)", line or "")
            verdict = m.group(1) if m else ("pending" if line is None else "invalid")
            if m:  # the verdict must agree with the findings: CHANGES iff an Importante one
                important = any(f["severity"] == "Importante"
                                for f in reviewing.parse_findings(rd / f"review-{r}.md", r))
                if important != (verdict == "CHANGES"):
                    verdict = "invalid"
            job["reviews"][r] = verdict
        return job

    # ---- gate and merge -----------------------------------------------------

    def gate(self, job_id):
        """Run the gate without holding the job: it takes minutes and `sf queue` must stay usable."""
        job = self.load(job_id)
        require(job["kind"] == "sub" and job["stage"] == "gate", f"{job_id} isn't a sub-task in gate")
        start = now()
        verdict = run_gate(self.home, job["repo"], job["id"], job["worktree"], job["base"], self.round_dir(job), "checks")
        job = self.locked(job_id)
        job["gate"] = verdict
        self._worked(job, "gate", f"giro {job['round']}: {verdict}", start)
        self.save(job)
        return verdict

    def _merge_one(self, job, key, wt, pr, branch, base, out):
        """Sync a branch with its base, gate it again, merge its PR. Returns None or why it failed."""
        repo = self.home.repo_path(key)
        if self._pr_state(key, pr) == "MERGED":
            return None
        git(wt, "fetch", "origin", base)
        merged = git(wt, "merge", "--no-edit", f"origin/{base}", check=False)
        if merged.returncode != 0:
            git(wt, "merge", "--abort", check=False)
            return f"Il branch di {key} è in conflitto con {base}:\n\n```\n{merged.stdout.strip()}\n```"
        name = f"merge-checks-{key}" if out.name == "merge" else "merge-checks"
        start = now()
        verdict = run_gate(self.home, key, f"{self.dir(job['id']).name}-{key}", wt, base, out, name,
                           f"allineato con {base} prima del merge")
        self._worked(job, "gate", f"prima del merge {key}: {verdict}", start)
        if verdict != "PASS":
            return f"Il gate di {key} è fallito dopo l'allineamento con {base}: vedi {out}/{name}.md."
        try:
            git(wt, "push", "origin", f"HEAD:{branch}")
            self._pr_ready(key, pr)
            gh(repo, "pr", "merge", str(pr), f"--{self.cfg.get('merge_method', 'merge')}")
        except Refused as e:
            return f"Il merge di {key} è fallito:\n\n```\n{e}\n```"
        git(repo, "push", "origin", "--delete", branch, check=False)
        return None

    def merge(self, job):
        """Sync with the base, gate again, merge the PR(s) through GitHub."""
        if job["stage"] in {"review", "verify", "approve"}:
            self.advance(job, "merging")
        require(job["stage"] == "merging", f"{job['id']} is in {job['stage']}, not merging")
        if job["kind"] == "sub":
            todo = [(job["repo"], job["worktree"], job["pr"], job["branch"], job["base"], self.round_dir(job))]
        else:
            todo = [(k, r["worktree"], r["pr"], r["branch"], self.home.base(k), self.dir(job["id"]) / "merge")
                    for k in repo_order(job["repos"]) for r in [job["repos"][k]]]
        for key, wt, pr, branch, base, out in todo:
            why = self._merge_one(job, key, wt, pr, branch, base, out)
            if why:
                self.dir(job["id"]).joinpath("decision.md").write_text(
                    f"ESCALATE\n\n## Per il team leader\n{why}\n", encoding="utf-8")
                return self.advance(job, "needs-human")
        self.advance(job, "merged")
        if job["kind"] == "parent":
            self.deliver(job)
        return job

    def deliver(self, job):
        o = self.odoo
        fe = o.tasks([["id", "in", job["fe_tasks"]]], fields=["stage_id"]) if job["fe_tasks"] else []
        pending = any(o.stage_key(t["stage_id"]) != "done" for t in fe)
        target = "waiting-fe" if pending else "test"
        if job["stage"] != target:
            self.advance(job, target)
        return job


def read_plan(path, cfg):
    body = section(path, "Piano") or ""
    m = re.search(r"```json\s*\n(.*?)```", body, re.S)
    require(m, "triage.md has no ```json block under ## Piano")
    try:
        plan = json.loads(m.group(1))
    except ValueError as e:
        raise Refused(f"the plan in triage.md isn't valid JSON: {e}") from None
    plan.setdefault("backend", [])
    plan.setdefault("frontend", [])
    require(plan["backend"] or plan["frontend"], "the plan has no sub-task")
    for kind in ("backend", "frontend"):
        for item in plan[kind]:
            require(item.get("title") and item.get("brief"), f"every {kind} sub-task needs a title and a brief")
            item.setdefault("risk", "medium")
            require(item["risk"] in {"low", "medium", "high"}, f"risk {item['risk']!r} isn't low, medium or high")
            item.setdefault("surfaces", [])
    return plan
