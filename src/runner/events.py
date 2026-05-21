"""Event-stream analysis, status classification, and per-attempt archiving."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .tool_counts import (
    _extract_mcp_server_statuses,
    _extract_text_fragments,
    _extract_tool_calls,
    _has_non_rag_pseudo_tool,
    _is_geos_primer_read,
    _new_tool_counts,
    _record_mcp_statuses,
    _record_primer_read,
    _record_pseudo_tool_invocations,
    _record_tool_call,
)


def classify_final_status(
    *,
    process_status: str,
    requires_rag: bool,
    counts: dict[str, Any],
) -> str:
    if process_status == "success" and _has_non_rag_pseudo_tool(counts):
        return "failed_pseudo_tool"
    if process_status == "success" and requires_rag and not bool(counts["rag_requirement_met"]):
        if counts.get("rag_mcp_unavailable") or counts.get("rag_pseudo_invocations"):
            return "failed_rag_unavailable"
        return "failed_no_rag"
    return process_status


def workspace_inputs_present(result_dir: Path, *, since: float | None = None) -> bool:
    inputs_dir = result_dir / "inputs"
    if not inputs_dir.exists():
        return False
    for path in inputs_dir.rglob("*"):
        if not path.is_file():
            continue
        if since is None or path.stat().st_mtime >= since:
            return True
    return False


def archive_native_attempt_outputs(result_dir: Path, attempt: int) -> None:
    archive_dir = result_dir / f"attempt_{attempt}"
    archive_dir.mkdir(exist_ok=True)
    for filename in (
        "status.json",
        "tool_calls.json",
        "events.jsonl",
        "acpx_output.json",
        "stdout.txt",
        "stderr.txt",
        "exit_code.txt",
    ):
        path = result_dir / filename
        if path.exists():
            path.replace(archive_dir / filename)


def analyze_event_stream_text(text: str) -> dict[str, Any]:
    counts = _new_tool_counts()
    stdout_tail: list[str] = []
    latest_agent_response = ""

    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                stdout_tail.append(line.strip())
            continue

        _record_mcp_statuses(counts, _extract_mcp_server_statuses(record))
        for tool_call in _extract_tool_calls(record):
            _record_tool_call(counts, str(tool_call["name"]))
            if _is_geos_primer_read(tool_call):
                _record_primer_read(counts)
        for fragment in _extract_text_fragments(record):
            fragment_text = fragment["text"]
            stdout_tail.append(fragment_text)
            _record_pseudo_tool_invocations(counts, fragment_text)
            if fragment.get("role") == "assistant":
                latest_agent_response = fragment_text

    return {
        "counts": counts,
        "latest_stdout": stdout_tail[-40:],
        "latest_agent_response": latest_agent_response,
    }
