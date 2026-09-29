import json
import logging
import os
from pathlib import Path
from typing import Any

try:
    from app.config import Config
except ImportError:
    from config import Config

logger = logging.getLogger("settings_manager")


class SettingsManager:
    """Manages persistent dynamic enablement settings for media servers and trackers."""

    def __init__(
        self,
        config: type[Config] = Config,
        settings_file: Path | None = None,
        data_dir: Path | None = None,
    ):
        self.config = config
        self.settings_file: Path = settings_file or config.SETTINGS_FILE
        self.data_dir: Path = (
            data_dir
            if data_dir is not None
            else getattr(config, "DATA_DIR", getattr(config, "BASE_DIR", Path(".")) / "data")
        )
        self._settings: dict[str, dict[str, bool]] = {
            "servers": self._detect_default_servers(),
            "trackers": {
                "trakt": True,
                "simkl": True,
                "anilist": True,
                "mal": True,
            },
        }
        self._load_settings()

    def _detect_default_servers(self) -> dict[str, bool]:
        """Detect initial default enablement for media servers.

        All media servers default to False (disabled) out-of-the-box.
        On upgrade or first run:
        - If explicitly configured in environment variables (PLEX_ENABLED, JELLYFIN_ENABLED, EMBY_ENABLED),
          respect the environment setting.
        - Otherwise, detect whether the server was previously active:
          * Plex: enabled if PLEX_URL / PLEX_TOKEN / PLEX_ALLOWED_USERS are set,
            or if trakt_tokens.json exists (upgraded legacy install),
            or if historical Plex events exist in data/events.json,
            or if prior database / co-watch configuration exists.
          * Jellyfin: enabled only if JELLYFIN_ENABLED is set or historical Jellyfin events exist.
          * Emby: enabled only if EMBY_ENABLED is set or historical Emby events exist.
        """
        defaults = {
            "plex": False,
            "jellyfin": False,
            "emby": False,
        }

        # 1. Explicit env configuration overrides
        env_plex = getattr(self.config, "PLEX_ENABLED", None)
        if env_plex is not None:
            defaults["plex"] = bool(env_plex)

        env_jellyfin = getattr(self.config, "JELLYFIN_ENABLED", None)
        if env_jellyfin is not None:
            defaults["jellyfin"] = bool(env_jellyfin)

        env_emby = getattr(self.config, "EMBY_ENABLED", None)
        if env_emby is not None:
            defaults["emby"] = bool(env_emby)

        # 2. Upgrade detection: inspect data directory and tokens for existing deployment
        base_dir = getattr(self.config, "BASE_DIR", Path("."))
        tokens_file = getattr(self.config, "TRAKT_TOKENS_FILE", base_dir / "trakt_tokens.json")
        has_plex_env = bool(
            getattr(self.config, "PLEX_ALLOWED_USERS", [])
            or os.getenv("PLEX_URL")
            or os.getenv("PLEX_TOKEN")
        )
        is_existing_upgrade = (
            has_plex_env
            or tokens_file.exists()
            or (base_dir / "trakt_tokens.json").exists()
            or (self.data_dir / "trakt_tokens.json").exists()
            or (self.data_dir / "events.json").exists()
            or (self.data_dir / "queue.db").exists()
            or (self.data_dir / "cowatch_shows.json").exists()
        )

        if is_existing_upgrade:
            if env_plex is None:
                defaults["plex"] = True

            # Check if Jellyfin or Emby had historical events
            events_file = self.data_dir / "events.json"
            if events_file.exists():
                try:
                    with open(events_file, "r", encoding="utf-8") as f:
                        events = json.load(f)
                        if isinstance(events, list):
                            for ev in events:
                                srv = str(ev.get("server", "")).lower()
                                if srv == "jellyfin" and env_jellyfin is None:
                                    defaults["jellyfin"] = True
                                elif srv == "emby" and env_emby is None:
                                    defaults["emby"] = True
                except Exception:
                    pass

        return defaults

    def _load_settings(self) -> None:
        """Loads settings from disk and merges with default environment configurations."""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        servers = data.get("servers", {})
                        if isinstance(servers, dict):
                            for k, v in servers.items():
                                self._settings["servers"][k.lower()] = bool(v)

                        trackers = data.get("trackers", {})
                        if isinstance(trackers, dict):
                            for k, v in trackers.items():
                                self._settings["trackers"][k.lower()] = bool(v)
            except Exception as e:
                logger.error(f"Error reading settings from {self.settings_file}: {e}")

    def _save_settings(self) -> None:
        """Persists current runtime settings to disk."""
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving settings to {self.settings_file}: {e}")

    def is_server_enabled(self, server: str) -> bool:
        """Checks if ingestion for the given media server is currently active."""
        key = str(server).strip().lower()
        return self._settings["servers"].get(key, True)

    def set_server_enabled(self, server: str, enabled: bool) -> dict[str, Any]:
        """Updates enablement state for a media server and saves to disk."""
        key = str(server).strip().lower()
        self._settings["servers"][key] = bool(enabled)
        self._save_settings()
        logger.info(f"Server '{key}' ingestion setting updated to: {enabled}")
        return self.get_all_settings()

    def is_tracker_enabled(self, tracker: str) -> bool:
        """Checks if synchronization to the given tracker is currently active."""
        key = str(tracker).strip().lower()
        if key in ("myanimelist", "mal"):
            key = "mal"
        return self._settings["trackers"].get(key, True)

    def set_tracker_enabled(self, tracker: str, enabled: bool) -> dict[str, Any]:
        """Updates enablement state for a tracker and saves to disk."""
        key = str(tracker).strip().lower()
        if key in ("myanimelist", "mal"):
            key = "mal"
        self._settings["trackers"][key] = bool(enabled)
        self._save_settings()
        logger.info(f"Tracker '{key}' sync setting updated to: {enabled}")
        return self.get_all_settings()

    def get_all_settings(self) -> dict[str, Any]:
        """Returns the full runtime settings dictionary."""
        return {
            "servers": dict(self._settings["servers"]),
            "trackers": dict(self._settings["trackers"]),
        }


settings_mgr = SettingsManager()
