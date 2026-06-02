"""Setup / migrate Notion CRM databases to full schema.

Safe to run multiple times — adds missing properties, never deletes existing ones.

    uv run python setup_notion.py
"""
import sys
import notion_client
import config


def get_existing_props(client, db_id: str) -> set[str]:
    db = client.databases.retrieve(database_id=db_id)
    return set(db["properties"].keys())


_DS_MAP = {
    "contacts":     lambda: config.NOTION_CONTACTS_DS_ID,
    "interactions": lambda: config.NOTION_INTERACTIONS_DS_ID,
    "followups":    lambda: config.NOTION_FOLLOWUPS_DS_ID,
}

def get_existing_props(client, ds_id: str) -> set[str]:
    ds = client.data_sources.retrieve(ds_id)
    return set(ds.get("properties", {}).keys())

def add_missing(client, ds_id: str, to_add: dict, db_name: str):
    existing = get_existing_props(client, ds_id)
    new_props = {k: v for k, v in to_add.items() if k not in existing}
    if not new_props:
        print(f"  {db_name}: all properties already exist")
        return
    client.data_sources.update(ds_id, properties=new_props)
    print(f"  {db_name}: added {len(new_props)} property/ies → {', '.join(new_props)}")


def main():
    if not config.NOTION_ENABLED:
        print("Notion not configured. Set NOTION_TOKEN, NOTION_CONTACTS_DB_ID, "
              "NOTION_INTERACTIONS_DB_ID in .env")
        sys.exit(1)

    client = notion_client.Client(auth=config.NOTION_TOKEN)

    print("\n── CRM Contacts ──────────────────────────────────────────────")
    contacts_props = {

        # ── Identity ──────────────────────────────────────────────────
        "Telegram Handle": {"rich_text": {}},           # @username
        "Email":           {"email": {}},
        "Phone":           {"phone_number": {}},

        # ── Professional ──────────────────────────────────────────────
        "Company":         {"rich_text": {}},
        "Role":            {"rich_text": {}},
        "Location":        {"rich_text": {}},

        # ── Online profiles (populated by enricher.py) ────────────────
        "LinkedIn":        {"url": {}},
        "GitHub":          {"url": {}},
        "Twitter":         {"url": {}},
        "Website":         {"url": {}},

        # ── CRM classification ────────────────────────────────────────
        "Relationship Type": {
            "select": {"options": [
                {"name": "client",   "color": "blue"},
                {"name": "partner",  "color": "green"},
                {"name": "vendor",   "color": "orange"},
                {"name": "prospect", "color": "yellow"},
                {"name": "personal", "color": "purple"},
            ]}
        },
        "Priority": {
            "select": {"options": [
                {"name": "high",   "color": "red"},
                {"name": "medium", "color": "yellow"},
                {"name": "low",    "color": "gray"},
            ]}
        },
        "Status": {
            "select": {"options": [
                {"name": "active",    "color": "green"},
                {"name": "follow-up", "color": "yellow"},
                {"name": "inactive",  "color": "gray"},
                {"name": "archived",  "color": "brown"},
            ]}
        },
        "Deal Active": {"checkbox": {}},

        # ── Tags & enrichment ─────────────────────────────────────────
        "Tags":         {"multi_select": {"options": []}},
        "Enriched At":  {"date": {}},
    }
    add_missing(client, config.NOTION_CONTACTS_DS_ID, contacts_props, "CRM Contacts")

    print("\n── CRM Interactions ──────────────────────────────────────────")
    interactions_props = {

        # ── AI intelligence ───────────────────────────────────────────
        "Relationship Type": {
            "select": {"options": [
                {"name": "client",   "color": "blue"},
                {"name": "partner",  "color": "green"},
                {"name": "vendor",   "color": "orange"},
                {"name": "prospect", "color": "yellow"},
                {"name": "personal", "color": "purple"},
            ]}
        },
        "Deal Signal": {"checkbox": {}},
        "Urgency": {
            "select": {"options": [
                {"name": "high",   "color": "red"},
                {"name": "medium", "color": "yellow"},
                {"name": "low",    "color": "gray"},
            ]}
        },
        "Topic Tags": {"multi_select": {"options": []}},

        # ── NLP enrichment ────────────────────────────────────────────
        "Sentiment": {
            "select": {"options": [
                {"name": "Positive", "color": "green"},
                {"name": "Neutral",  "color": "gray"},
                {"name": "Negative", "color": "red"},
            ]}
        },
        "People Mentioned": {"rich_text": {}},
        "Dates Mentioned":  {"rich_text": {}},
        "Emails Found":     {"rich_text": {}},
    }
    add_missing(client, config.NOTION_INTERACTIONS_DS_ID, interactions_props, "CRM Interactions")

    # Print final schemas
    print("\n── Final schemas ─────────────────────────────────────────────")
    for db_id, label in [
        (config.NOTION_CONTACTS_DB_ID, "CRM Contacts"),
        (config.NOTION_INTERACTIONS_DB_ID, "CRM Interactions"),
    ]:
        props = get_existing_props(client, db_id)
        print(f"\n  {label} ({len(props)} properties):")
        for p in sorted(props):
            print(f"    • {p}")

    print("\nDone. Both databases ready for CRM sync.\n")


if __name__ == "__main__":
    main()
