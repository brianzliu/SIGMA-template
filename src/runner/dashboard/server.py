"""HTTP server for the live dashboard."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import dashboard_html
from .snapshot import collect_conversation_log, collect_dashboard_snapshot


def start_dashboard_server(
    *,
    run_name: str,
    agent_keys: list[str],
    task_names: list[str],
    blocked_gt_by_task: dict[str, list[str]],
    host: str,
    port: int,
) -> tuple[ThreadingHTTPServer, str]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                payload = collect_dashboard_snapshot(
                    run_name,
                    agent_keys,
                    task_names,
                    blocked_gt_by_task,
                )
                self._send_json(200, payload)
                return

            if parsed.path == "/api/conversation":
                query = parse_qs(parsed.query)
                payload = collect_conversation_log(
                    run_name=run_name,
                    agent_keys=agent_keys,
                    agent_key=query.get("agent", [""])[0],
                    task_name=query.get("task", [""])[0],
                )
                self._send_json(200 if "error" not in payload else 400, payload)
                return

            body = dashboard_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    candidates = [port] if port == 0 else list(range(port, port + 20))
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            server = ThreadingHTTPServer((host, candidate), DashboardHandler)
            break
        except OSError as exc:
            last_error = exc
    else:
        raise RuntimeError(f"Could not start dashboard server: {last_error}")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    actual_host, actual_port = server.server_address
    display_host = "127.0.0.1" if actual_host in ("0.0.0.0", "::") else actual_host
    return server, f"http://{display_host}:{actual_port}"
