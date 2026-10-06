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
import re
from typing import Any, Optional

from app.config import Config

logger = logging.getLogger("omniscrobble.analytics_manager")


class AnalyticsManager:
    """Computes personal viewing telemetry, platform distributions, and OmniWrapped retrospectives."""

    # Event actions that represent a completed watch (legacy "scrobble" variants included)
    COMPLETED_WATCH_ACTIONS = frozenset({"mark_watched", "scrobble_stop", "manual_scrobble", "scrobble", "watched"})

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
        keys = ["total", "movies", "episodes", "ratings", "collections", "watch_minutes"]
        if not self.stats_file or not self.stats_file.exists():
            return {k: 0 for k in keys}
        try:
            with open(self.stats_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {k: int(data.get(k, 0)) for k in keys}
        except Exception:
            pass
        return {k: 0 for k in keys}

    @staticmethod
    def _extract_item_minutes(ev: dict[str, Any], media_type: str, action: str = "") -> int:
        """Extract actual watch minutes from event metadata or fall back to sensible averages."""
        media_payload = ev.get("media_payload") if isinstance(ev.get("media_payload"), dict) else {}

        # A threshold stop may be recorded before the item's runtime is complete.
        # Prefer the media server's playhead; if it is missing, use logged progress.
        if action == "scrobble_stop":
            try:
                duration = float(ev.get("duration_ms") or media_payload.get("duration_ms") or 0)
                offset = ev.get("view_offset_ms")
                if duration > 0 and offset is not None and float(offset) > 0:
                    return max(1, round(min(float(offset), duration) / 60000.0))
                progress_raw = str(ev.get("progress", "")).rstrip("%")
                progress = float(progress_raw)
                if duration > 0 and 0 < progress < 100:
                    return max(1, round(duration * progress / 100.0 / 60000.0))
            except (ValueError, TypeError):
                pass

        # 1. Direct duration_ms
        dur_ms = ev.get("duration_ms")
        if not dur_ms:
            dur_ms = media_payload.get("duration_ms")

        if dur_ms is not None:
            try:
                val_ms = float(dur_ms)
                if val_ms > 0:
                    return max(1, round(val_ms / 60000.0))
            except (ValueError, TypeError):
                pass

        # 3. Generic 'duration' field (may be in ms, seconds, or minutes)
        dur = ev.get("duration")
        if not dur:
            dur = media_payload.get("duration")
        if dur is not None:
            try:
                val = float(dur)
                if val > 0:
                    if val >= 60000:
                        return max(1, round(val / 60000.0))
                    if val >= 1000:
                        return max(1, round(val / 60.0))
                    return max(1, round(val))
            except (ValueError, TypeError):
                pass

        # 4. Fallback defaults for legacy items lacking runtime metadata
        return 90 if media_type == "movie" else 35

    def get_summary(
        self,
        period: str = "all",
        year: Optional[int] = None,
        demo: bool = False,
        is_admin: bool = True,
    ) -> dict[str, Any]:
        """Compute aggregated watch analytics across a given time window or calendar year."""
        if demo:
            demo_devices = [
                {"device": "Living Room Apple TV", "hours": 86.0},
                {"device": "Bedroom Chromecast", "hours": 38.5},
                {"device": "Office Shield TV", "hours": 22.0},
            ]
            if not is_admin:
                demo_devices = [{"device": f"Player {i+1}", "hours": d["hours"]} for i, d in enumerate(demo_devices)]
            return {
                "period": period,
                "total_watch_hours": 146.5,
                "total_watch_formatted": "146h 30m",
                "total_scrobbles": 182,
                "unique_titles": 46,
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
                "top_devices": demo_devices,
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
        if year is None:
            if period == "week":
                start_cutoff = now - datetime.timedelta(days=7)
            elif period == "month":
                start_cutoff = now - datetime.timedelta(days=30)
            elif period == "year":
                start_cutoff = now - datetime.timedelta(days=365)

        total_scrobbles = 0
        unique_titles_set: set[str] = set()
        movies_watched = 0
        episodes_watched = 0
        ratings_count = 0
        solo_mins = 0
        cowatch_mins = 0

        servers: collections.Counter[str] = collections.Counter()
        devices: collections.Counter[str] = collections.Counter()
        shows: collections.Counter[str] = collections.Counter()
        show_mins: collections.Counter[str] = collections.Counter()
        genres_counter: collections.Counter[str] = collections.Counter()
        pending_unscrobbles: collections.Counter[str] = collections.Counter()

        for ev in events:
            if not isinstance(ev, dict):
                continue

            ts_str = ev.get("timestamp")
            if ts_str:
                try:
                    clean_ts = str(ts_str).replace("Z", "+00:00")
                    try:
                        ev_time = datetime.datetime.fromisoformat(clean_ts)
                    except ValueError:
                        ev_time = datetime.datetime.strptime(str(ts_str).split(".")[0], "%Y-%m-%d %H:%M:%S")

                    if hasattr(ev_time, "tzinfo") and ev_time.tzinfo is not None:
                        ev_time = ev_time.replace(tzinfo=None)

                    if year is not None and ev_time.year != year:
                        continue
                    if start_cutoff and ev_time < start_cutoff:
                        continue
                except Exception:
                    pass

            action = str(ev.get("action", "")).lower()
            m_type = str(ev.get("type") or ev.get("media_type") or (ev.get("media_payload") or {}).get("media_type") or "").lower()
            title = str(ev.get("title", ""))

            if not m_type:
                if ev.get("show_title") or re.search(r"\s+[Ss]\d+([Ee]\d+)?", title):
                    m_type = "episode"
                else:
                    m_type = "movie"

            if "rate" in action or action == "rating":
                ratings_count += 1
                continue

            action_base = action.split(" (")[0].strip()

            # events.json is stored newest-first, so an unscrobble is seen before the older watch it undoes
            if action_base == "unscrobble":
                pending_unscrobbles[title.strip().lower()] += 1
                continue

            # Only completed watches count; start/pause/bypassed/collection events do not
            if action_base not in self.COMPLETED_WATCH_ACTIONS:
                continue

            if pending_unscrobbles[title.strip().lower()] > 0:
                pending_unscrobbles[title.strip().lower()] -= 1
                continue

            # Scrobbles / watch events
            total_scrobbles += 1
            if m_type == "movie":
                movies_watched += 1
                m_title = (ev.get("media_payload") or {}).get("title") or title
                clean_m_title = re.sub(r"\s*\(\d{4}\)$", "", m_title).strip()
                unique_titles_set.add(f"movie:{clean_m_title.lower()}")
            else:
                episodes_watched += 1

            item_mins = self._extract_item_minutes(ev, m_type, action_base)

            # Extract show title if episode using explicit field, metadata payload, or robust regex
            if m_type == "episode":
                show_name = ev.get("show_title")
                if not show_name:
                    media_pl = ev.get("media_payload") or {}
                    show_name = media_pl.get("show_title") or media_pl.get("grandparent_title")
                if not show_name:
                    match = re.search(r"\s+[Ss]\d+([Ee]\d+)?", title)
                    if match:
                        show_name = title[:match.start()].strip()
                    elif " - " in title:
                        show_name = title.split(" - ")[0].strip()
                    else:
                        show_name = title.strip()
                if show_name:
                    shows[show_name] += 1
                    show_mins[show_name] += item_mins
                    unique_titles_set.add(f"show:{show_name.lower().strip()}")

            # Co-watch classification (check for synced or affirmative eligibility, avoiding 'ineligible' false-positives)
            cw = ev.get("cowatch_status")
            cw_reason = ev.get("cowatch_reason")
            is_cowatch = False
            if isinstance(cw, dict):
                if cw.get("synced") is True:
                    is_cowatch = True
                elif cw.get("reason"):
                    r_str = str(cw.get("reason")).lower()
                    if "eligible" in r_str and "ineligible" not in r_str:
                        is_cowatch = True
            elif isinstance(cw, bool):
                is_cowatch = cw
            elif isinstance(cw, str):
                r_str = cw.lower()
                if "eligible" in r_str and "ineligible" not in r_str:
                    is_cowatch = True

            if not is_cowatch and cw_reason:
                r_str = str(cw_reason).lower()
                if "eligible" in r_str and "ineligible" not in r_str:
                    is_cowatch = True
            if is_cowatch:
                cowatch_mins += item_mins
            else:
                solo_mins += item_mins

            # Server tracking from server property or payload details
            ev_server = ev.get("server")
            raw_details = str(ev.get("details", "")).lower()
            if ev_server:
                servers[str(ev_server).capitalize()] += 1
            elif "plex" in raw_details or "plex" in action:
                servers["Plex"] += 1
            elif "jellyfin" in raw_details or "jellyfin" in action:
                servers["Jellyfin"] += 1
            elif "emby" in raw_details or "emby" in action:
                servers["Emby"] += 1
            else:
                servers["Plex"] += 1

            # Player device tracking
            player = ev.get("player") or "Default Player"
            devices[player] += item_mins

            # Real genre extraction from event or media payload
            ev_genres = ev.get("genres") or (ev.get("media_payload") or {}).get("genres")
            if isinstance(ev_genres, list):
                for g in ev_genres:
                    if g:
                        genres_counter[str(g).strip()] += 1
            elif isinstance(ev_genres, str) and ev_genres:
                for g in ev_genres.split(","):
                    if g.strip():
                        genres_counter[g.strip()] += 1

        # Fallback to lifetime stats if events empty and period is all
        if period == "all" and year is None and total_scrobbles == 0 and stats.get("total", 0) > 0:
            movies_watched = stats.get("movies", 0)
            episodes_watched = stats.get("episodes", 0)
            total_scrobbles = movies_watched + episodes_watched
            if stats.get("watch_minutes", 0) > 0:
                solo_mins = stats["watch_minutes"]
            else:
                solo_mins = (movies_watched * 90) + (episodes_watched * 35)
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
        if not is_admin and not demo:
            top_devs = [{"device": f"Player {i+1}", "hours": d["hours"]} for i, d in enumerate(top_devs)]

        top_sh = [{"show": s, "episodes": shows[s], "hours": round(show_mins[s] / 60.0, 1)} for s, _ in shows.most_common(5)]
        top_genres = [{"genre": g, "count": count} for g, count in genres_counter.most_common(5)]

        return {
            "period": period,
            "total_watch_hours": total_hours,
            "total_watch_formatted": f"{h}h {m}m",
            "total_scrobbles": total_scrobbles,
            "unique_titles": len(unique_titles_set) if total_scrobbles > 0 else 0,
            "movies_watched": movies_watched,
            "episodes_watched": episodes_watched,
            "ratings_submitted": ratings_count,
            "solo_hours": round(solo_mins / 60.0, 1),
            "cowatch_hours": round(cowatch_mins / 60.0, 1),
            "cowatch_ratio_percent": cowatch_ratio,
            "server_distribution": server_dist,
            "top_devices": top_devs or [{"device": "Default Media Player", "hours": total_hours}],
            "top_shows": top_sh,
            "top_genres": top_genres,
        }

    def get_omniwrapped(
        self,
        year: Optional[int] = None,
        demo: bool = False,
        is_admin: bool = True,
    ) -> dict[str, Any]:
        """Generate an annual 'OmniWrapped' retrospective summary for a given calendar year."""
        target_year = year if year is not None else datetime.datetime.now().year
        summary = self.get_summary(period="all", year=target_year, demo=demo, is_admin=is_admin)

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

        if summary.get("top_shows"):
            top_show_name = summary["top_shows"][0]["show"]
            top_show_episodes = summary["top_shows"][0]["episodes"]
        else:
            top_show_name = "No series logged yet"
            top_show_episodes = 0

        partner_raw = getattr(Config, "CO_WATCH_USER", "partner") or "Partner"
        if not is_admin and not demo:
            if partner_raw and partner_raw.lower() != "partner":
                partner_display = partner_raw[:2] + "****" if len(partner_raw) > 2 else "****"
            else:
                partner_display = "Partner"
        else:
            partner_display = partner_raw

        return {
            "year": target_year,
            "headline": f"Your {target_year} OmniWrapped",
            "archetype": archetype,
            "description": description,
            "total_watch_time": summary["total_watch_formatted"],
            "total_watch_hours": summary["total_watch_hours"],
            "total_scrobbles": summary["total_scrobbles"],
            "unique_titles": summary.get("unique_titles", 0),
            "movies_watched": summary["movies_watched"],
            "episodes_watched": summary["episodes_watched"],
            "ratings_submitted": summary["ratings_submitted"],
            "cowatch_breakdown": {
                "solo_hours": summary["solo_hours"],
                "shared_hours": summary["cowatch_hours"],
                "cowatch_percentage": summary["cowatch_ratio_percent"],
                "partner_user": partner_display,
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
