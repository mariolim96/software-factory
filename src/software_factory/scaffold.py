"""`sf init` and `sf add-repo`: create or update a factory home. `sf refs`: sync the read-only references."""

import json
import os
import shutil
import stat
from pathlib import Path

from .config import PER_REPO, repos_of
from .util import git, require, run

TEMPLATES = Path(__file__).parent / "templates"


def profiles():
    return sorted(p.name for p in (TEMPLATES / "profiles").iterdir() if p.is_dir())


def _profile_dir(name):
    d = TEMPLATES / "profiles" / name
    require(d.is_dir(), f"no profile {name!r}; available: {', '.join(profiles())}")
    return d


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, cfg):
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_managed(root, cfg):
    """The files the library owns, rewritten on every `sf init` so upgrading the library updates them:
    agents (core, then each profile's, which may override), the skill, the profile notes."""
    core = TEMPLATES / "core"
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    names = []
    for r in repos_of(cfg).values():
        if r["profile"] not in names:
            names.append(r["profile"])
    sources = sorted((core / "agents").glob("*.md"))
    for name in names:
        sources += sorted((_profile_dir(name) / "agents").glob("*.md"))
    for src in sources:
        shutil.copyfile(src, agents / src.name)
    skill = root / ".claude" / "skills" / "factory"
    skill.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(core / "SKILL.md", skill / "SKILL.md")
    profiles_dir = root / ".claude" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(_profile_dir(name) / "profile.md", profiles_dir / f"{name}.md")
    return f"agenti, skill e profili aggiornati ({', '.join(names)})"


def _repo_entry(profile, repo, base, extra=None):
    prof = _read(_profile_dir(profile) / "factory.json")
    entry = {"github": repo, "base_branch": base, "profile": profile}
    entry.update({k: prof[k] for k in PER_REPO if k in prof})
    entry.update(extra or {})
    return entry, {k: v for k, v in prof.items() if k not in PER_REPO}


def init(target, profile, repo, base):
    root = Path(target).expanduser().resolve()
    core = TEMPLATES / "core"
    root.mkdir(parents=True, exist_ok=True)
    done = []

    cfg_path = root / "factory.json"
    if not cfg_path.exists():
        require(profile and repo, "--profile and --repo OWNER/NAME are required the first time")
        cfg = _read(core / "factory.json")
        entry, rest = _repo_entry(profile, repo, base)
        cfg.update(rest)
        cfg["repos"] = {"be": entry}
        _write(cfg_path, cfg)
        done.append("factory.json creato: completa la sezione odoo")
    cfg = _read(cfg_path)
    done.append(sync_managed(root, cfg))

    settings = root / ".claude" / "settings.json"
    if not settings.exists():
        refs = str(Path(cfg.get("refs_dir", "~/.cache/software-factory/refs")).expanduser())
        creds = str(Path(cfg["odoo"]["credentials_file"]).expanduser())
        text = (core / "settings.json").read_text(encoding="utf-8")
        settings.write_text(text.replace("{{REFS}}", refs).replace("{{CREDENTIALS}}", creds), encoding="utf-8")
        done.append(".claude/settings.json creato")

    mcp = root / ".mcp.json"
    if not mcp.exists():
        mcp.write_text(json.dumps({"mcpServers": {"odoo": {
            "command": "sf", "args": ["mcp"], "env": {"SF_HOME": str(root)}}}}, indent=2) + "\n")
        done.append(".mcp.json creato")

    for src, dst in (("PROJECT.md", "PROJECT.md"), ("gitignore", ".gitignore")):
        if not (root / dst).exists():
            shutil.copyfile(core / src, root / dst)

    for key, r in repos_of(cfg).items():
        done += _materialize(root, key, r)
    for d in ("jobs", "worktrees"):
        (root / d).mkdir(exist_ok=True)
    return root, done


def _materialize(root, key, r):
    """The repository's checks (copied once from its profile) and its clone."""
    done = []
    checks = root / r.get("checks", f"checks/{key}")
    if not checks.exists():
        shutil.copytree(_profile_dir(r["profile"]) / "checks", checks)
        done.append(f"{checks.relative_to(root)}/ creato dal profilo {r['profile']}: adattalo al progetto")
    clone = root / r.get("path", f"repos/{key}")
    if not clone.exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", f"https://github.com/{r['github']}.git", str(clone)], cwd=root)
        done.append(f"clonato {r['github']} in {clone.relative_to(root)}/")
    return done


def add_repo(root, key, profile, repo, base):
    cfg_path = Path(root) / "factory.json"
    cfg = _read(cfg_path)
    if "repos" not in cfg:  # single-repo home: make its implicit `be` explicit
        cfg["repos"] = repos_of(cfg)
        cfg.pop("repo", None)
        cfg.pop("profile", None)
    require(key not in cfg["repos"], f"factory.json already has a repository {key!r}")
    entry, _ = _repo_entry(profile, repo, base)
    cfg["repos"][key] = entry
    _write(cfg_path, cfg)
    return [f"repo {key} ({repo}, profilo {profile}) aggiunto a factory.json", sync_managed(Path(root), cfg),
            *_materialize(Path(root), key, entry)]


def _writable(path, on):
    for dirpath, dirnames, filenames in os.walk(path):
        for name in [*dirnames, *filenames, ""]:
            p = Path(dirpath, name)
            if p.is_symlink():
                continue
            mode = p.stat().st_mode
            p.chmod(mode | stat.S_IWUSR if on else mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def sync_refs(home):
    """Shallow clones of the references, made read-only on disk."""
    out = []
    for r, path in zip(home.get("references", []), home.ref_paths()):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            _writable(path, True)
            git(path, "fetch", "--depth", "1", "origin", r["ref"])
            git(path, "reset", "--hard", "FETCH_HEAD")
        else:
            res = run(["git", "clone", "--depth", "1", "--branch", r["ref"], r["url"], str(path)],
                      cwd=path.parent, check=False)
            if res.returncode != 0:
                out.append(f"{r['name']}@{r['ref']}: clone fallito: {res.stderr.strip().splitlines()[-1:]}")
                continue
        _writable(path, False)
        out.append(f"{r['name']}@{r['ref']}: {path} (sola lettura)")
    return out
