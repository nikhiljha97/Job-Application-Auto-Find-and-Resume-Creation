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
        use_ui_flow = bool(self.config.get("use_linkedin_ui_flow", False))
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
                    except Exception as exc:
                        print(f"  Navigation failed: {exc}")
                        break

                job_cards = page.query_selector_all(CARD_SELECTOR)
                if not job_cards:
                    print(f"  No job cards found on page {page_index + 1}; stopping.")
                    break

                new_on_page = 0
                for card in job_cards:
                    posting = self._extract_job(card, page)
                    if posting and posting.key() not in self.known_job_keys and posting.key() not in jobs:
                        jobs[posting.key()] = posting
                        new_on_page += 1

                print(f"  Found {new_on_page} new jobs on page {page_index + 1} ({len(jobs)} total)")
                if new_on_page == 0 and page_index > 0:
                    break

            context.close()
        return list(jobs.values())

    def _safe_goto(self, page: Any, url: str, timeout: int = 30_000) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except Exception:
            page.goto(url, timeout=timeout)

    def _extract_job(self, card: Any, page: Any) -> JobPosting | None:
        try:
            job_id = (
                card.get_attribute("data-occludable-job-id")
                or card.get_attribute("data-job-id")
                or ""
            )
            title_el = card.query_selector(
                ".job-card-list__title, "
                ".job-card-container__link, "
                "a[href*='/jobs/view/']"
            )
            title = title_el.inner_text().strip() if title_el else ""
            if not title:
                return None

            company_el = card.query_selector(
                ".job-card-container__primary-description, "
                ".artdeco-entity-lockup__subtitle"
            )
            company = company_el.inner_text().strip() if company_el else ""

            location_el = card.query_selector(
                ".job-card-container__metadata-item, "
                ".artdeco-entity-lockup__caption"
            )
            location = location_el.inner_text().strip() if location_el else ""

            href = title_el.get_attribute("href") if title_el else ""
            url = ""
            if job_id:
                url = f"https://www.linkedin.com/jobs/view/{job_id}/"
            elif href:
                url = urljoin("https://www.linkedin.com", href)

            applicant_el = card.query_selector(".jobs-unified-top-card__applicant-count")
            applicant_text = applicant_el.inner_text().strip() if applicant_el else ""

            easy_apply = bool(card.query_selector(".jobs-apply-button--top-card"))

            listed_el = card.query_selector("time")
            listed_at = listed_el.get_attribute("datetime") if listed_el else ""

            return JobPosting(
                job_id=job_id or url,
                title=title,
                company=company,
                location=location,
                url=url,
                description="",
                source_url=page.url,
                listed_at=listed_at,
                scraped_at=utc_now_iso(),
                easy_apply=easy_apply,
                applicant_count_text=applicant_text,
            )
        except Exception:
            return None

    def _open_search_with_filters(self, page: Any, headless: bool = True) -> None:
        cfg = self.config
        base = "https://www.linkedin.com/jobs/search/?"
        params: dict[str, str] = {}
        if cfg.get("search_query"):
            params["keywords"] = cfg["search_query"]
        if cfg.get("linkedin_location"):
            params["location"] = cfg["linkedin_location"]

        date_map = {
            "Past 24 hours": "r86400",
            "Past Week": "r604800",
            "Past Month": "r2592000",
        }
        if dp := date_map.get(cfg.get("date_posted", "")):
            params["f_TPR"] = dp

        emp_map = {
            "Full-time": "F",
            "Part-time": "P",
            "Contract": "C",
            "Temporary": "T",
            "Volunteer": "V",
            "Other": "O",
        }
        if types := cfg.get("employment_types", []):
            params["f_JT"] = ",".join(emp_map[t] for t in types if t in emp_map)

        exp_map = {
            "Internship": "1",
            "Entry-level": "2",
            "Associate": "3",
            "Mid-Senior level": "4",
            "Director": "5",
            "Executive": "6",
        }
        if levels := cfg.get("experience_levels", []):
            params["f_E"] = ",".join(exp_map[l] for l in levels if l in exp_map)

        params["sortBy"] = "DD"
        url = base + urlencode(params)
        self._safe_goto(page, url, timeout=30_000)

    def login_and_save_session(self) -> None:
        """Open a non-headless browser so the user can log in to LinkedIn manually."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install playwright first.") from exc

        with sync_playwright() as playwright:
            context = self._launch_context(playwright, headless=False)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.linkedin.com/login")
            print("Please log in to LinkedIn in the browser window, then press Enter here.")
            input()
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
        # Inject saved cookies (e.g. li_at pasted via the Streamlit UI)
        cookies_file = self.profile_dir / "cookies.json"
        if cookies_file.exists():
            import json as _json
            try:
                ctx.add_cookies(_json.loads(cookies_file.read_text()))
            except Exception as exc:
                print(f"Warning: could not inject saved cookies: {exc}")
        return ctx

    def _looks_like_profile_cache_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "failed to read prefs",
            "disk cache",
            "unable to map index file",
            "leveldb",
            "profile",
        )
        return any(m in text for m in markers)

    def _clear_profile_cache_artifacts(self) -> None:
        cache_path = self.profile_dir / "Default" / "Cache"
        if cache_path.exists():
            shutil.rmtree(cache_path, ignore_errors=True)
        for f in self.profile_dir.glob("*.lock"):
            f.unlink(missing_ok=True)

    def _clear_stale_profile_locks(self) -> None:
        for lock in self.profile_dir.glob("**/*.lock"):
            try:
                lock.unlink()
            except OSError:
                pass
        singleton = self.profile_dir / "SingletonLock"
        singleton.unlink(missing_ok=True)


def normalize_search_url(url: str) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query))
    params.pop("start", None)
    new_query = urlencode(params)
    return urlunparse(parsed._replace(query=new_query))


def with_start(url: str, start: int) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query))
    if start:
        params["start"] = str(start)
    else:
        params.pop("start", None)
    return urlunparse(parsed._replace(query=urlencode(params)))
