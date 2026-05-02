import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

from codex_task_monitor.server import (
    _is_preferred_lan_ip,
    build_html,
    build_projects_compact_html,
    build_projects_html,
    build_projects_payload,
    build_tasks_payload,
    run_server,
)


class ServerTests(unittest.TestCase):
    def test_build_tasks_payload_includes_counts_and_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            codex_home.mkdir()
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": "thread-1",
                        "thread_name": "Kindle task",
                        "updated_at": "2026-05-02T06:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_tasks_payload(codex_home)

            self.assertEqual(payload["counts"]["total"], 1)
            self.assertEqual(payload["tasks"][0]["title"], "Kindle task")
            self.assertIn("generated_at", payload)

    def test_build_html_is_kindle_friendly_and_escapes_content(self):
        payload = {
            "generated_at": "2026-05-02T06:00:00Z",
            "counts": {"total": 1, "active": 1, "error": 0, "stale": 0},
            "tasks": [
                {
                    "title": "<Secret>",
                    "status": "active",
                    "phase": "working",
                    "last_action": "Assistant: parse <files>",
                    "updated_at": "2026-05-02T06:00:00Z",
                    "age_seconds": 60,
                }
            ],
        }

        html = build_html(payload, refresh_seconds=60)

        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn("setInterval(refreshPageData", html)
        self.assertIn("fetch(window.location.href", html)
        self.assertIn('data-refresh-region="updated-time"', html)
        self.assertIn("&lt;Secret&gt;", html)
        self.assertIn("Assistant: parse &lt;files&gt;", html)
        self.assertIn("Kindle Codex Tasks", html)
        self.assertIn("2026-05-02 14:00:00 UTC+8", html)

    def test_build_html_links_to_projects_page(self):
        payload = {
            "generated_at": "2026-05-02T06:00:00Z",
            "counts": {"total": 0, "active": 0, "error": 0, "stale": 0},
            "tasks": [],
        }

        html = build_html(payload, refresh_seconds=60)

        self.assertIn('<a href="/">Projects</a>', html)

    def test_build_html_includes_fullscreen_button(self):
        payload = {
            "generated_at": "2026-05-02T06:00:00Z",
            "counts": {"total": 0, "active": 0, "error": 0, "stale": 0},
            "tasks": [],
        }

        html = build_html(payload, refresh_seconds=60)

        self.assertIn('<button type="button" id="fullscreen-button"', html)
        self.assertIn("requestFullscreen", html)
        self.assertIn("exitFullscreen", html)
        self.assertIn("Exit fullscreen", html)
        self.assertIn("tryAutoFullscreen", html)
        self.assertIn("window.addEventListener('load'", html)
        self.assertIn("pseudo-fullscreen", html)
        self.assertNotIn("Fullscreen unavailable", html)

    def test_build_html_compacts_tasks_older_than_five_minutes(self):
        payload = {
            "generated_at": "2026-05-02T06:00:00Z",
            "counts": {"total": 2, "active": 1, "error": 0, "stale": 0},
            "tasks": [
                {
                    "title": "Fresh task",
                    "status": "active",
                    "phase": "working",
                    "last_action": "Assistant: still moving",
                    "updated_at": "2026-05-02T05:59:00Z",
                    "age_seconds": 300,
                },
                {
                    "title": "Older task",
                    "status": "done",
                    "phase": "complete",
                    "last_action": "Assistant: this verbose action should be hidden",
                    "updated_at": "2026-05-02T05:54:59Z",
                    "age_seconds": 301,
                },
            ],
        }

        html = build_html(payload, refresh_seconds=60)

        self.assertIn('class="task task-active"', html)
        self.assertIn("Assistant: still moving", html)
        self.assertIn("Older than 5m (1)", html)
        self.assertIn('class="compact-task compact-task-done"', html)
        self.assertIn("Older task", html)
        self.assertNotIn("this verbose action should be hidden", html)

    def test_preferred_lan_ip_rejects_proxy_ranges(self):
        self.assertTrue(_is_preferred_lan_ip("192.168.1.228"))
        self.assertTrue(_is_preferred_lan_ip("10.0.0.8"))
        self.assertTrue(_is_preferred_lan_ip("172.20.1.4"))
        self.assertFalse(_is_preferred_lan_ip("198.18.0.1"))
        self.assertFalse(_is_preferred_lan_ip("127.0.0.1"))

    def test_build_projects_payload_includes_project_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            (codex_home / "sessions" / "2026" / "05" / "02").mkdir(parents=True)
            (codex_home / "session_index.jsonl").write_text(
                json.dumps({"id": "thread-1", "thread_name": "Project task", "updated_at": "2026-05-02T06:00:00Z"})
                + "\n",
                encoding="utf-8",
            )
            (
                codex_home
                / "sessions"
                / "2026"
                / "05"
                / "02"
                / "rollout-2026-05-02T14-00-00-thread-1.jsonl"
            ).write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-02T06:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "thread-1", "cwd": str(repo / "app")},
                    }
                )
                + "\n"
                + json.dumps({"timestamp": "2026-05-02T06:00:01Z", "type": "event_msg", "payload": {"type": "task_started"}})
                + "\n",
                encoding="utf-8",
            )

            payload = build_projects_payload(codex_home, now=datetime(2026, 5, 2, 6, 1, tzinfo=timezone.utc))

            self.assertEqual(payload["counts"]["total"], 1)
            self.assertEqual(payload["counts"]["active"], 1)
            self.assertEqual(payload["projects"][0]["project_name"], "repo")
            self.assertEqual(payload["projects"][0]["recent_tasks"][0]["title"], "Project task")

    def test_build_projects_html_renders_recent_task_summaries(self):
        payload = {
            "generated_at": "2026-05-02T06:00:00Z",
            "counts": {"total": 1, "active": 1, "error": 0, "stale": 0},
            "projects": [
                {
                    "project_name": "<Project>",
                    "project_id": "/tmp/project",
                    "updated_at": "2026-05-02T06:00:00Z",
                    "age_seconds": 60,
                    "counts": {"total": 2, "active": 1, "error": 0, "stale": 0, "done": 1, "idle": 0},
                    "recent_tasks": [
                        {
                            "title": "<Task>",
                            "status": "active",
                            "phase": "working",
                            "last_action": "Assistant: parse <project>",
                            "updated_at": "2026-05-02T06:00:00Z",
                            "age_seconds": 60,
                        }
                    ],
                }
            ],
        }

        html = build_projects_html(payload, refresh_seconds=60)

        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn("setInterval(refreshPageData", html)
        self.assertIn("fetch(window.location.href", html)
        self.assertIn('data-refresh-region="updated-time"', html)
        self.assertIn("Kindle Codex Projects", html)
        self.assertIn("&lt;Project&gt;", html)
        self.assertIn("&lt;Task&gt;", html)
        self.assertIn("Assistant: parse &lt;project&gt;", html)
        self.assertIn("2026-05-02 14:00:00 UTC+8", html)

    def test_build_projects_html_includes_fullscreen_button(self):
        payload = {
            "generated_at": "2026-05-02T06:00:00Z",
            "counts": {"total": 0, "active": 0, "error": 0, "stale": 0},
            "projects": [],
        }

        html = build_projects_html(payload, refresh_seconds=60)

        self.assertIn('<button type="button" id="fullscreen-button"', html)
        self.assertIn("requestFullscreen", html)
        self.assertIn("exitFullscreen", html)
        self.assertIn("Exit fullscreen", html)
        self.assertIn("tryAutoFullscreen", html)
        self.assertIn("window.addEventListener('load'", html)
        self.assertIn("pseudo-fullscreen", html)
        self.assertNotIn("Fullscreen unavailable", html)

    def test_build_projects_compact_html_renders_project_list_only(self):
        payload = {
            "generated_at": "2026-05-02T06:00:00Z",
            "counts": {"total": 2, "active": 1, "error": 0, "stale": 0},
            "projects": [
                {
                    "project_name": "Newer",
                    "project_id": "/tmp/newer",
                    "updated_at": "2026-05-02T06:00:00Z",
                    "age_seconds": 60,
                    "counts": {"total": 1, "active": 1, "error": 0, "stale": 0, "done": 0, "idle": 0},
                    "recent_tasks": [{"title": "Hidden task", "last_action": "Hidden action"}],
                },
                {
                    "project_name": "Older",
                    "project_id": "/tmp/older",
                    "updated_at": "2026-05-02T05:00:00Z",
                    "age_seconds": 3660,
                    "counts": {"total": 3, "active": 0, "error": 0, "stale": 0, "done": 3, "idle": 0},
                    "recent_tasks": [{"title": "Another hidden task", "last_action": "Another hidden action"}],
                },
            ],
        }

        html = build_projects_compact_html(payload, refresh_seconds=60)

        self.assertIn("Kindle Codex Project List", html)
        self.assertIn("Newer", html)
        self.assertIn("Older", html)
        self.assertIn("Active: 1", html)
        self.assertIn("Tasks: 3", html)
        self.assertNotIn("Hidden task", html)
        self.assertNotIn("Hidden action", html)
        self.assertLess(html.index("Newer"), html.index("Older"))
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn("setInterval(refreshPageData", html)
        self.assertIn('data-refresh-region="updated-time"', html)
        self.assertIn("2026-05-02 14:00:00 UTC+8", html)

    def test_homepage_serves_compact_projects_and_tasks_route_serves_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            codex_home.mkdir()
            (codex_home / "session_index.jsonl").write_text(
                json.dumps({"id": "thread-1", "thread_name": "Task", "updated_at": "2026-05-02T06:00:00Z"})
                + "\n",
                encoding="utf-8",
            )
            server = run_server("127.0.0.1", 0, codex_home, 60)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                home = self._get_body(port, "/")
                tasks = self._get_body(port, "/tasks")

                self.assertIn("Kindle Codex Project List", home)
                self.assertIn("Kindle Codex Tasks", tasks)
            finally:
                server.shutdown()
                server.server_close()

    def _get_body(self, port, path):
        conn = HTTPConnection("127.0.0.1", port)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            return response.read().decode("utf-8")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
