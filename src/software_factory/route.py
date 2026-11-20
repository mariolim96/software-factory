"""Which reviewers look at a sub-task's branch, and on which model.

Two sources, so a reviewer can't be skipped because the triage missed something:
- the triage's risk and surfaces for the sub-task (`routing`, `surface_reviewers`);
- the diff itself: files matching `path_reviewers.<name>.paths` and added lines
  matching `path_reviewers.<name>.patterns` (e.g. `sudo(` or `cr.execute`).
When both ask for a reviewer, the stronger model wins.
"""

import fnmatch
import re

STRENGTH = {"haiku": 0, "sonnet": 1, "opus": 2}


def stronger(a, b):
    if a is None:
        return b
    return a if STRENGTH.get(a, 0) >= STRENGTH.get(b, 0) else b


def added_lines(diff):
    return "\n".join(line[1:] for line in diff.splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def route(cfg, risk, surfaces, files, diff):
    if risk not in cfg["routing"]:
        raise ValueError(f"risk {risk!r} isn't in routing ({', '.join(cfg['routing'])})")
    reviewers = dict(cfg["routing"][risk])
    for s in surfaces:
        for name, model in cfg.get("surface_reviewers", {}).get(s, {}).items():
            reviewers[name] = stronger(reviewers.get(name), model)
    added = added_lines(diff)
    for name, rule in cfg.get("path_reviewers", {}).items():
        by_path = any(fnmatch.fnmatch(f, p) for f in files for p in rule.get("paths", []))
        by_text = any(re.search(rx, added) for rx in rule.get("patterns", []))
        if by_path or by_text:
            reviewers[name] = stronger(reviewers.get(name), rule["model"])
    return reviewers


def high_risk_files(cfg, files):
    return [f for f in files if any(fnmatch.fnmatch(f, p) for p in cfg.get("high_risk_paths", []))]
