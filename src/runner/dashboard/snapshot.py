"""Dashboard JSON snapshot + conversation-log builders."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agents import AGENTS
from ..tool_counts import _new_tool_counts


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def collect_dashboard_snapshot(
    run_name: str,
    agent_keys: list[str],
    task_names: list[str] | None = None,
    blocked_gt_by_task: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    expected_task_names = set(task_names or [])
    blocked_gt_by_task = blocked_gt_by_task or {}
    for agent_key in agent_keys:
        root = AGENTS[agent_key]["results_dir"] / run_name
        discovered_task_names = set(expected_task_names)
        if root.exists():
            discovered_task_names.update(path.name for path in root.iterdir() if path.is_dir())
        for task_name in sorted(discovered_task_names):
            task_dir = root / task_name
            metadata = _read_json(task_dir / "eval_metadata.json") or {}
            default_status = {
                "task": task_name,
                "agent": agent_key,
                "run_name": run_name,
                "status": "pending",
                "process_status": "pending",
                "latest_stdout": [],
                "latest_agent_response": "",
                "latest_stderr": [],
                "blocked_gt_xml_filenames": metadata.get(
                    "blocked_gt_xml_filenames",
                    blocked_gt_by_task.get(task_name, []),
                ),
                **_new_tool_counts(),
            }
            status = {**default_status, **(_read_json(task_dir / "status.json") or {})}
            status["task_dir"] = str(task_dir)
            if "blocked_gt_xml_filenames" not in status:
                status["blocked_gt_xml_filenames"] = default_status["blocked_gt_xml_filenames"]
            tasks.append(status)
    tasks.sort(key=lambda item: (str(item.get("agent", "")), str(item.get("task", ""))))
    return {
        "run_name": run_name,
        "updated": datetime.now().isoformat(),
        "tasks": tasks,
    }


def _conversation_label(record: dict[str, Any]) -> str:
    record_type = str(record.get("type") or "event")
    if record_type == "system":
        subtype = record.get("subtype")
        return f"system:{subtype}" if subtype else "system"
    message = record.get("message")
    if isinstance(message, dict) and message.get("role"):
        return str(message["role"])
    return record_type


def _conversation_text(record: dict[str, Any]) -> str:
    if record.get("type") == "system":
        summary = {
            key: record.get(key)
            for key in ("subtype", "cwd", "session_id", "model", "tools", "mcp_servers", "plugins")
            if key in record
        }
        return json.dumps(summary, indent=2, sort_keys=True)

    message = record.get("message")
    if not isinstance(message, dict):
        return json.dumps(record, indent=2, sort_keys=True)

    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            item_type = item.get("type")
            if item_type == "text":
                parts.append(str(item.get("text", "")))
            elif item_type == "thinking":
                parts.append("[thinking]\n" + str(item.get("thinking", "")))
            elif item_type == "redacted_thinking":
                parts.append("[redacted thinking]")
            elif item_type == "tool_use":
                tool_name = item.get("name", "tool")
                tool_input = item.get("input", {})
                parts.append(
                    f"[tool use] {tool_name}\n"
                    f"{json.dumps(tool_input, indent=2, sort_keys=True)}"
                )
            elif item_type == "tool_result":
                result = item.get("content", "")
                parts.append(f"[tool result] {item.get('tool_use_id', '')}\n{result}")
            else:
                parts.append(json.dumps(item, indent=2, sort_keys=True))

    if parts:
        return "\n\n".join(part for part in parts if part)
    return json.dumps(message, indent=2, sort_keys=True)


def collect_conversation_log(
    *,
    run_name: str,
    agent_keys: list[str],
    agent_key: str,
    task_name: str,
) -> dict[str, Any]:
    if agent_key not in agent_keys:
        return {"error": f"agent not in dashboard scope: {agent_key}", "entries": []}

    task_dir = AGENTS[agent_key]["results_dir"] / run_name / task_name
    try:
        task_dir.relative_to(AGENTS[agent_key]["results_dir"] / run_name)
    except ValueError:
        return {"error": "invalid task path", "entries": []}

    status = _read_json(task_dir / "status.json") or {}
    metadata = _read_json(task_dir / "eval_metadata.json") or {}
    blocked_gt_xml_filenames = (
        status.get("blocked_gt_xml_filenames")
        or metadata.get("blocked_gt_xml_filenames")
        or []
    )

    entries: list[dict[str, str]] = []
    events_path = task_dir / "events.jsonl"
    if events_path.exists():
        with events_path.open("r", encoding="utf-8", errors="replace") as events_file:
            for index, line in enumerate(events_file, 1):
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    entries.append({"label": f"stdout:{index}", "text": line})
                    continue
                entries.append(
                    {
                        "label": _conversation_label(record),
                        "text": _conversation_text(record),
                    }
                )
    else:
        for label, filename in (("stdout", "stdout.txt"), ("stderr", "stderr.txt")):
            path = task_dir / filename
            if path.exists():
                entries.append(
                    {
                        "label": label,
                        "text": path.read_text(encoding="utf-8", errors="replace"),
                    }
                )

    return {
        "task": task_name,
        "agent": agent_key,
        "task_dir": str(task_dir),
        "blocked_gt_xml_filenames": blocked_gt_xml_filenames,
        "entries": entries,
    }
