"""Weekly Activity Digest generator and background dispatcher for Omniscrobble.

Aggregates 7-day scrobble telemetry, watch times, completed movies/episodes,
active devices, and co-watched sessions, and dispatches rich visual summaries
across configured notification channels (Discord, Telegram, Ntfy, Pushover, Gotify, Matrix).
"""
from __future__ import annotations

import asyncio
import datetime
import html
import json
import logging
import time
import urllib.parse
from typing import Any, Optional
import httpx

from app.config import Config
from app.services.notifier import notifier, DISCORD_COLOR_COWATCH, DISCORD_COLOR_SCROBBLE
from app.services.settings_manager import settings_mgr

logger = logging.getLogger("omniscrobble.digest_manager")


class DigestManager:
    """Computes weekly viewing statistics and formats multi-channel digest reports."""

    def __init__(self, events_file: Optional[Any] = None):
        self.events_file = events_file or (Config.BASE_DIR / "data" / "events.json")
        self._last_digest_sent: float = 0.0

    def generate_digest_stats(self, days: int = 7, demo: bool = False) -> dict[str, Any]:
        """Compute aggregated watch statistics over the specified past day range."""
        now = datetime.datetime.now()
        start_time = now - datetime.timedelta(days=days)

        if demo:
            return {
                "window_days": days,
                "start_date": start_time.strftime("%b %d"),
                "end_date": now.strftime("%b %d, %Y"),
                "total_scrobbles": 18,
                "movies_watched": 4,
                "episodes_watched": 14,
                "total_ratings": 3,
                "total_collections": 6,
                "watch_time_minutes": 980,  # ~16h 20m
                "watch_time_formatted": "16h 20m",
                "cowatch_sessions": 5,
                "cowatch_shows": ["Severance", "The Bear"],
                "top_servers": {"Plex": 12, "Jellyfin": 6},
                "top_devices": {"Apple TV 4K": 10, "Shield TV": 8},
                "top_media": [
                    "Dune: Part Two (2024)",
                    "Severance S02E01 - Goodbye Mrs. Selvig",
                    "Gladiator II (2024)",
                    "The Bear S03E01 - Tomorrow",
                    "Alien: Earth S01E01 - Welcome to Earth",
                ],
            }

        events = []
        if self.events_file and self.events_file.exists():
            try:
                with open(self.events_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, list):
                        events = raw
            except Exception as e:
                logger.warning(f"Could not load events for weekly digest: {e}")

        movies = 0
        episodes = 0
        ratings = 0
        collections = 0
        watch_time_mins = 0
        cowatch_count = 0
        cowatch_shows: set[str] = set()
        servers: dict[str, int] = {}
        devices: dict[str, int] = {}
        recent_titles: list[str] = []

        for ev in events:
            if not isinstance(ev, dict):
                continue

            # Parse event timestamp
            ts_str = ev.get("timestamp")
            if ts_str:
                try:
                    ev_time = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    if ev_time < start_time:
                        continue
                except Exception:
                    pass

            action = str(ev.get("action", "")).lower()
            m_type = str(ev.get("type", "")).lower()
            title = ev.get("title", "")
            srv = ev.get("server", "plex").capitalize()
            servers[srv] = servers.get(srv, 0) + 1
            dev = ev.get("device")
            if dev:
                devices[dev] = devices.get(dev, 0) + 1

            if "rate" in action:
                ratings += 1
            elif "collection" in action:
                collections += 1
            elif any(w in action for w in ("watched", "scrobble", "playback.stop")):
                if m_type == "movie":
                    movies += 1
                    watch_time_mins += 115  # Average movie runtime estimate
                elif m_type in ("episode", "show"):
                    episodes += 1
                    watch_time_mins += 45  # Average episode runtime estimate
                else:
                    episodes += 1
                    watch_time_mins += 45

                if title and title not in recent_titles and len(recent_titles) < 5:
                    recent_titles.append(title)

                # Check co-watch
                cw_status = ev.get("cowatch_status")
                if cw_status:
                    if isinstance(cw_status, dict) and cw_status.get("partner"):
                        cowatch_count += 1
                        show_title = ev.get("show_title") or title
                        if show_title:
                            cowatch_shows.add(show_title)
                    elif isinstance(cw_status, str) and ("cowatch" in cw_status.lower() or "@" in cw_status):
                        cowatch_count += 1
                        show_title = ev.get("show_title") or title
                        if show_title:
                            cowatch_shows.add(show_title)

        hours = watch_time_mins // 60
        mins = watch_time_mins % 60
        time_formatted = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

        return {
            "window_days": days,
            "start_date": start_time.strftime("%b %d"),
            "end_date": now.strftime("%b %d, %Y"),
            "total_scrobbles": movies + episodes,
            "movies_watched": movies,
            "episodes_watched": episodes,
            "total_ratings": ratings,
            "total_collections": collections,
            "watch_time_minutes": watch_time_mins,
            "watch_time_formatted": time_formatted,
            "cowatch_sessions": cowatch_count,
            "cowatch_shows": sorted(list(cowatch_shows)),
            "top_servers": servers,
            "top_devices": devices,
            "top_media": recent_titles,
        }

    def format_discord_digest(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Construct a Discord rich embed payload for the weekly digest."""
        time_frame = f"{stats.get('start_date')} – {stats.get('end_date')}"
        embed = {
            "title": "📊 Omniscrobble Weekly Activity Digest",
            "description": f"Here is your personal media consumption and scrobble report for **{time_frame}**.",
            "color": DISCORD_COLOR_COWATCH,
            "fields": [
                {
                    "name": "⏱️ Watch Time",
                    "value": f"**{stats.get('watch_time_formatted', '0m')}**",
                    "inline": True,
                },
                {
                    "name": "🎬 Media Completed",
                    "value": f"**{stats.get('total_scrobbles', 0)}** ({stats.get('movies_watched', 0)} movies, {stats.get('episodes_watched', 0)} eps)",
                    "inline": True,
                },
                {
                    "name": "⭐ Ratings & Collection",
                    "value": f"⭐ {stats.get('total_ratings', 0)} ratings • 💿 {stats.get('total_collections', 0)} collected",
                    "inline": True,
                },
            ],
            "footer": {"text": "Omniscrobble • Scheduled Weekly Digest"},
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        # Co-Watch field
        cw_count = stats.get("cowatch_sessions", 0)
        if cw_count > 0:
            shows_str = ", ".join(stats.get("cowatch_shows", [])[:3])
            val_text = f"**{cw_count}** sessions"
            if shows_str:
                val_text += f" ({shows_str})"
            embed["fields"].append({"name": "👥 Co-Watched Sessions", "value": val_text, "inline": False})

        # Top media titles
        top_media = stats.get("top_media", [])
        if top_media:
            media_list = "\n".join(f"• {m}" for m in top_media[:5])
            embed["fields"].append({"name": "🏆 Highlights & Completed", "value": media_list, "inline": False})

        # Media Servers
        servers = stats.get("top_servers", {})
        if servers:
            srv_list = " • ".join(f"{k}: {v}" for k, v in servers.items())
            embed["fields"].append({"name": "📡 Active Servers", "value": srv_list, "inline": True})

        return {
            "username": "Omniscrobble Weekly Digest",
            "embeds": [embed],
        }

    def format_html_digest(self, stats: dict[str, Any]) -> str:
        """Construct an HTML formatted digest report for Telegram and Matrix."""
        time_frame = f"{stats.get('start_date')} – {stats.get('end_date')}"
        lines = [
            f"📊 <b>Omniscrobble Weekly Activity Digest</b>",
            f"📅 <i>{html.escape(time_frame)}</i>\n",
            f"⏱️ <b>Total Watch Time:</b> <code>{html.escape(stats.get('watch_time_formatted', '0m'))}</code>",
            f"🎬 <b>Completed:</b> <code>{stats.get('total_scrobbles', 0)}</code> ({stats.get('movies_watched', 0)} movies, {stats.get('episodes_watched', 0)} episodes)",
            f"⭐ <b>Rated:</b> <code>{stats.get('total_ratings', 0)}</code> | 💿 <b>Collected:</b> <code>{stats.get('total_collections', 0)}</code>",
        ]

        cw_count = stats.get("cowatch_sessions", 0)
        if cw_count > 0:
            shows_str = html.escape(", ".join(stats.get("cowatch_shows", [])[:3]))
            lines.append(f"👥 <b>Co-Watched:</b> <code>{cw_count}</code> sessions ({shows_str})")

        top_media = stats.get("top_media", [])
        if top_media:
            lines.append("\n🏆 <b>Highlights:</b>")
            for m in top_media[:5]:
                lines.append(f"• {html.escape(m)}")

        return "<br>".join(lines)

    def format_plain_digest(self, stats: dict[str, Any]) -> str:
        """Construct plain text / markdown digest for Ntfy, Pushover, and Gotify."""
        time_frame = f"{stats.get('start_date')} – {stats.get('end_date')}"
        lines = [
            f"📊 Omniscrobble Weekly Digest ({time_frame})",
            f"⏱️ Watch Time: {stats.get('watch_time_formatted', '0m')}",
            f"🎬 Completed: {stats.get('total_scrobbles', 0)} ({stats.get('movies_watched', 0)} movies, {stats.get('episodes_watched', 0)} episodes)",
            f"⭐ Rated: {stats.get('total_ratings', 0)} | 💿 Collected: {stats.get('total_collections', 0)}",
        ]
        cw_count = stats.get("cowatch_sessions", 0)
        if cw_count > 0:
            shows_str = ", ".join(stats.get("cowatch_shows", [])[:3])
            lines.append(f"👥 Co-Watched: {cw_count} sessions ({shows_str})")

        top_media = stats.get("top_media", [])
        if top_media:
            lines.append("🏆 Highlights:")
            for m in top_media[:5]:
                lines.append(f"  - {m}")
        return "\n".join(lines)

    format_markdown_digest = format_plain_digest

    async def send_digest(
        self,
        demo: bool = False,
        days: int = 7,
        client: Optional[httpx.AsyncClient] = None,
    ) -> dict[str, Any]:
        """Dispatch weekly digest across all active notification channels."""
        stats = self.generate_digest_stats(days=days, demo=demo)
        if demo:
            await asyncio.sleep(0.05)
            self._last_digest_sent = time.time()
            return {
                "status": "success",
                "success": True,
                "demo": True,
                "stats": stats,
                "dispatched_channels": ["discord", "telegram", "gotify"],
                "message": "Demo Mode: Weekly activity digest dispatched successfully!",
            }

        http = client or notifier.get_client()
        tasks = []
        dispatched: list[str] = []

        # 1. Discord
        discord_url = notifier._get_discord_url()
        if discord_url:
            payload = self.format_discord_digest(stats)
            tasks.append(("discord", http.post(discord_url, json=payload)))
            dispatched.append("discord")

        # 2. Telegram
        tg_token = notifier._get_telegram_token()
        tg_chat = notifier._get_telegram_chat_id()
        if tg_token and tg_chat:
            tg_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            html_text = self.format_html_digest(stats).replace("<br>", "\n")
            tg_payload = {
                "chat_id": tg_chat,
                "text": html_text,
                "parse_mode": "HTML",
            }
            tasks.append(("telegram", http.post(tg_url, json=tg_payload)))
            dispatched.append("telegram")

        # 3. Ntfy
        ntfy_url = notifier._get_ntfy_url()
        if ntfy_url:
            plain_text = self.format_plain_digest(stats)
            headers = {
                "Title": "Omniscrobble Weekly Activity Digest",
                "Tags": "chart_with_upwards_trend,calendar,omniscrobble",
                "Priority": Config.NTFY_PRIORITY or "default",
            }
            auth_token = notifier._get_ntfy_auth_token()
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            tasks.append(("ntfy", http.post(ntfy_url, content=plain_text.encode("utf-8"), headers=headers)))
            dispatched.append("ntfy")

        # 4. Pushover
        p_user = notifier._get_pushover_user_key()
        p_token = notifier._get_pushover_api_token()
        if p_user and p_token:
            plain_text = self.format_plain_digest(stats)
            p_payload = {
                "token": p_token,
                "user": p_user,
                "title": "Omniscrobble Weekly Digest",
                "message": plain_text,
                "priority": Config.PUSHOVER_PRIORITY,
            }
            tasks.append(("pushover", http.post("https://api.pushover.net/1/messages.json", data=p_payload)))
            dispatched.append("pushover")

        # 5. Gotify
        g_url = notifier._get_gotify_url()
        g_token = notifier._get_gotify_token()
        if g_url and g_token:
            plain_text = self.format_plain_digest(stats)
            g_payload = {
                "title": "Omniscrobble Weekly Activity Digest",
                "message": plain_text,
                "priority": notifier._get_gotify_priority(),
                "extras": {"client::display": {"contentType": "text/markdown"}},
            }
            headers = {"X-Gotify-Key": g_token, "Content-Type": "application/json"}
            tasks.append(("gotify", http.post(f"{g_url}/message", json=g_payload, headers=headers)))
            dispatched.append("gotify")

        # 6. Matrix
        m_hs = notifier._get_matrix_homeserver_url()
        m_tok = notifier._get_matrix_access_token()
        m_rm = notifier._get_matrix_room_id()
        if m_hs and m_tok and m_rm:
            txn_id = f"digest_{int(time.time() * 1000)}"
            encoded_room = urllib.parse.quote(m_rm, safe="") if hasattr(urllib, "parse") else m_rm
            m_url = f"{m_hs}//_matrix/client/v3/rooms/{encoded_room}/send/m.room.message/{txn_id}".replace("///", "/")
            html_text = self.format_html_digest(stats)
            plain_text = self.format_plain_digest(stats)
            m_payload = {
                "msgtype": "m.text",
                "body": plain_text,
                "format": "org.matrix.custom.html",
                "formatted_body": html_text,
            }
            headers = {"Authorization": f"Bearer {m_tok}", "Content-Type": "application/json"}
            tasks.append(("matrix", http.put(m_url, json=m_payload, headers=headers)))
            dispatched.append("matrix")

        if not tasks:
            return {
                "status": "warning",
                "success": False,
                "message": "No notification channels configured for digest delivery.",
                "stats": stats,
            }

        results = await asyncio.gather(*(t[1] for t in tasks), return_exceptions=True)
        successful_channels = []
        for idx, res in enumerate(results):
            ch_name = tasks[idx][0]
            if isinstance(res, httpx.Response) and 200 <= res.status_code < 300:
                successful_channels.append(ch_name)
            elif not isinstance(res, Exception):
                logger.warning(f"Digest channel {ch_name} returned status {getattr(res, 'status_code', 'unknown')}")

        self._last_digest_sent = time.time()
        return {
            "status": "success",
            "success": True,
            "stats": stats,
            "dispatched_channels": successful_channels,
            "all_channels": dispatched,
            "message": f"Weekly digest dispatched to {len(successful_channels)}/{len(dispatched)} channel(s).",
        }


digest_mgr = DigestManager()
