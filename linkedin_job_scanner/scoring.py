from __future__ import annotations

import re
from typing import Any

from .models import JobPosting, ScoreResult
from .resume_bank import ResumeBank
from .text_utils import (
    DEFAULT_SKILL_PHRASES,
    clamp_score,
    cosine_similarity,
    extract_keywords,
    normalize_text,
    phrase_in_text,
    weighted_coverage,
)


DOMAIN_TERMS = [
    "analytics",
    "business intelligence",
    "consumer analytics",
    "consumer insights",
    "retail analytics",
    "retail media",
    "financial modelling",
    "finance",
    "risk analytics",
    "marketing analytics",
    "category insights",
    "strategy",
    "operations",
    "market research",
    "predictive analytics",
    "machine learning",
    "ai",
]

ATS_EXTRA_PHRASES = [
    "assortment",
    "business acumen",
    "category advisor",
    "category growth framework",
    "category growth strategy",
    "change management",
    "commercial development",
    "competitive dynamics",
    "fmcg",
    "go-to-market",
    "in-store execution",
    "innovation launch",
    "insight-led selling stories",
    "joint business planning",
    "market analytics",
    "market trends",
    "nielsen",
    "nielseniq",
    "p&l understanding",
    "pricing",
    "promotions",
    "retail partners",
    "revenue management",
    "shopper behaviour",
    "shopper behavior",
    "shopper understanding",
    "shopper-first",
    "supply chain",
    "syndicated data",
]

ATS_KEYWORD_VOCABULARY = sorted(set(DEFAULT_SKILL_PHRASES + ATS_EXTRA_PHRASES))


def score_job(job: JobPosting, resume_bank: ResumeBank, config: dict[str, Any]) -> ScoreResult:
    job_text = job.full_text()
    job_keywords = extract_keywords(job_text, DEFAULT_SKILL_PHRASES, top_n=70)
    coverage, matched_keywords, missing_keywords = weighted_coverage(job_keywords[:45], resume_bank.profile_text)

    role_fit = _role_fit(job, config)
    skill_match = clamp_score(coverage * 10.0)
    experience_match = clamp_score(10.0 * min(1.0, cosine_similarity(job_text, resume_bank.profile_text) * 3.2))
    domain_fit = _domain_fit(job_text, resume_bank.profile_text)
    seniority_location_fit = _seniority_location_fit(job, config)
    ats_keyword_coverage = clamp_score(coverage * 10.0)

    overall = clamp_score(
        role_fit * 0.20
        + skill_match * 0.25
        + experience_match * 0.20
        + domain_fit * 0.15
        + seniority_location_fit * 0.10
        + ats_keyword_coverage * 0.10
    )

    best_resume = resume_bank.best_resume_for_job(job_text, config.get("preferred_resume_templates", []))
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
        matched_resume_path=best_resume.path,
        notes=notes,
    )


def estimate_resume_ats_score(job: JobPosting, resume_text: str, config: dict[str, Any] | None = None) -> float:
    """Estimate LinkedIn-style ATS fit on a 100-point weighted scale.

    The weights follow the user's requested scoring model:
    keywords 50, experience 20, education 20, formatting 10.
    """

    config = config or {}
    job_text = job.full_text()
    job_keywords = extract_keywords(job_text, ATS_KEYWORD_VOCABULARY, top_n=70)
    keyword_coverage, _matched, _missing = weighted_coverage(job_keywords[:55], resume_text)

    keyword_points = keyword_coverage * 50.0
    experience_points = _experience_match_ratio(job.description, resume_text, config) * 20.0
    education_points = _education_match_ratio(job.description, resume_text) * 20.0
    formatting_points = _formatting_match_ratio(resume_text) * 10.0

    return round(keyword_points + experience_points + education_points + formatting_points, 1)


def _role_fit(job: JobPosting, config: dict[str, Any]) -> float:
    title_text = normalize_text(f"{job.title} {job.description[:600]}")
    target_terms = config.get("target_role_keywords", [])
    if not target_terms:
        return 7.0
    hits = sum(1 for term in target_terms if phrase_in_text(term, title_text))
    score = 5.2 + min(4.8, hits * 1.15)
    title = normalize_text(job.title)
    if any(term in title for term in ["analyst", "insights", "strategy", "analytics", "business intelligence"]):
        score += 1.0
    return clamp_score(score)


def _domain_fit(job_text: str, profile_text: str) -> float:
    job_domains = [term for term in DOMAIN_TERMS if phrase_in_text(term, job_text)]
    if not job_domains:
        return 6.5
    profile_hits = [term for term in job_domains if phrase_in_text(term, profile_text)]
    return clamp_score(4.5 + 5.5 * (len(profile_hits) / len(job_domains)))


def _seniority_location_fit(job: JobPosting, config: dict[str, Any]) -> float:
    title = normalize_text(job.title)
    description = normalize_text(job.description[:1000])
    location = normalize_text(job.location)
    score = 8.0

    for term in config.get("seniority_keywords_to_penalize", []):
        if phrase_in_text(term, title):
            score -= 2.0
        elif phrase_in_text(term, description):
            score -= 0.75

    if any(term in title for term in ["intern", "co-op", "coop"]):
        score -= 2.5

    target_locations = [normalize_text(x) for x in config.get("target_locations", [])]
    if target_locations:
        if any(term and term in location for term in target_locations):
            score += 1.5
        elif "remote" in location or "hybrid" in location:
            score += 1.0
        elif location:
            score -= 0.5

    return clamp_score(score)


def _build_notes(job_keywords: list[str], matched: list[str], missing: list[str]) -> str:
    top_match = ", ".join(matched[:8]) if matched else "Limited direct keyword overlap"
    top_missing = ", ".join(missing[:8]) if missing else "No major keyword gaps detected"
    return f"Matched: {top_match}. Gaps: {top_missing}. JD terms reviewed: {len(job_keywords)}."


def _experience_match_ratio(job_description: str, resume_text: str, config: dict[str, Any]) -> float:
    required_years = _required_years(job_description)
    candidate_years = float(config.get("candidate_experience_years") or _candidate_years(resume_text) or 3.9)
    if required_years <= 0:
        return 0.90
    return max(0.35, min(1.0, candidate_years / required_years))


def _education_match_ratio(job_description: str, resume_text: str) -> float:
    jd = normalize_text(job_description)
    resume = normalize_text(resume_text)
    if not any(term in jd for term in ["degree", "bachelor", "masters", "master", "mba", "university"]):
        return 1.0
    if "mba" in jd and "mba" in resume:
        return 1.0
    if any(term in resume for term in ["mba", "bachelor", "b.tech", "bachelor of technology", "master"]):
        return 1.0
    return 0.65


def _required_years(job_description: str) -> float:
    text = normalize_text(job_description)
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*(?:years|yrs)", text)
    if range_match:
        return float(range_match.group(1))
    matches = [float(item) for item in re.findall(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years|yrs)", text)]
    if not matches:
        return 0.0
    return min(matches)


def _candidate_years(resume_text: str) -> float:
    text = normalize_text(resume_text)
    matches = [float(item) for item in re.findall(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years|yrs)", text)]
    return max(matches) if matches else 0.0


def _formatting_match_ratio(resume_text: str) -> float:
    normalized = normalize_text(resume_text)
    checks = [
        "professional summary" in normalized or "summary" in normalized,
        "experience" in normalized or "work experience" in normalized,
        "education" in normalized,
        "skills" in normalized or "core competencies" in normalized,
        "@" in resume_text and ("linkedin" in normalized or "phone" in normalized or re.search(r"\d{3}.*\d{3}.*\d{4}", resume_text)),
    ]
    section_score = sum(1 for item in checks if item) / len(checks)
    word_count = len(normalized.split())
    length_score = 1.0 if 450 <= word_count <= 1200 else 0.75 if 300 <= word_count <= 1450 else 0.55
    return max(0.55, min(1.0, section_score * 0.75 + length_score * 0.25))
