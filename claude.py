import json
import sys

import anthropic

import config

_client: anthropic.Anthropic | None = None

MODEL = "claude-sonnet-4-6"
MAX_TRANSCRIPT_CHARS = 4000

_FALLBACK = {"summary": "Could not parse", "key_points": [], "action_items": []}


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _build_transcript(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        date_str = msg["date"][:16].replace("T", " ")
        lines.append(f"[{date_str}] {msg['sender']}: {msg['text']}")
    transcript = "\n".join(lines)

    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = "[earlier messages omitted]\n" + transcript[-MAX_TRANSCRIPT_CHARS:]

    return transcript


def extract_crm_data(chat_name: str, messages: list[dict]) -> dict:
    transcript = _build_transcript(messages)

    prompt = f"""You are a CRM assistant. Analyze this Telegram conversation and extract structured data for a personal CRM.

Chat: {chat_name}

--- CONVERSATION ---
{transcript}
--- END ---

Instructions:
- summary: 2-3 sentences capturing what was discussed and current status
- key_points: up to 5 notable facts, decisions, or context items
- action_items: concrete next steps for ME (the account owner), if any

Return ONLY a valid JSON object, no markdown, no explanation:
{{
  "summary": "...",
  "key_points": ["...", "..."],
  "action_items": ["...", "..."]
}}"""

    client = _get_client()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as e:
        print(f"Claude API error: {e}", file=sys.stderr)
        return _FALLBACK.copy()

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(raw)
        return {
            "summary": str(data.get("summary", "")),
            "key_points": list(data.get("key_points", [])),
            "action_items": list(data.get("action_items", [])),
        }
    except json.JSONDecodeError:
        print(f"Claude JSON parse failure. Raw response:\n{raw}", file=sys.stderr)
        return _FALLBACK.copy()
