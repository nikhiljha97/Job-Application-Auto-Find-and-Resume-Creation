from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import streamlit as st

# Force Playwright to use a writable, consistent path for browser binaries.
# Streamlit Cloud runs as 'adminuser' but the default cache path can resolve to
# '/home/appuser/.cache/ms-playwright' causing a user-mismatch. Overriding to /tmp fixes it.
_PW_BROWSERS_PATH = "/tmp/ms-playwright"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _PW_BROWSERS_PATH

# Install Playwright browser binary on first cold start.
_pw_flag = Path(tempfile.gettempdir()) / ".pw_chromium_installed_v2"
if not _pw_flag.exists():
    _result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": _PW_BROWSERS_PATH},
    )
    if _result.returncode == 0:
        _pw_flag.write_text("ok")

PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
JOBS_JSON = OUTPUT_DIR / "data" / "jobs.json"
SCORES_JSON = OUTPUT_DIR / "data" / "scores.json"
LINKEDIN_PROFILE = PROJECT_ROOT / ".linkedin_profile"
INDEED_PROFILE = PROJECT_ROOT / ".indeed_profile"

st.set_page_config(
    page_title="Job Scanner",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── helpers ───────────────────────────────────────────────────────────────────

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


def session_status(profile_dir: Path) -> str:
    if profile_dir.exists() and any(profile_dir.iterdir()):
        return "✅ Session saved"
    return "⚠️ Not logged in"


def run_subprocess(cmd: list[str], log_placeholder) -> int:
    log_lines: list[str] = []

    def _stream(proc: subprocess.Popen, q: queue.Queue) -> None:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            q.put(line)
        q.put(None)

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
        log_placeholder.code("\n".join(log_lines[-80:]), language="text")

    proc.wait()
    return proc.returncode


def _run_login(runner_script: Path, email: str, password: str, profile_dir: Path):
    """Run a headless login script, handle 2FA interactively. Returns (success, log_lines)."""
    tmp = Path(tempfile.mkdtemp())
    creds_file = tmp / "creds.json"
    twofa_req = tmp / "need_2fa"
    twofa_resp = tmp / "code_2fa"
    creds_file.write_text(json.dumps({"email": email, "password": password}))

    proc = subprocess.Popen(
        [sys.executable, "-u", str(runner_script),
         str(creds_file), str(twofa_req), str(twofa_resp), str(profile_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1",
             "PLAYWRIGHT_BROWSERS_PATH": _PW_BROWSERS_PATH},
    )

    log_area = st.empty()
    log_lines: list[str] = []
    needs_2fa = False

    with st.spinner("Logging in headlessly…"):
        assert proc.stdout
        for raw in iter(proc.stdout.readline, ""):
            line = raw.rstrip()
            log_lines.append(line)
            log_area.code("\n".join(log_lines), language="text")
            if "NEED_2FA" in line:
                needs_2fa = True
                break
            if "LOGIN_SUCCESS" in line or "LOGIN_FAILED" in line:
                break

    creds_file.unlink(missing_ok=True)

    if needs_2fa:
        st.warning("**2FA required.** Check your phone or email for a verification code.")
        twofa_code = st.text_input("Enter verification code", key=f"2fa_{runner_script.stem}")
        if st.button("Submit code", key=f"2fa_btn_{runner_script.stem}", type="primary"):
            twofa_resp.write_text(twofa_code.strip())
            with st.spinner("Verifying…"):
                for raw in iter(proc.stdout.readline, ""):
                    line = raw.rstrip()
                    log_lines.append(line)
                    log_area.code("\n".join(log_lines), language="text")
                    if "LOGIN_SUCCESS" in line or "LOGIN_FAILED" in line:
                        break
                proc.wait()
            twofa_resp.unlink(missing_ok=True)
            twofa_req.unlink(missing_ok=True)
        else:
            return False, log_lines
    else:
        proc.wait()

    success = any("LOGIN_SUCCESS" in l for l in log_lines)
    return success, log_lines


# ── sidebar nav ───────────────────────────────────────────────────────────────

page = st.sidebar.radio(
    "Navigation",
    [
        "🏠 Dashboard",
        "🔐 LinkedIn Login",
        "🔐 Indeed Login",
        "▶️ Run Scanner",
        "📋 Results",
        "⚙️ Configuration",
        "🔔 Notifications",
    ],
    label_visibility="collapsed",
)

st.sidebar.divider()
config_exists = CONFIG_PATH.exists()
st.sidebar.caption(f"Config: {'✅ found' if config_exists else '⚠️ missing'}")
st.sidebar.caption(f"LinkedIn: {session_status(LINKEDIN_PROFILE)}")
st.sidebar.caption(f"Indeed:   {session_status(INDEED_PROFILE)}")
jobs = load_jobs()
scores = load_scores()
st.sidebar.caption(f"Jobs tracked: {len(jobs)}")
excel_path = OUTPUT_DIR / "linkedin_job_results.xlsx"
if excel_path.exists():
    mtime = time.strftime("%b %d %H:%M", time.localtime(excel_path.stat().st_mtime))
    st.sidebar.caption(f"Last Excel: {mtime}")

# ── dashboard ─────────────────────────────────────────────────────────────────

if page == "🏠 Dashboard":
    st.title("Job Application Scanner")
    st.caption("LinkedIn + Indeed job scanner — scan, score, and generate tailored resumes")

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
    col2.metric(f"Jobs >= {min_score}", len(ranked))
    with_resumes = sum(1 for _, _, s in ranked if s.get("resume_path"))
    col3.metric("Resumes Generated", with_resumes)
    applied = sum(1 for j in jobs.values() if j.get("application_status") == "Applied")
    col4.metric("Applied", applied)

    linkedin_count = sum(1 for j in jobs.values() if "indeed" not in j.get("url", "").lower())
    indeed_count = sum(1 for j in jobs.values() if "indeed" in j.get("url", "").lower())
    if linkedin_count or indeed_count:
        src1, src2 = st.columns(2)
        src1.metric("LinkedIn Jobs", linkedin_count)
        src2.metric("Indeed Jobs", indeed_count)

    if not ranked:
        st.info("No results yet. Run the scanner from the **Run Scanner** page.")
    else:
        st.subheader("Top Matches")
        for jid, job, score in ranked[:20]:
            overall = score.get("overall_score", 0)
            source_tag = "Indeed" if "indeed" in job.get("url", "").lower() else "LinkedIn"
            with st.expander(
                f"{score_color(overall)} {overall:.1f}/10  [{source_tag}] — {job.get('title')} @ {job.get('company')} ({job.get('location', '')})"
            ):
                cols = st.columns([2, 1, 1, 1])
                cols[0].markdown(f"**[View Job]({job.get('url', '#')})**")
                cols[1].metric("Role Fit", f"{score.get('role_fit', 0):.1f}")
                cols[2].metric("Skill Match", f"{score.get('skill_match', 0):.1f}")
                cols[3].metric("ATS Score", f"{score.get('resume_ats_score', 0):.0f}/100")

                matched = score.get("matched_keywords", [])
                missing = score.get("missing_keywords", [])
                if matched:
                    st.markdown("**Matched:** " + ", ".join(f"`{k}`" for k in matched[:12]))
                if missing:
                    st.markdown("**Gaps:** " + ", ".join(f"`{k}`" for k in missing[:8]))

                resume_path = score.get("resume_path", "")
                cover_path = score.get("cover_letter_path", "")
                onedrive_url = score.get("onedrive_doc_url", "")
                dl_cols = st.columns(4)
                if resume_path and Path(resume_path).exists():
                    with open(resume_path, "rb") as fh:
                        dl_cols[0].download_button(
                            "Download Resume",
                            fh.read(),
                            file_name=Path(resume_path).name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"resume_{jid}",
                        )
                if cover_path and Path(cover_path).exists():
                    with open(cover_path, "rb") as fh:
                        dl_cols[1].download_button(
                            "Download Cover Letter",
                            fh.read(),
                            file_name=Path(cover_path).name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"cover_{jid}",
                        )
                if onedrive_url:
                    dl_cols[2].link_button("OneDrive Resume", onedrive_url)
                if score.get("onedrive_cover_letter_url"):
                    dl_cols[3].link_button("OneDrive Cover Letter", score["onedrive_cover_letter_url"])

    if excel_path.exists():
        st.divider()
        with open(excel_path, "rb") as fh:
            st.download_button(
                "Download Full Excel Results",
                fh.read(),
                file_name="linkedin_job_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

# ── linkedin login ────────────────────────────────────────────────────────────

elif page == "🔐 LinkedIn Login":
    st.title("LinkedIn Login")

    li_status = session_status(LINKEDIN_PROFILE)
    st.info(
        f"**Session status:** {li_status}\n\n"
        "Enter your LinkedIn credentials below. A **headless browser** runs on the server, "
        "logs in, and saves only the session cookie to `.linkedin_profile/`. "
        "Your password is used once and never stored."
    )

    if li_status.startswith("✅"):
        st.success("Session already saved. Re-enter credentials below to refresh it.")

    with st.form("linkedin_login_form"):
        li_email = st.text_input("LinkedIn email")
        li_password = st.text_input("LinkedIn password", type="password")
        li_submit = st.form_submit_button("Login to LinkedIn", type="primary")

    if li_submit:
        if not li_email or not li_password:
            st.error("Enter both email and password.")
        else:
            runner = PROJECT_ROOT / "_linkedin_login_runner.py"
            success, log_lines = _run_login(runner, li_email, li_password, LINKEDIN_PROFILE)
            if success:
                st.success("LinkedIn login successful! Session saved — the scanner will reuse it.")
                st.rerun()
            elif not any("NEED_2FA" in l for l in log_lines):
                st.error("Login failed. Check your credentials or try again.")
                st.code("\n".join(log_lines))

    if LINKEDIN_PROFILE.exists():
        files = list(LINKEDIN_PROFILE.iterdir())
        st.caption(f"Profile: `{LINKEDIN_PROFILE}` — {len(files)} files stored")

# ── indeed login ──────────────────────────────────────────────────────────────

elif page == "🔐 Indeed Login":
    st.title("Indeed Login")

    indeed_status = session_status(INDEED_PROFILE)
    st.info(
        f"**Session status:** {indeed_status}\n\n"
        "Enter your Indeed credentials below. A **headless browser** runs on the server, "
        "logs in, and saves only the session cookie to `.indeed_profile/`. "
        "Your password is used once and never stored."
    )

    if indeed_status.startswith("✅"):
        st.success("Session already saved. Re-enter credentials below to refresh it.")

    with st.form("indeed_login_form"):
        in_email = st.text_input("Indeed email")
        in_password = st.text_input("Indeed password", type="password")
        in_submit = st.form_submit_button("Login to Indeed", type="primary")

    if in_submit:
        if not in_email or not in_password:
            st.error("Enter both email and password.")
        else:
            runner = PROJECT_ROOT / "_indeed_login_runner.py"
            success, log_lines = _run_login(runner, in_email, in_password, INDEED_PROFILE)
            if success:
                st.success("Indeed login successful! Session saved — the scanner will reuse it.")
                st.rerun()
            elif not any("NEED_2FA" in l for l in log_lines):
                st.error("Login failed. Check your credentials or try again.")
                st.code("\n".join(log_lines))

    if INDEED_PROFILE.exists():
        files = list(INDEED_PROFILE.iterdir())
        st.caption(f"Profile: `{INDEED_PROFILE}` — {len(files)} files stored")

# ── run scanner ───────────────────────────────────────────────────────────────

elif page == "▶️ Run Scanner":
    st.title("Run Scanner")

    if not config_exists:
        st.error("No `config.json` found. Go to **Configuration** and save your settings first.")
        st.stop()

    li_ok = LINKEDIN_PROFILE.exists() and any(LINKEDIN_PROFILE.iterdir())
    indeed_ok = INDEED_PROFILE.exists() and any(INDEED_PROFILE.iterdir())

    if not li_ok and not indeed_ok:
        st.warning(
            "Neither LinkedIn nor Indeed sessions are saved. "
            "Use the Login pages first, or enable Sample mode below."
        )

    st.subheader("Sources")
    src_col1, src_col2, src_col3 = st.columns(3)
    scan_linkedin = src_col1.checkbox("Scan LinkedIn", value=li_ok, disabled=not li_ok)
    scan_indeed = src_col2.checkbox("Scan Indeed", value=indeed_ok, disabled=not indeed_ok)
    sample_mode = src_col3.checkbox("Sample mode (test without any login)", value=False)

    opt_col1, _ = st.columns(2)
    no_resumes = opt_col1.checkbox("Skip resume/cover letter generation", value=False)

    cfg = load_config()
    max_pages = st.slider(
        "Max pages to scan per source",
        min_value=1, max_value=20,
        value=int(cfg.get("max_pages", 6)),
        disabled=sample_mode,
    )

    if st.button("Start Scan", type="primary"):
        if not sample_mode and not scan_linkedin and not scan_indeed:
            st.error("Select at least one source or enable Sample mode.")
            st.stop()

        overall_ok = True

        if sample_mode or scan_linkedin:
            st.subheader("LinkedIn Scan Log")
            log_area_li = st.empty()
            cmd = [sys.executable, "-u", str(PROJECT_ROOT / "run_job_scanner.py"), "--once", "--headless",
                   "--max-pages", str(max_pages)]
            if sample_mode:
                cmd.append("--sample")
            if no_resumes:
                cmd.append("--no-resumes")
            with st.spinner("Running LinkedIn scanner..."):
                rc = run_subprocess(cmd, log_area_li)
            if rc == 0:
                st.success("LinkedIn scan complete.")
            else:
                st.error(f"LinkedIn scanner exited with code {rc}.")
                overall_ok = False

        if not sample_mode and scan_indeed:
            st.subheader("Indeed Scan Log")
            log_area_in = st.empty()
            cmd_indeed = [sys.executable, "-u", str(PROJECT_ROOT / "run_indeed_scanner.py"),
                          "--max-pages", str(max_pages)]
            if no_resumes:
                cmd_indeed.append("--no-resumes")
            with st.spinner("Running Indeed scanner..."):
                rc2 = run_subprocess(cmd_indeed, log_area_in)
            if rc2 == 0:
                st.success("Indeed scan complete.")
            else:
                st.error(f"Indeed scanner exited with code {rc2}.")
                overall_ok = False

        if overall_ok:
            st.balloons()
            st.success("All scans finished. Check the Results tab.")

# ── results ───────────────────────────────────────────────────────────────────

elif page == "📋 Results":
    st.title("Results")

    jobs = load_jobs()
    scores = load_scores()

    if not jobs:
        st.info("No jobs scanned yet. Run the scanner first.")
        st.stop()

    cfg = load_config()
    min_score = float(cfg.get("min_score", 6.0))

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
    min_filter = filter_col1.slider("Min score", 0.0, 10.0, min_score, 0.5)
    search_term = filter_col2.text_input("Search title / company", "")
    source_filter = filter_col3.selectbox("Source", ["All", "LinkedIn", "Indeed"])
    show_closed = filter_col4.checkbox("Show closed", False)

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
        is_indeed = "indeed" in job.get("url", "").lower()
        if source_filter == "Indeed" and not is_indeed:
            continue
        if source_filter == "LinkedIn" and is_indeed:
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
        is_indeed = "indeed" in job.get("url", "").lower()
        table_data.append({
            "Score": score.get("overall_score", 0),
            "Source": "Indeed" if is_indeed else "LinkedIn",
            "Title": job.get("title", ""),
            "Company": job.get("company", ""),
            "Location": job.get("location", ""),
            "Applicants": job.get("applicant_count_text", ""),
            "Easy Apply": "Yes" if job.get("easy_apply") else "",
            "Resume": "Yes" if score.get("resume_path") and Path(score["resume_path"]).exists() else "",
            "ATS": score.get("resume_ats_score", 0),
        })

    df = pd.DataFrame(table_data)
    st.dataframe(
        df,
        use_container_width=True,
        column_config={
            "Score": st.column_config.NumberColumn(format="%.1f"),
            "ATS": st.column_config.NumberColumn(format="%.0f"),
        },
        hide_index=True,
    )

    st.subheader("Job Detail")
    titles = [
        f"{r[2].get('overall_score',0):.1f} — {r[1].get('title','')} @ {r[1].get('company','')}"
        for r in all_rows
    ]
    selected = st.selectbox("Select a job", titles, index=0)
    idx = titles.index(selected)
    _, sel_job, sel_score = all_rows[idx]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Overall", f"{sel_score.get('overall_score',0):.1f}/10")
    c2.metric("Role Fit", f"{sel_score.get('role_fit',0):.1f}")
    c3.metric("Skill Match", f"{sel_score.get('skill_match',0):.1f}")
    c4.metric("ATS Score", f"{sel_score.get('resume_ats_score',0):.0f}/100")

    job_url = sel_job.get("url", "#")
    st.markdown(
        f"**[Open Job Posting]({job_url})**  |  {sel_job.get('location','')}  |  "
        f"{sel_job.get('applicant_count_text','')}  |  Listed: {sel_job.get('listed_at','')}"
    )

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
            dl1.download_button("Download Resume", fh.read(), Path(resume_path).name,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key="detail_resume")
    if cover_path and Path(cover_path).exists():
        with open(cover_path, "rb") as fh:
            dl2.download_button("Download Cover Letter", fh.read(), Path(cover_path).name,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key="detail_cover")
    if outreach_path and Path(outreach_path).exists():
        with open(outreach_path, "rb") as fh:
            dl3.download_button("Download Cold Outreach", fh.read(), Path(outreach_path).name,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key="detail_outreach")
    if sel_score.get("onedrive_doc_url"):
        dl4.link_button("OneDrive Resume", sel_score["onedrive_doc_url"])

    if excel_path.exists():
        st.divider()
        with open(excel_path, "rb") as fh:
            st.download_button("Download Full Excel", fh.read(), "linkedin_job_results.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ── configuration ─────────────────────────────────────────────────────────────

elif page == "⚙️ Configuration":
    st.title("Configuration")
    st.caption("Changes are saved to `config.json` in the project root.")

    cfg = load_config()

    with st.form("config_form"):
        st.subheader("LinkedIn Search")
        cfg["search_query"] = st.text_input("Search query", cfg.get("search_query", ""))
        cfg["linkedin_location"] = st.text_input("LinkedIn location", cfg.get("linkedin_location", "Canada"))
        cfg["max_pages"] = st.number_input("Max pages (LinkedIn)", 1, 50, int(cfg.get("max_pages", 6)))
        cfg["date_posted"] = st.selectbox(
            "Date posted",
            ["Past 24 hours", "Past Week", "Past Month", "Any time"],
            index=["Past 24 hours", "Past Week", "Past Month", "Any time"].index(
                cfg.get("date_posted", "Past 24 hours")
            ),
        )
        employment_options = ["Full-time", "Part-time", "Contract", "Temporary", "Volunteer", "Other"]
        cfg["employment_types"] = st.multiselect(
            "Employment types", employment_options,
            default=cfg.get("employment_types", ["Full-time"]),
        )
        exp_options = ["Entry-level", "Associate", "Mid-Senior level", "Director", "Executive", "Manager", "Internship"]
        cfg["experience_levels"] = st.multiselect(
            "Experience levels", exp_options,
            default=cfg.get("experience_levels", ["Entry-level", "Manager"]),
        )

        st.subheader("Indeed Search")
        cfg["indeed_location"] = st.text_input(
            "Indeed location", cfg.get("indeed_location", cfg.get("linkedin_location", "Canada"))
        )
        cfg["indeed_max_pages"] = st.number_input(
            "Max pages (Indeed)", 1, 20, int(cfg.get("indeed_max_pages", 4))
        )

        st.subheader("Scoring & Filtering")
        cfg["min_score"] = st.slider("Minimum fit score", 0.0, 10.0, float(cfg.get("min_score", 6.0)), 0.1)
        cfg["max_resumes_per_run"] = st.number_input("Max resumes per run", 1, 200, int(cfg.get("max_resumes_per_run", 80)))
        cfg["candidate_experience_years"] = st.number_input(
            "Your years of experience", 0.0, 40.0, float(cfg.get("candidate_experience_years", 3.9)), 0.1
        )
        cfg["max_required_experience_years"] = st.number_input(
            "Max required experience (filter out senior roles)", 0.0, 40.0,
            float(cfg.get("max_required_experience_years", 5.99)), 0.1
        )
        cfg["target_locations"] = st.text_area(
            "Target locations (one per line)", "\n".join(cfg.get("target_locations", []))
        ).splitlines()
        cfg["target_role_keywords"] = st.text_area(
            "Target role keywords (one per line)", "\n".join(cfg.get("target_role_keywords", []))
        ).splitlines()
        cfg["seniority_keywords_to_penalize"] = st.text_area(
            "Seniority keywords to penalise (one per line)",
            "\n".join(cfg.get("seniority_keywords_to_penalize", []))
        ).splitlines()

        st.subheader("OneDrive")
        one = cfg.setdefault("onedrive", {})
        one["enabled"] = st.checkbox("Enable OneDrive", bool(one.get("enabled", True)))
        one["client_id"] = st.text_input("App Client ID", one.get("client_id", ""))
        one["tenant_id"] = st.text_input("Tenant ID", one.get("tenant_id", "consumers"))
        one["resume_folder_path"] = st.text_input("Resume folder path", one.get("resume_folder_path", "Job Scan/Resumes"))
        one["excel_folder_path"] = st.text_input("Excel folder path", one.get("excel_folder_path", "Job Scan"))

        st.subheader("Advanced")
        cfg["headless"] = st.checkbox(
            "Run browser headless (always true on Streamlit Cloud)",
            bool(cfg.get("headless", True)),
        )
        submitted = st.form_submit_button("Save Configuration", type="primary")

    if submitted:
        save_config(cfg)
        st.success("Configuration saved to `config.json`.")

    with st.expander("Raw JSON"):
        st.json(cfg)

# ── notifications ─────────────────────────────────────────────────────────────

elif page == "🔔 Notifications":
    st.title("Notifications")

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
        env["JOB_SCANNER_SMTP_PASSWORD"] = st.text_input(
            "SMTP password", env.get("JOB_SCANNER_SMTP_PASSWORD", ""), type="password"
        )
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
            "From (WhatsApp)", env.get("JOB_SCANNER_TWILIO_FROM", "whatsapp:+14155238886")
        )
        env["JOB_SCANNER_TWILIO_TO"] = st.text_input(
            "Your WhatsApp number", env.get("JOB_SCANNER_TWILIO_TO", "whatsapp:+1YOURNUMBER")
        )

        cfg["notification_top_n"] = st.number_input(
            "Top N jobs in notification", 1, 50, int(cfg.get("notification_top_n", 10))
        )
        cfg["notify_only_new_jobs"] = st.checkbox(
            "Only notify for new jobs",
            bool(cfg.get("notify_only_new_jobs", True)),
        )
        save_notif = st.form_submit_button("Save Notification Settings", type="primary")

    if save_notif:
        save_config(cfg)
        save_env(env)
        st.success("Notification settings saved.")
