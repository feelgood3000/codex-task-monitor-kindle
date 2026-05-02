from __future__ import annotations

import argparse
import html
import json
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from .monitor import load_projects, load_tasks


DEFAULT_REFRESH_SECONDS = 60
DEFAULT_PORT = 8765
COMPACT_AFTER_SECONDS = 5 * 60
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


def build_tasks_payload(codex_home: Path) -> dict:
    tasks = load_tasks(codex_home)
    counts = {
        "total": len(tasks),
        "active": sum(1 for task in tasks if task["status"] == "active"),
        "error": sum(1 for task in tasks if task["status"] == "error"),
        "stale": sum(1 for task in tasks if task["status"] == "stale"),
    }
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "counts": counts,
        "tasks": tasks,
    }


def build_projects_payload(codex_home: Path, now: Optional[datetime] = None) -> dict:
    projects = load_projects(codex_home, now)
    counts = {
        "total": len(projects),
        "active": sum(1 for project in projects if project["counts"].get("active", 0) > 0),
        "error": sum(1 for project in projects if project["counts"].get("error", 0) > 0),
        "stale": sum(1 for project in projects if project["counts"].get("stale", 0) > 0),
    }
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "counts": counts,
        "projects": projects,
    }


def build_html(payload: dict, refresh_seconds: int = DEFAULT_REFRESH_SECONDS) -> str:
    fresh_tasks, older_tasks = _split_tasks_by_age(payload.get("tasks", []))
    tasks_html = "\n".join(_render_task(task) for task in fresh_tasks)
    if not tasks_html:
        tasks_html = '<section class="empty">No recent task movement.</section>'
    older_tasks_html = _render_older_tasks(older_tasks)
    counts = payload.get("counts", {})

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kindle Codex Tasks</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #111;
      --paper: #fff;
      --line: #222;
      --muted: #555;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 18px;
      background: var(--paper);
      color: var(--ink);
      font-family: Georgia, "Times New Roman", serif;
      font-size: 20px;
      line-height: 1.35;
    }}
    header {{
      border-bottom: 3px solid var(--line);
      padding-bottom: 12px;
      margin-bottom: 14px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 34px;
      line-height: 1.1;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      color: var(--muted);
      font-size: 17px;
    }}
    .fullscreen-button {{
      border: 1px solid var(--line);
      background: var(--paper);
      color: var(--ink);
      font: inherit;
      font-size: 16px;
      padding: 1px 6px;
    }}
    .fullscreen-button:disabled {{
      color: var(--muted);
    }}
    body.pseudo-fullscreen {{
      padding: 6px;
    }}
    body.pseudo-fullscreen header {{
      margin-bottom: 8px;
      padding-bottom: 8px;
    }}
    body.pseudo-fullscreen h1 {{
      font-size: 24px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      border: 2px solid var(--line);
      margin: 0 0 14px;
    }}
    .summary div {{
      padding: 8px;
      border-right: 1px solid var(--line);
      text-align: center;
    }}
    .summary div:last-child {{ border-right: 0; }}
    .summary strong {{
      display: block;
      font-size: 28px;
      line-height: 1;
    }}
    .summary span {{
      display: block;
      font-size: 14px;
      text-transform: uppercase;
      color: var(--muted);
      margin-top: 4px;
    }}
    main {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
      gap: 12px;
    }}
    article {{
      border: 2px solid var(--line);
      padding: 12px;
      min-height: 150px;
      page-break-inside: avoid;
    }}
    .task-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      border-bottom: 1px solid var(--line);
      padding-bottom: 7px;
      margin-bottom: 8px;
    }}
    h2 {{
      margin: 0;
      font-size: 24px;
      line-height: 1.15;
      letter-spacing: 0;
    }}
    .status {{
      white-space: nowrap;
      font-size: 16px;
      font-weight: 700;
      text-transform: uppercase;
      border: 1px solid var(--line);
      padding: 2px 5px;
    }}
    .action {{
      margin: 8px 0;
      font-size: 19px;
    }}
    .detail {{
      margin: 0;
      color: var(--muted);
      font-size: 16px;
    }}
    .empty {{
      border: 2px solid var(--line);
      padding: 20px;
      font-size: 22px;
    }}
    .older {{
      margin-top: 14px;
      border-top: 3px solid var(--line);
      padding-top: 10px;
    }}
    .older h2 {{
      font-size: 22px;
      margin: 0 0 8px;
    }}
    .compact-list {{
      border: 2px solid var(--line);
    }}
    .compact-task {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 6px 12px;
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      font-size: 17px;
    }}
    .compact-task:last-child {{ border-bottom: 0; }}
    .compact-title {{
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .compact-status {{
      font-weight: 700;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .compact-detail {{
      grid-column: 1 / -1;
      color: var(--muted);
      font-size: 15px;
    }}
    @media (max-width: 520px) {{
      body {{ padding: 12px; font-size: 18px; }}
      h1 {{ font-size: 28px; }}
      main {{ grid-template-columns: 1fr; }}
      .summary {{ grid-template-columns: repeat(2, 1fr); }}
      .summary div:nth-child(2) {{ border-right: 0; }}
      .summary div:nth-child(-n+2) {{ border-bottom: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Kindle Codex Tasks</h1>
    <div class="meta">
      <span data-refresh-region="updated-time">Updated: {_format_display_time(payload.get("generated_at"))}</span>
      <span>Refresh: {int(refresh_seconds)}s</span>
      <span><a href="/">Projects</a></span>
      <span>{_fullscreen_button()}</span>
    </div>
  </header>
  <section class="summary" aria-label="Task summary" data-refresh-region="summary">
    <div><strong>{int(counts.get("total", 0))}</strong><span>Total</span></div>
    <div><strong>{int(counts.get("active", 0))}</strong><span>Active</span></div>
    <div><strong>{int(counts.get("error", 0))}</strong><span>Error</span></div>
    <div><strong>{int(counts.get("stale", 0))}</strong><span>Stale</span></div>
  </section>
  <main data-refresh-region="main">
    {tasks_html}
  </main>
  {older_tasks_html}
  {_fullscreen_script()}
  {_refresh_script(refresh_seconds)}
</body>
</html>
"""


def build_projects_html(payload: dict, refresh_seconds: int = DEFAULT_REFRESH_SECONDS) -> str:
    projects_html = "\n".join(_render_project(project) for project in payload.get("projects", []))
    if not projects_html:
        projects_html = '<section class="empty">No Codex projects found.</section>'
    counts = payload.get("counts", {})

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kindle Codex Projects</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #111;
      --paper: #fff;
      --line: #222;
      --muted: #555;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 18px;
      background: var(--paper);
      color: var(--ink);
      font-family: Georgia, "Times New Roman", serif;
      font-size: 20px;
      line-height: 1.35;
    }}
    header {{
      border-bottom: 3px solid var(--line);
      padding-bottom: 12px;
      margin-bottom: 14px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: 0;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      color: var(--muted);
      font-size: 17px;
    }}
    .fullscreen-button {{
      border: 1px solid var(--line);
      background: var(--paper);
      color: var(--ink);
      font: inherit;
      font-size: 16px;
      padding: 1px 6px;
    }}
    .fullscreen-button:disabled {{
      color: var(--muted);
    }}
    body.pseudo-fullscreen {{
      padding: 6px;
    }}
    body.pseudo-fullscreen header {{
      margin-bottom: 8px;
      padding-bottom: 8px;
    }}
    body.pseudo-fullscreen h1 {{
      font-size: 24px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      border: 2px solid var(--line);
      margin: 0 0 14px;
    }}
    .summary div {{
      padding: 8px;
      border-right: 1px solid var(--line);
      text-align: center;
    }}
    .summary div:last-child {{ border-right: 0; }}
    .summary strong {{
      display: block;
      font-size: 28px;
      line-height: 1;
    }}
    .summary span {{
      display: block;
      font-size: 14px;
      text-transform: uppercase;
      color: var(--muted);
      margin-top: 4px;
    }}
    main {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
      gap: 12px;
    }}
    article {{
      border: 2px solid var(--line);
      padding: 12px;
      page-break-inside: avoid;
    }}
    .project-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 8px;
      margin-bottom: 8px;
    }}
    h2 {{
      margin: 0;
      font-size: 25px;
      line-height: 1.15;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }}
    .project-count {{
      white-space: nowrap;
      font-size: 16px;
      font-weight: 700;
      border: 1px solid var(--line);
      padding: 2px 5px;
      align-self: start;
    }}
    .project-meta {{
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 16px;
      overflow-wrap: anywhere;
    }}
    .task-row {{
      border-top: 1px solid var(--line);
      padding: 7px 0;
      font-size: 17px;
    }}
    .task-row:first-of-type {{ border-top: 0; }}
    .task-title {{
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .task-status {{
      font-weight: 700;
      text-transform: uppercase;
      margin-left: 6px;
      white-space: nowrap;
    }}
    .task-action {{
      color: var(--muted);
      font-size: 15px;
      margin-top: 2px;
      overflow-wrap: anywhere;
    }}
    .empty {{
      border: 2px solid var(--line);
      padding: 20px;
      font-size: 22px;
    }}
    @media (max-width: 520px) {{
      body {{ padding: 12px; font-size: 18px; }}
      h1 {{ font-size: 28px; }}
      main {{ grid-template-columns: 1fr; }}
      .summary {{ grid-template-columns: repeat(2, 1fr); }}
      .summary div:nth-child(2) {{ border-right: 0; }}
      .summary div:nth-child(-n+2) {{ border-bottom: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Kindle Codex Projects</h1>
    <div class="meta">
      <span data-refresh-region="updated-time">Updated: {_format_display_time(payload.get("generated_at"))}</span>
      <span>Refresh: {int(refresh_seconds)}s</span>
      <span><a href="/tasks">Tasks</a></span>
      <span><a href="/">Compact</a></span>
      <span>{_fullscreen_button()}</span>
    </div>
  </header>
  <section class="summary" aria-label="Project summary" data-refresh-region="summary">
    <div><strong>{int(counts.get("total", 0))}</strong><span>Projects</span></div>
    <div><strong>{int(counts.get("active", 0))}</strong><span>Active</span></div>
    <div><strong>{int(counts.get("error", 0))}</strong><span>Error</span></div>
    <div><strong>{int(counts.get("stale", 0))}</strong><span>Stale</span></div>
  </section>
  <main data-refresh-region="main">
    {projects_html}
  </main>
  {_fullscreen_script()}
  {_refresh_script(refresh_seconds)}
</body>
</html>
"""


def build_projects_compact_html(payload: dict, refresh_seconds: int = DEFAULT_REFRESH_SECONDS) -> str:
    projects_html = "\n".join(_render_compact_project(project) for project in payload.get("projects", []))
    if not projects_html:
        projects_html = '<section class="empty">No Codex projects found.</section>'
    counts = payload.get("counts", {})

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kindle Codex Project List</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #111;
      --paper: #fff;
      --line: #222;
      --muted: #555;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 18px;
      background: var(--paper);
      color: var(--ink);
      font-family: Georgia, "Times New Roman", serif;
      font-size: 20px;
      line-height: 1.35;
    }}
    header {{
      border-bottom: 3px solid var(--line);
      padding-bottom: 12px;
      margin-bottom: 14px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: 0;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      color: var(--muted);
      font-size: 17px;
    }}
    .fullscreen-button {{
      border: 1px solid var(--line);
      background: var(--paper);
      color: var(--ink);
      font: inherit;
      font-size: 16px;
      padding: 1px 6px;
    }}
    .fullscreen-button:disabled {{
      color: var(--muted);
    }}
    body.pseudo-fullscreen {{
      padding: 6px;
    }}
    body.pseudo-fullscreen header {{
      margin-bottom: 8px;
      padding-bottom: 8px;
    }}
    body.pseudo-fullscreen h1 {{
      font-size: 24px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      border: 2px solid var(--line);
      margin: 0 0 14px;
    }}
    .summary div {{
      padding: 8px;
      border-right: 1px solid var(--line);
      text-align: center;
    }}
    .summary div:last-child {{ border-right: 0; }}
    .summary strong {{
      display: block;
      font-size: 28px;
      line-height: 1;
    }}
    .summary span {{
      display: block;
      font-size: 14px;
      text-transform: uppercase;
      color: var(--muted);
      margin-top: 4px;
    }}
    .project-list {{
      border: 2px solid var(--line);
    }}
    .project-row {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 4px 14px;
      padding: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .project-row:last-child {{ border-bottom: 0; }}
    .project-name {{
      font-size: 23px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .project-age {{
      font-weight: 700;
      white-space: nowrap;
    }}
    .project-stats {{
      grid-column: 1 / -1;
      color: var(--muted);
      font-size: 16px;
    }}
    .project-path {{
      grid-column: 1 / -1;
      color: var(--muted);
      font-size: 14px;
      overflow-wrap: anywhere;
    }}
    .empty {{
      border: 2px solid var(--line);
      padding: 20px;
      font-size: 22px;
    }}
    @media (max-width: 520px) {{
      body {{ padding: 12px; font-size: 18px; }}
      h1 {{ font-size: 28px; }}
      .summary {{ grid-template-columns: repeat(2, 1fr); }}
      .summary div:nth-child(2) {{ border-right: 0; }}
      .summary div:nth-child(-n+2) {{ border-bottom: 1px solid var(--line); }}
      .project-row {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Kindle Codex Project List</h1>
    <div class="meta">
      <span data-refresh-region="updated-time">Updated: {_format_display_time(payload.get("generated_at"))}</span>
      <span>Refresh: {int(refresh_seconds)}s</span>
      <span><a href="/tasks">Tasks</a></span>
      <span><a href="/projects">Detailed</a></span>
      <span>{_fullscreen_button()}</span>
    </div>
  </header>
  <section class="summary" aria-label="Project summary" data-refresh-region="summary">
    <div><strong>{int(counts.get("total", 0))}</strong><span>Projects</span></div>
    <div><strong>{int(counts.get("active", 0))}</strong><span>Active</span></div>
    <div><strong>{int(counts.get("error", 0))}</strong><span>Error</span></div>
    <div><strong>{int(counts.get("stale", 0))}</strong><span>Stale</span></div>
  </section>
  <main class="project-list" data-refresh-region="main">
    {projects_html}
  </main>
  {_fullscreen_script()}
  {_refresh_script(refresh_seconds)}
</body>
</html>
"""


def run_server(
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    codex_home: Optional[Path] = None,
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS,
) -> ThreadingHTTPServer:
    codex_home = codex_home or Path.home() / ".codex"
    handler = _make_handler(codex_home.expanduser(), refresh_seconds)
    server = ThreadingHTTPServer((host, port), handler)
    return server


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a Kindle-friendly Codex task overview.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind. Default: 0.0.0.0")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help=f"Port to bind. Default: {DEFAULT_PORT}")
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Path to Codex home.")
    parser.add_argument(
        "--refresh-seconds",
        default=DEFAULT_REFRESH_SECONDS,
        type=int,
        help=f"HTML refresh interval. Default: {DEFAULT_REFRESH_SECONDS}",
    )
    args = parser.parse_args(argv)

    server = run_server(args.host, args.port, Path(args.codex_home), args.refresh_seconds)
    local_ip = _local_ip()
    print(f"Serving Kindle Codex Project List at http://{local_ip}:{args.port}/")
    print(f"Local URL: http://127.0.0.1:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()
    return 0


def _make_handler(codex_home: Path, refresh_seconds: int) -> Callable:
    class TaskHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/tasks":
                self._send_json(build_tasks_payload(codex_home))
            elif parsed.path == "/api/projects":
                self._send_json(build_projects_payload(codex_home))
            elif parsed.path == "/projects/compact":
                payload = build_projects_payload(codex_home)
                self._send_html(build_projects_compact_html(payload, refresh_seconds))
            elif parsed.path == "/projects":
                payload = build_projects_payload(codex_home)
                self._send_html(build_projects_html(payload, refresh_seconds))
            elif parsed.path == "/tasks":
                payload = build_tasks_payload(codex_home)
                self._send_html(build_html(payload, refresh_seconds))
            elif parsed.path in {"/", "/index.html"}:
                payload = build_projects_payload(codex_home)
                self._send_html(build_projects_compact_html(payload, refresh_seconds))
            else:
                self.send_error(404, "Not found")

        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return TaskHandler


def _render_task(task: dict) -> str:
    title = _escape(task.get("title", "Untitled Codex task"))
    status = _escape(task.get("status", "idle"))
    phase = _escape(task.get("phase", "idle"))
    action = _escape(task.get("last_action", "No recent action"))
    updated_at = _format_display_time(task.get("updated_at"))
    age = _format_age(task.get("age_seconds"))
    return f"""<article class="task task-{status}">
  <div class="task-head">
    <h2>{title}</h2>
    <span class="status">{status}</span>
  </div>
  <p class="action">{action}</p>
  <p class="detail">Phase: {phase} · Updated: {updated_at} · Age: {age}</p>
</article>"""


def _render_project(project: dict) -> str:
    name = _escape(project.get("project_name", "Unknown Project"))
    counts = project.get("counts", {})
    total = int(counts.get("total", 0))
    active = int(counts.get("active", 0))
    error = int(counts.get("error", 0))
    stale = int(counts.get("stale", 0))
    age = _format_age(project.get("age_seconds"))
    project_id = _escape(project.get("project_id", "unknown"))
    tasks = "\n".join(_render_project_task(task) for task in project.get("recent_tasks", []))
    if not tasks:
        tasks = '<div class="task-row">No recent tasks.</div>'
    return f"""<article class="project">
      <div class="project-head">
        <h2>{name}</h2>
        <span class="project-count">{total} tasks</span>
      </div>
      <p class="project-meta">Active: {active} · Error: {error} · Stale: {stale} · Age: {age}</p>
      <p class="project-meta">{project_id}</p>
      <div class="project-tasks">
        {tasks}
      </div>
    </article>"""


def _render_project_task(task: dict) -> str:
    title = _escape(task.get("title", "Untitled Codex task"))
    status = _escape(task.get("status", "idle"))
    action = _escape(task.get("last_action", "No recent action"))
    age = _format_age(task.get("age_seconds"))
    return f"""<div class="task-row">
          <div><span class="task-title">{title}</span><span class="task-status">{status}</span></div>
          <div class="task-action">{action} · {age}</div>
        </div>"""


def _render_compact_project(project: dict) -> str:
    name = _escape(project.get("project_name", "Unknown Project"))
    project_id = _escape(project.get("project_id", "unknown"))
    counts = project.get("counts", {})
    total = int(counts.get("total", 0))
    active = int(counts.get("active", 0))
    error = int(counts.get("error", 0))
    stale = int(counts.get("stale", 0))
    done = int(counts.get("done", 0))
    age = _format_age(project.get("age_seconds"))
    updated_at = _format_display_time(project.get("updated_at"))
    return f"""<div class="project-row">
      <span class="project-name">{name}</span>
      <span class="project-age">{age}</span>
      <span class="project-stats">Tasks: {total} · Active: {active} · Error: {error} · Stale: {stale} · Done: {done} · Updated: {updated_at}</span>
      <span class="project-path">{project_id}</span>
    </div>"""


def _fullscreen_button() -> str:
    return '<button type="button" id="fullscreen-button" class="fullscreen-button">Fullscreen</button>'


def _fullscreen_script() -> str:
    return """<script>
    (function () {
      var button = document.getElementById('fullscreen-button');
      if (!button) return;
      var target = document.documentElement;
      var requestFullscreen = target.requestFullscreen ||
        target.webkitRequestFullscreen ||
        target.mozRequestFullScreen ||
        target.msRequestFullscreen;
      var exitFullscreen = document.exitFullscreen ||
        document.webkitExitFullscreen ||
        document.mozCancelFullScreen ||
        document.msExitFullscreen;
      function fullscreenElement() {
        return document.fullscreenElement ||
          document.webkitFullscreenElement ||
          document.mozFullScreenElement ||
          document.msFullscreenElement;
      }
      function updateButton() {
        button.textContent = (fullscreenElement() || document.body.classList.contains('pseudo-fullscreen')) ?
          'Exit fullscreen' :
          'Fullscreen';
      }
      if (!requestFullscreen || !exitFullscreen) {
        document.body.classList.add('pseudo-fullscreen');
        button.addEventListener('click', function () {
          document.body.classList.toggle('pseudo-fullscreen');
          updateButton();
        });
        updateButton();
        return;
      }
      button.addEventListener('click', function () {
        if (fullscreenElement()) {
          exitFullscreen.call(document);
        } else {
          requestFullscreen.call(target);
        }
      });
      function tryAutoFullscreen() {
        if (fullscreenElement()) return;
        var request = requestFullscreen.call(target);
        if (request && request.catch) {
          request.catch(function () {
            updateButton();
          });
        }
      }
      document.addEventListener('fullscreenchange', updateButton);
      document.addEventListener('webkitfullscreenchange', updateButton);
      document.addEventListener('mozfullscreenchange', updateButton);
      document.addEventListener('MSFullscreenChange', updateButton);
      window.addEventListener('load', function () {
        setTimeout(tryAutoFullscreen, 0);
      });
      updateButton();
    })();
  </script>"""


def _refresh_script(refresh_seconds: int) -> str:
    interval_ms = max(1, int(refresh_seconds)) * 1000
    return f"""<script>
    (function () {{
      function refreshPageData() {{
        fetch(window.location.href, {{ cache: 'no-store' }})
          .then(function (response) {{
            return response.text();
          }})
          .then(function (html) {{
            var parser = new DOMParser();
            var nextDocument = parser.parseFromString(html, 'text/html');
            var regions = document.querySelectorAll('[data-refresh-region]');
            for (var i = 0; i < regions.length; i += 1) {{
              var name = regions[i].getAttribute('data-refresh-region');
              var nextRegion = nextDocument.querySelector('[data-refresh-region="' + name + '"]');
              if (nextRegion) {{
                regions[i].innerHTML = nextRegion.innerHTML;
              }}
            }}
          }})
          .catch(function () {{
            // Keep the current view if a refresh fails.
          }});
      }}
      setInterval(refreshPageData, {interval_ms});
    }})();
  </script>"""


def _split_tasks_by_age(tasks: list) -> tuple:
    fresh = []
    older = []
    for task in tasks:
        age = task.get("age_seconds")
        try:
            is_older = age is not None and int(age) > COMPACT_AFTER_SECONDS
        except (TypeError, ValueError):
            is_older = False
        if is_older:
            older.append(task)
        else:
            fresh.append(task)
    return fresh, older


def _render_older_tasks(tasks: list) -> str:
    if not tasks:
        return '<section class="older" data-refresh-region="older"></section>'
    rows = "\n".join(_render_compact_task(task) for task in tasks)
    return f"""<section class="older" aria-label="Older tasks" data-refresh-region="older">
    <h2>Older than 5m ({len(tasks)})</h2>
    <div class="compact-list">
      {rows}
    </div>
  </section>"""


def _render_compact_task(task: dict) -> str:
    title = _escape(task.get("title", "Untitled Codex task"))
    status = _escape(task.get("status", "idle"))
    phase = _escape(task.get("phase", "idle"))
    updated_at = _format_display_time(task.get("updated_at"))
    age = _format_age(task.get("age_seconds"))
    return f"""<div class="compact-task compact-task-{status}">
        <span class="compact-title">{title}</span>
        <span class="compact-status">{status}</span>
        <span class="compact-detail">Phase: {phase} · Updated: {updated_at} · Age: {age}</span>
      </div>"""


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _format_display_time(value) -> str:
    if not value:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _escape(value)
    local_time = parsed.astimezone(DISPLAY_TIMEZONE)
    return _escape(local_time.strftime("%Y-%m-%d %H:%M:%S UTC+8"))


def _format_age(age_seconds) -> str:
    if age_seconds is None:
        return "unknown"
    try:
        seconds = int(age_seconds)
    except (TypeError, ValueError):
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"


def _local_ip() -> str:
    for candidate in _ifconfig_private_ips():
        return candidate
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _ifconfig_private_ips() -> list:
    try:
        result = subprocess.run(["ifconfig"], check=False, capture_output=True, text=True)
    except OSError:
        return []
    ips = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "inet" and _is_preferred_lan_ip(parts[1]):
            ips.append(parts[1])
    return ips


def _is_preferred_lan_ip(value: str) -> bool:
    return (
        value.startswith("192.168.")
        or value.startswith("10.")
        or any(value.startswith(f"172.{part}.") for part in range(16, 32))
    )


if __name__ == "__main__":
    raise SystemExit(main())
