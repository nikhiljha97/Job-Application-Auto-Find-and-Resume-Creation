from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable


STOPWORDS = {
    "a",
    "about",
    "across",
    "after",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "by",
    "can",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "its",
    "more",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "this",
    "to",
    "we",
    "with",
    "you",
    "your",
}


DEFAULT_SKILL_PHRASES = [
    "a/b testing",
    "advanced analytics",
    "agile",
    "ai",
    "ai agent workflows",
    "ai-powered analytics",
    "alteryx",
    "analytics automation",
    "audience segmentation",
    "basket analysis",
    "business analysis",
    "business analyst",
    "business case",
    "business intelligence",
    "campaign measurement",
    "category insights",
    "category management",
    "causal estimation",
    "churn modelling",
    "classification models",
    "competitive analysis",
    "confluence",
    "consumer analytics",
    "consumer behavior",
    "consumer insights",
    "cross-functional collaboration",
    "cross-shopping analysis",
    "customer analytics",
    "dashboard development",
    "data analysis",
    "data analyst",
    "data automation",
    "data cleansing",
    "data mining",
    "data modelling",
    "data visualization",
    "decision intelligence",
    "digital transformation",
    "etl",
    "excel",
    "executive reporting",
    "financial modelling",
    "forecasting",
    "fraud analytics",
    "google analytics",
    "hypothesis testing",
    "insight delivery",
    "jira",
    "kpi design",
    "kpi reporting",
    "looker studio",
    "machine learning",
    "market research",
    "market share analysis",
    "marketing analytics",
    "measurement framework",
    "natural language processing",
    "new to brand",
    "new to category",
    "nlp",
    "okr",
    "operations",
    "path-to-purchase",
    "performance analytics",
    "power bi",
    "predictive analytics",
    "predictive modelling",
    "process automation",
    "propensity scoring",
    "python",
    "r",
    "regression analysis",
    "reporting automation",
    "retail analytics",
    "retail media",
    "risk analytics",
    "scenario analysis",
    "segmentation",
    "shopper insights",
    "sql",
    "stakeholder management",
    "statistical analysis",
    "statistical modelling",
    "strategy",
    "strategy and operations",
    "tableau",
    "text mining",
    "user stories",
    "window functions",
]


def normalize_text(text: str) -> str:
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#./%\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text)
    tokens = re.findall(r"[a-z0-9+#./%-]+", normalized)
    return [t for t in tokens if len(t) > 1 and t not in STOPWORDS]


def phrase_in_text(phrase: str, text: str) -> bool:
    phrase_norm = normalize_text(phrase)
    text_norm = normalize_text(text)
    if not phrase_norm:
        return False
    if phrase_norm in text_norm:
        return True
    phrase_tokens = set(tokenize(phrase_norm))
    if not phrase_tokens:
        return False
    text_tokens = set(tokenize(text_norm))
    return phrase_tokens.issubset(text_tokens)


def extract_keywords(text: str, vocabulary: Iterable[str] | None = None, top_n: int = 70) -> list[str]:
    normalized = normalize_text(text)
    vocab = list(vocabulary or DEFAULT_SKILL_PHRASES)
    scored: Counter[str] = Counter()

    for phrase in vocab:
        if phrase_in_text(phrase, normalized):
            phrase_norm = normalize_text(phrase)
            length_bonus = min(4, max(1, len(phrase_norm.split())))
            scored[phrase_norm] += 5 + length_bonus

    tokens = [t for t in tokenize(normalized) if len(t) > 2 and not t.isdigit()]
    unigram_counts = Counter(tokens)
    for token, count in unigram_counts.items():
        if count >= 2 or token in {"sql", "python", "tableau", "strategy", "analytics", "insights"}:
            scored[token] += count

    for n in (2, 3):
        for grams in zip(*(tokens[i:] for i in range(n))):
            if any(g in STOPWORDS for g in grams):
                continue
            phrase = " ".join(grams)
            if len(phrase) < 7:
                continue
            scored[phrase] += 1

    ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
    return [term for term, _score in ranked[:top_n]]


def weighted_coverage(required_terms: Iterable[str], text: str) -> tuple[float, list[str], list[str]]:
    terms = unique_preserve_order(required_terms)
    if not terms:
        return 0.0, [], []
    matched: list[str] = []
    missing: list[str] = []
    total_weight = 0.0
    matched_weight = 0.0
    for term in terms:
        weight = 1.0 + min(2.0, math.log(max(1, len(tokenize(term))), 2))
        total_weight += weight
        if phrase_in_text(term, text):
            matched.append(term)
            matched_weight += weight
        else:
            missing.append(term)
    return matched_weight / total_weight if total_weight else 0.0, matched, missing


def cosine_similarity(a: str, b: str) -> float:
    a_counts = Counter(tokenize(a))
    b_counts = Counter(tokenize(b))
    if not a_counts or not b_counts:
        return 0.0
    common = set(a_counts) & set(b_counts)
    dot = sum(a_counts[t] * b_counts[t] for t in common)
    mag_a = math.sqrt(sum(v * v for v in a_counts.values()))
    mag_b = math.sqrt(sum(v * v for v in b_counts.values()))
    if not mag_a or not mag_b:
        return 0.0
    return dot / (mag_a * mag_b)


def unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = normalize_text(str(item))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def clamp_score(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 2)


def safe_filename(value: str, fallback: str = "job") -> str:
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip()
    value = re.sub(r"\s+", "_", value)
    value = value[:120].strip("._-")
    return value or fallback
