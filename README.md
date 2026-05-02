# Codex Task Monitor for Kindle

A read-only local dashboard for showing recent Codex task and project state on a Kindle browser.

It watches your local `~/.codex` session files, groups recent Codex work into task and project views, and serves Kindle-friendly black-and-white pages over your LAN.

## Features

- Compact project list homepage at `/`
- Task dashboard at `/tasks`
- Project dashboard at `/projects`
- Compact project list at `/projects/compact`
- JSON APIs at `/api/tasks` and `/api/projects`
- Kindle-friendly typography, high contrast, and 60-second partial refresh
- Fullscreen button with best-effort auto fullscreen
- UTC+8 display times
- Read-only access to Codex session data

## Run

```sh
python3 -m codex_task_monitor.server --port 8765
```

Open the printed LAN URL on the Kindle experimental browser while the Kindle and Mac are on the same Wi-Fi.

## Development

```sh
python3 -m unittest discover -s tests
```

## Endpoints

- `/` serves the compact project list homepage.
- `/tasks` serves the task dashboard.
- `/api/tasks` serves the same task summary as JSON.
- `/projects` serves the project dashboard.
- `/projects/compact` serves a compact project list.
- `/api/projects` serves the same project summary as JSON.

The service reads from `~/.codex` and does not write to Codex session files.

## License

MIT
