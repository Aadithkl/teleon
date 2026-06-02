"""Archive old context-less follow-ups and re-seed with context."""
import notion_client, config

c = notion_client.Client(auth=config.NOTION_TOKEN)

# mark old ones done so re-seed doesn't skip them
resp = c.data_sources.query(
    config.NOTION_FOLLOWUPS_DS_ID,
    filter={"property": "Done", "checkbox": {"equals": False}},
    page_size=100,
)
for page in resp.get("results", []):
    source_items = page["properties"].get("Source Chat", {}).get("rich_text", [])
    if source_items and source_items[0]["plain_text"] == "seeded":
        c.pages.update(page_id=page["id"], archived=True)
        name = page["properties"]["Name"]["title"][0]["plain_text"]
        print(f"  archived old: {name}")

print("Done. Run seed_followups.py next.")
