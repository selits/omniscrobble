import logging
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("plex_parser")


def map_plex_resolution(res: Optional[str]) -> Optional[str]:
    """Map Plex videoResolution (e.g. 4k, 1080, 720) to Trakt resolution."""
    if not res:
        return None
    r = str(res).lower()
    if r in ("4k", "uhd", "2160", "2160p"):
        return "uhd_4k"
    elif r in ("1080", "1080p", "1080i"):
        return "hd_1080p"
    elif r in ("720", "720p"):
        return "hd_720p"
    elif r in ("576", "576p", "480", "480p", "sd"):
        return "sd_480p"
    return None


def map_plex_audio(codec: Optional[str]) -> Optional[str]:
    """Map Plex audioCodec (e.g. truehd, eac3, dca) to Trakt audio format."""
    if not codec:
        return None
    c = str(codec).lower()
    if "truehd" in c or "atmos" in c:
        return "dolby_truehd"
    elif "dca-ma" in c or "dts-hd" in c:
        return "dts_ma"
    elif "dca" in c or "dts" in c:
        return "dts"
    elif "eac3" in c:
        return "dolby_digital_plus"
    elif "ac3" in c:
        return "dolby_digital"
    elif "aac" in c:
        return "aac"
    elif "flac" in c:
        return "flac"
    elif "mp3" in c:
        return "mp3"
    return None


def map_plex_audio_channels(channels: Any) -> Optional[str]:
    """Map Plex audio channels count (e.g. 8, 6, 2) to Trakt format string (e.g. 7.1, 5.1)."""
    if not channels:
        return None
    try:
        cnt = int(channels)
        if cnt >= 8:
            return "7.1"
        elif cnt >= 6:
            return "5.1"
        elif cnt == 2:
            return "2.0"
        elif cnt == 1:
            return "1.0"
    except (ValueError, TypeError):
        pass
    return None


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
    duration_ms: Optional[int] = None
    view_offset_ms: Optional[int] = None
    player: Optional[str] = None
    device: Optional[str] = None
    video_resolution: Optional[str] = None
    audio_codec: Optional[str] = None
    audio_channels: Optional[str] = None
    library_section_title: Optional[str] = None
    server_type: str = "plex"
    rating_key: Optional[str] = None
    file_path: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    ids: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        if self.duration_ms:
            return self.duration_ms / 1000.0
        return 0.0

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            ids = dict(data.get("ids") or {})
            if "imdb_id" in data and data["imdb_id"]:
                ids.setdefault("imdb", str(data["imdb_id"]))
            if "tmdb_id" in data and data["tmdb_id"]:
                ids.setdefault("tmdb", str(data["tmdb_id"]))
            if "tvdb_id" in data and data["tvdb_id"]:
                ids.setdefault("tvdb", str(data["tvdb_id"]))
            data["ids"] = ids

            if "grandparent_title" in data and not data.get("show_title"):
                data["show_title"] = data["grandparent_title"]
            if "parent_index" in data and data.get("season") is None:
                data["season"] = data["parent_index"]
            if "index" in data and data.get("episode") is None:
                data["episode"] = data["index"]
        return data

    @property
    def imdb_id(self) -> Optional[str]:
        if self.ids and self.ids.get("imdb"):
            return str(self.ids["imdb"])
        return None

    @imdb_id.setter
    def imdb_id(self, value: Optional[str]) -> None:
        if self.ids is None:
            self.ids = {}
        if value:
            self.ids["imdb"] = str(value)
        elif "imdb" in self.ids:
            del self.ids["imdb"]

    @property
    def tmdb_id(self) -> Optional[str]:
        if self.ids and self.ids.get("tmdb"):
            return str(self.ids["tmdb"])
        return None

    @tmdb_id.setter
    def tmdb_id(self, value: Any) -> None:
        if self.ids is None:
            self.ids = {}
        if value:
            self.ids["tmdb"] = str(value)
        elif "tmdb" in self.ids:
            del self.ids["tmdb"]

    @property
    def tvdb_id(self) -> Optional[str]:
        if self.ids and self.ids.get("tvdb"):
            return str(self.ids["tvdb"])
        return None

    @tvdb_id.setter
    def tvdb_id(self, value: Any) -> None:
        if self.ids is None:
            self.ids = {}
        if value:
            self.ids["tvdb"] = str(value)
        elif "tvdb" in self.ids:
            del self.ids["tvdb"]

    @property
    def grandparent_title(self) -> Optional[str]:
        return self.show_title

    @grandparent_title.setter
    def grandparent_title(self, value: Optional[str]) -> None:
        self.show_title = value

    @property
    def parent_index(self) -> Optional[int]:
        return self.season

    @parent_index.setter
    def parent_index(self, value: Optional[int]) -> None:
        self.season = value

    @property
    def index(self) -> Optional[int]:
        return self.episode

    @index.setter
    def index(self, value: Optional[int]) -> None:
        self.episode = value

    def to_trakt_collection_payload(self) -> dict[str, Any]:
        """Convert to Trakt /sync/collection payload format."""
        meta: dict[str, Any] = {"media_type": "digital"}
        if self.video_resolution:
            res_mapped = map_plex_resolution(self.video_resolution)
            if res_mapped:
                meta["resolution"] = res_mapped
        if self.audio_codec:
            audio_mapped = map_plex_audio(self.audio_codec)
            if audio_mapped:
                meta["audio"] = audio_mapped
        if self.audio_channels:
            meta["audio_channels"] = str(self.audio_channels)

        if self.media_type == "episode":
            if self.ids:
                ep_obj = {"ids": self.ids}
                ep_obj.update(meta)
                return {"episodes": [ep_obj]}

            ep_obj = {"number": self.episode if self.episode is not None else 1}
            ep_obj.update(meta)
            show_obj: dict[str, Any] = {
                "title": self.show_title or self.title,
                "seasons": [
                    {
                        "number": self.season if self.season is not None else 1,
                        "episodes": [ep_obj],
                    }
                ],
            }
            if self.show_year:
                show_obj["year"] = self.show_year
            return {"shows": [show_obj]}
        else:  # movie
            movie_obj: dict[str, Any] = {
                "title": self.title,
            }
            if self.year:
                movie_obj["year"] = self.year
            if self.ids:
                movie_obj["ids"] = self.ids
            movie_obj.update(meta)
            return {"movies": [movie_obj]}


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

    def to_trakt_rating_payload(self, rating: Optional[int] = None) -> dict[str, Any]:
        """Convert to Trakt /sync/ratings payload format."""
        rating_val = rating if rating is not None else (self.rating or 10)

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
            elif gid.startswith("anilist://"):
                try:
                    ids["anilist"] = int(gid.replace("anilist://", ""))
                except ValueError:
                    pass
            elif gid.startswith("myanimelist://"):
                try:
                    ids["mal"] = int(gid.replace("myanimelist://", ""))
                except ValueError:
                    pass
            elif gid.startswith("anidb://"):
                try:
                    ids["anidb"] = int(gid.replace("anidb://", ""))
                except ValueError:
                    pass
            elif gid.startswith("simkl://"):
                try:
                    ids["simkl"] = int(gid.replace("simkl://", ""))
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
        elif "com.plexapp.agents.hama://" in legacy_guid:
            part = legacy_guid.split("com.plexapp.agents.hama://")[1].split("?")[0]
            if part.startswith("anidb-"):
                try:
                    ids["anidb"] = int(part.replace("anidb-", ""))
                except ValueError:
                    pass
        elif "com.plexapp.agents.anidb://" in legacy_guid:
            part = legacy_guid.split("com.plexapp.agents.anidb://")[1].split("?")[0]
            try:
                ids["anidb"] = int(part)
            except ValueError:
                pass

    return ids


def parse_plex_webhook(
    payload: dict[str, Any],
    allowed_users: Optional[list[str]] = None,
    allowed_libraries: Optional[list[str]] = None,
    excluded_libraries: Optional[list[str]] = None,
) -> Optional[ParsedMedia]:
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

    # Check library section filtering if configured
    library_section_title = metadata.get("librarySectionTitle")
    if library_section_title:
        title_lower = library_section_title.strip().lower()
        if excluded_libraries and any(title_lower == ex.strip().lower() for ex in excluded_libraries):
            logger.info(f"Skipping event from excluded library section '{library_section_title}'")
            return None
        if allowed_libraries and not any(title_lower == al.strip().lower() for al in allowed_libraries):
            logger.info(f"Skipping event from library section '{library_section_title}' (not in allowed: {allowed_libraries})")
            return None

    media_type = metadata.get("type")  # "episode", "movie", or "show"
    if media_type not in ("episode", "movie", "show"):
        logger.debug(f"Ignoring unsupported media type: {media_type}")
        return None

    # Calculate progress %
    duration_raw = metadata.get("duration")
    view_offset_raw = metadata.get("viewOffset") if metadata.get("viewOffset") is not None else payload.get("viewOffset")
    duration_ms = int(duration_raw) if duration_raw is not None else None
    view_offset_ms = int(view_offset_raw) if view_offset_raw is not None else None

    duration = float(duration_ms or 0)
    view_offset = float(view_offset_ms or 0)
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

    player_obj = payload.get("Player", {})
    player_title = player_obj.get("title") if isinstance(player_obj, dict) else None
    player_device = player_obj.get("device") if isinstance(player_obj, dict) else None

    # Technical stream metadata
    media_list = metadata.get("Media", [])
    first_media = media_list[0] if (isinstance(media_list, list) and media_list and isinstance(media_list[0], dict)) else {}
    video_res = first_media.get("videoResolution")
    audio_codec = first_media.get("audioCodec")
    audio_channels_val = first_media.get("audioChannels")
    audio_channels = map_plex_audio_channels(audio_channels_val) if audio_channels_val else None

    part_list = first_media.get("Part", [])
    first_part = part_list[0] if (isinstance(part_list, list) and part_list and isinstance(part_list[0], dict)) else {}
    file_path = str(first_part.get("file", "")).strip() or None

    rating_key = str(metadata.get("ratingKey", "")) if metadata.get("ratingKey") is not None else None

    # Ambient Artwork & Poster URL resolution
    imdb_id = ids.get("imdb")
    poster_url = None
    backdrop_url = None
    thumb = metadata.get("thumb") or metadata.get("grandparentThumb")
    art = metadata.get("art") or metadata.get("grandparentArt")
    if thumb and str(thumb).startswith(("http://", "https://")):
        poster_url = str(thumb)
    elif imdb_id:
        poster_url = f"https://images.metahub.space/poster/medium/{imdb_id}/img"

    if art and str(art).startswith(("http://", "https://")):
        backdrop_url = str(art)
    elif imdb_id:
        backdrop_url = f"https://images.metahub.space/background/medium/{imdb_id}/img"

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
            duration_ms=duration_ms,
            view_offset_ms=view_offset_ms,
            player=player_title,
            device=player_device,
            video_resolution=video_res,
            audio_codec=audio_codec,
            audio_channels=audio_channels,
            library_section_title=library_section_title,
            rating_key=rating_key,
            file_path=file_path,
            poster_url=poster_url,
            backdrop_url=backdrop_url,
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
            duration_ms=duration_ms,
            view_offset_ms=view_offset_ms,
            player=player_title,
            device=player_device,
            video_resolution=video_res,
            audio_codec=audio_codec,
            audio_channels=audio_channels,
            library_section_title=library_section_title,
            rating_key=rating_key,
            file_path=file_path,
            poster_url=poster_url,
            backdrop_url=backdrop_url,
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
            duration_ms=duration_ms,
            view_offset_ms=view_offset_ms,
            player=player_title,
            device=player_device,
            video_resolution=video_res,
            audio_codec=audio_codec,
            audio_channels=audio_channels,
            library_section_title=library_section_title,
            rating_key=rating_key,
            file_path=file_path,
            poster_url=poster_url,
            backdrop_url=backdrop_url,
            ids=ids,
            raw_payload=payload,
        )

