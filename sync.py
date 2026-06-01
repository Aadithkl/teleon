import asyncio
import sys
from datetime import datetime

import telegram
import notion
import claude
import config


async def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"CRM Sync — {now}")
    print(f"Syncing last {config.SYNC_DAYS_BACK} day(s)...")

    try:
        dialogs = await telegram.get_recent_dialogs(days_back=config.SYNC_DAYS_BACK)
    except Exception as e:
        print(f"Telegram auth/connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(dialogs)} active chats")

    synced = 0
    skipped = 0
    errors = 0

    for dialog in dialogs:
        kind_label = "DM" if dialog["kind"] == "dm" else "Group"
        print(f"  [{kind_label}] {dialog['name']}... ", end="", flush=True)

        try:
            messages = await telegram.get_messages(
                dialog["id"],
                limit=config.SYNC_MAX_MESSAGES_PER_CHAT,
                days_back=config.SYNC_DAYS_BACK,
            )
        except Exception as e:
            print(f"error fetching messages: {e}", file=sys.stderr)
            errors += 1
            continue

        if not messages:
            print("no new messages")
            skipped += 1
            continue

        try:
            crm_data = claude.extract_crm_data(dialog["name"], messages)

            source = "Telegram DM" if dialog["kind"] == "dm" else "Telegram Group"
            last_date = messages[-1]["date"]

            contact_id = notion.upsert_contact(
                name=dialog["name"],
                username=dialog.get("username"),
                source=source,
                last_contacted=last_date,
            )

            notion.log_interaction(
                contact_page_id=contact_id,
                chat_name=dialog["name"],
                kind="DM" if dialog["kind"] == "dm" else "Group",
                summary=crm_data["summary"],
                key_points=crm_data["key_points"],
                action_items=crm_data["action_items"],
                interaction_date=last_date,
            )

            print("synced")
            synced += 1

        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            errors += 1

    await telegram.close_client()
    print(f"\nDone. {synced} synced, {skipped} skipped, {errors} errors.")


if __name__ == "__main__":
    asyncio.run(main())
