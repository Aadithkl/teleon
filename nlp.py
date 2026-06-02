"""Local NLP enrichment — 100% offline, no API calls.

Tools:
  spaCy (en_core_web_sm)  — named entity extraction: persons, orgs, locations
  VADER (vaderSentiment)  — sentiment score per message + overall conversation
  langdetect              — language detection per conversation
  dateparser              — detect date references, extract follow-up due dates
  re (stdlib)             — extract emails, phone numbers, URLs, domains
  rapidfuzz               — fuzzy contact deduplication
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any


# ─── lazy loaders ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _spacy_nlp():
    try:
        import spacy
        return spacy.load("en_core_web_sm")
    except OSError:
        print("spaCy model missing. Run: uv run python -m spacy download en_core_web_sm",
              file=sys.stderr)
        return None
    except ImportError:
        return None


@lru_cache(maxsize=1)
def _vader():
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        return SentimentIntensityAnalyzer()
    except ImportError:
        return None


# ─── regex patterns ────────────────────────────────────────────────────────────

_EMAIL_RE  = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE  = re.compile(r"\+?[\d][\d\s\-().]{7,}\d")
_URL_RE    = re.compile(r"https?://[^\s]+")
_DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+(?:com|io|org|net|co|ai|xyz|dev|app)\b")

_FOLLOWUP_PATTERNS = [
    r"(?:follow up|follow-up|get back to you|send|check|call|meet|ping)\s.{0,60}"
    r"(?:by|on|before|next|this|tomorrow|monday|tuesday|wednesday|thursday|friday)\s[\w\s,]+",
    r"(?:let me know|i'll send|we'll discuss|i'll check).{0,80}"
    r"(?:by|on|next|this week|tomorrow)",
    r"(?:deadline|due|submit|deliver).{0,40}(?:by|on|before)\s[\w\s,]+",
]

_DATE_HINT_RE = re.compile(
    r"\b(?:today|tomorrow|yesterday|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|next\s+\w+|this\s+\w+|jan(?:uary)?|feb(?:ruary)?|"
    r"mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"\d{1,2}[\/\-]\d{1,2}|\d{4}[\/\-]\d{2}[\/\-]\d{2})\b",
    re.IGNORECASE,
)

_LANG_MAP = {
    "en": "English", "ar": "Arabic", "fr": "French", "es": "Spanish",
    "de": "German",  "tr": "Turkish", "pt": "Portuguese", "ru": "Russian",
    "zh-cn": "Chinese", "zh-tw": "Chinese", "hi": "Hindi", "ja": "Japanese",
    "ko": "Korean", "it": "Italian", "nl": "Dutch",
}


# ─── sentiment ────────────────────────────────────────────────────────────────

def score_sentiment(text: str) -> str:
    """Returns 'Positive', 'Neutral', or 'Negative' from a single text string."""
    analyzer = _vader()
    if not analyzer or not text:
        return "Neutral"
    score = analyzer.polarity_scores(text)["compound"]
    if score >= 0.05:
        return "Positive"
    if score <= -0.05:
        return "Negative"
    return "Neutral"


def sentiment_score(texts: list[str]) -> dict[str, Any]:
    """VADER across all message texts. Returns label + compound + breakdown."""
    analyzer = _vader()
    if not analyzer or not texts:
        return {"label": "Neutral", "compound": 0.0, "breakdown": {}}
    scores = [analyzer.polarity_scores(t) for t in texts if t]
    if not scores:
        return {"label": "Neutral", "compound": 0.0, "breakdown": {}}
    avg_c = sum(s["compound"] for s in scores) / len(scores)
    return {
        "label": "Positive" if avg_c >= 0.05 else "Negative" if avg_c <= -0.05 else "Neutral",
        "compound": round(avg_c, 3),
        "breakdown": {
            "positive": round(sum(s["pos"] for s in scores) / len(scores), 3),
            "neutral":  round(sum(s["neu"] for s in scores) / len(scores), 3),
            "negative": round(sum(s["neg"] for s in scores) / len(scores), 3),
        },
    }


# ─── entities ─────────────────────────────────────────────────────────────────

def extract_entities(texts: list[str]) -> dict[str, list[str]]:
    """spaCy NER across all texts. Returns {persons, orgs, locations}."""
    nlp = _spacy_nlp()
    if nlp is None:
        return {"persons": [], "orgs": [], "locations": []}
    persons: set[str] = set()
    orgs: set[str] = set()
    locations: set[str] = set()
    for text in texts:
        if not text:
            continue
        for ent in nlp(text[:1000]).ents:
            val = ent.text.strip()
            if not val:
                continue
            if ent.label_ == "PERSON":
                persons.add(val)
            elif ent.label_ in ("ORG", "PRODUCT", "WORK_OF_ART"):
                orgs.add(val)
            elif ent.label_ in ("GPE", "LOC", "FAC"):
                locations.add(val)
    return {"persons": sorted(persons), "orgs": sorted(orgs), "locations": sorted(locations)}


# ─── language detection ───────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    """Detect the primary language of a text. Returns English name or 'Unknown'."""
    try:
        from langdetect import detect, LangDetectException
        code = detect(text[:500])
        return _LANG_MAP.get(code, code.title())
    except Exception:
        return "Unknown"


# ─── dates ────────────────────────────────────────────────────────────────────

def extract_dates(texts: list[str]) -> list[str]:
    """dateparser — find date references, return ISO date strings (YYYY-MM-DD)."""
    try:
        import dateparser
    except ImportError:
        return []
    found: set[str] = set()
    for text in texts:
        for match in _DATE_HINT_RE.finditer(text):
            phrase = text[max(0, match.start() - 10): match.end() + 20]
            parsed = dateparser.parse(
                phrase,
                settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
            )
            if parsed:
                found.add(parsed.strftime("%Y-%m-%d"))
    return sorted(found)


def extract_followups(messages: list[dict], contact_name: str) -> list[dict]:
    """Scan messages for follow-up commitments.

    Returns list of {task, due_date_iso, source_text}.
    Each item becomes one row in the Follow-ups Notion database.
    """
    try:
        import dateparser
    except ImportError:
        return []

    results = []
    for msg in messages:
        text = msg.get("text", "")
        if not text:
            continue
        for pattern in _FOLLOWUP_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                snippet = match.group(0)
                parsed = dateparser.parse(
                    snippet,
                    settings={
                        "PREFER_DATES_FROM": "future",
                        "RETURN_AS_TIMEZONE_AWARE": True,
                        "RELATIVE_BASE": datetime.now(timezone.utc),
                    },
                )
                if parsed:
                    results.append({
                        "task": f"Follow up with {contact_name}: {snippet[:60]}",
                        "due_date_iso": parsed.date().isoformat(),
                        "source_text": text[:100],
                    })
                    break  # one follow-up per message max
    return results


# ─── contact info ─────────────────────────────────────────────────────────────

def extract_contact_info(texts: list[str]) -> dict[str, list[str]]:
    """Regex — extract emails, phones, URLs, domains."""
    emails: set[str] = set()
    phones: set[str] = set()
    urls: set[str] = set()
    domains: set[str] = set()
    for text in texts:
        if not text:
            continue
        emails.update(_EMAIL_RE.findall(text))
        urls.update(_URL_RE.findall(text))
        domains.update(_DOMAIN_RE.findall(text))
        for p in _PHONE_RE.findall(text):
            cleaned = re.sub(r"[\s\-().]", "", p)
            if len(cleaned) >= 7:
                phones.add(cleaned)
    filtered_domains = {
        d for d in domains
        if not any(d in u for u in urls) and not any(d in e for e in emails)
    }
    return {
        "emails": sorted(emails),
        "phones": sorted(phones),
        "urls": sorted(urls),
        "domains": sorted(filtered_domains),
    }


# ─── deduplication ────────────────────────────────────────────────────────────

def fuzzy_find_contact(
    name: str,
    candidates: list[str],
    threshold: float = 85.0,
) -> tuple[str | None, float]:
    """rapidfuzz token_sort_ratio — handles word-order variants.

    Returns (best_match_name, score) or (None, 0.0) if below threshold.
    """
    if not candidates:
        return None, 0.0
    try:
        from rapidfuzz import process, fuzz
        result = process.extractOne(
            name, candidates,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold,
        )
        if result:
            return result[0], result[1]
        return None, 0.0
    except ImportError:
        return None, 0.0


# ─── master enrich ────────────────────────────────────────────────────────────

def enrich(messages: list[dict]) -> dict:
    """Run all NLP tools over a list of message dicts.

    Input:  list of {date, sender, text}
    Output: {sentiment, entities, dates, contact_info, language}
    """
    texts = [m["text"] for m in messages if m.get("text")]
    full_text = " ".join(texts)
    return {
        "sentiment":    sentiment_score(texts),
        "entities":     extract_entities(texts),
        "dates":        extract_dates(texts),
        "contact_info": extract_contact_info(texts),
        "language":     detect_language(full_text) if full_text else "Unknown",
    }
