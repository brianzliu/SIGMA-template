#!/usr/bin/env python3
"""PostToolUse hook: validate output right after Write|Edit|MultiEdit.

Catches structural / parse errors within seconds of the bad write instead of
waiting until end-of-turn. Without this the agent can spend many minutes
deliberating before discovering its own bug.

Behavior:
  - Only fires for Write/Edit/MultiEdit on files inside
    ``${{ENV_PREFIX}}_HOOK_INPUTS_DIR`` (default
    ``$CLAUDE_PROJECT_DIR/inputs`` or ``/workspace/inputs``).
  - Only checks files whose extension is in CONFIG_EXTS
    (set from configs/simulator.yaml::config_format.extensions).
  - Calls ``adapters.validator.validate_text`` for the fast in-process check.
  - Returns ``decision: "block"`` with the validator's issues as feedback.

Honors ``${{ENV_PREFIX}}_HOOK_DISABLE`` so the existing kill switch covers
both hooks.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))
from adapters.validator import validate_text  # noqa: E402

ENV_PREFIX = "{{ENV_PREFIX}}"
CONFIG_EXTS = {"{{CONFIG_EXT}}"}


def _envflag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _inputs_dir() -> Path:
    override = os.environ.get(f"{ENV_PREFIX}_HOOK_INPUTS_DIR")
    if override:
        return Path(override)
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        return Path(project) / "inputs"
    return Path("/workspace/inputs")


def _event_log_path(inputs_dir: Path) -> Path:
    parent = inputs_dir.parent if inputs_dir.parent.exists() else Path("/tmp")
    return parent / ".verify_post_hook_events.jsonl"


def _log_event(inputs_dir: Path, decision: str, detail: str) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "detail": detail,
    }
    try:
        with _event_log_path(inputs_dir).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _allow(inputs_dir: Path, detail: str = "") -> None:
    _log_event(inputs_dir, "allow", detail)
    json.dump({"continue": True, "suppressOutput": True}, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0)


def _block(reason: str, inputs_dir: Path, detail: str) -> None:
    _log_event(inputs_dir, "block", detail)
    json.dump({"decision": "block", "reason": reason}, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0)


def _collect_paths(payload: dict) -> list[Path]:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    out: list[Path] = []
    if tool in {"Write", "Edit", "MultiEdit"}:
        p = tool_input.get("file_path")
        if isinstance(p, str) and p:
            out.append(Path(p))
    return out


def main() -> None:
    inputs_dir = _inputs_dir()

    if _envflag(f"{ENV_PREFIX}_HOOK_DISABLE"):
        _allow(inputs_dir, "disabled")

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        _allow(inputs_dir, "bad_hook_input")

    paths = _collect_paths(payload)
    if not paths:
        _allow(inputs_dir, "non_write_tool")

    try:
        inputs_resolved = inputs_dir.resolve()
    except OSError:
        inputs_resolved = inputs_dir

    for raw in paths:
        try:
            p = raw.resolve()
        except OSError:
            continue
        if p.suffix.lstrip(".") not in CONFIG_EXTS:
            continue
        try:
            inside = p.is_relative_to(inputs_resolved)
        except (AttributeError, ValueError):
            inside = str(p).startswith(str(inputs_resolved))
        if not inside or not p.exists():
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        issues = validate_text(text, p)
        if issues:
            try:
                rel = p.relative_to(inputs_resolved)
            except (ValueError, AttributeError):
                rel = p
            msg = "; ".join(iss.message + (f" — {iss.hint}" if iss.hint else "") for iss in issues)
            _block(
                f"PostToolUse verify_post_write: {rel} failed validation: {msg}. "
                "Fix this file before continuing.",
                inputs_dir=inputs_dir,
                detail=f"{rel}: {msg}",
            )

    _allow(inputs_dir, "clean")


if __name__ == "__main__":
    main()
