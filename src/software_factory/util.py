import json
import re
import subprocess
from datetime import datetime, timezone


class Refused(Exception):
    """A move the factory won't make. The message says why."""


def require(cond, msg):
    if not cond:
        raise Refused(msg)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd, cwd, check=True, input=None, env=None):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=input, env=env)
    if check and res.returncode != 0:
        raise Refused(f"`{' '.join(map(str, cmd))}` failed: {(res.stderr or res.stdout).strip()}")
    return res


def git(cwd, *args, check=True):
    return run(["git", *args], cwd=cwd, check=check)


def gh(cwd, *args):
    return run(["gh", *args], cwd=cwd).stdout


def gh_json(cwd, *args):
    return json.loads(gh(cwd, *args))


def first_line(path):
    return path.read_text(encoding="utf-8").split("\n", 1)[0].strip() if path.exists() else None


def header(path):
    """KEY: value lines at the top of an agent's file, up to the first blank line."""
    fields = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            break
        key, _, value = line.partition(":")
        fields[key.strip().upper()] = value.strip()
    return fields


def section(path, heading):
    """Body of a `## heading` section, or None."""
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)",
                  path.read_text(encoding="utf-8"), re.M | re.S)
    return m.group(1).strip() if m else None


def slugify(title, words=4):
    return "-".join(re.findall(r"[a-z0-9]+", title.lower())[:words]) or "task"
