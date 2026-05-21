"""Tool-call extraction, counters, and primer-read detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import CONTAINER_GEOS_PRIMER_PATH, PSEUDO_TOOL_RE, RAG_TOOL_NAMES


def _tool_name_base(name: str) -> str:
    return name.rsplit("__", 1)[-1]


def _is_rag_tool(name: str) -> bool:
    base = _tool_name_base(name)
    return base in RAG_TOOL_NAMES or any(tool in name for tool in RAG_TOOL_NAMES)


def _is_repo3_plugin_tool(name: str) -> bool:
    return "repo3" in name or "geos-rag" in name or _is_rag_tool(name)


def _extract_tool_calls(value: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node_type = str(node.get("type", ""))
            name = node.get("name") or node.get("tool_name") or node.get("toolName")
            if isinstance(name, str) and "tool" in node_type.lower():
                calls.append({"name": name, "input": node.get("input") or {}})
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return calls


def _string_values(value: Any) -> list[str]:
    values: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, str):
            values.append(node)
        elif isinstance(node, dict):
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return values


def _is_geos_primer_read(tool_call: dict[str, Any]) -> bool:
    name = _tool_name_base(str(tool_call.get("name", ""))).lower()
    input_payload = tool_call.get("input") or {}
    strings = _string_values(input_payload)

    if name == "read":
        return any(Path(text).name == CONTAINER_GEOS_PRIMER_PATH.name for text in strings)

    if name == "bash":
        read_commands = ("cat", "sed", "head", "tail", "less", "more", "rg", "grep")
        return any(
            CONTAINER_GEOS_PRIMER_PATH.name in text
            and any(command in text for command in read_commands)
            for text in strings
        )

    return False


def _extract_text_fragments(value: Any) -> list[dict[str, str]]:
    fragments: list[dict[str, str]] = []

    def role_for(node: dict[str, Any]) -> str:
        cursor: Any = node
        while isinstance(cursor, dict):
            message = cursor.get("message")
            if isinstance(message, dict) and isinstance(message.get("role"), str):
                return message["role"]
            cursor = cursor.get("_parent")
        return ""

    def visit(node: Any, parent: dict[str, Any] | None = None) -> None:
        if isinstance(node, dict):
            if parent is not None:
                node = {**node, "_parent": parent}
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                text = node["text"].strip()
                if text:
                    fragments.append({"role": role_for(node), "text": text})
            elif node.get("type") == "thinking" and isinstance(node.get("thinking"), str):
                text = node["thinking"].strip()
                if text and _extract_pseudo_tool_invocations(text):
                    fragments.append({"role": "assistant_thinking", "text": text})
            elif isinstance(node.get("result"), str):
                text = node["result"].strip()
                if text:
                    fragments.append({"role": str(node.get("type") or "result"), "text": text})
            for key, child in node.items():
                if key != "_parent":
                    visit(child, node)
        elif isinstance(node, list):
            for child in node:
                visit(child, parent)

    visit(value)
    return fragments


def _extract_mcp_server_statuses(value: Any) -> dict[str, str]:
    statuses: dict[str, str] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            servers = node.get("mcp_servers")
            if isinstance(servers, list):
                for server in servers:
                    if not isinstance(server, dict):
                        continue
                    name = server.get("name")
                    status = server.get("status")
                    if isinstance(name, str) and isinstance(status, str):
                        statuses[name] = status
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return statuses


def _extract_pseudo_tool_invocations(text: str) -> list[str]:
    return [match.group(1) for match in PSEUDO_TOOL_RE.finditer(text)]


def _new_tool_counts() -> dict[str, Any]:
    return {
        "total_tool_calls": 0,
        "per_tool_counts": {},
        "plugin_tool_calls": 0,
        "rag_tool_calls": 0,
        "rag_tool_counts": {name: 0 for name in sorted(RAG_TOOL_NAMES)},
        "rag_requirement_met": False,
        "rag_mcp_unavailable": False,
        "rag_pseudo_invocations": 0,
        "pseudo_tool_calls": 0,
        "pseudo_tool_counts": {},
        "mcp_server_statuses": {},
        "primer_read": False,
        "primer_read_tool_calls": 0,
    }


def _record_tool_call(counts: dict[str, Any], tool_name: str) -> None:
    per_tool = counts["per_tool_counts"]
    per_tool[tool_name] = per_tool.get(tool_name, 0) + 1
    counts["total_tool_calls"] += 1

    if _is_repo3_plugin_tool(tool_name):
        counts["plugin_tool_calls"] += 1

    if _is_rag_tool(tool_name):
        base = _tool_name_base(tool_name)
        rag_tool_counts = counts["rag_tool_counts"]
        if base not in rag_tool_counts:
            rag_tool_counts[base] = 0
        rag_tool_counts[base] += 1
        counts["rag_tool_calls"] += 1
        counts["rag_requirement_met"] = True


def _record_primer_read(counts: dict[str, Any]) -> None:
    counts["primer_read"] = True
    counts["primer_read_tool_calls"] = counts.get("primer_read_tool_calls", 0) + 1


def _record_pseudo_tool_invocations(counts: dict[str, Any], text: str) -> None:
    pseudo_tool_counts = counts.setdefault("pseudo_tool_counts", {})
    for tool_name in _extract_pseudo_tool_invocations(text):
        pseudo_tool_counts[tool_name] = pseudo_tool_counts.get(tool_name, 0) + 1
        counts["pseudo_tool_calls"] = counts.get("pseudo_tool_calls", 0) + 1
        if _is_rag_tool(tool_name):
            counts["rag_pseudo_invocations"] = (
                counts.get("rag_pseudo_invocations", 0) + 1
            )


def _has_non_rag_pseudo_tool(counts: dict[str, Any]) -> bool:
    return any(
        not _is_rag_tool(str(tool_name))
        for tool_name in counts.get("pseudo_tool_counts", {})
    )


def _record_mcp_statuses(counts: dict[str, Any], statuses: dict[str, str]) -> None:
    if not statuses:
        return
    current = counts.setdefault("mcp_server_statuses", {})
    current.update(statuses)
    geos_status = statuses.get("geos-rag")
    if geos_status and geos_status.lower() not in {"running", "ready", "connected", "available"}:
        counts["rag_mcp_unavailable"] = True
