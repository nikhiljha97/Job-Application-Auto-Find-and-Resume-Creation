from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .models import JobPosting, ScoreResult


class ScannerState:
    def __init__(self, output_dir: str | Path) -> None:
        self.data_dir = Path(output_dir) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_path = self.data_dir / "jobs.json"
        self.scores_path = self.data_dir / "scores.json"
        self.notified_path = self.data_dir / "notified_jobs.json"

    def load_jobs(self) -> dict[str, JobPosting]:
        data = _load_json(self.jobs_path, [])
        jobs = [JobPosting.from_dict(item) for item in data]
        return {job.key(): job for job in jobs}

    def save_jobs(self, jobs: dict[str, JobPosting]) -> None:
        ordered = sorted(jobs.values(), key=lambda item: item.scraped_at, reverse=True)
        _write_json(self.jobs_path, [job.to_dict() for job in ordered])

    def load_scores(self) -> dict[str, ScoreResult]:
        data = _load_json(self.scores_path, [])
        scores = [ScoreResult.from_dict(item) for item in data]
        return {score.job_id: score for score in scores}

    def save_scores(self, scores: dict[str, ScoreResult]) -> None:
        ordered = sorted(scores.values(), key=lambda item: item.overall_score, reverse=True)
        _write_json(self.scores_path, [score.to_dict() for score in ordered])

    def load_notified_keys(self) -> set[str]:
        data = _load_json(self.notified_path, [])
        return {str(item) for item in data}

    def save_notified_keys(self, keys: set[str]) -> None:
        _write_json(self.notified_path, sorted(keys))


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.stem}.tmp{path.suffix}")
    text = json.dumps(payload, indent=2)
    last_exc: OSError | None = None
    for attempt in range(1, 6):
        try:
            tmp_path.write_text(text, encoding="utf-8")
            tmp_path.replace(path)
            return
        except OSError as exc:
            last_exc = exc
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt == 5:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    raise last_exc or OSError(f"Could not write {path}")
