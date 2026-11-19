"""sf: the factory's deterministic spine. The orchestrator skill calls it; agents never change state.

  sf init <dir> --profile P [--repo OWNER/NAME] [--base BRANCH] [--key be|fe]   create or update a factory home
  sf add-repo <key> --profile P --repo OWNER/NAME [--base BRANCH]  add a repository (e.g. fe) to this home
  sf refs                         clone/update the read-only references (framework source, docs)
  sf doctor                       check Odoo credentials, project, stages, tags, users, gh, repos
  sf odoo-key [--also FILE]       save a new Odoo API key (asked without echo, checked first)
  sf queue                        what /factory run should do now (see `sf queue --help`)
  sf create <task>                new parent job for an Odoo task tagged for the factory
  sf advance <job> <stage>        move a job, or refuse with the reason
  sf check <job>                  the builder's dry run of the gate checks in its worktree
  sf gate <job>                   run the gate on a sub-task in gate → round-N/checks.md
  sf gate --base [<repo>] [--force]   gate the integration branch(es) alone (cached per commit)
  sf merge <job>                  sync with the base, gate, merge the PR(s) (sub-task or parent)
  sf header <job>                 the line every agent prompt starts with
  sf report start|close|header <task>     a front-end report: open it (prints the fe-report header),
                                  act on its verdict.md, or reprint the header
  sf task <job|task>              (re)write task.md, or print any project task as the agents see it
  sf reviews <job>                refresh the round's verdicts; prints NEXT: wait|verify|merge|build|needs-human
  sf summary [<job>...]           table of jobs: stage, round, PR, cost
  sf metrics [--days N]           review and merge numbers: rounds to merge, findings rejected, auto merges, reverts
  sf log [<task|job>...] [--days N | --since YYYY-MM-DD] [--totals | --timesheet]
                                  hours of work per ticket: every agent and gate run, with totals;
                                  --timesheet: per day and ticket, rounded, to copy into the timesheet
  sf mcp                          run the read-only Odoo MCP server (stdio)
"""

import argparse
import json
import re
import shutil
import sys
from datetime import UTC
from pathlib import Path

from .config import ConfigError, Home
from .odoo import OdooError
from .util import Refused, require, run


def _factory():
    from .jobs import Factory
    return Factory(Home())


def _print_rows(rows):
    for row in rows:
        print("\t".join(str(c) for c in row))


def cmd_doctor(f):
    o = f.odoo
    ok = True

    def check(label, fn):
        nonlocal ok
        try:
            print(f"ok    {label}: {fn()}")
        except Exception as e:  # report every problem, not just the first
            ok = False
            print(f"FAIL  {label}: {e}")

    check("Odoo", lambda: o.call("res.users", "context_get") and o.url)
    check("progetto", lambda: o.call("project.project", "read", ids=[o.project], fields=["name"])[0]["name"])
    stage_names = {r["id"]: r["name"] for r in o.call("project.task.type", "read", ids=list(o.stages.values()), fields=["name"])}
    for k, v in o.stages.items():
        check(f"stadio {k}", lambda v=v: stage_names[v])
    for k, v in o.tag_ids.items():
        check(f"tag {k}", lambda v=v: o.tag_names([v])[v])
    for k, v in o.users.items():
        check(f"utente {k}", lambda v=v: o.call("res.users", "read", ids=[v], fields=["name"])[0]["name"])
    check("gh", lambda: run(["gh", "auth", "status"], cwd=f.home.root).returncode == 0 and "autenticato")

    def mcp_server():
        from . import mcp_server  # noqa: F401  (fails when the mcp library changed its API)
        cfg = json.loads((f.home.root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["odoo"]
        if not shutil.which(cfg["command"]):
            raise Exception(f"{cfg['command']} non è eseguibile: rilancia sf init")
        return cfg["command"]
    check("server MCP odoo", mcp_server)
    for key in f.home.repo_keys():
        def present(path, hint):
            if not path.exists():
                raise Exception(f"manca {path}: {hint}")
            return str(path)
        check(f"repo {key}", lambda key=key: run(["git", "remote", "get-url", "origin"], cwd=f.home.repo_path(key)).stdout.strip()
              + f" → {f.home.base(key)}, profilo {f.home.profile(key)}")
        check(f"controlli {key}", lambda key=key: present(f.home.checks_dir(key) / "check.sh", "copiali dal profilo"))
        check(f"profilo {key}", lambda key=key: present(f.home.profile_md(key), "rilancia sf init"))
    return 0 if ok else 1


def cmd_summary(f, ids):
    jobs = [f.resolve(i) for i in ids] if ids else f.all()
    rows = [("job", "task", "stadio", "giro", "pr", "costo")]
    for j in jobs:
        if j["kind"] == "sub":
            pr, rnd = (f"{j['repo']}#{j['pr']}" if j["pr"] else "-"), str(j["round"])
        else:
            pr = " ".join(f"{k}#{r['pr']}" for k, r in sorted(j["repos"].items()) if r.get("pr")) or "-"
            rnd = f"c{j['cycle']}"
        rows.append((j["id"], f"T{j['task']}", j["stage"], rnd, pr, f"${j.get('cost_usd', 0):.2f}"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    print(f"totale ${sum(j.get('cost_usd', 0) for j in jobs):.2f} (equivalente API, solo agenti della factory)")


def cmd_metrics(f, days):
    from .jobs import RISK_RANK
    from .util import git
    jobs = f.all()
    subs = [j for j in jobs if j["kind"] == "sub" and j["stage"] == "merged"]
    rounds = [j["round"] for j in subs]
    logs = [r for j in jobs if j["kind"] == "sub" for r in j.get("rounds_log", [])]
    tot = {k: sum(r.get(k, 0) for r in logs) for k in ("important", "confirmed", "rejected", "nit", "preexisting")}
    parents = [j for j in jobs if j["kind"] == "parent" and j.get("merge")]
    print(f"sottotask uniti: {len(subs)}")
    if rounds:
        print(f"  giri per arrivare al merge: media {sum(rounds) / len(rounds):.2f}, "
              f"al primo giro {sum(r == 1 for r in rounds)}/{len(rounds)}")
    print(f"findings Importante: {tot['important']} (confermati {tot['confirmed']}, "
          f"respinti dai verificatori {tot['rejected']}); nit {tot['nit']}; preesistenti {tot['preexisting']}")
    if tot["important"]:
        print(f"  respinti: {100 * tot['rejected'] / tot['important']:.0f}% (rumore che il builder non ha visto)")
    print(f"padri approvati: {len(parents)}")
    for tier in sorted({p['merge']['tier'] for p in parents}, key=RISK_RANK.get):
        group = [p for p in parents if p["merge"]["tier"] == tier]
        auto = sum(p["merge"]["mode"] == "auto" for p in group)
        print(f"  rischio {tier}: {auto} merge automatici, {len(group) - auto} dal team leader")
    tasks = {j["task"] for j in jobs if j["kind"] == "parent"}
    reverted = []
    for key in f.home.repo_keys():
        repo, base = f.home.repo_path(key), f.home.base(key)
        git(repo, "fetch", "origin", base, check=False)
        log = git(repo, "log", f"origin/{base}", f"--since={days}.days", "--format=%h %s",
                  "--grep=^Revert", check=False).stdout
        for line in log.splitlines():
            m = re.search(r"\bT(\d+)\b", line)
            if m and int(m.group(1)) in tasks:
                reverted.append(f"{key} {line}")
    merged_auto = sum(p["merge"]["mode"] == "auto" for p in parents)
    print(f"revert di task della factory negli ultimi {days} giorni: {len(reverted)} "
          f"(merge automatici in totale: {merged_auto})")
    for r in reverted:
        print(f"  {r}")


def cmd_odoo_key(home, args):
    """Save a new Odoo API key after checking it. Never prints the key."""
    import getpass
    import os

    from .odoo import check_key, read_credentials
    p = argparse.ArgumentParser(prog="sf odoo-key")
    p.add_argument("--also", action="append", default=[], metavar="FILE",
                   help="un'altra copia del file delle credenziali da aggiornare")
    a = p.parse_args(args)
    if sys.stdin.isatty():
        key = getpass.getpass("Nuova chiave API di Odoo (non viene mostrata): ").strip()
    else:
        key = sys.stdin.readline().strip()
    require(key, "nessuna chiave")
    uid = check_key(home.cfg, key)
    path = Path(home.cfg["odoo"]["credentials_file"]).expanduser()
    for target in [path, *(Path(x).expanduser() for x in a.also)]:
        creds = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        creds["api_key"] = key
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(creds, out, indent=2)
            out.write("\n")
        tmp.replace(target)
        print(f"salvata in {target}")
    read_credentials(home.cfg)
    print(f"chiave valida (utente Odoo {uid}). Le sessioni aperte la rileggono alla prossima chiamata.")


def cmd_log(f, args):
    from datetime import datetime, timedelta

    from .hours import report, timesheet, tz
    p = argparse.ArgumentParser(prog="sf log")
    p.add_argument("jobs", nargs="*")
    p.add_argument("--days", type=int)
    p.add_argument("--since", help="YYYY-MM-DD")
    p.add_argument("--totals", action="store_true", help="solo il totale di ogni ticket")
    p.add_argument("--timesheet", action="store_true",
                   help="per giorno e ticket, le ore arrotondate da segnare nel timesheet")
    a = p.parse_args(args)
    since = None
    if a.since:
        try:
            since = datetime.strptime(a.since, "%Y-%m-%d").replace(tzinfo=tz(f.home))
        except ValueError:
            raise Refused(f"--since {a.since}: usa YYYY-MM-DD") from None
    elif a.days:
        since = datetime.now(UTC) - timedelta(days=a.days)
    lines = timesheet(f, a.jobs, since) if a.timesheet else report(f, a.jobs, since, a.totals)
    print("\n".join(lines))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__.strip())
        return 0
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "init":
            from .scaffold import init
            p = argparse.ArgumentParser(prog="sf init")
            p.add_argument("dir")
            p.add_argument("--profile")
            p.add_argument("--repo")
            p.add_argument("--base", default="main")
            p.add_argument("--key", default="be", help="be (back-end), fe (front-end) o docs (documentazione)")
            a = p.parse_args(args)
            root, done = init(a.dir, a.profile, a.repo, a.base, a.key)
            print(f"{root}:\n" + "\n".join(f"- {d}" for d in done))
            return 0
        if cmd == "odoo-key":
            cmd_odoo_key(Home(), args)
            return 0
        if cmd == "mcp":
            from .mcp_server import main as mcp_main
            mcp_main()
            return 0

        f = _factory()
        if cmd == "add-repo":
            from .scaffold import add_repo
            p = argparse.ArgumentParser(prog="sf add-repo")
            p.add_argument("key")
            p.add_argument("--profile", required=True)
            p.add_argument("--repo", required=True)
            p.add_argument("--base", default="main")
            a = p.parse_args(args)
            print("\n".join(f"- {d}" for d in add_repo(f.home.root, a.key, a.profile, a.repo, a.base)))
        elif cmd == "refs":
            from .scaffold import sync_refs
            print("\n".join(sync_refs(f.home)) or "nessun riferimento in factory.json")
        elif cmd == "doctor":
            return cmd_doctor(f)
        elif cmd == "queue":
            if args == ["--help"]:
                from . import queue
                print(queue.__doc__.strip())
                return 0
            from .queue import queue
            _print_rows(queue(f))
        elif cmd == "create" and len(args) == 1:
            print(f.create(int(args[0].lstrip("tT")))["id"])
        elif cmd == "advance" and len(args) == 2:
            job = f.advance(f.locked(f.resolve(args[0])["id"]), args[1])
            extra = f" (giro {job['round']})" if job["kind"] == "sub" else ""
            print(f"{job['id']}: {job['stage']}{extra}")
        elif cmd == "gate" and args[:1] == ["--base"]:
            from .gate import base_check
            keys = [a for a in args[1:] if not a.startswith("--")] or f.home.repo_keys()
            failed = False
            for key in keys:
                verdict, sha, report = base_check(f.home, key, force="--force" in args)
                print(f"GATE: {verdict} ({key}: {f.home.base(key)}@{sha[:7]}): {report}")
                failed |= verdict != "PASS"
            return 1 if failed else 0
        elif cmd == "gate" and len(args) == 1:
            job_id = f.resolve(args[0])["id"]
            verdict = f.gate(job_id)
            job = f.load(job_id)
            print(f"GATE: {verdict} ({job_id} giro {job['round']}): {f.round_dir(job) / 'checks.md'}")
            return 0 if verdict == "PASS" else 1
        elif cmd == "check" and len(args) == 1:
            from .gate import try_checks
            job = f.resolve(args[0])
            require(job["kind"] == "sub", "sf check is for sub-tasks")
            code, log, seconds, protected = try_checks(f.home, job["repo"], job["id"], job["worktree"], job["base"])
            print("\n".join(log.rstrip().splitlines()[-120:]))
            if protected:
                print(f"\nPercorsi protetti modificati (il gate fallirà): {', '.join(protected)}")
            print(f"\nCHECKS: {'PASS' if code == 0 and not protected else 'FAIL'} (exit {code}, {seconds}s)")
            return 0 if code == 0 and not protected else 1
        elif cmd == "merge" and len(args) == 1:
            job = f.merge(f.locked(f.resolve(args[0])["id"]))
            print(f"{job['id']}: {job['stage']}")
        elif cmd == "header" and len(args) == 1:
            print(f.header(f.resolve(args[0])))
        elif cmd == "report" and len(args) == 2 and args[0] in {"start", "close", "header"}:
            from . import reports
            tid = reports.task_id(args[1])
            if args[0] == "start":
                print(reports.report_header(f, reports.start(f, tid)))
            elif args[0] == "header":
                print(reports.report_header(f, reports.open_report(f, tid)))
            else:
                rep = reports.close(f, tid)
                print(f"{rep['id']}: {rep['verdict']}" + (f" → T{rep['task']}" if rep["task"] else ""))
        elif cmd == "task" and len(args) == 1:
            arg = args[0]
            try:
                job = f.locked(f.resolve(arg)["id"])
                f.write_task_md(job)
                print(f.dir(job["id"]) / "task.md")
            except Refused:
                from . import taskdoc
                print(taskdoc.render(f.odoo, int(arg.lstrip("tT"))))
        elif cmd == "reviews" and len(args) == 1:
            job = f.locked(f.resolve(args[0])["id"])
            nxt = f.review_next(job)
            f.save(job)
            print(json.dumps(job["reviews"]))
            print(f"NEXT: {nxt}")
        elif cmd == "summary":
            cmd_summary(f, args)
        elif cmd == "metrics":
            p = argparse.ArgumentParser(prog="sf metrics")
            p.add_argument("--days", type=int, default=90)
            cmd_metrics(f, p.parse_args(args).days)
        elif cmd == "log":
            cmd_log(f, args)
        else:
            print(f"sf: unknown command or arguments: {' '.join(argv)}\n\n{__doc__.strip()}", file=sys.stderr)
            return 2
    except (Refused, ConfigError, OdooError) as e:
        print(f"sf: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
