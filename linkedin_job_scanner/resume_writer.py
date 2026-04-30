from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .models import JobPosting, ScoreResult
from .resume_bank import ResumeBank, is_section_heading, read_docx_paragraphs
from .text_utils import DEFAULT_SKILL_PHRASES, safe_filename


SUMMARY_HEADINGS = {"professional summary", "summary"}
SKILL_HEADINGS = {"core competencies", "core competencies and skills", "skills", "technical skills"}
SUMMARY_DISPLAY_TERMS = set(DEFAULT_SKILL_PHRASES) | {
    "analytics",
    "insights",
    "strategy",
    "category growth strategy",
    "commercial development",
    "competitive dynamics",
    "fmcg",
    "go-to-market",
    "in-store execution",
    "joint business planning",
    "market analytics",
    "nielsen",
    "nielseniq",
    "pricing",
    "promotions",
    "revenue management",
    "shopper behaviour",
    "shopper behavior",
    "syndicated data",
}
SUMMARY_BLOCKED_TERMS = {"r", "client", "partner", "senior", "manager", "associate"}
CORE_POSITIONING_TITLE = "Category & Shopper Strategy / Business Analysis & Insights"
CORE_POSITIONING_SUMMARY = (
    "Category & Shopper Strategy / Business Analysis & Insights professional with an MBA in Finance from "
    "McMaster University and 4+ years of analytics experience across FMCG/CPG, retail media, financial "
    "services, telecom, and technology. At Loblaw Advance, supported Confectionery and Beauty category "
    "insights for Tier 1 CPG clients including Hershey, Lindt, Mondelez, L'Oreal, Nestle, and Ferrero on "
    "Canada's 41M+ member PC Optimum loyalty platform."
)
CORE_POSITIONING_IMPACT = (
    "Experienced translating NielsenIQ/Nielsen RMC, POS, panel, campaign, loyalty, and behavioral data into "
    "category growth strategies, shopper insights, assortment recommendations, pricing and promotions analysis, "
    "executive storytelling, KPI dashboards, and scalable analytics workflows. Proven impact includes $24M+ in "
    "category growth opportunities, $15M in retention opportunities, $9M in organic growth potential, 40%+ "
    "faster decision-making, $38M in revenue protected, 95% manual effort reduction, and 26% analytics adoption uplift."
)
FOUNDATIONAL_COMPETENCIES = {
    "Category, Shopper & Market Insights": [
        "category management",
        "category insights",
        "shopper insights",
        "consumer insights",
        "market share analysis",
        "competitive analysis",
        "retail analytics",
        "marketing analytics",
    ],
    "Analytics, BI & Modelling": [
        "sql",
        "python",
        "power bi",
        "tableau",
        "looker studio",
        "forecasting",
        "segmentation",
        "a/b testing",
    ],
    "Strategy, Stakeholders & Delivery": [
        "strategy",
        "business intelligence",
        "executive reporting",
        "stakeholder management",
        "cross-functional collaboration",
        "kpi reporting",
        "process automation",
        "agile",
    ],
    "AI, Automation & Advanced Analytics": [
        "ai-powered analytics",
        "ai agent workflows",
        "machine learning",
        "predictive analytics",
        "statistical modelling",
        "nlp",
        "etl",
        "financial modelling",
    ],
}


def create_tailored_resume(
    job: JobPosting,
    score: ScoreResult,
    resume_bank: ResumeBank,
    output_dir: str | Path,
    config: dict[str, Any],
) -> tuple[str, str]:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("python-docx is required. Run: pip install -r job_scanner/requirements.txt") from exc

    override_template = config.get("generation_resume_template")
    template_path = Path(score.matched_resume_path)
    if override_template:
        candidate = (resume_bank.root / str(override_template)).resolve()
        if candidate.exists():
            template_path = candidate
    if not template_path.exists():
        template_path = Path(resume_bank.best_resume_for_job(job.full_text(), config.get("preferred_resume_templates", [])).path)
    resume_dir = Path(output_dir) / "resumes"
    resume_dir.mkdir(parents=True, exist_ok=True)

    job_part = safe_filename(f"{job.company}_{job.title}_{job.job_id}", "linkedin_job")
    output_path = resume_dir / f"{job_part}_Nikhil_Jha_Resume.docx"
    shutil.copy2(template_path, output_path)

    doc = Document(str(output_path))
    supported_keywords = resume_bank.supported_keywords(score.matched_keywords)
    summary_keywords = _summary_display_keywords(supported_keywords, resume_bank.profile_keywords)
    summary_lines = _build_summary(job, summary_keywords)
    competency_lines = _build_competency_lines(supported_keywords, resume_bank.profile_keywords)

    _replace_top_headline(doc, job, summary_keywords)
    _replace_section(doc, SUMMARY_HEADINGS, summary_lines, plain=True)
    if not _replace_section(doc, SKILL_HEADINGS, competency_lines, plain=True):
        _insert_section_after(doc, SUMMARY_HEADINGS, "CORE COMPETENCIES", competency_lines)
    _trim_trailing_incomplete_role(doc)
    _normalize_layout(doc)
    doc.save(str(output_path))

    resume_text = "\n".join(read_docx_paragraphs(output_path))
    return str(output_path), resume_text


def _build_summary(job: JobPosting, supported_keywords: list[str]) -> list[str]:
    role_terms = ", ".join(_title_skill(term) for term in supported_keywords[:8])
    role_phrase = f" Strengths include {role_terms}." if role_terms else ""
    return [CORE_POSITIONING_SUMMARY, f"{CORE_POSITIONING_IMPACT}{role_phrase}"]


def _build_competency_lines(supported_keywords: list[str], profile_keywords: list[str]) -> list[str]:
    allowed = set(DEFAULT_SKILL_PHRASES)
    terms = _unique([term for term in [*supported_keywords, *profile_keywords] if term in allowed])
    lines: list[str] = []
    used: set[str] = set()

    jd_terms = [term for term in supported_keywords if term in allowed and term not in SUMMARY_BLOCKED_TERMS][:10]
    if jd_terms:
        lines.append("Target Role Alignment: " + " | ".join(_title_skill(term) for term in jd_terms))
        used.update(jd_terms)

    for label, desired in FOUNDATIONAL_COMPETENCIES.items():
        matches = [term for term in desired if term not in used]
        matches = matches[:8]
        used.update(matches)
        if matches:
            lines.append(f"{label}: " + " | ".join(_title_skill(term) for term in matches))

    remaining = [term for term in terms if term not in used and term not in SUMMARY_BLOCKED_TERMS][:10]
    if remaining:
        lines.append("Additional ATS Keywords: " + " | ".join(_title_skill(term) for term in remaining))
    return lines[:6]


def _summary_display_keywords(supported_keywords: list[str], profile_keywords: list[str]) -> list[str]:
    terms = _unique([*supported_keywords, *profile_keywords])
    display_terms = [
        term
        for term in terms
        if term in SUMMARY_DISPLAY_TERMS and term not in SUMMARY_BLOCKED_TERMS and len(term) > 1
    ]
    return display_terms[:10]


def _replace_top_headline(doc: Any, job: JobPosting, supported_keywords: list[str]) -> None:
    paragraphs = doc.paragraphs
    summary_index = _find_first_heading_index(paragraphs, SUMMARY_HEADINGS)
    if summary_index is None:
        return
    headline = _headline_for_job(job, supported_keywords)
    for idx in range(1, summary_index):
        text = paragraphs[idx].text.strip()
        lower = text.lower()
        if "@" in lower or "linkedin" in lower or any(char.isdigit() for char in lower[:20]):
            continue
        if 10 <= len(text) <= 180:
            _set_paragraph_text(paragraphs[idx], headline)
            return


def _headline_for_job(job: JobPosting, supported_keywords: list[str]) -> str:
    headline_terms = [
        term
        for term in supported_keywords
        if term not in {"python", "sql", "power bi", "excel", "tableau", "looker studio"}
    ][:3]
    terms = " | ".join(_title_skill(term) for term in headline_terms)
    title = job.title or "Strategy and Analytics"
    if terms:
        return f"{title} | {CORE_POSITIONING_TITLE} | {terms} | SQL, Python, Power BI"
    return f"{title} | {CORE_POSITIONING_TITLE} | SQL, Python, Power BI | MBA Finance"


def _replace_section(doc: Any, headings: set[str], lines: list[str], plain: bool = False) -> bool:
    if not lines:
        return False
    paragraphs = doc.paragraphs
    start = _find_first_heading_index(paragraphs, headings)
    if start is None:
        return False
    end = _find_next_heading_index(paragraphs, start + 1)
    content = paragraphs[start + 1 : end]
    if not content:
        anchor = paragraphs[start]
        for line in lines:
            anchor = _insert_paragraph_after(anchor, line, plain=plain)
        return True

    for paragraph, line in zip(content, lines):
        _set_paragraph_text(paragraph, line, plain=plain)

    if len(lines) > len(content):
        anchor = content[-1]
        for line in lines[len(content) :]:
            anchor = _insert_paragraph_after(anchor, line, style=content[-1].style, plain=plain)
    else:
        for paragraph in reversed(content[len(lines) :]):
            _delete_paragraph(paragraph)
    return True


def _insert_section_after(doc: Any, after_headings: set[str], heading_text: str, lines: list[str]) -> bool:
    paragraphs = doc.paragraphs
    start = _find_first_heading_index(paragraphs, after_headings)
    if start is None:
        return False
    end = _find_next_heading_index(paragraphs, start + 1)
    anchor = paragraphs[end - 1] if end > start else paragraphs[start]
    heading = _insert_paragraph_after(anchor, heading_text, style=paragraphs[start].style)
    if heading.runs:
        heading.runs[0].bold = True
    content_style = paragraphs[start + 1].style if start + 1 < len(paragraphs) else None
    anchor = heading
    for line in lines:
        anchor = _insert_paragraph_after(anchor, line, style=content_style, plain=True)
    return True


def _find_first_heading_index(paragraphs: list[Any], headings: set[str]) -> int | None:
    normalized = {_norm_heading(item) for item in headings}
    for index, paragraph in enumerate(paragraphs):
        if _norm_heading(paragraph.text) in normalized:
            return index
    return None


def _find_next_heading_index(paragraphs: list[Any], start: int) -> int:
    for index in range(start, len(paragraphs)):
        if is_section_heading(paragraphs[index].text.strip()):
            return index
    return len(paragraphs)


def _norm_heading(text: str) -> str:
    return " ".join(text.replace("&", " and ").lower().split())


def _set_paragraph_text(paragraph: Any, text: str, plain: bool = False) -> None:
    _clear_paragraph_content(paragraph)
    run = paragraph.add_run(text)
    if plain:
        run.bold = False
        run.italic = False


def _insert_paragraph_after(paragraph: Any, text: str, style: Any | None = None, plain: bool = False) -> Any:
    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    new_p = OxmlElement("w:p")
    paragraph._p.addnext(new_p)
    new_para = Paragraph(new_p, paragraph._parent)
    if style is not None:
        new_para.style = style
    run = new_para.add_run(text)
    if plain:
        run.bold = False
        run.italic = False
    return new_para


def _clear_paragraph_content(paragraph: Any) -> None:
    from docx.oxml.ns import qn

    for child in list(paragraph._p):
        if child.tag != qn("w:pPr"):
            paragraph._p.remove(child)


def _delete_paragraph(paragraph: Any) -> None:
    element = paragraph._element
    element.getparent().remove(element)
    paragraph._p = paragraph._element = None


def _normalize_layout(doc: Any) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    for section in doc.sections:
        section.top_margin = Inches(0.625)
        section.bottom_margin = Inches(0.625)
        section.left_margin = Inches(0.55)
        section.right_margin = Inches(0.56)

    for index, paragraph in enumerate(doc.paragraphs):
        text = paragraph.text.strip()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        paragraph.paragraph_format.keep_with_next = False
        paragraph.paragraph_format.keep_together = False
        paragraph.paragraph_format.page_break_before = False
        paragraph.paragraph_format.line_spacing = None
        _format_paragraph_runs(paragraph, "Calibri", 9)
        if index == 0 and text:
            _format_paragraph_runs(paragraph, "Calibri", 20, bold=True)
            paragraph.paragraph_format.space_after = Pt(1.5)
        elif index == 1 and text:
            _format_paragraph_runs(paragraph, "Calibri", 13)
            paragraph.paragraph_format.space_after = Pt(1.5)
        elif index == 2 and text:
            _format_paragraph_runs(paragraph, "Calibri", 9)
            paragraph.paragraph_format.space_after = Pt(5)
        elif is_section_heading(text):
            _format_paragraph_runs(paragraph, "Calibri", 11, bold=True)
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(3)
        elif paragraph.style and paragraph.style.name == "List Paragraph":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            paragraph.paragraph_format.space_before = Pt(1)
            paragraph.paragraph_format.space_after = Pt(1)
            _bold_label_prefix(paragraph)
        elif _looks_like_role_header(text):
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(1)
            _bold_before_separator(paragraph, " | ")
        elif text:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            paragraph.paragraph_format.space_before = Pt(1.5)
            paragraph.paragraph_format.space_after = Pt(1.5)
            _bold_label_prefix(paragraph)
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if is_section_heading(text) or _looks_like_role_header(text) or _looks_like_date_line(text):
            paragraph.paragraph_format.keep_with_next = True


def _format_paragraph_runs(paragraph: Any, font_name: str, font_size_pt: int | float, bold: bool | None = None) -> None:
    from docx.shared import Pt

    for run in paragraph.runs:
        run.font.name = font_name
        run.font.size = Pt(font_size_pt)
        if bold is not None:
            run.font.bold = bold


def _bold_label_prefix(paragraph: Any) -> None:
    from docx.shared import Pt

    text = paragraph.text
    colon = text.find(":")
    if colon <= 0 or colon > 70:
        return
    _clear_paragraph_content(paragraph)
    label = paragraph.add_run(text[: colon + 1])
    label.font.name = "Calibri"
    label.font.size = Pt(9)
    label.bold = True
    body = paragraph.add_run(text[colon + 1 :])
    body.font.name = "Calibri"
    body.font.size = Pt(9)


def _bold_before_separator(paragraph: Any, separator: str) -> None:
    from docx.shared import Pt

    text = paragraph.text
    pos = text.find(separator)
    if pos <= 0 or len(paragraph.runs) != 1:
        return
    _clear_paragraph_content(paragraph)
    first = paragraph.add_run(text[:pos])
    first.font.name = "Calibri"
    first.font.size = Pt(9)
    first.bold = True
    rest = paragraph.add_run(text[pos:])
    rest.font.name = "Calibri"
    rest.font.size = Pt(9)


def _trim_trailing_incomplete_role(doc: Any) -> None:
    paragraphs = doc.paragraphs
    nonempty = [(idx, p, p.text.strip()) for idx, p in enumerate(paragraphs) if p.text.strip()]
    if len(nonempty) < 4:
        return

    tail: list[tuple[int, Any, str]] = []
    for idx, paragraph, text in reversed(nonempty):
        if _is_trailing_fragment(text):
            tail.append((idx, paragraph, text))
            continue
        break
    if not tail or not any(_looks_like_role_header(text) for _idx, _p, text in tail):
        return

    first_idx = min(idx for idx, _p, _text in tail)
    if first_idx > 0 and is_section_heading(paragraphs[first_idx - 1].text.strip()):
        first_idx -= 1
    for paragraph in reversed(paragraphs[first_idx:]):
        _delete_paragraph(paragraph)


def _title_skill(term: str) -> str:
    overrides = {
        "ai": "AI",
        "ai agent workflows": "AI Agent Workflows",
        "ai-powered analytics": "AI-Powered Analytics",
        "a/b testing": "A/B Testing",
        "cpg": "CPG",
        "etl": "ETL",
        "fmcg": "FMCG",
        "jira": "Jira",
        "kpi reporting": "KPI Reporting",
        "looker studio": "Looker Studio",
        "nielseniq": "NielsenIQ",
        "nlp": "NLP",
        "okr": "OKR",
        "pos data analysis": "POS Data Analysis",
        "power bi": "Power BI",
        "python": "Python",
        "r": "R",
        "sql": "SQL",
    }
    return overrides.get(term, term.title())


def _looks_like_role_header(text: str) -> bool:
    if len(text) > 130 or len(text) < 12:
        return False
    if ":" in text:
        return False
    if "|" in text and not text.endswith("."):
        return True
    return bool(re.search(r"\b(manager|analyst|engineer|assistant|consultant|intern|associate)\b", text, re.I)) and not text.endswith(".")


def _looks_like_date_line(text: str) -> bool:
    return bool(re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", text, re.I)) and len(text) < 80


def _is_trailing_fragment(text: str) -> bool:
    if is_section_heading(text) or _looks_like_role_header(text) or _looks_like_date_line(text):
        return True
    if len(text) < 130 and "|" in text and not text.endswith("."):
        return True
    return False


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result
