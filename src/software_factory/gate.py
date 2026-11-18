"""The deterministic gate: does a branch pass the project's checks?

For a repository `<key>`, the gate runs the home's `<checks>/check.sh` inside the
job's worktree, then `<checks>/upgrade.sh` when the profile has one (install from
the integration branch, then upgrade to this branch). The checks live in the
factory home, outside the code repository, so a branch can't change what it is
judged by. The gate also fails a branch that modifies or deletes a file matching
the repository's `protected_paths`.

Environment given to the checks:
  FACTORY_JOB, FACTORY_HOME, FACTORY_REPO   job id, factory home, repository key
  FACTORY_BASE_REF / FACTORY_BASE_BRANCH    origin/<branch the job merges into> (the parent branch for a sub-task)
  FACTORY_TARGET_REF / FACTORY_TARGET_BRANCH  origin/<integration branch> (develop/main)
  FACTORY_TARGET_WORKTREE  a detached checkout of the integration branch, removed afterwards:
                         the trusted copy of repo scripts, and the "before" of the upgrade test
"""

import fnmatch
import os
import time
from pathlib import Path

from .util import git, require, run

TAIL = 80


def protected_changes(rconf, worktree, base_ref):
    diff = git(worktree, "diff", "--name-status", f"{base_ref}...HEAD").stdout
    hits = []
    for line in diff.splitlines():
        status, *paths = line.split("\t")
        if status.startswith("A"):
            continue
        if any(fnmatch.fnmatch(paths[0], p) for p in rconf.get("protected_paths", [])):
            hits.append(f"{status[0]} {paths[0]}")
    return hits


def _target_worktree(home, key, tag, ref=None):
    path = home.worktrees / f"_target-{tag}"
    repo = home.repo_path(key)
    if path.exists():
        git(repo, "worktree", "remove", "--force", str(path), check=False)
    git(repo, "worktree", "prune", check=False)
    git(repo, "worktree", "add", "--detach", str(path), ref or f"origin/{home.base(key)}")
    return path


def _env(home, key, job_id, base_branch, target):
    return dict(os.environ, FACTORY_JOB=job_id, FACTORY_HOME=str(home.root), FACTORY_REPO=key,
                FACTORY_BASE_REF=f"origin/{base_branch}", FACTORY_BASE_BRANCH=base_branch,
                FACTORY_TARGET_REF=f"origin/{home.base(key)}", FACTORY_TARGET_BRANCH=home.base(key),
                FACTORY_TARGET_WORKTREE=str(target))


def _run_checks(checks, worktree, env, upgrade=True):
    log, code, start = [], 0, time.monotonic()
    for script in ("check.sh", "upgrade.sh"):
        path = checks / script
        if not path.exists() or (script == "upgrade.sh" and not upgrade):
            continue
        res = run(["bash", str(path)], cwd=worktree, check=False, env=env)
        log.append(f"===== {script} (exit {res.returncode}) =====\n{res.stdout}{res.stderr}")
        if res.returncode != 0:
            code = res.returncode
            break
    return code, "\n".join(log), int(time.monotonic() - start)


def run_gate(home, key, job_id, worktree, base_branch, out_dir, name, note=""):
    """Write <out_dir>/<name>.md and .log; return "PASS" or "FAIL"."""
    checks = home.checks_dir(key)
    require((checks / "check.sh").exists(), f"{checks}/check.sh doesn't exist")
    git(home.repo_path(key), "fetch", "origin", base_branch, home.base(key))
    commit = git(worktree, "rev-parse", "HEAD").stdout.strip()
    protected = protected_changes(home.rconf(key), worktree, f"origin/{base_branch}")
    target = _target_worktree(home, key, job_id)
    try:
        code, log, seconds = _run_checks(checks, worktree, _env(home, key, job_id, base_branch, target))
    finally:
        git(home.repo_path(key), "worktree", "remove", "--force", str(target), check=False)
    verdict = "PASS" if code == 0 and not protected else "FAIL"
    write_report(out_dir, name, verdict, commit, code, log, seconds, protected, note)
    return verdict


def try_checks(home, key, job_id, worktree, base_branch):
    """The gate's checks for the builder, before it hands over: no report, no state."""
    git(home.repo_path(key), "fetch", "origin", base_branch, home.base(key))
    target = _target_worktree(home, key, f"{job_id}-try")
    try:
        code, log, seconds = _run_checks(home.checks_dir(key), worktree, _env(home, key, job_id, base_branch, target))
    finally:
        git(home.repo_path(key), "worktree", "remove", "--force", str(target), check=False)
    return code, log, seconds, protected_changes(home.rconf(key), worktree, f"origin/{base_branch}")


def write_report(out_dir, name, verdict, commit, code, log, seconds, protected, note=""):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.log").write_text(log, encoding="utf-8")
    tail = "\n".join(log.rstrip().splitlines()[-TAIL:])
    summary = [
        f"I controlli {'sono passati' if code == 0 else f'sono falliti con exit {code}'} in {seconds}s",
        "Percorsi protetti: " + (("modificati: " + ", ".join(protected)) if protected else "nessuno modificato"),
        f"Commit {commit[:7]}" + (f", {note}" if note else ""),
    ]
    (out_dir / f"{name}.md").write_text(
        f"GATE: {verdict}\nCOMMIT: {commit}\n\n"
        "## Summary\n" + "\n".join(f"- {s}" for s in summary) + "\n\n"
        "## Percorsi protetti\n" + ("\n".join(f"- {p}" for p in protected) if protected else "Nessuno modificato.")
        + f"\n\n## Controlli\nExit {code} in {seconds}s. Ultime {TAIL} righe di {name}.log:\n\n```\n{tail}\n```\n",
        encoding="utf-8")


def base_report(home, key):
    """(verdict or None, sha, report path) for the integration branch at its current commit."""
    repo = home.repo_path(key)
    git(repo, "fetch", "origin", home.base(key))
    sha = git(repo, "rev-parse", f"origin/{home.base(key)}").stdout.strip()
    report = home.jobs / "_base" / f"{key}-{sha}.md"
    if not report.exists():
        return None, sha, report
    return report.read_text(encoding="utf-8").split("\n", 1)[0].split(": ")[1], sha, report


def base_check(home, key, force=False):
    """Gate the integration branch alone, cached per commit in jobs/_base/<key>-<sha>.md.

    When the integration branch itself is red, every job would fail its gate for
    reasons that aren't its own: the factory pauses instead.
    """
    verdict, sha, report = base_report(home, key)
    if verdict and not force:
        return verdict, sha, report
    wt = _target_worktree(home, key, f"_base-{key}", sha)
    try:
        code, log, seconds = _run_checks(home.checks_dir(key), wt, _env(home, key, "_base", home.base(key), wt),
                                         upgrade=False)
    finally:
        git(home.repo_path(key), "worktree", "remove", "--force", str(wt), check=False)
    verdict = "PASS" if code == 0 else "FAIL"
    write_report(report.parent, report.stem, verdict, sha, code, log, seconds, [], f"{home.base(key)} da solo")
    return verdict, sha, report
