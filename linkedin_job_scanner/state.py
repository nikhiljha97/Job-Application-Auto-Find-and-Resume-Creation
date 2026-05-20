from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .file_io import read_text_with_retries, write_text_atomic_with_retries
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
        return json.loads(read_text_with_retries(path))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    write_text_atomic_with_retries(path, json.dumps(payload, indent=2))
