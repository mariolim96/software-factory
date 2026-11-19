"""What `/factory run` should do now.

`queue` first moves the jobs whose next step needs no agent (PRs merged on
GitHub, front-end sub-tasks Done, the PM closing a task), then prints one row per
job or new task:

  action  job  task  stage  next  note

action is one of:
  base    a repository's integration branch hasn't been gated at this commit: run `next` first
  pause   an integration branch is red: nothing starts until it is fixed
  resume  a running job: continue it from its stage; when `next` isn't "-", run it first
  start   a sub-task whose turn has come: run `next`, then spawn its spec-writer
  approve every factory sub-task of a parent is merged: run `next`, then spawn the approver
  new     a task tagged for the factory without a job: run `next` (sf create), then the triage
  hold    would start, but every concurrency slot is busy
  wait    waits on a person (PM, team leader, front-end)
  error   a deterministic move failed: the note says why
"""

from .gate import base_report
from .jobs import RUNNING
from .util import Refused


def _settle(f, jobs, errors):
    """Moves that need no agent."""
    o = f.odoo
    queue_tag = o.tag_ids["queue"]
    for j in jobs:
        try:
            st = j["stage"]
            if j["kind"] == "parent":
                if f.fe_feedback(j):  # a report from the front-end: routed to the team leader
                    j = f.load(j["id"])
                if st in {"approve", "ready", "needs-human"} and f.parent_prs_merged(j):
                    j = f.locked(j["id"])
                    f.advance(j, "merged")
                    f.deliver(j)
                elif st == "building" and not j["subs"] and j["fe_tasks"]:
                    f.deliver(f.locked(j["id"]))
                elif st == "waiting-fe":
                    f.deliver(f.locked(j["id"]))
                elif st == "test" and o.stage_key(o.task(j["task"])["stage_id"]) == "done":
                    f.advance(f.locked(j["id"]), "done")
                elif st in {"unclear", "ready", "needs-human"} and queue_tag not in o.task(j["task"])["tag_ids"]:
                    f.advance(f.locked(j["id"]), "dropped")
            elif st == "needs-human" and j["pr"] and f._pr_state(j["repo"], j["pr"]) == "MERGED":
                f.advance(f.locked(j["id"]), "merged")
        except Refused as e:
            errors.append(("error", j["id"], f"T{j['task']}", j["stage"], "-", str(e)))


def queue(f):
    home = f.home
    for key in home.repo_keys():
        verdict, sha, report = base_report(home, key)
        if verdict is None:
            return [("base", "-", "-", "-", f"sf gate --base {key}",
                     f"{key}: {home.base(key)}@{sha[:7]} non ancora controllato")]
        if verdict != "PASS":
            return [("pause", "-", "-", "-", "-",
                     f"{key}: {home.base(key)}@{sha[:7]} è rosso, la factory è in pausa: {report}")]

    errors = []
    _settle(f, f.all(), errors)
    jobs = f.all()
    by_id = {j["id"]: j for j in jobs}
    o = f.odoo

    running = [j for j in jobs if j["stage"] in RUNNING[j["kind"]]]
    rows = [("resume", j["id"], f"T{j['task']}", j["stage"], "-",
             f"giro {j['round']}" if j["kind"] == "sub" else f"ciclo {j['cycle']}") for j in running]
    candidates, waits = [], []

    for j in jobs:
        st, tid, jid = j["stage"], f"T{j['task']}", j["id"]
        if j["kind"] == "parent":
            fb = j.get("fe_feedback")
            if st in {"unclear", "ready", "needs-human"}:
                if f.parent_answer(j):
                    candidates.append(("resume", jid, tid, st, f"sf advance {jid} triage",
                                       "nuovo commento del PM o del team leader"))
                else:
                    waits.append(("wait", jid, tid, st, "-", {
                        "unclear": "aspetta la risposta alle domande del triage",
                        "ready": "aspetta il merge delle PR o un commento del team leader",
                        "needs-human": "aspetta una decisione del team leader"}[st]))
            elif st == "test":
                if o.stage_key(o.task(j["task"])["stage_id"]) == "todo":
                    candidates.append(("resume", jid, tid, st, f"sf advance {jid} triage", "riaperto dal PM"))
                else:
                    waits.append(("wait", jid, tid, st, "-", "in test dal PM"))
            elif st == "waiting-fe":
                if fb and f.parent_answer(j):
                    candidates.append(("resume", jid, tid, st, f"sf advance {jid} triage",
                                       "il team leader ha risposto alla segnalazione del front-end"))
                else:
                    waits.append(("wait", jid, tid, st, "-",
                                  "aspetta il team leader sulla segnalazione del front-end" if fb
                                  else "aspetta i sottotask front-end"))
            elif st == "building":
                if fb:
                    waits.append(("wait", jid, tid, st, "-", "segnalazione del front-end al team leader: "
                                  "la risposta si legge quando il back-end di questo ciclo è unito"))
                subs = [by_id[s] for s in j["subs"]]
                if subs and all(s["stage"] == "merged" for s in subs):
                    candidates.append(("approve", jid, tid, st, f"sf advance {jid} approve", "sottotask completi"))
                    continue
                nxt = next((s for s in subs if s["stage"] != "merged"), None)
                if nxt and nxt["stage"] == "queued":
                    candidates.append(("start", nxt["id"], f"T{nxt['task']}", "queued",
                                       f"sf advance {nxt['id']} spec", f"[{nxt['repo']}] {nxt['title']}"))
        elif st == "question":
            parent = by_id[j["parent"]]
            if f.latest_instruction(parent):
                candidates.append(("resume", jid, tid, st, f"sf advance {jid} spec",
                                   f"il PM ha risposto su T{parent['task']}"))
            else:
                waits.append(("wait", jid, tid, st, "-", f"aspetta la risposta del PM su T{parent['task']}"))
        elif st == "needs-human":
            if f.latest_instruction(j):
                candidates.append(("resume", jid, tid, st, f"sf advance {jid} build", "il team leader ha risposto"))
            else:
                waits.append(("wait", jid, tid, st, "-", "aspetta una decisione del team leader"))

    known = {j["task"] for j in jobs}
    tagged = o.tasks([["stage_id", "=", o.stages["todo"]], ["tag_ids", "in", [o.tag_ids["queue"]]],
                      ["parent_id", "=", False]], fields=["name"])
    for t in tagged:
        if t["id"] not in known:
            candidates.append(("new", "-", f"T{t['id']}", "-", f"sf create {t['id']}", t["name"]))

    slots = f.cfg["concurrency"] - len(running)
    for row in candidates:
        if slots > 0:
            rows.append(row)
            slots -= 1
        else:
            rows.append(("hold",) + row[1:5] + (f"concurrency {f.cfg['concurrency']} raggiunta",))
    return rows + waits + errors
