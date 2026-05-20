from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .file_io import read_text_with_retries


PROJECT_ROOT = Path(__file__).resolve().parents[1]


DEFAULT_CONFIG: dict[str, Any] = {
    "search_url": "",
    "search_query": (
        'strategy OR insight OR insights OR analyst OR analytics OR "data analyst" OR '
        '"data analytic" OR "junior analyst" OR "entry analyst" OR "entry level analyst"'
    ),
    "linkedin_location": "Canada",
    "resume_root": "..",
    "output_dir": "outputs",
    "linkedin_profile_dir": ".linkedin_profile",
    "max_pages": 5,
    "page_size": 25,
    "min_score": 6.0,
    "max_resumes_per_run": 25,
    "hourly_interval_minutes": 60,
    "headless": False,
    "launch_agent_headless": True,
    "target_locations": ["Toronto", "Mississauga", "Hamilton", "Ontario", "Canada", "Remote", "Hybrid"],
    "target_role_keywords": [
        "strategy",
        "insight",
        "insights",
        "analytics",
        "analytic",
        "analyst",
        "data analyst",
        "data analytics",
        "data analytic",
        "junior analyst",
        "entry analyst",
        "entry level analyst",
        "business intelligence",
    ],
    "junior_gate_terms": ["junior", "jr", "jr."],
    "junior_required_terms": ["analyst", "analytics", "analytic", "data", "insight", "insights"],
    "seniority_keywords_to_penalize": ["director", "vp", "vice president", "head of", "principal", "staff"],
    "preferred_resume_templates": [],
    "exclude_resume_name_terms": ["coverletter", "cover letter", "statement of purpose", "~$"],
    "resume_bank_use_cache_without_rescan": True,
    "excel_sort_mode": "latest_first",
    "excel_hide_closed_jobs": True,
    "max_required_experience_years": 5.99,
}


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else PROJECT_ROOT / "config.json"
    if not path.exists():
        config = DEFAULT_CONFIG.copy()
    else:
        user_config = json.loads(read_text_with_retries(path))
        config = {**DEFAULT_CONFIG, **user_config}

    config["_config_path"] = str(path.resolve())
    config["_project_root"] = str(PROJECT_ROOT)
    config["resume_root"] = str(resolve_path(config["resume_root"], PROJECT_ROOT))
    if config.get("trusted_resume_root"):
        config["trusted_resume_root"] = str(resolve_path(config["trusted_resume_root"], PROJECT_ROOT))
    config["output_dir"] = str(resolve_path(config["output_dir"], PROJECT_ROOT))
    config["linkedin_profile_dir"] = str(resolve_path(config["linkedin_profile_dir"], PROJECT_ROOT))
    return config


def resolve_path(value: str | Path, base: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (Path(base) / path).resolve()
