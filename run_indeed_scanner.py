from __future__ import annotations

import argparse
import sys
from pathlib import Path

from linkedin_job_scanner.config import PROJECT_ROOT, load_config
from linkedin_job_scanner.env_loader import load_env_file
from linkedin_job_scanner.excel_report import read_excel_application_status, write_excel_report
from linkedin_job_scanner.indeed import IndeedScanner
from linkedin_job_scanner.job_filters import is_actionable_job
from linkedin_job_scanner.models import applicant_sort_value
from linkedin_job_scanner.resume_bank import ResumeBank
from linkedin_job_scanner.resume_writer import create_tailored_resume, ensure_tailored_companion_materials
from linkedin_job_scanner.scoring import estimate_resume_ats_score, score_job
from linkedin_job_scanner.state import ScannerState


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Indeed jobs, score fit, and tailor resumes.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"))
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--no-resumes", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    load_env_file(config)
    if args.max_pages:
        config["indeed_max_pages"] = args.max_pages
    config["headless"] = True

    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    excel_path = output_dir / "linkedin_job_results.xlsx"
    state = ScannerState(output_dir)

    application_status = read_excel_application_status(excel_path)

    print("Building resume bank…")
    resume_bank = ResumeBank.build(config["resume_root"], config)
    print(f"Loaded {len(resume_bank.documents)} resume documents.")

    existing_jobs = state.load_jobs()
    existing_keys = set(existing_jobs)

    print("Scanning Indeed…")
    scanned_jobs = IndeedScanner(config, known_job_keys=existing_keys).scan()
    print(f"Scanned {len(scanned_jobs)} Indeed jobs.")

    for job in scanned_jobs:
        existing_jobs[job.key()] = job

    scores = state.load_scores()
    print(f"Scoring {len(existing_jobs)} jobs…")
    for i, job in enumerate(existing_jobs.values(), 1):
        scores[job.key()] = score_job(job, resume_bank, config)
        if i % 50 == 0 or i == len(existing_jobs):
            print(f"Scored {i}/{len(existing_jobs)}")

    min_score = float(config.get("min_score", 6.0))
    ranked_jobs = sorted(
        [j for j in existing_jobs.values() if scores[j.key()].overall_score >= min_score and is_actionable_job(j, config)],
        key=lambda item: (applicant_sort_value(item), -scores[item.key()].overall_score),
    )

    if not args.no_resumes:
        created = 0
        max_resumes = int(config.get("max_resumes_per_run", 25))
        for job in ranked_jobs:
            if created >= max_resumes:
                break
            score = scores[job.key()]
            if score.resume_path and Path(score.resume_path).exists():
                try:
                    ensure_tailored_companion_materials(job, score, resume_bank, output_dir, config)
                except Exception as exc:
                    print(f"Companion material failed: {exc}", file=sys.stderr)
                continue
            resume_path, resume_text = create_tailored_resume(job, score, resume_bank, output_dir, config)
            score.resume_path = resume_path
            score.resume_ats_score = estimate_resume_ats_score(job, resume_text, config)
            created += 1
        print(f"Created {created} tailored resumes.")

    state.save_jobs(existing_jobs)
    state.save_scores(scores)
    write_excel_report(existing_jobs.values(), scores, excel_path, min_score, application_status, config)
    print(f"Excel updated: {excel_path}")
    print(f"Indeed jobs matching score >= {min_score}: {len(ranked_jobs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
