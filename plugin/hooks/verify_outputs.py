#!/usr/bin/env python3
"""Stop-hook self-verification for {{SIMULATOR_NAME}} authoring tasks.

Fires when the Claude Code agent ends its turn. Checks that
``/workspace/inputs/`` contains at least one output file and that every file
passes the simulator's validator (see ``plugin/adapters/validator.py``).
If any check fails, emits ``decision: "block"`` on stdout so Claude Code
re-enters the agent with the reason as feedback; otherwise allows the stop.

Environment knobs (all prefixed with ``{{ENV_PREFIX}}_HOOK_``):

  {{ENV_PREFIX}}_HOOK_INPUTS_DIR   Override the workspace inputs directory.
                                   Defaults to ``$CLAUDE_PROJECT_DIR/inputs``.
  {{ENV_PREFIX}}_HOOK_MAX_RETRIES  Max times this hook blocks before giving
                                   up. Default 2. Counter lives in
                                   ``<inputs-parent>/.verify_retry_count``.
  {{ENV_PREFIX}}_HOOK_DISABLE      If 1/true/yes, hook no-ops.
  {{ENV_PREFIX}}_HOOK_SCHEMA_PATH  Path to schema/spec for validate_file.
                                   Defaults to ``{{SCHEMA_PATH}}``.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))
from adapters.validator import validate_file, validate_directory, ValidationIssue  # noqa: E402

ENV_PREFIX = "{{ENV_PREFIX}}"
CONFIG_EXTS = {"{{CONFIG_EXT}}"}
DEFAULT_SCHEMA_PATH = Path("{{SCHEMA_PATH}}") if "{{SCHEMA_PATH}}" else None
OUTPUT_IS_DIRECTORY = False  # set true by init_template.py if config_format.output_is_directory

MAX_FILES_REPORTED = 4
MAX_ERRORS_PER_FILE = 8


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
    return parent / ".verify_hook_events.jsonl"


def _log_event(inputs_dir: Path, decision: str, category: str, retries: int, detail: str = "") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "category": category,
        "retries": retries,
        "detail": detail,
    }
    try:
        with _event_log_path(inputs_dir).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _allow(inputs_dir: Path, category: str = "allow", retries: int = 0) -> None:
    _log_event(inputs_dir, "allow", category, retries)
    json.dump({"continue": True, "suppressOutput": True}, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0)


def _block(reason: str, inputs_dir: Path, category: str, retries: int, detail: str = "") -> None:
    _log_event(inputs_dir, "block", category, retries, detail)
    json.dump({"decision": "block", "reason": reason}, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0)


def _bump_counter(inputs_dir: Path) -> int:
    parent = inputs_dir.parent if inputs_dir.parent.exists() else Path("/tmp")
    counter = parent / ".verify_retry_count"
    try:
        current = int(counter.read_text().strip() or "0")
    except (FileNotFoundError, ValueError):
        current = 0
    current += 1
    try:
        counter.write_text(str(current))
    except OSError:
        pass
    return current


def _list_outputs(inputs_dir: Path) -> list[Path]:
    if not inputs_dir.exists():
        return []
    if OUTPUT_IS_DIRECTORY:
        return [inputs_dir] if any(inputs_dir.iterdir()) else []
    return sorted(p for p in inputs_dir.rglob("*") if p.is_file() and p.suffix.lstrip(".") in CONFIG_EXTS)


def _format_issues(issues: list[ValidationIssue], inputs_dir: Path) -> str:
    by_file: dict[str, list[str]] = {}
    for iss in issues:
        try:
            key = str(iss.file.relative_to(inputs_dir)) if iss.file else "(global)"
        except ValueError:
            key = str(iss.file) if iss.file else "(global)"
        by_file.setdefault(key, []).append(iss.message + (f" — {iss.hint}" if iss.hint else ""))
    parts: list[str] = []
    for fname, msgs in list(by_file.items())[:MAX_FILES_REPORTED]:
        joined = "\n  ".join(msgs[:MAX_ERRORS_PER_FILE])
        parts.append(f"- {fname}:\n  {joined}")
    remaining = len(by_file) - MAX_FILES_REPORTED
    if remaining > 0:
        parts.append(f"- ...plus {remaining} more file(s) with issues.")
    return "\n".join(parts)


def main() -> None:
    inputs_dir = _inputs_dir()

    if _envflag(f"{ENV_PREFIX}_HOOK_DISABLE"):
        _allow(inputs_dir, category="disabled")

    try:
        json.load(sys.stdin)
    except json.JSONDecodeError:
        _allow(inputs_dir, category="bad_hook_input")

    max_retries = int(os.environ.get(f"{ENV_PREFIX}_HOOK_MAX_RETRIES", "2") or 2)
    outputs = _list_outputs(inputs_dir)

    if not outputs:
        retries = _bump_counter(inputs_dir)
        if retries > max_retries:
            _allow(inputs_dir, category="no_outputs_max_retries", retries=retries)
        _block(
            f"Stop blocked by verify_outputs hook: no output files found under "
            f"{inputs_dir}. This is a required output of the task. Produce the "
            f"requested {{CONFIG_LABEL}} now using the Write tool (write under "
            f"{inputs_dir}/) and then end your turn.",
            inputs_dir=inputs_dir, category="no_outputs", retries=retries,
        )

    schema_override = os.environ.get(f"{ENV_PREFIX}_HOOK_SCHEMA_PATH")
    schema = Path(schema_override) if schema_override else DEFAULT_SCHEMA_PATH

    issues: list[ValidationIssue] = []
    if OUTPUT_IS_DIRECTORY:
        issues = validate_directory(inputs_dir)
    else:
        for f in outputs:
            issues.extend(validate_file(f, schema))

    if issues:
        retries = _bump_counter(inputs_dir)
        if retries > max_retries:
            _allow(inputs_dir, category="validation_error_max_retries", retries=retries)
        feedback = _format_issues(issues, inputs_dir)
        _block(
            f"Stop blocked by verify_outputs hook: validation failed.\n\n{feedback}\n\n"
            "Fix the issues above and then end your turn.",
            inputs_dir=inputs_dir, category="validation_error", retries=retries,
            detail=feedback[:500],
        )

    _allow(inputs_dir, category="clean")


if __name__ == "__main__":
    main()
