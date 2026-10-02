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
        self._settings: dict[str, Any] = {
            "servers": self._detect_default_servers(),
            "trackers": {
                "trakt": True,
                "simkl": True,
                "anilist": True,
                "mal": True,
            },
            "reconciliation": self._detect_default_reconciliation(),
        }
        self._load_settings()

    def _detect_default_reconciliation(self) -> dict[str, Any]:
        """Detect initial default reconciliation settings from Config."""
        plex_url = getattr(self.config, "PLEX_URL", "")
        plex_token = getattr(self.config, "PLEX_TOKEN", "")
        jellyfin_url = getattr(self.config, "JELLYFIN_URL", "")
        jellyfin_token = getattr(self.config, "JELLYFIN_TOKEN", "")
        jellyfin_user_id = getattr(self.config, "JELLYFIN_USER_ID", "")
        emby_url = getattr(self.config, "EMBY_URL", "")
        emby_token = getattr(self.config, "EMBY_TOKEN", "")
        emby_user_id = getattr(self.config, "EMBY_USER_ID", "")
        interval = getattr(self.config, "REVERSE_SYNC_INTERVAL", 0)
        sync_startup = getattr(self.config, "REVERSE_SYNC_ON_STARTUP", False)
        sync_ratings = getattr(self.config, "REVERSE_SYNC_RATINGS", True)
        enabled = bool((plex_url and plex_token) or (jellyfin_url and jellyfin_token) or (emby_url and emby_token))
        return {
            "enabled": enabled,
            "server_type": "plex",
            "plex_url": plex_url,
            "plex_token": plex_token,
            "jellyfin_url": jellyfin_url,
            "jellyfin_token": jellyfin_token,
            "jellyfin_user_id": jellyfin_user_id,
            "emby_url": emby_url,
            "emby_token": emby_token,
            "emby_user_id": emby_user_id,
            "interval_minutes": interval,
            "sync_on_startup": sync_startup,
            "sync_ratings": sync_ratings,
            "direction_default": "all",
        }

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

                        recon = data.get("reconciliation", {})
                        if isinstance(recon, dict):
                            current_recon = self._settings.setdefault("reconciliation", self._detect_default_reconciliation())
                            for k, v in recon.items():
                                if k in current_recon:
                                    current_recon[k] = v
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

    def get_reconciliation_settings(self, mask_token: bool = True) -> dict[str, Any]:
        """Returns reconciliation settings dictionary with optional token masking."""
        recon = dict(self._settings.get("reconciliation", self._detect_default_reconciliation()))
        raw_plex_token = recon.get("plex_token", "") or ""
        raw_jf_token = recon.get("jellyfin_token", "") or ""
        raw_emby_token = recon.get("emby_token", "") or ""

        recon["has_plex_token"] = bool(raw_plex_token)
        recon["is_plex_token_set"] = bool(raw_plex_token)
        recon["has_jellyfin_token"] = bool(raw_jf_token)
        recon["is_jellyfin_token_set"] = bool(raw_jf_token)
        recon["has_emby_token"] = bool(raw_emby_token)
        recon["is_emby_token_set"] = bool(raw_emby_token)

        # Backwards compatibility flags
        active_srv = recon.get("server_type", "plex")
        active_token = raw_jf_token if active_srv == "jellyfin" else raw_emby_token if active_srv == "emby" else raw_plex_token
        recon["is_token_set"] = bool(active_token or raw_plex_token)
        recon["has_token"] = bool(active_token or raw_plex_token)

        def _mask(tok: str) -> str:
            if not tok:
                return ""
            return "••••••••" + (tok[-4:] if len(tok) >= 4 else "")

        recon["masked_plex_token"] = _mask(raw_plex_token)
        recon["masked_jellyfin_token"] = _mask(raw_jf_token)
        recon["masked_emby_token"] = _mask(raw_emby_token)
        recon["masked_token"] = recon["masked_plex_token"]

        if mask_token:
            recon["plex_token"] = recon["masked_plex_token"]
            recon["jellyfin_token"] = recon["masked_jellyfin_token"]
            recon["emby_token"] = recon["masked_emby_token"]

        return recon

    def update_reconciliation_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        """Updates reconciliation settings and persists to disk."""
        recon = self._settings.setdefault("reconciliation", self._detect_default_reconciliation())
        for k in (
            "enabled",
            "server_type",
            "plex_url",
            "jellyfin_url",
            "jellyfin_user_id",
            "emby_url",
            "emby_user_id",
            "interval_minutes",
            "sync_on_startup",
            "sync_ratings",
            "direction_default",
        ):
            if k in data and data[k] is not None:
                if k in ("enabled", "sync_on_startup", "sync_ratings"):
                    recon[k] = bool(data[k])
                elif k == "interval_minutes":
                    try:
                        recon[k] = max(0, int(data[k]))
                    except (ValueError, TypeError):
                        pass
                else:
                    val = str(data[k]).strip()
                    if k in ("plex_url", "jellyfin_url", "emby_url"):
                        val = val.rstrip("/")
                    recon[k] = val

        # Handle Plex token
        if data.get("clear_token") or data.get("clear_plex_token"):
            recon["plex_token"] = ""
        elif "plex_token" in data and data["plex_token"] is not None:
            new_token = str(data["plex_token"]).strip()
            if new_token and not (new_token.startswith("••••") or new_token.startswith("●●●●")):
                recon["plex_token"] = new_token

        # Handle Jellyfin token
        if data.get("clear_jellyfin_token"):
            recon["jellyfin_token"] = ""
        elif "jellyfin_token" in data and data["jellyfin_token"] is not None:
            new_token = str(data["jellyfin_token"]).strip()
            if new_token and not (new_token.startswith("••••") or new_token.startswith("●●●●")):
                recon["jellyfin_token"] = new_token

        # Handle Emby token
        if data.get("clear_emby_token"):
            recon["emby_token"] = ""
        elif "emby_token" in data and data["emby_token"] is not None:
            new_token = str(data["emby_token"]).strip()
            if new_token and not (new_token.startswith("••••") or new_token.startswith("●●●●")):
                recon["emby_token"] = new_token

        if "enabled" not in data:
            recon["enabled"] = bool(
                (recon.get("plex_url") and recon.get("plex_token"))
                or (recon.get("jellyfin_url") and recon.get("jellyfin_token"))
                or (recon.get("emby_url") and recon.get("emby_token"))
            )

        self._save_settings()
        logger.info("Reconciliation settings saved to disk.")
        return self.get_reconciliation_settings(mask_token=True)

    def get_all_settings(self) -> dict[str, Any]:
        """Returns the full runtime settings dictionary."""
        return {
            "servers": dict(self._settings["servers"]),
            "trackers": dict(self._settings["trackers"]),
            "reconciliation": self.get_reconciliation_settings(mask_token=True),
        }


settings_mgr = SettingsManager()
