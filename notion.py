"""Notion CRM — synchronous write layer.

notion-client v3+ uses data_sources.query (not databases.query) for all reads.
pages.create / pages.update / databases.update / databases.retrieve still work as before.

Rules:
  - Never duplicate a contact (search by name before creating)
  - Never overwrite Notes or First Seen on contact update
  - Interactions are append-only (always create, never update)
  - Every Notion API error is caught, logged to logs/failed_writes.jsonl, skipped
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import notion_client

import config
import nlp as _nlp

_client: notion_client.Client | None = None
FAILED_LOG = Path("logs/failed_writes.jsonl")


# ─── client ───────────────────────────────────────────────────────────────────

def get_client() -> notion_client.Client:
    global _client
    if _client is None:
        _client = notion_client.Client(auth=config.NOTION_TOKEN)
    return _client


# ─── query helper (v3 compat) ─────────────────────────────────────────────────

def _query(ds_id: str, filter: dict | None = None, sorts: list | None = None,
           page_size: int = 100, start_cursor: str | None = None) -> dict:
    """Wrapper around data_sources.query (notion-client v3+)."""
    kwargs: dict = {"page_size": page_size}
    if filter:
        kwargs["filter"] = filter
    if sorts:
        kwargs["sorts"] = sorts
    if start_cursor:
        kwargs["start_cursor"] = start_cursor
    return get_client().data_sources.query(ds_id, **kwargs)


# ─── error-safe create ────────────────────────────────────────────────────────

def _safe_create(db_id: str, props: dict) -> str | None:
    try:
        page = get_client().pages.create(
            parent={"database_id": db_id},
            properties=props,
        )
        return page["id"]
    except Exception as e:
        print(f"[notion error] {e}", file=sys.stderr)
        FAILED_LOG.parent.mkdir(exist_ok=True)
        with FAILED_LOG.open("a") as f:
            f.write(json.dumps({"db": db_id, "error": str(e)}) + "\n")
        return None


# ─── contacts ─────────────────────────────────────────────────────────────────

def find_contact(name: str, fuzzy: bool = True) -> dict | None:
    """Exact title match first, rapidfuzz fallback if enabled."""
    resp = _query(
        config.NOTION_CONTACTS_DS_ID,
        filter={"property": "Name", "title": {"equals": name}},
    )
    results = resp.get("results", [])
    if results:
        return results[0]

    if not fuzzy:
        return None

    all_names = _fetch_all_contact_names()
    match, _score = _nlp.fuzzy_find_contact(name, all_names, threshold=85.0)
    if match:
        resp2 = _query(
            config.NOTION_CONTACTS_DS_ID,
            filter={"property": "Name", "title": {"equals": match}},
        )
        results2 = resp2.get("results", [])
        if results2:
            return results2[0]

    return None


def _fetch_all_contact_names() -> list[str]:
    names, cursor = [], None
    while True:
        resp = _query(config.NOTION_CONTACTS_DS_ID, page_size=100,
                      start_cursor=cursor)
        for page in resp.get("results", []):
            title = page["properties"].get("Name", {}).get("title", [])
            if title:
                names.append(title[0]["plain_text"])
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return names


def upsert_contact(
    name: str,
    username: str | None,
    source: str,
    last_message_date: str,
    company: str = "",
    role: str = "",
    language: str = "",
    last_activity: str = "",
) -> str:
    """Find or create a contact. Never overwrites First Seen or Notes."""
    today = date.today().isoformat()
    date_value = last_message_date[:10] if last_message_date else today
    handle = f"@{username}" if username else ""

    existing = find_contact(name, fuzzy=True)

    if existing:
        page_id = existing["id"]
        update_props: dict = {
            "Username":     {"rich_text": [{"text": {"content": handle}}]},
            "Last Contact": {"date": {"start": date_value}},
        }
        if company:
            update_props["Company"] = {"rich_text": [{"text": {"content": company}}]}
        if role:
            update_props["Role"] = {"rich_text": [{"text": {"content": role}}]}
        if last_activity:
            update_props["Last Activity"] = {"rich_text": [{"text": {"content": last_activity}}]}
        get_client().pages.update(page_id=page_id, properties=update_props)
        return page_id

    create_props: dict = {
        "Name":          {"title": [{"text": {"content": name}}]},
        "Username":      {"rich_text": [{"text": {"content": handle}}]},
        "Source":        {"select": {"name": source}},
        "Company":       {"rich_text": [{"text": {"content": company}}]},
        "Role":          {"rich_text": [{"text": {"content": role}}]},
        "Language":      {"select": {"name": language or "English"}},
        "First Seen":    {"date": {"start": today}},
        "Last Contact":  {"date": {"start": date_value}},
        "Status":        {"select": {"name": "Active"}},
        "Last Activity": {"rich_text": [{"text": {"content": last_activity}}]},
    }
    try:
        page = get_client().pages.create(
            parent={"database_id": config.NOTION_CONTACTS_DB_ID},
            properties=create_props,
        )
        return page["id"]
    except notion_client.errors.APIResponseError as e:
        if e.status == 404:
            raise ValueError("Check NOTION_CONTACTS_DB_ID in .env") from e
        raise


# ─── interactions ─────────────────────────────────────────────────────────────

def log_interaction(
    contact_page_id: str,
    contact_name: str,
    chat_name: str,
    summary: str,
    relationship: str,
    sentiment: str,
    topics: list[str],
    action_items: list[str],
    deal_signal: bool,
    interaction_date: str,
) -> str | None:
    """Append a new interaction. Never updates existing ones."""
    date_value = interaction_date[:10]
    safe_topics = [str(t)[:100] for t in topics[:10]]
    props: dict = {
        "Name":         {"title": [{"text": {"content": f"{contact_name} — {date_value}"}}]},
        "Contact":      {"relation": [{"id": contact_page_id}]},
        "Date":         {"date": {"start": date_value}},
        "Chat":         {"rich_text": [{"text": {"content": chat_name}}]},
        "Summary":      {"rich_text": [{"text": {"content": summary[:2000]}}]},
        "Relationship": {"select": {"name": relationship}},
        "Sentiment":    {"select": {"name": sentiment}},
        "Topics":       {"multi_select": [{"name": t} for t in safe_topics]},
        "Action Items": {"rich_text": [{"text": {"content": "\n".join(action_items)}}]},
        "Deal Signal":  {"checkbox": deal_signal},
    }
    return _safe_create(config.NOTION_INTERACTIONS_DB_ID, props)


# ─── follow-ups ───────────────────────────────────────────────────────────────

def log_followup(
    contact_page_id: str,
    task_name: str,
    due_date_iso: str,
    chat_name: str,
    context: str = "",
) -> str | None:
    props: dict = {
        "Name":        {"title": [{"text": {"content": task_name}}]},
        "Contact":     {"relation": [{"id": contact_page_id}]},
        "Due Date":    {"date": {"start": due_date_iso[:10]}},
        "Done":        {"checkbox": False},
        "Source Chat": {"rich_text": [{"text": {"content": chat_name}}]},
        "Context":     {"rich_text": [{"text": {"content": context[:2000]}}]},
    }
    return _safe_create(config.NOTION_FOLLOWUPS_DB_ID, props)


# ─── read ─────────────────────────────────────────────────────────────────────

def list_contacts(limit: int = 20) -> list[dict]:
    resp = _query(
        config.NOTION_CONTACTS_DS_ID,
        sorts=[{"property": "Last Contact", "direction": "descending"}],
        page_size=limit,
    )
    out = []
    for page in resp.get("results", []):
        p = page["properties"]
        out.append({
            "name":          _txt(p, "Name", title=True),
            "username":      _txt(p, "Username"),
            "source":        _sel(p, "Source"),
            "status":        _sel(p, "Status"),
            "last_contact":  _date(p, "Last Contact"),
            "last_activity": _txt(p, "Last Activity"),
        })
    return out


def get_interactions(contact_page_id: str, limit: int = 10) -> list[dict]:
    resp = _query(
        config.NOTION_INTERACTIONS_DS_ID,
        filter={"property": "Contact", "relation": {"contains": contact_page_id}},
        sorts=[{"property": "Date", "direction": "descending"}],
        page_size=limit,
    )
    out = []
    for page in resp.get("results", []):
        p = page["properties"]
        out.append({
            "date":      _date(p, "Date"),
            "chat":      _txt(p, "Chat"),
            "summary":   _txt(p, "Summary"),
            "sentiment": _sel(p, "Sentiment"),
        })
    return out


# ─── helpers ──────────────────────────────────────────────────────────────────

def _txt(props: dict, key: str, title: bool = False) -> str:
    items = props.get(key, {}).get("title" if title else "rich_text", [])
    return items[0]["plain_text"] if items else ""


def _sel(props: dict, key: str) -> str:
    return (props.get(key, {}).get("select") or {}).get("name", "")


def _date(props: dict, key: str) -> str:
    return (props.get(key, {}).get("date") or {}).get("start", "")
