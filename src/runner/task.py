"""Native-Claude per-task runner."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .claude_settings import _safe_write_json
from .cost import compute_openrouter_cost, patch_events_openrouter_cost
from .events import (
    classify_final_status,
    workspace_inputs_present,
)
from .process_mgr import (
    STOP_REQUESTED,
    _register_process,
    _unregister_process,
)
from .tool_counts import (
    _extract_mcp_server_statuses,
    _extract_text_fragments,
    _extract_tool_calls,
    _is_geos_primer_read,
    _new_tool_counts,
    _record_mcp_statuses,
    _record_primer_read,
    _record_pseudo_tool_invocations,
    _record_tool_call,
)


def run_claude_native_task(
    *,
    task_name: str,
    agent_key: str,
    run_name: str,
    cmd: list[str],
    docker_env: dict[str, str],
    result_dir: Path,
    timeout: int,
    requires_rag: bool,
    primer_in_system_prompt: bool,
) -> dict[str, Any]:
    status_path = result_dir / "status.json"
    tool_counts_path = result_dir / "tool_calls.json"
    events_path = result_dir / "events.jsonl"
    compatibility_output_path = result_dir / "acpx_output.json"
    stdout_text_path = result_dir / "stdout.txt"
    stderr_path = result_dir / "stderr.txt"
    exit_code_path = result_dir / "exit_code.txt"

    counts = _new_tool_counts()
    started = time.time()
    stdout_tail: list[str] = []
    stderr_tail: list[str] = []
    latest_agent_response = ""
    lock = threading.Lock()

    state: dict[str, Any] = {
        "task": task_name,
        "agent": agent_key,
        "run_name": run_name,
        "status": "running",
        "process_status": "running",
        "exit_code": None,
        "started": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "elapsed_seconds": 0.0,
        "latest_stdout": [],
        "latest_agent_response": "",
        "latest_stderr": [],
        "primer_in_system_prompt": primer_in_system_prompt,
        **counts,
    }

    def flush_status() -> None:
        with lock:
            state.update(counts)
            state["updated"] = datetime.now().isoformat()
            state["elapsed_seconds"] = round(time.time() - started, 1)
            state["latest_stdout"] = stdout_tail[-40:]
            state["latest_agent_response"] = latest_agent_response
            state["latest_stderr"] = stderr_tail[-40:]
            _safe_write_json(status_path, state)
            _safe_write_json(tool_counts_path, counts)

    flush_status()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=docker_env,
    )
    _register_process(proc)

    def read_stdout() -> None:
        nonlocal latest_agent_response
        assert proc.stdout is not None
        with (
            events_path.open("w", encoding="utf-8") as events_file,
            compatibility_output_path.open("w", encoding="utf-8") as compat_file,
            stdout_text_path.open("w", encoding="utf-8") as stdout_file,
        ):
            for line in proc.stdout:
                events_file.write(line)
                events_file.flush()
                compat_file.write(line)
                compat_file.flush()
                stdout_file.write(line)
                stdout_file.flush()

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    fragments = [{"role": "stdout", "text": line.strip()}] if line.strip() else []
                    tool_calls: list[dict[str, Any]] = []
                    mcp_statuses: dict[str, str] = {}
                else:
                    fragments = _extract_text_fragments(record)
                    tool_calls = _extract_tool_calls(record)
                    mcp_statuses = _extract_mcp_server_statuses(record)

                with lock:
                    _record_mcp_statuses(counts, mcp_statuses)
                    for tool_call in tool_calls:
                        _record_tool_call(counts, str(tool_call["name"]))
                        if _is_geos_primer_read(tool_call):
                            _record_primer_read(counts)
                    for fragment in fragments:
                        text = fragment["text"]
                        stdout_tail.append(text)
                        _record_pseudo_tool_invocations(counts, text)
                        if fragment.get("role") == "assistant":
                            latest_agent_response = text
                    if len(stdout_tail) > 100:
                        del stdout_tail[:-100]
                flush_status()

    def read_stderr() -> None:
        assert proc.stderr is not None
        with stderr_path.open("w", encoding="utf-8") as stderr_file:
            for line in proc.stderr:
                stderr_file.write(line)
                stderr_file.flush()
                text = line.strip()
                if text:
                    with lock:
                        stderr_tail.append(text)
                        if len(stderr_tail) > 100:
                            del stderr_tail[:-100]
                    flush_status()

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    heartbeat_stop = threading.Event()

    def heartbeat_status() -> None:
        while not heartbeat_stop.wait(2.0):
            if proc.poll() is not None:
                break
            flush_status()

    heartbeat_thread = threading.Thread(target=heartbeat_status, daemon=True)
    heartbeat_thread.start()

    try:
        return_code = proc.wait(timeout=timeout)
        if STOP_REQUESTED.is_set() and return_code != 0:
            process_status = "interrupted"
        else:
            process_status = "success" if return_code == 0 else "failed"
    except subprocess.TimeoutExpired:
        proc.kill()
        return_code = None
        process_status = "timeout"
    finally:
        heartbeat_stop.set()
        _unregister_process(proc)
        heartbeat_thread.join(timeout=5)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

    rag_requirement_met = bool(counts["rag_requirement_met"])
    status = classify_final_status(
        process_status=process_status,
        requires_rag=requires_rag,
        counts=counts,
    )
    has_workspace_inputs = workspace_inputs_present(result_dir, since=started)
    if status == "success" and not has_workspace_inputs:
        status = "failed_no_outputs"

    exit_code_path.write_text("timeout" if return_code is None else str(return_code))
    with lock:
        state["status"] = status
        state["process_status"] = process_status
        state["exit_code"] = return_code
        state["rag_requirement_met"] = rag_requirement_met
        state["workspace_inputs_present"] = has_workspace_inputs
    flush_status()

    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    openrouter_cost = compute_openrouter_cost(events_path, openrouter_api_key)
    if openrouter_cost is not None:
        with lock:
            state["openrouter_cost_usd"] = openrouter_cost
        _safe_write_json(status_path, state)
        patch_events_openrouter_cost(events_path, openrouter_cost)

    result: dict[str, Any] = {
        "task": task_name,
        "agent": agent_key,
        "status": status,
        "process_status": process_status,
        "exit_code": return_code,
        "rag_requirement_met": rag_requirement_met,
        "primer_read": bool(counts.get("primer_read")),
        "primer_read_tool_calls": counts.get("primer_read_tool_calls", 0),
        "primer_in_system_prompt": primer_in_system_prompt,
        "workspace_inputs_present": has_workspace_inputs,
        "total_tool_calls": counts["total_tool_calls"],
        "per_tool_counts": counts["per_tool_counts"],
        "pseudo_tool_calls": counts.get("pseudo_tool_calls", 0),
        "pseudo_tool_counts": counts.get("pseudo_tool_counts", {}),
    }
    if openrouter_cost is not None:
        result["openrouter_cost_usd"] = openrouter_cost
    return result
