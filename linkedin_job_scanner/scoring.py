from __future__ import annotations

import re
from typing import Any

from .models import JobPosting, ScoreResult
from .resume_bank import ResumeBank


# ---------------------------------------------------------------------------
# Keyword helpers
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "this", "that", "these", "those", "it", "its", "we", "our", "you",
    "your", "they", "their", "as", "if", "so", "up", "out", "about",
    "into", "through", "during", "including", "using", "while", "prior",
    "within", "along", "following", "across", "behind", "beyond", "plus",
    "except", "but", "around", "between", "per", "not", "no", "nor",
}


def _tokenize(text: str) -> list[str]:
    return [
        w.lower()
        for w in re.split(r"[^a-zA-Z0-9+#]+", text)
        if len(w) >= 3 and w.lower() not in _STOPWORDS
    ]


def _extract_keywords(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in _tokenize(text):
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _keyword_overlap(job_keywords: list[str], resume_text: str) -> tuple[list[str], list[str]]:
    resume_tokens = set(_tokenize(resume_text))
    matched = [kw for kw in job_keywords if kw in resume_tokens]
    missing = [kw for kw in job_keywords if kw not in resume_tokens]
    return matched, missing


def cosine_similarity(text_a: str, text_b: str) -> float:
    from math import sqrt

    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    freq_a: dict[str, int] = {}
    freq_b: dict[str, int] = {}
    for t in tokens_a:
        freq_a[t] = freq_a.get(t, 0) + 1
    for t in tokens_b:
        freq_b[t] = freq_b.get(t, 0) + 1
    dot = sum(freq_a.get(t, 0) * freq_b.get(t, 0) for t in freq_a)
    mag_a = sqrt(sum(v * v for v in freq_a.values()))
    mag_b = sqrt(sum(v * v for v in freq_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def clamp_score(value: float, low: float = 1.0, high: float = 10.0) -> float:
    return max(low, min(high, round(value * 10, 2)))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _build_notes(
    job_keywords: list[str],
    matched: list[str],
    missing: list[str],
) -> str:
    pct = int(100 * len(matched) / max(len(job_keywords), 1))
    parts = [f"Keyword coverage: {pct}% ({len(matched)}/{len(job_keywords)} job keywords matched in resume bank)"]
    if matched:
        parts.append("Matched: " + ", ".join(matched[:15]))
    if missing:
        parts.append("Missing: " + ", ".join(missing[:15]))
    return ". ".join(parts)


def score_job(job: JobPosting, resume_bank: ResumeBank, config: dict[str, Any]) -> ScoreResult:
    profile_text = resume_bank.profile_text or ""
    job_text = " ".join(filter(None, [job.title, job.company, job.location, job.description]))

    job_keywords = _extract_keywords(job_text)
    matched_keywords, missing_keywords = _keyword_overlap(job_keywords, profile_text)

    kw_ratio = len(matched_keywords) / max(len(job_keywords), 1)

    role_fit = clamp_score(cosine_similarity(job.title + " " + job.description[:500], profile_text) * 1.5)
    skill_match = clamp_score(kw_ratio * 1.2)
    experience_match = clamp_score(cosine_similarity(job.description, profile_text) * 1.3)
    domain_fit = clamp_score(cosine_similarity(job.description[:300], profile_text) * 1.4)
    seniority_location_fit = clamp_score(cosine_similarity(job.title + " " + job.location, profile_text) * 1.6)
    ats_keyword_coverage = clamp_score(kw_ratio * 1.1)

    overall = clamp_score(
        role_fit * 0.20
        + skill_match * 0.25
        + experience_match * 0.20
        + domain_fit * 0.15
        + seniority_location_fit * 0.10
        + ats_keyword_coverage * 0.10
    )

    matched_resume_path = None
    if resume_bank.documents:
        best_resume = resume_bank.best_resume_for_job(job_text, config.get("preferred_resume_templates", []))
        matched_resume_path = best_resume.path
    notes = _build_notes(job_keywords, matched_keywords, missing_keywords)
    return ScoreResult(
        job_id=job.key(),
        overall_score=overall,
        role_fit=role_fit,
        skill_match=skill_match,
        experience_match=experience_match,
        domain_fit=domain_fit,
        seniority_location_fit=seniority_location_fit,
        ats_keyword_coverage=ats_keyword_coverage,
        matched_keywords=matched_keywords[:35],
        missing_keywords=missing_keywords[:25],
        matched_resume_path=matched_resume_path,
        notes=notes,
    )


def estimate_resume_ats_score(job: JobPosting, resume_text: str, config: dict[str, Any] | None = None) -> float:
    """Estimate LinkedIn-style ATS fit on a 100-point weighted scale."""
    job_text = " ".join(filter(None, [job.title, job.company, job.location, job.description]))
    job_keywords = _extract_keywords(job_text)
    if not job_keywords:
        return 50.0
    matched, _ = _keyword_overlap(job_keywords, resume_text)
    raw = len(matched) / len(job_keywords)
    return round(min(100.0, raw * 120), 1)
