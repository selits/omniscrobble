import logging
from typing import Any, Optional
from app.plex_parser import ParsedMedia

logger = logging.getLogger("jellyfin_parser")


def _extract_provider_ids(provider_ids: dict[str, Any], raw_payload: dict[str, Any]) -> dict[str, Any]:
    """Extract standard trakt ids (imdb, tmdb, tvdb) from Jellyfin provider IDs."""
    ids: dict[str, Any] = {}
    
    # Check ProviderIds dict (case-insensitive keys)
    if isinstance(provider_ids, dict):
        for k, v in provider_ids.items():
            if not v:
                continue
            key_lower = k.lower()
            if key_lower == "imdb":
                ids["imdb"] = str(v)
            elif key_lower == "tmdb":
                try:
                    ids["tmdb"] = int(v)
                except (ValueError, TypeError):
                    ids["tmdb"] = str(v)
            elif key_lower == "tvdb":
                try:
                    ids["tvdb"] = int(v)
                except (ValueError, TypeError):
                    ids["tvdb"] = str(v)
            elif key_lower == "anilist":
                try:
                    ids["anilist"] = int(v)
                except (ValueError, TypeError):
                    pass
            elif key_lower in ("mal", "myanimelist"):
                try:
                    ids["mal"] = int(v)
                except (ValueError, TypeError):
                    pass
            elif key_lower == "anidb":
                try:
                    ids["anidb"] = int(v)
                except (ValueError, TypeError):
                    pass
            elif key_lower == "kitsu":
                try:
                    ids["kitsu"] = int(v)
                except (ValueError, TypeError):
                    pass
            elif key_lower == "simkl":
                try:
                    ids["simkl"] = int(v)
                except (ValueError, TypeError):
                    pass

    # Also check flat keys often provided by Jellyfin webhook templates
    for flat_key, target in [
        ("Provider_imdb", "imdb"),
        ("Provider_tmdb", "tmdb"),
        ("Provider_tvdb", "tvdb"),
        ("Provider_anilist", "anilist"),
        ("Provider_mal", "mal"),
        ("Provider_anidb", "anidb"),
    ]:
        val = raw_payload.get(flat_key)
        if val and target not in ids:
            if target == "imdb":
                ids["imdb"] = str(val)
            else:
                try:
                    ids[target] = int(val)
                except (ValueError, TypeError):
                    ids[target] = str(val)

    return ids


def parse_jellyfin_webhook(
    payload: dict[str, Any],
    allowed_users: Optional[list[str]] = None,
    allowed_libraries: Optional[list[str]] = None,
    excluded_libraries: Optional[list[str]] = None,
) -> Optional[ParsedMedia]:
    """
    Parse a Jellyfin webhook payload into a standardized ParsedMedia object.
    Supports both nested Item payloads and flat webhook plugin templates.
    """
    if not isinstance(payload, dict):
        return None

    # Resolve event
    event_raw = str(payload.get("NotificationType") or payload.get("Event") or "").strip()
    if not event_raw:
        return None

    event_map = {
        "playbackstart": "media.play",
        "playbackprogress": "media.pause" if payload.get("IsPaused") else "media.play",
        "playbackstop": "media.stop",
        "itemadded": "library.new",
    }
    
    event_lower = event_raw.lower()
    if event_lower in event_map:
        event = event_map[event_lower]
    elif event_lower == "userdatasaved":
        # Check if saved due to playback finish or rating
        user_data = payload.get("UserData", {}) if isinstance(payload.get("UserData"), dict) else {}
        if payload.get("PlayedToCompletion") or payload.get("Played") or payload.get("SaveReason") == "PlaybackFinished" or user_data.get("Played"):
            event = "media.scrobble"
        elif (
            payload.get("Rating") is not None
            or payload.get("UserRating") is not None
            or user_data.get("Rating") is not None
            or user_data.get("Likes") is not None
            or user_data.get("IsFavorite") is not None
        ):
            event = "media.rate"
        else:
            event = "media.stop"
    else:
        logger.debug(f"Ignoring unhandled Jellyfin event: {event_raw}")
        return None

    # Resolve user
    user_obj = payload.get("User", {})
    username = (
        payload.get("NotificationUsername")
        or (user_obj.get("Name") if isinstance(user_obj, dict) else None)
        or payload.get("UserName")
        or "default"
    )

    if allowed_users and username.lower() not in [u.lower() for u in allowed_users]:
        logger.info(f"Ignored Jellyfin event: user '{username}' not in allowed users list.")
        return None

    # Resolve item container (either nested 'Item' or top-level)
    item = payload.get("Item") if isinstance(payload.get("Item"), dict) else payload
    item_type = str(item.get("Type") or payload.get("ItemType") or "").lower()

    if item_type in ("movie",):
        media_type = "movie"
    elif item_type in ("episode",):
        media_type = "episode"
    elif item_type in ("series", "tvshow"):
        media_type = "show"
    else:
        logger.debug(f"Ignoring unsupported Jellyfin item type: {item_type}")
        return None

    # Resolve library filtering
    library_name = item.get("LibraryName") or payload.get("LibraryName") or ""
    if allowed_libraries and library_name:
        if library_name.lower() not in [lib.lower() for lib in allowed_libraries]:
            logger.info(f"Ignored Jellyfin event: library '{library_name}' not in allowed libraries.")
            return None
    if excluded_libraries and library_name:
        if library_name.lower() in [lib.lower() for lib in excluded_libraries]:
            logger.info(f"Ignored Jellyfin event: library '{library_name}' is in excluded libraries.")
            return None

    # Resolve Titles & metadata
    title = str(item.get("Name") or payload.get("Name") or "").strip()
    show_title: Optional[str] = None
    show_year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None

    year_val = item.get("ProductionYear") or payload.get("Year")
    if year_val:
        try:
            year = int(year_val)
        except (ValueError, TypeError):
            pass

    if media_type == "episode":
        show_title = str(item.get("SeriesName") or payload.get("SeriesName") or "").strip()
        season_val = item.get("ParentIndexNumber") if item.get("ParentIndexNumber") is not None else payload.get("SeasonNumber")
        episode_val = item.get("IndexNumber") if item.get("IndexNumber") is not None else payload.get("EpisodeNumber")
        if season_val is not None:
            try:
                season = int(season_val)
            except (ValueError, TypeError):
                pass
        if episode_val is not None:
            try:
                episode = int(episode_val)
            except (ValueError, TypeError):
                pass
        if year:
            show_year = year

    # Resolve external IDs
    provider_ids = (
        item.get("ProviderIds")
        or item.get("Provider_Ids")
        or payload.get("ProviderIds")
        or payload.get("Provider_Ids")
        or {}
    )
    if not isinstance(provider_ids, dict):
        provider_ids = {}
    ids = _extract_provider_ids(provider_ids, payload)

    # Resolve Playback Progress & Duration
    # Jellyfin uses 10,000,000 ticks per second (10,000 ticks per millisecond)
    pos_ticks = payload.get("PlaybackPositionTicks") if payload.get("PlaybackPositionTicks") is not None else item.get("PlaybackPositionTicks")
    runtime_ticks = payload.get("RunTimeTicks") if payload.get("RunTimeTicks") is not None else item.get("RunTimeTicks")

    duration_ms: Optional[int] = None
    view_offset_ms: Optional[int] = None
    progress = 0.0

    if runtime_ticks and int(runtime_ticks) > 0:
        duration_ms = int(runtime_ticks) // 10000
        view_offset_ms = int(pos_ticks or 0) // 10000
        progress = min(100.0, max(0.0, (float(pos_ticks or 0) / float(runtime_ticks)) * 100.0))

    if payload.get("PlayedToCompletion") or item.get("PlayedToCompletion"):
        progress = 100.0
        if event == "media.stop":
            event = "media.scrobble"

    # Resolve Player & Device
    device = payload.get("DeviceName") or item.get("DeviceName")
    player = payload.get("ClientName") or payload.get("Client") or device

    # Resolve Rating
    user_data = payload.get("UserData", {}) if isinstance(payload.get("UserData"), dict) else {}
    rating: Optional[int] = None
    rating_raw = (
        payload.get("Rating")
        or payload.get("UserRating")
        or user_data.get("Rating")
        or item.get("Rating")
    )
    if rating_raw is not None:
        try:
            r = float(rating_raw)
            # If rating is 0-5 stars, convert to 1-10
            if r <= 5.0 and r > 0.0:
                r = r * 2.0
            rating = max(1, min(10, int(round(r))))
        except (ValueError, TypeError):
            pass
    elif user_data.get("IsFavorite") is True:
        rating = 10

    item_id = str(item.get("Id") or payload.get("ItemId") or payload.get("Id") or "").strip() or None
    file_path = str(item.get("Path") or payload.get("Path") or "").strip() or None

    # Ambient Artwork & Poster URL resolution
    imdb_id = ids.get("imdb")
    poster_url = None
    backdrop_url = None
    primary_tag = item.get("PrimaryImageTag") or payload.get("PrimaryImageTag") or item.get("ImageUrl") or payload.get("ImageUrl")
    backdrop_tag = item.get("BackdropImageTag") or payload.get("BackdropImageTag")
    if primary_tag and str(primary_tag).startswith(("http://", "https://")):
        poster_url = str(primary_tag)
    elif imdb_id:
        poster_url = f"https://images.metahub.space/poster/medium/{imdb_id}/img"

    if backdrop_tag and str(backdrop_tag).startswith(("http://", "https://")):
        backdrop_url = str(backdrop_tag)
    elif imdb_id:
        backdrop_url = f"https://images.metahub.space/background/medium/{imdb_id}/img"

    return ParsedMedia(
        event=event,
        username=username,
        media_type=media_type,
        title=title,
        show_title=show_title,
        show_year=show_year,
        season=season,
        episode=episode,
        year=year,
        rating=rating,
        progress=progress,
        duration_ms=duration_ms,
        view_offset_ms=view_offset_ms,
        player=str(player) if player else None,
        device=str(device) if device else None,
        library_section_title=library_name or None,
        server_type="jellyfin",
        rating_key=item_id,
        file_path=file_path,
        poster_url=poster_url,
        backdrop_url=backdrop_url,
        ids=ids,
        raw_payload=payload,
    )
