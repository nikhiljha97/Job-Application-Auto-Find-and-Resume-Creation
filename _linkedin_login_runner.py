"""Headless LinkedIn login helper — called by streamlit_app.py as a subprocess."""
from __future__ import annotations

import json
import pathlib
import sys
import time

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

    print("Navigating to LinkedIn login…", flush=True)
    page.goto("https://www.linkedin.com/login", timeout=30000)
    page.wait_for_timeout(2000)

    page.fill("#username", email)
    page.fill("#password", password)
    page.click('[type="submit"]')
    print("Submitted credentials, waiting…", flush=True)
    page.wait_for_timeout(4000)

    current_url = page.url
    print(f"After login URL: {current_url}", flush=True)

    needs_2fa = (
        "checkpoint" in current_url
        or "challenge" in current_url
        or page.query_selector("input[name='pin']") is not None
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
                    pin = (
                        page.query_selector("input[name='pin']")
                        or page.query_selector("input[autocomplete='one-time-code']")
                        or page.query_selector("input[type='tel']")
                    )
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
        "feed" in final_url
        or final_url == "https://www.linkedin.com/"
        or "/in/" in final_url
        or "mynetwork" in final_url
        or "jobs" in final_url
    )

    if success:
        print("LOGIN_SUCCESS", flush=True)
    else:
        print(f"LOGIN_FAILED: unexpected URL {final_url}", flush=True)
        sys.exit(1)

    ctx.close()
