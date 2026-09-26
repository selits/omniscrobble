import logging
from typing import Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("plex_parser")


class ParsedMedia(BaseModel):
    event: str
    username: str
    media_type: str  # "episode", "movie", or "show"
    title: str
    show_title: Optional[str] = None
    show_year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    rating: Optional[int] = None
    progress: float = 0.0
    ids: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    def to_trakt_scrobble_payload(self) -> dict[str, Any]:
        """Convert to Trakt /scrobble/{start,pause,stop} payload format."""
        if self.media_type == "episode":
            show_obj: dict[str, Any] = {
                "title": self.show_title or self.title
            }
            if self.show_year:
                show_obj["year"] = self.show_year
            episode_obj: dict[str, Any] = {
                "season": self.season if self.season is not None else 1,
                "number": self.episode if self.episode is not None else 1,
            }
            if self.ids:
                episode_obj["ids"] = self.ids
            if self.title:
                episode_obj["title"] = self.title


            return {
                "show": show_obj,
                "episode": episode_obj,
                "progress": round(self.progress, 1),
            }
        else:
            movie_obj: dict[str, Any] = {
                "title": self.title,
            }
            if self.year:
                movie_obj["year"] = self.year
            if self.ids:
                movie_obj["ids"] = self.ids

            return {
                "movie": movie_obj,
                "progress": round(self.progress, 1),
            }

    def to_trakt_history_payload(self) -> dict[str, Any]:
        """Convert to Trakt /sync/history payload format."""
        if self.media_type == "episode":
            # If we have direct episode IDs (e.g. IMDb/TMDb/TVDb for the episode)
            if self.ids:
                return {
                    "episodes": [
                        {
                            "ids": self.ids
                        }
                    ]
                }
            # Otherwise match via show structure
            show_obj: dict[str, Any] = {
                "title": self.show_title or self.title,
                "seasons": [
                    {
                        "number": self.season if self.season is not None else 1,
                        "episodes": [
                            {
                                "number": self.episode if self.episode is not None else 1
                            }
                        ]
                    }
                ]
            }
            if self.show_year:
                show_obj["year"] = self.show_year
            return {
                "shows": [show_obj]
            }
        else:
            movie_item: dict[str, Any] = {
                "title": self.title,
            }
            if self.year:
                movie_item["year"] = self.year
            if self.ids:
                movie_item["ids"] = self.ids
            return {
                "movies": [movie_item]
            }

    def to_trakt_rating_payload(self) -> dict[str, Any]:
        """Convert to Trakt /sync/ratings payload format."""
        rating_val = self.rating or 10

        if self.media_type == "episode":
            if self.ids:
                return {
                    "episodes": [
                        {
                            "rating": rating_val,
                            "ids": self.ids,
                        }
                    ]
                }
            show_obj: dict[str, Any] = {
                "title": self.show_title or self.title,
                "seasons": [
                    {
                        "number": self.season if self.season is not None else 1,
                        "episodes": [
                            {
                                "number": self.episode if self.episode is not None else 1,
                                "rating": rating_val,
                            }
                        ],
                    }
                ],
            }
            if self.show_year:
                show_obj["year"] = self.show_year
            return {"shows": [show_obj]}

        elif self.media_type == "show":
            show_obj = {
                "title": self.title,
                "rating": rating_val,
            }
            if self.year:
                show_obj["year"] = self.year
            if self.ids:
                show_obj["ids"] = self.ids
            return {"shows": [show_obj]}

        else:  # movie
            movie_item: dict[str, Any] = {
                "title": self.title,
                "rating": rating_val,
            }
            if self.year:
                movie_item["year"] = self.year
            if self.ids:
                movie_item["ids"] = self.ids
            return {
                "movies": [movie_item]
            }


def parse_plex_ids(guid_list: list[dict[str, str]], legacy_guid: str = "") -> dict[str, Any]:
    """Extract IMDb, TMDb, and TVDb IDs from Plex Metadata."""
    ids: dict[str, Any] = {}
    if isinstance(guid_list, list):
        for item in guid_list:
            gid = item.get("id", "")
            if gid.startswith("imdb://"):
                ids["imdb"] = gid.replace("imdb://", "")
            elif gid.startswith("tmdb://"):
                try:
                    ids["tmdb"] = int(gid.replace("tmdb://", ""))
                except ValueError:
                    pass
            elif gid.startswith("tvdb://"):
                try:
                    ids["tvdb"] = int(gid.replace("tvdb://", ""))
                except ValueError:
                    pass

    # Fallback to legacy guid strings if modern Guid array wasn't present
    if not ids and legacy_guid:
        if "imdb://" in legacy_guid:
            ids["imdb"] = legacy_guid.split("imdb://")[1].split("?")[0]
        elif "thetvdb://" in legacy_guid:
            part = legacy_guid.split("thetvdb://")[1].split("?")[0].split("/")[0]
            try:
                ids["tvdb"] = int(part)
            except ValueError:
                pass
        elif "themoviedb://" in legacy_guid:
            part = legacy_guid.split("themoviedb://")[1].split("?")[0]
            try:
                ids["tmdb"] = int(part)
            except ValueError:
                pass

    return ids


def parse_plex_webhook(payload: dict[str, Any], allowed_users: Optional[list[str]] = None) -> Optional[ParsedMedia]:
    """Validate and parse a raw Plex webhook JSON dictionary."""
    if not isinstance(payload, dict):
        logger.warning("Plex payload is not a valid dictionary.")
        return None

    event = payload.get("event", "")
    account = payload.get("Account", {})
    username = account.get("title", "")

    # Check user whitelist if configured
    if allowed_users and username not in allowed_users:
        logger.info(f"Skipping event for user '{username}' (not in allowed users: {allowed_users})")
        return None

    metadata = payload.get("Metadata", {})
    if not metadata:
        logger.debug("Plex webhook received with no Metadata field.")
        return None

    media_type = metadata.get("type")  # "episode", "movie", or "show"
    if media_type not in ("episode", "movie", "show"):
        logger.debug(f"Ignoring unsupported media type: {media_type}")
        return None

    # Calculate progress %
    duration = float(metadata.get("duration", 0))
    view_offset = float(metadata.get("viewOffset", 0))
    progress = 0.0
    if duration > 0:
        progress = min(100.0, max(0.0, (view_offset / duration) * 100.0))

    # If the event is media.scrobble, Plex has determined the media was fully watched
    if event == "media.scrobble" and progress < 90.0:
        progress = 100.0

    # Extract user rating if present (media.rate event or userRating/rating fields)
    raw_rating = (
        payload.get("rating")
        if payload.get("rating") is not None
        else (metadata.get("userRating") or metadata.get("rating"))
    )
    rating: Optional[int] = None
    if raw_rating is not None:
        try:
            val = float(raw_rating)
            rating = min(10, max(1, int(round(val))))
        except (ValueError, TypeError):
            rating = None

    guid_list = metadata.get("Guid", [])
    legacy_guid = metadata.get("guid", "")
    ids = parse_plex_ids(guid_list, legacy_guid)

    if media_type == "episode":
        grandparent_year = metadata.get("grandparentYear")
        show_year = int(grandparent_year) if grandparent_year else None
        return ParsedMedia(
            event=event,
            username=username,
            media_type="episode",
            title=metadata.get("title", ""),
            show_title=metadata.get("grandparentTitle") or metadata.get("parentTitle") or "",
            show_year=show_year,
            season=metadata.get("parentIndex"),
            episode=metadata.get("index"),
            year=metadata.get("year"),
            rating=rating,
            progress=progress,
            ids=ids,
            raw_payload=payload,
        )
    elif media_type == "show":
        return ParsedMedia(
            event=event,
            username=username,
            media_type="show",
            title=metadata.get("title", ""),
            year=metadata.get("year"),
            rating=rating,
            progress=progress,
            ids=ids,
            raw_payload=payload,
        )
    else:  # movie
        return ParsedMedia(
            event=event,
            username=username,
            media_type="movie",
            title=metadata.get("title", ""),
            year=metadata.get("year"),
            rating=rating,
            progress=progress,
            ids=ids,
            raw_payload=payload,
        )

