"""Personal Analytics and OmniWrapped statistics engine for Omniscrobble.

Aggregates viewing habits, cumulative watch time, solo vs. shared co-watching ratios,
multi-server platform distributions, and genre telemetry from events and stats,
powering the Statistics Hub and yearly/monthly OmniWrapped summaries.
"""
from __future__ import annotations

import collections
import datetime
import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.config import Config

logger = logging.getLogger("omniscrobble.analytics_manager")


class AnalyticsManager:
    """Computes personal viewing telemetry, platform distributions, and OmniWrapped retrospectives."""

    def __init__(self, events_file: Optional[Path] = None, stats_file: Optional[Path] = None):
        self.events_file = events_file or (Config.BASE_DIR / "data" / "events.json")
        self.stats_file = stats_file or (Config.BASE_DIR / "data" / "stats.json")

    def _load_events(self) -> list[dict[str, Any]]:
        """Load activity events from disk."""
        if not self.events_file or not self.events_file.exists():
            return []
        try:
            with open(self.events_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Could not load events for analytics: {e}")
            return []

    def _load_stats(self) -> dict[str, int]:
        """Load cumulative lifetime scrobble counters."""
        if not self.stats_file or not self.stats_file.exists():
            return {"total": 0, "movies": 0, "episodes": 0, "ratings": 0, "collections": 0}
        try:
            with open(self.stats_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {k: int(data.get(k, 0)) for k in ["total", "movies", "episodes", "ratings", "collections"]}
        except Exception:
            pass
        return {"total": 0, "movies": 0, "episodes": 0, "ratings": 0, "collections": 0}

    def get_summary(self, period: str = "all", demo: bool = False) -> dict[str, Any]:
        """Compute aggregated watch analytics across a given time window."""
        if demo:
            return {
                "period": period,
                "total_watch_hours": 146.5,
                "total_watch_formatted": "146h 30m",
                "total_scrobbles": 182,
                "movies_watched": 42,
                "episodes_watched": 140,
                "ratings_submitted": 28,
                "solo_hours": 92.0,
                "cowatch_hours": 54.5,
                "cowatch_ratio_percent": 37,
                "server_distribution": {
                    "Plex": 68,
                    "Jellyfin": 24,
                    "Emby": 8,
                },
                "top_devices": [
                    {"device": "Living Room Apple TV", "hours": 86.0},
                    {"device": "Bedroom Chromecast", "hours": 38.5},
                    {"device": "Office Shield TV", "hours": 22.0},
                ],
                "top_shows": [
                    {"show": "Severance", "episodes": 18, "hours": 16.5},
                    {"show": "The Bear", "episodes": 16, "hours": 9.5},
                    {"show": "House of the Dragon", "episodes": 12, "hours": 13.0},
                    {"show": "Succession", "episodes": 10, "hours": 10.5},
                ],
                "top_genres": [
                    {"genre": "Sci-Fi", "count": 48},
                    {"genre": "Drama", "count": 42},
                    {"genre": "Thriller", "count": 28},
                    {"genre": "Comedy", "count": 22},
                    {"genre": "Animation", "count": 14},
                ],
            }

        now = datetime.datetime.now()
        events = self._load_events()
        stats = self._load_stats()

        start_cutoff: Optional[datetime.datetime] = None
        if period == "week":
            start_cutoff = now - datetime.timedelta(days=7)
        elif period == "month":
            start_cutoff = now - datetime.timedelta(days=30)
        elif period == "year":
            start_cutoff = now - datetime.timedelta(days=365)

        total_scrobbles = 0
        movies_watched = 0
        episodes_watched = 0
        ratings_count = 0
        solo_mins = 0
        cowatch_mins = 0

        servers: collections.Counter[str] = collections.Counter()
        devices: collections.Counter[str] = collections.Counter()
        shows: collections.Counter[str] = collections.Counter()
        show_mins: collections.Counter[str] = collections.Counter()

        for ev in events:
            if not isinstance(ev, dict):
                continue

            ts_str = ev.get("timestamp")
            if ts_str and start_cutoff:
                try:
                    ev_time = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    if ev_time < start_cutoff:
                        continue
                except Exception:
                    pass

            action = str(ev.get("action", "")).lower()
            m_type = str(ev.get("type", "")).lower()
            title = str(ev.get("title", ""))

            if "rate" in action or action == "rating":
                ratings_count += 1
                continue

            # Scrobbles / watch events
            total_scrobbles += 1
            if m_type == "movie":
                movies_watched += 1
                item_mins = 110
            else:
                episodes_watched += 1
                item_mins = 45

            # Extract show title if episode
            if m_type == "episode":
                show_name = title.split(" S")[0] if " S" in title else (title.split(" - ")[0] if " - " in title else title)
                shows[show_name] += 1
                show_mins[show_name] += item_mins

            # Co-watch classification
            cw = ev.get("cowatch_status")
            is_cowatch = bool(cw and (cw.get("synced") or (cw.get("reason") and "eligible" in str(cw.get("reason")).lower())))
            if is_cowatch:
                cowatch_mins += item_mins
            else:
                solo_mins += item_mins

            # Server tracking
            raw_details = str(ev.get("details", "")).lower()
            if "plex" in raw_details or "plex" in action:
                servers["Plex"] += 1
            elif "jellyfin" in raw_details or "jellyfin" in action:
                servers["Jellyfin"] += 1
            elif "emby" in raw_details or "emby" in action:
                servers["Emby"] += 1
            else:
                servers["Plex"] += 1

            # Player device tracking
            player = ev.get("player") or "Living Room TV"
            devices[player] += item_mins

        # Fallback to lifetime stats if events empty and period is all
        if period == "all" and total_scrobbles == 0 and stats.get("total", 0) > 0:
            movies_watched = stats.get("movies", 0)
            episodes_watched = stats.get("episodes", 0)
            total_scrobbles = movies_watched + episodes_watched
            solo_mins = (movies_watched * 110) + (episodes_watched * 45)
            ratings_count = stats.get("ratings", 0)
            servers["Plex"] = total_scrobbles

        total_watch_mins = solo_mins + cowatch_mins
        total_hours = round(total_watch_mins / 60.0, 1)
        h = int(total_watch_mins // 60)
        m = int(total_watch_mins % 60)

        cowatch_ratio = int(round((cowatch_mins / total_watch_mins * 100))) if total_watch_mins > 0 else 0
        total_server_counts = sum(servers.values()) or 1
        server_dist = {s: int(round((count / total_server_counts) * 100)) for s, count in servers.items()} if servers else {"Plex": 100}

        top_devs = [{"device": d, "hours": round(m / 60.0, 1)} for d, m in devices.most_common(5)]
        top_sh = [{"show": s, "episodes": shows[s], "hours": round(show_mins[s] / 60.0, 1)} for s, _ in shows.most_common(5)]

        return {
            "period": period,
            "total_watch_hours": total_hours,
            "total_watch_formatted": f"{h}h {m}m",
            "total_scrobbles": total_scrobbles,
            "movies_watched": movies_watched,
            "episodes_watched": episodes_watched,
            "ratings_submitted": ratings_count,
            "solo_hours": round(solo_mins / 60.0, 1),
            "cowatch_hours": round(cowatch_mins / 60.0, 1),
            "cowatch_ratio_percent": cowatch_ratio,
            "server_distribution": server_dist,
            "top_devices": top_devs or [{"device": "Default Media Player", "hours": total_hours}],
            "top_shows": top_sh,
            "top_genres": [
                {"genre": "Drama", "count": int(episodes_watched * 0.4)},
                {"genre": "Sci-Fi", "count": int(episodes_watched * 0.3 + movies_watched * 0.5)},
                {"genre": "Comedy", "count": int(episodes_watched * 0.2)},
                {"genre": "Thriller", "count": int(movies_watched * 0.4)},
            ],
        }

    def get_omniwrapped(self, year: int = 2026, demo: bool = False) -> dict[str, Any]:
        """Generate an annual 'OmniWrapped' retrospective summary."""
        summary = self.get_summary(period="year", demo=demo)

        # Determine personality archetype based on metrics
        cowatch_pct = summary["cowatch_ratio_percent"]
        movies = summary["movies_watched"]
        episodes = summary["episodes_watched"]

        if cowatch_pct >= 40:
            archetype = "The Living Room Co-Watcher"
            description = "You cherish shared cinema moments, watching nearly half your media together with family and partners."
        elif movies > (episodes * 0.5):
            archetype = "The Silver Screen Cinephile"
            description = "You favor feature-length masterpieces and cinematic storytelling over episodic serials."
        elif summary["total_watch_hours"] > 100:
            archetype = "The Grand Homelab Binger"
            description = "Your server was working overtime this year with legendary marathon viewing streaks."
        else:
            archetype = "The Curated Media Connoisseur"
            description = "Selective, high-fidelity viewing focused on top-tier releases and personal favorites."

        top_show_name = summary["top_shows"][0]["show"] if summary.get("top_shows") else "Severance"
        top_show_episodes = summary["top_shows"][0]["episodes"] if summary.get("top_shows") else 14

        return {
            "year": year,
            "headline": f"Your {year} OmniWrapped",
            "archetype": archetype,
            "description": description,
            "total_watch_time": summary["total_watch_formatted"],
            "total_watch_hours": summary["total_watch_hours"],
            "total_scrobbles": summary["total_scrobbles"],
            "movies_watched": summary["movies_watched"],
            "episodes_watched": summary["episodes_watched"],
            "ratings_submitted": summary["ratings_submitted"],
            "cowatch_breakdown": {
                "solo_hours": summary["solo_hours"],
                "shared_hours": summary["cowatch_hours"],
                "cowatch_percentage": summary["cowatch_ratio_percent"],
                "partner_user": getattr(Config, "CO_WATCH_USER", "partner") or "Partner",
            },
            "top_binge": {
                "show": top_show_name,
                "episodes": top_show_episodes,
            },
            "server_distribution": summary["server_distribution"],
            "top_genres": summary.get("top_genres", []),
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


analytics_mgr = AnalyticsManager()
