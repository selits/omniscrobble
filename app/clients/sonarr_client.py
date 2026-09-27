"""Client for interacting with Sonarr API and parsing Sonarr/Radarr webhooks."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional
import httpx

try:
    from app.config import Config
    from app.plex_parser import ParsedMedia
except ImportError:
    from config import Config
    from plex_parser import ParsedMedia

logger = logging.getLogger("sonarr_client")


def map_arr_resolution(quality_val: Any) -> Optional[str]:
    """Map Sonarr/Radarr quality string or dict to Trakt resolution."""
    if not quality_val:
        return None
    if isinstance(quality_val, dict):
        # Could be {"quality": {"name": "WEBDL-1080p", "resolution": 1080}}
        name = quality_val.get("quality", {}).get("name") or quality_val.get("name") or ""
        res_num = quality_val.get("quality", {}).get("resolution") or quality_val.get("resolution")
        s = f"{name} {res_num}".lower()
    else:
        s = str(quality_val).lower()

    if "2160" in s or "4k" in s or "uhd" in s:
        return "uhd_4k"
    elif "1080" in s:
        return "hd_1080p"
    elif "720" in s:
        return "hd_720p"
    elif "480" in s or "576" in s or "dvd" in s or "sdtv" in s:
        return "sd_480p"
    return None


def map_arr_media_type(quality_val: Any) -> str:
    """Map Sonarr/Radarr quality string or dict to Trakt media_type (digital, bluray, dvd)."""
    if not quality_val:
        return "digital"
    if isinstance(quality_val, dict):
        name = quality_val.get("quality", {}).get("name") or quality_val.get("name") or ""
        s = str(name).lower()
    else:
        s = str(quality_val).lower()

    if "bluray" in s or "remux" in s:
        return "bluray"
    elif "dvd" in s:
        return "dvd"
    return "digital"


class SonarrClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url or Config.SONARR_URL).rstrip("/")
        self.api_key = api_key or Config.SONARR_API_KEY
        self._cached_series: list[dict[str, Any]] = []
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 300.0  # 5 minutes in-memory cache

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def get_series(
        self, client: Optional[httpx.AsyncClient] = None, force_refresh: bool = False
    ) -> list[dict[str, Any]]:
        """Fetch all series from Sonarr, using an in-memory cache."""
        if not self.is_configured:
            return []

        now = time.time()
        if not force_refresh and self._cached_series and (now - self._cache_timestamp < self._cache_ttl):
            return self._cached_series

        headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
        }
        url = f"{self.base_url}/api/v3/series"

        try:
            if client:
                resp = await client.get(url, headers=headers, timeout=10.0)
            else:
                async with httpx.AsyncClient() as c:
                    resp = await c.get(url, headers=headers, timeout=10.0)

            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    simplified = [
                        {
                            "title": s.get("title", ""),
                            "year": s.get("year"),
                            "tvdbId": s.get("tvdbId"),
                            "imdbId": s.get("imdbId"),
                            "status": s.get("status", ""),
                            "monitored": s.get("monitored", True),
                        }
                        for s in data
                        if s.get("title")
                    ]
                    # Sort alphabetically
                    simplified.sort(key=lambda x: x["title"].lower())
                    self._cached_series = simplified
                    self._cache_timestamp = now
                    return self._cached_series
            else:
                logger.warning(f"Sonarr API returned status {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Failed to fetch series from Sonarr ({url}): {e}")

        return self._cached_series

    async def search_series(
        self, query: str = "", client: Optional[httpx.AsyncClient] = None, limit: int = 15
    ) -> list[dict[str, Any]]:
        """Search cached Sonarr series by title substring/prefix."""
        all_series = await self.get_series(client=client)
        if not query or not query.strip():
            return all_series[:limit]

        q = query.strip().lower()
        starts_with = [s for s in all_series if s["title"].lower().startswith(q)]
        contains = [
            s for s in all_series if q in s["title"].lower() and not s["title"].lower().startswith(q)
        ]
        results = starts_with + contains
        return results[:limit]

    def clear_cache(self) -> None:
        self._cached_series = []
        self._cache_timestamp = 0.0


def parse_sonarr_webhook(
    payload: dict[str, Any]
) -> tuple[str, Optional[dict[str, Any]], Optional[ParsedMedia]]:
    """Parse an incoming Sonarr webhook payload.

    Returns:
        (event_type, trakt_collection_payload, parsed_media)
    """
    event_type = payload.get("eventType", "")
    if event_type == "Test":
        return ("test", None, None)

    if event_type not in ("Download", "Upgrade"):
        return ("ignored", None, None)

    series = payload.get("series", {})
    series_title = series.get("title", "")
    series_year = series.get("year")
    tvdb_id = series.get("tvdbId")
    imdb_id = series.get("imdbId")

    episodes = payload.get("episodes", [])
    if not episodes and "episode" in payload:
        episodes = [payload["episode"]]

    if not series_title or not episodes:
        return ("invalid", None, None)

    season_num = episodes[0].get("seasonNumber", 1)
    ep_numbers = [ep.get("episodeNumber") for ep in episodes if ep.get("episodeNumber") is not None]
    if not ep_numbers:
        ep_numbers = [1]

    ep_title = episodes[0].get("title", "")

    # Quality inspection
    episode_file = payload.get("episodeFile", {})
    quality_raw = episode_file.get("quality") or payload.get("release", {}).get("quality")
    resolution = map_arr_resolution(quality_raw)
    media_type = map_arr_media_type(quality_raw)

    show_ids: dict[str, Any] = {}
    if tvdb_id:
        show_ids["tvdb"] = int(tvdb_id)
    if imdb_id:
        show_ids["imdb"] = str(imdb_id)

    # Build Trakt collection payload
    episode_entries = []
    for ep_num in ep_numbers:
        entry: dict[str, Any] = {
            "number": ep_num,
            "media_type": media_type,
        }
        if resolution:
            entry["resolution"] = resolution
        episode_entries.append(entry)

    show_obj: dict[str, Any] = {
        "title": series_title,
        "seasons": [
            {
                "number": season_num,
                "episodes": episode_entries,
            }
        ],
    }
    if series_year:
        show_obj["year"] = series_year
    if show_ids:
        show_obj["ids"] = show_ids

    trakt_payload = {"shows": [show_obj]}

    # Build ParsedMedia for notifications and logging
    parsed = ParsedMedia(
        event="sonarr.download",
        username="Sonarr",
        media_type="episode",
        title=ep_title or f"Episode {ep_numbers[0]}",
        show_title=series_title,
        show_year=series_year,
        season=season_num,
        episode=ep_numbers[0],
        video_resolution=resolution,
        ids=show_ids,
        raw_payload=payload,
    )

    return ("download", trakt_payload, parsed)


def parse_radarr_webhook(
    payload: dict[str, Any]
) -> tuple[str, Optional[dict[str, Any]], Optional[ParsedMedia]]:
    """Parse an incoming Radarr webhook payload.

    Returns:
        (event_type, trakt_collection_payload, parsed_media)
    """
    event_type = payload.get("eventType", "")
    if event_type == "Test":
        return ("test", None, None)

    if event_type not in ("Download", "Upgrade"):
        return ("ignored", None, None)

    movie = payload.get("movie", {})
    movie_title = movie.get("title", "")
    movie_year = movie.get("year")
    tmdb_id = movie.get("tmdbId")
    imdb_id = movie.get("imdbId")

    if not movie_title:
        return ("invalid", None, None)

    movie_file = payload.get("movieFile", {})
    quality_raw = movie_file.get("quality") or payload.get("release", {}).get("quality")
    resolution = map_arr_resolution(quality_raw)
    media_type = map_arr_media_type(quality_raw)

    movie_ids: dict[str, Any] = {}
    if tmdb_id:
        movie_ids["tmdb"] = int(tmdb_id)
    if imdb_id:
        movie_ids["imdb"] = str(imdb_id)

    movie_obj: dict[str, Any] = {
        "title": movie_title,
        "media_type": media_type,
    }
    if movie_year:
        movie_obj["year"] = movie_year
    if movie_ids:
        movie_obj["ids"] = movie_ids
    if resolution:
        movie_obj["resolution"] = resolution

    trakt_payload = {"movies": [movie_obj]}

    parsed = ParsedMedia(
        event="radarr.download",
        username="Radarr",
        media_type="movie",
        title=movie_title,
        year=movie_year,
        video_resolution=resolution,
        ids=movie_ids,
        raw_payload=payload,
    )

    return ("download", trakt_payload, parsed)
