import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise ValueError(f"Missing required environment variable: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default) or default


# ── Telegram (required) ────────────────────────────────────────────────────────
TELEGRAM_API_ID: int = int(_require("TELEGRAM_API_ID"))
TELEGRAM_API_HASH: str = _require("TELEGRAM_API_HASH")
TELEGRAM_PHONE: str = _require("TELEGRAM_PHONE")

# ── Notion (optional — skip writing if not configured) ────────────────────────
NOTION_TOKEN: str              = _optional("NOTION_TOKEN")
NOTION_CONTACTS_DB_ID: str     = _optional("NOTION_CONTACTS_DB_ID")
NOTION_INTERACTIONS_DB_ID: str = _optional("NOTION_INTERACTIONS_DB_ID")
NOTION_FOLLOWUPS_DB_ID: str    = _optional("NOTION_FOLLOWUPS_DB_ID")

# data source IDs — used for queries in notion-client v3+ (data_sources.query)
# Find these by opening each DB in Notion, then: Share → Copy link → the UUID in the URL
NOTION_CONTACTS_DS_ID: str     = _optional("NOTION_CONTACTS_DS_ID")
NOTION_INTERACTIONS_DS_ID: str = _optional("NOTION_INTERACTIONS_DS_ID")
NOTION_FOLLOWUPS_DS_ID: str    = _optional("NOTION_FOLLOWUPS_DS_ID")

NOTION_ENABLED: bool = bool(NOTION_TOKEN and NOTION_CONTACTS_DB_ID and NOTION_INTERACTIONS_DB_ID)

# ── AI provider (optional — falls back to NLP-only if not set) ────────────────
AI_PROVIDER: str = _optional("AI_PROVIDER", "none")   # claude | gemini | openai | none
AI_MODEL: str = _optional("AI_MODEL", "")              # override default model
AI_API_KEY: str = _optional("AI_API_KEY", "")          # single key slot (any provider)

# provider-specific keys (used if AI_API_KEY is not set)
ANTHROPIC_API_KEY: str = _optional("ANTHROPIC_API_KEY")
GEMINI_API_KEY: str = _optional("GEMINI_API_KEY")
OPENAI_API_KEY: str = _optional("OPENAI_API_KEY")

# ── Sync behaviour ─────────────────────────────────────────────────────────────
SYNC_DAYS_BACK: int = int(_optional("SYNC_DAYS_BACK", "1"))
SYNC_MAX_MESSAGES_PER_CHAT: int = int(_optional("SYNC_MAX_MESSAGES_PER_CHAT", "50"))
SYNC_DMS_ONLY: bool = _optional("SYNC_DMS_ONLY", "false").lower() == "true"
AUTO_TRACK_NEW_CHATS: bool = _optional("AUTO_TRACK_NEW_CHATS", "false").lower() == "true"
SYNC_DELAY_BETWEEN_CHATS: float = float(_optional("SYNC_DELAY_BETWEEN_CHATS", "1.5"))


if __name__ == "__main__":
    print("Config loaded successfully.")
    print(f"  TELEGRAM_API_ID : {TELEGRAM_API_ID}")
    print(f"  TELEGRAM_PHONE  : {TELEGRAM_PHONE}")
    print(f"  NOTION_ENABLED  : {NOTION_ENABLED}")
    print(f"  AI_PROVIDER     : {AI_PROVIDER}" + (f" / {AI_MODEL}" if AI_MODEL else ""))
    print(f"  SYNC_DAYS_BACK  : {SYNC_DAYS_BACK}")
