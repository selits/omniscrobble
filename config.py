import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

class Config:
    TRAKT_CLIENT_ID: str = os.getenv("TRAKT_CLIENT_ID", "")
    TRAKT_CLIENT_SECRET: str = os.getenv("TRAKT_CLIENT_SECRET", "")
    TRAKT_API_URL: str = os.getenv("TRAKT_API_URL", "https://api.trakt.tv").rstrip("/")
    TRAKT_TOKENS_FILE: Path = BASE_DIR / os.getenv("TRAKT_TOKENS_FILE", "trakt_tokens.json")

    # Optional comma-separated list of Plex usernames allowed to scrobble
    # If empty, all Plex users triggering this webhook are processed
    PLEX_ALLOWED_USERS: list[str] = [
        u.strip() for u in os.getenv("PLEX_ALLOWED_USERS", "").split(",") if u.strip()
    ]

    # Server settings
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8080"))

    # Behavior: "scrobble" (real-time play/pause/stop + scrobble) or "watched_only" (only marks watched on scrobble)
    SCROBBLE_MODE: str = os.getenv("SCROBBLE_MODE", "scrobble").lower()

    # Minimum watched percentage to mark as viewed (Trakt standard is >= 80%)
    SCROBBLE_THRESHOLD: float = float(os.getenv("SCROBBLE_THRESHOLD", "80.0"))
