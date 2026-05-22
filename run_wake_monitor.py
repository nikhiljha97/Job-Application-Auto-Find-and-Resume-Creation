from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the job scanner scheduler after Mac wake events.")
    parser.add_argument("--repo", required=True, help="Path to the job_scanner repository")
    parser.add_argument("--config", required=True, help="Path to config.json")
    parser.add_argument("--output-dir", required=True, help="Runtime output directory")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--wake-gap-seconds", type=int, default=180)
    parser.add_argument("--run-at-start", action="store_true")
    args = parser.parse_args()

    interval = max(15, args.interval_seconds)
    wake_gap = max(interval * 2, args.wake_gap_seconds)
    repo = Path(args.repo).resolve()
    config = Path(args.config).resolve()
    output_dir = Path(args.output_dir).resolve()
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_log = log_dir / "launchd.out.log"
    err_log = log_dir / "launchd.err.log"

    print(f"Wake monitor started at {_stamp()} with interval={interval}s wake_gap={wake_gap}s.", flush=True)
    last_seen = datetime.now().astimezone()
    if args.run_at_start:
        _run_scheduler(repo, config, out_log, err_log, reason="monitor start")

    while True:
        time.sleep(interval)
        now = datetime.now().astimezone()
        elapsed = (now - last_seen).total_seconds()
        if elapsed >= wake_gap:
            print(f"Wake detected at {_stamp()} after {elapsed:.0f}s gap.", flush=True)
            _run_scheduler(repo, config, out_log, err_log, reason=f"wake gap {elapsed:.0f}s")
        last_seen = now


def _run_scheduler(repo: Path, config: Path, out_log: Path, err_log: Path, reason: str) -> None:
    command = [
        sys.executable,
        "-u",
        str(repo / "run_scheduled_job_scanner.py"),
        "--config",
        str(config),
    ]
    with out_log.open("a", encoding="utf-8") as out, err_log.open("a", encoding="utf-8") as err:
        out.write(f"Wake monitor trigger at {_stamp()} ({reason}).\n")
        out.flush()
        completed = subprocess.run(command, cwd=str(repo), stdout=out, stderr=err, check=False)
        out.write(f"Wake monitor scheduler exit code {completed.returncode} at {_stamp()}.\n")
        out.flush()


def _stamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


if __name__ == "__main__":
    raise SystemExit(main())
