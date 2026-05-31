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

# ── helpers ─────────────────────────────────────────────────────────────────────────────────

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
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def save_env(data: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in data.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n")


def load_jobs() -> list[dict]:
    if JOBS_JSON.exists():
        try:
            return json.loads(JOBS_JSON.read_text())
        except Exception:
            return []
    return []


def load_scores() -> dict:
    if SCORES_JSON.exists():
        try:
            return json.loads(SCORES_JSON.read_text())
        except Exception:
            return {}
    return {}


# ── sidebar nav ────────────────────────────────────────────────────────────────────────

PAGES = [
    "🏠 Dashboard",
    "▶️ Run Scanner",
    "🔍 Job Results",
    "⚙️ Configuration",
    "🔒 LinkedIn Login",
    "🔒 Indeed Login",
]

with st.sidebar:
    st.title("🔍 Job Scanner")
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")


# ───────────────────────────────────────────────────────────────────────────────────
# Dashboard
# ───────────────────────────────────────────────────────────────────────────────────
if page == "🏠 Dashboard":
    st.title("🏠 Dashboard")
    jobs = load_jobs()
    scores = load_scores()
    config = load_config()
    min_score = float(config.get("min_score", 6.0))

    col1, col2, col3, col4 = st.columns(4)
    total = len(jobs)
    scored = len(scores)
    matching = sum(1 for s in scores.values() if isinstance(s, dict) and s.get("overall_score", 0) >= min_score)
    easy_apply = sum(1 for j in jobs if isinstance(j, dict) and j.get("easy_apply"))

    col1.metric("💼 Total Jobs", total)
    col2.metric("✅ Scored", scored)
    col3.metric("🎯 Matching", matching)
    col4.metric("⚡ Easy Apply", easy_apply)

    if jobs and scores:
        st.subheader("Top Matches")
        job_map = {j.get("job_id", j.get("url", "")): j for j in jobs if isinstance(j, dict)}
        ranked = sorted(
            [(k, v) for k, v in scores.items() if isinstance(v, dict) and v.get("overall_score", 0) >= min_score],
            key=lambda x: -x[1].get("overall_score", 0),
        )[:10]
        for key, score in ranked:
            job = job_map.get(key, {})
            title = job.get("title", "Unknown")
            company = job.get("company", "")
            url = job.get("url", "")
            s = score.get("overall_score", 0)
            st.markdown(f"**{s:.1f}/10** — [{title}]({url}) @ {company}")
    elif not jobs:
        st.info("No jobs scanned yet. Go to **Run Scanner** to start.")


# ───────────────────────────────────────────────────────────────────────────────────
# Run Scanner
# ───────────────────────────────────────────────────────────────────────────────────
elif page == "▶️ Run Scanner":
    st.title("▶️ Run Scanner")

    col_l, col_r = st.columns(2)
    with col_l:
        scan_linkedin = st.checkbox("Scan LinkedIn", value=True)
    with col_r:
        scan_indeed = st.checkbox("Scan Indeed", value=False)

    run_btn = st.button("🚀 Run Scanner Now", type="primary")

    output_placeholder = st.empty()
    status_placeholder = st.empty()

    if run_btn:
        config = load_config()
        if not scan_linkedin and not scan_indeed:
            st.warning("Select at least one platform to scan.")
        else:
            env = {**os.environ}
            if not scan_linkedin:
                env["SKIP_LINKEDIN"] = "1"
            if not scan_indeed:
                env["SKIP_INDEED"] = "1"

            cmd = [sys.executable, "-u", str(PROJECT_ROOT / "run_job_scanner.py"), "--once"]
            log_lines: list[str] = []
            output_area = st.empty()

            def stream_output(proc: subprocess.Popen) -> None:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.rstrip("\n")
                    log_lines.append(line)

            with st.spinner("Scanner running…"):
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                    cwd=str(PROJECT_ROOT),
                )
                t = threading.Thread(target=stream_output, args=(proc,), daemon=True)
                t.start()
                while proc.poll() is None:
                    output_area.code("\n".join(log_lines[-60:]), language="")
                    time.sleep(0.5)
                t.join(timeout=5)
                output_area.code("\n".join(log_lines), language="")

            if proc.returncode == 0:
                st.success("✅ Scanner completed successfully.")
            else:
                st.error(f"❌ Scanner exited with code {proc.returncode}")


# ───────────────────────────────────────────────────────────────────────────────────
# Job Results
# ───────────────────────────────────────────────────────────────────────────────────
elif page == "🔍 Job Results":
    st.title("🔍 Job Results")
    jobs = load_jobs()
    scores = load_scores()
    config = load_config()
    min_score = float(config.get("min_score", 6.0))

    if not jobs:
        st.info("No jobs yet. Run the scanner to populate results.")
    else:
        filter_col1, filter_col2, filter_col3 = st.columns(3)
        with filter_col1:
            search_term = st.text_input("🔍 Filter by title/company", "")
        with filter_col2:
            min_score_filter = st.slider("Min score", 1.0, 10.0, min_score, 0.5)
        with filter_col3:
            easy_apply_only = st.checkbox("Easy Apply only", value=False)

        job_map = {j.get("job_id", j.get("url", "")): j for j in jobs if isinstance(j, dict)}
        scored_jobs = [
            (key, score, job_map[key])
            for key, score in scores.items()
            if isinstance(score, dict)
            and key in job_map
            and score.get("overall_score", 0) >= min_score_filter
        ]
        if search_term:
            scored_jobs = [
                (k, s, j) for k, s, j in scored_jobs
                if search_term.lower() in j.get("title", "").lower()
                or search_term.lower() in j.get("company", "").lower()
            ]
        if easy_apply_only:
            scored_jobs = [(k, s, j) for k, s, j in scored_jobs if j.get("easy_apply")]

        scored_jobs.sort(key=lambda x: -x[1].get("overall_score", 0))
        st.write(f"Showing {len(scored_jobs)} jobs")

        for key, score, job in scored_jobs:
            title = job.get("title", "Unknown")
            company = job.get("company", "")
            location = job.get("location", "")
            url = job.get("url", "")
            s = score.get("overall_score", 0)
            easy = "⚡ Easy Apply" if job.get("easy_apply") else ""
            applicants = job.get("applicant_count_text", "")

            with st.expander(f"{s:.1f}/10 — {title} @ {company} {easy}"):
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.markdown(f"**Company:** {company}")
                    st.markdown(f"**Location:** {location}")
                    if applicants:
                        st.markdown(f"**Applicants:** {applicants}")
                    if url:
                        st.markdown(f"[View on LinkedIn]({url})")
                with col2:
                    st.markdown(f"**Score:** {s:.2f}/10")
                    for field in ("role_fit", "skill_match", "experience_match", "domain_fit"):
                        v = score.get(field)
                        if v is not None:
                            st.markdown(f"**{field.replace('_', ' ').title()}:** {v:.1f}")
                description = job.get("description", "")
                if description:
                    st.markdown("**Description:**")
                    st.markdown(description[:800] + ("..." if len(description) > 800 else ""))
                notes = score.get("notes", "")
                if notes:
                    st.caption(notes)


# ───────────────────────────────────────────────────────────────────────────────────
# Configuration
# ───────────────────────────────────────────────────────────────────────────────────
elif page == "⚙️ Configuration":
    st.title("⚙️ Configuration")
    config = load_config()
    env = load_env()

    with st.expander("🔍 Search Settings", expanded=True):
        search_query = st.text_input("Search query", config.get("search_query", ""))
        linkedin_location = st.text_input("LinkedIn location", config.get("linkedin_location", "Canada"))
        max_pages = st.number_input("Max pages", min_value=1, max_value=20, value=int(config.get("max_pages", 5)))
        min_score = st.number_input("Min score (0–10)", min_value=0.0, max_value=10.0, value=float(config.get("min_score", 6.0)), step=0.5)

    with st.expander("📁 Paths"):
        resume_root = st.text_input("Resume root directory", config.get("resume_root", ""))
        output_dir = st.text_input("Output directory", config.get("output_dir", "outputs"))

    with st.expander("☁️ OneDrive (optional)"):
        one_enabled = st.checkbox("Enable OneDrive", value=bool(config.get("onedrive", {}).get("enabled", False)))
        one_client_id = st.text_input("Client ID", config.get("onedrive", {}).get("client_id", ""))
        one_folder = st.text_input("Upload folder", config.get("onedrive", {}).get("upload_folder", "JobScanner"))

    with st.expander("☁️ Google Drive (optional)"):
        gd_enabled = st.checkbox("Enable Google Drive", value=bool(config.get("google_drive", {}).get("enabled", False)))
        gd_folder = st.text_input("Google Drive folder", config.get("google_drive", {}).get("upload_folder", "JobScanner"))
        gd_creds = st.text_area("service_account.json contents", env.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""), height=120)

    with st.expander("🔔 Notifications (optional)"):
        notify_email = st.text_input("Notify email", env.get("NOTIFY_EMAIL", ""))
        notify_smtp = st.text_input("SMTP server", env.get("NOTIFY_SMTP_SERVER", ""))
        notify_smtp_user = st.text_input("SMTP user", env.get("NOTIFY_SMTP_USER", ""))
        notify_smtp_pass = st.text_input("SMTP password", env.get("NOTIFY_SMTP_PASS", ""), type="password")

    if st.button("Save Configuration", type="primary"):
        config["search_query"] = search_query
        config["linkedin_location"] = linkedin_location
        config["max_pages"] = max_pages
        config["min_score"] = min_score
        config["resume_root"] = resume_root
        config["output_dir"] = output_dir
        config.setdefault("onedrive", {})
        config["onedrive"]["enabled"] = one_enabled
        config["onedrive"]["client_id"] = one_client_id
        config["onedrive"]["upload_folder"] = one_folder
        config.setdefault("google_drive", {})
        config["google_drive"]["enabled"] = gd_enabled
        config["google_drive"]["upload_folder"] = gd_folder
        save_config(config)
        env_data = load_env()
        if notify_email:
            env_data["NOTIFY_EMAIL"] = notify_email
        if notify_smtp:
            env_data["NOTIFY_SMTP_SERVER"] = notify_smtp
        if notify_smtp_user:
            env_data["NOTIFY_SMTP_USER"] = notify_smtp_user
        if notify_smtp_pass:
            env_data["NOTIFY_SMTP_PASS"] = notify_smtp_pass
        if gd_creds:
            env_data["GOOGLE_SERVICE_ACCOUNT_JSON"] = gd_creds
        save_env(env_data)
        st.success("✅ Configuration saved.")
        st.rerun()


# ───────────────────────────────────────────────────────────────────────────────────
# LinkedIn Login
# ───────────────────────────────────────────────────────────────────────────────────
elif page == "🔒 LinkedIn Login":
    st.title("🔒 LinkedIn Login")
    st.markdown(
        "The scanner uses a headless browser to search LinkedIn. "
        "You need to authenticate once so it can access job listings."
    )

    config = load_config()
    li_method = st.radio(
        "Login method",
        ["Cookie (recommended — no CAPTCHA)", "Email & password (headless)"],
        horizontal=True,
    )

    if li_method == "Cookie (recommended — no CAPTCHA)":
        st.info(
            "**How to get your `li_at` cookie (30 seconds):**\n\n"
            "1. Open LinkedIn in a **private/incognito window** and log in\n"
            "2. Press **F12** → Application tab → Cookies → `https://www.linkedin.com`\n"
            "3. Find the cookie named **`li_at`** and copy its value\n"
            "4. Paste it below and click Save, then close the incognito window\n\n"
            "**Why incognito?** The scanner uses your cookie to start its own browser session. "
            "Using your main browser's cookie causes LinkedIn to log you out there. "
            "Using incognito gives a separate session — closing it doesn't affect your main browser. "
            "After the first scan the scanner stores its own session and won't need the cookie again.\n\n"
            "_Your cookie is stored only in the server's memory profile directory and never committed to GitHub._"
        )
        with st.form("li_cookie_form"):
            li_at = st.text_input("Paste your `li_at` cookie value", type="password")
            save_cookie = st.form_submit_button("Save LinkedIn session", type="primary")

        if save_cookie:
            if not li_at.strip():
                st.error("Cookie value cannot be empty.")
            else:
                import json as _json
                LINKEDIN_PROFILE.mkdir(parents=True, exist_ok=True)
                cookies_file = LINKEDIN_PROFILE / "cookies.json"
                cookies = [
                    {
                        "name": "li_at",
                        "value": li_at.strip(),
                        "domain": ".linkedin.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "None",
                    }
                ]
                cookies_file.write_text(_json.dumps(cookies, indent=2))
                st.success("LinkedIn session cookie saved! The scanner will use it automatically.")
                st.rerun()
    else:
        st.info(
            "A headless browser will log in on the server. "
            "LinkedIn sometimes triggers a CAPTCHA challenge for automated logins — "
            "if that happens, use the **Cookie** method above instead."
        )
        with st.form("linkedin_login_form"):
            li_email = st.text_input("LinkedIn email")
            li_password = st.text_input("LinkedIn password", type="password")
            li_submit = st.form_submit_button("Login", type="primary")

        if li_submit:
            if not li_email or not li_password:
                st.error("Email and password are required.")
            else:
                _run_login(li_email, li_password, config)


def _run_login(email: str, password: str, config: dict) -> None:
    import json as _json
    import tempfile as _tmp

    creds_file = Path(_tmp.mktemp(suffix=".json"))
    twofa_req = Path(_tmp.mktemp(suffix=".txt"))
    twofa_resp = Path(_tmp.mktemp(suffix=".txt"))
    profile_dir = str(LINKEDIN_PROFILE)

    creds_file.write_text(_json.dumps({"email": email, "password": password}))

    runner = PROJECT_ROOT / "_linkedin_login_runner.py"
    cmd = [
        sys.executable,
        "-u",
        str(runner),
        str(creds_file),
        str(twofa_req),
        str(twofa_resp),
        profile_dir,
    ]
    env = {
        **os.environ,
        "PLAYWRIGHT_BROWSERS_PATH": _PW_BROWSERS_PATH,
    }

    log_lines: list[str] = []
    twofa_prompted = False
    twofa_code_entered = False
    status_area = st.empty()
    log_area = st.empty()
    twofa_area = st.empty()

    with st.spinner("Logging in to LinkedIn…"):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        out_q: queue.Queue[str | None] = queue.Queue()

        def _reader(p: subprocess.Popen, q: queue.Queue) -> None:
            assert p.stdout
            for line in p.stdout:
                q.put(line.rstrip("\n"))
            q.put(None)

        threading.Thread(target=_reader, args=(proc, out_q), daemon=True).start()

        while True:
            try:
                line = out_q.get(timeout=0.3)
            except queue.Empty:
                line = ""

            if line is None:
                break

            if line:
                log_lines.append(line)
                log_area.code("\n".join(log_lines[-40:]), language="")

            if "NEED_2FA" in (line or "") and not twofa_prompted:
                twofa_prompted = True
                with twofa_area.container():
                    st.warning("🔐 LinkedIn 2FA required")
                    with st.form("twofa_form"):
                        code = st.text_input("Enter your 2FA code")
                        submit_2fa = st.form_submit_button("Submit")
                    if submit_2fa and code:
                        twofa_resp.write_text(code.strip())
                        twofa_code_entered = True
                        twofa_area.empty()

            if proc.poll() is not None:
                break

            time.sleep(0.1)

        proc.wait()

    try:
        creds_file.unlink(missing_ok=True)
    except Exception:
        pass

    full_output = "\n".join(log_lines)
    if "LOGIN_SUCCESS" in full_output:
        status_area.success("✅ LinkedIn login successful! The session is saved for future scans.")
    elif "LOGIN_FAILED" in full_output:
        msg = next((l for l in log_lines if "LOGIN_FAILED" in l), "Login failed")
        status_area.error(f"❌ {msg}")
    else:
        status_area.warning("⚠️ Login completed but status unclear. Check the log above.")


# ───────────────────────────────────────────────────────────────────────────────────
# Indeed Login
# ───────────────────────────────────────────────────────────────────────────────────
elif page == "🔒 Indeed Login":
    st.title("🔒 Indeed Login")
    st.markdown(
        "The scanner can optionally search Indeed for jobs. "
        "Authenticate once to allow access."
    )

    config = load_config()

    st.info(
        "A headless browser will log in to Indeed on the server. "
        "If Indeed shows a CAPTCHA or bot check, the login may fail."
    )
    with st.form("indeed_login_form"):
        indeed_email = st.text_input("Indeed email")
        indeed_password = st.text_input("Indeed password", type="password")
        indeed_submit = st.form_submit_button("Login to Indeed", type="primary")

    if indeed_submit:
        if not indeed_email or not indeed_password:
            st.error("Email and password are required.")
        else:
            _run_indeed_login(indeed_email, indeed_password, config)


def _run_indeed_login(email: str, password: str, config: dict) -> None:
    import json as _json
    import tempfile as _tmp

    creds_file = Path(_tmp.mktemp(suffix=".json"))
    twofa_req = Path(_tmp.mktemp(suffix=".txt"))
    twofa_resp = Path(_tmp.mktemp(suffix=".txt"))
    profile_dir = str(INDEED_PROFILE)

    creds_file.write_text(_json.dumps({"email": email, "password": password}))

    runner = PROJECT_ROOT / "_indeed_login_runner.py"
    if not runner.exists():
        st.error("Indeed login runner not found.")
        return

    cmd = [
        sys.executable,
        "-u",
        str(runner),
        str(creds_file),
        str(twofa_req),
        str(twofa_resp),
        profile_dir,
    ]
    env = {
        **os.environ,
        "PLAYWRIGHT_BROWSERS_PATH": _PW_BROWSERS_PATH,
    }

    log_lines: list[str] = []
    twofa_prompted = False
    status_area = st.empty()
    log_area = st.empty()
    twofa_area = st.empty()

    with st.spinner("Logging in to Indeed…"):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        out_q: queue.Queue[str | None] = queue.Queue()

        def _reader(p: subprocess.Popen, q: queue.Queue) -> None:
            assert p.stdout
            for line in p.stdout:
                q.put(line.rstrip("\n"))
            q.put(None)

        threading.Thread(target=_reader, args=(proc, out_q), daemon=True).start()

        while True:
            try:
                line = out_q.get(timeout=0.3)
            except queue.Empty:
                line = ""

            if line is None:
                break

            if line:
                log_lines.append(line)
                log_area.code("\n".join(log_lines[-40:]), language="")

            if "NEED_2FA" in (line or "") and not twofa_prompted:
                twofa_prompted = True
                with twofa_area.container():
                    st.warning("🔐 Indeed 2FA required")
                    with st.form("indeed_twofa_form"):
                        code = st.text_input("Enter your 2FA code")
                        submit_2fa = st.form_submit_button("Submit")
                    if submit_2fa and code:
                        twofa_resp.write_text(code.strip())
                        twofa_area.empty()

            if proc.poll() is not None:
                break

            time.sleep(0.1)

        proc.wait()

    try:
        creds_file.unlink(missing_ok=True)
    except Exception:
        pass

    full_output = "\n".join(log_lines)
    if "LOGIN_SUCCESS" in full_output:
        status_area.success("✅ Indeed login successful!")
    elif "LOGIN_FAILED" in full_output:
        msg = next((l for l in log_lines if "LOGIN_FAILED" in l), "Login failed")
        status_area.error(f"❌ {msg}")
    else:
        status_area.warning("⚠️ Login completed but status unclear. Check the log above.")
