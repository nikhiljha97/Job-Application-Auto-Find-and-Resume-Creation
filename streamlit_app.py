from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
JOBS_JSON = OUTPUT_DIR / "data" / "jobs.json"
SCORES_JSON = OUTPUT_DIR / "data" / "scores.json"

st.set_page_config(
    page_title="Job Scanner",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── helpers ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    example = PROJECT_ROOT / "config.example.json"
    if example.exists():
        with open(example) as f:
            return json.load(f)
    return {}


def save_config(data: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def save_env(env: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in env.items() if v]
    ENV_PATH.write_text("\n".join(lines) + "\n")


def load_jobs() -> dict:
    if JOBS_JSON.exists():
        with open(JOBS_JSON) as f:
            return json.load(f)
    return {}


def load_scores() -> dict:
    if SCORES_JSON.exists():
        with open(SCORES_JSON) as f:
            return json.load(f)
    return {}


def score_color(score: float) -> str:
    if score >= 8:
        return "🟢"
    if score >= 6:
        return "🟡"
    return "🔴"


# ── sidebar nav ──────────────────────────────────────────────────────────────

page = st.sidebar.radio(
    "Navigation",
    ["🏠 Dashboard", "▶️ Run Scanner", "📋 Results", "⚙️ Configuration", "🔔 Notifications"],
    label_visibility="collapsed",
)

st.sidebar.divider()
config_exists = CONFIG_PATH.exists()
st.sidebar.caption(f"Config: {'✅ found' if config_exists else '⚠️ missing — edit Configuration first'}")
jobs = load_jobs()
scores = load_scores()
st.sidebar.caption(f"Jobs in state: {len(jobs)}")
excel_path = OUTPUT_DIR / "linkedin_job_results.xlsx"
if excel_path.exists():
    mtime = time.strftime("%b %d %H:%M", time.localtime(excel_path.stat().st_mtime))
    st.sidebar.caption(f"Last Excel: {mtime}")

# ── dashboard ────────────────────────────────────────────────────────────────

if page == "🏠 Dashboard":
    st.title("Job Application Scanner")
    st.caption("LinkedIn job scanner — scan, score, and generate tailored resumes")

    cfg = load_config()
    min_score = float(cfg.get("min_score", 6.0))

    ranked = sorted(
        [
            (jid, job, scores[jid])
            for jid, job in jobs.items()
            if jid in scores and scores[jid].get("overall_score", 0) >= min_score
            and job.get("accepting_applications", True)
        ],
        key=lambda x: -x[2].get("overall_score", 0),
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Jobs Tracked", len(jobs))
    col2.metric(f"Jobs ≥ {min_score}", len(ranked))
    with_resumes = sum(1 for _, _, s in ranked if s.get("resume_path"))
    col3.metric("Resumes Generated", with_resumes)
    applied = sum(1 for j in jobs.values() if j.get("application_status") == "Applied")
    col4.metric("Applied", applied)

    if not ranked:
        st.info("No results yet. Run the scanner from the **▶️ Run Scanner** page.")
    else:
        st.subheader("Top Matches")
        for jid, job, score in ranked[:20]:
            overall = score.get("overall_score", 0)
            with st.expander(f"{score_color(overall)} {overall:.1f}/10 — {job.get('title')} @ {job.get('company')} ({job.get('location', '')})"):
                cols = st.columns([2, 1, 1, 1])
                cols[0].markdown(f"**[View on LinkedIn]({job.get('url', '#')})**")
                cols[1].metric("Role Fit", f"{score.get('role_fit', 0):.1f}")
                cols[2].metric("Skill Match", f"{score.get('skill_match', 0):.1f}")
                cols[3].metric("ATS Score", f"{score.get('resume_ats_score', 0):.0f}/100")

                matched = score.get("matched_keywords", [])
                missing = score.get("missing_keywords", [])
                if matched:
                    st.markdown("**Matched keywords:** " + ", ".join(f"`{k}`" for k in matched[:12]))
                if missing:
                    st.markdown("**Gaps:** " + ", ".join(f"`{k}`" for k in missing[:8]))

                resume_path = score.get("resume_path", "")
                cover_path = score.get("cover_letter_path", "")
                onedrive_url = score.get("onedrive_doc_url", "")

                dl_cols = st.columns(4)
                if resume_path and Path(resume_path).exists():
                    with open(resume_path, "rb") as fh:
                        dl_cols[0].download_button(
                            "⬇ Resume DOCX",
                            fh.read(),
                            file_name=Path(resume_path).name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"resume_{jid}",
                        )
                if cover_path and Path(cover_path).exists():
                    with open(cover_path, "rb") as fh:
                        dl_cols[1].download_button(
                            "⬇ Cover Letter",
                            fh.read(),
                            file_name=Path(cover_path).name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"cover_{jid}",
                        )
                if onedrive_url:
                    dl_cols[2].link_button("☁ OneDrive Resume", onedrive_url)
                if score.get("onedrive_cover_letter_url"):
                    dl_cols[3].link_button("☁ OneDrive Cover Letter", score["onedrive_cover_letter_url"])

    if excel_path.exists():
        st.divider()
        with open(excel_path, "rb") as fh:
            st.download_button(
                "⬇ Download Full Excel Results",
                fh.read(),
                file_name="linkedin_job_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

# ── run scanner ──────────────────────────────────────────────────────────────

elif page == "▶️ Run Scanner":
    st.title("Run Scanner")

    if not config_exists:
        st.error("No `config.json` found. Go to **⚙️ Configuration** and save your settings first.")
        st.stop()

    st.info(
        "**Cloud note:** The scanner runs headless (no visible browser). "
        "For the first LinkedIn run you need a pre-existing `.linkedin_profile/` session in the repo, "
        "or use **Sample mode** to test without LinkedIn."
    )

    col1, col2 = st.columns(2)
    sample_mode = col1.checkbox("Sample mode (no LinkedIn, built-in test jobs)", value=False)
    no_resumes = col2.checkbox("Skip resume/cover letter generation", value=False)

    cfg = load_config()
    max_pages = st.slider(
        "Max LinkedIn pages to scan",
        min_value=1, max_value=20,
        value=int(cfg.get("max_pages", 6)),
        disabled=sample_mode,
    )

    if st.button("🚀 Start Scan", type="primary"):
        cmd = [sys.executable, "-u", str(PROJECT_ROOT / "run_job_scanner.py"), "--once", "--headless"]
        if sample_mode:
            cmd.append("--sample")
        if no_resumes:
            cmd.append("--no-resumes")
        if not sample_mode:
            cmd += ["--max-pages", str(max_pages)]

        st.subheader("Scan Log")
        log_area = st.empty()
        log_lines: list[str] = []

        def _stream(proc: subprocess.Popen, q: queue.Queue) -> None:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                q.put(line)
            q.put(None)

        with st.spinner("Running scanner…"):
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(PROJECT_ROOT),
                env=env,
            )
            q: queue.Queue = queue.Queue()
            t = threading.Thread(target=_stream, args=(proc, q), daemon=True)
            t.start()

            while True:
                try:
                    line = q.get(timeout=0.2)
                except queue.Empty:
                    if proc.poll() is not None:
                        break
                    continue
                if line is None:
                    break
                log_lines.append(line.rstrip())
                log_area.code("\n".join(log_lines[-80:]), language="text")

            proc.wait()

        if proc.returncode == 0:
            st.success("Scan completed successfully. Check the **📋 Results** tab.")
        else:
            st.error(f"Scanner exited with code {proc.returncode}. Check the log above for errors.")

# ── results ──────────────────────────────────────────────────────────────────

elif page == "📋 Results":
    st.title("Results")

    jobs = load_jobs()
    scores = load_scores()

    if not jobs:
        st.info("No jobs scanned yet. Run the scanner first.")
        st.stop()

    cfg = load_config()
    min_score = float(cfg.get("min_score", 6.0))

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    min_filter = filter_col1.slider("Min score", 0.0, 10.0, min_score, 0.5)
    search_term = filter_col2.text_input("Search title / company", "")
    show_closed = filter_col3.checkbox("Show closed / no longer accepting", False)

    all_rows = []
    for jid, job in jobs.items():
        score = scores.get(jid, {})
        if score.get("overall_score", 0) < min_filter:
            continue
        if not show_closed and not job.get("accepting_applications", True):
            continue
        if search_term:
            haystack = f"{job.get('title','')} {job.get('company','')}".lower()
            if search_term.lower() not in haystack:
                continue
        all_rows.append((jid, job, score))

    all_rows.sort(key=lambda x: -x[2].get("overall_score", 0))
    st.caption(f"Showing {len(all_rows)} jobs")

    if not all_rows:
        st.info("No jobs match the current filters.")
        st.stop()

    import pandas as pd

    table_data = []
    for jid, job, score in all_rows:
        table_data.append({
            "Score": score.get("overall_score", 0),
            "Title": job.get("title", ""),
            "Company": job.get("company", ""),
            "Location": job.get("location", ""),
            "Applicants": job.get("applicant_count_text", ""),
            "Easy Apply": "✅" if job.get("easy_apply") else "",
            "Resume": "✅" if score.get("resume_path") and Path(score["resume_path"]).exists() else "",
            "ATS": score.get("resume_ats_score", 0),
            "URL": job.get("url", ""),
        })

    df = pd.DataFrame(table_data)
    st.dataframe(
        df.drop(columns=["URL"]),
        use_container_width=True,
        column_config={
            "Score": st.column_config.NumberColumn(format="%.1f"),
            "ATS": st.column_config.NumberColumn(format="%.0f"),
        },
        hide_index=True,
    )

    st.subheader("Job Detail")
    titles = [f"{r[2].get('overall_score',0):.1f} — {r[1].get('title','')} @ {r[1].get('company','')}" for r in all_rows]
    selected = st.selectbox("Select a job", titles, index=0)
    idx = titles.index(selected)
    _, sel_job, sel_score = all_rows[idx]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Overall", f"{sel_score.get('overall_score',0):.1f}/10")
    c2.metric("Role Fit", f"{sel_score.get('role_fit',0):.1f}")
    c3.metric("Skill Match", f"{sel_score.get('skill_match',0):.1f}")
    c4.metric("ATS Score", f"{sel_score.get('resume_ats_score',0):.0f}/100")

    st.markdown(f"**[Open on LinkedIn]({sel_job.get('url','#')})**  |  {sel_job.get('location','')}  |  {sel_job.get('applicant_count_text','')}  |  Listed: {sel_job.get('listed_at','')}")

    matched = sel_score.get("matched_keywords", [])
    missing = sel_score.get("missing_keywords", [])
    kw_col1, kw_col2 = st.columns(2)
    kw_col1.markdown("**Matched keywords**\n\n" + "  ".join(f"`{k}`" for k in matched[:20]))
    kw_col2.markdown("**Keyword gaps**\n\n" + "  ".join(f"`{k}`" for k in missing[:15]))

    with st.expander("Job Description"):
        st.write(sel_job.get("description", ""))

    dl1, dl2, dl3, dl4 = st.columns(4)
    resume_path = sel_score.get("resume_path", "")
    cover_path = sel_score.get("cover_letter_path", "")
    outreach_path = sel_score.get("cold_outreach_path", "")
    if resume_path and Path(resume_path).exists():
        with open(resume_path, "rb") as fh:
            dl1.download_button("⬇ Resume", fh.read(), Path(resume_path).name,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key="detail_resume")
    if cover_path and Path(cover_path).exists():
        with open(cover_path, "rb") as fh:
            dl2.download_button("⬇ Cover Letter", fh.read(), Path(cover_path).name,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key="detail_cover")
    if outreach_path and Path(outreach_path).exists():
        with open(outreach_path, "rb") as fh:
            dl3.download_button("⬇ Cold Outreach", fh.read(), Path(outreach_path).name,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key="detail_outreach")
    if sel_score.get("onedrive_doc_url"):
        dl4.link_button("☁ OneDrive Resume", sel_score["onedrive_doc_url"])

# ── configuration ────────────────────────────────────────────────────────────

elif page == "⚙️ Configuration":
    st.title("Configuration")
    st.caption("Changes are saved to `config.json` in the project root.")

    cfg = load_config()

    with st.form("config_form"):
        st.subheader("LinkedIn Search")
        cfg["search_query"] = st.text_input("Search query", cfg.get("search_query", ""))
        cfg["linkedin_location"] = st.text_input("Location", cfg.get("linkedin_location", "Canada"))
        cfg["max_pages"] = st.number_input("Max pages to scan", 1, 50, int(cfg.get("max_pages", 6)))
        cfg["date_posted"] = st.selectbox(
            "Date posted",
            ["Past 24 hours", "Past Week", "Past Month", "Any time"],
            index=["Past 24 hours", "Past Week", "Past Month", "Any time"].index(
                cfg.get("date_posted", "Past 24 hours")
            ),
        )

        employment_options = ["Full-time", "Part-time", "Contract", "Temporary", "Volunteer", "Other"]
        cfg["employment_types"] = st.multiselect(
            "Employment types",
            employment_options,
            default=cfg.get("employment_types", ["Full-time"]),
        )
        exp_options = ["Entry-level", "Associate", "Mid-Senior level", "Director", "Executive", "Manager", "Internship"]
        cfg["experience_levels"] = st.multiselect(
            "Experience levels",
            exp_options,
            default=cfg.get("experience_levels", ["Entry-level", "Manager"]),
        )

        st.subheader("Scoring & Filtering")
        cfg["min_score"] = st.slider("Minimum fit score to include", 0.0, 10.0, float(cfg.get("min_score", 6.0)), 0.1)
        cfg["max_resumes_per_run"] = st.number_input(
            "Max resumes per run", 1, 200, int(cfg.get("max_resumes_per_run", 80))
        )
        cfg["candidate_experience_years"] = st.number_input(
            "Your years of experience", 0.0, 40.0, float(cfg.get("candidate_experience_years", 3.9)), 0.1
        )
        cfg["max_required_experience_years"] = st.number_input(
            "Max required experience (filter out senior roles)", 0.0, 40.0,
            float(cfg.get("max_required_experience_years", 5.99)), 0.1
        )

        loc_default = cfg.get("target_locations", [])
        cfg["target_locations"] = st.text_area(
            "Target locations (one per line)",
            "\n".join(loc_default),
        ).splitlines()

        kw_default = cfg.get("target_role_keywords", [])
        cfg["target_role_keywords"] = st.text_area(
            "Target role keywords (one per line)",
            "\n".join(kw_default),
        ).splitlines()

        penalise_default = cfg.get("seniority_keywords_to_penalize", [])
        cfg["seniority_keywords_to_penalize"] = st.text_area(
            "Seniority keywords to penalise (one per line)",
            "\n".join(penalise_default),
        ).splitlines()

        st.subheader("OneDrive")
        one = cfg.setdefault("onedrive", {})
        one["enabled"] = st.checkbox("Enable OneDrive", bool(one.get("enabled", True)))
        one["client_id"] = st.text_input("OneDrive App Client ID", one.get("client_id", ""))
        one["tenant_id"] = st.text_input("Tenant ID", one.get("tenant_id", "consumers"))
        one["resume_folder_path"] = st.text_input("Resume folder path", one.get("resume_folder_path", "Job Scan/Resumes"))
        one["excel_folder_path"] = st.text_input("Excel folder path", one.get("excel_folder_path", "Job Scan"))

        st.subheader("Advanced")
        cfg["headless"] = st.checkbox(
            "Run browser headless (required on cloud / servers)",
            bool(cfg.get("headless", True)),
        )

        submitted = st.form_submit_button("💾 Save Configuration", type="primary")

    if submitted:
        save_config(cfg)
        st.success("Configuration saved to `config.json`.")

    with st.expander("Raw JSON (read-only)"):
        st.json(cfg)

# ── notifications ────────────────────────────────────────────────────────────

elif page == "🔔 Notifications":
    st.title("Notifications")
    st.caption("Configure `.env` credentials and enable channels in `config.json`.")

    cfg = load_config()
    env = load_env()

    with st.form("notif_form"):
        st.subheader("Telegram")
        notif = cfg.setdefault("notifications", {})
        tg = notif.setdefault("telegram", {})
        tg["enabled"] = st.checkbox("Enable Telegram", bool(tg.get("enabled", False)))
        env["JOB_SCANNER_TELEGRAM_BOT_TOKEN"] = st.text_input(
            "Bot token", env.get("JOB_SCANNER_TELEGRAM_BOT_TOKEN", ""), type="password"
        )
        env["JOB_SCANNER_TELEGRAM_CHAT_ID"] = st.text_input(
            "Chat ID", env.get("JOB_SCANNER_TELEGRAM_CHAT_ID", "")
        )

        st.subheader("Email (SMTP)")
        em = notif.setdefault("email", {})
        em["enabled"] = st.checkbox("Enable email", bool(em.get("enabled", False)))
        env["JOB_SCANNER_SMTP_HOST"] = st.text_input("SMTP host", env.get("JOB_SCANNER_SMTP_HOST", "smtp.gmail.com"))
        env["JOB_SCANNER_SMTP_PORT"] = st.text_input("SMTP port", env.get("JOB_SCANNER_SMTP_PORT", "587"))
        env["JOB_SCANNER_SMTP_USERNAME"] = st.text_input("SMTP username", env.get("JOB_SCANNER_SMTP_USERNAME", ""))
        env["JOB_SCANNER_SMTP_PASSWORD"] = st.text_input("SMTP password", env.get("JOB_SCANNER_SMTP_PASSWORD", ""), type="password")
        env["JOB_SCANNER_EMAIL_FROM"] = st.text_input("From address", env.get("JOB_SCANNER_EMAIL_FROM", ""))
        env["JOB_SCANNER_EMAIL_TO"] = st.text_input("To address", env.get("JOB_SCANNER_EMAIL_TO", ""))

        st.subheader("WhatsApp (Twilio)")
        wa = notif.setdefault("whatsapp_twilio", {})
        wa["enabled"] = st.checkbox("Enable WhatsApp", bool(wa.get("enabled", False)))
        env["JOB_SCANNER_TWILIO_ACCOUNT_SID"] = st.text_input(
            "Account SID", env.get("JOB_SCANNER_TWILIO_ACCOUNT_SID", ""), type="password"
        )
        env["JOB_SCANNER_TWILIO_AUTH_TOKEN"] = st.text_input(
            "Auth token", env.get("JOB_SCANNER_TWILIO_AUTH_TOKEN", ""), type="password"
        )
        env["JOB_SCANNER_TWILIO_FROM"] = st.text_input(
            "From (WhatsApp number)", env.get("JOB_SCANNER_TWILIO_FROM", "whatsapp:+14155238886")
        )
        env["JOB_SCANNER_TWILIO_TO"] = st.text_input(
            "Your WhatsApp number", env.get("JOB_SCANNER_TWILIO_TO", "whatsapp:+1YOURNUMBER")
        )

        cfg["notification_top_n"] = st.number_input(
            "Top N jobs to include in notification", 1, 50,
            int(cfg.get("notification_top_n", 10))
        )
        cfg["notify_only_new_jobs"] = st.checkbox(
            "Only notify for new jobs (not re-notified on each run)",
            bool(cfg.get("notify_only_new_jobs", True)),
        )

        save_notif = st.form_submit_button("💾 Save Notification Settings", type="primary")

    if save_notif:
        save_config(cfg)
        save_env(env)
        st.success("Notification settings saved.")
