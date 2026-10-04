"""MyAnimeList REST API v2 client for Omniscrobble anime tracking.

Handles OAuth token persistence, user profile validation, anime search,
episode progress scrobbling, and rating synchronization.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import Config
from app.services.atomic_writer import atomic_write_json

logger = logging.getLogger("omniscrobble.mal")


class MyAnimeListClient:
    """Asynchronous client for interacting with the MyAnimeList (MAL) API v2."""

    BASE_URL = "https://api.myanimelist.net/v2"

    def __init__(
        self,
        config: type[Config] = Config,
        client: Optional[httpx.AsyncClient] = None,
        tokens_file: Optional[Path] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> None:
        self.config = config
        try:
            from app.services.settings_manager import settings_mgr
            stored_creds = settings_mgr.get_tracker_credentials("mal", mask=False)
        except Exception:
            stored_creds = {}
        self.client_id = client_id or stored_creds.get("client_id") or config.MAL_CLIENT_ID
        self.client_secret = client_secret or stored_creds.get("client_secret") or config.MAL_CLIENT_SECRET
        self.tokens_file = tokens_file or config.MAL_TOKENS_FILE
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(timeout=15.0)

        self.access_token: Optional[str] = access_token
        self.refresh_token: Optional[str] = None
        self.user_name: Optional[str] = None
        self.user_avatar: Optional[str] = None
        self.user_id: Optional[int] = None
        self.load_tokens()

    def update_credentials(self, client_id: Optional[str] = None, client_secret: Optional[str] = None) -> None:
        """Update client credentials in-memory dynamically."""
        if client_id is not None:
            self.client_id = client_id
        if client_secret is not None:
            self.client_secret = client_secret

    @property
    def effective_client_id(self) -> str:
        if self.client_id:
            return self.client_id
        try:
            from app.services.settings_manager import settings_mgr
            return settings_mgr.get_tracker_credentials("mal", mask=False).get("client_id", "")
        except Exception:
            return getattr(self.config, "MAL_CLIENT_ID", "")

    def load_tokens(self) -> None:
        """Load stored tokens and user details from disk."""
        if not self.tokens_file.exists():
            return
        try:
            with open(self.tokens_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not self.access_token:
                    self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.user_name = data.get("user_name") or data.get("user")
                self.user_avatar = data.get("user_avatar")
                self.user_id = data.get("user_id")
        except Exception as e:
            logger.error("Failed to load MAL tokens from %s: %s", self.tokens_file, e)

    def save_tokens(self, token_data: dict[str, Any]) -> None:
        """Atomically persist token data to disk."""
        try:
            if "access_token" in token_data:
                self.access_token = token_data.get("access_token")
            if "refresh_token" in token_data:
                self.refresh_token = token_data.get("refresh_token")
            if "user_name" in token_data:
                self.user_name = token_data.get("user_name")
            if "user_avatar" in token_data:
                self.user_avatar = token_data.get("user_avatar")
            if "user_id" in token_data:
                self.user_id = token_data.get("user_id")

            payload = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "user_name": self.user_name,
                "user_avatar": self.user_avatar,
                "user_id": self.user_id,
            }
            atomic_write_json(self.tokens_file, payload)
            logger.info("Saved MyAnimeList token for user: %s", self.user_name or "unknown")
        except Exception as e:
            logger.error("Failed to save MAL tokens: %s", e)

    def delete_tokens(self) -> None:
        """Delete stored tokens from disk and memory."""
        self.access_token = None
        self.refresh_token = None
        self.user_name = None
        self.user_avatar = None
        self.user_id = None
        if self.tokens_file.exists():
            try:
                self.tokens_file.unlink()
                logger.info("Deleted MAL tokens file: %s", self.tokens_file)
            except Exception as e:
                logger.error("Failed to delete MAL tokens file: %s", e)

    def is_authenticated(self) -> bool:
        """Check if an access token is available."""
        return bool(self.access_token)

    def is_enabled(self) -> bool:
        """Check if MyAnimeList tracking is enabled in configuration."""
        try:
            from app.services.settings_manager import settings_mgr
            return settings_mgr.is_tracker_enabled("mal")
        except Exception:
            return self.config.MAL_ENABLED

    def _get_headers(self, auth: bool = True) -> dict[str, str]:
        """Construct headers for MAL API request."""
        headers = {
            "User-Agent": "Omniscrobble/1.9.0",
        }
        cid = self.effective_client_id
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        elif cid:
            headers["X-MAL-CLIENT-ID"] = cid
        return headers

    def get_client(self) -> httpx.AsyncClient:
        """Get or recreate an active httpx.AsyncClient instance."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def check_connection(self) -> dict[str, Any]:
        """Test credentials and retrieve authenticated user profile."""
        if not self.is_authenticated():
            return {
                "configured": bool(self.effective_client_id or self.access_token),
                "enabled": self.is_enabled(),
                "authenticated": False,
                "status": "not_authenticated",
                "user": None,
                "avatar": None,
            }

        url = f"{self.BASE_URL}/users/@me"
        try:
            client = self.get_client()
            resp = await client.get(url, headers=self._get_headers(auth=True))
            if resp.status_code == 401:
                return {
                    "configured": True,
                    "enabled": self.is_enabled(),
                    "authenticated": False,
                    "status": "invalid_token",
                    "error": "Unauthorized or expired MAL token",
                    "user": None,
                    "avatar": None,
                }
            if resp.status_code != 200:
                return {
                    "configured": True,
                    "enabled": self.is_enabled(),
                    "authenticated": False,
                    "status": "error",
                    "error": f"HTTP {resp.status_code}: {resp.text}",
                    "user": self.user_name,
                    "avatar": self.user_avatar,
                }

            user_data = resp.json()
            self.user_id = user_data.get("id")
            self.user_name = user_data.get("name")
            self.user_avatar = user_data.get("picture")

            self.save_tokens({
                "access_token": self.access_token,
                "user_name": self.user_name,
                "user_avatar": self.user_avatar,
                "user_id": self.user_id,
            })

            return {
                "configured": True,
                "enabled": self.is_enabled(),
                "authenticated": True,
                "status": "connected",
                "user": self.user_name,
                "avatar": self.user_avatar,
                "id": self.user_id,
            }
        except Exception as e:
            logger.error("MAL check_connection exception: %s", e)
            return {
                "configured": True,
                "enabled": self.is_enabled(),
                "authenticated": False,
                "status": "error",
                "error": str(e),
                "user": self.user_name,
                "avatar": self.user_avatar,
            }

    async def search_anime(self, title: str) -> Optional[dict[str, Any]]:
        """Search MAL for an anime by title."""
        url = f"{self.BASE_URL}/anime"
        params = {
            "q": title,
            "limit": 5,
            "fields": "id,title,main_picture,alternative_titles,num_episodes,status,media_type",
        }
        try:
            client = self.get_client()
            resp = await client.get(url, params=params, headers=self._get_headers(auth=self.is_authenticated()))
            if resp.status_code != 200:
                return None
            data = resp.json().get("data", [])
            if not data:
                return None
            node = data[0].get("node", {})
            picture = node.get("main_picture") or {}
            return {
                "id": node.get("id"),
                "title": node.get("title"),
                "episodes": node.get("num_episodes"),
                "status": node.get("status"),
                "media_type": node.get("media_type"),
                "cover_image": picture.get("large") or picture.get("medium"),
            }
        except Exception as e:
            logger.error("MAL search_anime exception: %s", e)
            return None

    async def update_progress(
        self,
        anime_id: int,
        episode: int,
        total_episodes: Optional[int] = None,
    ) -> dict[str, Any]:
        """Update episode progress and status on MyAnimeList.
        
        Args:
            anime_id: MyAnimeList Anime ID
            episode: Episode number watched
            total_episodes: Total episode count if known
        """
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}

        is_completed = total_episodes is not None and total_episodes > 0 and episode >= total_episodes
        status_val = "completed" if is_completed else "watching"

        url = f"{self.BASE_URL}/anime/{anime_id}/my_list_status"
        data = {
            "status": status_val,
            "num_watched_episodes": episode,
        }
        try:
            client = self.get_client()
            resp = await client.patch(
                url,
                data=data,
                headers=self._get_headers(auth=True),
            )
            if resp.status_code == 200:
                res_data = resp.json()
                logger.info(
                    "MAL progress updated: anime_id=%d, episode=%d, status=%s",
                    anime_id,
                    episode,
                    status_val,
                )
                return {"status": "success", "data": res_data}
            else:
                logger.error("MAL update_progress HTTP %d: %s", resp.status_code, resp.text)
                return {"status": "error", "code": resp.status_code, "error": resp.text}
        except Exception as e:
            logger.error("MAL update_progress exception: %s", e)
            return {"status": "error", "error": str(e)}

    async def update_rating(self, anime_id: int, rating: int) -> dict[str, Any]:
        """Sync user rating to MyAnimeList.
        
        Args:
            anime_id: MyAnimeList Anime ID
            rating: Score on 1-10 integer scale
        """
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}

        score_val = max(0, min(10, int(rating)))
        url = f"{self.BASE_URL}/anime/{anime_id}/my_list_status"
        data = {"score": score_val}

        try:
            client = self.get_client()
            resp = await client.patch(
                url,
                data=data,
                headers=self._get_headers(auth=True),
            )
            if resp.status_code == 200:
                res_data = resp.json()
                logger.info("MAL rating updated: anime_id=%d, score=%d", anime_id, score_val)
                return {"status": "success", "data": res_data}
            else:
                logger.error("MAL update_rating HTTP %d: %s", resp.status_code, resp.text)
                return {"status": "error", "code": resp.status_code, "error": resp.text}
        except Exception as e:
            logger.error("MAL update_rating exception: %s", e)
            return {"status": "error", "error": str(e)}

    async def delete_progress(self, anime_id: int) -> dict[str, Any]:
        """Delete anime from user's MyAnimeList list (DELETE /anime/{anime_id}/my_list_status)."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}

        url = f"{self.BASE_URL}/anime/{anime_id}/my_list_status"
        try:
            client = self.get_client()
            resp = await client.delete(
                url,
                headers=self._get_headers(auth=True),
            )
            if resp.status_code in (200, 204, 404):
                logger.info("MAL entry deleted: anime_id=%d", anime_id)
                return {"status": "success", "deleted": True}
            else:
                logger.error("MAL delete_progress HTTP %d: %s", resp.status_code, resp.text)
                return {"status": "error", "code": resp.status_code, "error": resp.text}
        except Exception as e:
            logger.error("MAL delete_progress exception: %s", e)
            return {"status": "error", "error": str(e)}

    async def close(self) -> None:
        """Close underlying HTTP client session."""
        if not self._external_client:
            await self._client.aclose()
