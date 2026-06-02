"""Contact enrichment engine — search online/custom sources, synthesise with AI,
save results as extra columns in the Notion Contacts database.

Sources supported:
  web         — DuckDuckGo general web search (free, no key)
  linkedin    — DuckDuckGo scoped to linkedin.com
  github      — DuckDuckGo scoped to github.com
  twitter     — DuckDuckGo scoped to twitter.com / x.com
  crunchbase  — DuckDuckGo scoped to crunchbase.com
  custom      — any query string the user passes via --query

Usage:
  teleon enrich "Alice"
  teleon enrich "Alice" --source linkedin --source github
  teleon enrich "Alice" --query "Alice Chen CEO Acme Corp funding"
  teleon enrich --all --source linkedin          # enrich all tracked contacts
"""
from __future__ import annotations

import json
import sys
from datetime import date
from typing import Any

import notion_client

import config

MAX_RESULTS_PER_SOURCE = 5
MAX_SNIPPET_CHARS = 300

# Notion property type map for enrichment fields
_PROP_TYPES: dict[str, dict] = {
    "LinkedIn":     {"url": {}},
    "GitHub":       {"url": {}},
    "Twitter":      {"url": {}},
    "Website":      {"url": {}},
    "Company":      {"rich_text": {}},
    "Title":        {"rich_text": {}},
    "Location":     {"rich_text": {}},
    "Bio":          {"rich_text": {}},
    "Enriched At":  {"date": {}},
}

# Extra fields the AI can return beyond the fixed set above
_EXTRA_PROP_TEMPLATE = {"rich_text": {}}


# ─── search ───────────────────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = MAX_RESULTS_PER_SOURCE) -> list[dict]:
    """Run a DuckDuckGo text search. Returns list of {title, href, body}."""
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url":   r.get("href", ""),
                    "body":  r.get("body", "")[:MAX_SNIPPET_CHARS],
                })
        return results
    except Exception as e:
        print(f"Search error for '{query}': {e}", file=sys.stderr)
        return []


SOURCES: dict[str, str] = {
    "web":        "{name}",
    "linkedin":   "{name} site:linkedin.com",
    "github":     "{name} site:github.com",
    "twitter":    "{name} site:twitter.com OR site:x.com",
    "crunchbase": "{name} site:crunchbase.com",
}


def search(name: str, sources: list[str], custom_query: str | None = None) -> dict[str, list[dict]]:
    """Run searches across requested sources. Returns {source: [results]}."""
    all_results: dict[str, list[dict]] = {}

    for source in sources:
        if source == "custom":
            query = custom_query or name
        else:
            template = SOURCES.get(source, "{name}")
            query = template.format(name=name)
        print(f"  Searching [{source}]: {query}")
        results = _ddg_search(query)
        if results:
            all_results[source] = results

    return all_results


# ─── AI synthesis ─────────────────────────────────────────────────────────────

def _build_synthesis_prompt(name: str, search_results: dict[str, list[dict]]) -> str:
    sections = []
    for source, results in search_results.items():
        sections.append(f"[{source.upper()} RESULTS]")
        for r in results:
            sections.append(f"  Title: {r['title']}")
            sections.append(f"  URL:   {r['url']}")
            sections.append(f"  Body:  {r['body']}")
            sections.append("")

    results_text = "\n".join(sections)

    return f"""You are a business intelligence analyst enriching a CRM contact record.

Contact name: {name}

Here are search results from multiple sources:
{results_text}

Extract all factual, verifiable information about this person for a CRM.
Focus on: professional identity, company/role, social profiles, location, background.

Return ONLY valid JSON with any of these fields that you can confidently extract.
Do NOT include fields you are guessing or cannot confirm from the results:
{{
  "LinkedIn":    "https://linkedin.com/in/...",
  "GitHub":      "https://github.com/...",
  "Twitter":     "https://twitter.com/...",
  "Website":     "https://...",
  "Company":     "Company name",
  "Title":       "Job title / role",
  "Location":    "City, Country",
  "Bio":         "1-2 sentence professional summary"
}}

You may add extra fields beyond this list if you find other useful structured data.
Omit any field you cannot confirm. Return only JSON, no explanation.
"""


def synthesise(name: str, search_results: dict[str, list[dict]]) -> dict[str, str]:
    """Use the configured AI provider to extract structured fields from search results."""
    provider = config.AI_PROVIDER.lower()

    if provider == "none" or not search_results:
        # Fallback: extract URLs directly from results without AI
        extracted: dict[str, str] = {}
        for source, results in search_results.items():
            for r in results:
                url = r["url"]
                if "linkedin.com/in/" in url and "LinkedIn" not in extracted:
                    extracted["LinkedIn"] = url.split("?")[0]
                elif "github.com/" in url and "/" in url.split("github.com/")[-1] and "GitHub" not in extracted:
                    extracted["GitHub"] = url.split("?")[0]
                elif ("twitter.com/" in url or "x.com/" in url) and "Twitter" not in extracted:
                    extracted["Twitter"] = url.split("?")[0]
        return extracted

    prompt = _build_synthesis_prompt(name, search_results)

    try:
        if provider == "claude":
            import anthropic
            key = config.AI_API_KEY or config.ANTHROPIC_API_KEY
            model = config.AI_MODEL or "claude-sonnet-4-6"
            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(
                model=model, max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
        elif provider == "gemini":
            import google.generativeai as genai
            key = config.AI_API_KEY or config.GEMINI_API_KEY
            genai.configure(api_key=key)
            model = config.AI_MODEL or "gemini-2.0-flash"
            raw = genai.GenerativeModel(model).generate_content(prompt).text.strip()
        elif provider == "openai":
            from openai import OpenAI
            key = config.AI_API_KEY or config.OPENAI_API_KEY
            model = config.AI_MODEL or "gpt-4o-mini"
            resp = OpenAI(api_key=key).chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
            )
            raw = resp.choices[0].message.content.strip()
        else:
            return {}

        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data = json.loads(raw)
        return {k: str(v) for k, v in data.items() if v}

    except Exception as e:
        print(f"AI synthesis error: {e}", file=sys.stderr)
        return {}


# ─── Notion write ─────────────────────────────────────────────────────────────

def _ensure_properties(client: notion_client.Client, db_id: str, fields: dict[str, str]) -> None:
    """Add any missing properties to the Notion Contacts database."""
    ds = client.data_sources.retrieve(config.NOTION_CONTACTS_DS_ID)
    existing = set(ds.get("properties", {}).keys())
    to_add = {}
    for field_name in fields:
        if field_name not in existing:
            prop_def = _PROP_TYPES.get(field_name, _EXTRA_PROP_TEMPLATE)
            to_add[field_name] = prop_def
    if to_add:
        client.data_sources.update(config.NOTION_CONTACTS_DS_ID, properties=to_add)
        print(f"  Added {len(to_add)} new column(s) to Contacts: {', '.join(to_add)}")


def _build_notion_props(fields: dict[str, str]) -> dict[str, Any]:
    """Convert extracted field dict to Notion property format."""
    props: dict[str, Any] = {
        "Enriched At": {"date": {"start": date.today().isoformat()}},
    }
    for name, value in fields.items():
        if not value:
            continue
        prop_type = _PROP_TYPES.get(name, _EXTRA_PROP_TEMPLATE)
        if "url" in prop_type:
            props[name] = {"url": value}
        elif "date" in prop_type:
            props[name] = {"date": {"start": value}}
        else:
            props[name] = {"rich_text": [{"text": {"content": value[:2000]}}]}
    return props


def save_to_notion(contact_page_id: str, fields: dict[str, str]) -> None:
    """Ensure columns exist, then write enrichment fields to the contact page."""
    if not config.NOTION_ENABLED:
        print("  Notion not configured — printing enrichment data only.")
        for k, v in fields.items():
            print(f"    {k}: {v}")
        return

    client = notion_client.Client(auth=config.NOTION_TOKEN)
    _ensure_properties(client, config.NOTION_CONTACTS_DB_ID, fields)
    props = _build_notion_props(fields)
    client.pages.update(page_id=contact_page_id, properties=props)


# ─── main entry ───────────────────────────────────────────────────────────────

def enrich_contact(
    contact_name: str,
    contact_page_id: str,
    sources: list[str],
    custom_query: str | None = None,
) -> dict[str, str]:
    """Full enrichment pipeline for one contact.

    1. Search each source
    2. Synthesise with AI (or regex fallback)
    3. Save to Notion
    Returns the extracted fields dict.
    """
    print(f"\nEnriching: {contact_name}")
    results = search(contact_name, sources, custom_query)

    if not results:
        print("  No results found.")
        return {}

    total = sum(len(v) for v in results.values())
    print(f"  Found {total} result(s) across {len(results)} source(s). Synthesising...")

    fields = synthesise(contact_name, results)

    if not fields:
        print("  Could not extract structured data from results.")
        return {}

    print(f"  Extracted {len(fields)} field(s): {', '.join(fields.keys())}")
    save_to_notion(contact_page_id, fields)
    return fields
