from __future__ import annotations

import re
import time
from typing import Any

from playwright.sync_api import sync_playwright, Page, BrowserContext

from .models import JobPosting, utc_now_iso


INDEED_PROFILE_DIR = ".indeed_profile"


def _parse_applicant_count(text: str) -> int | None:
    m = re.search(r"([\d,]+)", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


class IndeedScanner:
    def __init__(self, config: dict[str, Any], known_job_keys: set[str] | None = None) -> None:
        self.config = config
        self.known_job_keys = known_job_keys or set()
        self.profile_dir = str(
            __import__("pathlib").Path(__file__).parent.parent / config.get("indeed_profile_dir", INDEED_PROFILE_DIR)
        )
        self.headless = bool(config.get("headless", True))
        self.wait_seconds = float(config.get("wait_seconds_after_action", 5))
        self.max_pages = int(config.get("indeed_max_pages", config.get("max_pages", 4)))
        self.search_query = config.get("search_query", "analyst")
        self.location = config.get("indeed_location", config.get("linkedin_location", "Canada"))

    def scan(self) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        with sync_playwright() as p:
            ctx: BrowserContext = p.chromium.launch_persistent_context(
                self.profile_dir,
                headless=self.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            try:
                jobs = self._scan_pages(page)
            finally:
                ctx.close()
        return jobs

    def _scan_pages(self, page: Page) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        query_enc = self.search_query.replace(" ", "+")
        loc_enc = self.location.replace(" ", "+")

        for page_num in range(self.max_pages):
            start = page_num * 10
            url = f"https://ca.indeed.com/jobs?q={query_enc}&l={loc_enc}&start={start}&sort=date"
            page.goto(url, timeout=30000)
            time.sleep(self.wait_seconds)

            cards = page.query_selector_all("div.job_seen_beacon, div[data-jk]")
            if not cards:
                break

            new_on_page = 0
            for card in cards:
                try:
                    job = self._parse_card(card, page)
                    if job and job.key() not in self.known_job_keys:
                        jobs.append(job)
                        new_on_page += 1
                except Exception:
                    pass

            if new_on_page == 0:
                break

        return jobs

    def _parse_card(self, card: Any, page: Page) -> JobPosting | None:
        job_id = card.get_attribute("data-jk") or ""
        title_el = card.query_selector("h2.jobTitle a, a[data-jk]")
        title = title_el.inner_text().strip() if title_el else ""
        if not title:
            return None

        company_el = card.query_selector("span[data-testid='company-name'], .companyName")
        company = company_el.inner_text().strip() if company_el else ""

        location_el = card.query_selector("div[data-testid='text-location'], .companyLocation")
        location = location_el.inner_text().strip() if location_el else ""

        snippet_el = card.query_selector("div.job-snippet, ul.jobsearch-ResultsList li")
        description = snippet_el.inner_text().strip() if snippet_el else ""

        if job_id:
            url = f"https://ca.indeed.com/viewjob?jk={job_id}"
        elif title_el:
            href = title_el.get_attribute("href") or ""
            url = f"https://ca.indeed.com{href}" if href.startswith("/") else href
        else:
            return None

        listed_el = card.query_selector("span.date, span[data-testid='myJobsStateDate']")
        listed_at = listed_el.inner_text().strip() if listed_el else ""

        return JobPosting(
            job_id=f"indeed-{job_id}" if job_id else url,
            title=title,
            company=company,
            location=location,
            url=url,
            description=description,
            source_url=f"https://ca.indeed.com/jobs?q={self.search_query}&l={self.location}",
            listed_at=listed_at,
            scraped_at=utc_now_iso(),
        )

    def login_and_save_session(self) -> None:
        """Open a non-headless browser so the user can log in to Indeed manually."""
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                self.profile_dir,
                headless=False,
                args=["--no-sandbox"],
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            page.goto("https://secure.indeed.com/account/login")
            print("Please log in to Indeed in the browser window, then press Enter here.")
            input()
            ctx.close()
