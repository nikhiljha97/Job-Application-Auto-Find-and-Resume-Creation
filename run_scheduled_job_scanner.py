from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from linkedin_job_scanner.config import PROJECT_ROOT, load_config
from linkedin_job_scanner.file_io import read_text_with_retries, write_text_atomic_with_retries


def main() -> int:
    parser = argparse.ArgumentParser(description="Reliably run due job-scanner schedule slots.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"), help="Path to config.json")
    parser.add_argument("--window-minutes", type=int, default=None, help="Run a slot within this many minutes after its time.")
    args = parser.parse_args()

    config = load_config(args.config)
    window_minutes = args.window_minutes
    if window_minutes is None:
        window_minutes = int(config.get("schedule_window_minutes", 90))
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    scheduler_state_path = output_dir / "data" / "schedule_runs.json"
    scheduler_state_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
    due_slot = _due_slot(config, now, max(1, window_minutes))
    if not due_slot:
        print(f"No scheduled scan due at {now.strftime('%Y-%m-%d %H:%M:%S %Z')}.")
        return 0

    state = _load_json(scheduler_state_path, {})
    run_key = f"{now.date().isoformat()} {due_slot}"
    if state.get("last_started") == run_key or run_key in set(state.get("completed", [])):
        print(f"Scheduled scan already handled for {run_key}.")
        return 0

    state["last_started"] = run_key
    _write_json(scheduler_state_path, state)

    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "run_job_scanner.py"),
        "--once",
        "--config",
        str(Path(args.config).resolve()),
    ]
    if bool(config.get("launch_agent_headless", True)):
        command.append("--headless")

    print(f"Starting scheduled scan for {run_key}: {' '.join(command)}")
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    if completed.returncode == 0:
        finished = list(state.get("completed", []))
        finished.append(run_key)
        state["completed"] = finished[-30:]
        state["last_completed"] = run_key
        _write_json(scheduler_state_path, state)
    else:
        print(f"Scheduled scan failed for {run_key} with exit code {completed.returncode}.", file=sys.stderr)
    return completed.returncode


def _due_slot(config: dict[str, Any], now: datetime, window_minutes: int) -> str:
    schedule = config.get("launch_schedule", {})
    if schedule.get("mode") != "daily_times":
        return now.strftime("%H:%M")
    for value in schedule.get("times", []):
        hour, minute = _parse_time(value)
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if scheduled <= now <= scheduled + timedelta(minutes=window_minutes):
            return f"{hour:02d}:{minute:02d}"
    return ""


def _parse_time(value: Any) -> tuple[int, int]:
    if isinstance(value, dict):
        return int(value.get("hour", 8)), int(value.get("minute", 0))
    hour_text, _, minute_text = str(value).partition(":")
    return int(hour_text), int(minute_text or 0)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(read_text_with_retries(path))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    write_text_atomic_with_retries(path, json.dumps(payload, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
