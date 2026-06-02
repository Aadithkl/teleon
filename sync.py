import asyncio
import sys
from datetime import datetime

import telegram
import notion
import ai
import nlp
import tracker
import config

# Map ai.py relationship_type values → Notion Interactions Relationship options
_REL_MAP = {
    "client":   "Client",
    "partner":  "Partner",
    "vendor":   "Partner",   # closest match
    "prospect": "Prospect",
    "personal": "Personal",
}


async def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"CRM Sync — {now}")
    print(f"AI provider : {ai.provider_info()}")
    print(f"Notion      : {'enabled' if config.NOTION_ENABLED else 'disabled (no keys)'}")
    print(f"Throttle    : {config.SYNC_DELAY_BETWEEN_CHATS}s between chats")
    print(f"Syncing last {config.SYNC_DAYS_BACK} day(s)...\n")

    try:
        dialogs = await telegram.get_recent_dialogs(
            days_back=config.SYNC_DAYS_BACK,
            dms_only=config.SYNC_DMS_ONLY,
        )
    except Exception as e:
        print(f"Telegram auth/connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(dialogs)} active dialogs from Telegram\n")

    synced = skipped_paused = new_pending = ignored = errors = 0

    for dialog in dialogs:
        chat_status = tracker.status(dialog["id"])
        name = dialog["name"]

        if chat_status == "ignored":
            print(f"  [ignored ]  {name}")
            ignored += 1
            continue

        if chat_status == "paused":
            print(f"  [paused  ]  {name}")
            skipped_paused += 1
            continue

        if chat_status in ("unknown", "pending"):
            if config.AUTO_TRACK_NEW_CHATS:
                tracker.add_tracked(dialog)
                chat_status = "tracked"
            else:
                if chat_status == "unknown":
                    tracker.add_pending(dialog)
                    new_pending += 1
                print(f"  [NEW     ]  {name:<35} → pending")
                continue

        # tracked — run full pipeline
        print(f"  [tracked ]  {name:<35} ", end="", flush=True)

        # proactive throttle — prevents flood waits before they happen
        if config.SYNC_DELAY_BETWEEN_CHATS > 0:
            await asyncio.sleep(config.SYNC_DELAY_BETWEEN_CHATS)

        try:
            messages = await telegram.get_messages(
                dialog["id"],
                limit=config.SYNC_MAX_MESSAGES_PER_CHAT,
                days_back=config.SYNC_DAYS_BACK,
            )
        except Exception as e:
            print(f"error fetching: {e}", file=sys.stderr)
            errors += 1
            continue

        if not messages:
            print("no new messages")
            continue

        try:
            crm_data  = ai.extract_crm_data(name, messages)
            nlp_data  = nlp.enrich(messages)

            source       = "Telegram DM" if dialog["kind"] == "dm" else "Telegram Group"
            last_date    = messages[-1]["date"]
            sentiment    = nlp_data["sentiment"]["label"]
            relationship = _REL_MAP.get(crm_data.get("relationship_type", "personal"), "Personal")
            deal_signal  = crm_data.get("deal_signal", False)
            topics       = crm_data.get("topic_tags", [])
            action_items = crm_data.get("action_items", [])
            company       = ", ".join(nlp_data["entities"].get("orgs", [])[:2])
            language      = nlp_data.get("language", "English")
            last_activity = crm_data.get("last_activity", "")

            if config.NOTION_ENABLED:
                contact_id = notion.upsert_contact(
                    name=name,
                    username=dialog.get("username"),
                    source=source,
                    last_message_date=last_date,
                    company=company,
                    language=language,
                    last_activity=last_activity,
                )

                notion.log_interaction(
                    contact_page_id=contact_id,
                    contact_name=name,
                    chat_name=name,
                    summary=crm_data.get("summary", ""),
                    relationship=relationship,
                    sentiment=sentiment,
                    topics=topics,
                    action_items=action_items,
                    deal_signal=deal_signal,
                    interaction_date=last_date,
                )

            urgency_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                crm_data.get("urgency", "low"), "")
            deal_icon  = "🤝" if deal_signal else ""
            notion_tag = "" if config.NOTION_ENABLED else " [notion off]"
            activity_label = f" — {last_activity}" if last_activity else ""
            print(f"synced {urgency_icon}{deal_icon} [{relationship}]{activity_label}{notion_tag}")
            synced += 1

        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            errors += 1

    await telegram.close_client()
    print(f"\nDone. {synced} synced, {skipped_paused} paused, "
          f"{new_pending} new pending, {ignored} ignored, {errors} errors.")
    if new_pending:
        print("Run 'teleon chats pending' to review new chats.")


if __name__ == "__main__":
    asyncio.run(main())
