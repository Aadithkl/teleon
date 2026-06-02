"""Seed the Follow-ups table with one check-in task per DM contact.

Runs once. Creates a 'Check in with X' follow-up due 7 days after
their Last Contact date. Skips contacts that already have open follow-ups.
"""
import sys
from datetime import date, timedelta

import notion_client
import config

c = notion_client.Client(auth=config.NOTION_TOKEN)


def get_open_followup_contact_ids() -> set[str]:
    """Return set of contact page IDs that already have an open follow-up."""
    ids: set[str] = set()
    cursor = None
    while True:
        kwargs: dict = {
            "filter": {"property": "Done", "checkbox": {"equals": False}},
            "page_size": 100,
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = c.data_sources.query(config.NOTION_FOLLOWUPS_DS_ID, **kwargs)
        for page in resp.get("results", []):
            rel = page["properties"].get("Contact", {}).get("relation", [])
            for r in rel:
                ids.add(r["id"])
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return ids


def get_latest_interaction_summary(contact_page_id: str) -> str:
    """Pull the most recent interaction summary for this contact."""
    resp = c.data_sources.query(
        config.NOTION_INTERACTIONS_DS_ID,
        filter={"property": "Contact", "relation": {"contains": contact_page_id}},
        sorts=[{"property": "Date", "direction": "descending"}],
        page_size=1,
    )
    results = resp.get("results", [])
    if not results:
        return ""
    p = results[0]["properties"]
    date_str = (p.get("Date", {}).get("date") or {}).get("start", "")
    summary_items = p.get("Summary", {}).get("rich_text", [])
    summary = summary_items[0]["plain_text"] if summary_items else ""
    sentiment = (p.get("Sentiment", {}).get("select") or {}).get("name", "")
    topics_items = p.get("Topics", {}).get("multi_select", [])
    topics = ", ".join(t["name"] for t in topics_items) if topics_items else ""

    parts = []
    if date_str:
        parts.append(f"Last chat: {date_str}")
    if sentiment:
        parts.append(f"Sentiment: {sentiment}")
    if topics:
        parts.append(f"Topics: {topics}")
    if summary:
        parts.append(f"Summary: {summary}")
    return " | ".join(parts)


def get_dm_contacts() -> list[dict]:
    contacts = []
    cursor = None
    while True:
        kwargs: dict = {
            "filter": {"property": "Source", "select": {"equals": "Telegram DM"}},
            "page_size": 100,
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = c.data_sources.query(config.NOTION_CONTACTS_DS_ID, **kwargs)
        for page in resp.get("results", []):
            p = page["properties"]
            name_items = p.get("Name", {}).get("title", [])
            name = name_items[0]["plain_text"] if name_items else ""
            lc = (p.get("Last Contact", {}).get("date") or {}).get("start", "")
            # pull Last Activity as context for the follow-up
            la_items = p.get("Last Activity", {}).get("rich_text", [])
            last_activity = la_items[0]["plain_text"] if la_items else ""
            contacts.append({
                "id": page["id"],
                "name": name,
                "last_contact": lc,
                "last_activity": last_activity,
            })
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return contacts


def seed():
    existing = get_open_followup_contact_ids()
    contacts = get_dm_contacts()

    print(f"Found {len(contacts)} DM contacts, {len(existing)} already have open follow-ups\n")

    created = skipped = 0
    for contact in contacts:
        if contact["id"] in existing:
            print(f"  skip  {contact['name']} (already has open follow-up)")
            skipped += 1
            continue

        # due date = last_contact + 7 days, or today + 7 if unknown
        if contact["last_contact"]:
            base = date.fromisoformat(contact["last_contact"][:10])
        else:
            base = date.today()
        due = (base + timedelta(days=7)).isoformat()

        # prefer Last Activity (AI), fall back to latest Interaction summary (NLP), then date
        context = (
            contact.get("last_activity")
            or get_latest_interaction_summary(contact["id"])
            or f"First contact on {contact['last_contact'] or 'unknown'}"
        )

        try:
            c.pages.create(
                parent={"database_id": config.NOTION_FOLLOWUPS_DB_ID},
                properties={
                    "Name":        {"title": [{"text": {"content": f"Check in with {contact['name']}"}}]},
                    "Contact":     {"relation": [{"id": contact["id"]}]},
                    "Due Date":    {"date": {"start": due}},
                    "Done":        {"checkbox": False},
                    "Source Chat": {"rich_text": [{"text": {"content": "seeded"}}]},
                    "Context":     {"rich_text": [{"text": {"content": context}}]},
                },
            )
            print(f"  ✓  {contact['name']:<30} due {due}  |  {context[:60]}")
            created += 1
        except Exception as e:
            print(f"  ✗  {contact['name']}: {e}", file=sys.stderr)

    print(f"\nDone. {created} follow-ups created, {skipped} skipped.")


if __name__ == "__main__":
    seed()
