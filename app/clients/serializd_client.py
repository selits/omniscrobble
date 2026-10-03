"""Client for interacting with Serializd Social TV Diary API.

Allows TV episode watch logging (>= 80% completion) and rating synchronization
leveraging TMDb and TVDb identifiers.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
import httpx

try:
    from app.config import Config
except ImportError:
    from config import Config

logger = logging.getLogger("omniscrobble.serializd_client")


class SerializdClient:
    """Asynchronous client for Serializd TV Diary API."""

    BASE_URL = "https://www.serializd.com/api"

    def __init__(
        self,
        config: type[Config] = Config,
        token: Optional[str] = None,
        username: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.config = config
        self.token = token or getattr(config, "SERIALIZD_TOKEN", "") or ""
        self.username = username or getattr(config, "SERIALIZD_USERNAME", "") or ""
        if transport is not None and client is None:
            client = httpx.AsyncClient(transport=transport, timeout=10.0)
        self._external_client = client
        self._internal_client: Optional[httpx.AsyncClient] = None

    def get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        if self._internal_client is None or self._internal_client.is_closed:
            self._internal_client = httpx.AsyncClient(timeout=10.0)
        return self._internal_client

    def is_configured(self) -> bool:
        return bool(self.token)

    def is_authenticated(self) -> bool:
        return bool(self.token)

    def is_enabled(self) -> bool:
        return self.is_configured()

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Omniscrobble/2.5.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-Serializd-Token"] = self.token
        return headers

    async def check_connection(self) -> dict[str, Any]:
        """Verify token and resolve user profile."""
        if not self.is_configured():
            return {
                "name": "Serializd",
                "configured": False,
                "authenticated": False,
                "enabled": True,
                "status": "unconfigured",
                "message": "Serializd token is not configured.",
            }

        client = self.get_client()
        url = f"{self.BASE_URL}/me"
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                self.username = data.get("username") or self.username or "user"
                return {
                    "name": "Serializd",
                    "configured": True,
                    "authenticated": True,
                    "enabled": True,
                    "status": "connected",
                    "username": self.username,
                    "user": self.username,
                    "message": f"Connected as @{self.username}",
                }
            elif resp.status_code == 404 and self.username:
                # Fallback to checking public profile if /me endpoint is not exposed
                profile_url = f"{self.BASE_URL}/user/{self.username}"
                p_resp = await client.get(profile_url, headers=self._get_headers())
                if p_resp.status_code == 200:
                    return {
                        "name": "Serializd",
                        "configured": True,
                        "authenticated": True,
                        "enabled": True,
                        "status": "connected",
                        "username": self.username,
                        "message": f"Profile verified for @{self.username}",
                    }

            return {
                "name": "Serializd",
                "configured": True,
                "authenticated": False,
                "enabled": True,
                "status": "error",
                "message": f"Serializd returned HTTP {resp.status_code}",
            }
        except Exception as e:
            logger.error("Serializd check_connection failed: %s", e)
            return {
                "name": "Serializd",
                "configured": True,
                "authenticated": False,
                "enabled": True,
                "status": "error",
                "message": str(e),
            }

    async def log_episode(
        self,
        show_title: str,
        season: int,
        episode: int,
        tmdb_id: Optional[int | str] = None,
        rating: Optional[float | int] = None,
    ) -> dict[str, Any]:
        """Log a watched TV episode to Serializd diary."""
        if not self.is_configured():
            return {"status": "error", "message": "Serializd not configured"}

        client = self.get_client()
        url = f"{self.BASE_URL}/diary/log"

        payload: dict[str, Any] = {
            "show_title": show_title,
            "season": season,
            "episode": episode,
        }
        if tmdb_id:
            payload["tmdb_id"] = int(tmdb_id)
        converted_rating = None
        if rating is not None and float(rating) > 0:
            converted_rating = round(float(rating) / 2.0, 1)  # 0.5 - 5.0 scale
            payload["rating"] = converted_rating

        try:
            resp = await client.post(url, headers=self._get_headers(), json=payload)
            if resp.status_code in (200, 201):
                result = {"status": "success", "show": show_title, "season": season, "episode": episode}
                if converted_rating is not None:
                    result["rating"] = converted_rating
                    result["rating10"] = rating
                return result
            return {"status": "error", "error": f"Serializd HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error("Serializd log_episode failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def sync_rating(
        self,
        show_title: str,
        rating: float | int,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        tmdb_id: Optional[int | str] = None,
    ) -> dict[str, Any]:
        """Submit a 1-5 star rating to Serializd."""
        return await self.log_episode(
            show_title=show_title,
            season=season or 1,
            episode=episode or 1,
            tmdb_id=tmdb_id,
            rating=rating,
        )

    async def close(self) -> None:
        if self._internal_client and not self._internal_client.is_closed:
            await self._internal_client.aclose()
            self._internal_client = None
