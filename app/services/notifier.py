import asyncio
import datetime
import html
import logging
import time
import urllib.parse
from typing import Any, Optional
import httpx

try:
    from app.config import Config
    from app.plex_parser import ParsedMedia
except ImportError:
    from config import Config
    from plex_parser import ParsedMedia

logger = logging.getLogger("notifier")

DISCORD_COLOR_SCROBBLE = 0xED1C24   # Trakt Red / Primary
DISCORD_COLOR_RATE = 0xF5A623       # Gold
DISCORD_COLOR_COLLECTION = 0x00A8E8  # Cyan / Collection Blue
DISCORD_COLOR_ARR = 0x2ECC71         # Emerald Green / Acquisition
DISCORD_COLOR_COWATCH = 0x6366F1     # Indigo / Co-Watch Purple
DISCORD_COLOR_FAILURE = 0xE74C3C     # Crimson Red / Failure Alert


def format_media_title(media: ParsedMedia) -> str:
    """Format a clean, descriptive title for notifications."""
    if media.media_type == "episode":
        season_num = media.season if media.season is not None else 1
        ep_num = media.episode if media.episode is not None else 1
        show = media.show_title or media.title
        ep_code = f"S{season_num:02d}E{ep_num:02d}"
        if media.title and media.title != show:
            return f"{show} {ep_code} - {media.title}"
        return f"{show} {ep_code}"
    else:
        year_str = f" ({media.year})" if media.year else ""
        return f"{media.title}{year_str}"


def get_trakt_url(media: ParsedMedia) -> str:
    """Resolve direct Trakt search web URL based on media title or show title."""
    if media.media_type == "episode":
        query = media.show_title or media.title
    else:
        query = media.title

    if query:
        return f"https://trakt.tv/search?q={urllib.parse.quote_plus(query)}"
    return "https://trakt.tv"


class Notifier:
    """Handles dispatching outgoing webhook notifications across Discord, Telegram, Ntfy, and Pushover."""

    def __init__(self, config: type[Config] = Config):
        self.config = config
        self._http_client: Optional[httpx.AsyncClient] = None
        self._failure_cache: dict[str, float] = {}

    def get_client(self) -> httpx.AsyncClient:
        """Returns or instantiates a reusable httpx.AsyncClient."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def close(self) -> None:
        """Closes the underlying HTTP client session cleanly."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def get_status(self) -> dict[str, Any]:
        """Returns the current activation state for configured notification channels."""
        return {
            "discord": bool(self.config.DISCORD_WEBHOOK_URL),
            "telegram": bool(self.config.TELEGRAM_BOT_TOKEN and self.config.TELEGRAM_CHAT_ID),
            "ntfy": bool(self.config.NTFY_URL),
            "pushover": bool(self.config.PUSHOVER_USER_KEY and self.config.PUSHOVER_API_TOKEN),
            "notify_on_scrobble": self.config.NOTIFY_ON_SCROBBLE,
            "notify_on_rate": self.config.NOTIFY_ON_RATE,
            "notify_on_collection": self.config.NOTIFY_ON_COLLECTION,
            "notify_on_failure": getattr(self.config, "NOTIFY_ON_FAILURE", True),
        }

    def build_discord_payload(
        self,
        media: ParsedMedia,
        action: str,
        cowatch_partner: Optional[str] = None,
        trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Constructs a Discord rich embed notification payload."""
        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)
        type_str = media.media_type.capitalize()

        if action == "rate":
            rating_val = media.rating or 10
            color = DISCORD_COLOR_RATE
            description = f"Rated **{rating_val}/10** on Trackers"
            fields = [
                {"name": "Rating", "value": f"⭐ {rating_val}/10", "inline": True},
                {"name": "User", "value": media.username, "inline": True},
                {"name": "Type", "value": type_str, "inline": True},
            ]
        elif action == "collection":
            color = DISCORD_COLOR_COLLECTION
            description = "Added to Media Collection"
            fields = [
                {"name": "Status", "value": "Collected", "inline": True},
                {"name": "User", "value": media.username or "Media Server", "inline": True},
                {"name": "Type", "value": type_str, "inline": True},
            ]
            if media.video_resolution or media.audio_codec:
                specs = " • ".join(filter(None, [media.video_resolution, media.audio_codec]))
                fields.append({"name": "Specs", "value": specs.upper(), "inline": True})
        elif action == "arr_add":
            color = DISCORD_COLOR_ARR
            description = f"Added to **{media.username or 'Media Downloader'}** from Trakt Watchlist"
            fields = [
                {"name": "Status", "value": "📥 Monitored & Added", "inline": True},
                {"name": "App", "value": media.username or "Arr", "inline": True},
                {"name": "Type", "value": type_str, "inline": True},
            ]
        else:
            color = DISCORD_COLOR_COWATCH if cowatch_partner else DISCORD_COLOR_SCROBBLE
            progress_val = f"{media.progress:.1f}%" if media.progress is not None else "100.0%"
            scrobble_label = "Co-Watch Dual Scrobble" if cowatch_partner else "Scrobbled"
            description = f"{scrobble_label} ({progress_val} watched)"
            fields = [
                {"name": "Status", "value": "Watched", "inline": True},
                {"name": "Progress", "value": progress_val, "inline": True},
                {"name": "User", "value": media.username, "inline": True},
                {"name": "Type", "value": type_str, "inline": True},
            ]

        if cowatch_partner:
            fields.append({"name": "👥 Co-Watched With", "value": f"Watching with @{cowatch_partner}", "inline": True})

        if trackers:
            formatted_trackers = ", ".join(t.capitalize() for t in trackers)
            fields.append({"name": "📡 Synced Trackers", "value": formatted_trackers, "inline": True})

        footer_text = f"Omniscrobble • {media.username or 'Server'}"
        if cowatch_partner:
            footer_text += f" & @{cowatch_partner}"

        embed = {
            "title": title_str,
            "url": trakt_url,
            "color": color,
            "description": description,
            "fields": fields,
            "footer": {
                "text": footer_text,
            },
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        return {
            "username": "Omniscrobble",
            "avatar_url": "https://walter-2.trakt.tv/assets/logos/logomark.square.gradient-c38a2e5d93e1a7428800244cf5308630dbda3a03bf5bf7c858b9fdf5bacc3710.png",
            "embeds": [embed],
        }

    def build_failure_discord_payload(
        self,
        media: ParsedMedia,
        failed_tracker: str,
        error_msg: str,
        user: str = "",
        is_retryable: bool = False,
    ) -> dict[str, Any]:
        """Constructs a Discord failure alert notification payload."""
        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)
        status_desc = "Queued in SQLite for retry" if is_retryable else "Action required: please check credentials"

        embed = {
            "title": f"⚠️ Scrobble Failed on {failed_tracker.capitalize()}",
            "url": trakt_url,
            "color": DISCORD_COLOR_FAILURE,
            "description": f"Could not mark **{title_str}** as viewed on **{failed_tracker.capitalize()}**.",
            "fields": [
                {"name": "Media", "value": title_str, "inline": True},
                {"name": "User", "value": user or media.username or "Admin", "inline": True},
                {"name": "Tracker", "value": failed_tracker.capitalize(), "inline": True},
                {"name": "Reason", "value": f"`{error_msg}`", "inline": False},
                {"name": "Status", "value": status_desc, "inline": False},
            ],
            "footer": {
                "text": "Omniscrobble • Multi-Tracker Alert",
            },
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        return {
            "username": "Omniscrobble Alerts",
            "embeds": [embed],
        }

    def build_telegram_payload(
        self,
        media: ParsedMedia,
        action: str,
        cowatch_partner: Optional[str] = None,
        trackers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Constructs a Telegram HTML formatted message payload."""
        title_str = html.escape(format_media_title(media))
        user_str = html.escape(media.username or "Server")
        trakt_url = get_trakt_url(media)

        cw_str = ""
        if cowatch_partner:
            cw_str = f"👥 <b>Co-Watched With:</b> <code>@{html.escape(cowatch_partner)}</code>\n"

        trackers_str = ""
        if trackers:
            trackers_str = f"📡 <b>Trackers:</b> <code>{html.escape(', '.join(t.capitalize() for t in trackers))}</code>\n"

        if action == "rate":
            rating_val = media.rating or 10
            text = (
                f"⭐ <b>Rated Media</b>\n\n"
                f"🎬 <b>{title_str}</b>\n"
                f"👤 <b>User:</b> <code>{user_str}</code>\n"
                f"{cw_str}"
                f"{trackers_str}"
                f"⭐ <b>Rating:</b> <b>{rating_val}/10</b>\n"
                f"🔗 <a href=\"{trakt_url}\">View on Trakt</a>"
            )
        elif action == "collection":
            specs_str = ""
            if media.video_resolution or media.audio_codec:
                specs_str = f"💿 <b>Specs:</b> <code>{html.escape((media.video_resolution or '').upper())} • {html.escape((media.audio_codec or '').upper())}</code>\n"
            text = (
                f"📥 <b>Added to Media Collection</b>\n\n"
                f"🎬 <b>{title_str}</b>\n"
                f"👤 <b>User:</b> <code>{user_str}</code>\n"
                f"{specs_str}"
                f"🔗 <a href=\"{trakt_url}\">View on Trakt</a>"
            )
        elif action == "arr_add":
            app_str = html.escape(media.username or "Arr")
            text = (
                f"📥 <b>Added to {app_str}</b>\n\n"
                f"🎬 <b>{title_str}</b>\n"
                f"👤 <b>Source:</b> <code>Trakt Watchlist</code>\n"
                f"🔗 <a href=\"{trakt_url}\">View on Trakt</a>"
            )
        else:
            progress_val = f"{media.progress:.1f}%" if media.progress is not None else "100.0%"
            scrobble_head = "👥 <b>Co-Watch Scrobbled</b>" if cowatch_partner else "🎬 <b>Scrobbled to Trakt</b>"
            text = (
                f"{scrobble_head}\n\n"
                f"🍿 <b>{title_str}</b>\n"
                f"👤 <b>User:</b> <code>{user_str}</code>\n"
                f"{cw_str}"
                f"{trackers_str}"
                f"📊 <b>Progress:</b> <code>{progress_val}</code>\n"
                f"🔗 <a href=\"{trakt_url}\">View on Trakt</a>"
            )

        return {
            "chat_id": self.config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

    async def send_discord(
        self,
        media: ParsedMedia,
        action: str,
        client: Optional[httpx.AsyncClient] = None,
        cowatch_partner: Optional[str] = None,
        trackers: Optional[list[str]] = None,
    ) -> bool:
        """Sends a rich embed notification to the configured Discord webhook URL."""
        webhook_url = self.config.DISCORD_WEBHOOK_URL
        if not webhook_url:
            return False

        payload = self.build_discord_payload(
            media, action, cowatch_partner=cowatch_partner, trackers=trackers
        )
        http = client or self.get_client()

        try:
            res = await http.post(webhook_url, json=payload)
            if 200 <= res.status_code < 300:
                logger.debug(f"Discord notification sent successfully for {media.title}")
                return True
            else:
                logger.warning(
                    f"Discord webhook failed with status {res.status_code}: {res.text}"
                )
                return False
        except Exception as e:
            logger.warning(f"Failed to deliver Discord notification: {e}")
            return False

    async def send_telegram(
        self,
        media: ParsedMedia,
        action: str,
        client: Optional[httpx.AsyncClient] = None,
        cowatch_partner: Optional[str] = None,
        trackers: Optional[list[str]] = None,
    ) -> bool:
        """Sends a formatted HTML notification message via Telegram Bot API."""
        bot_token = self.config.TELEGRAM_BOT_TOKEN
        chat_id = self.config.TELEGRAM_CHAT_ID
        if not bot_token or not chat_id:
            return False

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = self.build_telegram_payload(
            media, action, cowatch_partner=cowatch_partner, trackers=trackers
        )
        http = client or self.get_client()

        try:
            res = await http.post(url, json=payload)
            if 200 <= res.status_code < 300:
                logger.debug(f"Telegram notification sent successfully for {media.title}")
                return True
            else:
                logger.warning(
                    f"Telegram notification failed with status {res.status_code}: {res.text}"
                )
                return False
        except Exception as e:
            logger.warning(f"Failed to deliver Telegram notification: {e}")
            return False

    async def send_ntfy(
        self,
        media: ParsedMedia,
        action: str,
        client: Optional[httpx.AsyncClient] = None,
        cowatch_partner: Optional[str] = None,
        trackers: Optional[list[str]] = None,
    ) -> bool:
        """Sends a push notification via Ntfy."""
        url = self.config.NTFY_URL
        if not url:
            return False

        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)
        headers: dict[str, str] = {
            "Title": f"Trakt: {title_str}",
            "Click": trakt_url,
            "Priority": self.config.NTFY_PRIORITY or "default",
        }
        if self.config.NTFY_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {self.config.NTFY_AUTH_TOKEN}"

        cw_tag = f" with @{cowatch_partner}" if cowatch_partner else ""
        if action == "rate":
            msg = f"Rated {media.rating or 10}/10 on Trackers by {media.username}{cw_tag}"
            headers["Tags"] = "star,omniscrobble"
        elif action == "collection":
            msg = f"Added {title_str} to Media Collection"
            headers["Tags"] = "cd,package,omniscrobble"
        elif action == "arr_add":
            msg = f"Added {title_str} to {media.username or 'Arr'} from Watchlist"
            headers["Tags"] = "inbox_tray,movie_camera"
        else:
            msg = f"Scrobbled {title_str} ({media.progress:.1f}% watched) by {media.username}{cw_tag}"
            headers["Tags"] = "movie_camera,popcorn,omniscrobble"

        http = client or self.get_client()
        try:
            res = await http.post(url, content=msg.encode("utf-8"), headers=headers)
            return 200 <= res.status_code < 300
        except Exception as e:
            logger.warning(f"Failed to deliver Ntfy notification: {e}")
            return False

    async def send_pushover(
        self,
        media: ParsedMedia,
        action: str,
        client: Optional[httpx.AsyncClient] = None,
        cowatch_partner: Optional[str] = None,
        trackers: Optional[list[str]] = None,
    ) -> bool:
        """Sends a push notification via Pushover API."""
        user_key = self.config.PUSHOVER_USER_KEY
        api_token = self.config.PUSHOVER_API_TOKEN
        if not user_key or not api_token:
            return False

        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)
        cw_tag = f" & @{cowatch_partner}" if cowatch_partner else ""

        if action == "rate":
            msg = f"⭐ Rated {media.rating or 10}/10 by {media.username}{cw_tag}"
        elif action == "collection":
            msg = f"📥 Added to Media Collection by {media.username or 'Server'}"
        elif action == "arr_add":
            msg = f"📥 Added {title_str} to {media.username or 'Arr'} from Watchlist"
        else:
            msg = f"🍿 Scrobbled ({media.progress:.1f}% watched) by {media.username}{cw_tag}"

        payload = {
            "token": api_token,
            "user": user_key,
            "title": f"Omniscrobble: {title_str}",
            "message": msg,
            "url": trakt_url,
            "url_title": "View on Trakt",
            "priority": self.config.PUSHOVER_PRIORITY,
        }

        http = client or self.get_client()
        try:
            res = await http.post("https://api.pushover.net/1/messages.json", data=payload)
            return 200 <= res.status_code < 300
        except Exception as e:
            logger.warning(f"Failed to deliver Pushover notification: {e}")
            return False

    async def send_failure_alert(
        self,
        media: ParsedMedia,
        failed_tracker: str,
        error_msg: str,
        user: str = "",
        is_retryable: bool = False,
        client: Optional[httpx.AsyncClient] = None,
    ) -> bool:
        """Sends a failure alert notification with spam-prevention deduplication."""
        if not getattr(self.config, "NOTIFY_ON_FAILURE", True):
            return False

        # TTL cache: deduplicate alerts for 30 minutes to prevent spam
        now = time.time()
        title = media.title or "Unknown"
        cache_key = f"{title}:{failed_tracker.lower()}:{str(error_msg)[:40]}"
        last_sent = self._failure_cache.get(cache_key, 0.0)
        if now - last_sent < 1800.0:
            logger.debug(f"Suppressing duplicate failure alert for {cache_key}")
            return False

        self._failure_cache[cache_key] = now
        webhook_url = self.config.DISCORD_WEBHOOK_URL
        if not webhook_url:
            return False

        payload = self.build_failure_discord_payload(
            media=media,
            failed_tracker=failed_tracker,
            error_msg=error_msg,
            user=user,
            is_retryable=is_retryable,
        )
        http = client or self.get_client()
        try:
            res = await http.post(webhook_url, json=payload)
            return 200 <= res.status_code < 300
        except Exception as e:
            logger.warning(f"Failed to deliver failure alert notification: {e}")
            return False

    async def dispatch(
        self,
        media: ParsedMedia,
        action: str,
        client: Optional[httpx.AsyncClient] = None,
        cowatch_partner: Optional[str] = None,
        trackers: Optional[list[str]] = None,
    ) -> None:
        """Dispatches notifications across all enabled channels concurrently."""
        if action in ("mark_watched", "scrobble_stop"):
            if not self.config.NOTIFY_ON_SCROBBLE:
                return
        elif action == "rate":
            if not self.config.NOTIFY_ON_RATE:
                return
        elif action == "collection":
            if not self.config.NOTIFY_ON_COLLECTION:
                return
        elif action == "arr_add":
            if not getattr(self.config, "ARR_NOTIFY_ON_ADD", True):
                return
        else:
            return

        tasks = []
        if self.config.DISCORD_WEBHOOK_URL:
            tasks.append(
                self.send_discord(
                    media, action, client=client, cowatch_partner=cowatch_partner, trackers=trackers
                )
            )
        if self.config.TELEGRAM_BOT_TOKEN and self.config.TELEGRAM_CHAT_ID:
            tasks.append(
                self.send_telegram(
                    media, action, client=client, cowatch_partner=cowatch_partner, trackers=trackers
                )
            )
        if self.config.NTFY_URL:
            tasks.append(
                self.send_ntfy(
                    media, action, client=client, cowatch_partner=cowatch_partner, trackers=trackers
                )
            )
        if self.config.PUSHOVER_USER_KEY and self.config.PUSHOVER_API_TOKEN:
            tasks.append(
                self.send_pushover(
                    media, action, client=client, cowatch_partner=cowatch_partner, trackers=trackers
                )
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


notifier = Notifier()
