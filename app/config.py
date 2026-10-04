import os
from pathlib import Path
from typing import Optional
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

    # Dynamic Runtime Settings File
    _settings_env = os.getenv("SETTINGS_FILE", "data/settings.json")
    SETTINGS_FILE: Path = (
        Path(_settings_env) if Path(_settings_env).is_absolute() else (BASE_DIR / _settings_env)
    )

    # Server Ingestion Enablement Flags (Plex, Jellyfin, Emby)
    # Default is None when not specified in .env, allowing dynamic detection on upgrade or explicit bool override
    _plex_env = os.getenv("PLEX_ENABLED")
    PLEX_ENABLED: Optional[bool] = (
        _plex_env.lower() in ("true", "1", "yes") if _plex_env is not None else None
    )

    _jellyfin_env = os.getenv("JELLYFIN_ENABLED")
    JELLYFIN_ENABLED: Optional[bool] = (
        _jellyfin_env.lower() in ("true", "1", "yes") if _jellyfin_env is not None else None
    )

    _emby_env = os.getenv("EMBY_ENABLED")
    EMBY_ENABLED: Optional[bool] = (
        _emby_env.lower() in ("true", "1", "yes") if _emby_env is not None else None
    )

    # Primary Tracker Enablement Flag
    TRAKT_ENABLED: bool = os.getenv("TRAKT_ENABLED", "true").lower() in ("true", "1", "yes")

    # Server settings
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8080"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    # Behavior: "scrobble" (real-time play/pause/stop + scrobble) or "watched_only" (only marks watched on scrobble)
    SCROBBLE_MODE: str = os.getenv("SCROBBLE_MODE", "scrobble").lower()

    # Minimum watched percentage to mark as viewed (Trakt standard is >= 80%)
    SCROBBLE_THRESHOLD: float = float(os.getenv("SCROBBLE_THRESHOLD", "80.0"))
    EPISODE_SCROBBLE_THRESHOLD: float = float(
        os.getenv("EPISODE_SCROBBLE_THRESHOLD", os.getenv("SCROBBLE_THRESHOLD", "80.0"))
    )
    MOVIE_SCROBBLE_THRESHOLD: float = float(
        os.getenv("MOVIE_SCROBBLE_THRESHOLD", os.getenv("SCROBBLE_THRESHOLD", "90.0"))
    )

    # Maximum activity history events retained in memory and persisted to data/events.json
    MAX_EVENT_HISTORY: int = int(os.getenv("MAX_EVENT_HISTORY", "100"))

    @classmethod
    def get_threshold(cls, media_type: str) -> float:
        """Return the scrobble threshold percentage for the given media type."""
        mt = str(media_type).lower()
        if mt == "movie":
            return cls.MOVIE_SCROBBLE_THRESHOLD
        if mt == "episode":
            return cls.EPISODE_SCROBBLE_THRESHOLD
        return cls.SCROBBLE_THRESHOLD

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
    NOTIFY_ON_FAILURE: bool = os.getenv("NOTIFY_ON_FAILURE", "true").lower() in ("true", "1", "yes")

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
    MIN_DURATION_SECONDS: int = int(os.getenv("MIN_DURATION_SECONDS", "300"))
    APPLY_MIN_DURATION_TO_EPISODES: bool = os.getenv("APPLY_MIN_DURATION_TO_EPISODES", "false").lower() in ("true", "1", "yes")
    _ignored_paths_env = os.getenv("IGNORED_PATH_PATTERNS", "")
    IGNORED_PATH_PATTERNS: list[str] = [
        s.strip() for s in _ignored_paths_env.split(",") if s.strip()
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
    _cowatch_shows_file_env = os.getenv("CO_WATCH_DATA_FILE", "data/cowatch_shows.json")
    CO_WATCH_DATA_FILE: Path = (
        Path(_cowatch_shows_file_env) if Path(_cowatch_shows_file_env).is_absolute() else (BASE_DIR / _cowatch_shows_file_env)
    )
    _cowatch_devices_file_env = os.getenv("CO_WATCH_DEVICES_DATA_FILE", "data/cowatch_devices.json")
    CO_WATCH_DEVICES_DATA_FILE: Path = (
        Path(_cowatch_devices_file_env) if Path(_cowatch_devices_file_env).is_absolute() else (BASE_DIR / _cowatch_devices_file_env)
    )

    # Sonarr & Radarr Integrations & Watchlist Automation
    SONARR_URL: str = os.getenv("SONARR_URL", "").strip().rstrip("/")
    SONARR_API_KEY: str = os.getenv("SONARR_API_KEY", "").strip()
    RADARR_URL: str = os.getenv("RADARR_URL", "").strip().rstrip("/")
    RADARR_API_KEY: str = os.getenv("RADARR_API_KEY", "").strip()
    AUTO_ADD_FROM_WATCHLIST: bool = os.getenv("AUTO_ADD_FROM_WATCHLIST", "false").lower() in ("true", "1", "yes")
    SEARCH_ON_ADD: bool = os.getenv("SEARCH_ON_ADD", "true").lower() in ("true", "1", "yes")
    ARR_WATCHLIST_INTERVAL: int = int(os.getenv("ARR_WATCHLIST_INTERVAL", "0"))
    SONARR_QUALITY_PROFILE_ID: Optional[int] = int(os.getenv("SONARR_QUALITY_PROFILE_ID", "").strip()) if os.getenv("SONARR_QUALITY_PROFILE_ID", "").strip() else None
    SONARR_ROOT_FOLDER: Optional[str] = os.getenv("SONARR_ROOT_FOLDER", "").strip() or None
    RADARR_QUALITY_PROFILE_ID: Optional[int] = int(os.getenv("RADARR_QUALITY_PROFILE_ID", "").strip()) if os.getenv("RADARR_QUALITY_PROFILE_ID", "").strip() else None
    RADARR_ROOT_FOLDER: Optional[str] = os.getenv("RADARR_ROOT_FOLDER", "").strip() or None
    ARR_NOTIFY_ON_ADD: bool = os.getenv("ARR_NOTIFY_ON_ADD", "true").lower() in ("true", "1", "yes")

    # Two-Way Synchronization & Reverse Sync (Trakt -> Media Server)
    PLEX_URL: str = os.getenv("PLEX_URL", "").strip().rstrip("/")
    PLEX_TOKEN: str = os.getenv("PLEX_TOKEN", "").strip()
    JELLYFIN_URL: str = os.getenv("JELLYFIN_URL", "").strip().rstrip("/")
    JELLYFIN_TOKEN: str = os.getenv("JELLYFIN_TOKEN", "").strip()
    JELLYFIN_USER_ID: str = os.getenv("JELLYFIN_USER_ID", "").strip()
    EMBY_URL: str = os.getenv("EMBY_URL", "").strip().rstrip("/")
    EMBY_TOKEN: str = os.getenv("EMBY_TOKEN", "").strip()
    EMBY_USER_ID: str = os.getenv("EMBY_USER_ID", "").strip()
    REVERSE_SYNC_INTERVAL: int = int(os.getenv("REVERSE_SYNC_INTERVAL", "0"))
    REVERSE_SYNC_ON_STARTUP: bool = os.getenv("REVERSE_SYNC_ON_STARTUP", "false").lower() in ("true", "1", "yes")
    REVERSE_SYNC_RATINGS: bool = os.getenv("REVERSE_SYNC_RATINGS", "true").lower() in ("true", "1", "yes")
    BACKGROUND_CLOUD_SYNC_INTERVAL_HOURS: int = int(os.getenv("BACKGROUND_CLOUD_SYNC_INTERVAL_HOURS", "24"))

    # Multi-Tracker Integration (Simkl)
    SIMKL_CLIENT_ID: str = os.getenv("SIMKL_CLIENT_ID", "").strip()
    SIMKL_CLIENT_SECRET: str = os.getenv("SIMKL_CLIENT_SECRET", "").strip()
    SIMKL_API_URL: str = os.getenv("SIMKL_API_URL", "https://api.simkl.com").rstrip("/")
    SIMKL_ENABLED: bool = os.getenv("SIMKL_ENABLED", "true" if os.getenv("SIMKL_CLIENT_ID") else "false").lower() in ("true", "1", "yes")
    _simkl_tokens_env = os.getenv("SIMKL_TOKENS_FILE", "data/simkl_tokens.json")
    SIMKL_TOKENS_FILE: Path = (
        Path(_simkl_tokens_env) if Path(_simkl_tokens_env).is_absolute() else (BASE_DIR / _simkl_tokens_env)
    )

    # Anime Tracking & Resolution Engine
    ANIME_AUTO_DETECT: bool = os.getenv("ANIME_AUTO_DETECT", "true").lower() in ("true", "1", "yes")
    _anime_cache_env = os.getenv("ANIME_CACHE_FILE", "data/anime_cache.json")
    ANIME_CACHE_FILE: Path = (
        Path(_anime_cache_env) if Path(_anime_cache_env).is_absolute() else (BASE_DIR / _anime_cache_env)
    )

    # AniList Integration
    ANILIST_CLIENT_ID: str = os.getenv("ANILIST_CLIENT_ID", "").strip()
    ANILIST_CLIENT_SECRET: str = os.getenv("ANILIST_CLIENT_SECRET", "").strip()
    ANILIST_ENABLED: bool = os.getenv("ANILIST_ENABLED", "true").lower() in ("true", "1", "yes")
    _anilist_tokens_env = os.getenv("ANILIST_TOKENS_FILE", "data/anilist_tokens.json")
    ANILIST_TOKENS_FILE: Path = (
        Path(_anilist_tokens_env) if Path(_anilist_tokens_env).is_absolute() else (BASE_DIR / _anilist_tokens_env)
    )

    # MyAnimeList (MAL) Integration
    MAL_CLIENT_ID: str = os.getenv("MAL_CLIENT_ID", "").strip()
    MAL_CLIENT_SECRET: str = os.getenv("MAL_CLIENT_SECRET", "").strip()
    MAL_ENABLED: bool = os.getenv("MAL_ENABLED", "true").lower() in ("true", "1", "yes")
    _mal_tokens_env = os.getenv("MAL_TOKENS_FILE", "data/mal_tokens.json")
    MAL_TOKENS_FILE: Path = (
        Path(_mal_tokens_env) if Path(_mal_tokens_env).is_absolute() else (BASE_DIR / _mal_tokens_env)
    )

    # TMDb Integration
    TMDB_API_KEY: str = os.getenv("TMDB_API_KEY", "").strip()
    TMDB_READ_ACCESS_TOKEN: str = os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
    TMDB_SESSION_ID: str = os.getenv("TMDB_SESSION_ID", "").strip()
    TMDB_ACCOUNT_ID: str = os.getenv("TMDB_ACCOUNT_ID", "").strip()
    TMDB_ENABLED: bool = os.getenv("TMDB_ENABLED", "true").lower() in ("true", "1", "yes")

    # Kitsu Integration
    KITSU_API_KEY: str = os.getenv("KITSU_API_KEY", "").strip()
    KITSU_USER_ID: str = os.getenv("KITSU_USER_ID", "").strip()
    KITSU_ENABLED: bool = os.getenv("KITSU_ENABLED", "true").lower() in ("true", "1", "yes")

    # Letterboxd Integration
    LETTERBOXD_USERNAME: str = os.getenv("LETTERBOXD_USERNAME", "").strip()
    LETTERBOXD_ENABLED: bool = os.getenv("LETTERBOXD_ENABLED", "true").lower() in ("true", "1", "yes")
    _letterboxd_diary_env = os.getenv("LETTERBOXD_DIARY_FILE", "data/letterboxd_diary.json")
    LETTERBOXD_DIARY_FILE: Path = (
        Path(_letterboxd_diary_env) if Path(_letterboxd_diary_env).is_absolute() else (BASE_DIR / _letterboxd_diary_env)
    )
    LETTERBOXD_DATA_FILE: Path = LETTERBOXD_DIARY_FILE

    # Serializd Integration
    SERIALIZD_USERNAME: str = os.getenv("SERIALIZD_USERNAME", "").strip()
    SERIALIZD_TOKEN: str = os.getenv("SERIALIZD_TOKEN", "").strip()
    SERIALIZD_ENABLED: bool = os.getenv("SERIALIZD_ENABLED", "true").lower() in ("true", "1", "yes")

    # MDBList Integration
    MDBLIST_API_KEY: str = os.getenv("MDBLIST_API_KEY", "").strip()
    MDBLIST_ENABLED: bool = os.getenv("MDBLIST_ENABLED", "true").lower() in ("true", "1", "yes")

    # SSL & External Reverse Proxy Configuration
    SSL_CERTFILE: str = os.getenv("SSL_CERTFILE", "").strip()
    SSL_KEYFILE: str = os.getenv("SSL_KEYFILE", "").strip()
    EXTERNAL_URL: str = os.getenv("EXTERNAL_URL", "").strip()

    # Master Passphrase for At-Rest Token & Configuration Encryption
    CONFIG_ENCRYPTION_KEY: str = os.getenv("CONFIG_ENCRYPTION_KEY", "").strip()

    # Cookie Security Settings
    COOKIE_SAMESITE: str = os.getenv("COOKIE_SAMESITE", "lax").strip().lower()




