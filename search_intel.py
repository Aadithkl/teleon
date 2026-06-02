"""Search intelligence — pure logic, no storage assumptions.

All functions operate on passed-in data. Callers own persistence.

Features:
- Query expansion (keyword → related terms)
- Relevance scoring (patterns + recency)
- Dedup (by text prefix)
- Entity extraction (@usernames, links, amounts)
- Chat affinity learning (topic → chat ranking)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# ─── built-in expansions ──────────────────────────────────────────────────────

DEFAULT_EXPANSIONS: dict[str, list[str]] = {
    "bd": ["business development", "biz dev", "sales"],
    "pm": ["product manager", "project manager"],
    "deals": ["pricing", "contract", "proposal", "negotiation", "budget"],
    "jobs": ["hiring", "position", "role", "career", "opening"],
    "funding": ["raise", "invest", "round", "capital", "series"],
    "crypto": ["web3", "blockchain", "defi", "token"],
    "partnership": ["collaboration", "alliance", "integration"],
    "referral": ["ref", "invite", "via", "affiliate"],
}

# Patterns for relevance scoring: (regex, weight_multiplier)
SCORE_PATTERNS: list[tuple[str, float]] = [
    (r"@\w+", 1.3),
    (r"https?://\S+", 1.2),
    (r"ref=|via=|referral", 1.5),
    (r"\$\d+[kKMB]?", 1.4),
    (r"(?:apply|hiring|job|role)\b", 1.3),
    (r"(?:demo|pricing|contract|proposal)\b", 1.3),
    (r"(?:urgent|asap|deadline|eod)\b", 1.2),
]


# ─── query expansion ──────────────────────────────────────────────────────────

def expand_query(query: str, expansions: dict[str, list[str]] | None = None) -> list[str]:
    """Return [original, *related_terms] for broader search coverage.

    Args:
        query: The user's search term.
        expansions: Custom expansion map (falls back to DEFAULT_EXPANSIONS).

    Returns: [original, ...extra_terms] — all terms that should be searched.
    """
    lookup = expansions if expansions is not None else DEFAULT_EXPANSIONS
    q_lower = query.lower()
    extra: list[str] = []

    for term, alternatives in lookup.items():
        if term in q_lower:
            extra.extend(a for a in alternatives if a not in extra)
        for alt in alternatives:
            if alt in q_lower and term not in extra:
                extra.append(term)

    stripped = re.sub(r"(?:ing|ed|s|er|or)\b", "", q_lower).strip()
    if stripped and stripped != q_lower:
        extra.append(stripped)

    return [query] + extra


# ─── relevance scoring ───────────────────────────────────────────────────────

def score_message(msg: dict) -> float:
    """Score a single message for relevance (higher = more relevant)."""
    text = msg.get("text", "")
    if not text:
        return 0.0

    score = 1.0
    for pattern, weight in SCORE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score *= weight

    if msg.get("date"):
        try:
            d = datetime.fromisoformat(msg["date"])
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            days_old = (datetime.now(timezone.utc) - d).days
            if days_old <= 7:
                score *= 1.2
            elif days_old <= 30:
                score *= 1.0
            else:
                score *= max(0.5, 1.0 - (days_old - 30) * 0.003)
        except (ValueError, TypeError):
            pass

    return round(min(score, 3.0), 2)


# ─── dedup ────────────────────────────────────────────────────────────────────

def deduplicate(results: list[dict]) -> list[dict]:
    """Remove near-duplicate messages (same first 80 chars)."""
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        key = r.get("text", "")[:80].lower().strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


# ─── entity extraction from hits ──────────────────────────────────────────────

def extract_entities(results: list[dict]) -> dict:
    """Extract @usernames, links, dollar amounts from result messages."""
    usernames: set[str] = set()
    links: set[str] = set()
    amounts: set[str] = set()

    for r in results:
        text = r.get("text", "")
        for m in re.findall(r"@\w+", text):
            usernames.add(m.lower())
        for m in re.findall(r"https?://\S+", text):
            links.add(m.rstrip(".,;:!?"))
        for m in re.findall(r"\$\d+(?:[kKMBkmb]|,\d{3})*(?:\s*(?:/yr|/year|/mo|/month))?", text):
            amounts.add(m)
        if r.get("username"):
            usernames.add(r["username"].lower())

    return {
        "usernames": sorted(usernames),
        "links": sorted(links)[:20],
        "amounts": sorted(amounts),
    }


# ─── context-aware entity extraction ──────────────────────────────────────────

def extract_entities_from_blocks(blocks: list[dict]) -> dict:
    """Extract entities from context blocks (hit + before + after messages)."""
    all_msgs: list[dict] = []
    for block in blocks:
        if block.get("hit"):
            all_msgs.append(block["hit"])
        for m in block.get("before", []):
            all_msgs.append(m)
        for m in block.get("after", []):
            all_msgs.append(m)
    return extract_entities(all_msgs)


# ─── chat affinity learning ────────────────────────────────────────────────────

def learn(
    store: dict,
    chat_name: str,
    query: str,
    match_count: int,
) -> dict:
    """Update topic→chat affinity and return the updated store.

    Args:
        store: The intel store dict (created by caller, persisted by caller).
        chat_name: Chat display name.
        query: The search query that produced matches.
        match_count: How many matches were found.

    Returns: The updated store (caller should persist if desired).
    """
    if match_count < 1:
        return store
    topic = query.lower().strip()
    affinity = store.setdefault("chat_affinity", {})
    chat_entry = affinity.setdefault(chat_name, {})
    chat_entry[topic] = chat_entry.get(topic, 0) + match_count
    return store


def rank_chats(
    store: dict,
    chat_names: list[str],
    query: str,
) -> list[str]:
    """Sort chats by learned affinity for this query's topic.

    Args:
        store: The intel store dict (may contain chat_affinity data).
        chat_names: List of chat names to rank.
        query: The search query.

    Returns: chat_names sorted by affinity (most relevant first).
    """
    affinity = store.get("chat_affinity", {})
    topic = query.lower().strip()

    def _score(name: str) -> int:
        entry = affinity.get(name, {})
        return entry.get(topic, 0) + sum(
            v for k, v in entry.items() if topic in k or k in topic
        )

    return sorted(chat_names, key=_score, reverse=True)


# ─── scoring pipeline for flat results ────────────────────────────────────────

def process_results(results: list[dict], query: str) -> dict:
    """Full pipeline: score, dedup, extract entities.

    Args:
        results: Raw messages (flat list).
        query: Original search query (for context).

    Returns:
        results:  deduped + scored, sorted by relevance descending.
        entities: {usernames, links, amounts}
        stats:    {total_raw, total_after_dedup}
    """
    scored = [(score_message(r), r) for r in results]
    scored.sort(key=lambda x: (-x[0], x[1].get("date", "")))

    deduped = deduplicate([r for _, r in scored])
    entities = extract_entities(deduped)

    return {
        "results": deduped,
        "entities": entities,
        "stats": {
            "total_raw": len(results),
            "total_deduped": len(deduped),
        },
    }
