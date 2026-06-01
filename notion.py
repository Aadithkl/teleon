from datetime import date

import notion_client

import config

_client: notion_client.Client | None = None


def get_client() -> notion_client.Client:
    global _client
    if _client is None:
        _client = notion_client.Client(auth=config.NOTION_TOKEN)
    return _client


def find_contact(name: str) -> dict | None:
    client = get_client()
    try:
        resp = client.databases.query(
            database_id=config.NOTION_CONTACTS_DB_ID,
            filter={
                "property": "Name",
                "title": {"equals": name},
            },
        )
    except notion_client.errors.APIResponseError as e:
        if e.status == 404:
            raise ValueError("Check NOTION_CONTACTS_DB_ID in .env") from e
        raise
    results = resp.get("results", [])
    return results[0] if results else None


def upsert_contact(name: str, username: str | None, source: str, last_contacted: str) -> str:
    client = get_client()
    today = date.today().isoformat()
    date_value = last_contacted[:10] if last_contacted else today

    existing = find_contact(name)

    username_prop = (
        {"rich_text": [{"text": {"content": username}}]}
        if username
        else {"rich_text": []}
    )

    if existing:
        page_id = existing["id"]
        props: dict = {
            "Source": {"select": {"name": source}},
            "Last Contacted": {"date": {"start": date_value}},
        }
        if username:
            props["Username"] = username_prop
        client.pages.update(page_id=page_id, properties=props)
        return page_id
    else:
        try:
            page = client.pages.create(
                parent={"database_id": config.NOTION_CONTACTS_DB_ID},
                properties={
                    "Name": {"title": [{"text": {"content": name}}]},
                    "Username": username_prop,
                    "Source": {"select": {"name": source}},
                    "First Seen": {"date": {"start": today}},
                    "Last Contacted": {"date": {"start": date_value}},
                },
            )
        except notion_client.errors.APIResponseError as e:
            if e.status == 404:
                raise ValueError("Check NOTION_CONTACTS_DB_ID in .env") from e
            raise
        return page["id"]


def log_interaction(
    contact_page_id: str,
    chat_name: str,
    kind: str,
    summary: str,
    key_points: list[str],
    action_items: list[str],
    interaction_date: str,
) -> str:
    client = get_client()
    date_value = interaction_date[:10]
    title = f"{chat_name} — {date_value}"

    body_blocks = []

    body_blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Key Points"}}]},
    })
    for point in key_points:
        body_blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"- {point}"}}]},
        })

    body_blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": ""}}]},
    })
    body_blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Action Items"}}]},
    })
    for item in action_items:
        body_blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"- {item}"}}]},
        })

    try:
        page = client.pages.create(
            parent={"database_id": config.NOTION_INTERACTIONS_DB_ID},
            properties={
                "Name": {"title": [{"text": {"content": title}}]},
                "Contact": {"relation": [{"id": contact_page_id}]},
                "Date": {"date": {"start": date_value}},
                "Chat": {"rich_text": [{"text": {"content": chat_name}}]},
                "Summary": {"rich_text": [{"text": {"content": summary}}]},
                "Kind": {"select": {"name": kind}},
            },
            children=body_blocks,
        )
    except notion_client.errors.APIResponseError as e:
        if e.status == 404:
            raise ValueError("Check NOTION_INTERACTIONS_DB_ID in .env") from e
        raise
    return page["id"]


def list_contacts(limit: int = 20) -> list[dict]:
    client = get_client()
    resp = client.databases.query(
        database_id=config.NOTION_CONTACTS_DB_ID,
        sorts=[{"property": "Last Contacted", "direction": "descending"}],
        page_size=limit,
    )
    contacts = []
    for page in resp.get("results", []):
        props = page["properties"]

        name_items = props.get("Name", {}).get("title", [])
        name = name_items[0]["plain_text"] if name_items else ""

        username_items = props.get("Username", {}).get("rich_text", [])
        username = username_items[0]["plain_text"] if username_items else ""

        source = props.get("Source", {}).get("select", {})
        source_name = source.get("name", "") if source else ""

        lc = props.get("Last Contacted", {}).get("date", {})
        last_contacted = lc.get("start", "") if lc else ""

        contacts.append({
            "name": name,
            "username": username,
            "source": source_name,
            "last_contacted": last_contacted,
        })
    return contacts


def get_interactions_for_contact(contact_page_id: str, limit: int = 10) -> list[dict]:
    client = get_client()
    resp = client.databases.query(
        database_id=config.NOTION_INTERACTIONS_DB_ID,
        filter={
            "property": "Contact",
            "relation": {"contains": contact_page_id},
        },
        sorts=[{"property": "Date", "direction": "descending"}],
        page_size=limit,
    )
    interactions = []
    for page in resp.get("results", []):
        props = page["properties"]

        date_val = props.get("Date", {}).get("date", {})
        date_str = date_val.get("start", "") if date_val else ""

        chat_items = props.get("Chat", {}).get("rich_text", [])
        chat = chat_items[0]["plain_text"] if chat_items else ""

        summary_items = props.get("Summary", {}).get("rich_text", [])
        summary = summary_items[0]["plain_text"] if summary_items else ""

        interactions.append({
            "date": date_str,
            "chat": chat,
            "summary": summary,
        })
    return interactions
