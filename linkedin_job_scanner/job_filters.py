from __future__ import annotations

from typing import Any

from .experience_requirements import exceeds_experience_limit
from .models import JobPosting
from .text_utils import normalize_text, phrase_in_text


DEFAULT_JUNIOR_TERMS = ["junior", "jr", "jr."]
DEFAULT_JUNIOR_REQUIRED_TERMS = [
    "analyst",
    "analytics",
    "analytic",
    "data",
    "insight",
    "insights",
]


def is_actionable_job(job: JobPosting, config: dict[str, Any]) -> bool:
    """Return whether a job should be ranked, notified, and used for docs."""

    if not job.accepting_applications:
        return False
    if exceeds_experience_limit(job.full_text(), float(config.get("max_required_experience_years", 5.99))):
        return False
    return _passes_junior_gate(job, config)


def _passes_junior_gate(job: JobPosting, config: dict[str, Any]) -> bool:
    text = normalize_text(f"{job.title} {job.description[:1200]}")
    junior_terms = config.get("junior_gate_terms", DEFAULT_JUNIOR_TERMS)
    required_terms = config.get("junior_required_terms", DEFAULT_JUNIOR_REQUIRED_TERMS)
    has_junior = any(_has_term(text, term) for term in junior_terms)
    if not has_junior:
        return True
    return any(_has_term(text, term) for term in required_terms)


def _has_term(text: str, term: str) -> bool:
    term = str(term).strip()
    if not term:
        return False
    return phrase_in_text(term, text)
