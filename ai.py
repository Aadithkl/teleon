"""Provider-agnostic AI extraction layer.

Set AI_PROVIDER in .env to select which AI backend to use:
  claude   — Anthropic API  (needs ANTHROPIC_API_KEY)
  gemini   — Google Gemini  (needs GEMINI_API_KEY)
  openai   — OpenAI         (needs OPENAI_API_KEY)
  none     — skip AI extraction, NLP enrichment still runs

Set AI_MODEL to override the default model for any provider.
"""
from __future__ import annotations

import json
import sys

import config

MAX_TRANSCRIPT_CHARS = 4000

_FALLBACK = {
    "summary": "",
    "key_points": [],
    "action_items": [],
    "last_activity": "",
    "relationship_type": "personal",
    "deal_signal": False,
    "urgency": "low",
    "topic_tags": [],
}

_DEFAULTS = {
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
}

RELATIONSHIP_TYPES = ["client", "partner", "vendor", "personal", "prospect"]
URGENCY_LEVELS = ["high", "medium", "low"]

KNOWN_TAGS = [
    "pricing", "budget", "contract", "proposal", "negotiation",
    "demo", "trial", "onboarding", "pitch",
    "support", "bug", "issue", "complaint", "escalation",
    "intro", "networking", "referral", "cold-outreach",
    "followup", "check-in", "reminder",
    "technical", "integration", "api", "infrastructure",
    "partnership", "collaboration", "co-founder",
    "hiring", "recruitment", "job",
    "deadline", "urgent",
    "personal", "social",
    "investment", "funding", "due-diligence",
    "legal", "compliance",
]


# ─── prompt ───────────────────────────────────────────────────────────────────

def _build_prompt(chat_name: str, messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        date_str = msg["date"][:16].replace("T", " ")
        lines.append(f"[{date_str}] {msg['sender']}: {msg['text']}")
    transcript = "\n".join(lines)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = "[earlier messages omitted]\n" + transcript[-MAX_TRANSCRIPT_CHARS:]

    return f"""You are a senior business analyst and CRM intelligence specialist reviewing Telegram conversations on behalf of the account owner. Your job is to extract structured business intelligence — not just summarise, but read between the lines for relationship signals, commercial intent, and priorities.

Analyse this conversation with the mindset of someone who:
- Tracks deal pipelines and commercial opportunities
- Identifies urgency and follow-up priorities across a portfolio of contacts
- Classifies relationships (client, partner, vendor, prospect, personal)
- Spots topic patterns useful for segmentation and outreach strategy

Chat: {chat_name}

--- CONVERSATION ---
{transcript}
--- END ---

Extract the following. Be specific and opinionated — do not hedge with "possibly" or "might be":

last_activity
  Max 30 words. Summarise the status and conclusion of the last exchange.
  Answer two things in one sentence: what happened + where it stands now.
  Be specific to the actual content — never generic filler.
  Examples:
    "Sent pricing proposal last Tuesday, client reviewing and will reply by Friday."
    "Partnership terms agreed in principle, contract draft being prepared by their legal team."
    "Demo completed, prospect asked for ROI numbers before committing."
    "Follow-up on unpaid invoice — client acknowledged delay, promised payment this week."
    "Support issue with login resolved, client confirmed fix is working."
    "Intro call done, mutual interest confirmed, no next step set yet."
    "Lead went quiet after initial interest — no reply in 10 days."
    "Negotiation stalled on pricing, waiting for their budget approval."

summary
  2-3 sentences: what was discussed, current relationship status, where things stand.

key_points
  Up to 5 bullet facts — notable decisions, commitments, context, or signals.
  Focus on business-relevant information, not small talk.

action_items
  Concrete next steps for ME (the account owner). Only include if a clear action exists.

relationship_type
  Pick exactly one: client | partner | vendor | personal | prospect
  - client: paying customer or active user
  - partner: strategic ally, co-builder, referral source
  - vendor: someone selling to me or providing a service
  - prospect: potential client or deal not yet closed
  - personal: friend, acquaintance, no clear commercial context

deal_signal
  true if the conversation contains any commercial signal: pricing discussed,
  budget mentioned, purchase intent, contract/proposal talk, demo requested,
  funding/investment angle. false otherwise.

urgency
  high   — time-sensitive, deadline mentioned, waiting on me, escalation
  medium — active thread, soft timeline, needs response within days
  low    — casual, informational, no clear follow-up pressure

topic_tags
  Array of relevant tags from business context. Use from this list where applicable,
  add custom tags if needed:
  {json.dumps(KNOWN_TAGS, indent=2)}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "last_activity": "Sent pricing proposal, awaiting decision",
  "summary": "...",
  "key_points": ["...", "..."],
  "action_items": ["...", "..."],
  "relationship_type": "client|partner|vendor|personal|prospect",
  "deal_signal": true,
  "urgency": "high|medium|low",
  "topic_tags": ["tag1", "tag2"]
}}"""


def _parse(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(raw)
        return {
            "last_activity": str(data.get("last_activity", ""))[:200],
            "summary": str(data.get("summary", "")),
            "key_points": list(data.get("key_points", [])),
            "action_items": list(data.get("action_items", [])),
            "relationship_type": data.get("relationship_type", "personal")
                if data.get("relationship_type") in RELATIONSHIP_TYPES else "personal",
            "deal_signal": bool(data.get("deal_signal", False)),
            "urgency": data.get("urgency", "low")
                if data.get("urgency") in URGENCY_LEVELS else "low",
            "topic_tags": [str(t) for t in data.get("topic_tags", [])],
        }
    except json.JSONDecodeError:
        print(f"AI JSON parse error. Raw:\n{raw}", file=sys.stderr)
        return _FALLBACK.copy()


# ─── provider backends ────────────────────────────────────────────────────────

def _call_claude(prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic not installed. Run: uv add anthropic")
    key = config.AI_API_KEY or config.ANTHROPIC_API_KEY
    if not key:
        raise RuntimeError("Set ANTHROPIC_API_KEY or AI_API_KEY in .env")
    model = config.AI_MODEL or _DEFAULTS["claude"]
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _call_gemini(prompt: str) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError("google-generativeai not installed. Run: uv add google-generativeai")
    key = config.AI_API_KEY or config.GEMINI_API_KEY
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY or AI_API_KEY in .env")
    model = config.AI_MODEL or _DEFAULTS["gemini"]
    genai.configure(api_key=key)
    resp = genai.GenerativeModel(model).generate_content(prompt)
    return resp.text


def _call_openai(prompt: str) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai not installed. Run: uv add openai")
    key = config.AI_API_KEY or config.OPENAI_API_KEY
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY or AI_API_KEY in .env")
    model = config.AI_MODEL or _DEFAULTS["openai"]
    client = OpenAI(api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )
    return resp.choices[0].message.content


def _call_cli(prompt: str) -> str:
    """Use the local Claude Code CLI — no API key needed."""
    import subprocess
    import sys
    # On Windows npm CLIs are .cmd wrappers
    exe = "claude.cmd" if sys.platform == "win32" else "claude"
    result = subprocess.run(
        [exe, "--print"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        raise RuntimeError(f"Claude CLI exited {result.returncode}: {err}")
    return result.stdout.strip()


_PROVIDERS = {
    "claude": _call_claude,
    "gemini": _call_gemini,
    "openai": _call_openai,
    "cli":    _call_cli,
}


# ─── public API ───────────────────────────────────────────────────────────────

def extract_crm_data(chat_name: str, messages: list[dict]) -> dict:
    """Extract business intelligence from a conversation.

    Returns: summary, key_points, action_items, relationship_type,
             deal_signal, urgency, topic_tags.
    Falls back to _FALLBACK if provider is 'none' or call fails.
    NLP enrichment runs regardless of provider.
    """
    provider = config.AI_PROVIDER.lower()

    if provider == "none":
        return _FALLBACK.copy()

    call = _PROVIDERS.get(provider)
    if not call:
        print(f"Unknown AI_PROVIDER '{provider}'. Options: claude, gemini, openai, none", file=sys.stderr)
        return _FALLBACK.copy()

    prompt = _build_prompt(chat_name, messages)
    try:
        raw = call(prompt)
        return _parse(raw)
    except Exception as e:
        print(f"AI extraction error ({provider}): {e}", file=sys.stderr)
        return _FALLBACK.copy()


def analyze_search_results(query: str, results: list[dict]) -> str:
    """Analyze search results across chats and return a structured summary."""
    if not results:
        return "No results to analyze."

    provider = config.AI_PROVIDER.lower()
    if provider == "none":
        return "AI analysis skipped (AI_PROVIDER=none)"

    call = _PROVIDERS.get(provider)
    if not call:
        return f"No AI provider available for '{provider}'"

    chats = {}
    for r in results:
        chats.setdefault(r["chat_name"], []).append(r)

    parts = [f"Search query: {query}\n"]
    parts.append(f"Found {len(results)} matching messages across {len(chats)} chat(s).\n")

    for chat_name, msgs in sorted(chats.items()):
        parts.append(f"\n--- {chat_name} ({len(msgs)} matches) ---")
        for m in sorted(msgs, key=lambda x: x["date"]):
            date_short = m["date"][:10]
            parts.append(f"[{date_short}] {m['sender']}: {m['text'][:300]}")

    transcript = "\n".join(parts)
    prompt = f"""You are a research analyst reviewing search results from Teleon (Telegram → Notion CRM). The user searched for: "{query}".

Below are the matching messages grouped by chat.

{transcript}

Provide a concise analysis covering:
1. **Key findings** — what the search reveals across all chats
2. **Most relevant results** — which messages/chats are most important
3. **Patterns or signals** — any recurring topics, mentions, or commercial signals

Be specific and reference actual messages. Output plain text, no JSON."""

    try:
        return call(prompt)
    except Exception as e:
        return f"AI analysis error: {e}"


_PROFILE_FALLBACK = {
    "summary": "",
    "topics": [],
    "projects": [],
    "interests": [],
    "expertise": [],
    "style": "",
    "recent_focus": "",
    "openers": [],
}


def _parse_profile(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(raw)
        return {
            "summary":      str(data.get("summary", "")),
            "topics":       [str(t) for t in data.get("topics", [])],
            "projects":     [str(t) for t in data.get("projects", [])],
            "interests":    [str(t) for t in data.get("interests", [])],
            "expertise":    [str(t) for t in data.get("expertise", [])],
            "style":        str(data.get("style", "")),
            "recent_focus": str(data.get("recent_focus", "")),
            "openers":      [str(t) for t in data.get("openers", [])],
        }
    except json.JSONDecodeError:
        print(f"Profile parse error. Raw:\n{raw}", file=sys.stderr)
        return _PROFILE_FALLBACK.copy()


def build_person_profile(person: str, messages: list[dict]) -> dict:
    """Analyse a person's collected messages and return profile + cold DM openers.

    Returns: summary, topics, projects, interests, expertise, style,
             recent_focus, openers.
    Falls back to _PROFILE_FALLBACK if provider is 'none' or call fails.
    """
    if not messages:
        return _PROFILE_FALLBACK.copy()

    provider = config.AI_PROVIDER.lower()
    if provider == "none":
        return _PROFILE_FALLBACK.copy()

    call = _PROVIDERS.get(provider)
    if not call:
        return _PROFILE_FALLBACK.copy()

    chats: dict[str, list[dict]] = {}
    for m in messages:
        chats.setdefault(m.get("chat_name", "?"), []).append(m)

    parts: list[str] = []
    for chat_name, msgs in sorted(chats.items()):
        parts.append(f"\n[{chat_name}]")
        for m in sorted(msgs, key=lambda x: x.get("date", "")):
            date_str = m["date"][:10] if m.get("date") else "?"
            parts.append(f"  {date_str}: {m['text'][:300]}")

    transcript = "\n".join(parts)
    if len(transcript) > 5000:
        transcript = "[earlier messages omitted]\n" + transcript[-5000:]

    prompt = f"""You are an intelligence analyst building a profile on a person to help craft relevant cold outreach.

Person: {person}
Messages collected: {len(messages)} across {len(chats)} chat(s)

{transcript}

Analyse their messages and extract the following. Be specific — reference actual content, not generic guesses.

topics        What subjects do they regularly discuss? (specific, not vague)
projects      Products, companies, initiatives, or ventures they mention or are working on
interests     Professional and personal interests evident in their messages
expertise     What do they appear knowledgeable or experienced in?
style         One sentence: how do they communicate? (tone, brevity, formality, technical level)
recent_focus  One sentence: what have they been talking about most in recent messages?
openers       3 cold DM conversation starters that are specific and non-generic.
              Each must reference something they actually said, shared, or are working on.
              Bad: "I saw you're into crypto"
              Good: "Your point about Polymarket's fee structure last week was sharp — I've been wrestling with the same liquidity tradeoff on a similar product"
summary       2-3 sentences: who is this person professionally and what are they focused on right now?

Return ONLY valid JSON:
{{
  "summary": "...",
  "topics": ["...", "..."],
  "projects": ["...", "..."],
  "interests": ["...", "..."],
  "expertise": ["...", "..."],
  "style": "...",
  "recent_focus": "...",
  "openers": ["...", "...", "..."]
}}"""

    try:
        raw = call(prompt)
        return _parse_profile(raw)
    except Exception as e:
        print(f"Profile build error ({provider}): {e}", file=sys.stderr)
        return _PROFILE_FALLBACK.copy()


def provider_info() -> str:
    provider = config.AI_PROVIDER.lower()
    if provider == "none":
        return "none (NLP-only mode)"
    if provider == "cli":
        return "cli (Claude Code)"
    model = config.AI_MODEL or _DEFAULTS.get(provider, "?")
    return f"{provider} / {model}"
