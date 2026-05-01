from __future__ import annotations

from pathlib import Path
from typing import Any

from .google_drive import export_google_doc_as_docx, extract_google_doc_id
from .models import JobPosting, ScoreResult, applicant_sort_value
from .onedrive import download_onedrive_docx, resolve_onedrive_item_id
from .text_utils import safe_filename


USER_COLUMNS = ["Applied", "Use As Source", "Application Date", "Notes"]

SHEET_HEADERS = [
    "Job ID",
    "Overall Score",
    "ATS Score",
    "Title",
    "Company",
    "Location",
    "Applicants",
    "Applicant Count Text",
    "Application Status",
    "Job Link",
    "Google Resume Link",
    "Google Resume ID",
    "OneDrive Resume Link",
    "OneDrive Resume ID",
    "Local Resume Backup",
    "Applied",
    "Use As Source",
    "Application Date",
    "Notes",
    "Matched Keywords",
    "Missing Keywords",
    "Scraped At",
]


def sync_google_sheet(
    config: dict[str, Any],
    jobs: list[JobPosting],
    scores: dict[str, ScoreResult],
    min_score: float,
) -> None:
    sheet_config = config.get("google_sheets", {})
    if not sheet_config.get("enabled"):
        return

    worksheet = _worksheet(config)
    existing = _existing_rows_by_job_id(worksheet)

    ranked = sorted(
        [
            job
            for job in jobs
            if job.key() in scores
            and scores[job.key()].overall_score >= min_score
            and job.accepting_applications
        ],
        key=lambda item: (applicant_sort_value(item), -scores[item.key()].overall_score),
    )
    rows = [SHEET_HEADERS]
    for job in ranked:
        score = scores[job.key()]
        preserved = existing.get(job.key(), {})
        rows.append(
            [
                job.key(),
                score.overall_score,
                score.resume_ats_score,
                job.title,
                job.company,
                job.location,
                job.applicant_count,
                job.applicant_count_text,
                job.application_status,
                job.url,
                score.google_doc_url,
                score.google_doc_id,
                score.onedrive_doc_url,
                score.onedrive_doc_id,
                score.resume_path,
                preserved.get("Applied", ""),
                preserved.get("Use As Source", ""),
                preserved.get("Application Date", ""),
                preserved.get("Notes", ""),
                ", ".join(score.matched_keywords),
                ", ".join(score.missing_keywords),
                job.scraped_at,
            ]
        )

    worksheet.clear()
    worksheet.update(rows, value_input_option="USER_ENTERED")
    try:
        worksheet.freeze(rows=1)
    except Exception:
        pass
    print(f"Google Sheets synced: {worksheet.title}")


def download_trusted_google_resume_sources(config: dict[str, Any]) -> int:
    sheet_config = config.get("google_sheets", {})
    if not sheet_config.get("enabled"):
        return 0
    trusted_root = Path(str(config.get("trusted_resume_root", ""))).expanduser()
    if not trusted_root:
        return 0

    worksheet = _worksheet(config)
    rows = worksheet.get_all_records()
    count = 0
    for row in rows:
        applied = _truthy(row.get("Applied", ""))
        use_as_source = _truthy(row.get("Use As Source", ""))
        if not applied and not use_as_source:
            continue
        doc_id = str(row.get("Google Resume ID", "")).strip() or extract_google_doc_id(str(row.get("Google Resume Link", "")))
        title = safe_filename(f"{row.get('Company','')}_{row.get('Title','')}_{row.get('Job ID','')}", "trusted_google_resume")
        output_path = trusted_root / f"{title}.docx"
        if doc_id:
            try:
                if export_google_doc_as_docx(config, doc_id, output_path):
                    count += 1
                    continue
            except Exception as exc:
                print(f"Could not export trusted Google Doc {doc_id}: {exc}")
        onedrive_id = str(row.get("OneDrive Resume ID", "")).strip()
        if not onedrive_id and row.get("OneDrive Resume Link"):
            try:
                onedrive_id = resolve_onedrive_item_id(config, str(row.get("OneDrive Resume Link", "")))
            except Exception as exc:
                print(f"Could not resolve trusted OneDrive link: {exc}")
        if onedrive_id:
            try:
                if download_onedrive_docx(config, onedrive_id, output_path):
                    count += 1
            except Exception as exc:
                print(f"Could not download trusted OneDrive DOCX {onedrive_id}: {exc}")
    if count:
        print(f"Downloaded {count} applied/approved Google Docs as trusted source resumes.")
    return count


def _worksheet(config: dict[str, Any]) -> Any:
    try:
        import gspread
    except ImportError as exc:
        raise RuntimeError("Install gspread first: python -m pip install -r requirements.txt") from exc

    sheet_config = config.get("google_sheets", {})
    spreadsheet_id = str(sheet_config.get("spreadsheet_id", "")).strip()
    worksheet_name = str(sheet_config.get("worksheet_name", "LinkedIn Jobs")).strip() or "LinkedIn Jobs"
    service_account_json = str(sheet_config.get("service_account_json", "")).strip()
    if not spreadsheet_id or not service_account_json:
        raise RuntimeError("Set google_sheets.spreadsheet_id and google_sheets.service_account_json in config.json.")

    credentials_path = Path(service_account_json).expanduser()
    if not credentials_path.exists():
        raise RuntimeError(f"Service account file not found: {credentials_path}")

    client = gspread.service_account(filename=str(credentials_path))
    spreadsheet = client.open_by_key(spreadsheet_id)
    try:
        return spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(SHEET_HEADERS))


def _existing_rows_by_job_id(worksheet: Any) -> dict[str, dict[str, Any]]:
    try:
        rows = worksheet.get_all_records()
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        job_id = str(row.get("Job ID", "")).strip()
        if not job_id:
            job_link = str(row.get("Job Link", "")).strip()
            job_id = job_link.rstrip("/").split("/")[-1] if job_link else ""
        if job_id:
            result[job_id] = row
    return result


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"yes", "y", "true", "1", "applied", "source"}
