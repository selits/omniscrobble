import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

class Config:
    TRAKT_CLIENT_ID: str = os.getenv("TRAKT_CLIENT_ID", "")
    TRAKT_CLIENT_SECRET: str = os.getenv("TRAKT_CLIENT_SECRET", "")
    TRAKT_API_URL: str = os.getenv("TRAKT_API_URL", "https://api.trakt.tv").rstrip("/")
    _tokens_env = os.getenv("TRAKT_TOKENS_FILE", "trakt_tokens.json")
    TRAKT_TOKENS_FILE: Path = (
        Path(_tokens_env) if Path(_tokens_env).is_absolute() else (BASE_DIR / _tokens_env)
    )

    _queue_env = os.getenv("QUEUE_DB_FILE", "data/queue.db")
    QUEUE_DB_FILE: Path = (
        Path(_queue_env) if Path(_queue_env).is_absolute() else (BASE_DIR / _queue_env)
    )
    QUEUE_RETRY_INTERVAL: int = int(os.getenv("QUEUE_RETRY_INTERVAL", "300"))

    # Optional secret token to authenticate incoming webhook requests (e.g. /webhook?token=YOUR_SECRET)
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "").strip()

    # Optional comma-separated list of Plex usernames allowed to scrobble
    # If empty, all Plex users triggering this webhook are processed
    PLEX_ALLOWED_USERS: list[str] = [
        u.strip() for u in os.getenv("PLEX_ALLOWED_USERS", "").split(",") if u.strip()
    ]

    # Server settings
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8080"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    # Behavior: "scrobble" (real-time play/pause/stop + scrobble) or "watched_only" (only marks watched on scrobble)
    SCROBBLE_MODE: str = os.getenv("SCROBBLE_MODE", "scrobble").lower()

    # Minimum watched percentage to mark as viewed (Trakt standard is >= 80%)
    SCROBBLE_THRESHOLD: float = float(os.getenv("SCROBBLE_THRESHOLD", "80.0"))

    # Outgoing Notifications (Discord & Telegram)
    DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    NOTIFY_ON_SCROBBLE: bool = os.getenv("NOTIFY_ON_SCROBBLE", "true").lower() in ("true", "1", "yes")
    NOTIFY_ON_RATE: bool = os.getenv("NOTIFY_ON_RATE", "true").lower() in ("true", "1", "yes")

