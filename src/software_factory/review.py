"""Findings of a review round: parse them, apply the verifiers' votes, write findings.md.

A reviewer tags every finding of `## Findings` with a severity:

  1. [Importante] path:line  the problem and the fix          blocks the merge
  2. [Nit] ...                                                 never blocks
  3. [Preesistente] ...                                        not this branch's fault; never blocks

An untagged finding counts as Importante. Before the builder sees them, every Importante
finding goes to `verify.votes` verifier agents (round-N/verify-<k>.md):

  - code-1: CONFERMATO  why
  - security-2: RESPINTO  why

and stays only with at least `verify.confirm` CONFERMATO. With `votes: 0` there is no
verification and every Importante finding stays.
"""

import re

from .util import Refused, section

SEVERITIES = {"importante": "Importante", "nit": "Nit", "preesistente": "Preesistente"}
ITEM = re.compile(r"^\s*(\d+)\.\s+(.*)$")
TAG = re.compile(r"^\[(\w+)\]\s*(.*)$", re.S)
VOTE = re.compile(r"^\s*[-*]\s*`?([a-z0-9][\w-]*-\d+)`?\s*:\s*\**(CONFERMATO|RESPINTO)\**\b\s*[-:—]*\s*(.*)$", re.I)


def verify_cfg(cfg):
    v = cfg.get("verify", {})
    votes = int(v.get("votes", 2))
    return {"votes": votes, "confirm": min(int(v.get("confirm", votes)), votes), "model": v.get("model", "sonnet")}


def parse_findings(path, reviewer):
    """The numbered items under `## Findings`, with continuation lines, as {id, reviewer, severity, text}."""
    body = section(path, "Findings") or ""
    items, cur = [], None
    for line in body.splitlines():
        m = ITEM.match(line)
        if m:
            cur = {"n": m.group(1), "text": m.group(2)}
            items.append(cur)
        elif cur is not None and line.strip():
            cur["text"] += "\n" + line.rstrip()
    out = []
    for it in items:
        sev, text = "Importante", it["text"].strip()
        t = TAG.match(text)
        if t and t.group(1).lower() in SEVERITIES:
            sev, text = SEVERITIES[t.group(1).lower()], t.group(2).strip()
        out.append({"id": f"{reviewer}-{it['n']}", "reviewer": reviewer, "severity": sev, "text": text})
    return out


def round_findings(rd, reviewers):
    found = []
    for r in reviewers:
        p = rd / f"review-{r}.md"
        if p.exists():
            found += parse_findings(p, r)
    return found


def verify_files(rd, votes):
    return [rd / f"verify-{k}.md" for k in range(1, votes + 1)]


def read_votes(path, ids):
    """{finding id: (CONFERMATO|RESPINTO, why)}; refuses when a finding has no vote."""
    votes = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = VOTE.match(line)
        if m and m.group(1) in ids:
            votes[m.group(1)] = (m.group(2).upper(), m.group(3).strip())
    missing = [i for i in ids if i not in votes]
    if missing:
        raise Refused(f"{path.name} has no vote for {', '.join(missing)}: run that verifier again")
    return votes


def apply_votes(findings, rd, vcfg):
    """Mark each Importante finding confirmed or rejected. Returns the findings."""
    important = [f for f in findings if f["severity"] == "Importante"]
    if not vcfg["votes"]:
        for f in important:
            f["outcome"] = "confermato"
        return findings
    ids = [f["id"] for f in important]
    ballots = [read_votes(p, ids) for p in verify_files(rd, vcfg["votes"])]
    for f in important:
        yes = [b[f["id"]][1] for b in ballots if b[f["id"]][0] == "CONFERMATO"]
        no = [b[f["id"]][1] for b in ballots if b[f["id"]][0] == "RESPINTO"]
        f["outcome"] = "confermato" if len(yes) >= vcfg["confirm"] else "respinto"
        f["votes"] = f"{len(yes)}/{len(ballots)}"
        f["why"] = "; ".join(w for w in (no if f["outcome"] == "respinto" else yes) if w)
    return findings


def stats(findings):
    imp = [f for f in findings if f["severity"] == "Importante"]
    return {"important": len(imp),
            "confirmed": sum(f.get("outcome") == "confermato" for f in imp),
            "rejected": sum(f.get("outcome") == "respinto" for f in imp),
            "nit": sum(f["severity"] == "Nit" for f in findings),
            "preexisting": sum(f["severity"] == "Preesistente" for f in findings)}


def render(findings, n):
    """round-N/findings.md: what the builder fixes, what it leaves, and why."""
    def items(fs, extra=lambda f: ""):
        return "\n".join(f"- **{f['id']}**{extra(f)} {f['text']}" for f in fs) or "Nessuno."

    imp = [f for f in findings if f["severity"] == "Importante"]
    keep = [f for f in imp if f.get("outcome") != "respinto"]
    drop = [f for f in imp if f.get("outcome") == "respinto"]

    def votes(f):
        return f" ({f['votes']})" if f.get("votes") else ""

    return (f"# Findings del giro {n}\n\n"
            "## Da correggere\n" + items(keep, votes) + "\n\n"
            "## Respinti dai verificatori\nNon correggerli e non ripresentarli.\n\n"
            + items(drop, lambda f: f" ({f['votes']}: {f.get('why') or 'non confermato'})") + "\n\n"
            "## Nit\nFacoltativi.\n\n" + items([f for f in findings if f["severity"] == "Nit"]) + "\n\n"
            "## Preesistenti\nNon sono di questo branch: l'approver li propone come task nuovi.\n\n"
            + items([f for f in findings if f["severity"] == "Preesistente"]) + "\n")
