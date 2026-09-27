from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Any, Optional
import httpx
from config import Config

logger = logging.getLogger("trakt_client")


class TraktClient:
    def __init__(self, config: type[Config] = Config, tokens_file: Optional[Path] = None):
        self.config = config
        self.client_id = config.TRAKT_CLIENT_ID
        self.client_secret = config.TRAKT_CLIENT_SECRET
        self.api_url = config.TRAKT_API_URL
        self.tokens_file = tokens_file if tokens_file is not None else config.TRAKT_TOKENS_FILE
        self._tokens: Optional[dict[str, Any]] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    def get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=15.0)
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _get_headers(self, authenticated: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
        }
        if authenticated:
            token = await self.get_valid_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def load_tokens(self) -> Optional[dict[str, Any]]:
        if self._tokens:
            return self._tokens
        if self.tokens_file.exists():
            try:
                with open(self.tokens_file, "r", encoding="utf-8") as f:
                    self._tokens = json.load(f)
                    return self._tokens
            except Exception as e:
                logger.error(f"Error loading Trakt tokens from {self.tokens_file}: {e}")
        return None

    def save_tokens(self, tokens: dict[str, Any]) -> None:
        self._tokens = tokens
        self.tokens_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.tokens_file, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        logger.info(f"Successfully saved Trakt tokens to {self.tokens_file}")

    def is_authenticated(self) -> bool:
        tokens = self.load_tokens()
        return bool(tokens and tokens.get("access_token"))

    def get_token_info(self) -> dict[str, Any]:
        """Return token status, health, and remaining days until auto-renewal."""
        tokens = self.load_tokens()
        if not tokens or not tokens.get("access_token"):
            return {"status": "none", "healthy": False, "days_remaining": 0}

        created_at = tokens.get("created_at", 0)
        expires_in = tokens.get("expires_in", 0)
        if not created_at or not expires_in:
            return {"status": "healthy", "healthy": True, "days_remaining": 90}

        now = time.time()
        expires_at = created_at + expires_in
        seconds_remaining = expires_at - now
        days_remaining = max(0, int(seconds_remaining // 86400))

        if seconds_remaining <= 0:
            return {
                "status": "expired",
                "healthy": False,
                "days_remaining": 0,
                "created_at": created_at,
                "expires_in": expires_in,
            }

        return {
            "status": "healthy",
            "healthy": True,
            "days_remaining": days_remaining,
            "created_at": created_at,
            "expires_in": expires_in,
        }

    async def get_valid_token(self) -> Optional[str]:
        tokens = self.load_tokens()
        if not tokens:
            return None

        access_token = tokens.get("access_token")
        created_at = tokens.get("created_at", 0)
        expires_in = tokens.get("expires_in", 0)
        now = time.time()

        # If token expires within 24 hours (or is expired), refresh it
        if created_at and expires_in and (now >= created_at + expires_in - 86400):
            logger.info("Trakt access token near expiry, refreshing...")
            refreshed = await self.refresh_token()
            if refreshed:
                return refreshed.get("access_token")
            return access_token

        return access_token

    # ------------------ OAuth Device Code Flow ------------------

    async def generate_device_code(self) -> dict[str, Any]:
        """Request a device code from Trakt."""
        if not self.client_id:
            raise ValueError("TRAKT_CLIENT_ID is not configured in .env")

        url = f"{self.api_url}/oauth/device/code"
        client = self.get_client()
        res = await client.post(url, json={"client_id": self.client_id})
        if res.status_code != 200:
            raise RuntimeError(f"Failed to generate device code ({res.status_code}): {res.text}")
        return res.json()

    async def poll_for_token(self, device_code: str) -> dict[str, Any]:
        """Poll Trakt token endpoint for user approval of device code.

        Returns tokens dict when approved, or raises an exception.
        """
        if not self.client_id or not self.client_secret:
            raise ValueError("TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET are required for authentication.")

        url = f"{self.api_url}/oauth/device/token"
        payload = {
            "code": device_code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        client = self.get_client()
        res = await client.post(url, json=payload)

        if res.status_code == 200:
            tokens = res.json()
            if "created_at" not in tokens:
                tokens["created_at"] = int(time.time())
            self.save_tokens(tokens)
            return tokens
        elif res.status_code == 400:
            return {"status": "pending"}
        elif res.status_code == 404:
            raise RuntimeError("Invalid device code.")
        elif res.status_code == 409:
            raise RuntimeError("Device code has already been used.")
        elif res.status_code == 410:
            raise TimeoutError("Device code has expired. Please request a new one.")
        elif res.status_code == 418:
            raise PermissionError("User denied the authorization request on Trakt.")
        elif res.status_code == 429:
            logger.warning("Trakt rate limit encountered during polling.")
            return {"status": "slow_down"}
        else:
            raise RuntimeError(f"Unexpected status from Trakt ({res.status_code}): {res.text}")

    async def refresh_token(self) -> Optional[dict[str, Any]]:
        """Refresh single-use OAuth access/refresh tokens."""
        tokens = self.load_tokens()
        if not tokens or not tokens.get("refresh_token"):
            logger.error("No refresh token available.")
            return None

        url = f"{self.api_url}/oauth/token"
        payload = {
            "refresh_token": tokens["refresh_token"],
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        }
        client = self.get_client()
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                new_tokens = res.json()
                if "created_at" not in new_tokens:
                    new_tokens["created_at"] = int(time.time())
                self.save_tokens(new_tokens)
                return new_tokens
            else:
                logger.error(f"Failed to refresh Trakt token ({res.status_code}): {res.text}")
                return None
        except Exception as e:
            logger.error(f"Network error refreshing Trakt token: {e}")
            return None

    # ------------------ Scrobbling & History Endpoints ------------------

    async def scrobble_start(self, media_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /scrobble/start - Notify Trakt playback started."""
        url = f"{self.api_url}/scrobble/start"
        return await self._post_authenticated(url, media_payload)

    async def scrobble_pause(self, media_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /scrobble/pause - Notify Trakt playback paused."""
        url = f"{self.api_url}/scrobble/pause"
        return await self._post_authenticated(url, media_payload)

    async def scrobble_stop(self, media_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /scrobble/stop - Notify Trakt playback stopped.

        Marks as watched if progress >= 80%.
        """
        url = f"{self.api_url}/scrobble/stop"
        return await self._post_authenticated(url, media_payload)

    async def sync_history(self, sync_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/history - Directly mark episodes/movies as watched in Trakt history."""
        url = f"{self.api_url}/sync/history"
        return await self._post_authenticated(url, sync_payload)

    async def sync_ratings(self, rating_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/ratings - Rate movies, shows, or episodes on Trakt."""
        url = f"{self.api_url}/sync/ratings"
        return await self._post_authenticated(url, rating_payload)

    async def sync_collection(self, collection_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/collection - Add movies, shows, or episodes to user's Trakt collection."""
        url = f"{self.api_url}/sync/collection"
        return await self._post_authenticated(url, collection_payload)


    async def get_user_settings(self) -> Optional[dict[str, Any]]:
        """GET /users/settings - Retrieve authenticated user profile information."""
        if not self.is_authenticated():
            return None
        url = f"{self.api_url}/users/settings"
        headers = await self._get_headers(authenticated=True)
        client = self.get_client()
        try:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json()
            elif res.status_code == 401:
                refreshed = await self.refresh_token()
                if refreshed:
                    headers = await self._get_headers(authenticated=True)
                    retry_res = await client.get(url, headers=headers)
                    if retry_res.status_code == 200:
                        return retry_res.json()
        except Exception as e:
            logger.warning(f"Failed to fetch user settings from Trakt: {e}")
        return None

    async def search_show(self, title: str, year: Optional[int] = None) -> list[dict[str, Any]]:
        """Fallback search for a show by title/year."""
        url = f"{self.api_url}/search/show"
        params: dict[str, Any] = {"query": title}
        if year:
            params["years"] = str(year)
        try:
            client = self.get_client()
            headers = await self._get_headers(authenticated=False)
            res = await client.get(url, headers=headers, params=params)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Error searching show '{title}': {e}")
        return []

    async def search_movie(self, title: str, year: Optional[int] = None) -> list[dict[str, Any]]:
        """Fallback search for a movie by title/year."""
        url = f"{self.api_url}/search/movie"
        params: dict[str, Any] = {"query": title}
        if year:
            params["years"] = str(year)
        try:
            client = self.get_client()
            headers = await self._get_headers(authenticated=False)
            res = await client.get(url, headers=headers, params=params)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Error searching movie '{title}': {e}")
    async def search_media(self, query: str, media_type: Optional[str] = None) -> list[dict[str, Any]]:
        """Search Trakt catalog for movies and/or shows."""
        type_path = media_type if media_type in ("movie", "show", "episode") else "movie,show"
        url = f"{self.api_url}/search/{type_path}"
        params: dict[str, Any] = {"query": query, "limit": 10}
        try:
            client = self.get_client()
            headers = await self._get_headers(authenticated=False)
            res = await client.get(url, headers=headers, params=params)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Error searching media '{query}': {e}")
        return []

    async def _post_authenticated(
        self, url: str, data: dict[str, Any], retry_auth: bool = True
    ) -> dict[str, Any]:
        client = self.get_client()

        for attempt in range(3):
            headers = await self._get_headers(authenticated=True)
            try:
                res = await client.post(url, headers=headers, json=data)
            except httpx.RequestError as exc:
                logger.error(f"HTTP error contacting Trakt at {url}: {exc}")
                return {"error": str(exc), "status": 503}

            if res.status_code in (200, 201):
                return res.json()
            elif res.status_code == 409:
                # Trakt returns 409 if already scrobbled or conflict
                logger.info(f"Trakt 409 Conflict/Already scrobbled for {url}")
                return {"action": "conflict", "status": 409}
            elif res.status_code == 401 and retry_auth:
                logger.warning("Trakt 401 Unauthorized encountered. Attempting token refresh...")
                refreshed = await self.refresh_token()
                if refreshed:
                    # Retry once with refreshed credentials
                    return await self._post_authenticated(url, data, retry_auth=False)
                else:
                    return {"error": "Authentication failed (token refresh failed)", "status": 401}
            elif res.status_code == 429:
                retry_after_raw = res.headers.get("Retry-After", "1")
                try:
                    retry_after = int(retry_after_raw)
                except ValueError:
                    retry_after = 1
                wait_time = min(max(retry_after, 1), 5)
                logger.warning(
                    f"Trakt rate limit (429) on {url}. Waiting {wait_time}s (attempt {attempt + 1}/3)..."
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Trakt API error ({res.status_code}) on {url}: {res.text}")
                return {"error": res.text, "status": res.status_code}

        return {"error": "Trakt rate limit exceeded after retries", "status": 429}
