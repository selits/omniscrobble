from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Optional

try:
    from app.config import Config
    from app.clients.trakt_client import TraktClient
except ImportError:
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

    def update_credentials(self, client_id: Optional[str] = None, client_secret: Optional[str] = None) -> None:
        """Update client credentials across all registered user clients."""
        for client in self._clients.values():
            client.update_credentials(client_id=client_id, client_secret=client_secret)

    def _clean_username(self, username: Optional[str]) -> str:
        if not username:
            return "default"
        clean = "".join(c for c in username if c.isalnum() or c in ("-", "_", ".")).lower().strip()
        return clean or "default"

    def get_tokens_file(self, username: Optional[str] = None) -> Path:
        """Resolve the token JSON file path for a given username."""
        clean = self._clean_username(username)
        if clean == "default":
            return self.config.TRAKT_TOKENS_FILE
        direct_file = self.tokens_dir / f"{clean}_tokens.json"
        if direct_file.exists():
            return direct_file
        # Check alphanumeric-only filename if dot or hyphen was stripped
        alpha_clean = re.sub(r"[^a-z0-9_-]", "", clean)
        alpha_file = self.tokens_dir / f"{alpha_clean}_tokens.json"
        if alpha_file.exists():
            return alpha_file
        return direct_file

    def get_client(self, username: Optional[str] = None) -> TraktClient:
        """Return a cached or newly instantiated TraktClient for the user."""
        clean = self._clean_username(username)
        if clean in self._clients:
            return self._clients[clean]

        token_file = self.get_tokens_file(clean)
        client = TraktClient(config=self.config, tokens_file=token_file)
        self._clients[clean] = client
        return client

    def is_user_authenticated(self, username: Optional[str] = None) -> bool:
        """Check if a specific user has valid authenticated Trakt credentials."""
        if not username:
            return False
        return self.get_client(username).is_authenticated()

    def list_configured_users(self) -> list[dict[str, Any]]:
        """List all users with configured token profiles and authentication state."""
        users: list[dict[str, Any]] = []
        cowatch_user = getattr(self.config, "CO_WATCH_USER", "").strip()
        clean_cw = self._clean_username(cowatch_user) if cowatch_user else ""
        alpha_cw = re.sub(r"[^a-z0-9]", "", clean_cw) if clean_cw else ""

        # 1. Default user
        default_client = self.get_client("default")
        default_auth = default_client.is_authenticated()
        users.append({
            "username": "default",
            "is_default": True,
            "authenticated": default_auth,
            "tokens_file": str(default_client.tokens_file.name),
        })

        # 2. Per-user tokens in data/tokens/
        seen_cleans = {"default"}
        if self.tokens_dir.exists():
            for path in sorted(self.tokens_dir.glob("*_tokens.json")):
                uname = path.stem.replace("_tokens", "")
                if uname == "default":
                    continue
                clean_u = self._clean_username(uname)
                alpha_u = re.sub(r"[^a-z0-9]", "", clean_u)

                is_cw = bool(alpha_cw and (clean_u == clean_cw or alpha_u == alpha_cw))
                display_uname = cowatch_user if is_cw else uname

                client = self.get_client(display_uname)
                users.append({
                    "username": display_uname,
                    "is_default": False,
                    "is_cowatch_target": is_cw,
                    "authenticated": client.is_authenticated(),
                    "tokens_file": str(path.name),
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
