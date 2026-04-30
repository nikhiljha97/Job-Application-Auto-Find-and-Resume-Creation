from __future__ import annotations

import re
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
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=headless,
                viewport={"width": 1440, "height": 1100},
                slow_mo=60,
            )
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
                    if key in self.known_job_keys:
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

    def _wait_for_login_if_needed(self, page: Any, headless: bool) -> None:
        login_markers = ["input#username", "input[name='session_key']", "text=Sign in"]
        needs_login = "login" in page.url.lower()
        for selector in login_markers:
            try:
                if page.locator(selector).count() > 0:
                    needs_login = True
                    break
            except Exception:
                continue
        if needs_login:
            if headless:
                raise RuntimeError("LinkedIn requires login. Set headless=false and run once so you can sign in.")
            print("\nLinkedIn login is required in the opened browser window.")
            print("Sign in manually, finish any security checks, then return here and press Enter.")
            input("Press Enter after LinkedIn jobs search is visible...")

    def _load_visible_cards(self, page: Any) -> None:
        for _ in range(10):
            try:
                page.evaluate(
                    """
                    () => {
                      const containers = [
                        document.querySelector('.jobs-search-results-list__list'),
                        document.querySelector('.jobs-search-results-list'),
                        document.querySelector('.scaffold-layout__list'),
                        document.querySelector('.jobs-search-results__list'),
                        document.scrollingElement
                      ].filter(Boolean);
                      for (const c of containers) c.scrollTop = c.scrollHeight;
                    }
                    """
                )
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
        self._fill_search_box(page, search_query)
        if location:
            print(f"Setting LinkedIn location to: {location}")
            self._fill_location_box(page, location)
        page.keyboard.press("Enter")
        page.wait_for_timeout(wait_ms)
        if bool(self.config.get("apply_filters_by_url", True)):
            print("Applying LinkedIn filters by URL: Last 24 hours, employment types except Internship, Entry-level + Manager")
            self._safe_goto(page, build_filtered_search_url(search_query, self.config), timeout=30_000)
            page.wait_for_timeout(wait_ms)
            return

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
                if "Timeout" not in type(exc).__name__ and "Timeout" not in str(exc):
                    raise
                print(f"LinkedIn navigation timed out; continuing with current page state ({attempt + 1}/{retries + 1}): {url}")
                try:
                    page.wait_for_timeout(3_000)
                except Exception:
                    pass
        return False

    def _fill_search_box(self, page: Any, query: str) -> None:
        selectors = [
            "input[placeholder*='Describe the job']",
            "input[aria-label*='Describe the job']",
            "input[placeholder*='Search jobs']",
            "input[aria-label*='Search jobs']",
            "input[placeholder*='Search']",
            "input[aria-label*='Search']",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count():
                    locator.click(timeout=5_000)
                    page.keyboard.press("Meta+A")
                    page.keyboard.press("Control+A")
                    locator.fill(query)
                    return
            except Exception:
                continue
        try:
            filled = page.evaluate(
                """
                (query) => {
                  const inputs = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]'));
                  const target = inputs.find((el) => {
                    const haystack = [
                      el.getAttribute('placeholder') || '',
                      el.getAttribute('aria-label') || '',
                      el.innerText || '',
                      el.value || ''
                    ].join(' ').toLowerCase();
                    const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    return visible && /(describe the job|search jobs|search)/i.test(haystack);
                  }) || inputs.find((el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                  if (!target) return false;
                  target.focus();
                  if (target.isContentEditable) {
                    target.innerText = query;
                  } else {
                    target.value = query;
                  }
                  target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: query }));
                  target.dispatchEvent(new Event('change', { bubbles: true }));
                  return true;
                }
                """,
                query,
            )
            if filled:
                return
        except Exception:
            pass
        raise RuntimeError("Could not find LinkedIn job search box.")

    def _fill_location_box(self, page: Any, location: str) -> None:
        selectors = [
            "input[placeholder*='City, state, or zip code']",
            "input[aria-label*='City, state, or zip code']",
            "input[placeholder*='Location']",
            "input[aria-label*='Location']",
            "input[name='location']",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count():
                    locator.click(timeout=5_000)
                    page.keyboard.press("Meta+A")
                    page.keyboard.press("Control+A")
                    locator.fill(location)
                    return
            except Exception:
                continue
        try:
            filled = page.evaluate(
                """
                (location) => {
                  const inputs = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]'));
                  const target = inputs.find((el) => {
                    const haystack = [
                      el.getAttribute('placeholder') || '',
                      el.getAttribute('aria-label') || '',
                      el.getAttribute('name') || '',
                      el.innerText || '',
                      el.value || ''
                    ].join(' ').toLowerCase();
                    const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    return visible && /(city, state, or zip code|location)/i.test(haystack);
                  });
                  if (!target) return false;
                  target.focus();
                  if (target.isContentEditable) {
                    target.innerText = location;
                  } else {
                    target.value = location;
                  }
                  target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: location }));
                  target.dispatchEvent(new Event('change', { bubbles: true }));
                  return true;
                }
                """,
                location,
            )
            if filled:
                return
        except Exception:
            pass
        print("Could not find LinkedIn location box; continuing with URL location filter.")

    def _apply_filter(self, page: Any, filter_name: str, option_names: list[str], wait_ms: int) -> None:
        self._click_filter_button(page, filter_name)
        page.wait_for_timeout(1_500)
        for option in option_names:
            self._select_filter_option(page, option)
            page.wait_for_timeout(500)
        self._click_show_results(page)
        page.wait_for_timeout(wait_ms)

    def _click_filter_button(self, page: Any, filter_name: str) -> None:
        try:
            button = page.get_by_role("button", name=re.compile(filter_name, re.I)).first
            if button.count():
                button.click(timeout=8_000)
                return
        except Exception:
            pass
        for selector in [f"button:has-text('{filter_name}')", f"[role='button']:has-text('{filter_name}')"]:
            locator = page.locator(selector).first
            try:
                if locator.count():
                    locator.click(timeout=8_000)
                    return
            except Exception:
                continue
        raise RuntimeError(f"Could not open LinkedIn filter: {filter_name}")

    def _has_filter_button(self, page: Any, filter_name: str) -> bool:
        try:
            if page.get_by_role("button", name=re.compile(filter_name, re.I)).count():
                return True
        except Exception:
            pass
        try:
            return page.locator(f"button:has-text('{filter_name}')").count() > 0
        except Exception:
            return False

    def _select_filter_option(self, page: Any, option_name: str) -> None:
        try:
            checkbox = page.get_by_role("checkbox", name=re.compile(rf"^{re.escape(option_name)}$", re.I)).first
            if checkbox.count():
                checkbox.check(timeout=5_000)
                return
        except Exception:
            pass
        try:
            page.get_by_text(re.compile(rf"^{re.escape(option_name)}$", re.I)).first.click(timeout=5_000)
        except Exception as exc:
            print(f"Could not select filter option '{option_name}': {exc}")

    def _click_show_results(self, page: Any) -> None:
        try:
            page.get_by_role("button", name=re.compile("Show results", re.I)).last.click(timeout=8_000)
            return
        except Exception:
            pass
        page.locator("button:has-text('Show results')").last.click(timeout=8_000)

    def _go_to_results_page(self, page: Any, page_number: int) -> bool:
        wait_ms = int(float(self.config.get("wait_seconds_after_action", 10)) * 1000)
        selectors = [
            f"button[aria-label='Page {page_number}']",
            f"li button:has-text('{page_number}')",
            f"button:has-text('{page_number}')",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count():
                    locator.scroll_into_view_if_needed(timeout=5_000)
                    locator.click(timeout=8_000)
                    page.wait_for_timeout(wait_ms)
                    return True
            except Exception:
                continue
        try:
            page.get_by_role("button", name=re.compile("Next", re.I)).click(timeout=8_000)
            page.wait_for_timeout(wait_ms)
            return True
        except Exception:
            return False


def with_start(url: str, start: int) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if start <= 0:
        params.pop("start", None)
    else:
        params["start"] = str(start)
    query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=query))


def normalize_search_url(url: str) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if parsed.path.rstrip("/") == "/jobs/search-results":
        params.pop("currentJobId", None)
        params.pop("referralSearchId", None)
        params.setdefault("origin", "JOB_SEARCH_PAGE_SEARCH_BUTTON")
        return urlunparse(parsed._replace(path="/jobs/search/", query=urlencode(params, doseq=True)))
    return url


def build_search_url(query: str, location: str = "") -> str:
    params = {
        "keywords": query,
        "origin": "JOB_SEARCH_PAGE_SEARCH_BUTTON",
    }
    if location:
        params["location"] = location
    return "https://www.linkedin.com/jobs/search/?" + urlencode(
        params
    )


def build_filtered_search_url(query: str, config: dict[str, Any]) -> str:
    params: dict[str, str] = {
        "keywords": query,
        "origin": "JOB_SEARCH_PAGE_SEARCH_BUTTON",
    }
    location = str(config.get("linkedin_location", "Canada")).strip()
    if location:
        params["location"] = location
    if str(config.get("date_posted", "")).lower() == "past 24 hours":
        params["f_TPR"] = "r86400"

    employment_codes = {
        "full-time": "F",
        "part-time": "P",
        "contract": "C",
        "temporary": "T",
        "volunteer": "V",
        "other": "O",
        "internship": "I",
    }
    selected_employment = [
        employment_codes[item.strip().lower()]
        for item in config.get("employment_types", [])
        if item.strip().lower() in employment_codes
    ]
    if selected_employment:
        params["f_JT"] = ",".join(selected_employment)

    experience_codes = {
        "internship": "1",
        "entry-level": "2",
        "entry level": "2",
        "senior": "3",
        "manager": "4",
        "director": "5",
        "executive": "6",
    }
    selected_experience = [
        experience_codes[item.strip().lower()]
        for item in config.get("experience_levels", [])
        if item.strip().lower() in experience_codes
    ]
    if selected_experience:
        params["f_E"] = ",".join(selected_experience)

    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


def normalize_job_id(job_id: str, url: str) -> str:
    job_id = re.sub(r"\D", "", job_id or "")
    if job_id:
        return job_id
    match = re.search(r"/jobs/view/(\d+)", url or "")
    return match.group(1) if match else ""


def normalize_job_url(url: str, job_id: str = "") -> str:
    if url:
        full = urljoin("https://www.linkedin.com", url)
        match = re.search(r"/jobs/view/(\d+)", full)
        if match:
            return f"https://www.linkedin.com/jobs/view/{match.group(1)}/"
        return full
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}/"
    return ""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_title(value: str) -> str:
    title = clean_text(value)
    title = re.sub(r"\s+with verification\b", "", title, flags=re.IGNORECASE)
    words = title.split()
    if len(words) % 2 == 0:
        half = len(words) // 2
        if words[:half] == words[half:]:
            title = " ".join(words[:half])
    return title


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
    || (link && (link.href.match(/\\/jobs\\/view\\/(\\d+)/) || [])[1])
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
  const anchors = Array.from(document.querySelectorAll("a[href*='/jobs/view/']"));
  for (const link of anchors) {
    const href = link.href || "";
    const match = href.match(/\\/jobs\\/view\\/(\\d+)/);
    if (!match) continue;
    const jobId = match[1];
    if (seen.has(jobId)) continue;
    seen.add(jobId);

    const root = link.closest("li[data-occludable-job-id], .job-card-container, [data-job-id], li, div") || link;
    const text = (selector) => {
      const node = root.querySelector(selector);
      return node && node.innerText ? node.innerText.trim() : "";
    };
    rows.push({
      job_id: root.getAttribute("data-occludable-job-id") || root.dataset.jobId || jobId,
      title: text(".job-card-list__title, .job-card-container__link, a[href*='/jobs/view/']") || link.innerText.trim(),
      company: text(".artdeco-entity-lockup__subtitle, .job-card-container__primary-description, .job-card-container__company-name"),
      location: text(".artdeco-entity-lockup__caption, .job-card-container__metadata-item"),
      listed_at: text("time, .job-card-container__listed-time"),
      url: href,
      description: root.innerText || link.innerText || ""
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
  target.scrollTop = Math.min(target.scrollHeight, target.scrollTop + Math.max(650, target.clientHeight || 650));
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
    ".jobs-description__content",
    ".jobs-box__html-content",
    ".jobs-description-content__text",
    ".jobs-search__job-details--container",
    ".job-view-layout"
  ]);
  const link = document.querySelector("a[href*='/jobs/view/']");
  const easyApply = !!Array.from(document.querySelectorAll("button, a"))
    .find((node) => /easy apply/i.test(node.innerText || ""));
  return {
    title,
    company,
    location,
    description,
    url: link ? link.href : window.location.href,
    easy_apply: easyApply
  };
}
"""
