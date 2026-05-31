from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from .models import JobPosting, utc_now_iso


CARD_SELECTOR = (
    "li[data-occludable-job-id], "
    ".jobs-search-results__list-item, "
    ".job-card-container, "
    "[data-job-id], "
    "a[href*='/jobs/view/']"
)


class LinkedInScanner:
    def __init__(self, config: dict[str, Any], known_job_keys: set[str] | None = None) -> None:
        self.config = config
        self.profile_dir = Path(config["linkedin_profile_dir"])
        self.known_job_keys = known_job_keys or set()

    def scan(self) -> list[JobPosting]:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for LinkedIn scanning. Run:\n"
                "  python -m pip install -r job_scanner/requirements.txt\n"
                "  python -m playwright install chromium"
            ) from exc

        search_url = normalize_search_url(self.config["search_url"])
        max_pages = int(self.config.get("max_pages", 5))
        page_size = int(self.config.get("page_size", 25))
        headless = bool(self.config.get("headless", False))
        use_ui_flow = bool(self.config.get("use_linkedin_ui_flow", True))
        jobs: dict[str, JobPosting] = {}

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_profile_locks()
        with sync_playwright() as playwright:
            context = self._launch_context(playwright, headless)
            page = context.pages[0] if context.pages else context.new_page()
            if use_ui_flow:
                self._open_search_with_filters(page, headless=headless)

            for page_index in range(max_pages):
                page_url = page.url if use_ui_flow else with_start(search_url, page_index * page_size)
                print(f"Scanning LinkedIn page {page_index + 1}/{max_pages}: start={page_index * page_size}")
                if not use_ui_flow:
                    try:
                        self._safe_goto(page, page_url, timeout=30_000)
                    except PlaywrightTimeoutError:
                        print("Page load timed out; continuing with whatever loaded.")

                self._wait_for_login_if_needed(page, headless=headless)
                self._load_visible_cards(page)

                card_data = self._collect_current_page_results(page)
                if not card_data:
                    details = self._extract_details(page)
                    direct_url = normalize_job_url(details.get("url", ""), "")
                    direct_job_id = normalize_job_id("", direct_url)
                    if details.get("title") and direct_url:
                        jobs[direct_job_id or direct_url] = JobPosting(
                            job_id=direct_job_id or direct_url,
                            title=clean_title(details.get("title", "")),
                            company=clean_text(details.get("company", "")),
                            location=clean_text(details.get("location", "")),
                            url=direct_url,
                            description=clean_text(details.get("description", "")),
                            source_url=page_url,
                            scraped_at=utc_now_iso(),
                            easy_apply=bool(details.get("easy_apply", False)),
                            accepting_applications=bool(details.get("accepting_applications", True)),
                            application_status=clean_text(str(details.get("application_status", "Unknown"))),
                            applicant_count=optional_int(details.get("applicant_count")),
                            applicant_count_text=clean_text(str(details.get("applicant_count_text", ""))),
                        )
                        print("Collected 1 direct job from this page.")
                        continue
                    print("No job cards found on this page; stopping pagination.")
                    break

                page_job_count = 0
                detail_page = context.new_page()
                for index, data in enumerate(card_data, start=1):
                    job_id = normalize_job_id(str(data.get("job_id", "")), str(data.get("url", "")))
                    job_url = normalize_job_url(str(data.get("url", "")), job_id)
                    key = job_id or job_url
                    if not key or key in jobs:
                        continue

                    try:
                        self._safe_goto(detail_page, job_url, timeout=20_000, retries=0)
                        detail_page.wait_for_timeout(1_200)
                    except Exception as exc:
                        print(f"Could not open job {index}: {job_url} ({exc})")
                        continue

                    details = self._extract_details(detail_page)
                    title = details.get("title") or data.get("title") or ""
                    company = details.get("company") or data.get("company") or ""
                    location = details.get("location") or data.get("location") or ""
                    description = details.get("description") or data.get("description") or ""
                    final_url = normalize_job_url(details.get("url") or job_url, job_id)
                    accepting_applications = bool(details.get("accepting_applications", True)) and bool(
                        data.get("accepting_applications", True)
                    )
                    applicant_count = optional_int(details.get("applicant_count"))
                    applicant_count_text = clean_text(str(details.get("applicant_count_text", "")))
                    if applicant_count is None:
                        applicant_count = optional_int(data.get("applicant_count"))
                    if not applicant_count_text:
                        applicant_count_text = clean_text(str(data.get("applicant_count_text", "")))
                    application_status = clean_text(str(details.get("application_status", "")))
                    if not application_status or application_status == "Unknown":
                        application_status = clean_text(str(data.get("application_status", "Unknown")))

                    if not title or not final_url:
                        continue
                    jobs[key] = JobPosting(
                        job_id=job_id or key,
                        title=clean_title(title),
                        company=clean_text(company),
                        location=clean_text(location),
                        url=final_url,
                        description=clean_text(description),
                        source_url=page_url,
                        scraped_at=utc_now_iso(),
                        listed_at=clean_text(str(data.get("listed_at", ""))),
                        easy_apply=bool(details.get("easy_apply", False)),
                        accepting_applications=accepting_applications,
                        application_status=application_status,
                        applicant_count=applicant_count,
                        applicant_count_text=applicant_count_text,
                    )
                    page_job_count += 1
                detail_page.close()

                print(f"Collected {page_job_count} new jobs from page {page_index + 1}.")
                if page_job_count == 0:
                    break
                if use_ui_flow and page_index < max_pages - 1:
                    if not self._go_to_results_page(page, page_index + 2):
                        print("No next results page found; stopping pagination.")
                        break

            context.close()

        return list(jobs.values())

    def revalidate_application_status(self, jobs: list[JobPosting]) -> None:
        if not jobs:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is required for LinkedIn job validation.") from exc

        headless = bool(self.config.get("headless", False))
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_profile_locks()
        with sync_playwright() as playwright:
            context = self._launch_context(playwright, headless)
            page = context.pages[0] if context.pages else context.new_page()
            timeout_ms = int(self.config.get("revalidate_job_timeout_ms", 8_000))
            for index, job in enumerate(jobs, start=1):
                try:
                    self._safe_goto(page, job.url, timeout=timeout_ms, retries=0)
                    self._wait_for_login_if_needed(page, headless=headless)
                    page.wait_for_timeout(1_000)
                    details = self._extract_details(page)
                    job.accepting_applications = bool(details.get("accepting_applications", job.accepting_applications))
                    job.application_status = clean_text(str(details.get("application_status", job.application_status)))
                    applicant_count = optional_int(details.get("applicant_count"))
                    if applicant_count is not None:
                        job.applicant_count = applicant_count
                    applicant_count_text = clean_text(str(details.get("applicant_count_text", "")))
                    if applicant_count_text:
                        job.applicant_count_text = applicant_count_text
                    if index % 5 == 0 or index == len(jobs):
                        print(f"Revalidated {index}/{len(jobs)} jobs.")
                except Exception as exc:
                    print(f"Could not revalidate job {index} ({job.url}): {exc}")
            context.close()

    def _launch_context(self, playwright: Any, headless: bool) -> Any:
        try:
            return self._launch_persistent_context(playwright, headless)
        except Exception as exc:
            if not self._looks_like_profile_cache_error(exc):
                raise
            print("Chromium profile cache/state looked stale; cleaning artifacts and retrying once.")
            self._clear_profile_cache_artifacts()
            self._clear_stale_profile_locks()
            return self._launch_persistent_context(playwright, headless)

    def _launch_persistent_context(self, playwright: Any, headless: bool) -> Any:
        ctx = playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=headless,
            viewport={"width": 1440, "height": 1100},
            slow_mo=60,
        )
        # Only inject cookies.json if the persistent profile doesn't already have
        # a live LinkedIn session. Re-injecting every run creates a new session
        # event that causes LinkedIn to invalidate the user's browser session.
        cookies_file = self.profile_dir / "cookies.json"
        if cookies_file.exists() and not self._profile_has_linkedin_session(ctx):
            import json as _json
            try:
                ctx.add_cookies(_json.loads(cookies_file.read_text()))
                print("Injected saved LinkedIn cookie into fresh profile session.", flush=True)
                # Delete cookies.json so future scans reuse the profile's stored
                # session instead of re-injecting (re-injection creates a new
                # LinkedIn session event that logs out the user's own browser).
                try:
                    cookies_file.unlink()
                    print("cookies.json removed — profile session is now self-contained.", flush=True)
                except Exception:
                    pass
            except Exception as exc:
                print(f"Warning: could not inject saved cookies: {exc}")
        return ctx

    def _profile_has_linkedin_session(self, ctx: Any) -> bool:
        """Return True if the persistent profile already has a live li_at cookie."""
        try:
            for cookie in ctx.cookies("https://www.linkedin.com"):
                if cookie.get("name") == "li_at" and cookie.get("value"):
                    return True
        except Exception:
            pass
        return False

    def _looks_like_profile_cache_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "failed to read prefs",
            "disk cache",
            "unable to map index file",
            "target page, context or browser has been closed",
        )
        return any(marker in text for marker in markers)

    def _wait_for_login_if_needed(self, page: Any, headless: bool) -> None:
        login_markers = ["input#username", "input[name='session_key']"]
        needs_login = "login" in page.url.lower() or "authwall" in page.url.lower()
        for selector in login_markers:
            try:
                if page.locator(selector).count() > 0:
                    needs_login = True
                    break
            except Exception:
                continue

        if not needs_login:
            return

        # Try re-injecting cookies and reloading before giving up
        cookies_file = self.profile_dir / "cookies.json"
        if cookies_file.exists():
            import json as _json
            try:
                page.context.add_cookies(_json.loads(cookies_file.read_text()))
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                still_needs_login = "login" in page.url.lower() or "authwall" in page.url.lower()
                if not still_needs_login:
                    print("LinkedIn session restored from saved cookies.", flush=True)
                    # Remove cookies.json so subsequent scans rely on the stored
                    # profile session instead of re-injecting and disturbing the
                    # user's own browser session.
                    try:
                        cookies_file.unlink()
                        print("cookies.json removed — profile session is now self-contained.", flush=True)
                    except Exception:
                        pass
                    return
            except Exception as exc:
                print(f"Cookie re-injection failed: {exc}", flush=True)

        if headless or not sys.stdin.isatty():
            raise RuntimeError(
                "LinkedIn requires login. Go to the LinkedIn Login page in the Streamlit app "
                "and save your li_at cookie or use Email & password login."
            )
        print("\nLinkedIn login is required in the opened browser window.")
        print("Sign in manually, finish any security checks, then return here and press Enter.")
        input("Press Enter after LinkedIn jobs search is visible...")

    def _clear_stale_profile_locks(self) -> None:
        lock = self.profile_dir / "SingletonLock"
        if not lock.exists() and not lock.is_symlink():
            return
        if lock.is_symlink():
            target = os.readlink(lock)
            maybe_pid = target.rsplit("-", 1)[-1]
            try:
                pid = int(maybe_pid)
                os.kill(pid, 0)
                return
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass

    def _clear_profile_cache_artifacts(self) -> None:
        for subdir in ("Cache", "Code Cache", "GPUCache", "DawnCache"):
            artifact = self.profile_dir / subdir
            if artifact.exists():
                shutil.rmtree(artifact, ignore_errors=True)

    def _load_visible_cards(self, page: Any) -> None:
        wait_ms = int(float(self.config.get("wait_seconds_after_action", 10)) * 1000)
        try:
            page.wait_for_selector(CARD_SELECTOR, timeout=wait_ms)
        except Exception:
            pass
        for _ in range(3):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1_500)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

    def _collect_current_page_results(self, page: Any) -> list[dict]:
        return page.evaluate(
            """
            () => {
                const cards = Array.from(document.querySelectorAll(
                    'li[data-occludable-job-id], .jobs-search-results__list-item, .job-card-container, [data-job-id]'
                ));
                return cards.map(card => {
                    const jobId = card.getAttribute('data-occludable-job-id')
                        || card.getAttribute('data-job-id')
                        || '';
                    const anchor = card.querySelector('a[href*="/jobs/view/"]');
                    const url = anchor ? anchor.href : '';
                    const titleEl = card.querySelector('.job-card-list__title, .job-card-container__link, h3, h4');
                    const title = titleEl ? titleEl.innerText.trim() : '';
                    const companyEl = card.querySelector('.job-card-container__company-name, .job-card-container__primary-description, [data-control-name="job_card_company_name"]');
                    const company = companyEl ? companyEl.innerText.trim() : '';
                    const locationEl = card.querySelector('.job-card-container__metadata-item, .artdeco-entity-lockup__caption');
                    const location = locationEl ? locationEl.innerText.trim() : '';
                    const listedEl = card.querySelector('time, .job-card-container__listed-status');
                    const listedAt = listedEl ? (listedEl.getAttribute('datetime') || listedEl.innerText.trim()) : '';
                    const easyApplyEl = card.querySelector('.job-card-container__apply-method');
                    const easyApply = easyApplyEl ? easyApplyEl.innerText.toLowerCase().includes('easy apply') : false;
                    const applicantEl = card.querySelector('.job-card-container__applicant-count, .tvm__text');
                    const applicantText = applicantEl ? applicantEl.innerText.trim() : '';
                    const applicantMatch = applicantText.match(/(\d[\d,]*)/);
                    const applicantCount = applicantMatch ? parseInt(applicantMatch[1].replace(/,/g, ''), 10) : null;
                    return { job_id: jobId, url, title, company, location, listed_at: listedAt, easy_apply: easyApply, applicant_count: applicantCount, applicant_count_text: applicantText, accepting_applications: true, application_status: 'Unknown' };
                }).filter(j => j.url || j.job_id);
            }
            """
        )

    def _extract_details(self, page: Any) -> dict:
        return page.evaluate(
            """
            () => {
                const titleEl = document.querySelector('.job-details-jobs-unified-top-card__job-title, h1.t-24, h1');
                const title = titleEl ? titleEl.innerText.trim() : '';
                const companyEl = document.querySelector('.job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name, a[data-tracking-control-name="public_jobs_topcard-org-name"]');
                const company = companyEl ? companyEl.innerText.trim() : '';
                const locationEl = document.querySelector('.job-details-jobs-unified-top-card__bullet, .jobs-unified-top-card__bullet, .job-details-jobs-unified-top-card__primary-description-container');
                const location = locationEl ? locationEl.innerText.trim() : '';
                const descEl = document.querySelector('.jobs-description-content__text, .jobs-description__content, #job-details');
                const description = descEl ? descEl.innerText.trim() : '';
                const easyApplyBtn = document.querySelector('.jobs-apply-button--top-card, .jobs-s-apply button');
                const easyApply = easyApplyBtn ? easyApplyBtn.innerText.toLowerCase().includes('easy apply') : false;
                const closedEl = document.querySelector('.artdeco-inline-feedback--error, .jobs-s-apply__application-closed');
                const acceptingApplications = !closedEl;
                const statusEl = document.querySelector('.jobs-s-apply__application-status, .artdeco-inline-feedback');
                const applicationStatus = statusEl ? statusEl.innerText.trim() : 'Unknown';
                const applicantEl = document.querySelector('.jobs-unified-top-card__applicant-count, .tvm__text--neutral, .jobs-details-top-card__apply-count');
                const applicantText = applicantEl ? applicantEl.innerText.trim() : '';
                const applicantMatch = applicantText.match(/(\d[\d,]*)/);
                const applicantCount = applicantMatch ? parseInt(applicantMatch[1].replace(/,/g, ''), 10) : null;
                const canonicalEl = document.querySelector('link[rel="canonical"]');
                const url = canonicalEl ? canonicalEl.href : window.location.href;
                return { title, company, location, description, url, easy_apply: easyApply, accepting_applications: acceptingApplications, application_status: applicationStatus, applicant_count: applicantCount, applicant_count_text: applicantText };
            }
            """
        )

    def _go_to_results_page(self, page: Any, page_number: int) -> bool:
        try:
            next_btn = page.locator(f'button[aria-label="Page {page_number}"]')
            if next_btn.count() > 0:
                next_btn.first.click()
                page.wait_for_timeout(3_000)
                return True
            next_btn2 = page.locator('button[aria-label="Next"]')
            if next_btn2.count() > 0:
                next_btn2.first.click()
                page.wait_for_timeout(3_000)
                return True
        except Exception:
            pass
        return False

    def _open_search_with_filters(self, page: Any, headless: bool) -> None:
        wait_ms = int(float(self.config.get("wait_seconds_after_action", 10)) * 1000)
        start_url = str(self.config.get("linkedin_start_url", "https://www.linkedin.com/jobs/"))
        search_query = str(self.config.get("search_query", "strategy OR insight OR insights OR Analyst"))
        location = str(self.config.get("linkedin_location", "Canada")).strip()
        print(f"Opening LinkedIn Jobs: {start_url}")
        self._safe_goto(page, start_url, timeout=30_000)
        self._wait_for_login_if_needed(page, headless=headless)
        page.wait_for_timeout(wait_ms)

        print(f"Searching LinkedIn for: {search_query}")
        if bool(self.config.get("apply_filters_by_url", True)):
            try:
                self._fill_search_box(page, search_query)
                if location:
                    print(f"Setting LinkedIn location to: {location}")
                    self._fill_location_box(page, location)
                page.keyboard.press("Enter")
                page.wait_for_timeout(wait_ms)
            except RuntimeError as exc:
                print(f"LinkedIn search box unavailable; using direct filtered search URL instead: {exc}")
            print("Applying LinkedIn filters by URL: Last 24 hours, employment types except Internship, Entry-level + Manager")
            self._safe_goto(page, build_filtered_search_url(search_query, self.config), timeout=30_000)
            page.wait_for_timeout(wait_ms)
            return

        self._fill_search_box(page, search_query)
        if location:
            print(f"Setting LinkedIn location to: {location}")
            self._fill_location_box(page, location)
        page.keyboard.press("Enter")
        page.wait_for_timeout(wait_ms)

        if not self._has_filter_button(page, "Date posted"):
            self._safe_goto(page, build_search_url(search_query, location), timeout=30_000)
            page.wait_for_timeout(wait_ms)

        date_posted = self.config.get("date_posted")
        if date_posted:
            print(f"Applying Date posted filter: {date_posted}")
            self._apply_filter(page, "Date posted", [str(date_posted)], wait_ms)

        employment_types = [str(item) for item in self.config.get("employment_types", [])]
        if employment_types:
            print("Applying Employment type filters: " + ", ".join(employment_types))
            self._apply_filter(page, "Employment type", employment_types, wait_ms)

        experience_levels = [str(item) for item in self.config.get("experience_levels", [])]
        if experience_levels:
            print("Applying Experience level filters: " + ", ".join(experience_levels))
            self._apply_filter(page, "Experience level", experience_levels, wait_ms)

    def _safe_goto(self, page: Any, url: str, timeout: int = 30_000, retries: int = 1) -> bool:
        for attempt in range(retries + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                return True
            except Exception as exc:
                message = str(exc)
                if "interrupted by another navigation" in message:
                    return True
                if attempt < retries:
                    page.wait_for_timeout(2_000)
                    continue
                raise
        return False

    def _fill_search_box(self, page: Any, query: str) -> None:
        selectors = [
            ".jobs-search-box__text-input[aria-label*='Search']",
            "input[role='combobox'][aria-label*='Search']",
            ".jobs-search-box__keyboard-text-input",
            "input[id*='jobs-search-box-keyword']",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    loc.first.triple_click()
                    loc.first.type(query, delay=40)
                    return
            except Exception:
                continue
        raise RuntimeError("LinkedIn search box not found")

    def _fill_location_box(self, page: Any, location: str) -> None:
        selectors = [
            ".jobs-search-box__text-input[aria-label*='City']",
            ".jobs-search-box__text-input[aria-label*='Location']",
            "input[role='combobox'][aria-label*='City']",
            "input[role='combobox'][aria-label*='Location']",
            "input[id*='jobs-search-box-location']",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    loc.first.triple_click()
                    loc.first.type(location, delay=40)
                    return
            except Exception:
                continue

    def _has_filter_button(self, page: Any, label: str) -> bool:
        try:
            return page.locator(f"button:has-text('{label}')").count() > 0
        except Exception:
            return False

    def _apply_filter(self, page: Any, filter_name: str, values: list[str], wait_ms: int) -> None:
        try:
            btn = page.locator(f"button:has-text('{filter_name}')")
            if btn.count() == 0:
                return
            btn.first.click()
            page.wait_for_timeout(1_000)
            for value in values:
                try:
                    option = page.locator(f"label:has-text('{value}')")
                    if option.count() > 0:
                        option.first.click()
                        page.wait_for_timeout(300)
                except Exception:
                    pass
            apply_btn = page.locator("button:has-text('Show results'), button:has-text('Apply')")
            if apply_btn.count() > 0:
                apply_btn.first.click()
                page.wait_for_timeout(wait_ms)
        except Exception as exc:
            print(f"Could not apply filter '{filter_name}': {exc}")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def normalize_search_url(url: str) -> str:
    if not url or url == "sample://linkedin":
        return url
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    return url


def normalize_job_url(url: str, job_id: str) -> str:
    if not url:
        if job_id:
            return f"https://www.linkedin.com/jobs/view/{job_id}/"
        return ""
    if url.startswith("/"):
        url = "https://www.linkedin.com" + url
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") + "/"
    return urlunparse(("https", "www.linkedin.com", path, "", "", ""))


def normalize_job_id(job_id: str, url: str) -> str:
    if job_id and job_id.isdigit():
        return job_id
    match = re.search(r"/jobs/view/(\d+)", url)
    if match:
        return match.group(1)
    return job_id


def with_start(url: str, start: int) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query))
    params["start"] = str(start)
    return urlunparse(parsed._replace(query=urlencode(params)))


def build_search_url(query: str, location: str) -> str:
    params = urlencode({"keywords": query, "location": location, "f_TPR": "r86400"})
    return f"https://www.linkedin.com/jobs/search/?{params}"


def build_filtered_search_url(query: str, config: dict) -> str:
    location = str(config.get("linkedin_location", "Canada")).strip()
    params: dict[str, str] = {
        "keywords": query,
        "location": location,
    }
    if config.get("date_posted"):
        date_map = {"Past 24 hours": "r86400", "Past week": "r604800", "Past month": "r2592000"}
        params["f_TPR"] = date_map.get(str(config["date_posted"]), "r86400")
    else:
        params["f_TPR"] = "r86400"
    emp_map = {"Full-time": "F", "Part-time": "P", "Contract": "C", "Temporary": "T", "Internship": "I", "Volunteer": "V", "Other": "O"}
    employment_types = [str(e) for e in config.get("employment_types", ["Full-time", "Contract", "Part-time"])]
    emp_codes = [emp_map[e] for e in employment_types if e in emp_map]
    if emp_codes:
        params["f_JT"] = ",".join(emp_codes)
    exp_map = {"Internship": "1", "Entry level": "2", "Associate": "3", "Mid-Senior level": "4", "Director": "5", "Executive": "6"}
    experience_levels = [str(e) for e in config.get("experience_levels", ["Entry level", "Associate", "Mid-Senior level", "Director"])]
    exp_codes = [exp_map[e] for e in experience_levels if e in exp_map]
    if exp_codes:
        params["f_E"] = ",".join(exp_codes)
    return f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def clean_text(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split())


def clean_title(text: str) -> str:
    text = clean_text(text)
    # Remove LinkedIn "· Easy Apply" suffix variants
    text = re.sub(r"\s*·\s*Easy Apply\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None
