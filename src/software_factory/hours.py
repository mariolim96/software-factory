"""Hours the factory worked on each ticket, for `sf log`.

Two sources:

- the agents: each subagent transcript (see cost.py) has a timestamp on every
  message, and its `.meta.json` says which agent it was. An agent's work is from
  its first to its last message; a pause longer than IDLE (an agent resumed
  later) splits it in two.
- the gate: `sf gate` and `sf merge` run the checks themselves, without an agent;
  jobs.py records those runs in job["work"].

Claude Code deletes old transcripts, so every `sf advance` copies the agents'
intervals into job["work"] too; `sf log` adds what the transcripts show now.
Hours of work are the union of the intervals of a ticket (reviewers running in
parallel count once); agent hours are their sum.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

from .cost import job_transcripts

IDLE = timedelta(minutes=45)  # longer than a gate run inside an agent (`sf check`)


def parse(ts):
    return datetime.fromisoformat(ts)


def stamp(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def agent_work(home, job_dir):
    """The job's agent intervals, read from the transcripts: [{key, what, note, start, end}]."""
    out = []
    for path, lines in job_transcripts(home, job_dir):
        meta = {}
        try:
            meta = json.loads(path.with_name(path.stem + ".meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        times = []
        for line in lines:
            try:
                ts = json.loads(line).get("timestamp")
            except ValueError:
                continue
            if ts:
                times.append(parse(ts))
        times.sort()
        segments, start, last = [], None, None
        for t in times:
            if start is None:
                start = last = t
            elif t - last > IDLE:
                segments.append((start, last))
                start = last = t
            else:
                last = t
        if start is not None:
            segments.append((start, last))
        agent_id = path.stem.removeprefix("agent-")
        for n, (s, e) in enumerate(segments):
            if e > s:
                out.append({"key": f"agent:{agent_id}:{n}", "what": meta.get("agentType") or "agente",
                            "note": meta.get("description", ""), "start": stamp(s), "end": stamp(e)})
    return out


def merge_work(stored, fresh):
    """stored + fresh, the fresh copy winning on the same key, sorted by start."""
    by_key = {w["key"]: w for w in stored or []}
    by_key.update({w["key"]: w for w in fresh})
    return sorted(by_key.values(), key=lambda w: w["start"])


def seconds(w):
    return (parse(w["end"]) - parse(w["start"])).total_seconds()


def union_seconds(work):
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted((parse(w["start"]), parse(w["end"])) for w in work):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total


def hm(secs):
    m = round(secs / 60)
    return f"{m // 60}:{m % 60:02d}"


def tz(home):
    name = home.get("timezone")
    if name:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            print(f"sf: timezone {name!r} sconosciuta, uso quella della macchina", file=sys.stderr)
    return datetime.now().astimezone().tzinfo


def _tickets(f, args, since):
    """[(parent job, [(interval, job)] sorted by start)] for the tickets asked, with work after `since`."""
    jobs = f.all()
    if args:
        wanted = {f.resolve(a)["id"] for a in args}
        wanted |= {j["parent"] for j in jobs if j["id"] in wanted and j["kind"] == "sub"}
        parents = [j for j in jobs if j["kind"] == "parent" and j["id"] in wanted]
    else:
        parents = [j for j in jobs if j["kind"] == "parent"]
    out = []
    for p in parents:
        rows = []
        for j in [p, *(f.load(s) for s in p["subs"])]:
            for w in merge_work(j.get("work"), agent_work(f.home, f.dir(j["id"]))):
                if since is None or parse(w["end"]) >= since:
                    rows.append((w, j))
        if rows:
            out.append((p, sorted(rows, key=lambda r: r[0]["start"])))
    return out


def report(f, args, since=None, totals_only=False):
    """Lines of `sf log`: per ticket, every interval of work, then its hours."""
    zone = tz(f.home)
    lines, all_work, ticket_secs = [], [], 0.0
    tickets = _tickets(f, args, since)
    for p, rows in tickets:
        work = [w for w, _ in rows]
        all_work.extend(work)
        lines.append(f"T{p['task']}  {p['title']}  ({p['stage']})")
        if not totals_only:
            for w, j in rows:
                s, e = parse(w["start"]).astimezone(zone), parse(w["end"]).astimezone(zone)
                lines.append(f"  {s:%Y-%m-%d %H:%M}–{e:%H:%M}  {hm(seconds(w)):>5}  T{j['task']:<6} "
                             f"{w['what']:<20} {w.get('note', '')}".rstrip())
        ticket_secs += union_seconds(work)
        lines.append(f"  ore di lavoro {hm(union_seconds(work))} (ore agente {hm(sum(map(seconds, work)))})")
        lines.append("")
    if not tickets:
        return ["nessuna ora registrata"]
    lines.append(f"totale {hm(ticket_secs)} ore di lavoro su {len(tickets)} ticket "
                 f"(ore agente {hm(sum(map(seconds, all_work)))})")
    return lines


ACTIVITY = {"triage": "analisi", "spec-writer": "specifica", "builder": "sviluppo", "gate": "controlli",
            "e2e": "test end-to-end",
            "verifier": "verifica della review", "approver": "approvazione"}


WEEKDAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def activity(what):
    return "review" if what.startswith("reviewer") else ACTIVITY.get(what, what)


def round_up(secs, step):
    """Minutes, rounded up to `step` minutes; at least one step when there was any work."""
    m = -(-secs // 60)
    return int(-(-m // step) * step) if secs > 0 else 0


def timesheet(f, args, since=None):
    """Lines of `sf log --timesheet`: per day and ticket, the hours to write in the timesheet.
    Each interval goes on the day it starts; hours are the union, rounded up to
    `timesheet_round_minutes` (default 15) for each day and ticket."""
    zone = tz(f.home)
    step = int(f.home.get("timesheet_round_minutes", 15)) or 1
    days = {}
    for p, rows in _tickets(f, args, since):
        for w, _j in rows:
            day = parse(w["start"]).astimezone(zone).date()
            days.setdefault(day, {}).setdefault(p["id"], (p, []))[1].append(w)
    if not days:
        return ["nessuna ora registrata"]
    lines, grand = [], 0
    for day in sorted(days):
        lines.append(f"{day:%Y-%m-%d} {WEEKDAYS[day.weekday()]}")
        total = 0
        for p, work in sorted(days[day].values(), key=lambda t: t[0]["task"]):
            m = round_up(union_seconds(work), step)
            total += m
            acts = list(dict.fromkeys(activity(w["what"]) for w in sorted(work, key=lambda w: w["start"])))
            lines.append(f"  T{p['task']:<6} {m // 60}:{m % 60:02d}  {m / 60:5.2f} h  {p['title']}")
            lines.append(f"  {'':<6}  {'':4}  {'':7}  {', '.join(acts)}")
        lines.append(f"  totale  {total // 60}:{total % 60:02d}  {total / 60:5.2f} h")
        lines.append("")
        grand += total
    if len(days) > 1:
        lines.append(f"totale del periodo {grand // 60}:{grand % 60:02d} ({grand / 60:.2f} h)")
    else:
        lines.pop()
    return lines
