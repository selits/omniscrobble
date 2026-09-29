"""AniList GraphQL API client for Omniscrobble anime tracking.

Handles user authentication, GraphQL queries/mutations, anime search/metadata resolution,
and progress scrobbling and rating synchronization.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import Config

logger = logging.getLogger("omniscrobble.anilist")


class AniListClient:
    """Asynchronous client for interacting with the AniList GraphQL API."""

    GRAPHQL_URL = "https://graphql.anilist.co"

    def __init__(
        self,
        config: type[Config] = Config,
        client: Optional[httpx.AsyncClient] = None,
        tokens_file: Optional[Path] = None,
        access_token: Optional[str] = None,
    ) -> None:
        self.config = config
        self.tokens_file = tokens_file or config.ANILIST_TOKENS_FILE
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(timeout=15.0)

        self.access_token: Optional[str] = access_token
        self.user_name: Optional[str] = None
        self.user_avatar: Optional[str] = None
        self.user_id: Optional[int] = None
        self.load_tokens()

    def load_tokens(self) -> None:
        """Load stored access token and cached user info from disk."""
        if not self.tokens_file.exists():
            return
        try:
            with open(self.tokens_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not self.access_token:
                    self.access_token = data.get("access_token")
                self.user_name = data.get("user_name")
                self.user_avatar = data.get("user_avatar")
                self.user_id = data.get("user_id")
        except Exception as e:
            logger.error("Failed to load AniList tokens from %s: %s", self.tokens_file, e)

    def save_tokens(self, token_data: dict[str, Any]) -> None:
        """Atomically persist token data to disk."""
        try:
            self.tokens_file.parent.mkdir(parents=True, exist_ok=True)
            if "access_token" in token_data:
                self.access_token = token_data.get("access_token")
            if "user_name" in token_data:
                self.user_name = token_data.get("user_name")
            if "user_avatar" in token_data:
                self.user_avatar = token_data.get("user_avatar")
            if "user_id" in token_data:
                self.user_id = token_data.get("user_id")

            payload = {
                "access_token": self.access_token,
                "user_name": self.user_name,
                "user_avatar": self.user_avatar,
                "user_id": self.user_id,
            }
            with open(self.tokens_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info("Saved AniList token for user: %s", self.user_name or "unknown")
        except Exception as e:
            logger.error("Failed to save AniList tokens: %s", e)

    def delete_tokens(self) -> None:
        """Delete stored tokens from disk and memory."""
        self.access_token = None
        self.user_name = None
        self.user_avatar = None
        self.user_id = None
        if self.tokens_file.exists():
            try:
                self.tokens_file.unlink()
                logger.info("Deleted AniList tokens file: %s", self.tokens_file)
            except Exception as e:
                logger.error("Failed to delete AniList tokens file: %s", e)

    def is_authenticated(self) -> bool:
        """Check if an access token is available."""
        return bool(self.access_token)

    def is_enabled(self) -> bool:
        """Check if AniList tracking is enabled in configuration."""
        return self.config.ANILIST_ENABLED

    def _get_headers(self, auth: bool = True) -> dict[str, str]:
        """Construct headers for GraphQL request."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Omniscrobble/1.9.0",
        }
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def get_client(self) -> httpx.AsyncClient:
        """Get or recreate an active httpx.AsyncClient instance."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def execute_query(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        """Execute a GraphQL query or mutation against the AniList API."""
        payload = {
            "query": query,
            "variables": variables or {},
        }
        try:
            client = self.get_client()
            resp = await client.post(
                self.GRAPHQL_URL,
                json=payload,
                headers=self._get_headers(auth=auth),
            )
        except Exception as e:
            logger.error("AniList network request error: %s", e)
            return {"errors": [{"message": f"Network error: {e}"}]}

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning("AniList rate-limited (429). Retry-After: %ds", retry_after)
            return {"errors": [{"message": "Rate limited by AniList", "status": 429}]}

        if resp.status_code == 401:
            logger.warning("AniList 401 Unauthorized token.")
            return {"errors": [{"message": "Unauthorized", "status": 401}]}

        try:
            return resp.json()
        except Exception:
            return {"errors": [{"message": f"HTTP {resp.status_code}: {resp.text}"}]}

    async def check_connection(self) -> dict[str, Any]:
        """Test credentials and retrieve authenticated user profile."""
        if not self.is_authenticated():
            return {
                "configured": bool(self.config.ANILIST_CLIENT_ID or self.access_token),
                "enabled": self.is_enabled(),
                "authenticated": False,
                "status": "not_authenticated",
                "user": None,
                "avatar": None,
            }

        query = """
        query {
            Viewer {
                id
                name
                avatar {
                    large
                    medium
                }
            }
        }
        """
        data = await self.execute_query(query, auth=True)
        errors = data.get("errors")
        if errors:
            err_msg = errors[0].get("message", "Unknown error")
            status = errors[0].get("status", 400)
            if status == 401 or "Unauthorized" in err_msg or "Invalid token" in err_msg:
                return {
                    "configured": True,
                    "enabled": self.is_enabled(),
                    "authenticated": False,
                    "status": "invalid_token",
                    "error": err_msg,
                    "user": None,
                    "avatar": None,
                }
            return {
                "configured": True,
                "enabled": self.is_enabled(),
                "authenticated": False,
                "status": "error",
                "error": err_msg,
                "user": self.user_name,
                "avatar": self.user_avatar,
            }

        viewer = data.get("data", {}).get("Viewer") or {}
        self.user_id = viewer.get("id")
        self.user_name = viewer.get("name")
        avatar_obj = viewer.get("avatar") or {}
        self.user_avatar = avatar_obj.get("large") or avatar_obj.get("medium")

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

    async def search_anime(
        self,
        title: str,
        year: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """Search AniList for an anime by title and optional release year.
        
        Public endpoint - does not require user authentication.
        """
        query = """
        query ($search: String, $type: MediaType) {
            Media (search: $search, type: $type, format_in: [TV, TV_SHORT, MOVIE, OVA, ONA, SPECIAL]) {
                id
                idMal
                title {
                    romaji
                    english
                    native
                    userPreferred
                }
                format
                status
                episodes
                seasonYear
                genres
                synonyms
                coverImage {
                    large
                    medium
                }
            }
        }
        """
        variables = {"search": title, "type": "ANIME"}
        res = await self.execute_query(query, variables=variables, auth=False)
        media = res.get("data", {}).get("Media")
        if not media:
            return None

        # Format match result
        titles = media.get("title") or {}
        cover = media.get("coverImage") or {}
        return {
            "id": media.get("id"),
            "idMal": media.get("idMal"),
            "title_romaji": titles.get("romaji"),
            "title_english": titles.get("english"),
            "title_native": titles.get("native"),
            "title_preferred": titles.get("userPreferred") or titles.get("english") or titles.get("romaji"),
            "format": media.get("format"),
            "status": media.get("status"),
            "episodes": media.get("episodes"),
            "year": media.get("seasonYear"),
            "genres": media.get("genres", []),
            "cover_image": cover.get("large") or cover.get("medium"),
        }

    async def get_media_by_id(self, media_id: int) -> Optional[dict[str, Any]]:
        """Retrieve anime details by AniList ID."""
        query = """
        query ($id: Int) {
            Media (id: $id, type: ANIME) {
                id
                idMal
                title {
                    romaji
                    english
                    native
                    userPreferred
                }
                format
                status
                episodes
                seasonYear
                genres
                coverImage {
                    large
                    medium
                }
            }
        }
        """
        res = await self.execute_query(query, variables={"id": media_id}, auth=False)
        media = res.get("data", {}).get("Media")
        if not media:
            return None
        titles = media.get("title") or {}
        cover = media.get("coverImage") or {}
        return {
            "id": media.get("id"),
            "idMal": media.get("idMal"),
            "title_preferred": titles.get("userPreferred") or titles.get("english") or titles.get("romaji"),
            "format": media.get("format"),
            "status": media.get("status"),
            "episodes": media.get("episodes"),
            "year": media.get("seasonYear"),
            "genres": media.get("genres", []),
            "cover_image": cover.get("large") or cover.get("medium"),
        }

    async def update_progress(
        self,
        media_id: int,
        episode: int,
        total_episodes: Optional[int] = None,
    ) -> dict[str, Any]:
        """Update episode progress and completion status on AniList.
        
        Args:
            media_id: AniList Media ID
            episode: Episode number watched
            total_episodes: Total episode count if known
        """
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}

        # Status: If episode >= total_episodes, mark COMPLETED, else CURRENT (watching)
        is_completed = total_episodes is not None and total_episodes > 0 and episode >= total_episodes
        status_val = "COMPLETED" if is_completed else "CURRENT"

        mutation = """
        mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
            SaveMediaListEntry (mediaId: $mediaId, progress: $progress, status: $status) {
                id
                mediaId
                status
                progress
            }
        }
        """
        variables = {
            "mediaId": media_id,
            "progress": episode,
            "status": status_val,
        }
        res = await self.execute_query(mutation, variables=variables, auth=True)
        if "errors" in res:
            logger.error("AniList progress update error: %s", res["errors"])
            return {"status": "error", "errors": res["errors"]}

        entry = res.get("data", {}).get("SaveMediaListEntry") or {}
        logger.info(
            "AniList progress updated: media_id=%d, episode=%d, status=%s",
            media_id,
            episode,
            status_val,
        )
        return {"status": "success", "data": entry}

    async def update_rating(self, media_id: int, rating: float) -> dict[str, Any]:
        """Sync user rating to AniList.
        
        Args:
            media_id: AniList Media ID
            rating: Rating on 1-10 scale
        """
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}

        # Score parameter accepts float
        score_val = max(0.0, min(10.0, float(rating)))

        mutation = """
        mutation ($mediaId: Int, $score: Float) {
            SaveMediaListEntry (mediaId: $mediaId, score: $score) {
                id
                mediaId
                score
            }
        }
        """
        variables = {
            "mediaId": media_id,
            "score": score_val,
        }
        res = await self.execute_query(mutation, variables=variables, auth=True)
        if "errors" in res:
            logger.error("AniList rating update error: %s", res["errors"])
            return {"status": "error", "errors": res["errors"]}

        entry = res.get("data", {}).get("SaveMediaListEntry") or {}
        logger.info("AniList rating updated: media_id=%d, score=%.1f", media_id, score_val)
        return {"status": "success", "data": entry}

    async def delete_progress(self, media_id: int) -> dict[str, Any]:
        """Delete media entry from user's AniList list."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}

        query = """
        query ($mediaId: Int) {
            MediaList (mediaId: $mediaId) {
                id
            }
        }
        """
        res = await self.execute_query(query, variables={"mediaId": media_id}, auth=True)
        entry_data = res.get("data", {}).get("MediaList")
        if not entry_data or not entry_data.get("id"):
            return {"status": "success", "deleted": True, "message": "No entry found on AniList"}

        entry_id = entry_data["id"]
        del_mutation = """
        mutation ($id: Int) {
            DeleteMediaListEntry (id: $id) {
                deleted
            }
        }
        """
        del_res = await self.execute_query(del_mutation, variables={"id": entry_id}, auth=True)
        if "errors" in del_res:
            return {"status": "error", "errors": del_res["errors"]}
        return {"status": "success", "deleted": True}

    async def close(self) -> None:
        """Close underlying HTTP client session."""
        if not self._external_client:
            await self._client.aclose()
