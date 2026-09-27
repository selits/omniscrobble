import os
from pathlib import Path
from dotenv import load_dotenv

# BASE_DIR anchors to the repository root directory
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Config:
    BASE_DIR: Path = BASE_DIR
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

    # Outgoing Notifications (Discord, Telegram, Ntfy, Pushover)
    DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    NTFY_URL: str = os.getenv("NTFY_URL", "").strip()
    NTFY_AUTH_TOKEN: str = os.getenv("NTFY_AUTH_TOKEN", "").strip()
    NTFY_PRIORITY: str = os.getenv("NTFY_PRIORITY", "default").strip()
    PUSHOVER_USER_KEY: str = os.getenv("PUSHOVER_USER_KEY", "").strip()
    PUSHOVER_API_TOKEN: str = os.getenv("PUSHOVER_API_TOKEN", "").strip()
    PUSHOVER_PRIORITY: int = int(os.getenv("PUSHOVER_PRIORITY", "0"))
    NOTIFY_ON_SCROBBLE: bool = os.getenv("NOTIFY_ON_SCROBBLE", "true").lower() in ("true", "1", "yes")
    NOTIFY_ON_RATE: bool = os.getenv("NOTIFY_ON_RATE", "true").lower() in ("true", "1", "yes")
    NOTIFY_ON_COLLECTION: bool = os.getenv("NOTIFY_ON_COLLECTION", "true").lower() in ("true", "1", "yes")

    # Trakt Collection Sync (library.new events)
    SYNC_COLLECTION: bool = os.getenv("SYNC_COLLECTION", "true").lower() in ("true", "1", "yes")

    # Library Section Filtering (comma-separated list of Plex library names)
    _allowed_libs_env = os.getenv("ALLOWED_LIBRARIES", "")
    ALLOWED_LIBRARIES: list[str] = [
        s.strip() for s in _allowed_libs_env.split(",") if s.strip()
    ]
    _excluded_libs_env = os.getenv("EXCLUDED_LIBRARIES", "")
    EXCLUDED_LIBRARIES: list[str] = [
        s.strip() for s in _excluded_libs_env.split(",") if s.strip()
    ]

    # Homelab Observability & Metrics
    PROMETHEUS_METRICS_ENABLED: bool = os.getenv("PROMETHEUS_METRICS_ENABLED", "true").lower() in ("true", "1", "yes")

    # Multi-User & Co-Watching ("Watch Together") Settings
    CO_WATCH_USER: str = os.getenv("CO_WATCH_USER", "").strip()
    _cowatch_shows_env = os.getenv("CO_WATCH_SHOWS", "")
    CO_WATCH_SHOWS: list[str] = [
        s.strip() for s in _cowatch_shows_env.split(",") if s.strip()
    ]
    _cowatch_players_env = os.getenv("CO_WATCH_PLAYERS", "")
    CO_WATCH_PLAYERS: list[str] = [
        p.strip() for p in _cowatch_players_env.split(",") if p.strip()
    ]
    CO_WATCH_MOVIES: bool = os.getenv("CO_WATCH_MOVIES", "false").lower() in ("true", "1", "yes")
    CO_WATCH_DATA_FILE: Path = BASE_DIR / "data" / "cowatch_shows.json"

    # Sonarr & Radarr Integrations
    SONARR_URL: str = os.getenv("SONARR_URL", "").strip().rstrip("/")
    SONARR_API_KEY: str = os.getenv("SONARR_API_KEY", "").strip()
    RADARR_URL: str = os.getenv("RADARR_URL", "").strip().rstrip("/")
    RADARR_API_KEY: str = os.getenv("RADARR_API_KEY", "").strip()
