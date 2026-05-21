"""OpenRouter cost helpers."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

_OPENROUTER_GEN_ID_RE = re.compile(r"^gen-\w+")


def _fetch_openrouter_generation_cost(gen_id: str, api_key: str) -> float | None:
    """Query OpenRouter's /api/v1/generation endpoint for a single generation's cost."""
    url = f"https://openrouter.ai/api/v1/generation?id={gen_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return float(data["data"]["total_cost"])
    except (urllib.error.URLError, KeyError, TypeError, ValueError):
        return None


def compute_openrouter_cost(events_path: Path, api_key: str) -> float | None:
    """Parse events.jsonl, collect unique OpenRouter generation IDs, and sum costs."""
    if not events_path.exists() or not api_key:
        return None

    gen_ids: dict[str, None] = {}  # ordered set
    with events_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = record.get("message")
            if isinstance(msg, dict):
                msg_id = msg.get("id", "")
                if isinstance(msg_id, str) and _OPENROUTER_GEN_ID_RE.match(msg_id):
                    gen_ids[msg_id] = None

    if not gen_ids:
        return None

    total = 0.0
    found_any = False
    for gen_id in gen_ids:
        cost = _fetch_openrouter_generation_cost(gen_id, api_key)
        if cost is not None:
            total += cost
            found_any = True

    return total if found_any else None


def patch_events_openrouter_cost(events_path: Path, openrouter_cost: float) -> None:
    """Overwrite the total_cost_usd in the result record of events.jsonl with OpenRouter cost."""
    if not events_path.exists():
        return
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)
    patched = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            patched.append(line)
            continue
        if record.get("type") == "result":
            record["cc_cost_usd"] = record.get("total_cost_usd")
            record["total_cost_usd"] = openrouter_cost
            record["openrouter_cost_usd"] = openrouter_cost
            patched.append(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            patched.append(line)
    events_path.write_text("".join(patched), encoding="utf-8")
