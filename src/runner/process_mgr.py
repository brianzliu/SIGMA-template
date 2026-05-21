"""Process registry + global stop signal."""

from __future__ import annotations

import subprocess
import threading
import time

STOP_REQUESTED = threading.Event()
ACTIVE_PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES: dict[int, subprocess.Popen[str]] = {}


def _register_process(proc: subprocess.Popen[str]) -> None:
    with ACTIVE_PROCESS_LOCK:
        ACTIVE_PROCESSES[proc.pid] = proc


def _unregister_process(proc: subprocess.Popen[str]) -> None:
    with ACTIVE_PROCESS_LOCK:
        ACTIVE_PROCESSES.pop(proc.pid, None)


def stop_active_processes(*, grace_seconds: float = 5.0) -> None:
    STOP_REQUESTED.set()
    with ACTIVE_PROCESS_LOCK:
        processes = list(ACTIVE_PROCESSES.values())

    for proc in processes:
        if proc.poll() is None:
            proc.terminate()

    deadline = time.time() + grace_seconds
    for proc in processes:
        remaining = max(0.0, deadline - time.time())
        if proc.poll() is not None:
            continue
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            if proc.poll() is None:
                proc.kill()
