"""SIGINT/SIGTERM logger for the harness.

Background: in run9 (2026-04-28) twelve in-flight tasks were killed by an
external signal at 01:02:02 and we couldn't tell from the artifacts who
sent it. This module installs handlers that record signal context (pid,
ppid, parent's cmdline, count) to a JSONL log next to the run, then raises
``KeyboardInterrupt`` so the existing cleanup path in ``cli.main`` runs.
"""
from __future__ import annotations

import json
import os
import signal
from datetime import datetime
from pathlib import Path


def _proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return "?"
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip() or "?"


def install_signal_logger(log_path: Path) -> None:
    """Wrap SIGINT and SIGTERM so each delivery is recorded before unwinding.

    Multiple signals are appended to the JSONL file with an incrementing
    count so a forceful 3x-kill leaves a clear trail. Both signals raise
    ``KeyboardInterrupt`` to reuse the harness's existing cleanup branch.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    counter = {"n": 0}

    def _handler(signum: int, _frame) -> None:
        counter["n"] += 1
        try:
            ppid = os.getppid()
            payload = {
                "timestamp": datetime.now().isoformat(),
                "signal": signal.Signals(signum).name,
                "signum": int(signum),
                "count": counter["n"],
                "our_pid": os.getpid(),
                "our_ppid": ppid,
                "our_pgid": os.getpgrp(),
                "our_sid": os.getsid(0),
                "parent_cmdline": _proc_cmdline(ppid),
            }
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
        except Exception:
            # Logging must never block signal delivery.
            pass
        raise KeyboardInterrupt(f"received {signal.Signals(signum).name}")

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
