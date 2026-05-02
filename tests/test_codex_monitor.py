import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codex_task_monitor.monitor import load_projects, load_tasks


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class CodexMonitorTests(unittest.TestCase):
    def test_loads_active_task_from_session_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / ".codex"
            thread_id = "active-thread"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {
                        "id": thread_id,
                        "thread_name": "Active Kindle dashboard",
                        "updated_at": "2026-05-02T06:00:00Z",
                    }
                ],
            )
            write_jsonl(
                codex_home
                / "sessions"
                / "2026"
                / "05"
                / "02"
                / "rollout-2026-05-02T14-00-00-active-thread.jsonl",
                [
                    {"timestamp": "2026-05-02T06:00:00Z", "type": "session_meta", "payload": {"id": thread_id}},
                    {
                        "timestamp": "2026-05-02T06:00:05Z",
                        "type": "event_msg",
                        "payload": {"type": "task_started"},
                    },
                    {
                        "timestamp": "2026-05-02T06:00:06Z",
                        "type": "event_msg",
                        "payload": {"type": "agent_message", "message": "Parsing sessions now"},
                    },
                ],
            )

            tasks = load_tasks(codex_home, now=datetime(2026, 5, 2, 6, 1, tzinfo=timezone.utc))

            self.assertEqual(tasks[0]["id"], thread_id)
            self.assertEqual(tasks[0]["title"], "Active Kindle dashboard")
            self.assertEqual(tasks[0]["status"], "active")
            self.assertEqual(tasks[0]["phase"], "working")
            self.assertEqual(tasks[0]["last_action"], "Assistant: Parsing sessions now")

    def test_classifies_done_stale_error_and_missing_session_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / ".codex"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {"id": "done-thread", "thread_name": "Done task", "updated_at": "2026-05-02T05:59:00Z"},
                    {"id": "stale-thread", "thread_name": "Stale task", "updated_at": "2026-05-02T03:00:00Z"},
                    {"id": "error-thread", "thread_name": "Error task", "updated_at": "2026-05-02T05:58:00Z"},
                    {"id": "missing-thread", "thread_name": "Index only", "updated_at": "2026-05-02T05:57:00Z"},
                ],
            )
            sessions_dir = codex_home / "sessions" / "2026" / "05" / "02"
            write_jsonl(
                sessions_dir / "rollout-2026-05-02T13-59-00-done-thread.jsonl",
                [
                    {"timestamp": "2026-05-02T05:59:00Z", "type": "session_meta", "payload": {"id": "done-thread"}},
                    {"timestamp": "2026-05-02T05:59:01Z", "type": "event_msg", "payload": {"type": "task_started"}},
                    {"timestamp": "2026-05-02T05:59:20Z", "type": "event_msg", "payload": {"type": "task_complete"}},
                ],
            )
            write_jsonl(
                sessions_dir / "rollout-2026-05-02T11-00-00-stale-thread.jsonl",
                [
                    {"timestamp": "2026-05-02T03:00:00Z", "type": "session_meta", "payload": {"id": "stale-thread"}},
                    {"timestamp": "2026-05-02T03:00:10Z", "type": "event_msg", "payload": {"type": "agent_message", "message": "Waiting"}},
                ],
            )
            write_jsonl(
                sessions_dir / "rollout-2026-05-02T13-58-00-error-thread.jsonl",
                [
                    {"timestamp": "2026-05-02T05:58:00Z", "type": "session_meta", "payload": {"id": "error-thread"}},
                    {
                        "timestamp": "2026-05-02T05:58:05Z",
                        "type": "event_msg",
                        "payload": {"type": "exec_command_end", "exit_code": 2, "command": ["pytest"]},
                    },
                ],
            )

            tasks = load_tasks(codex_home, now=datetime(2026, 5, 2, 6, 0, tzinfo=timezone.utc))
            by_id = {task["id"]: task for task in tasks}

            self.assertEqual(by_id["done-thread"]["status"], "done")
            self.assertEqual(by_id["stale-thread"]["status"], "stale")
            self.assertEqual(by_id["error-thread"]["status"], "error")
            self.assertEqual(by_id["missing-thread"]["status"], "idle")
            self.assertEqual(by_id["missing-thread"]["last_action"], "Indexed, no session file found")

    def test_completed_task_is_not_overridden_by_log_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / ".codex"
            thread_id = "019de234-d90c-7db2-8f2c-abf288310d0f"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": thread_id, "thread_name": "Complete despite old log", "updated_at": "2026-05-02T05:59:00Z"}],
            )
            write_jsonl(
                codex_home / "sessions" / "2026" / "05" / "02" / f"rollout-2026-05-02T13-59-00-{thread_id}.jsonl",
                [
                    {"timestamp": "2026-05-02T05:59:00Z", "type": "session_meta", "payload": {"id": thread_id}},
                    {"timestamp": "2026-05-02T05:59:20Z", "type": "event_msg", "payload": {"type": "task_complete"}},
                ],
            )
            log_path = codex_home / "log" / "codex-tui.log"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                f"2026-05-02T05:59:30Z ERROR session_loop{{thread_id={thread_id}}}: old shutdown failure\n",
                encoding="utf-8",
            )

            tasks = load_tasks(codex_home, now=datetime(2026, 5, 2, 6, 0, tzinfo=timezone.utc))

            self.assertEqual(tasks[0]["status"], "done")

    def test_successful_later_action_clears_command_error_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / ".codex"
            thread_id = "recovered-thread"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": thread_id, "thread_name": "Recovered task", "updated_at": "2026-05-02T06:00:00Z"}],
            )
            write_jsonl(
                codex_home / "sessions" / "2026" / "05" / "02" / f"rollout-2026-05-02T14-00-00-{thread_id}.jsonl",
                [
                    {"timestamp": "2026-05-02T06:00:00Z", "type": "session_meta", "payload": {"id": thread_id}},
                    {"timestamp": "2026-05-02T06:00:01Z", "type": "event_msg", "payload": {"type": "task_started"}},
                    {
                        "timestamp": "2026-05-02T06:00:02Z",
                        "type": "event_msg",
                        "payload": {"type": "exec_command_end", "exit_code": 1, "command": ["false"]},
                    },
                    {
                        "timestamp": "2026-05-02T06:00:03Z",
                        "type": "event_msg",
                        "payload": {"type": "exec_command_end", "exit_code": 0, "command": ["true"]},
                    },
                ],
            )

            tasks = load_tasks(codex_home, now=datetime(2026, 5, 2, 6, 1, tzinfo=timezone.utc))

            self.assertEqual(tasks[0]["status"], "active")

    def test_load_tasks_includes_project_metadata_from_session_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / ".codex"
            repo = root / "workspace" / "repo"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            thread_id = "project-thread"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": thread_id, "thread_name": "Project task", "updated_at": "2026-05-02T06:00:00Z"}],
            )
            write_jsonl(
                codex_home / "sessions" / "2026" / "05" / "02" / f"rollout-2026-05-02T14-00-00-{thread_id}.jsonl",
                [
                    {
                        "timestamp": "2026-05-02T06:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": thread_id, "cwd": str(repo / "subdir")},
                    },
                    {"timestamp": "2026-05-02T06:00:01Z", "type": "event_msg", "payload": {"type": "task_started"}},
                ],
            )

            tasks = load_tasks(codex_home, now=datetime(2026, 5, 2, 6, 1, tzinfo=timezone.utc))

            self.assertEqual(tasks[0]["cwd"], str(repo / "subdir"))
            self.assertEqual(tasks[0]["project_id"], str(repo))
            self.assertEqual(tasks[0]["project_name"], "repo")

    def test_load_projects_groups_tasks_by_git_root_and_fallback_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / ".codex"
            repo = root / "workspace" / "repo"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            loose = root / "scratch"
            loose.mkdir()
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {"id": "repo-active", "thread_name": "Repo active", "updated_at": "2026-05-02T06:00:00Z"},
                    {"id": "repo-done", "thread_name": "Repo done", "updated_at": "2026-05-02T05:59:00Z"},
                    {"id": "loose-task", "thread_name": "Loose", "updated_at": "2026-05-02T05:58:00Z"},
                    {"id": "unknown-task", "thread_name": "Unknown", "updated_at": "2026-05-02T05:57:00Z"},
                ],
            )
            sessions_dir = codex_home / "sessions" / "2026" / "05" / "02"
            write_jsonl(
                sessions_dir / "rollout-2026-05-02T14-00-00-repo-active.jsonl",
                [
                    {
                        "timestamp": "2026-05-02T06:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "repo-active", "cwd": str(repo / "frontend")},
                    },
                    {"timestamp": "2026-05-02T06:00:01Z", "type": "event_msg", "payload": {"type": "task_started"}},
                ],
            )
            write_jsonl(
                sessions_dir / "rollout-2026-05-02T13-59-00-repo-done.jsonl",
                [
                    {
                        "timestamp": "2026-05-02T05:59:00Z",
                        "type": "session_meta",
                        "payload": {"id": "repo-done", "cwd": str(repo / "backend")},
                    },
                    {"timestamp": "2026-05-02T05:59:01Z", "type": "event_msg", "payload": {"type": "task_complete"}},
                ],
            )
            write_jsonl(
                sessions_dir / "rollout-2026-05-02T13-58-00-loose-task.jsonl",
                [
                    {
                        "timestamp": "2026-05-02T05:58:00Z",
                        "type": "session_meta",
                        "payload": {"id": "loose-task", "cwd": str(loose)},
                    }
                ],
            )
            write_jsonl(
                sessions_dir / "rollout-2026-05-02T13-57-00-unknown-task.jsonl",
                [{"timestamp": "2026-05-02T05:57:00Z", "type": "session_meta", "payload": {"id": "unknown-task"}}],
            )

            projects = load_projects(codex_home, now=datetime(2026, 5, 2, 6, 1, tzinfo=timezone.utc))
            by_name = {project["project_name"]: project for project in projects}

            self.assertEqual(by_name["repo"]["counts"]["total"], 2)
            self.assertEqual(by_name["repo"]["counts"]["active"], 1)
            self.assertEqual(by_name["repo"]["recent_tasks"][0]["title"], "Repo active")
            self.assertEqual(by_name["scratch"]["project_id"], str(loose))
            self.assertEqual(by_name["Unknown Project"]["counts"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
