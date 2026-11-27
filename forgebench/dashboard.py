from __future__ import annotations

import json
import tomllib
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from forgebench.security import ensure_within_root

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ForgeBench — Coding Task Factory Evidence Explorer</title>
  <style>
    :root {
      --bg: #0f172a; --panel: #1e293b; --border: #334155;
      --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
      --ok: #22c55e; --bad: #ef4444; --warn: #f59e0b;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: var(--bg); color: var(--text); }
    header { padding: 16px 24px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
    header h1 { margin: 0; font-size: 18px; letter-spacing: 0.3px; }
    header span { font-size: 13px; color: var(--muted); }
    main { display: grid; grid-template-columns: 320px 1fr; min-height: calc(100vh - 58px); }
    aside { border-right: 1px solid var(--border); padding: 16px; overflow-y: auto; }
    section { padding: 24px; overflow-y: auto; }
    .task-item { padding: 12px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 10px; cursor: pointer; background: var(--panel); }
    .task-item:hover, .task-item.active { border-color: var(--accent); }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
    .badge.ok { background: rgba(34,197,94,0.18); color: var(--ok); }
    .badge.bad { background: rgba(239,68,68,0.18); color: var(--bad); }
    .badge.info { background: rgba(56,189,248,0.16); color: var(--accent); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 16px 0; }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
    .card h3 { margin: 0 0 6px; font-size: 13px; color: var(--muted); }
    .tabs { display: flex; gap: 8px; margin: 16px 0 10px; }
    .tab { background: var(--panel); border: 1px solid var(--border); color: var(--text); padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
    .tab.active { border-color: var(--accent); color: var(--accent); }
    pre { background: #020617; border: 1px solid var(--border); border-radius: 8px; padding: 14px; overflow-x: auto; font-size: 12.5px; line-height: 1.5; white-space: pre-wrap; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--border); padding: 8px 10px; text-align: left; }
    th { color: var(--muted); font-weight: 600; }
  </style>
</head>
<body>
  <header>
    <h1>ForgeBench — Coding Task Factory</h1>
    <span id="summaryMeta">Loading certified task packages...</span>
  </header>
  <main>
    <aside id="taskList"></aside>
    <section id="detailPane"><p style="color:var(--muted)">Select a generated task package to inspect requirements, patches, and 4-Gate validation evidence.</p></section>
  </main>
  <script>
    let allTasks = [];
    let activeTask = null;
    let activeTab = "spec";

    function el(tag, attrs, text) {
      const node = document.createElement(tag);
      if (attrs) Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
      if (text !== undefined) node.textContent = text;
      return node;
    }

    async function loadTasks() {
      const res = await fetch("/api/tasks");
      const data = await res.json();
      allTasks = data.tasks || [];
      document.getElementById("summaryMeta").textContent =
        `${allTasks.length} package(s) indexed`;
      renderList();
      if (allTasks.length > 0) selectTask(allTasks[0].task_id);
    }

    function renderList() {
      const list = document.getElementById("taskList");
      list.textContent = "";
      allTasks.forEach(t => {
        const div = el("div", { class: "task-item" + (activeTask && activeTask.meta.task_id === t.task_id ? " active" : "") });
        div.onclick = () => selectTask(t.task_id);
        div.appendChild(el("div", { style: "font-weight:600;margin-bottom:6px;font-size:14px" }, t.title || t.task_id));
        const row = el("div", { style: "display:flex;gap:6px;flex-wrap:wrap" });
        row.appendChild(el("span", { class: "badge " + (t.certified ? "ok" : "bad") }, t.certified ? "CERTIFIED" : "REJECTED"));
        row.appendChild(el("span", { class: "badge info" }, t.category || "feature"));
        div.appendChild(row);
        list.appendChild(div);
      });
    }

    async function selectTask(taskId) {
      const res = await fetch("/api/tasks/" + encodeURIComponent(taskId));
      activeTask = await res.json();
      renderList();
      renderDetail();
    }

    function renderDetail() {
      const pane = document.getElementById("detailPane");
      pane.textContent = "";
      if (!activeTask) return;
      const { meta, report, problem_statement, reference_patch, feature_tests_patch } = activeTask;

      pane.appendChild(el("h2", { style: "margin:0 0 6px" }, meta.title));
      pane.appendChild(el("div", { style: "color:var(--muted);font-size:13px;margin-bottom:14px" },
        `Task ID: ${meta.task_id}  |  Repo: ${meta.repository.name} @ ${meta.repository.base_commit.slice(0, 12)}  |  Strategy: ${meta.generator_strategy}`));

      if (report) {
        const grid = el("div", { class: "grid" });
        report.gates.forEach(g => {
          const card = el("div", { class: "card" });
          card.appendChild(el("h3", null, g.name));
          card.appendChild(el("span", { class: "badge " + (g.passed ? "ok" : "bad") }, g.passed ? "PASS" : "FAIL"));
          card.appendChild(el("p", { style: "font-size:12.5px;margin:8px 0 0;color:var(--text)" }, g.summary));
          grid.appendChild(card);
        });
        pane.appendChild(grid);
      }

      const tabs = el("div", { class: "tabs" });
      [
        ["spec", "Problem Statement"],
        ["ref", "Reference Patch"],
        ["tests", "Feature Tests Patch"],
        ["mutants", "Mutation Matrix"],
      ].forEach(([key, label]) => {
        const btn = el("button", { class: "tab" + (activeTab === key ? " active" : "") }, label);
        btn.onclick = () => { activeTab = key; renderDetail(); };
        tabs.appendChild(btn);
      });
      pane.appendChild(tabs);

      if (activeTab === "spec") {
        pane.appendChild(el("pre", null, problem_statement || ""));
      } else if (activeTab === "ref") {
        pane.appendChild(el("pre", null, reference_patch || ""));
      } else if (activeTab === "tests") {
        pane.appendChild(el("pre", null, feature_tests_patch || ""));
      } else if (activeTab === "mutants" && report) {
        const tbl = el("table");
        const thead = el("thead");
        const hr = el("tr");
        ["Mutant", "File", "Line", "Operator", "Description", "Result"].forEach(h => hr.appendChild(el("th", null, h)));
        thead.appendChild(hr);
        tbl.appendChild(thead);
        const tbody = el("tbody");
        (report.mutations || []).forEach(m => {
          const tr = el("tr");
          tr.appendChild(el("td", null, m.mutant_id));
          tr.appendChild(el("td", null, m.file_path));
          tr.appendChild(el("td", null, String(m.line_number)));
          tr.appendChild(el("td", null, m.operator));
          tr.appendChild(el("td", null, m.description));
          const td = el("td");
          td.appendChild(el("span", { class: "badge " + (m.killed ? "ok" : "bad") }, m.killed ? "KILLED" : "SURVIVED"));
          tr.appendChild(td);
          tbody.appendChild(tr);
        });
        tbl.appendChild(tbody);
        pane.appendChild(tbl);
      }
    }

    loadTasks();
  </script>
</body>
</html>
"""


def _index_tasks(tasks_root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not tasks_root.is_dir():
        return items

    seen: set[str] = set()
    # Index Harbor v0.25.x task directories (`task.toml`)
    for toml_file in sorted(tasks_root.glob("*/task.toml")):
        try:
            data = tomllib.loads(toml_file.read_text(encoding="utf-8"))
            md = data.get("metadata", {})
            tid = md.get("task_id", toml_file.parent.name)
            ev_file = tasks_root / "_validation_evidence" / f"{tid}.validation.json"
            certified = True
            if ev_file.is_file():
                ev = json.loads(ev_file.read_text(encoding="utf-8"))
                certified = bool(ev.get("certified", False))
            items.append(
                {
                    "task_id": tid,
                    "title": md.get("title", tid),
                    "category": md.get("category", "feature-development"),
                    "difficulty": md.get("difficulty", "medium"),
                    "certified": certified,
                }
            )
            seen.add(tid)
        except Exception:
            continue

    # Also index Standard ForgeBench directories (`task.json`)
    for meta_file in sorted(tasks_root.glob("*/task.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            tid = meta["task_id"]
            if tid in seen:
                continue
            items.append(
                {
                    "task_id": tid,
                    "title": meta.get("title", tid),
                    "category": meta.get("category", "feature"),
                    "difficulty": meta.get("difficulty", "medium"),
                    "certified": bool(meta.get("certified", False)),
                }
            )
        except Exception:
            continue
    return items


def _load_task_detail(tasks_root: Path, task_id: str) -> dict[str, Any] | None:
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        return None
    task_dir = ensure_within_root(tasks_root, tasks_root / task_id)

    def _read(rel: str) -> str:
        p = task_dir / rel
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

    # Harbor v0.25.x layout
    toml_file = task_dir / "task.toml"
    if toml_file.is_file():
        data = tomllib.loads(toml_file.read_text(encoding="utf-8"))
        md = data.get("metadata", {})
        ev_file = tasks_root / "_validation_evidence" / f"{task_id}.validation.json"
        report = (
            json.loads(ev_file.read_text(encoding="utf-8"))
            if ev_file.is_file()
            else None
        )
        test_files_content = "\n\n".join(
            f"# --- {p.name} ---\n{p.read_text(encoding='utf-8', errors='replace')}"
            for p in sorted((task_dir / "tests").glob("test_feature_*.py"))
        )
        return {
            "meta": {
                "task_id": md.get("task_id", task_id),
                "title": md.get("title", task_id),
                "category": md.get("category", "feature-development"),
                "generator_strategy": md.get("generator_strategy", "harbor-v0.25"),
                "repository": {
                    "name": md.get("repository", "unknown"),
                    "base_commit": md.get("base_commit", "unknown"),
                },
            },
            "report": report,
            "problem_statement": _read("instruction.md"),
            "reference_patch": _read("solution/reference.patch"),
            "feature_tests_patch": test_files_content or _read("tests/test.sh"),
        }

    # Standard ForgeBench layout
    meta_file = task_dir / "task.json"
    if not meta_file.is_file():
        return None

    report_path = task_dir / "validation_report.json"
    report = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.is_file()
        else None
    )
    return {
        "meta": json.loads(meta_file.read_text(encoding="utf-8")),
        "report": report,
        "problem_statement": _read("problem_statement.md"),
        "reference_patch": _read("reference.patch"),
        "feature_tests_patch": _read("feature_tests.patch"),
    }


def create_dashboard_server(
    tasks_dir: Path, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    tasks_root = tasks_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
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
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "/index.html":
                body = DASHBOARD_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/tasks":
                self._send_json(HTTPStatus.OK, {"tasks": _index_tasks(tasks_root)})
                return

            if path.startswith("/api/tasks/"):
                task_id = urllib.parse.unquote(path[len("/api/tasks/") :])
                detail = _load_task_detail(tasks_root, task_id)
                if detail is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Task not found"})
                else:
                    self._send_json(HTTPStatus.OK, detail)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
