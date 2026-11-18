"""API-equivalent cost of a job's agents, read from Claude Code's transcripts.

Claude Code writes each subagent's messages, with model and token usage, to
<config>/projects/<project>/<session>/subagents/agent-<id>.jsonl. Every factory
prompt starts with the job header, which contains `Job folder: <path>/`: that
string ties a transcript to its job across sessions. The transcript format is
not a documented interface, so a Claude Code update can break this; the factory
keeps working, only the figure goes to zero.
"""

import json
import os
import re
from pathlib import Path


def transcripts_dir(home):
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    return config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(home.root))


def _prompt_text(line):
    content = json.loads(line)["message"]["content"]
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))


def cost_usd(home, job_dir):
    needle = f"Job folder: {job_dir}/"
    prices = home.get("prices_usd_per_mtok", {})
    total = 0.0
    for path in transcripts_dir(home).glob("*/subagents/agent-*.jsonl"):
        try:
            with path.open(encoding="utf-8") as f:
                first = f.readline()
                if not first or needle not in _prompt_text(first):
                    continue
                usage = {}
                for line in f:
                    entry = json.loads(line)
                    msg = entry.get("message")
                    if entry.get("type") == "assistant" and isinstance(msg, dict) and msg.get("usage"):
                        usage[msg["id"]] = (msg["model"], msg["usage"])
        except (OSError, ValueError, KeyError):
            continue
        for model, u in usage.values():
            p = prices.get(model)
            if not p:
                continue
            write_1h = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0)
            write_5m = u.get("cache_creation_input_tokens", 0) - write_1h
            total += (u.get("input_tokens", 0) * p["input"]
                      + u.get("output_tokens", 0) * p["output"]
                      + write_5m * p["cache_write_5m"]
                      + write_1h * p["cache_write_1h"]
                      + u.get("cache_read_input_tokens", 0) * p["cache_read"]) / 1e6
    return round(total, 2)
