"""PID-file lock so two harness invocations can't share a --run name.

The April 2026 run9 incident was caused by a second `run_experiment.py` being
launched against an already-running --run; the original 12 in-flight tasks
got SIGTERMed and the second invocation silently took over. This lock makes
that case loud instead.

Lockfile lives at ``<results_root_dir>/.run_locks/<run_name>.lock`` and holds
a JSON record of pid/ppid/started/command. ``acquire_run_lock`` is a context
manager: on entry it errors out if a live PID is found, on exit it removes
its own lockfile.
"""
from __future__ import annotations

import errno
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator


def _is_pid_alive(pid: int) -> bool:
    """True iff ``pid`` is a process we can signal (kill 0)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but is owned by someone else — still "alive" for our purposes.
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


class RunLockHeld(RuntimeError):
    """Raised when another live process already holds the run lock."""


@contextmanager
def acquire_run_lock(
    results_root_dir: Path,
    run_name: str,
    command: list[str],
    *,
    force: bool = False,
) -> Iterator[Path]:
    lock_dir = results_root_dir / ".run_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{run_name}.lock"

    if lock_path.exists():
        existing_pid = 0
        existing: dict | None = None
        try:
            existing = json.loads(lock_path.read_text())
            existing_pid = int(existing.get("pid", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            existing = None

        alive = _is_pid_alive(existing_pid) if existing_pid else False
        if alive and not force:
            cmd_preview = (existing.get("command", "?") if existing else "?")[:120]
            raise RunLockHeld(
                f"Run '{run_name}' is already locked by PID {existing_pid} "
                f"(started {existing.get('started') if existing else '?'}, "
                f"host {existing.get('hostname') if existing else '?'}, "
                f"user {existing.get('user') if existing else '?'}, "
                f"command: {cmd_preview}). Lockfile: {lock_path}. "
                f"Wait for it to finish, or pass --force-unlock if the lock "
                f"is stale."
            )
        # Stale or force-overridden — replace.
        lock_path.unlink(missing_ok=True)

    payload = {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "started": datetime.now().isoformat(),
        "command": " ".join(command),
        "hostname": os.uname().nodename,
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "?",
    }
    lock_path.write_text(json.dumps(payload, indent=2))

    try:
        yield lock_path
    finally:
        # Only remove if the lock still belongs to us — guards against the
        # rare case where a forced relaunch overwrote our lockfile.
        try:
            current = json.loads(lock_path.read_text())
            if int(current.get("pid", 0)) == os.getpid():
                lock_path.unlink(missing_ok=True)
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            pass
