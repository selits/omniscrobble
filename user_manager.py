import logging
from pathlib import Path
from typing import Any, Optional

from config import Config
from trakt_client import TraktClient

logger = logging.getLogger("user_manager")


class UserClientManager:
    """Manages multi-user Trakt client instances and per-user token files."""

    def __init__(self, config: type[Config] = Config, default_client: Optional[TraktClient] = None):
        self.config = config
        self.tokens_dir = config.BASE_DIR / "data" / "tokens"
        self.tokens_dir.mkdir(parents=True, exist_ok=True)
        self._clients: dict[str, TraktClient] = {}
        if default_client:
            self._clients["default"] = default_client

    def set_default_client(self, client: TraktClient) -> None:
        """Register the primary default TraktClient instance."""
        self._clients["default"] = client

    def _clean_username(self, username: Optional[str]) -> str:
        if not username:
            return "default"
        clean = "".join(c for c in username if c.isalnum() or c in ("-", "_")).lower().strip()
        return clean or "default"

    def get_tokens_file(self, username: Optional[str] = None) -> Path:
        """Resolve the token JSON file path for a given username."""
        clean = self._clean_username(username)
        if clean == "default":
            return self.config.TRAKT_TOKENS_FILE
        return self.tokens_dir / f"{clean}_tokens.json"

    def get_client(self, username: Optional[str] = None) -> TraktClient:
        """Return a cached or newly instantiated TraktClient for the user."""
        clean = self._clean_username(username)
        if clean in self._clients:
            return self._clients[clean]

        token_file = self.get_tokens_file(clean)
        # If user-specific token does not exist but default does and user was "default",
        # it points to default token file
        client = TraktClient(config=self.config, tokens_file=token_file)
        self._clients[clean] = client
        return client

    def list_configured_users(self) -> list[dict[str, Any]]:
        """List all users with configured token profiles and authentication state."""
        users = []

        # 1. Default user
        default_client = self.get_client("default")
        default_auth = default_client.is_authenticated()
        default_profile = default_client.load_tokens()
        users.append({
            "username": "default",
            "is_default": True,
            "authenticated": default_auth,
            "tokens_file": str(default_client.tokens_file.name),
        })

        # 2. Per-user tokens in data/tokens/
        if self.tokens_dir.exists():
            for path in sorted(self.tokens_dir.glob("*_tokens.json")):
                uname = path.stem.replace("_tokens", "")
                if uname == "default":
                    continue
                client = self.get_client(uname)
                users.append({
                    "username": uname,
                    "is_default": False,
                    "authenticated": client.is_authenticated(),
                    "tokens_file": str(path.name),
                })

        # 3. Include configured CO_WATCH_USER if set and not already present
        cowatch_user = getattr(self.config, "CO_WATCH_USER", "").strip()
        if cowatch_user and not any(u["username"].lower() == cowatch_user.lower() for u in users):
            cw_client = self.get_client(cowatch_user)
            users.append({
                "username": cowatch_user,
                "is_default": False,
                "is_cowatch_target": True,
                "authenticated": cw_client.is_authenticated(),
                "tokens_file": str(cw_client.tokens_file.name),
            })

        return users

    async def close_all(self) -> None:
        """Gracefully close all active TraktClient HTTP sessions."""
        for client in self._clients.values():
            try:
                await client.close()
            except Exception as e:
                logger.debug(f"Error closing TraktClient: {e}")
        self._clients.clear()


user_mgr = UserClientManager()
