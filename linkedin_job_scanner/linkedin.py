from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
                print(f"  Page URL after login check: {page.url}", flush=True)

                card_data = self._collect_current_page_results(page)
                print(f"  Found {len(card_data)} job cards on page {page_index + 1}")

                if not card_data:
                    try:
                        snippet = page.evaluate("() => document.title + ' | ' + document.body.innerText.slice(0, 300)")
                        print(f"  Page snippet: {snippet[:300]}", flush=True)
                    except Exception:
                        pass
                    print(f"  No job cards found on page {page_index + 1}; stopping pagination.")
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
                        print(f"  Could not open job {index}: {job_url} ({exc})")
                        continue

                    details = self._extract_details(detail_page)
                    title = details.get("title") or data.get("title") or ""
                    company = details.get("company") or data.get("company") or ""
                    location = details.get("location") or data.get("location") or ""
                    description = details.get("description") or data.get("description") or ""
                    final_url = normalize_job_url(details.get("url") or job_url, job_id)
                    accepting = bool(details.get("accepting_applications", True)) and bool(data.get("accepting_applications", True))
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
                        accepting_applications=accepting,
                        application_status=application_status,
                        applicant_count=applicant_count,
                        applicant_count_text=applicant_count_text,
                    )
                    page_job_count += 1
                detail_page.close()

                print(f"  Collected {page_job_count} new jobs from page {page_index + 1}.")
                if page_job_count == 0:
                    break
                if use_ui_flow and page_index < max_pages - 1:
                    if not self._go_to_next_page(page, page_index + 2):
                        print("  No next results page found; stopping pagination.")
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
                    cnt = optional_int(details.get("applicant_count"))
                    if cnt is not None:
                        job.applicant_count = cnt
                    cnt_text = clean_text(str(details.get("applicant_count_text", "")))
                    if cnt_text:
                        job.applicant_count_text = cnt_text
                    if index % 5 == 0 or index == len(jobs):
                        print(f"Revalidated {index}/{len(jobs)} jobs.")
                except Exception as exc:
                    print(f"Could not revalidate job {index} ({job.url}): {exc}")
            context.close()

    # ------------------------------------------------------------------ context

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
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                # Suppress the automation flag that LinkedIn detects
                "--disable-blink-features=AutomationControlled",
            ],
            # Suppress navigator.webdriver so LinkedIn's bot detection doesn't block
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            slow_mo=60,
        )
        # Override navigator.webdriver on every new page so LinkedIn can't detect headless
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        cookies_file = self.profile_dir / "cookies.json"
        if cookies_file.exists() and not self._profile_has_linkedin_session(ctx):
            import json as _json
            try:
                ctx.add_cookies(_json.loads(cookies_file.read_text()))
                print("Injected saved LinkedIn cookie into fresh profile session.", flush=True)
                try:
                    cookies_file.unlink()
                    print("cookies.json removed — profile session is now self-contained.", flush=True)
                except Exception:
                    pass
            except Exception as exc:
                print(f"Warning: could not inject saved cookies: {exc}")
        return ctx

    def _profile_has_linkedin_session(self, ctx: Any) -> bool:
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

    # ------------------------------------------------------------------ login

    def _wait_for_login_if_needed(self, page: Any, headless: bool) -> None:
        needs_login = "login" in page.url.lower() or "authwall" in page.url.lower()
        if not needs_login:
            for selector in ("input#username", "input[name='session_key']"):
                try:
                    if page.locator(selector).count() > 0:
                        needs_login = True
                        break
                except Exception:
                    continue

        if not needs_login:
            return

        cookies_file = self.profile_dir / "cookies.json"
        if cookies_file.exists():
            import json as _json
            try:
                page.context.add_cookies(_json.loads(cookies_file.read_text()))
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2_000)
                if "login" not in page.url.lower() and "authwall" not in page.url.lower():
                    print("LinkedIn session restored from saved cookies.", flush=True)
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

    # ------------------------------------------------------------------ profile

    def _clear_stale_profile_locks(self) -> None:
        lock = self.profile_dir / "SingletonLock"
        if not lock.exists() and not lock.is_symlink():
            return
        if lock.is_symlink():
            target = os.readlink(lock)
            maybe_pid = target.rsplit("-", 1)[-1]
            if maybe_pid.isdigit() and _pid_is_running(int(maybe_pid)):
                return
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            path = self.profile_dir / name
            try:
                path.unlink()
                print(f"Removed stale LinkedIn browser profile lock: {path.name}")
            except FileNotFoundError:
                pass

    def _clear_profile_cache_artifacts(self) -> None:
        artifact_names = (
            "Local State",
            "RunningChromeVersion",
            "GrShaderCache",
            "ShaderCache",
            "GraphiteDawnCache",
            "Default/GPUCache",
            "Default/Cache",
            "Default/Code Cache",
            "Default/DawnCache",
            "Default/Service Worker/CacheStorage",
        )
        for name in artifact_names:
            artifact = self.profile_dir / name
            try:
                if artifact.is_dir() and not artifact.is_symlink():
                    shutil.rmtree(artifact)
                    print(f"Removed stale Chromium profile artifact: {name}")
                else:
                    artifact.unlink()
                    print(f"Removed stale Chromium profile artifact: {name}")
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------ scraping

    def _wait_for_job_cards(self, page: Any, timeout_ms: int = 20_000) -> None:
        """Wait for LinkedIn's React SPA to render job card links before scraping."""
        try:
            page.wait_for_selector("a[href*='/jobs/view/']", timeout=timeout_ms)
        except Exception:
            pass  # proceed anyway; some pages may genuinely have zero results

    def _load_visible_cards(self, page: Any) -> None:
        for _ in range(10):
            try:
                page.evaluate(SCROLL_RESULTS_LIST_JS)
                page.wait_for_timeout(600)
            except Exception:
                break
        try:
            page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass

    def _extract_details(self, page: Any) -> dict[str, Any]:
        try:
            return page.evaluate(DETAIL_EVALUATE_JS)
        except Exception:
            return {}

    def _collect_search_results(self, page: Any) -> list[dict[str, Any]]:
        try:
            return page.evaluate(COLLECT_SEARCH_RESULTS_JS)
        except Exception:
            return []

    def _collect_current_page_results(self, page: Any) -> list[dict[str, Any]]:
        self._wait_for_job_cards(page)
        seen: dict[str, dict[str, Any]] = {}
        for _ in range(14):
            for item in self._collect_search_results(page):
                job_id = normalize_job_id(str(item.get("job_id", "")), str(item.get("url", "")))
                url = normalize_job_url(str(item.get("url", "")), job_id)
                key = job_id or url
                if key:
                    seen[key] = item
            try:
                page.evaluate(SCROLL_RESULTS_LIST_JS)
                page.wait_for_timeout(800)
            except Exception:
                break
        try:
            page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass
        return list(seen.values())

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
            filtered_url = build_filtered_search_url(search_query, self.config)
            print(f"Applying LinkedIn filters by URL: {filtered_url}")
            self._safe_goto(page, filtered_url, timeout=30_000)
            page.wait_for_timeout(wait_ms)
            print(f"  After filter URL navigation, page URL: {page.url}", flush=True)
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
                    print(f"Navigation superseded; continuing: {url}")
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=5_000)
                    except Exception:
                        page.wait_for_timeout(3_000)
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

    def _go_to_next_page(self, page: Any, page_number: int) -> bool:
        try:
            btn = page.locator(f'button[aria-label="Page {page_number}"]')
            if btn.count() > 0:
                btn.first.click()
                page.wait_for_timeout(3_000)
                return True
            btn2 = page.locator('button[aria-label="Next"]')
            if btn2.count() > 0:
                btn2.first.click()
                page.wait_for_timeout(3_000)
                return True
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def normalize_search_url(url: str) -> str:
    if not url or url == "sample://linkedin":
        return url
    if not urlparse(url).scheme:
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
    if job_id and re.match(r"^\d+$", job_id):
        return job_id
    m = re.search(r"/jobs/view/(\d+)", url)
    return m.group(1) if m else job_id


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
    params: dict[str, str] = {"keywords": query, "location": location}
    date_map = {"Past 24 hours": "r86400", "Past week": "r604800", "Past month": "r2592000",
                "Past Week": "r604800", "Past Month": "r2592000"}
    params["f_TPR"] = date_map.get(str(config.get("date_posted", "")), "r86400")
    emp_map = {"Full-time": "F", "Part-time": "P", "Contract": "C", "Temporary": "T",
               "Internship": "I", "Volunteer": "V", "Other": "O"}
    employment_types = [str(e) for e in config.get("employment_types", ["Full-time", "Contract", "Part-time"])]
    emp_codes = [emp_map[e] for e in employment_types if e in emp_map]
    if emp_codes:
        params["f_JT"] = ",".join(emp_codes)
    exp_map = {"Internship": "1", "Entry level": "2", "Entry-level": "2", "Associate": "3",
               "Mid-Senior level": "4", "Director": "5", "Executive": "6", "Manager": "4"}
    experience_levels = [str(e) for e in config.get("experience_levels", ["Entry level", "Associate", "Mid-Senior level", "Director"])]
    exp_codes = [exp_map[e] for e in experience_levels if e in exp_map]
    if exp_codes:
        params["f_E"] = ",".join(exp_codes)
    return f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"


def clean_text(text: str) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


def clean_title(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"\s*·\s*Easy Apply\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# JavaScript constants
# Note: JS regex forward slashes written as [/] to avoid Python escape warning
# ---------------------------------------------------------------------------

CARD_EVALUATE_JS = """
(el) => {
  const root = el.closest("li[data-occludable-job-id], .job-card-container, [data-job-id], li, div") || el;
  const text = (selector) => {
    const node = root.querySelector(selector);
    return node ? node.innerText.trim() : "";
  };
  const link = el.matches && el.matches("a[href*='/jobs/view/']")
    ? el
    : root.querySelector("a[href*='/jobs/view/']");
  const jobId = root.getAttribute("data-occludable-job-id")
    || root.dataset.jobId
    || (link && (link.href.match(/[/]jobs[/]view[/](\\d+)/) || [])[1])
    || "";
  return {
    job_id: jobId,
    title: text(".job-card-list__title, .job-card-container__link, a[href*='/jobs/view/']") || (link ? link.innerText.trim() : ""),
    company: text(".artdeco-entity-lockup__subtitle, .job-card-container__primary-description, .job-card-container__company-name"),
    location: text(".artdeco-entity-lockup__caption, .job-card-container__metadata-item"),
    listed_at: text("time, .job-card-container__listed-time"),
    url: link ? link.href : "",
    description: root.innerText || el.innerText || ""
  };
}
"""


COLLECT_SEARCH_RESULTS_JS = """
() => {
  const seen = new Set();
  const rows = [];
  const parseApplicants = (text) => {
    const compact = (text || "").replace(/\\s+/g, " ");
    const patterns = [
      /over\\s+([\\d,]+)\\s+(?:applicants?|people clicked apply)/i,
      /be among (?:the )?first\\s+([\\d,]+)\\s+(?:applicants?|people clicked apply)/i,
      /([\\d,]+)\\s+(?:applicants?|people clicked apply)/i
    ];
    for (const pattern of patterns) {
      const match = compact.match(pattern);
      if (!match) continue;
      const value = parseInt(match[1].replace(/,/g, ""), 10);
      if (!Number.isFinite(value)) continue;
      return {
        count: pattern.source.startsWith("over") ? value + 1 : value,
        text: match[0]
      };
    }
    return { count: null, text: "" };
  };
  const anchors = Array.from(document.querySelectorAll("a[href*='/jobs/view/']"));
  for (const link of anchors) {
    const href = link.href || "";
    const match = href.match(/[/]jobs[/]view[/](\\d+)/);
    if (!match) continue;
    const jobId = match[1];
    if (seen.has(jobId)) continue;
    seen.add(jobId);
    const root = link.closest("li[data-occludable-job-id], .job-card-container, [data-job-id], li, div") || link;
    const text = (selector) => {
      const node = root.querySelector(selector);
      return node && node.innerText ? node.innerText.trim() : "";
    };
    const rowText = root.innerText || link.innerText || "";
    const applicants = parseApplicants(rowText);
    const noLongerAccepting = /no longer accepting applications?/i.test(rowText);
    rows.push({
      job_id: root.getAttribute("data-occludable-job-id") || root.dataset.jobId || jobId,
      title: text(".job-card-list__title, .job-card-container__link, a[href*='/jobs/view/']") || link.innerText.trim(),
      company: text(".artdeco-entity-lockup__subtitle, .job-card-container__primary-description, .job-card-container__company-name"),
      location: text(".artdeco-entity-lockup__caption, .job-card-container__metadata-item"),
      listed_at: text("time, .job-card-container__listed-time"),
      url: href,
      description: rowText,
      accepting_applications: !noLongerAccepting,
      application_status: noLongerAccepting ? "No Longer Accepting Applications" : "Unknown",
      applicant_count: applicants.count,
      applicant_count_text: applicants.text
    });
  }
  return rows;
}
"""


SCROLL_RESULTS_LIST_JS = """
() => {
  const candidates = [
    document.querySelector('.jobs-search-results-list__list'),
    document.querySelector('.jobs-search-results-list'),
    document.querySelector('.scaffold-layout__list'),
    document.querySelector('.jobs-search-results__list'),
    ...Array.from(document.querySelectorAll('div, ul')).filter((node) => {
      const style = window.getComputedStyle(node);
      return /(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 200;
    }),
    document.scrollingElement
  ].filter(Boolean);
  let target = candidates[0];
  for (const candidate of candidates) {
    if (candidate.querySelector && candidate.querySelector("a[href*='/jobs/view/']")) {
      target = candidate;
      break;
    }
  }
  if (target) target.scrollTop = Math.min(target.scrollHeight, target.scrollTop + Math.max(650, target.clientHeight || 650));
}
"""


DETAIL_EVALUATE_JS = """
() => {
  const pickText = (selectors) => {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      if (node && node.innerText && node.innerText.trim()) return node.innerText.trim();
    }
    return "";
  };
  const title = pickText([
    ".jobs-unified-top-card__job-title",
    ".job-details-jobs-unified-top-card__job-title",
    "h1"
  ]);
  const company = pickText([
    ".jobs-unified-top-card__company-name",
    ".job-details-jobs-unified-top-card__company-name",
    "a[href*='/company/']"
  ]);
  const location = pickText([
    ".jobs-unified-top-card__bullet",
    ".job-details-jobs-unified-top-card__primary-description-container",
    ".jobs-unified-top-card__primary-description"
  ]);
  const description = pickText([
    "#job-details",
    ".jobs-description-content__text",
    ".jobs-description__content"
  ]);
  const url = (document.querySelector('link[rel="canonical"]') || {}).href || window.location.href;
  const easyApplyBtn = document.querySelector(".jobs-apply-button--top-card, .jobs-s-apply button");
  const easyApply = easyApplyBtn ? /easy apply/i.test(easyApplyBtn.innerText) : false;
  const closedEl = document.querySelector(".artdeco-inline-feedback--error, .jobs-s-apply__application-closed");
  const hasApplyButton = !!document.querySelector(".jobs-apply-button, .jobs-s-apply");
  const noLongerText = /no longer accepting applications/i.test(document.body.innerText || "");
  const acceptingApplications = !closedEl && !noLongerText;
  const applicationStatus = noLongerText
    ? "No Longer Accepting Applications"
    : hasApplyButton
      ? "Accepting Applications"
      : "No Apply Button Found";
  const applicantEl = document.querySelector(
    ".jobs-unified-top-card__applicant-count, .tvm__text--neutral, .jobs-details-top-card__apply-count, .jobs-unified-top-card__subtitle-secondary-grouping"
  );
  const applicantText = applicantEl ? applicantEl.innerText.trim() : "";
  const parseApplicants = (text) => {
    const compact = (text || "").replace(/\\s+/g, " ");
    const patterns = [
      /over\\s+([\\d,]+)\\s+(?:applicants?|people clicked apply)/i,
      /be among (?:the )?first\\s+([\\d,]+)\\s+(?:applicants?|people clicked apply)/i,
      /([\\d,]+)\\s+(?:applicants?|people clicked apply)/i
    ];
    for (const pattern of patterns) {
      const match = compact.match(pattern);
      if (!match) continue;
      const value = parseInt(match[1].replace(/,/g, ""), 10);
      if (!Number.isFinite(value)) continue;
      return { count: value, text: match[0] };
    }
    return { count: null, text: "" };
  };
  const applicants = parseApplicants(applicantText);
  return {
    title, company, location, description, url,
    easy_apply: easyApply,
    accepting_applications: acceptingApplications,
    application_status: applicationStatus,
    applicant_count: applicants.count,
    applicant_count_text: applicants.text || applicantText
  };
}
"""
