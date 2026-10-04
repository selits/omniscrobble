import json
import logging
import os
from pathlib import Path
import re
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
                "tmdb": True,
                "kitsu": True,
                "letterboxd": True,
                "serializd": True,
                "mdblist": True,
            },
            "credentials": self._detect_default_credentials(),
            "reconciliation": self._detect_default_reconciliation(),
            "arr": self._detect_default_arr(),
            "rules": self._detect_default_rules(),
            "notifications": {},
            "multi_server_mirroring": bool(getattr(self.config, "MULTI_SERVER_MIRRORING", False)),
        }
        self._custom_notifications: dict[str, Any] = {}
        self._load_settings()

    def _detect_default_notifications(self) -> dict[str, Any]:
        """Detect initial default notification settings from Config."""
        return {
            "discord_webhook_url": getattr(self.config, "DISCORD_WEBHOOK_URL", "") or "",
            "telegram_bot_token": getattr(self.config, "TELEGRAM_BOT_TOKEN", "") or "",
            "telegram_chat_id": getattr(self.config, "TELEGRAM_CHAT_ID", "") or "",
            "ntfy_url": getattr(self.config, "NTFY_URL", "") or "",
            "ntfy_auth_token": getattr(self.config, "NTFY_AUTH_TOKEN", "") or "",
            "pushover_user_key": getattr(self.config, "PUSHOVER_USER_KEY", "") or "",
            "pushover_api_token": getattr(self.config, "PUSHOVER_API_TOKEN", "") or "",
            "gotify_url": getattr(self.config, "GOTIFY_URL", "") or "",
            "gotify_token": getattr(self.config, "GOTIFY_TOKEN", "") or "",
            "gotify_priority": int(getattr(self.config, "GOTIFY_PRIORITY", 5)),
            "matrix_homeserver_url": getattr(self.config, "MATRIX_HOMESERVER_URL", "") or "",
            "matrix_access_token": getattr(self.config, "MATRIX_ACCESS_TOKEN", "") or "",
            "matrix_room_id": getattr(self.config, "MATRIX_ROOM_ID", "") or "",
            "weekly_digest_enabled": bool(getattr(self.config, "WEEKLY_DIGEST_ENABLED", False)),
            "weekly_digest_day": getattr(self.config, "WEEKLY_DIGEST_DAY", "sunday") or "sunday",
            "weekly_digest_hour": int(getattr(self.config, "WEEKLY_DIGEST_HOUR", 20)),
            "notify_on_scrobble": bool(getattr(self.config, "NOTIFY_ON_SCROBBLE", True)),
            "notify_on_rate": bool(getattr(self.config, "NOTIFY_ON_RATE", True)),
            "notify_on_collection": bool(getattr(self.config, "NOTIFY_ON_COLLECTION", True)),
            "notify_on_failure": bool(getattr(self.config, "NOTIFY_ON_FAILURE", True)),
        }

    def _detect_default_credentials(self) -> dict[str, dict[str, str]]:
        """Detect initial default API credentials from Config."""
        return {
            "simkl": {
                "client_id": getattr(self.config, "SIMKL_CLIENT_ID", "") or "",
                "client_secret": getattr(self.config, "SIMKL_CLIENT_SECRET", "") or "",
            },
            "anilist": {
                "client_id": getattr(self.config, "ANILIST_CLIENT_ID", "") or "",
                "client_secret": getattr(self.config, "ANILIST_CLIENT_SECRET", "") or "",
            },
            "mal": {
                "client_id": getattr(self.config, "MAL_CLIENT_ID", "") or "",
                "client_secret": getattr(self.config, "MAL_CLIENT_SECRET", "") or "",
            },
            "trakt": {
                "client_id": getattr(self.config, "TRAKT_CLIENT_ID", "") or "",
                "client_secret": getattr(self.config, "TRAKT_CLIENT_SECRET", "") or "",
            },
            "tmdb": {
                "api_key": getattr(self.config, "TMDB_API_KEY", "") or "",
                "access_token": getattr(self.config, "TMDB_ACCESS_TOKEN", "") or "",
                "session_id": getattr(self.config, "TMDB_SESSION_ID", "") or "",
            },
            "kitsu": {
                "api_token": getattr(self.config, "KITSU_API_TOKEN", "") or "",
            },
            "letterboxd": {
                "username": getattr(self.config, "LETTERBOXD_USERNAME", "") or "",
            },
            "serializd": {
                "token": getattr(self.config, "SERIALIZD_TOKEN", "") or "",
                "username": getattr(self.config, "SERIALIZD_USERNAME", "") or "",
            },
            "mdblist": {
                "api_key": getattr(self.config, "MDBLIST_API_KEY", "") or "",
            },
        }

    def _detect_default_arr(self) -> dict[str, Any]:
        """Detect initial default *Arr acquisition settings from Config."""
        return {
            "sonarr_url": getattr(self.config, "SONARR_URL", "") or "",
            "sonarr_api_key": getattr(self.config, "SONARR_API_KEY", "") or "",
            "radarr_url": getattr(self.config, "RADARR_URL", "") or "",
            "radarr_api_key": getattr(self.config, "RADARR_API_KEY", "") or "",
            "auto_add_from_watchlist": bool(getattr(self.config, "AUTO_ADD_FROM_WATCHLIST", False)),
            "search_on_add": bool(getattr(self.config, "SEARCH_ON_ADD", True)),
            "sonarr_quality_profile_id": getattr(self.config, "SONARR_QUALITY_PROFILE_ID", None),
            "sonarr_root_folder": getattr(self.config, "SONARR_ROOT_FOLDER", None),
            "radarr_quality_profile_id": getattr(self.config, "RADARR_QUALITY_PROFILE_ID", None),
            "radarr_root_folder": getattr(self.config, "RADARR_ROOT_FOLDER", None),
            "overseerr_url": getattr(self.config, "OVERSEERR_URL", "") or "",
            "overseerr_api_key": getattr(self.config, "OVERSEERR_API_KEY", "") or "",
            "overseerr_enabled": bool(getattr(self.config, "OVERSEERR_ENABLED", False)),
        }

    def _detect_default_rules(self) -> dict[str, Any]:
        """Detect initial default scrobble rules and filters configuration from Config."""
        excluded_libs = list(getattr(self.config, "EXCLUDED_LIBRARIES", []) or [])
        return {
            "scrobble_threshold": int(getattr(self.config, "EPISODE_SCROBBLE_THRESHOLD", getattr(self.config, "SCROBBLE_THRESHOLD", 80))),
            "movie_scrobble_threshold": int(getattr(self.config, "MOVIE_SCROBBLE_THRESHOLD", 90)),
            "min_duration_seconds": int(getattr(self.config, "MIN_DURATION_SECONDS", 300)),
            "apply_min_duration_to_episodes": bool(getattr(self.config, "APPLY_MIN_DURATION_TO_EPISODES", False)),
            "ignore_libraries": excluded_libs,
            "ignore_path_patterns": list(getattr(self.config, "IGNORED_PATH_PATTERNS", []) or []),
        }

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
                from app.services.crypto_manager import crypto_mgr
                data = crypto_mgr.read_secure_json(self.settings_file)
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

                        creds = data.get("credentials", {})
                        if isinstance(creds, dict):
                            current_creds = self._settings.setdefault("credentials", self._detect_default_credentials())
                            for trk, c_vals in creds.items():
                                if isinstance(c_vals, dict):
                                    trk_k = str(trk).lower()
                                    if trk_k in ("myanimelist", "mal"):
                                        trk_k = "mal"
                                    target_c = current_creds.setdefault(trk_k, {})
                                    for ck, cv in c_vals.items():
                                        if cv is not None:
                                            target_c[ck] = str(cv).strip()

                        arr_data = data.get("arr", {})
                        if isinstance(arr_data, dict):
                            current_arr = self._settings.setdefault("arr", self._detect_default_arr())
                            for ak, av in arr_data.items():
                                if ak in current_arr and av is not None:
                                    current_arr[ak] = av

                        rules_data = data.get("rules", {})
                        if isinstance(rules_data, dict):
                            current_rules = self._settings.setdefault("rules", self._detect_default_rules())
                            for rk, rv in rules_data.items():
                                if rk in current_rules and rv is not None:
                                    current_rules[rk] = rv

                        notif_data = data.get("notifications", {})
                        if isinstance(notif_data, dict):
                            for nk, nv in notif_data.items():
                                if nv is not None:
                                    self._custom_notifications[nk] = nv
                            self._settings["notifications"] = dict(self._custom_notifications)

                        if "multi_server_mirroring" in data:
                            self._settings["multi_server_mirroring"] = bool(data["multi_server_mirroring"])
            except Exception as e:
                logger.error(f"Error reading settings from {self.settings_file}: {e}")

    def _save_settings(self) -> None:
        """Persists current runtime settings to disk atomically."""
        try:
            from app.services.crypto_manager import crypto_mgr
            crypto_mgr.write_secure_json(self.settings_file, self._settings)
        except Exception as e:
            logger.error(f"Error saving settings to {self.settings_file}: {e}")

    def is_multi_server_mirroring_enabled(self) -> bool:
        """Checks if real-time multi-server watched status mirroring is enabled."""
        return bool(self._settings.get("multi_server_mirroring", False))

    def set_multi_server_mirroring(self, enabled: bool) -> dict[str, Any]:
        """Enables or disables real-time multi-server watched status mirroring."""
        self._settings["multi_server_mirroring"] = bool(enabled)
        self._save_settings()
        logger.info(f"Multi-server mirroring setting updated to: {enabled}")
        return self.get_all_settings()

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

    @staticmethod
    def _mask_val(val: str) -> str:
        if not val:
            return ""
        return "••••••••" + (val[-4:] if len(val) >= 4 else "")

    @staticmethod
    def _is_masked(val: Any) -> bool:
        if val is None:
            return False
        s = str(val).strip()
        return s.startswith("••••") or s.startswith("●●●●") or "••••" in s

    def get_tracker_credentials(self, tracker: str, mask: bool = True) -> dict[str, Any]:
        """Returns credentials dictionary for the requested tracker with optional masking."""
        trk = str(tracker).strip().lower()
        if trk in ("myanimelist", "mal"):
            trk = "mal"
        defaults = self._detect_default_credentials().get(trk, {})
        current = self._settings.setdefault("credentials", self._detect_default_credentials()).setdefault(trk, {})

        keys = set(list(defaults.keys()) + list(current.keys()))
        if trk in ("trakt", "simkl", "anilist", "mal"):
            keys.update(["client_id", "client_secret"])

        result: dict[str, Any] = {}
        for field in keys:
            val = current.get(field) if current.get(field) is not None else defaults.get(field, "")
            val_str = str(val or "").strip()
            is_secret = any(s in field.lower() for s in ("secret", "token", "password", "key", "session_id"))
            if is_secret:
                masked = self._mask_val(val_str)
                result[field] = masked if mask else val_str
                result[f"masked_{field}"] = masked
                result[f"has_{field}"] = bool(val_str)
            else:
                result[field] = val_str
                result[f"has_{field}"] = bool(val_str)

        if "client_id" in result:
            result.setdefault("has_client_id", bool(result.get("client_id")))
        if "client_secret" in result:
            raw_sec = str(current.get("client_secret") or defaults.get("client_secret") or "")
            result.setdefault("has_client_secret", bool(raw_sec))
            result.setdefault("masked_client_secret", self._mask_val(raw_sec))

        return result

    def update_tracker_credentials(self, tracker: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update tracker credentials and persist to disk."""
        trk = str(tracker).strip().lower()
        if trk in ("myanimelist", "mal"):
            trk = "mal"
        current = self._settings.setdefault("credentials", self._detect_default_credentials()).setdefault(trk, {})

        for k, v in data.items():
            if k.startswith("clear_"):
                target_field = k[len("clear_"):]
                current[target_field] = ""
            elif v is not None:
                val = str(v).strip()
                if not self._is_masked(val):
                    current[k] = val

        self._save_settings()
        logger.info(f"Updated tracker credentials for '{trk}'")
        return self.get_tracker_credentials(trk, mask=True)

    def get_arr_settings(self, mask: bool = True) -> dict[str, Any]:
        """Returns *Arr configuration dictionary with optional token masking."""
        defaults = self._detect_default_arr()
        current = self._settings.setdefault("arr", defaults)
        res = dict(defaults)
        res.update(current)

        raw_sonarr_key = str(res.get("sonarr_api_key", "") or "")
        raw_radarr_key = str(res.get("radarr_api_key", "") or "")
        raw_overseerr_key = str(res.get("overseerr_api_key", "") or "")

        res["has_sonarr_key"] = bool(raw_sonarr_key)
        res["has_radarr_key"] = bool(raw_radarr_key)
        res["has_overseerr_key"] = bool(raw_overseerr_key)
        res["masked_sonarr_key"] = self._mask_val(raw_sonarr_key)
        res["masked_radarr_key"] = self._mask_val(raw_radarr_key)
        res["masked_overseerr_key"] = self._mask_val(raw_overseerr_key)

        if mask:
            res["sonarr_api_key"] = res["masked_sonarr_key"]
            res["radarr_api_key"] = res["masked_radarr_key"]
            res["overseerr_api_key"] = res["masked_overseerr_key"]

        return res

    def is_overseerr_enabled(self) -> bool:
        """Return True if Overseerr / Jellyseerr request bridge is enabled."""
        arr = self._settings.get("arr", {})
        return bool(arr.get("overseerr_enabled", getattr(self.config, "OVERSEERR_ENABLED", False)))

    def set_overseerr_enabled(self, enabled: bool) -> None:
        """Enable or disable Overseerr / Jellyseerr request routing."""
        arr = self._settings.setdefault("arr", self._detect_default_arr())
        arr["overseerr_enabled"] = bool(enabled)
        self._save_settings()

    def update_arr_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        """Updates *Arr automation settings and persists to disk."""
        arr = self._settings.setdefault("arr", self._detect_default_arr())
        for k in (
            "sonarr_url",
            "radarr_url",
            "overseerr_url",
            "overseerr_enabled",
            "auto_add_from_watchlist",
            "search_on_add",
            "sonarr_quality_profile_id",
            "sonarr_root_folder",
            "radarr_quality_profile_id",
            "radarr_root_folder",
        ):
            if k in data and data[k] is not None:
                if k in ("auto_add_from_watchlist", "search_on_add", "overseerr_enabled"):
                    arr[k] = bool(data[k])
                elif k in ("sonarr_quality_profile_id", "radarr_quality_profile_id"):
                    try:
                        arr[k] = int(data[k]) if str(data[k]).strip() else None
                    except (ValueError, TypeError):
                        pass
                else:
                    val = str(data[k]).strip()
                    if k in ("sonarr_url", "radarr_url", "overseerr_url"):
                        val = val.rstrip("/")
                    arr[k] = val

        if data.get("clear_sonarr_api_key"):
            arr["sonarr_api_key"] = ""
        elif "sonarr_api_key" in data and data["sonarr_api_key"] is not None:
            new_key = str(data["sonarr_api_key"]).strip()
            if not self._is_masked(new_key):
                arr["sonarr_api_key"] = new_key

        if data.get("clear_radarr_api_key"):
            arr["radarr_api_key"] = ""
        elif "radarr_api_key" in data and data["radarr_api_key"] is not None:
            new_key = str(data["radarr_api_key"]).strip()
            if not self._is_masked(new_key):
                arr["radarr_api_key"] = new_key

        if data.get("clear_overseerr_api_key"):
            arr["overseerr_api_key"] = ""
        elif "overseerr_api_key" in data and data["overseerr_api_key"] is not None:
            new_key = str(data["overseerr_api_key"]).strip()
            if not self._is_masked(new_key):
                arr["overseerr_api_key"] = new_key

        self._save_settings()
        logger.info("*Arr settings saved to disk.")
        return self.get_arr_settings(mask=True)

    def get_custom_notifications(self) -> dict[str, Any]:
        """Returns explicitly customized notification overrides."""
        return dict(self._custom_notifications)

    def get_notifications(self, mask: bool = True) -> dict[str, Any]:
        """Returns the current notifications settings with optional credential masking."""
        res = self._detect_default_notifications()
        res.update(self._custom_notifications)
        if mask:
            res["discord_webhook_url"] = self._mask_val(res.get("discord_webhook_url", ""))
            res["telegram_bot_token"] = self._mask_val(res.get("telegram_bot_token", ""))
            res["ntfy_auth_token"] = self._mask_val(res.get("ntfy_auth_token", ""))
            res["pushover_user_key"] = self._mask_val(res.get("pushover_user_key", ""))
            res["pushover_api_token"] = self._mask_val(res.get("pushover_api_token", ""))
            res["gotify_token"] = self._mask_val(res.get("gotify_token", ""))
            res["matrix_access_token"] = self._mask_val(res.get("matrix_access_token", ""))
        return res

    def update_notifications(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Updates notifications settings and persists to disk."""
        allowed_keys = {
            "discord_webhook_url",
            "telegram_bot_token",
            "telegram_chat_id",
            "ntfy_url",
            "ntfy_auth_token",
            "pushover_user_key",
            "pushover_api_token",
            "gotify_url",
            "gotify_token",
            "gotify_priority",
            "matrix_homeserver_url",
            "matrix_access_token",
            "matrix_room_id",
            "weekly_digest_enabled",
            "weekly_digest_day",
            "weekly_digest_hour",
            "notify_on_scrobble",
            "notify_on_rate",
            "notify_on_collection",
            "notify_on_failure",
        }
        boolean_keys = {
            "notify_on_scrobble",
            "notify_on_rate",
            "notify_on_collection",
            "notify_on_failure",
            "weekly_digest_enabled",
        }
        masked_keys = {
            "discord_webhook_url",
            "telegram_bot_token",
            "ntfy_auth_token",
            "pushover_user_key",
            "pushover_api_token",
            "gotify_token",
            "matrix_access_token",
        }
        for k, v in updates.items():
            if k in allowed_keys:
                if k in boolean_keys:
                    self._custom_notifications[k] = bool(v)
                elif k in masked_keys:
                    # Ignore if incoming is masked and not empty
                    if v is not None and not self._is_masked(v):
                        self._custom_notifications[k] = str(v).strip()
                elif k in ("weekly_digest_hour", "gotify_priority"):
                    try:
                        self._custom_notifications[k] = int(v)
                    except (ValueError, TypeError):
                        pass
                else:
                    if v is not None:
                        self._custom_notifications[k] = str(v).strip()
        self._settings["notifications"] = dict(self._custom_notifications)
        self._save_settings()
        logger.info("Notification settings saved to disk.")
        return self.get_notifications(mask=True)

    def is_weekly_digest_enabled(self) -> bool:
        """Return True if Weekly Activity Digest background scheduling is enabled."""
        notif = self._settings.get("notifications", {})
        return bool(notif.get("weekly_digest_enabled", getattr(self.config, "WEEKLY_DIGEST_ENABLED", False)))

    def set_weekly_digest_enabled(self, enabled: bool) -> None:
        """Enable or disable Weekly Activity Digest background dispatch."""
        notif = self._settings.setdefault("notifications", self._detect_default_notifications())
        notif["weekly_digest_enabled"] = bool(enabled)
        self._custom_notifications["weekly_digest_enabled"] = bool(enabled)
        self._save_settings()

    def get_rules_settings(self) -> dict[str, Any]:
        """Returns the current dynamic scrobble rules and filters configuration."""
        return dict(self._settings.get("rules", self._detect_default_rules()))

    def update_rules_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        """Updates dynamic scrobble rules and filters and persists to disk."""
        rules = self._settings.setdefault("rules", self._detect_default_rules())
        if "scrobble_threshold" in data and data["scrobble_threshold"] is not None:
            try:
                val = int(data["scrobble_threshold"])
                rules["scrobble_threshold"] = max(50, min(95, val))
            except (ValueError, TypeError):
                pass
        if "movie_scrobble_threshold" in data and data["movie_scrobble_threshold"] is not None:
            try:
                val = int(data["movie_scrobble_threshold"])
                rules["movie_scrobble_threshold"] = max(50, min(95, val))
            except (ValueError, TypeError):
                pass
        if "min_duration_seconds" in data and data["min_duration_seconds"] is not None:
            try:
                rules["min_duration_seconds"] = max(0, int(data["min_duration_seconds"]))
            except (ValueError, TypeError):
                pass
        if "apply_min_duration_to_episodes" in data and data["apply_min_duration_to_episodes"] is not None:
            rules["apply_min_duration_to_episodes"] = bool(data["apply_min_duration_to_episodes"])
        if "ignore_libraries" in data and isinstance(data["ignore_libraries"], (list, tuple)):
            clean_libs = []
            for lib in data["ignore_libraries"]:
                s = str(lib).strip()
                if s and s not in clean_libs:
                    clean_libs.append(s)
            rules["ignore_libraries"] = clean_libs
        if "ignore_path_patterns" in data and isinstance(data["ignore_path_patterns"], (list, tuple)):
            clean_patterns = []
            for pat in data["ignore_path_patterns"]:
                s = str(pat).strip()
                if s:
                    try:
                        re.compile(s)
                        if s not in clean_patterns:
                            clean_patterns.append(s)
                    except re.error:
                        logger.warning(f"Ignoring invalid regex pattern in rules: {s}")
            rules["ignore_path_patterns"] = clean_patterns

        self._save_settings()
        logger.info("Rules and filters configuration updated and saved to disk.")
        return self.get_rules_settings()

    def get_effective_threshold(self, media_type: str) -> float:
        """Return the effective scrobble threshold percentage for the media type."""
        rules = self.get_rules_settings()
        m_type = (media_type or "").lower().strip()
        if m_type == "movie":
            if "movie_scrobble_threshold" in rules and rules["movie_scrobble_threshold"] is not None:
                return float(rules["movie_scrobble_threshold"])
            return float(getattr(self.config, "MOVIE_SCROBBLE_THRESHOLD", 90.0))
        if "scrobble_threshold" in rules and rules["scrobble_threshold"] is not None:
            return float(rules["scrobble_threshold"])
        return float(self.config.get_threshold(m_type))

    def is_media_allowed(self, media: Any) -> tuple[bool, str]:
        """Evaluates whether the given media item is allowed to be scrobbled or processed.

        Returns (True, "Allowed") or (False, "reason for bypass").
        """
        rules = self.get_rules_settings()
        min_dur = rules.get("min_duration_seconds", 300)
        apply_to_eps = rules.get("apply_min_duration_to_episodes", False)
        ignore_libs = [str(x).strip().lower() for x in rules.get("ignore_libraries", []) if str(x).strip()]
        ignore_patterns = rules.get("ignore_path_patterns", [])

        # 1. Check Library Name Exclusion
        lib_title = str(getattr(media, "library_section_title", "") or "").strip().lower()
        if lib_title and any(lib_title == ex for ex in ignore_libs):
            return False, f"Library section '{getattr(media, 'library_section_title', '')}' is ignored in rules"

        # 2. Check Minimum Duration
        media_type = str(getattr(media, "media_type", "") or "").lower().strip()
        duration_ms = getattr(media, "duration_ms", None)
        if duration_ms is not None and duration_ms > 0:
            duration_s = duration_ms / 1000.0
            should_check_duration = (media_type == "movie") or (apply_to_eps and media_type in ("episode", "show"))
            if should_check_duration and duration_s < min_dur:
                return False, f"Duration below minimum threshold ({int(duration_s)}s < {min_dur}s)"

        # 3. Check File Path Regex Exclusion
        file_path = str(getattr(media, "file_path", "") or "").strip()
        if not file_path and isinstance(getattr(media, "raw_payload", None), dict):
            raw = media.raw_payload
            meta = raw.get("Metadata", {}) if isinstance(raw, dict) else {}
            media_list = meta.get("Media", [])
            first_m = media_list[0] if isinstance(media_list, list) and media_list and isinstance(media_list[0], dict) else {}
            part_list = first_m.get("Part", [])
            first_p = part_list[0] if isinstance(part_list, list) and part_list and isinstance(part_list[0], dict) else {}
            file_path = str(first_p.get("file") or raw.get("Item", {}).get("Path") or raw.get("Path") or "")

        if file_path and ignore_patterns:
            for pat in ignore_patterns:
                try:
                    if re.search(pat, file_path, re.IGNORECASE):
                        return False, f"File path matched ignore pattern '{pat}'"
                except re.error:
                    continue

        return True, "Allowed"

    def get_all_settings(self, mask_token: bool = True) -> dict[str, Any]:
        """Returns the full runtime settings dictionary."""
        return {
            "servers": dict(self._settings["servers"]),
            "trackers": dict(self._settings["trackers"]),
            "credentials": {
                t: self.get_tracker_credentials(t, mask=mask_token)
                for t in ("trakt", "simkl", "tmdb", "anilist", "mal", "kitsu", "letterboxd", "serializd", "mdblist")
            },
            "reconciliation": self.get_reconciliation_settings(mask_token=mask_token),
            "arr": self.get_arr_settings(mask=mask_token),
            "rules": self.get_rules_settings(),
            "notifications": self.get_notifications(mask=mask_token),
            "multi_server_mirroring": self.is_multi_server_mirroring_enabled(),
        }

    def update_all_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update any subset of settings and persist to disk."""
        if "servers" in data and isinstance(data["servers"], dict):
            for srv, en in data["servers"].items():
                self._settings["servers"][str(srv).lower().strip()] = bool(en)

        if "trackers" in data and isinstance(data["trackers"], dict):
            for trk, en in data["trackers"].items():
                k = str(trk).lower().strip()
                if k in ("myanimelist", "mal"):
                    k = "mal"
                self._settings["trackers"][k] = bool(en)

        if "credentials" in data and isinstance(data["credentials"], dict):
            for trk, creds in data["credentials"].items():
                if isinstance(creds, dict):
                    self.update_tracker_credentials(trk, creds)

        if "reconciliation" in data and isinstance(data["reconciliation"], dict):
            self.update_reconciliation_settings(data["reconciliation"])

        if "arr" in data and isinstance(data["arr"], dict):
            self.update_arr_settings(data["arr"])

        if "rules" in data and isinstance(data["rules"], dict):
            self.update_rules_settings(data["rules"])

        if "notifications" in data and isinstance(data["notifications"], dict):
            self.update_notifications(data["notifications"])

        if "multi_server_mirroring" in data:
            self._settings["multi_server_mirroring"] = bool(data["multi_server_mirroring"])

        self._save_settings()
        return self.get_all_settings(mask_token=True)


settings_mgr = SettingsManager()
