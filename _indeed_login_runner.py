"""Headless Indeed login helper — called by streamlit_app.py as a subprocess."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

# Force a consistent browser path to avoid user-mismatch on Streamlit Cloud.
_PW_PATH = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/tmp/ms-playwright")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _PW_PATH

# Ensure Chromium binary is present.
print("Installing Playwright Chromium binary…", flush=True)
_install = subprocess.run(
    [sys.executable, "-m", "playwright", "install", "chromium"],
    capture_output=False,
    env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": _PW_PATH},
)
if _install.returncode != 0:
    print(f"ERROR: playwright install failed (exit {_install.returncode})", flush=True)
    sys.exit(1)
print("Playwright install done.", flush=True)

from playwright.sync_api import sync_playwright

credentials_file = sys.argv[1]
twofa_request_file = sys.argv[2]
twofa_response_file = sys.argv[3]
profile_dir = sys.argv[4]

creds = json.loads(pathlib.Path(credentials_file).read_text())
email = creds["email"]
password = creds["password"]

pathlib.Path(profile_dir).mkdir(parents=True, exist_ok=True)

print("Starting headless browser…", flush=True)
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        profile_dir,
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox"],
        viewport={"width": 1280, "height": 900},
    )
    page = ctx.new_page()

    print("Navigating to Indeed login…", flush=True)
    page.goto("https://secure.indeed.com/account/login", timeout=30000)
    page.wait_for_timeout(2000)

    # Step 1: enter email
    email_input = page.query_selector("input[type='email'], input[name='__email']")
    if email_input:
        email_input.fill(email)
        continue_btn = page.query_selector("button[type='submit'], button:has-text('Continue')")
        if continue_btn:
            continue_btn.click()
            page.wait_for_timeout(2000)

    # Step 2: enter password
    password_input = page.query_selector("input[type='password'], input[name='__password']")
    if password_input:
        password_input.fill(password)
        sign_in_btn = page.query_selector("button[type='submit'], button:has-text('Sign in')")
        if sign_in_btn:
            sign_in_btn.click()
            print("Submitted credentials, waiting…", flush=True)
            page.wait_for_timeout(4000)

    current_url = page.url
    print(f"After login URL: {current_url}", flush=True)

    needs_2fa = (
        "challenge" in current_url
        or "two-factor" in current_url
        or "verification" in current_url
        or page.query_selector("input[autocomplete='one-time-code']") is not None
    )

    if needs_2fa:
        print("NEED_2FA", flush=True)
        pathlib.Path(twofa_request_file).write_text("1")
        print("Waiting for 2FA code (up to 5 minutes)…", flush=True)
        for _ in range(60):
            if pathlib.Path(twofa_response_file).exists():
                code = pathlib.Path(twofa_response_file).read_text().strip()
                if code:
                    print("Received 2FA code, submitting…", flush=True)
                    pin = page.query_selector("input[autocomplete='one-time-code'], input[type='tel'], input[type='number']")
                    if pin:
                        pin.fill(code)
                        page.keyboard.press("Enter")
                        page.wait_for_timeout(4000)
                    break
            time.sleep(5)
        pathlib.Path(twofa_response_file).unlink(missing_ok=True)
        pathlib.Path(twofa_request_file).unlink(missing_ok=True)

    final_url = page.url
    print(f"Final URL: {final_url}", flush=True)

    success = (
        "indeed.com" in final_url
        and "login" not in final_url
        and "challenge" not in final_url
    )

    if success:
        print("LOGIN_SUCCESS", flush=True)
    else:
        print(f"LOGIN_FAILED: unexpected URL {final_url}", flush=True)
        sys.exit(1)

    ctx.close()
