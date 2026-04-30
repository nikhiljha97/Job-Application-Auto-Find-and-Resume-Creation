from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class JobPosting:
    job_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    source_url: str
    scraped_at: str = field(default_factory=utc_now_iso)
    listed_at: str = ""
    easy_apply: bool = False

    def key(self) -> str:
        return self.job_id or self.url

    def full_text(self) -> str:
        return "\n".join(
            part
            for part in [
                self.title,
                self.company,
                self.location,
                self.description,
            ]
            if part
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "description": self.description,
            "source_url": self.source_url,
            "scraped_at": self.scraped_at,
            "listed_at": self.listed_at,
            "easy_apply": self.easy_apply,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobPosting":
        return cls(
            job_id=str(data.get("job_id", "")),
            title=str(data.get("title", "")),
            company=str(data.get("company", "")),
            location=str(data.get("location", "")),
            url=str(data.get("url", "")),
            description=str(data.get("description", "")),
            source_url=str(data.get("source_url", "")),
            scraped_at=str(data.get("scraped_at", utc_now_iso())),
            listed_at=str(data.get("listed_at", "")),
            easy_apply=bool(data.get("easy_apply", False)),
        )


@dataclass
class ScoreResult:
    job_id: str
    overall_score: float
    role_fit: float
    skill_match: float
    experience_match: float
    domain_fit: float
    seniority_location_fit: float
    ats_keyword_coverage: float
    matched_keywords: list[str]
    missing_keywords: list[str]
    matched_resume_path: str
    resume_path: str = ""
    resume_ats_score: float = 0.0
    google_doc_url: str = ""
    google_doc_id: str = ""
    onedrive_doc_url: str = ""
    onedrive_doc_id: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "overall_score": self.overall_score,
            "role_fit": self.role_fit,
            "skill_match": self.skill_match,
            "experience_match": self.experience_match,
            "domain_fit": self.domain_fit,
            "seniority_location_fit": self.seniority_location_fit,
            "ats_keyword_coverage": self.ats_keyword_coverage,
            "matched_keywords": self.matched_keywords,
            "missing_keywords": self.missing_keywords,
            "matched_resume_path": self.matched_resume_path,
            "resume_path": self.resume_path,
            "resume_ats_score": self.resume_ats_score,
            "google_doc_url": self.google_doc_url,
            "google_doc_id": self.google_doc_id,
            "onedrive_doc_url": self.onedrive_doc_url,
            "onedrive_doc_id": self.onedrive_doc_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScoreResult":
        return cls(
            job_id=str(data.get("job_id", "")),
            overall_score=float(data.get("overall_score", 0)),
            role_fit=float(data.get("role_fit", 0)),
            skill_match=float(data.get("skill_match", 0)),
            experience_match=float(data.get("experience_match", 0)),
            domain_fit=float(data.get("domain_fit", 0)),
            seniority_location_fit=float(data.get("seniority_location_fit", 0)),
            ats_keyword_coverage=float(data.get("ats_keyword_coverage", 0)),
            matched_keywords=list(data.get("matched_keywords", [])),
            missing_keywords=list(data.get("missing_keywords", [])),
            matched_resume_path=str(data.get("matched_resume_path", "")),
            resume_path=str(data.get("resume_path", "")),
            resume_ats_score=float(data.get("resume_ats_score", 0)),
            google_doc_url=str(data.get("google_doc_url", "")),
            google_doc_id=str(data.get("google_doc_id", "")),
            onedrive_doc_url=str(data.get("onedrive_doc_url", "")),
            onedrive_doc_id=str(data.get("onedrive_doc_id", "")),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ResumeDocument:
    path: str
    text: str
    paragraphs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "text": self.text, "paragraphs": self.paragraphs}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResumeDocument":
        return cls(
            path=str(data.get("path", "")),
            text=str(data.get("text", "")),
            paragraphs=list(data.get("paragraphs", [])),
        )
