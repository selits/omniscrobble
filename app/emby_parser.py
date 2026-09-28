import logging
from typing import Any, Optional
from app.plex_parser import ParsedMedia
from app.jellyfin_parser import _extract_provider_ids

logger = logging.getLogger("emby_parser")


def parse_emby_webhook(
    payload: dict[str, Any],
    allowed_users: Optional[list[str]] = None,
    allowed_libraries: Optional[list[str]] = None,
    excluded_libraries: Optional[list[str]] = None,
) -> Optional[ParsedMedia]:
    """
    Parse an Emby webhook payload into a standardized ParsedMedia object.
    Supports Emby Server Webhooks and Emby Webhook Plugin JSON formats.
    """
    if not isinstance(payload, dict):
        return None

    # Resolve event
    event_raw = str(payload.get("Event") or payload.get("event") or "").strip().lower()
    if not event_raw:
        return None

    event_map = {
        "playback.start": "media.play",
        "playback.pause": "media.pause",
        "playback.unpause": "media.resume",
        "playback.stop": "media.stop",
        "playback.scrobble": "media.scrobble",
        "item.rate": "media.rate",
        "user.rating": "media.rate",
        "library.new": "library.new",
    }

    if event_raw in event_map:
        event = event_map[event_raw]
    else:
        logger.debug(f"Ignoring unhandled Emby event: {event_raw}")
        return None

    # Resolve user
    user_obj = payload.get("User", {})
    username = (
        (user_obj.get("Name") if isinstance(user_obj, dict) else None)
        or payload.get("UserName")
        or payload.get("NotificationUsername")
        or "default"
    )

    if allowed_users and username.lower() not in [u.lower() for u in allowed_users]:
        logger.info(f"Ignored Emby event: user '{username}' not in allowed users list.")
        return None

    # Resolve item
    item = payload.get("Item", {})
    if not isinstance(item, dict):
        return None

    item_type = str(item.get("Type") or "").lower()
    if item_type in ("movie",):
        media_type = "movie"
    elif item_type in ("episode",):
        media_type = "episode"
    elif item_type in ("series", "tvshow"):
        media_type = "show"
    else:
        logger.debug(f"Ignoring unsupported Emby item type: {item_type}")
        return None

    # Library filtering
    library_name = item.get("LibraryName") or ""
    if allowed_libraries and library_name:
        if library_name.lower() not in [lib.lower() for lib in allowed_libraries]:
            logger.info(f"Ignored Emby event: library '{library_name}' not in allowed libraries.")
            return None
    if excluded_libraries and library_name:
        if library_name.lower() in [lib.lower() for lib in excluded_libraries]:
            logger.info(f"Ignored Emby event: library '{library_name}' is in excluded libraries.")
            return None

    # Titles & metadata
    title = str(item.get("Name") or "").strip()
    show_title: Optional[str] = None
    show_year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None

    year_val = item.get("ProductionYear") or item.get("PremiereDate", "")[:4]
    if year_val:
        try:
            year = int(year_val)
        except (ValueError, TypeError):
            pass

    if media_type == "episode":
        show_title = str(item.get("SeriesName") or "").strip()
        season_val = item.get("ParentIndexNumber")
        episode_val = item.get("IndexNumber")
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
    provider_ids = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
    ids = _extract_provider_ids(provider_ids, payload)

    # Resolve PlaybackInfo & Ticks
    playback_info = payload.get("PlaybackInfo", {}) if isinstance(payload.get("PlaybackInfo"), dict) else {}
    pos_ticks = playback_info.get("PositionTicks")
    runtime_ticks = item.get("RunTimeTicks")

    duration_ms: Optional[int] = None
    view_offset_ms: Optional[int] = None
    progress = 0.0

    if runtime_ticks and int(runtime_ticks) > 0:
        duration_ms = int(runtime_ticks) // 10000
        view_offset_ms = int(pos_ticks or 0) // 10000
        progress = min(100.0, max(0.0, (float(pos_ticks or 0) / float(runtime_ticks)) * 100.0))

    if event == "media.scrobble":
        progress = 100.0

    # Device & Client
    device = playback_info.get("DeviceName") or payload.get("DeviceName")
    player = playback_info.get("ClientName") or payload.get("ClientName") or device

    # Rating
    rating: Optional[int] = None
    rating_raw = (
        payload.get("UserRating")
        or item.get("UserRating")
        or payload.get("Rating")
        or item.get("Rating")
        or item.get("CommunityRating")
        or item.get("CustomRating")
    )
    if rating_raw is not None:
        try:
            r = float(rating_raw)
            if r <= 5.0 and r > 0.0:
                r = r * 2.0
            rating = max(1, min(10, int(round(r))))
        except (ValueError, TypeError):
            pass
    elif event == "media.rate" and (payload.get("Event") == "item.markfavorite" or payload.get("IsFavorite")):
        rating = 10

    item_id = str(item.get("Id") or payload.get("ItemId") or payload.get("Id") or "").strip() or None

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
        server_type="emby",
        rating_key=item_id,
        ids=ids,
        raw_payload=payload,
    )
