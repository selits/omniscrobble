import json
import logging
import time
from typing import Any, Optional
import requests
from config import Config

logger = logging.getLogger("trakt_client")


class TraktClient:
    def __init__(self, config: type[Config] = Config):
        self.config = config
        self.client_id = config.TRAKT_CLIENT_ID
        self.client_secret = config.TRAKT_CLIENT_SECRET
        self.api_url = config.TRAKT_API_URL
        self.tokens_file = config.TRAKT_TOKENS_FILE
        self._tokens: Optional[dict[str, Any]] = None

    def _get_headers(self, authenticated: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
        }
        if authenticated:
            token = self.get_valid_token()
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

    def get_valid_token(self) -> Optional[str]:
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
            refreshed = self.refresh_token()
            if refreshed:
                return refreshed.get("access_token")
            return access_token

        return access_token

    # ------------------ OAuth Device Code Flow ------------------

    def generate_device_code(self) -> dict[str, Any]:
        """Request a device code from Trakt."""
        if not self.client_id:
            raise ValueError("TRAKT_CLIENT_ID is not configured in .env")

        url = f"{self.api_url}/oauth/device/code"
        res = requests.post(url, json={"client_id": self.client_id}, timeout=10)
        if res.status_code != 200:
            raise RuntimeError(f"Failed to generate device code ({res.status_code}): {res.text}")
        return res.json()

    def poll_for_token(self, device_code: str) -> dict[str, Any]:
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
        res = requests.post(url, json=payload, timeout=10)

        if res.status_code == 200:
            tokens = res.json()
            # Trakt response includes created_at, or fallback to current time
            if "created_at" not in tokens:
                tokens["created_at"] = int(time.time())
            self.save_tokens(tokens)
            return tokens
        elif res.status_code == 400:
            # Pending user authorization
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

    def refresh_token(self) -> Optional[dict[str, Any]]:
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
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            new_tokens = res.json()
            if "created_at" not in new_tokens:
                new_tokens["created_at"] = int(time.time())
            self.save_tokens(new_tokens)
            return new_tokens
        else:
            logger.error(f"Failed to refresh Trakt token ({res.status_code}): {res.text}")
            return None

    # ------------------ Scrobbling & History Endpoints ------------------

    def scrobble_start(self, media_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /scrobble/start - Notify Trakt playback started."""
        url = f"{self.api_url}/scrobble/start"
        return self._post_authenticated(url, media_payload)

    def scrobble_pause(self, media_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /scrobble/pause - Notify Trakt playback paused."""
        url = f"{self.api_url}/scrobble/pause"
        return self._post_authenticated(url, media_payload)

    def scrobble_stop(self, media_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /scrobble/stop - Notify Trakt playback stopped.

        Marks as watched if progress >= 80%.
        """
        url = f"{self.api_url}/scrobble/stop"
        return self._post_authenticated(url, media_payload)

    def sync_history(self, sync_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/history - Directly mark episodes/movies as watched in Trakt history."""
        url = f"{self.api_url}/sync/history"
        return self._post_authenticated(url, sync_payload)

    def search_show(self, title: str, year: Optional[int] = None) -> list[dict[str, Any]]:
        """Fallback search for a show by title/year."""
        url = f"{self.api_url}/search/show"
        params: dict[str, Any] = {"query": title}
        if year:
            params["years"] = str(year)
        try:
            res = requests.get(url, headers=self._get_headers(authenticated=False), params=params, timeout=5)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Error searching show '{title}': {e}")
        return []

    def search_movie(self, title: str, year: Optional[int] = None) -> list[dict[str, Any]]:
        """Fallback search for a movie by title/year."""
        url = f"{self.api_url}/search/movie"
        params: dict[str, Any] = {"query": title}
        if year:
            params["years"] = str(year)
        try:
            res = requests.get(url, headers=self._get_headers(authenticated=False), params=params, timeout=5)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Error searching movie '{title}': {e}")
        return []

    def _post_authenticated(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        headers = self._get_headers(authenticated=True)
        res = requests.post(url, headers=headers, json=data, timeout=10)
        if res.status_code in (200, 201):
            return res.json()
        elif res.status_code == 409:
            # Trakt returns 409 if already scrobbled or conflict
            logger.info(f"Trakt 409 Conflict/Already scrobbled for {url}")
            return {"action": "conflict", "status": 409}
        else:
            logger.error(f"Trakt API error ({res.status_code}) on {url}: {res.text}")
            return {"error": res.text, "status": res.status_code}
