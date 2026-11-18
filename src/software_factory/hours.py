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


def report(f, args, since=None, totals_only=False):
    """Lines of `sf log`: per ticket, every interval of work, then its hours."""
    jobs = f.all()
    if args:
        wanted = {f.resolve(a)["id"] for a in args}
        wanted |= {j["parent"] for j in jobs if j["id"] in wanted and j["kind"] == "sub"}
        parents = [j for j in jobs if j["kind"] == "parent" and j["id"] in wanted]
    else:
        parents = [j for j in jobs if j["kind"] == "parent"]
    zone = tz(f.home)
    lines, all_work, ticket_secs = [], [], 0.0
    for p in parents:
        rows = []
        for j in [p, *(f.load(s) for s in p["subs"])]:
            for w in merge_work(j.get("work"), agent_work(f.home, f.dir(j["id"]))):
                if since is None or parse(w["end"]) >= since:
                    rows.append((w, j))
        if not rows:
            continue
        rows.sort(key=lambda r: r[0]["start"])
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
    if not lines:
        return ["nessuna ora registrata"]
    n = sum(1 for line in lines if line.startswith("T"))
    lines.append(f"totale {hm(ticket_secs)} ore di lavoro su {n} ticket "
                 f"(ore agente {hm(sum(map(seconds, all_work)))})")
    return lines
