"""Multi-tracker coordination service for Omniscrobble.

Dispatches scrobble, rating, and history removal (unscrobble) events across
categorized tracking platforms:
- Universal: Trakt.tv, Simkl, TMDb
- Anime: AniList, MyAnimeList, Kitsu
- Social Diaries: Letterboxd, Serializd
- Lists & Ratings: MDBList

With decoupled resilience, dynamic enablement toggles, domain isolation, and failure alerting.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Optional

from app.clients.anilist_client import AniListClient
from app.clients.kitsu_client import KitsuClient
from app.clients.letterboxd_client import LetterboxdClient
from app.clients.mal_client import MyAnimeListClient
from app.clients.mdblist_client import MDBListClient
from app.clients.serializd_client import SerializdClient
from app.clients.simkl_client import SimklClient
from app.clients.tmdb_client import TMDbClient
from app.clients.trakt_client import TraktClient
from app.config import Config
from app.plex_parser import ParsedMedia
from app.services.anime_resolver import AnimeResolver
from app.services.notifier import notifier
from app.services.settings_manager import settings_mgr

logger = logging.getLogger("omniscrobble.multi_tracker")

TRACKER_REGISTRY: dict[str, dict[str, Any]] = {
    "trakt": {
        "id": "trakt",
        "name": "Trakt.tv",
        "category": "universal",
        "media_types": ["movie", "episode", "show"],
        "supports_realtime_scrobble": True,
        "supports_ratings": True,
        "supports_watchlist": True,
        "badge_color": "#ed1c24",
        "description": "Primary cloud tracker for movies and TV series with real-time playback states.",
    },
    "simkl": {
        "id": "simkl",
        "name": "Simkl",
        "category": "universal",
        "media_types": ["movie", "episode", "show"],
        "supports_realtime_scrobble": True,
        "supports_ratings": True,
        "supports_watchlist": True,
        "badge_color": "#00aaff",
        "description": "Cross-tracker hub covering movies, TV shows, and anime with real-time sync.",
    },
    "tmdb": {
        "id": "tmdb",
        "name": "TMDb",
        "category": "universal",
        "media_types": ["movie", "episode", "show"],
        "supports_realtime_scrobble": False,
        "supports_ratings": True,
        "supports_watchlist": True,
        "badge_color": "#01d277",
        "description": "The Movie Database native user watchlist, favorites, and 1-10 star ratings.",
    },
    "anilist": {
        "id": "anilist",
        "name": "AniList",
        "category": "anime",
        "media_types": ["episode", "movie", "show"],
        "supports_realtime_scrobble": False,
        "supports_ratings": True,
        "supports_watchlist": False,
        "badge_color": "#02a9ff",
        "description": "GraphQL-powered anime tracker with automated title matching and progress sync.",
    },
    "myanimelist": {
        "id": "myanimelist",
        "name": "MyAnimeList",
        "category": "anime",
        "media_types": ["episode", "movie", "show"],
        "supports_realtime_scrobble": False,
        "supports_ratings": True,
        "supports_watchlist": False,
        "badge_color": "#2e51a2",
        "description": "REST v2 anime tracking and rating sync for the premier anime community.",
    },
    "kitsu": {
        "id": "kitsu",
        "name": "Kitsu",
        "category": "anime",
        "media_types": ["episode", "movie", "show"],
        "supports_realtime_scrobble": False,
        "supports_ratings": True,
        "supports_watchlist": False,
        "badge_color": "#fd755c",
        "description": "JSON:API v1 anime library tracker completing the anime Big Three.",
    },
    "letterboxd": {
        "id": "letterboxd",
        "name": "Letterboxd",
        "category": "social_diary",
        "media_types": ["movie"],
        "supports_realtime_scrobble": False,
        "supports_ratings": True,
        "supports_watchlist": False,
        "badge_color": "#00e054",
        "description": "Social film diary with 1-click import CSV generation for completed films.",
    },
    "serializd": {
        "id": "serializd",
        "name": "Serializd",
        "category": "social_diary",
        "media_types": ["episode", "show"],
        "supports_realtime_scrobble": False,
        "supports_ratings": True,
        "supports_watchlist": False,
        "badge_color": "#ffbe1a",
        "description": "Social TV diary logging completed episodes and episode ratings.",
    },
    "mdblist": {
        "id": "mdblist",
        "name": "MDBList",
        "category": "lists_ratings",
        "media_types": ["movie", "episode", "show"],
        "supports_realtime_scrobble": False,
        "supports_ratings": True,
        "supports_watchlist": True,
        "badge_color": "#8b5cf6",
        "description": "Multi-source rating aggregator (Rotten Tomatoes, Metacritic, Letterboxd) and lists.",
    },
}


class MultiTrackerManager:
    """Coordinates scrobbles, ratings, and diary entries across categorized cloud trackers."""

    def __init__(
        self,
        config: type[Config] = Config,
        simkl_client: Optional[SimklClient] = None,
        anilist_client: Optional[AniListClient] = None,
        mal_client: Optional[MyAnimeListClient] = None,
        kitsu_client: Optional[KitsuClient] = None,
        tmdb_client: Optional[TMDbClient] = None,
        letterboxd_client: Optional[LetterboxdClient] = None,
        serializd_client: Optional[SerializdClient] = None,
        mdblist_client: Optional[MDBListClient] = None,
        anime_resolver: Optional[AnimeResolver] = None,
    ) -> None:
        self.config = config
        self.simkl_client = simkl_client or SimklClient(config=config)
        self.anilist_client = anilist_client or AniListClient(config=config)
        self.mal_client = mal_client or MyAnimeListClient(config=config)
        self.kitsu_client = kitsu_client or KitsuClient(config=config)
        self.tmdb_client = tmdb_client or TMDbClient(config=config)
        self.letterboxd_client = letterboxd_client or LetterboxdClient(config=config)
        self.serializd_client = serializd_client or SerializdClient(config=config)
        self.mdblist_client = mdblist_client or MDBListClient(config=config)
        self.anime_resolver = anime_resolver or AnimeResolver(
            config=config,
            anilist_client=self.anilist_client,
            mal_client=self.mal_client,
            kitsu_client=self.kitsu_client,
        )

    def get_registered_trackers(self) -> dict[str, dict[str, Any]]:
        """Return the capability registry of all supported trackers."""
        return dict(TRACKER_REGISTRY)

    async def dispatch_scrobble(
        self,
        action: str,
        media: ParsedMedia,
        trakt_client: TraktClient,
        progress: float,
        selected_trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Dispatch playback scrobble action (start, pause, stop, scrobble) across active trackers.
        
        Real-time actions (start, pause, stop < threshold) route only to realtime trackers (Trakt, Simkl).
        Completion actions (scrobble, stop >= threshold) route to categorized diary and list trackers.
        """
        use_explicit_targets = selected_trackers is not None
        all_target_keys = ["trakt", "simkl", "tmdb", "anilist", "mal", "kitsu", "letterboxd", "serializd", "mdblist"]
        targets = [t.lower() for t in (selected_trackers or all_target_keys)]
        if "myanimelist" in targets and "mal" not in targets:
            targets.append("mal")

        threshold = Config.get_threshold(media.media_type)
        is_completion = action == "scrobble" or (action == "stop" and progress >= threshold)

        results: dict[str, Any] = {
            "action": action,
            "media": f"{media.show_title or media.title} ({media.year or 'N/A'})",
            "progress": round(progress, 1),
            "trackers": [],
            "trakt": None,
            "simkl": None,
            "tmdb": None,
            "anilist": None,
            "myanimelist": None,
            "kitsu": None,
            "letterboxd": None,
            "serializd": None,
            "mdblist": None,
            "is_anime": False,
        }

        # -------------------------------------------------------------
        # 1. Universal Real-Time Trackers: Trakt
        # -------------------------------------------------------------
        if (
            "trakt" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("trakt"))
            and trakt_client.is_authenticated()
        ):
            results["trackers"].append("trakt")
            try:
                if action == "start":
                    trakt_res = await trakt_client.scrobble_start(media, progress=progress)
                elif action == "pause":
                    trakt_res = await trakt_client.scrobble_pause(media, progress=progress)
                else:
                    trakt_res = await trakt_client.scrobble_stop(media, progress=progress)
                results["trakt"] = trakt_res

                if isinstance(trakt_res, dict) and trakt_res.get("status") == "error":
                    err_msg = str(trakt_res.get("error", "Unknown Trakt error"))
                    asyncio.create_task(
                        notifier.send_failure_alert(
                            media, "Trakt", err_msg, user=media.username, is_retryable=False
                        )
                    )
            except Exception as e:
                logger.error("Trakt scrobble dispatch failed: %s", e)
                results["trakt"] = {"status": "error", "error": str(e)}
                asyncio.create_task(
                    notifier.send_failure_alert(
                        media, "Trakt", str(e), user=media.username, is_retryable=False
                    )
                )

        # -------------------------------------------------------------
        # 2. Universal Real-Time Trackers: Simkl
        # -------------------------------------------------------------
        if (
            "simkl" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("simkl"))
            and self.simkl_client.is_enabled()
            and self.simkl_client.is_authenticated()
        ):
            results["trackers"].append("simkl")
            try:
                if action == "start":
                    simkl_res = await self.simkl_client.scrobble_start(media, progress=progress)
                elif action == "pause":
                    simkl_res = await self.simkl_client.scrobble_pause(media, progress=progress)
                else:
                    simkl_res = await self.simkl_client.scrobble_stop(media, progress=progress)
                results["simkl"] = simkl_res

                if isinstance(simkl_res, dict) and simkl_res.get("status") == "error":
                    err_msg = str(simkl_res.get("error", "Simkl error"))
                    asyncio.create_task(
                        notifier.send_failure_alert(
                            media, "Simkl", err_msg, user=media.username, is_retryable=False
                        )
                    )
            except Exception as e:
                logger.error("Simkl scrobble dispatch failed: %s", e)
                results["simkl"] = {"status": "error", "error": str(e)}
                asyncio.create_task(
                    notifier.send_failure_alert(
                        media, "Simkl", str(e), user=media.username, is_retryable=False
                    )
                )

        # -------------------------------------------------------------
        # 3. Completion-Only & Diary Trackers
        # -------------------------------------------------------------
        if is_completion:
            # A. Letterboxd (Movies Only)
            if (
                media.media_type == "movie"
                and "letterboxd" in targets
                and (use_explicit_targets or settings_mgr.is_tracker_enabled("letterboxd"))
            ):
                results["trackers"].append("letterboxd")
                try:
                    lb_res = await self.letterboxd_client.log_movie_entry(
                        title=media.title,
                        year=media.year,
                        rating=media.rating,
                        imdb_id=media.ids.get("imdb"),
                        tmdb_id=media.ids.get("tmdb"),
                    )
                    results["letterboxd"] = lb_res
                except Exception as e:
                    logger.error("Letterboxd diary dispatch failed: %s", e)
                    results["letterboxd"] = {"status": "error", "error": str(e)}

            # B. Serializd (TV Shows & Episodes Only)
            if (
                media.media_type in ("episode", "show")
                and "serializd" in targets
                and (use_explicit_targets or settings_mgr.is_tracker_enabled("serializd"))
                and self.serializd_client.is_configured()
            ):
                results["trackers"].append("serializd")
                try:
                    ser_res = await self.serializd_client.log_episode(
                        show_title=media.show_title or media.title,
                        season=media.season or 1,
                        episode=media.episode or 1,
                        tmdb_id=media.ids.get("tmdb"),
                        rating=media.rating,
                    )
                    results["serializd"] = ser_res
                except Exception as e:
                    logger.error("Serializd dispatch failed: %s", e)
                    results["serializd"] = {"status": "error", "error": str(e)}

            # C. TMDb (Watchlist Sync on Completion)
            if (
                "tmdb" in targets
                and (use_explicit_targets or settings_mgr.is_tracker_enabled("tmdb"))
                and self.tmdb_client.is_configured()
                and media.ids.get("tmdb")
            ):
                results["trackers"].append("tmdb")
                try:
                    tmdb_res = await self.tmdb_client.sync_watchlist(
                        media_type=media.media_type,
                        tmdb_id=media.ids["tmdb"],
                        watchlist=True,
                    )
                    results["tmdb"] = tmdb_res
                except Exception as e:
                    logger.error("TMDb dispatch failed: %s", e)
                    results["tmdb"] = {"status": "error", "error": str(e)}

            # D. MDBList (Watchlist Ingestion on Completion)
            if (
                "mdblist" in targets
                and (use_explicit_targets or settings_mgr.is_tracker_enabled("mdblist"))
                and self.mdblist_client.is_configured()
                and (media.ids.get("imdb") or media.ids.get("tmdb"))
            ):
                results["trackers"].append("mdblist")
                try:
                    mdb_res = await self.mdblist_client.add_to_watchlist(
                        media_type=media.media_type,
                        imdb_id=media.ids.get("imdb"),
                        tmdb_id=media.ids.get("tmdb"),
                    )
                    results["mdblist"] = mdb_res
                except Exception as e:
                    logger.error("MDBList dispatch failed: %s", e)
                    results["mdblist"] = {"status": "error", "error": str(e)}

            # ---------------------------------------------------------
            # 4. Anime Trackers (AniList, MyAnimeList, Kitsu)
            # ---------------------------------------------------------
            try:
                resolved_anime = await self.anime_resolver.resolve(media)
                if resolved_anime and resolved_anime.get("is_anime"):
                    results["is_anime"] = True
                    results["anime_info"] = resolved_anime

                    # AniList
                    if (
                        "anilist" in targets
                        and (use_explicit_targets or settings_mgr.is_tracker_enabled("anilist"))
                        and self.anilist_client.is_enabled()
                        and self.anilist_client.is_authenticated()
                    ):
                        results["trackers"].append("anilist")
                        try:
                            ani_res = await self.anilist_client.update_progress(
                                media_id=resolved_anime["anilist_id"],
                                episode=resolved_anime.get("episode_number") or media.episode or 1,
                                total_episodes=resolved_anime.get("episodes"),
                            )
                            results["anilist"] = ani_res
                            if isinstance(ani_res, dict) and ani_res.get("status") == "error":
                                err_msg = str(ani_res.get("errors", "AniList error"))
                                asyncio.create_task(
                                    notifier.send_failure_alert(
                                        media, "AniList", err_msg, user=media.username, is_retryable=False
                                    )
                                )
                        except Exception as e:
                            logger.error("AniList progress dispatch failed: %s", e)
                            results["anilist"] = {"status": "error", "error": str(e)}

                    # MyAnimeList
                    if (
                        ("mal" in targets or "myanimelist" in targets)
                        and (use_explicit_targets or settings_mgr.is_tracker_enabled("mal"))
                        and self.mal_client.is_enabled()
                        and self.mal_client.is_authenticated()
                        and resolved_anime.get("mal_id")
                    ):
                        results["trackers"].append("myanimelist")
                        try:
                            mal_res = await self.mal_client.update_progress(
                                anime_id=resolved_anime["mal_id"],
                                episode=resolved_anime.get("episode_number") or media.episode or 1,
                                total_episodes=resolved_anime.get("episodes"),
                            )
                            results["myanimelist"] = mal_res
                            if isinstance(mal_res, dict) and mal_res.get("status") == "error":
                                err_msg = str(mal_res.get("error", "MAL error"))
                                asyncio.create_task(
                                    notifier.send_failure_alert(
                                        media, "MyAnimeList", err_msg, user=media.username, is_retryable=False
                                    )
                                )
                        except Exception as e:
                            logger.error("MAL progress dispatch failed: %s", e)
                            results["myanimelist"] = {"status": "error", "error": str(e)}

                    # Kitsu
                    if (
                        "kitsu" in targets
                        and (use_explicit_targets or settings_mgr.is_tracker_enabled("kitsu"))
                        and self.kitsu_client.is_configured()
                        and resolved_anime.get("kitsu_id")
                    ):
                        results["trackers"].append("kitsu")
                        try:
                            kit_res = await self.kitsu_client.update_progress(
                                anime_id=resolved_anime["kitsu_id"],
                                episode_number=resolved_anime.get("episode_number") or media.episode or 1,
                                total_episodes=resolved_anime.get("episodes"),
                            )
                            results["kitsu"] = kit_res
                        except Exception as e:
                            logger.error("Kitsu progress dispatch failed: %s", e)
                            results["kitsu"] = {"status": "error", "error": str(e)}
            except Exception as e:
                logger.error("Anime scrobble resolution failed: %s", e)

        return results

    async def dispatch_manual_scrobble(
        self,
        media: ParsedMedia,
        trakt_client: TraktClient,
        selected_trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Manually mark media as watched across chosen or all active cloud trackers."""
        use_explicit_targets = selected_trackers is not None
        all_target_keys = ["trakt", "simkl", "tmdb", "anilist", "mal", "kitsu", "letterboxd", "serializd", "mdblist"]
        targets = [t.lower() for t in (selected_trackers or all_target_keys)]
        if "myanimelist" in targets and "mal" not in targets:
            targets.append("mal")

        results: dict[str, Any] = {
            "status": "success",
            "synced_trackers": [],
            "errors": {},
        }

        watched_at_ts = datetime.now(timezone.utc).isoformat()

        # 1. Trakt Sync History
        if (
            "trakt" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("trakt"))
            and trakt_client.is_authenticated()
        ):
            try:
                if media.media_type == "episode":
                    show_dict: dict[str, Any] = {
                        "title": media.show_title or media.title,
                        "seasons": [
                            {
                                "number": media.season if media.season is not None else 1,
                                "episodes": [
                                    {
                                        "number": media.episode if media.episode is not None else 1,
                                        "watched_at": watched_at_ts,
                                    }
                                ],
                            }
                        ],
                    }
                    if media.year:
                        show_dict["year"] = media.year
                    if media.ids:
                        show_dict["ids"] = media.ids
                    history_payload = {"shows": [show_dict]}
                else:
                    movie_dict: dict[str, Any] = {"title": media.title, "watched_at": watched_at_ts}
                    if media.year:
                        movie_dict["year"] = media.year
                    if media.ids:
                        movie_dict["ids"] = media.ids
                    history_payload = {"movies": [movie_dict]}

                trakt_res = await trakt_client.sync_history(history_payload)
                if isinstance(trakt_res, dict) and trakt_res.get("error"):
                    results["errors"]["trakt"] = trakt_res["error"]
                else:
                    results["synced_trackers"].append("trakt")
            except Exception as e:
                results["errors"]["trakt"] = str(e)
                asyncio.create_task(notifier.send_failure_alert(media, "Trakt", str(e), user=media.username))

        # 2. Simkl Sync History
        if (
            "simkl" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("simkl"))
            and self.simkl_client.is_enabled()
            and self.simkl_client.is_authenticated()
        ):
            try:
                simkl_res = await self.simkl_client.sync_history(media, watched_at=watched_at_ts)
                if isinstance(simkl_res, dict) and simkl_res.get("status") == "error":
                    results["errors"]["simkl"] = simkl_res.get("error", "Simkl sync error")
                else:
                    results["synced_trackers"].append("simkl")
            except Exception as e:
                results["errors"]["simkl"] = str(e)
                asyncio.create_task(notifier.send_failure_alert(media, "Simkl", str(e), user=media.username))

        # 3. TMDb (Watchlist / History)
        if (
            "tmdb" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("tmdb"))
            and self.tmdb_client.is_configured()
            and media.ids.get("tmdb")
        ):
            try:
                tmdb_res = await self.tmdb_client.sync_watchlist(
                    media_type=media.media_type,
                    tmdb_id=media.ids["tmdb"],
                    watchlist=True,
                )
                if isinstance(tmdb_res, dict) and tmdb_res.get("status") == "error":
                    results["errors"]["tmdb"] = tmdb_res.get("error", "TMDb sync error")
                else:
                    results["synced_trackers"].append("tmdb")
            except Exception as e:
                results["errors"]["tmdb"] = str(e)

        # 4. Letterboxd Diary (Movies Only)
        if (
            media.media_type == "movie"
            and "letterboxd" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("letterboxd"))
        ):
            try:
                lb_res = await self.letterboxd_client.log_movie_entry(
                    title=media.title,
                    year=media.year,
                    rating=media.rating,
                    imdb_id=media.ids.get("imdb"),
                    tmdb_id=media.ids.get("tmdb"),
                )
                if isinstance(lb_res, dict) and lb_res.get("status") == "error":
                    results["errors"]["letterboxd"] = lb_res.get("error", "Letterboxd error")
                else:
                    results["synced_trackers"].append("letterboxd")
            except Exception as e:
                results["errors"]["letterboxd"] = str(e)

        # 5. Serializd (TV Shows & Episodes Only)
        if (
            media.media_type in ("episode", "show")
            and "serializd" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("serializd"))
            and self.serializd_client.is_configured()
        ):
            try:
                ser_res = await self.serializd_client.log_episode(
                    show_title=media.show_title or media.title,
                    season=media.season or 1,
                    episode=media.episode or 1,
                    tmdb_id=media.ids.get("tmdb"),
                    rating=media.rating,
                )
                if isinstance(ser_res, dict) and ser_res.get("status") == "error":
                    results["errors"]["serializd"] = ser_res.get("error", "Serializd error")
                else:
                    results["synced_trackers"].append("serializd")
            except Exception as e:
                results["errors"]["serializd"] = str(e)

        # 6. MDBList (Watchlist Ingestion)
        if (
            "mdblist" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("mdblist"))
            and self.mdblist_client.is_configured()
            and (media.ids.get("imdb") or media.ids.get("tmdb"))
        ):
            try:
                mdb_res = await self.mdblist_client.add_to_watchlist(
                    media_type=media.media_type,
                    imdb_id=media.ids.get("imdb"),
                    tmdb_id=media.ids.get("tmdb"),
                )
                if isinstance(mdb_res, dict) and mdb_res.get("status") == "error":
                    results["errors"]["mdblist"] = mdb_res.get("error", "MDBList error")
                else:
                    results["synced_trackers"].append("mdblist")
            except Exception as e:
                results["errors"]["mdblist"] = str(e)

        # 7. Anime Trackers (AniList, MyAnimeList, Kitsu)
        try:
            resolved = await self.anime_resolver.resolve(media)
            if resolved and resolved.get("is_anime"):
                # AniList
                if (
                    "anilist" in targets
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("anilist"))
                    and self.anilist_client.is_enabled()
                    and self.anilist_client.is_authenticated()
                ):
                    try:
                        ani_res = await self.anilist_client.update_progress(
                            media_id=resolved["anilist_id"],
                            episode=resolved.get("episode_number") or media.episode or 1,
                            total_episodes=resolved.get("episodes"),
                        )
                        if isinstance(ani_res, dict) and ani_res.get("status") == "error":
                            results["errors"]["anilist"] = str(ani_res.get("errors", "AniList error"))
                        else:
                            results["synced_trackers"].append("anilist")
                    except Exception as e:
                        results["errors"]["anilist"] = str(e)

                # MyAnimeList
                if (
                    ("mal" in targets or "myanimelist" in targets)
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("mal"))
                    and self.mal_client.is_enabled()
                    and self.mal_client.is_authenticated()
                    and resolved.get("mal_id")
                ):
                    try:
                        mal_res = await self.mal_client.update_progress(
                            anime_id=resolved["mal_id"],
                            episode=resolved.get("episode_number") or media.episode or 1,
                            total_episodes=resolved.get("episodes"),
                        )
                        if isinstance(mal_res, dict) and mal_res.get("status") == "error":
                            results["errors"]["myanimelist"] = str(mal_res.get("error", "MAL error"))
                        else:
                            results["synced_trackers"].append("myanimelist")
                    except Exception as e:
                        results["errors"]["myanimelist"] = str(e)

                # Kitsu
                if (
                    "kitsu" in targets
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("kitsu"))
                    and self.kitsu_client.is_configured()
                    and resolved.get("kitsu_id")
                ):
                    try:
                        kit_res = await self.kitsu_client.update_progress(
                            anime_id=resolved["kitsu_id"],
                            episode_number=resolved.get("episode_number") or media.episode or 1,
                            total_episodes=resolved.get("episodes"),
                        )
                        if isinstance(kit_res, dict) and kit_res.get("status") == "error":
                            results["errors"]["kitsu"] = str(kit_res.get("error", "Kitsu error"))
                        else:
                            results["synced_trackers"].append("kitsu")
                    except Exception as e:
                        results["errors"]["kitsu"] = str(e)
        except Exception as e:
            logger.warning("Anime resolution exception during manual scrobble: %s", e)

        if not results["synced_trackers"] and results["errors"]:
            results["status"] = "error"

        return results

    async def dispatch_rating(
        self,
        media: ParsedMedia,
        trakt_client: TraktClient,
        rating: int,
        selected_trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Dispatch a user rating across all active and supported cloud trackers."""
        use_explicit_targets = selected_trackers is not None
        all_target_keys = ["trakt", "simkl", "tmdb", "anilist", "mal", "kitsu", "letterboxd", "serializd", "mdblist"]
        targets = [t.lower() for t in (selected_trackers or all_target_keys)]
        if "myanimelist" in targets and "mal" not in targets:
            targets.append("mal")

        results: dict[str, Any] = {
            "action": "rate",
            "rating": rating,
            "trackers": [],
            "trakt": None,
            "simkl": None,
            "tmdb": None,
            "anilist": None,
            "myanimelist": None,
            "kitsu": None,
            "letterboxd": None,
            "serializd": None,
            "is_anime": False,
        }

        # 1. Trakt Rating
        if (
            "trakt" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("trakt"))
            and trakt_client.is_authenticated()
        ):
            results["trackers"].append("trakt")
            try:
                rating_payload = media.to_trakt_rating_payload(rating)
                trakt_res = await trakt_client.sync_ratings(rating_payload)
                results["trakt"] = trakt_res
            except Exception as e:
                logger.error("Trakt rating dispatch failed: %s", e)
                results["trakt"] = {"status": "error", "error": str(e)}

        # 2. Simkl Rating
        if (
            "simkl" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("simkl"))
            and self.simkl_client.is_enabled()
            and self.simkl_client.is_authenticated()
        ):
            results["trackers"].append("simkl")
            try:
                simkl_res = await self.simkl_client.sync_ratings(media, rating=rating)
                results["simkl"] = simkl_res
            except Exception as e:
                logger.error("Simkl rating dispatch failed: %s", e)
                results["simkl"] = {"status": "error", "error": str(e)}

        # 3. TMDb Rating
        if (
            "tmdb" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("tmdb"))
            and self.tmdb_client.is_configured()
            and media.ids.get("tmdb")
        ):
            results["trackers"].append("tmdb")
            try:
                tmdb_res = await self.tmdb_client.sync_rating(
                    media_type=media.media_type,
                    tmdb_id=media.ids["tmdb"],
                    rating=rating,
                )
                results["tmdb"] = tmdb_res
            except Exception as e:
                logger.error("TMDb rating dispatch failed: %s", e)
                results["tmdb"] = {"status": "error", "error": str(e)}

        # 4. Letterboxd Rating (Movies Only)
        if (
            media.media_type == "movie"
            and "letterboxd" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("letterboxd"))
        ):
            results["trackers"].append("letterboxd")
            try:
                lb_res = await self.letterboxd_client.log_movie_entry(
                    title=media.title,
                    year=media.year,
                    rating=rating,
                    imdb_id=media.ids.get("imdb"),
                    tmdb_id=media.ids.get("tmdb"),
                )
                results["letterboxd"] = lb_res
            except Exception as e:
                logger.error("Letterboxd rating dispatch failed: %s", e)
                results["letterboxd"] = {"status": "error", "error": str(e)}

        # 5. Serializd Rating (Shows & Episodes Only)
        if (
            media.media_type in ("episode", "show")
            and "serializd" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("serializd"))
            and self.serializd_client.is_configured()
        ):
            results["trackers"].append("serializd")
            try:
                ser_res = await self.serializd_client.sync_rating(
                    show_title=media.show_title or media.title,
                    rating=rating,
                    season=media.season,
                    episode=media.episode,
                    tmdb_id=media.ids.get("tmdb"),
                )
                results["serializd"] = ser_res
            except Exception as e:
                logger.error("Serializd rating dispatch failed: %s", e)
                results["serializd"] = {"status": "error", "error": str(e)}

        # 6. Anime Rating Dispatch (AniList, MyAnimeList, Kitsu)
        try:
            resolved_anime = await self.anime_resolver.resolve(media)
            if resolved_anime and resolved_anime.get("is_anime"):
                results["is_anime"] = True
                results["anime_info"] = resolved_anime

                # AniList Rating
                if (
                    "anilist" in targets
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("anilist"))
                    and self.anilist_client.is_enabled()
                    and self.anilist_client.is_authenticated()
                ):
                    results["trackers"].append("anilist")
                    try:
                        ani_res = await self.anilist_client.update_rating(
                            media_id=resolved_anime["anilist_id"],
                            rating=float(rating),
                        )
                        results["anilist"] = ani_res
                    except Exception as e:
                        logger.error("AniList rating dispatch failed: %s", e)
                        results["anilist"] = {"status": "error", "error": str(e)}

                # MyAnimeList Rating
                if (
                    ("mal" in targets or "myanimelist" in targets)
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("mal"))
                    and self.mal_client.is_enabled()
                    and self.mal_client.is_authenticated()
                    and resolved_anime.get("mal_id")
                ):
                    results["trackers"].append("myanimelist")
                    try:
                        mal_res = await self.mal_client.update_rating(
                            anime_id=resolved_anime["mal_id"],
                            rating=rating,
                        )
                        results["myanimelist"] = mal_res
                    except Exception as e:
                        logger.error("MAL rating dispatch failed: %s", e)
                        results["myanimelist"] = {"status": "error", "error": str(e)}

                # Kitsu Rating
                if (
                    "kitsu" in targets
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("kitsu"))
                    and self.kitsu_client.is_configured()
                    and resolved_anime.get("kitsu_id")
                ):
                    results["trackers"].append("kitsu")
                    try:
                        kit_res = await self.kitsu_client.update_rating(
                            anime_id=resolved_anime["kitsu_id"],
                            rating=rating,
                        )
                        results["kitsu"] = kit_res
                    except Exception as e:
                        logger.error("Kitsu rating dispatch failed: %s", e)
                        results["kitsu"] = {"status": "error", "error": str(e)}
        except Exception as e:
            logger.error("Anime rating dispatch exception: %s", e)

        return results

    async def dispatch_unscrobble(
        self,
        media: ParsedMedia,
        trakt_client: TraktClient,
        selected_trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Dispatch history removal across Trakt, Simkl, and anime trackers."""
        use_explicit_targets = selected_trackers is not None
        targets = [t.lower() for t in (selected_trackers or ["trakt", "simkl", "anilist", "mal", "kitsu"])]

        results: dict[str, Any] = {
            "action": "unscrobble",
            "media": f"{media.show_title or media.title} ({media.year or 'N/A'})",
            "removed_trackers": [],
            "errors": {},
        }

        # 1. Trakt Remove History
        if (
            "trakt" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("trakt"))
            and trakt_client.is_authenticated()
        ):
            try:
                trakt_payload = media.to_trakt_history_payload()
                res = await trakt_client.remove_history(trakt_payload)
                if isinstance(res, dict) and res.get("status") == "error":
                    results["errors"]["trakt"] = res.get("error", "Trakt remove error")
                else:
                    results["removed_trackers"].append("trakt")
            except Exception as e:
                results["errors"]["trakt"] = str(e)

        # 2. Simkl Remove History
        if (
            "simkl" in targets
            and (use_explicit_targets or settings_mgr.is_tracker_enabled("simkl"))
            and self.simkl_client.is_enabled()
            and self.simkl_client.is_authenticated()
        ):
            try:
                simkl_payload: dict[str, Any] = {}
                if media.media_type == "movie":
                    m_item: dict[str, Any] = {"title": media.title}
                    if media.year:
                        m_item["year"] = media.year
                    if media.ids:
                        m_item["ids"] = media.ids
                    simkl_payload["movies"] = [m_item]
                else:
                    s_item: dict[str, Any] = {
                        "title": media.show_title or media.title,
                        "seasons": [
                            {
                                "number": media.season if media.season is not None else 1,
                                "episodes": [{"number": media.episode if media.episode is not None else 1}],
                            }
                        ],
                    }
                    if media.year:
                        s_item["year"] = media.year
                    if media.ids:
                        s_item["ids"] = media.ids
                    simkl_payload["shows"] = [s_item]

                sim_res = await self.simkl_client.remove_history(simkl_payload)
                if isinstance(sim_res, dict) and sim_res.get("status") == "error":
                    results["errors"]["simkl"] = sim_res.get("error", "Simkl remove error")
                else:
                    results["removed_trackers"].append("simkl")
            except Exception as e:
                results["errors"]["simkl"] = str(e)

        # 3. Anime Remove Progress (AniList, MAL, Kitsu)
        try:
            resolved = await self.anime_resolver.resolve(media)
            if resolved and resolved.get("is_anime"):
                if (
                    "anilist" in targets
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("anilist"))
                    and self.anilist_client.is_enabled()
                    and self.anilist_client.is_authenticated()
                ):
                    try:
                        ani_res = await self.anilist_client.delete_progress(resolved["anilist_id"])
                        if isinstance(ani_res, dict) and ani_res.get("status") == "error":
                            results["errors"]["anilist"] = str(ani_res.get("errors", "AniList delete error"))
                        else:
                            results["removed_trackers"].append("anilist")
                    except Exception as e:
                        results["errors"]["anilist"] = str(e)

                if (
                    ("mal" in targets or "myanimelist" in targets)
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("mal"))
                    and self.mal_client.is_enabled()
                    and self.mal_client.is_authenticated()
                    and resolved.get("mal_id")
                ):
                    try:
                        mal_res = await self.mal_client.delete_progress(resolved["mal_id"])
                        if isinstance(mal_res, dict) and mal_res.get("status") == "error":
                            results["errors"]["mal"] = str(mal_res.get("error", "MAL delete error"))
                        else:
                            results["removed_trackers"].append("myanimelist")
                    except Exception as e:
                        results["errors"]["mal"] = str(e)

                if (
                    "kitsu" in targets
                    and (use_explicit_targets or settings_mgr.is_tracker_enabled("kitsu"))
                    and self.kitsu_client.is_configured()
                    and resolved.get("kitsu_id")
                ):
                    try:
                        kit_res = await self.kitsu_client.delete_progress(resolved["kitsu_id"])
                        if isinstance(kit_res, dict) and kit_res.get("status") == "error":
                            results["errors"]["kitsu"] = str(kit_res.get("error", "Kitsu delete error"))
                        else:
                            results["removed_trackers"].append("kitsu")
                    except Exception as e:
                        results["errors"]["kitsu"] = str(e)
        except Exception as e:
            logger.warning("Anime resolution exception during unscrobble: %s", e)

        return results

    async def get_status(self) -> dict[str, Any]:
        """Return connectivity, authentication, and categorization diagnostics for all trackers."""
        simkl_status = await self.simkl_client.check_connection()
        anilist_status = await self.anilist_client.check_connection()
        mal_status = await self.mal_client.check_connection()
        kitsu_status = await self.kitsu_client.check_connection()
        tmdb_status = await self.tmdb_client.check_connection()
        lb_status = await self.letterboxd_client.check_connection()
        ser_status = await self.serializd_client.check_connection()
        mdb_status = await self.mdblist_client.check_connection()

        active = []
        if settings_mgr.is_tracker_enabled("trakt"):
            active.append("trakt")
        if settings_mgr.is_tracker_enabled("simkl") and simkl_status.get("authenticated") and simkl_status.get("enabled"):
            active.append("simkl")
        if settings_mgr.is_tracker_enabled("tmdb") and tmdb_status.get("configured") and tmdb_status.get("enabled"):
            active.append("tmdb")
        if settings_mgr.is_tracker_enabled("anilist") and anilist_status.get("authenticated") and anilist_status.get("enabled"):
            active.append("anilist")
        if settings_mgr.is_tracker_enabled("mal") and mal_status.get("authenticated") and mal_status.get("enabled"):
            active.append("myanimelist")
        if settings_mgr.is_tracker_enabled("kitsu") and kitsu_status.get("authenticated") and kitsu_status.get("enabled"):
            active.append("kitsu")
        if settings_mgr.is_tracker_enabled("letterboxd") and lb_status.get("configured"):
            active.append("letterboxd")
        if settings_mgr.is_tracker_enabled("serializd") and ser_status.get("authenticated") and ser_status.get("enabled"):
            active.append("serializd")
        if settings_mgr.is_tracker_enabled("mdblist") and mdb_status.get("configured") and mdb_status.get("enabled"):
            active.append("mdblist")

        trakt_cid = getattr(self.config, "TRAKT_CLIENT_ID", "")
        if not trakt_cid:
            try:
                trakt_cid = settings_mgr.get_tracker_credentials("trakt", mask=False).get("client_id", "")
            except Exception:
                pass

        trackers_dict = {
            "trakt": {
                **TRACKER_REGISTRY["trakt"],
                "configured": bool(trakt_cid),
                "authenticated": bool(trakt_cid),
                "enabled": settings_mgr.is_tracker_enabled("trakt"),
            },
            "simkl": {
                **TRACKER_REGISTRY["simkl"],
                **simkl_status,
                "enabled": settings_mgr.is_tracker_enabled("simkl") and simkl_status.get("enabled", True),
            },
            "tmdb": {
                **TRACKER_REGISTRY["tmdb"],
                **tmdb_status,
                "enabled": settings_mgr.is_tracker_enabled("tmdb"),
            },
            "anilist": {
                **TRACKER_REGISTRY["anilist"],
                **anilist_status,
                "enabled": settings_mgr.is_tracker_enabled("anilist") and anilist_status.get("enabled", True),
            },
            "myanimelist": {
                **TRACKER_REGISTRY["myanimelist"],
                **mal_status,
                "enabled": settings_mgr.is_tracker_enabled("mal") and mal_status.get("enabled", True),
            },
            "kitsu": {
                **TRACKER_REGISTRY["kitsu"],
                **kitsu_status,
                "enabled": settings_mgr.is_tracker_enabled("kitsu"),
            },
            "letterboxd": {
                **TRACKER_REGISTRY["letterboxd"],
                **lb_status,
                "enabled": settings_mgr.is_tracker_enabled("letterboxd"),
            },
            "serializd": {
                **TRACKER_REGISTRY["serializd"],
                **ser_status,
                "enabled": settings_mgr.is_tracker_enabled("serializd"),
            },
            "mdblist": {
                **TRACKER_REGISTRY["mdblist"],
                **mdb_status,
                "enabled": settings_mgr.is_tracker_enabled("mdblist"),
            },
        }

        categories = {
            "universal": ["trakt", "simkl", "tmdb"],
            "anime": ["anilist", "myanimelist", "kitsu"],
            "social_diary": ["letterboxd", "serializd"],
            "lists_ratings": ["mdblist"],
        }

        return {
            "categories": categories,
            "active_trackers": active,
            "trackers": trackers_dict,
            # Backwards compatibility top-level tracker keys
            "trakt": trackers_dict["trakt"],
            "simkl": trackers_dict["simkl"],
            "tmdb": trackers_dict["tmdb"],
            "anilist": trackers_dict["anilist"],
            "myanimelist": trackers_dict["myanimelist"],
            "kitsu": trackers_dict["kitsu"],
            "letterboxd": trackers_dict["letterboxd"],
            "serializd": trackers_dict["serializd"],
            "mdblist": trackers_dict["mdblist"],
        }

    async def close(self) -> None:
        """Close underlying client sessions."""
        await self.simkl_client.close()
        await self.anilist_client.close()
        await self.mal_client.close()
        await self.kitsu_client.close()
        await self.tmdb_client.close()
        await self.letterboxd_client.close()
        await self.serializd_client.close()
        await self.mdblist_client.close()
