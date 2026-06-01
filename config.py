import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise ValueError(f"Missing required environment variable: {key}")
    return val


TELEGRAM_API_ID: int = int(_require("TELEGRAM_API_ID"))
TELEGRAM_API_HASH: str = _require("TELEGRAM_API_HASH")
TELEGRAM_PHONE: str = _require("TELEGRAM_PHONE")

NOTION_TOKEN: str = _require("NOTION_TOKEN")
NOTION_CONTACTS_DB_ID: str = _require("NOTION_CONTACTS_DB_ID")
NOTION_INTERACTIONS_DB_ID: str = _require("NOTION_INTERACTIONS_DB_ID")

ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")

SYNC_DAYS_BACK: int = int(os.getenv("SYNC_DAYS_BACK", "1"))
SYNC_MAX_MESSAGES_PER_CHAT: int = int(os.getenv("SYNC_MAX_MESSAGES_PER_CHAT", "50"))


if __name__ == "__main__":
    print("Config loaded successfully.")
    print(f"  TELEGRAM_API_ID: {TELEGRAM_API_ID}")
    print(f"  TELEGRAM_PHONE: {TELEGRAM_PHONE}")
    print(f"  NOTION_CONTACTS_DB_ID: {NOTION_CONTACTS_DB_ID}")
    print(f"  NOTION_INTERACTIONS_DB_ID: {NOTION_INTERACTIONS_DB_ID}")
    print(f"  SYNC_DAYS_BACK: {SYNC_DAYS_BACK}")
    print(f"  SYNC_MAX_MESSAGES_PER_CHAT: {SYNC_MAX_MESSAGES_PER_CHAT}")
