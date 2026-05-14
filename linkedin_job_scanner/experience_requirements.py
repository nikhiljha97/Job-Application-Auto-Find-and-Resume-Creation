from __future__ import annotations

import re
from dataclasses import dataclass


NUMBER_WORDS = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "eleven": 11.0,
    "twelve": 12.0,
    "thirteen": 13.0,
    "fourteen": 14.0,
    "fifteen": 15.0,
}

NUMBER_PATTERN = r"(?:\d+(?:\.\d+)?|" + "|".join(NUMBER_WORDS) + r")"


@dataclass(frozen=True)
class ExperienceRequirement:
    minimum_years: float
    maximum_years: float | None
    phrase: str


def find_experience_requirement(text: str) -> ExperienceRequirement | None:
    normalized = _normalize(text)
    candidates: list[ExperienceRequirement] = []
    range_spans: list[tuple[int, int]] = []

    # 6-8 years, 6 - 10 yrs, 8 to 12 years, eight (8) to twelve (12) years
    range_pattern = re.compile(
        rf"(?P<first>{NUMBER_PATTERN})(?:\s*\(\s*(?P<first_paren>\d+(?:\.\d+)?)\s*\))?"
        rf"\s*(?:-|–|—|to)\s*"
        rf"(?P<second>{NUMBER_PATTERN})(?:\s*\(\s*(?P<second_paren>\d+(?:\.\d+)?)\s*\))?"
        rf"\s*(?:\+|plus)?\s*(?:years?|yrs?)"
        rf"(?:\s+(?:of\s+)?(?:relevant\s+)?(?:work\s+)?experience)?",
        re.IGNORECASE,
    )
    for match in range_pattern.finditer(normalized):
        first = _number(match.group("first_paren") or match.group("first"))
        second = _number(match.group("second_paren") or match.group("second"))
        if first is not None and second is not None:
            range_spans.append(match.span())
            candidates.append(ExperienceRequirement(min(first, second), max(first, second), _phrase(match)))

    # 6+ years, 6 plus years, 6 or more years, six (6) years
    direct_pattern = re.compile(
        rf"(?P<years>{NUMBER_PATTERN})(?:\s*\(\s*(?P<paren>\d+(?:\.\d+)?)\s*\))?"
        rf"\s*(?:\+|plus|or\s+more)?\s*(?:years?|yrs?)"
        rf"(?:\s+(?:of\s+)?(?:relevant\s+)?(?:professional\s+)?(?:work\s+)?experience)?",
        re.IGNORECASE,
    )
    for match in direct_pattern.finditer(normalized):
        if _overlaps(match.span(), range_spans):
            continue
        years = _number(match.group("paren") or match.group("years"))
        if years is not None:
            candidates.append(ExperienceRequirement(years, None, _phrase(match)))

    # at least 6 years, minimum of 6 years, required: 8 years
    prefixed_pattern = re.compile(
        rf"(?:at\s+least|minimum(?:\s+of)?|requires?|required|preferred|must\s+have)\s+"
        rf"(?P<years>{NUMBER_PATTERN})(?:\s*\(\s*(?P<paren>\d+(?:\.\d+)?)\s*\))?"
        rf"\s*(?:\+|plus|or\s+more)?\s*(?:years?|yrs?)",
        re.IGNORECASE,
    )
    for match in prefixed_pattern.finditer(normalized):
        years = _number(match.group("paren") or match.group("years"))
        if years is not None:
            candidates.append(ExperienceRequirement(years, None, _phrase(match)))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item.minimum_years)


def exceeds_experience_limit(text: str, max_allowed_years: float = 5.99) -> bool:
    requirement = find_experience_requirement(text)
    return bool(requirement and requirement.minimum_years > max_allowed_years)


def requirement_label(requirement: ExperienceRequirement | None) -> str:
    if not requirement:
        return ""
    if requirement.maximum_years is not None:
        return f"{requirement.minimum_years:g}-{requirement.maximum_years:g} years"
    return f"{requirement.minimum_years:g}+ years"


def _normalize(text: str) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split()).lower()


def _number(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    if cleaned in NUMBER_WORDS:
        return NUMBER_WORDS[cleaned]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _phrase(match: re.Match[str]) -> str:
    return " ".join(match.group(0).split())


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in spans)
