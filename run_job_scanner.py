from __future__ import annotations

import argparse
import plistlib
import sys
import time
from pathlib import Path
from typing import Any

from linkedin_job_scanner.config import PROJECT_ROOT, load_config
from linkedin_job_scanner.env_loader import load_env_file
from linkedin_job_scanner.excel_report import read_excel_application_status, write_excel_report
from linkedin_job_scanner.google_drive import google_drive_ready, upload_docx_as_google_doc
from linkedin_job_scanner.google_sheets import sync_google_sheet
from linkedin_job_scanner.google_sheets import download_trusted_google_resume_sources
from linkedin_job_scanner.linkedin import LinkedInScanner
from linkedin_job_scanner.models import JobPosting, applicant_sort_value
from linkedin_job_scanner.notifications import notify_after_run
from linkedin_job_scanner.onedrive import (
    download_excel_from_onedrive,
    download_trusted_onedrive_resume_sources,
    onedrive_ready,
    upload_docx_to_onedrive,
    upload_excel_to_onedrive,
)
from linkedin_job_scanner.resume_bank import ResumeBank
from linkedin_job_scanner.resume_writer import create_tailored_resume, ensure_tailored_companion_materials
from linkedin_job_scanner.scoring import estimate_resume_ats_score, score_job
from linkedin_job_scanner.state import ScannerState


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan LinkedIn jobs, score fit, tailor resumes, and export Excel results.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"), help="Path to config.json")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit. This is the default.")
    parser.add_argument("--watch", action="store_true", help="Run continuously every configured interval.")
    parser.add_argument("--sample", action="store_true", help="Run against built-in sample jobs without opening LinkedIn.")
    parser.add_argument("--headless", action="store_true", help="Override config and run browser headless.")
    parser.add_argument("--no-resumes", action="store_true", help="Score jobs and write Excel without creating DOCX resumes.")
    parser.add_argument("--max-pages", type=int, help="Override LinkedIn pages to scan.")
    parser.add_argument("--install-launch-agent", action="store_true", help="Install a macOS LaunchAgent for hourly scans.")
    args = parser.parse_args()

    config = load_config(args.config)
    load_env_file(config)
    if args.headless:
        config["headless"] = True
    if args.max_pages:
        config["max_pages"] = args.max_pages

    if args.install_launch_agent:
        path = install_launch_agent(config, args.config)
        print(f"LaunchAgent written to {path}")
        print("Load it with: launchctl load ~/Library/LaunchAgents/com.nikhil.linkedin-job-scanner.plist")
        return 0

    if args.watch:
        interval = max(5, int(config.get("hourly_interval_minutes", 60))) * 60
        while True:
            try:
                run_once(config, sample=args.sample, create_resumes=not args.no_resumes)
            except Exception as exc:
                print(f"Scan failed: {exc}", file=sys.stderr)
            print(f"Sleeping {interval // 60} minutes before the next scan.")
            time.sleep(interval)

    run_once(config, sample=args.sample, create_resumes=not args.no_resumes)
    return 0


def run_once(config: dict[str, Any], sample: bool = False, create_resumes: bool = True) -> None:
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    excel_path = output_dir / "linkedin_job_results.xlsx"
    state = ScannerState(output_dir)

    one_ready, one_reason = onedrive_ready(config)
    if config.get("onedrive", {}).get("enabled") and not one_ready:
        print(f"OneDrive integration pending: {one_reason}")
    if one_ready:
        try:
            if download_excel_from_onedrive(config, excel_path):
                print(f"Downloaded latest OneDrive Excel state: {excel_path}")
        except Exception as exc:
            print(f"OneDrive Excel download skipped: {exc}", file=sys.stderr)
        try:
            download_trusted_onedrive_resume_sources(config, excel_path)
        except Exception as exc:
            print(f"Trusted OneDrive resume download failed: {exc}", file=sys.stderr)

    try:
        download_trusted_google_resume_sources(config)
    except Exception as exc:
        print(f"Trusted Google resume download failed: {exc}", file=sys.stderr)
    drive_ready, drive_reason = google_drive_ready(config)
    if config.get("google_drive", {}).get("enabled") and not drive_ready:
        print(f"Google Docs integration pending: {drive_reason}")
    application_status = read_excel_application_status(excel_path)

    print("Building resume/profile bank from existing DOCX resumes...")
    resume_bank = ResumeBank.build(config["resume_root"], config)
    print(f"Loaded {len(resume_bank.documents)} resume documents.")

    existing_jobs = state.load_jobs()
    existing_keys = set(existing_jobs)
    notified_keys = state.load_notified_keys()
    if sample:
        scanned_jobs = sample_jobs(config.get("search_url", "sample://linkedin"))
    else:
        scanned_jobs = LinkedInScanner(config, known_job_keys=existing_keys).scan()
    print(f"Scanned {len(scanned_jobs)} jobs this run.")

    for job in scanned_jobs:
        existing_jobs[job.key()] = job

    scores = state.load_scores()
    jobs_to_score = list(existing_jobs.values())
    print(f"Scoring {len(jobs_to_score)} saved jobs against resume/profile bank...")
    for index, job in enumerate(jobs_to_score, start=1):
        previous_score = scores.get(job.key())
        refreshed_score = score_job(job, resume_bank, config)
        if previous_score:
            _carry_generated_outputs(previous_score, refreshed_score)
        scores[job.key()] = refreshed_score
        if index == len(jobs_to_score) or index % 50 == 0:
            print(f"Scored {index}/{len(jobs_to_score)} jobs.")

    min_score = float(config.get("min_score", 6.0))
    ranked_jobs = sorted(
        [
            job
            for job in existing_jobs.values()
            if scores[job.key()].overall_score >= min_score
            and job.accepting_applications
        ],
        key=lambda item: (applicant_sort_value(item), -scores[item.key()].overall_score),
    )

    if create_resumes:
        created = 0
        max_resumes = int(config.get("max_resumes_per_run", 25))
        print(f"Preparing resumes/companion docs for up to {max_resumes} ranked jobs.")
        for job in ranked_jobs:
            score = scores[job.key()]
            if score.resume_path:
                existing_resume_path = Path(score.resume_path)
                if _valid_file(existing_resume_path):
                    try:
                        ensure_tailored_companion_materials(job, score, resume_bank, output_dir, config)
                    except Exception as exc:
                        print(f"Companion material generation failed for {existing_resume_path}: {exc}", file=sys.stderr)
                    if drive_ready and not score.google_doc_url:
                        try:
                            doc_id, doc_url = upload_docx_as_google_doc(config, existing_resume_path, existing_resume_path.stem)
                            score.google_doc_id = doc_id
                            score.google_doc_url = doc_url
                        except Exception as exc:
                            print(f"Google Doc upload failed for {existing_resume_path}: {exc}", file=sys.stderr)
                    if one_ready and not score.onedrive_doc_url:
                        try:
                            doc_id, doc_url = upload_docx_to_onedrive(config, existing_resume_path, existing_resume_path.stem)
                            score.onedrive_doc_id = doc_id
                            score.onedrive_doc_url = doc_url
                        except Exception as exc:
                            print(f"OneDrive upload failed for {existing_resume_path}: {exc}", file=sys.stderr)
                    if one_ready:
                        _upload_companion_docs_to_onedrive(config, score)
                    continue
                if existing_resume_path.exists():
                    existing_resume_path.unlink()
                score.resume_path = ""
            if created >= max_resumes:
                break
            resume_path, resume_text = create_tailored_resume(job, score, resume_bank, output_dir, config)
            score.resume_path = resume_path
            score.resume_ats_score = estimate_resume_ats_score(job, resume_text, config)
            if drive_ready and not score.google_doc_url:
                try:
                    doc_id, doc_url = upload_docx_as_google_doc(config, resume_path, Path(resume_path).stem)
                    score.google_doc_id = doc_id
                    score.google_doc_url = doc_url
                except Exception as exc:
                    print(f"Google Doc upload failed for {resume_path}: {exc}", file=sys.stderr)
            if one_ready and not score.onedrive_doc_url:
                try:
                    doc_id, doc_url = upload_docx_to_onedrive(config, resume_path, Path(resume_path).stem)
                    score.onedrive_doc_id = doc_id
                    score.onedrive_doc_url = doc_url
                except Exception as exc:
                    print(f"OneDrive upload failed for {resume_path}: {exc}", file=sys.stderr)
            if one_ready:
                _upload_companion_docs_to_onedrive(config, score)
            created += 1
        print(f"Created {created} tailored resumes.")
    else:
        print("Resume creation skipped.")

    state.save_jobs(existing_jobs)
    state.save_scores(scores)
    write_excel_report(existing_jobs.values(), scores, excel_path, min_score, application_status)
    if one_ready:
        try:
            excel_id, excel_url = upload_excel_to_onedrive(config, excel_path)
            if excel_url:
                print(f"OneDrive Excel results: {excel_url}")
        except Exception as exc:
            print(f"OneDrive Excel upload failed for {excel_path}: {exc}", file=sys.stderr)

    new_matching_jobs = sorted(
        [
            job
            for job in scanned_jobs
            if job.key() in scores
            and job.accepting_applications
            and scores[job.key()].overall_score >= min_score
            and job.key() not in existing_keys
            and (not bool(config.get("notify_only_new_jobs", True)) or job.key() not in notified_keys)
        ],
        key=lambda item: (applicant_sort_value(item), -scores[item.key()].overall_score),
    )

    try:
        sync_google_sheet(config, list(existing_jobs.values()), scores, min_score)
    except Exception as exc:
        print(f"Google Sheets sync failed: {exc}", file=sys.stderr)

    try:
        notify_after_run(config, new_matching_jobs, scores, excel_path)
    except Exception as exc:
        print(f"Notification failed: {exc}", file=sys.stderr)

    notified_keys.update(job.key() for job in new_matching_jobs)
    notified_keys.update(job.key() for job in scanned_jobs if job.key() in scores and scores[job.key()].overall_score >= min_score)
    state.save_notified_keys(notified_keys)

    print(f"Jobs shown with score >= {min_score}: {len(ranked_jobs)}")
    print(f"New matching jobs this run: {len(new_matching_jobs)}")
    print(f"Excel results: {excel_path}")
    if ranked_jobs[:5]:
        print("Top matches:")
        for job in ranked_jobs[:5]:
            score = scores[job.key()]
            applicants = job.applicant_count_text or "applicants unknown"
            print(f"  {score.overall_score:.2f}/10 - {applicants} - {job.title} - {job.company} - {job.url}")


def _upload_companion_docs_to_onedrive(config: dict[str, Any], score: Any) -> None:
    if score.cover_letter_path and not score.onedrive_cover_letter_url:
        cover_path = Path(score.cover_letter_path)
        if cover_path.exists() and cover_path.suffix.lower() == ".docx":
            try:
                doc_id, doc_url = upload_docx_to_onedrive(config, cover_path, cover_path.stem)
                score.onedrive_cover_letter_id = doc_id
                score.onedrive_cover_letter_url = doc_url
            except Exception as exc:
                print(f"OneDrive cover letter upload failed for {cover_path}: {exc}", file=sys.stderr)
    if score.cold_outreach_path and not score.onedrive_cold_outreach_url:
        outreach_path = Path(score.cold_outreach_path)
        if outreach_path.exists() and outreach_path.suffix.lower() == ".docx":
            try:
                doc_id, doc_url = upload_docx_to_onedrive(config, outreach_path, outreach_path.stem)
                score.onedrive_cold_outreach_id = doc_id
                score.onedrive_cold_outreach_url = doc_url
            except Exception as exc:
                print(f"OneDrive cold outreach upload failed for {outreach_path}: {exc}", file=sys.stderr)


def _carry_generated_outputs(previous_score: Any, refreshed_score: Any) -> None:
    fields = [
        "resume_path",
        "resume_ats_score",
        "google_doc_url",
        "google_doc_id",
        "onedrive_doc_url",
        "onedrive_doc_id",
        "cover_letter_path",
        "cold_outreach_path",
        "onedrive_cover_letter_url",
        "onedrive_cover_letter_id",
        "onedrive_cold_outreach_url",
        "onedrive_cold_outreach_id",
    ]
    for field in fields:
        value = getattr(previous_score, field, "")
        if value:
            setattr(refreshed_score, field, value)


def _valid_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def sample_jobs(source_url: str) -> list[JobPosting]:
    return [
        JobPosting(
            job_id="sample-strategy-insights",
            title="Strategy and Consumer Insights Analyst",
            company="Sample Retail Analytics Co.",
            location="Toronto, ON Hybrid",
            url="https://www.linkedin.com/jobs/view/sample-strategy-insights/",
            source_url=source_url,
            description=(
                "Own consumer insights, SQL analysis, Power BI dashboards, market research, KPI reporting, "
                "segmentation, A/B testing, executive storytelling, cross-functional stakeholder management, "
                "and strategy recommendations for retail and CPG partners."
            ),
        ),
        JobPosting(
            job_id="sample-director-ops",
            title="Director of Operations Transformation",
            company="Sample Enterprise",
            location="Vancouver, BC",
            url="https://www.linkedin.com/jobs/view/sample-director-ops/",
            source_url=source_url,
            description=(
                "Lead enterprise transformation, people management, vendor contracts, operational governance, "
                "change management, reporting automation, and executive presentations."
            ),
        ),
    ]


def install_launch_agent(config: dict[str, Any], config_path: str) -> Path:
    logs_dir = Path(config["output_dir"]) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.nikhil.linkedin-job-scanner.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    program = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "run_job_scanner.py"),
        "--once",
        "--config",
        str(Path(config_path).resolve()),
    ]
    if bool(config.get("launch_agent_headless", True)):
        program.append("--headless")
    schedule = config.get("launch_schedule", {})
    payload = {
        "Label": "com.nikhil.linkedin-job-scanner",
        "ProgramArguments": program,
        "WorkingDirectory": str(PROJECT_ROOT),
        "RunAtLoad": bool(schedule.get("run_at_load", False)),
        "StandardOutPath": str(logs_dir / "launchd.out.log"),
        "StandardErrorPath": str(logs_dir / "launchd.err.log"),
    }
    if schedule.get("mode") == "daily_times":
        payload["StartCalendarInterval"] = [_parse_launch_time(value) for value in schedule.get("times", [])]
        if not payload["StartCalendarInterval"]:
            payload["StartCalendarInterval"] = [{"Hour": 8, "Minute": 0}]
    elif schedule.get("mode") == "daily":
        payload["StartCalendarInterval"] = {
            "Hour": int(schedule.get("hour", 8)),
            "Minute": int(schedule.get("minute", 0)),
        }
    else:
        payload["StartInterval"] = int(config.get("hourly_interval_minutes", 60)) * 60
    with plist_path.open("wb") as f:
        plistlib.dump(payload, f)
    return plist_path


def _parse_launch_time(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        return {"Hour": int(value.get("hour", 8)), "Minute": int(value.get("minute", 0))}
    hour_text, _, minute_text = str(value).partition(":")
    return {"Hour": int(hour_text), "Minute": int(minute_text or 0)}


if __name__ == "__main__":
    raise SystemExit(main())
