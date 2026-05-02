from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional


STALE_AFTER_SECONDS = 90 * 60


@dataclass
class SessionSummary:
    thread_id: str
    cwd: Optional[str] = None
    last_event_at: Optional[datetime] = None
    last_action: Optional[str] = None
    phase: str = "idle"
    task_started: bool = False
    task_complete: bool = False
    has_error: bool = False


def load_tasks(codex_home: Path, now: Optional[datetime] = None) -> List[dict]:
    now = now or datetime.now(timezone.utc)
    codex_home = Path(codex_home).expanduser()
    indexed = _load_index(codex_home / "session_index.jsonl")
    summaries = _load_session_summaries(codex_home / "sessions")
    log_errors = _load_recent_log_error_threads(codex_home / "log" / "codex-tui.log")

    tasks = []
    for thread_id, index_row in indexed.items():
        summary = summaries.get(thread_id)
        updated_at = _parse_time(index_row.get("updated_at"))
        last_event_at = summary.last_event_at if summary and summary.last_event_at else updated_at
        status = _classify_status(summary, last_event_at, now, thread_id in log_errors)
        phase = summary.phase if summary else "indexed"
        last_action = summary.last_action if summary and summary.last_action else "Indexed, no session file found"

        tasks.append(
            {
                "id": thread_id,
                "title": index_row.get("thread_name") or "Untitled Codex task",
                "status": status,
                "phase": phase,
                "last_action": last_action,
                "updated_at": _format_iso(last_event_at),
                "age_seconds": _age_seconds(last_event_at, now),
                **_project_metadata(summary.cwd if summary else None),
            }
        )

    tasks.sort(key=lambda task: task["updated_at"] or "", reverse=True)
    return tasks


def load_projects(codex_home: Path, now: Optional[datetime] = None, recent_limit: int = 3) -> List[dict]:
    tasks = load_tasks(codex_home, now)
    projects: Dict[str, dict] = {}
    for task in tasks:
        project_id = task.get("project_id") or "unknown"
        project = projects.setdefault(
            project_id,
            {
                "project_id": project_id,
                "project_name": task.get("project_name") or "Unknown Project",
                "cwd": task.get("project_cwd"),
                "updated_at": task.get("updated_at"),
                "age_seconds": task.get("age_seconds"),
                "counts": {"total": 0, "active": 0, "error": 0, "stale": 0, "done": 0, "idle": 0},
                "recent_tasks": [],
            },
        )
        project["counts"]["total"] += 1
        status = task.get("status") or "idle"
        if status in project["counts"]:
            project["counts"][status] += 1
        if not project.get("updated_at") or (task.get("updated_at") or "") > project["updated_at"]:
            project["updated_at"] = task.get("updated_at")
            project["age_seconds"] = task.get("age_seconds")
        if len(project["recent_tasks"]) < recent_limit:
            project["recent_tasks"].append(
                {
                    "id": task.get("id"),
                    "title": task.get("title"),
                    "status": task.get("status"),
                    "phase": task.get("phase"),
                    "last_action": task.get("last_action"),
                    "updated_at": task.get("updated_at"),
                    "age_seconds": task.get("age_seconds"),
                }
            )

    ordered = list(projects.values())
    ordered.sort(key=lambda project: project.get("updated_at") or "", reverse=True)
    return ordered


def _load_index(path: Path) -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    for row in _read_jsonl(path):
        thread_id = row.get("id")
        if thread_id:
            rows[thread_id] = row
    return rows


def _load_session_summaries(sessions_dir: Path) -> Dict[str, SessionSummary]:
    summaries: Dict[str, SessionSummary] = {}
    if not sessions_dir.exists():
        return summaries

    for path in sorted(sessions_dir.rglob("rollout-*.jsonl")):
        thread_id = _thread_id_from_path(path)
        summary = summaries.get(thread_id) if thread_id else None

        for row in _read_jsonl(path):
            row_thread_id = _thread_id_from_row(row) or (summary.thread_id if summary else None) or thread_id
            if not row_thread_id:
                continue
            if summary is None or summary.thread_id != row_thread_id:
                summary = summaries.setdefault(row_thread_id, SessionSummary(row_thread_id))
                thread_id = row_thread_id

            event_at = _parse_time(row.get("timestamp"))
            if event_at:
                summary.last_event_at = event_at

            _apply_row(summary, row)

    return summaries


def _apply_row(summary: SessionSummary, row: dict) -> None:
    payload = row.get("payload") or {}
    row_type = row.get("type")
    payload_type = payload.get("type")

    if row_type == "session_meta":
        cwd = payload.get("cwd")
        if cwd:
            summary.cwd = cwd
    elif row_type == "event_msg":
        if payload_type == "task_started":
            summary.task_started = True
            summary.task_complete = False
            summary.phase = "working"
            summary.last_action = "Task started"
        elif payload_type == "task_complete":
            summary.task_complete = True
            summary.phase = "complete"
            summary.last_action = "Task complete"
        elif payload_type == "agent_message":
            message = _summarize_text(payload.get("message"))
            if message:
                summary.has_error = False
                summary.last_action = f"Assistant: {message}"
                if summary.phase == "idle":
                    summary.phase = "responding"
        elif payload_type == "user_message":
            message = _summarize_text(payload.get("message"))
            if message:
                summary.has_error = False
                summary.last_action = f"User: {message}"
                summary.phase = "waiting"
        elif payload_type == "exec_command_end":
            exit_code = payload.get("exit_code")
            command = _summarize_command(payload.get("command"))
            if exit_code not in (None, 0):
                summary.has_error = True
                summary.phase = "blocked"
                summary.last_action = f"Command failed: {command or 'unknown command'}"
            elif command:
                summary.has_error = False
                summary.last_action = f"Command finished: {command}"
                if summary.phase == "idle":
                    summary.phase = "checking"
    elif row_type == "response_item":
        if payload_type == "function_call":
            summary.phase = "tooling"
            name = payload.get("name")
            if name:
                summary.last_action = f"Tool call: {name}"
        elif payload_type == "message":
            role = payload.get("role")
            if role == "assistant":
                text = _extract_content_text(payload.get("content"))
                if text:
                    summary.last_action = f"Assistant: {_summarize_text(text)}"


def _classify_status(
    summary: Optional[SessionSummary],
    last_event_at: Optional[datetime],
    now: datetime,
    log_has_error: bool,
) -> str:
    if summary and summary.task_complete:
        return "done"
    if (summary and summary.has_error) or log_has_error:
        return "error"
    if summary and summary.task_started and not summary.task_complete:
        return "active"
    if _age_seconds(last_event_at, now) is not None and _age_seconds(last_event_at, now) > STALE_AFTER_SECONDS:
        return "stale"
    return "idle"


def _read_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _thread_id_from_path(path: Path) -> Optional[str]:
    match = re.search(r"rollout-.+?-([0-9a-fA-F-]{8,}|[A-Za-z0-9_-]+)\.jsonl$", path.name)
    return match.group(1) if match else None


def _thread_id_from_row(row: dict) -> Optional[str]:
    payload = row.get("payload") or {}
    if row.get("type") == "session_meta":
        return payload.get("id")
    return payload.get("thread_id") or row.get("thread_id")


def _project_metadata(cwd: Optional[str]) -> dict:
    if not cwd:
        return {
            "cwd": None,
            "project_id": "unknown",
            "project_name": "Unknown Project",
            "project_cwd": None,
        }
    project_root = _git_root(cwd) or cwd
    return {
        "cwd": cwd,
        "project_id": project_root,
        "project_name": Path(project_root).name or project_root,
        "project_cwd": project_root,
    }


def _git_root(cwd: str) -> Optional[str]:
    path = Path(cwd)
    current = path if path.exists() else path.parent
    while True:
        if (current / ".git").exists():
            return str(current)
        if current.parent == current:
            break
        current = current.parent
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode == 0:
        return result.stdout.strip() or None
    return None


def _load_recent_log_error_threads(path: Path) -> set:
    if not path.exists():
        return set()
    errors = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
    except OSError:
        return errors
    for line in lines:
        if "ERROR" not in line and "failed" not in line.lower():
            continue
        match = re.search(r"thread_id=([0-9a-fA-F-]{8,})", line)
        if match:
            errors.add(match.group(1))
    return errors


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _format_iso(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _age_seconds(value: Optional[datetime], now: datetime) -> Optional[int]:
    if not value:
        return None
    return max(0, int((now.astimezone(timezone.utc) - value).total_seconds()))


def _summarize_text(value: Optional[str], limit: int = 96) -> str:
    if not value:
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _summarize_command(value) -> str:
    if isinstance(value, list):
        return _summarize_text(" ".join(str(part) for part in value), 80)
    if isinstance(value, str):
        return _summarize_text(value, 80)
    return ""


def _extract_content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"output_text", "input_text"} and item.get("text"):
            parts.append(item["text"])
    return " ".join(parts)
