from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Optional

try:
    from app.config import Config
    from app.clients.trakt_client import TraktClient
    from app.clients.simkl_client import SimklClient
    from app.clients.anilist_client import AniListClient
    from app.clients.mal_client import MyAnimeListClient
except ImportError:
    from config import Config
    from trakt_client import TraktClient
    from simkl_client import SimklClient
    from anilist_client import AniListClient
    from mal_client import MyAnimeListClient

logger = logging.getLogger("user_manager")

_SAFE_FILENAME_RE = re.compile(r"[^a-z0-9_-]")
_ALPHA_NUMERIC_RE = re.compile(r"[^a-z0-9]")


class UserClientManager:
    """Manages multi-user client instances across Trakt, Simkl, AniList, and MAL."""

    def __init__(self, config: type[Config] = Config, default_client: Optional[TraktClient] = None, tokens_dir: Optional[Path] = None):
        self.config = config
        self.tokens_dir = tokens_dir if tokens_dir is not None else (config.BASE_DIR / "data" / "tokens")
        self.tokens_dir.mkdir(parents=True, exist_ok=True)
        self._clients: dict[str, TraktClient] = {}
        self._tracker_clients: dict[tuple[str, str], Any] = {}
        if default_client:
            self._clients["default"] = default_client
            self._tracker_clients[("default", "trakt")] = default_client

    def set_default_client(self, client: TraktClient) -> None:
        """Register the primary default TraktClient instance."""
        self._clients["default"] = client
        self._tracker_clients[("default", "trakt")] = client

    def update_credentials(self, client_id: Optional[str] = None, client_secret: Optional[str] = None) -> None:
        """Update client credentials across all registered user clients."""
        for client in self._clients.values():
            client.update_credentials(client_id=client_id, client_secret=client_secret)
        for (u, tracker), client in self._tracker_clients.items():
            if hasattr(client, "update_credentials"):
                client.update_credentials(client_id=client_id, client_secret=client_secret)

    def _clean_username(self, username: Optional[str]) -> str:
        if not username:
            return "default"
        clean = "".join(c for c in username if c.isalnum() or c in ("-", "_", ".")).lower().strip()
        return clean or "default"

    def get_tokens_file(self, username: Optional[str] = None, tracker: str = "trakt") -> Path:
        """Resolve the token JSON file path for a given username and tracker."""
        clean = self._clean_username(username)
        t_id = (tracker or "trakt").lower().strip()
        if t_id == "myanimelist":
            t_id = "mal"

        if clean == "default":
            if t_id == "simkl":
                return getattr(self.config, "SIMKL_TOKENS_FILE", self.config.BASE_DIR / "data" / "simkl_tokens.json")
            elif t_id == "anilist":
                return getattr(self.config, "ANILIST_TOKENS_FILE", self.config.BASE_DIR / "data" / "anilist_token.json")
            elif t_id == "mal":
                return getattr(self.config, "MAL_TOKENS_FILE", self.config.BASE_DIR / "data" / "mal_tokens.json")
            return self.config.TRAKT_TOKENS_FILE

        suffix = "_tokens.json" if t_id == "trakt" else f"_{t_id}_tokens.json"
        direct_file = self.tokens_dir / f"{clean}{suffix}"
        if direct_file.exists():
            return direct_file
        # Check alphanumeric-only filename if dot or hyphen was stripped
        alpha_clean = _SAFE_FILENAME_RE.sub("", clean)
        alpha_file = self.tokens_dir / f"{alpha_clean}{suffix}"
        if alpha_file.exists():
            return alpha_file
        return direct_file

    def get_client(self, username: Optional[str] = None) -> TraktClient:
        """Return a cached or newly instantiated TraktClient for the user."""
        clean = self._clean_username(username)
        if clean in self._clients:
            return self._clients[clean]

        token_file = self.get_tokens_file(clean, tracker="trakt")
        client = TraktClient(config=self.config, tokens_file=token_file)
        self._clients[clean] = client
        self._tracker_clients[(clean, "trakt")] = client
        return client

    def get_tracker_client(self, username: Optional[str] = None, tracker: str = "trakt") -> Any:
        """Return a cached or newly instantiated client for the user and tracker."""
        clean = self._clean_username(username)
        t_id = (tracker or "trakt").lower().strip()
        if t_id == "myanimelist":
            t_id = "mal"

        cache_key = (clean, t_id)
        if cache_key in self._tracker_clients:
            return self._tracker_clients[cache_key]

        token_file = self.get_tokens_file(clean, tracker=t_id)

        if t_id == "trakt":
            client = self.get_client(clean)
            self._tracker_clients[cache_key] = client
            return client
        elif t_id == "simkl":
            client = SimklClient(config=self.config, tokens_file=token_file)
        elif t_id == "anilist":
            client = AniListClient(config=self.config, tokens_file=token_file)
        elif t_id == "mal":
            client = MyAnimeListClient(config=self.config, tokens_file=token_file)
        else:
            raise ValueError(f"Unsupported multi-user tracker: {tracker}")

        self._tracker_clients[cache_key] = client
        return client

    def is_user_authenticated(self, username: Optional[str] = None) -> bool:
        """Check if a specific user has valid authenticated Trakt credentials."""
        if not username:
            return False
        return self.get_client(username).is_authenticated()

    def is_tracker_authenticated(self, username: Optional[str] = None, tracker: str = "trakt") -> bool:
        """Check if a specific user has authenticated credentials for the given tracker."""
        if not username:
            return False
        try:
            client = self.get_tracker_client(username, tracker)
            return bool(client.is_authenticated())
        except Exception:
            return False

    def disconnect_user_tracker(self, username: str, tracker: str) -> bool:
        """Purge token file and clear cache for a specific user and tracker."""
        clean = self._clean_username(username)
        t_id = (tracker or "trakt").lower().strip()
        if t_id == "myanimelist":
            t_id = "mal"

        token_file = self.get_tokens_file(clean, tracker=t_id)
        cache_key = (clean, t_id)
        client = self._tracker_clients.pop(cache_key, None)
        if t_id == "trakt":
            self._clients.pop(clean, None)

        if client and hasattr(client, "access_token"):
            client.access_token = None
        if client and hasattr(client, "user_name"):
            client.user_name = None

        if token_file.exists():
            try:
                token_file.unlink()
                logger.info(f"Purged token file for user '{clean}', tracker '{t_id}': {token_file}")
                return True
            except Exception as e:
                logger.error(f"Failed to remove token file {token_file}: {e}")
                return False
        return False

    def get_user_trackers_status(self, username: Optional[str] = None) -> dict[str, Any]:
        """Returns connection and username info for all supported multi-user trackers."""
        clean = self._clean_username(username)
        res: dict[str, Any] = {}
        for t_id in ("trakt", "simkl", "anilist", "mal"):
            try:
                client = self.get_tracker_client(clean, t_id)
                auth = bool(client.is_authenticated())
                uname = getattr(client, "user_name", None)
                if not uname and auth and t_id == "trakt":
                    uname = getattr(client, "username", None)
                res[t_id] = {
                    "authenticated": auth,
                    "username": uname or ("Connected" if auth else "Not Connected"),
                    "user": uname,
                    "tokens_file": str(self.get_tokens_file(clean, t_id).name),
                }
            except Exception as e:
                res[t_id] = {"authenticated": False, "username": None, "user": None, "error": str(e)}
        return res

    def list_configured_users(self) -> list[dict[str, Any]]:
        """List all users with configured token profiles and authentication state."""
        users: list[dict[str, Any]] = []
        cowatch_user = getattr(self.config, "CO_WATCH_USER", "").strip()
        clean_cw = self._clean_username(cowatch_user) if cowatch_user else ""
        alpha_cw = _ALPHA_NUMERIC_RE.sub("", clean_cw) if clean_cw else ""

        # 1. Default user
        default_client = self.get_client("default")
        default_auth = default_client.is_authenticated()
        users.append({
            "username": "default",
            "is_default": True,
            "authenticated": default_auth,
            "tokens_file": str(default_client.tokens_file.name),
            "trackers": self.get_user_trackers_status("default"),
        })

        # 2. Per-user tokens in data/tokens/
        seen_cleans = {"default"}
        if self.tokens_dir.exists():
            for path in sorted(self.tokens_dir.glob("*.json")):
                name = path.stem
                if name == "default":
                    continue
                uname = None
                for suffix in ("_simkl_tokens", "_anilist_tokens", "_mal_tokens", "_tokens"):
                    if name.endswith(suffix):
                        uname = name[:-len(suffix)]
                        break
                if not uname or uname == "default":
                    continue

                clean_u = self._clean_username(uname)
                if clean_u in seen_cleans:
                    continue
                alpha_u = _ALPHA_NUMERIC_RE.sub("", clean_u)

                is_cw = bool(alpha_cw and (clean_u == clean_cw or alpha_u == alpha_cw))
                display_uname = cowatch_user if is_cw else uname

                client = self.get_client(display_uname)
                users.append({
                    "username": display_uname,
                    "is_default": False,
                    "is_cowatch_target": is_cw,
                    "authenticated": client.is_authenticated(),
                    "tokens_file": str(path.name),
                    "trackers": self.get_user_trackers_status(display_uname),
                })
                seen_cleans.add(clean_u)
                if alpha_u:
                    seen_cleans.add(alpha_u)

        # 3. Include configured CO_WATCH_USER if set and not already matched above
        if cowatch_user and clean_cw not in seen_cleans and alpha_cw not in seen_cleans:
            cw_client = self.get_client(cowatch_user)
            users.append({
                "username": cowatch_user,
                "is_default": False,
                "is_cowatch_target": True,
                "authenticated": cw_client.is_authenticated(),
                "tokens_file": str(cw_client.tokens_file.name),
                "trackers": self.get_user_trackers_status(cowatch_user),
            })

        return users

    async def close_all(self) -> None:
        """Gracefully close all active client HTTP sessions."""
        for client in list(self._tracker_clients.values()):
            if hasattr(client, "close") and callable(client.close):
                try:
                    await client.close()
                except Exception as e:
                    logger.debug(f"Error closing tracker client: {e}")
        self._tracker_clients.clear()
        self._clients.clear()


user_mgr = UserClientManager()
