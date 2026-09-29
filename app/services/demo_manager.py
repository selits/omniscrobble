import datetime
from typing import Any


class DemoManager:
    """Provides realistic simulated data for Demo Mode previews."""

    DEMO_SHOWS = [
        "Fallout",
        "Fargo",
        "House of the Dragon",
        "Severance",
        "Shōgun",
        "Silo",
        "Slow Horses",
        "Succession",
        "Ted Lasso",
        "The Bear",
        "The Last of Us",
        "The White Lotus",
    ]

    SONARR_CATALOG = [
        {"title": "Severance", "year": 2022, "status": "continuing"},
        {"title": "The Bear", "year": 2022, "status": "continuing"},
        {"title": "Succession", "year": 2018, "status": "ended"},
        {"title": "Shōgun", "year": 2024, "status": "continuing"},
        {"title": "Slow Horses", "year": 2022, "status": "continuing"},
        {"title": "Fallout", "year": 2024, "status": "continuing"},
        {"title": "Fargo", "year": 2014, "status": "continuing"},
        {"title": "The Last of Us", "year": 2023, "status": "continuing"},
        {"title": "The White Lotus", "year": 2021, "status": "continuing"},
        {"title": "Ted Lasso", "year": 2020, "status": "ended"},
        {"title": "Silo", "year": 2023, "status": "continuing"},
        {"title": "House of the Dragon", "year": 2022, "status": "continuing"},
        {"title": "Yellowstone", "year": 2018, "status": "continuing"},
        {"title": "True Detective", "year": 2014, "status": "continuing"},
        {"title": "Better Call Saul", "year": 2015, "status": "ended"},
    ]

    def get_demo_playback(self) -> dict[str, Any]:
        return {
            "state": "playing",
            "title": "Severance S02E01 - Hello, Kier",
            "username": "demo_viewer",
            "player": "Living Room Apple TV",
            "device": "tvOS 18.2",
            "progress": 68.4,
            "trakt_url": "https://trakt.tv/search?q=Severance",
        }

    def get_demo_stats(self) -> dict[str, int]:
        return {
            "total": 1428,
            "movies": 312,
            "episodes": 1064,
            "ratings": 52,
            "collections": 148,
        }

    def get_demo_cowatch_shows(self) -> list[str]:
        return list(self.DEMO_SHOWS)

    def get_demo_users(self) -> list[dict[str, Any]]:
        return [
            {
                "username": "demo_viewer",
                "is_default": True,
                "is_cowatch_target": False,
                "authenticated": True,
            },
            {
                "username": "demo_partner",
                "is_default": False,
                "is_cowatch_target": True,
                "authenticated": True,
            },
        ]

    def get_demo_events(self) -> list[dict[str, Any]]:
        now = datetime.datetime.now()
        fmt = "%Y-%m-%d %H:%M:%S"

        def _time_ago(minutes: int) -> str:
            return (now - datetime.timedelta(minutes=minutes)).strftime(fmt)

        return [
            {
                "timestamp": _time_ago(4),
                "user": "demo_viewer",
                "event": "media.play",
                "action": "play (68.4%)",
                "title": "Severance S02E01 - Hello, Kier",
                "type": "episode",
                "show_title": "Severance",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Hello, Kier",
                    "year": 2025,
                    "season": 2,
                    "episode": 1,
                    "ids": {"imdb": "tt11280740"},
                },
                "progress": "68.4%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(38),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble (100.0%)",
                "title": "The Bear S03E01 - Tomorrow",
                "type": "episode",
                "show_title": "The Bear",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Tomorrow",
                    "year": 2024,
                    "season": 3,
                    "episode": 1,
                    "ids": {"imdb": "tt27984852"},
                },
                "progress": "100.0%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(85),
                "user": "demo_viewer",
                "event": "media.rate",
                "action": "rate (9/10)",
                "title": "Dune: Part Two (2024)",
                "type": "movie",
                "show_title": None,
                "media_payload": {
                    "media_type": "movie",
                    "title": "Dune: Part Two",
                    "year": 2024,
                    "ids": {"imdb": "tt15239678"},
                },
                "progress": "9/10",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(190),
                "user": "demo_partner",
                "event": "media.scrobble",
                "action": "scrobble (100.0%)",
                "title": "Succession S04E03 - Connor's Wedding",
                "type": "episode",
                "show_title": "Succession",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Connor's Wedding",
                    "year": 2023,
                    "season": 4,
                    "episode": 3,
                    "ids": {"imdb": "tt19363162"},
                },
                "progress": "100.0%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(320),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble (100.0%)",
                "title": "Slow Horses S04E01 - Identity Theft",
                "type": "episode",
                "show_title": "Slow Horses",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Identity Theft",
                    "year": 2024,
                    "season": 4,
                    "episode": 1,
                    "ids": {"imdb": "tt21876404"},
                },
                "progress": "100.0%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(480),
                "user": "demo_viewer",
                "event": "media.pause",
                "action": "pause (42.1%)",
                "title": "Shōgun S01E01 - Anjin",
                "type": "episode",
                "show_title": "Shōgun",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Anjin",
                    "year": 2024,
                    "season": 1,
                    "episode": 1,
                    "ids": {"imdb": "tt8888322"},
                },
                "progress": "42.1%",
                "result_status": "ok",
            },
        ]

    def get_demo_sonarr_shows(self, query: str = "") -> list[dict[str, Any]]:
        q = (query or "").lower().strip()
        if not q:
            return self.SONARR_CATALOG[:8]
        return [s for s in self.SONARR_CATALOG if q in s["title"].lower()]

    def get_demo_logs(self, lines: int = 40) -> list[str]:
        now = datetime.datetime.now()
        date_str = now.strftime("%b %d")
        logs = [
            f"{date_str} 08:00:01 mediaserver systemd[1]: Started Plex to Trakt Webhook Scrobbler.",
            f"{date_str} 08:00:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:00:02 [INFO] plex_trakt_scrobbler: Startup Diagnostics: Token health verified (valid for 84 days).",
            f"{date_str} 08:00:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:00:02 [INFO] plex_trakt_scrobbler: Startup Diagnostics: Multi-user co-watching enabled for partner @demo_partner.",
            f"{date_str} 08:00:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:00:02 [INFO] plex_trakt_scrobbler: Startup Diagnostics: Sonarr integration enabled (https://sonarr.demo.internal).",
            f"{date_str} 08:00:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:00:02 [INFO] uvicorn.error: Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)",
            f"{date_str} 08:15:32 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:15:32 [INFO] plex_trakt_scrobbler: Webhook received: media.play for 'The Bear S03E01 - Tomorrow' by user 'demo_viewer'",
            f"{date_str} 08:15:33 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:15:33 [INFO] plex_trakt_scrobbler: Trakt scrobble: start payload dispatched for The Bear S03E01",
            f"{date_str} 08:15:33 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:15:33 [INFO] plex_trakt_scrobbler: Trakt scrobble response: 201 Created",
            f"{date_str} 08:48:12 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:48:12 [INFO] plex_trakt_scrobbler: Webhook received: media.scrobble for 'The Bear S03E01 - Tomorrow' (progress: 100.0%)",
            f"{date_str} 08:48:12 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:48:12 [INFO] plex_trakt_scrobbler: Co-watching dual-sync triggering for partner @demo_partner: The Bear",
            f"{date_str} 08:48:13 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:48:13 [INFO] plex_trakt_scrobbler: Co-watch dual-sync succeeded for partner @demo_partner: The Bear",
            f"{date_str} 08:48:13 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:48:13 [INFO] plex_trakt_scrobbler: Trakt scrobble response: 201 Created",
            f"{date_str} 08:55:01 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:55:01 [INFO] plex_trakt_scrobbler: Webhook received: media.rate for 'Dune: Part Two (2024)' (rating: 9/10)",
            f"{date_str} 08:55:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:55:02 [INFO] plex_trakt_scrobbler: Trakt rating successfully submitted: 201 Created",
            f"{date_str} 09:02:45 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:02:45 [INFO] plex_trakt_scrobbler: Webhook received: media.play for 'Severance S02E01 - Hello, Kier' by user 'demo_viewer'",
            f"{date_str} 09:02:46 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:02:46 [INFO] plex_trakt_scrobbler: Trakt scrobble: start dispatched for Severance S02E01",
            f"{date_str} 09:02:46 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:02:46 [INFO] plex_trakt_scrobbler: Trakt scrobble response: 201 Created",
            f"{date_str} 09:18:20 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:18:20 [INFO] plex_trakt_scrobbler: Webhook received: media.pause for 'Severance S02E01 - Hello, Kier' (progress: 68.4%)",
            f"{date_str} 09:18:21 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:18:21 [INFO] plex_trakt_scrobbler: Trakt scrobble: pause dispatched for Severance S02E01",
        ]
        return logs[-lines:]

    def get_demo_reconciliation(self) -> list[dict[str, Any]]:
        """Return simulated library discrepancies for demo mode."""
        return [
            {
                "id": "movie:101",
                "type": "movie",
                "title": "Interstellar",
                "series_title": None,
                "season": None,
                "episode": None,
                "year": 2014,
                "rating_key": "101",
                "ids": {"imdb": "tt0816692", "tmdb": "157336"},
                "status": "trakt_only",
                "plex_watched": False,
                "trakt_watched": True,
                "plex_rating": None,
                "trakt_rating": 10,
                "action_recommended": "mark_plex_watched",
            },
            {
                "id": "movie:102",
                "type": "movie",
                "title": "Dune: Part Two",
                "series_title": None,
                "season": None,
                "episode": None,
                "year": 2024,
                "rating_key": "102",
                "ids": {"imdb": "tt15239678", "tmdb": "693134"},
                "status": "rating_mismatch",
                "plex_watched": True,
                "trakt_watched": True,
                "plex_rating": 8.0,
                "trakt_rating": 9,
                "action_recommended": "sync_rating_to_plex",
            },
            {
                "id": "episode:201",
                "type": "episode",
                "title": "Tomorrow",
                "series_title": "The Bear",
                "season": 3,
                "episode": 1,
                "year": 2024,
                "rating_key": "201",
                "ids": {"imdb": "tt27984852"},
                "status": "plex_only",
                "plex_watched": True,
                "trakt_watched": False,
                "plex_rating": None,
                "trakt_rating": None,
                "action_recommended": "sync_to_trakt",
            },
            {
                "id": "episode:202",
                "type": "episode",
                "title": "Felina",
                "series_title": "Breaking Bad",
                "season": 5,
                "episode": 16,
                "year": 2013,
                "rating_key": "202",
                "ids": {"imdb": "tt2301451"},
                "status": "trakt_only",
                "plex_watched": False,
                "trakt_watched": True,
                "plex_rating": None,
                "trakt_rating": 10,
                "action_recommended": "mark_plex_watched",
            },
        ]

    def get_demo_cross_tracker_diff(self) -> list[dict[str, Any]]:
        """Return simulated cross-tracker discrepancies between Trakt and Simkl."""
        return [
            {
                "id": "diff_movie_t2s_tt15239678",
                "media_type": "movie",
                "title": "Dune: Part Two",
                "year": 2024,
                "season": None,
                "episode": None,
                "show_title": None,
                "direction": "trakt_to_simkl",
                "sync_type": "watched",
                "source_status": "watched",
                "target_status": "unwatched",
                "source_rating": None,
                "target_rating": None,
                "ids": {"imdb": "tt15239678", "tmdb": "693134"},
                "watched_at": "2026-09-27T21:40:00Z",
            },
            {
                "id": "diff_ep_t2s_tt11280740_s2e1",
                "media_type": "episode",
                "title": "S02E01",
                "year": 2025,
                "season": 2,
                "episode": 1,
                "show_title": "Severance",
                "direction": "trakt_to_simkl",
                "sync_type": "watched",
                "source_status": "watched",
                "target_status": "unwatched",
                "source_rating": None,
                "target_rating": None,
                "ids": {"imdb": "tt11280740", "tvdb": "371980"},
                "watched_at": "2026-09-28T02:15:00Z",
            },
            {
                "id": "diff_movie_s2t_tt0245429",
                "media_type": "movie",
                "title": "Spirited Away",
                "year": 2001,
                "season": None,
                "episode": None,
                "show_title": None,
                "direction": "simkl_to_trakt",
                "sync_type": "watched",
                "source_status": "watched",
                "target_status": "unwatched",
                "source_rating": None,
                "target_rating": None,
                "ids": {"imdb": "tt0245429", "tmdb": "129", "simkl": "2608"},
                "watched_at": "2026-09-26T18:30:00Z",
            },
            {
                "id": "diff_ep_s2t_tt29930773_s1e1",
                "media_type": "episode",
                "title": "S01E01",
                "year": 2023,
                "season": 1,
                "episode": 1,
                "show_title": "Frieren: Beyond Journey's End",
                "direction": "simkl_to_trakt",
                "sync_type": "watched",
                "source_status": "watched",
                "target_status": "unwatched",
                "source_rating": None,
                "target_rating": None,
                "ids": {"imdb": "tt29930773", "tvdb": "424759", "simkl": "2029530"},
                "watched_at": "2026-09-25T14:10:00Z",
            },
            {
                "id": "diff_movie_rating_t2s_tt15398776",
                "media_type": "movie",
                "title": "Oppenheimer",
                "year": 2023,
                "season": None,
                "episode": None,
                "show_title": None,
                "direction": "trakt_to_simkl",
                "sync_type": "rating",
                "source_status": "10/10",
                "target_status": "unrated",
                "source_rating": 10,
                "target_rating": None,
                "ids": {"imdb": "tt15398776", "tmdb": "872585"},
                "watched_at": None,
            },
            {
                "id": "diff_ep_rating_s2t_tt2560140_s4e28",
                "media_type": "episode",
                "title": "S04E28",
                "year": 2022,
                "season": 4,
                "episode": 28,
                "show_title": "Attack on Titan",
                "direction": "simkl_to_trakt",
                "sync_type": "rating",
                "source_status": "9/10",
                "target_status": "unrated",
                "source_rating": 9,
                "target_rating": None,
                "ids": {"imdb": "tt2560140", "tvdb": "267440", "simkl": "40316"},
                "watched_at": None,
            },
        ]


demo_mgr = DemoManager()
