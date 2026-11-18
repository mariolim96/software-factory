"""The factory home and its configuration.

A factory home is a folder outside the code repositories. It holds the Claude Code
setup (agents, the orchestrator skill, settings, the MCP server entry), the
project's `factory.json`, the gate's checks, a clone of each code repository, the
job worktrees and the job state. Nothing of the factory lives in a code
repository, so a branch can never change the checks it is judged by, and a
client's repository never receives factory files.

A project has one or more repositories, each with a key (`be`, `fe`) and a stack
profile. Per-repository settings (routing, reviewers, risky and protected paths)
live in `repos.<key>` and fall back to the top level of factory.json.
"""

import json
import os
from pathlib import Path

PER_REPO = ("routing", "surface_reviewers", "path_reviewers", "high_risk_paths", "protected_paths")


class ConfigError(Exception):
    pass


def find_home(start=None):
    env = os.environ.get("SF_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(start or Path.cwd()).resolve()
    for d in [here, *here.parents]:
        if (d / "factory.json").exists():
            return d
    raise ConfigError("no factory.json in this folder or above: run `sf init` or set SF_HOME")


def repos_of(cfg):
    """`repos` of factory.json; a single-repo home (`repo` + `profile`) reads as `repos.be`."""
    if "repos" in cfg:
        return cfg["repos"]
    r = cfg["repo"]
    return {"be": {"github": r["github"], "base_branch": r["base_branch"], "profile": cfg.get("profile"),
                   "path": "repo", "checks": "checks"}}


class Home:
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root else find_home()
        path = self.root / "factory.json"
        if not path.exists():
            raise ConfigError(f"{path} doesn't exist")
        self.cfg = json.loads(path.read_text(encoding="utf-8"))
        self.jobs = self.root / "jobs"
        self.worktrees = self.root / "worktrees"

    def get(self, key, default=None):
        return self.cfg.get(key, default)

    # ---- repositories

    def repos(self):
        return repos_of(self.cfg)

    def repo_keys(self):
        return list(self.repos())

    def has_repo(self, key):
        return key in self.repos()

    def rconf(self, key):
        """factory.json as seen by one repository: its own settings over the top level."""
        r = self.repos()[key]
        return {**self.cfg, **{k: r[k] for k in PER_REPO if k in r}}

    def repo_path(self, key):
        return self.root / self.repos()[key].get("path", f"repos/{key}")

    def checks_dir(self, key):
        return self.root / self.repos()[key].get("checks", f"checks/{key}")

    def base(self, key):
        """The integration branch the parent PRs of this repository merge into."""
        return self.repos()[key]["base_branch"]

    def profile(self, key):
        return self.repos()[key]["profile"]

    def profile_md(self, key):
        return self.root / ".claude" / "profiles" / f"{self.profile(key)}.md"

    # ---- references

    @property
    def refs_dir(self):
        return Path(self.cfg.get("refs_dir", "~/.cache/software-factory/refs")).expanduser()

    def ref_paths(self):
        """Read-only reference checkouts the agents may read (framework source, docs repos)."""
        return [self.refs_dir / f"{r['name']}@{r['ref']}" for r in self.cfg.get("references", [])]
