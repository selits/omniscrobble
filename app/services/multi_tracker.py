"""Multi-tracker coordination service for Omniscrobble.

Dispatches scrobble, rating, and history removal (unscrobble) events across
primary (Trakt) and secondary (Simkl, AniList, MyAnimeList) tracking platforms
with decoupled resilience, dynamic enablement toggles, and failure alerting.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.clients.anilist_client import AniListClient
from app.clients.mal_client import MyAnimeListClient
from app.clients.simkl_client import SimklClient
from app.clients.trakt_client import TraktClient
from app.config import Config
from app.plex_parser import ParsedMedia
from app.services.anime_resolver import AnimeResolver
from app.services.notifier import notifier
from app.services.settings_manager import settings_mgr

logger = logging.getLogger("omniscrobble.multi_tracker")


class MultiTrackerManager:
    """Coordinates scrobbles, ratings, and history removal across Trakt, Simkl, AniList, and MyAnimeList."""

    def __init__(
        self,
        config: type[Config] = Config,
        simkl_client: Optional[SimklClient] = None,
        anilist_client: Optional[AniListClient] = None,
        mal_client: Optional[MyAnimeListClient] = None,
        anime_resolver: Optional[AnimeResolver] = None,
    ) -> None:
        self.config = config
        self.simkl_client = simkl_client or SimklClient(config=config)
        self.anilist_client = anilist_client or AniListClient(config=config)
        self.mal_client = mal_client or MyAnimeListClient(config=config)
        self.anime_resolver = anime_resolver or AnimeResolver(
            config=config,
            anilist_client=self.anilist_client,
            mal_client=self.mal_client,
        )

    async def dispatch_scrobble(
        self,
        action: str,
        media: ParsedMedia,
        trakt_client: TraktClient,
        progress: float,
        selected_trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Dispatch playback scrobble action (start, pause, stop) to all active or selected trackers.
        
        Args:
            action: 'start' | 'pause' | 'stop' | 'scrobble'
            media: Standardized ParsedMedia instance
            trakt_client: Authenticated TraktClient for current user
            progress: Playback completion percentage (0.0 - 100.0)
            selected_trackers: Optional explicit list of trackers to target (e.g. ['trakt', 'simkl']).
                              When provided, explicitly overrides background auto-sync toggles.
            
        Returns:
            dict containing per-tracker dispatch statuses and list of active trackers.
        """
        use_explicit_targets = selected_trackers is not None
        targets = [t.lower() for t in (selected_trackers or ["trakt", "simkl", "anilist", "mal"])]

        results: dict[str, Any] = {
            "action": action,
            "media": f"{media.show_title or media.title} ({media.year or 'N/A'})",
            "progress": round(progress, 1),
            "trackers": [],
            "trakt": None,
            "simkl": None,
            "anilist": None,
            "myanimelist": None,
            "is_anime": False,
        }

        # 1. Dispatch to Trakt (Primary, if enabled in settings or explicitly requested)
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

        # 2. Dispatch to Simkl (Secondary, if enabled in settings or explicitly requested, and authenticated)
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

        # 3. Check Anime Resolution & Dispatch to Anime Trackers (AniList & MAL)
        try:
            resolved_anime = await self.anime_resolver.resolve(media)
            if resolved_anime and resolved_anime.get("is_anime"):
                results["is_anime"] = True
                results["anime_info"] = resolved_anime

                threshold = self.config.get_threshold(media.media_type)
                should_scrobble_anime = action in ("stop", "scrobble") and (progress >= threshold or action == "scrobble")

                if should_scrobble_anime:
                    # AniList Dispatch
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
                                episode=resolved_anime["episode_number"],
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
                            asyncio.create_task(
                                notifier.send_failure_alert(
                                    media, "AniList", str(e), user=media.username, is_retryable=False
                                )
                            )

                    # MyAnimeList Dispatch
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
                                episode=resolved_anime["episode_number"],
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
                            asyncio.create_task(
                                notifier.send_failure_alert(
                                    media, "MyAnimeList", str(e), user=media.username, is_retryable=False
                                )
                            )
        except Exception as e:
            logger.error("Anime tracking resolution/dispatch exception: %s", e)

        return results

    async def dispatch_manual_scrobble(
        self,
        media: ParsedMedia,
        trakt_client: TraktClient,
        selected_trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Manually mark media as watched across chosen or all active trackers."""
        use_explicit_targets = selected_trackers is not None
        targets = [t.lower() for t in (selected_trackers or ["trakt", "simkl", "anilist", "mal"])]
        results: dict[str, Any] = {
            "status": "success",
            "synced_trackers": [],
            "errors": {},
        }

        # 1. Trakt Sync History
        if "trakt" in targets and (use_explicit_targets or settings_mgr.is_tracker_enabled("trakt")) and trakt_client.is_authenticated():
            try:
                if media.media_type == "episode":
                    show_dict: dict[str, Any] = {
                        "title": media.show_title or media.title,
                        "seasons": [
                            {
                                "number": media.season if media.season is not None else 1,
                                "episodes": [
                                    {"number": media.episode if media.episode is not None else 1}
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
                    movie_dict: dict[str, Any] = {"title": media.title}
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
                simkl_res = await self.simkl_client.sync_history(media)
                if isinstance(simkl_res, dict) and simkl_res.get("status") == "error":
                    results["errors"]["simkl"] = simkl_res.get("error", "Simkl sync error")
                else:
                    results["synced_trackers"].append("simkl")
            except Exception as e:
                results["errors"]["simkl"] = str(e)
                asyncio.create_task(notifier.send_failure_alert(media, "Simkl", str(e), user=media.username))

        # 3. Anime Trackers (AniList & MAL)
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
                            episode=resolved["episode_number"],
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
                            episode=resolved["episode_number"],
                            total_episodes=resolved.get("episodes"),
                        )
                        if isinstance(mal_res, dict) and mal_res.get("status") == "error":
                            results["errors"]["mal"] = str(mal_res.get("error", "MAL error"))
                        else:
                            results["synced_trackers"].append("myanimelist")
                    except Exception as e:
                        results["errors"]["mal"] = str(e)
        except Exception as e:
            logger.warning("Anime resolution exception during manual scrobble: %s", e)

        return results

    async def dispatch_unscrobble(
        self,
        media: ParsedMedia,
        trakt_client: TraktClient,
        selected_trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Remove media from watched history across chosen or all active trackers."""
        targets = [t.lower() for t in (selected_trackers or ["trakt", "simkl", "anilist", "mal"])]
        results: dict[str, Any] = {
            "status": "success",
            "removed_trackers": [],
            "errors": {},
        }

        # 1. Trakt Remove History
        if "trakt" in targets and (use_explicit_targets or settings_mgr.is_tracker_enabled("trakt")) and trakt_client.is_authenticated():
            try:
                if media.media_type == "episode":
                    show_dict: dict[str, Any] = {
                        "title": media.show_title or media.title,
                        "seasons": [
                            {
                                "number": media.season if media.season is not None else 1,
                                "episodes": [
                                    {"number": media.episode if media.episode is not None else 1}
                                ],
                            }
                        ],
                    }
                    if media.year:
                        show_dict["year"] = media.year
                    if media.ids:
                        show_dict["ids"] = media.ids
                    remove_payload = {"shows": [show_dict]}
                else:
                    movie_dict: dict[str, Any] = {"title": media.title}
                    if media.year:
                        movie_dict["year"] = media.year
                    if media.ids:
                        movie_dict["ids"] = media.ids
                    remove_payload = {"movies": [movie_dict]}

                res = await trakt_client.remove_history(remove_payload)
                if isinstance(res, dict) and res.get("error"):
                    results["errors"]["trakt"] = res["error"]
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

        # 3. Anime Remove Progress (AniList & MAL)
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
                        ani_res = await self.anilist_client.delete_progress(resolved["anilist_id"])
                        if isinstance(ani_res, dict) and ani_res.get("status") == "error":
                            results["errors"]["anilist"] = str(ani_res.get("errors", "AniList delete error"))
                        else:
                            results["removed_trackers"].append("anilist")
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
                        mal_res = await self.mal_client.delete_progress(resolved["mal_id"])
                        if isinstance(mal_res, dict) and mal_res.get("status") == "error":
                            results["errors"]["mal"] = str(mal_res.get("error", "MAL delete error"))
                        else:
                            results["removed_trackers"].append("myanimelist")
                    except Exception as e:
                        results["errors"]["mal"] = str(e)
        except Exception as e:
            logger.warning("Anime resolution exception during unscrobble: %s", e)

        return results

    async def dispatch_rating(
        self,
        media: ParsedMedia,
        trakt_client: TraktClient,
        rating: int,
    ) -> dict[str, Any]:
        """Dispatch a user rating to all active trackers."""
        results: dict[str, Any] = {
            "action": "rate",
            "rating": rating,
            "trackers": [],
            "trakt": None,
            "simkl": None,
            "anilist": None,
            "myanimelist": None,
            "is_anime": False,
        }

        # 1. Trakt Rating
        if settings_mgr.is_tracker_enabled("trakt"):
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
            settings_mgr.is_tracker_enabled("simkl")
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

        # 3. Anime Rating Dispatch
        try:
            resolved_anime = await self.anime_resolver.resolve(media)
            if resolved_anime and resolved_anime.get("is_anime"):
                results["is_anime"] = True
                results["anime_info"] = resolved_anime

                # AniList Rating
                if (
                    settings_mgr.is_tracker_enabled("anilist")
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
                    settings_mgr.is_tracker_enabled("mal")
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
        except Exception as e:
            logger.error("Anime rating dispatch exception: %s", e)

        return results

    async def get_status(self) -> dict[str, Any]:
        """Return connectivity and authentication diagnostics for all supported trackers."""
        simkl_status = await self.simkl_client.check_connection()
        anilist_status = await self.anilist_client.check_connection()
        mal_status = await self.mal_client.check_connection()

        active = []
        if settings_mgr.is_tracker_enabled("trakt"):
            active.append("trakt")
        if (
            settings_mgr.is_tracker_enabled("simkl")
            and simkl_status.get("authenticated")
            and simkl_status.get("enabled")
        ):
            active.append("simkl")
        if (
            settings_mgr.is_tracker_enabled("anilist")
            and anilist_status.get("authenticated")
            and anilist_status.get("enabled")
        ):
            active.append("anilist")
        if (
            settings_mgr.is_tracker_enabled("mal")
            and mal_status.get("authenticated")
            and mal_status.get("enabled")
        ):
            active.append("myanimelist")

        trakt_cid = getattr(self.config, "TRAKT_CLIENT_ID", "")
        if not trakt_cid:
            try:
                trakt_cid = settings_mgr.get_tracker_credentials("trakt", mask=False).get("client_id", "")
            except Exception:
                pass

        return {
            "active_trackers": active,
            "trakt": {
                "configured": bool(trakt_cid),
                "name": "Trakt",
                "enabled": settings_mgr.is_tracker_enabled("trakt"),
            },
            "simkl": {**simkl_status, "enabled": settings_mgr.is_tracker_enabled("simkl") and simkl_status.get("enabled", True)},
            "anilist": {**anilist_status, "enabled": settings_mgr.is_tracker_enabled("anilist") and anilist_status.get("enabled", True)},
            "myanimelist": {**mal_status, "enabled": settings_mgr.is_tracker_enabled("mal") and mal_status.get("enabled", True)},
        }

    async def close(self) -> None:
        """Close underlying clients."""
        await self.simkl_client.close()
        await self.anilist_client.close()
        await self.mal_client.close()
