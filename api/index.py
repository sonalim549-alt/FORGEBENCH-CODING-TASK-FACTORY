"""Vercel serverless entrypoint for the ForgeBench Evidence Explorer dashboard."""

from __future__ import annotations

import json
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from forgebench.dashboard import DASHBOARD_HTML, _index_tasks, _load_task_detail

TASKS_ROOT = (ROOT_DIR / "sample_tasks").resolve()


def _resolve_route(raw_path: str) -> str:
    """Normalize both direct paths and Vercel rewrite `?route=...` paths."""
    parsed = urllib.parse.urlparse(raw_path)
    path = parsed.path
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if path in ("/api/index", "/api/index.py", "/api") and "route" in query:
        route = query["route"][0]
        return "/" + route.lstrip("/") if route else "/"
    return path


class handler(BaseHTTPRequestHandler):
    """Vercel Python BaseHTTPRequestHandler serving the read-only Evidence Explorer."""

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'",
        )

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = _resolve_route(self.path)

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_security_headers()
            self.end_headers()
            return

        if path in ("/", "/index.html", "/api/index", "/api/index.py", "/api"):
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "ForgeBench Evidence Explorer",
                    "tasks_indexed": len(_index_tasks(TASKS_ROOT)),
                },
            )
            return

        if path == "/api/tasks":
            self._send_json(HTTPStatus.OK, {"tasks": _index_tasks(TASKS_ROOT)})
            return

        if path.startswith("/api/tasks/"):
            task_id = urllib.parse.unquote(path[len("/api/tasks/") :])
            detail = _load_task_detail(TASKS_ROOT, task_id)
            if detail is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Task not found"})
            else:
                self._send_json(HTTPStatus.OK, detail)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        return
