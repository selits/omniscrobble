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

try:
    from app.services.settings_manager import settings_mgr
except ImportError:
    try:
        from services.settings_manager import settings_mgr
    except ImportError:
        settings_mgr = None

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

    @staticmethod
    def _is_masked(val: Any) -> bool:
        if not val:
            return False
        s = str(val).strip()
        return s.startswith("••••") or s.startswith("●●●●") or "••••" in s

    def _get_setting(self, key: str, fallback_config_attr: str, default: Any = "") -> Any:
        """Retrieve dynamic runtime notification setting with fallback to Config."""
        if settings_mgr is not None:
            custom = settings_mgr.get_custom_notifications()
            if key in custom and custom[key] is not None:
                val = custom[key]
                if str(val).strip():
                    return val
        return getattr(self.config, fallback_config_attr, default)

    def _get_discord_url(self) -> str:
        return self._get_setting("discord_webhook_url", "DISCORD_WEBHOOK_URL", "")

    def _get_telegram_token(self) -> str:
        return self._get_setting("telegram_bot_token", "TELEGRAM_BOT_TOKEN", "")

    def _get_telegram_chat_id(self) -> str:
        return self._get_setting("telegram_chat_id", "TELEGRAM_CHAT_ID", "")

    def _get_ntfy_url(self) -> str:
        return self._get_setting("ntfy_url", "NTFY_URL", "")

    def _get_ntfy_auth_token(self) -> str:
        return self._get_setting("ntfy_auth_token", "NTFY_AUTH_TOKEN", "")

    def _get_pushover_user_key(self) -> str:
        return self._get_setting("pushover_user_key", "PUSHOVER_USER_KEY", "")

    def _get_pushover_api_token(self) -> str:
        return self._get_setting("pushover_api_token", "PUSHOVER_API_TOKEN", "")

    def _is_event_enabled(self, key: str, fallback_config_attr: str, default: bool = True) -> bool:
        """Checks if a notification event type is enabled in runtime settings or Config."""
        if settings_mgr is not None:
            custom = settings_mgr.get_custom_notifications()
            if key in custom and custom[key] is not None:
                return bool(custom[key])
        return bool(getattr(self.config, fallback_config_attr, default))

    def get_status(self) -> dict[str, Any]:
        """Returns the current activation state for configured notification channels."""
        return {
            "discord": bool(self._get_discord_url()),
            "telegram": bool(self._get_telegram_token() and self._get_telegram_chat_id()),
            "ntfy": bool(self._get_ntfy_url()),
            "pushover": bool(self._get_pushover_user_key() and self._get_pushover_api_token()),
            "notify_on_scrobble": self._is_event_enabled("notify_on_scrobble", "NOTIFY_ON_SCROBBLE", True),
            "notify_on_rate": self._is_event_enabled("notify_on_rate", "NOTIFY_ON_RATE", True),
            "notify_on_collection": self._is_event_enabled("notify_on_collection", "NOTIFY_ON_COLLECTION", True),
            "notify_on_failure": self._is_event_enabled("notify_on_failure", "NOTIFY_ON_FAILURE", True),
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
            "chat_id": self._get_telegram_chat_id(),
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
        webhook_url_override: Optional[str] = None,
    ) -> bool:
        """Sends a rich embed notification to the configured Discord webhook URL."""
        webhook_url = webhook_url_override or self._get_discord_url()
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
        bot_token_override: Optional[str] = None,
        chat_id_override: Optional[str] = None,
    ) -> bool:
        """Sends a formatted HTML notification message via Telegram Bot API."""
        bot_token = bot_token_override or self._get_telegram_token()
        chat_id = chat_id_override or self._get_telegram_chat_id()
        if not bot_token or not chat_id:
            return False

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = self.build_telegram_payload(
            media, action, cowatch_partner=cowatch_partner, trackers=trackers
        )
        payload["chat_id"] = chat_id
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
        url_override: Optional[str] = None,
    ) -> bool:
        """Sends a push notification via Ntfy."""
        url = url_override or self._get_ntfy_url()
        if not url:
            return False

        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)
        headers: dict[str, str] = {
            "Title": f"Trakt: {title_str}",
            "Click": trakt_url,
            "Priority": self.config.NTFY_PRIORITY or "default",
        }
        ntfy_token = self._get_ntfy_auth_token()
        if ntfy_token:
            headers["Authorization"] = f"Bearer {ntfy_token}"

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
        user_key_override: Optional[str] = None,
        api_token_override: Optional[str] = None,
    ) -> bool:
        """Sends a push notification via Pushover API."""
        user_key = user_key_override or self._get_pushover_user_key()
        api_token = api_token_override or self._get_pushover_api_token()
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

    def _prune_failure_cache(self, now: float) -> None:
        """Prune entries from failure deduplication cache older than TTL (1800s)."""
        expired = [k for k, ts in self._failure_cache.items() if now - ts >= 1800.0]
        for k in expired:
            del self._failure_cache[k]

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
        if not self._is_event_enabled("notify_on_failure", "NOTIFY_ON_FAILURE", True):
            return False

        # TTL cache: deduplicate alerts for 30 minutes to prevent spam
        now = time.time()
        self._prune_failure_cache(now)
        title = media.title or "Unknown"
        cache_key = f"{title}:{failed_tracker.lower()}:{str(error_msg)[:40]}"
        last_sent = self._failure_cache.get(cache_key, 0.0)
        if now - last_sent < 1800.0:
            logger.debug(f"Suppressing duplicate failure alert for {cache_key}")
            return False

        self._failure_cache[cache_key] = now
        webhook_url = self._get_discord_url()
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
            if not self._is_event_enabled("notify_on_scrobble", "NOTIFY_ON_SCROBBLE", True):
                return
        elif action == "rate":
            if not self._is_event_enabled("notify_on_rate", "NOTIFY_ON_RATE", True):
                return
        elif action == "collection":
            if not self._is_event_enabled("notify_on_collection", "NOTIFY_ON_COLLECTION", True):
                return
        elif action == "arr_add":
            if not getattr(self.config, "ARR_NOTIFY_ON_ADD", True):
                return
        else:
            return

        tasks = []
        if self._get_discord_url():
            tasks.append(
                self.send_discord(
                    media, action, client=client, cowatch_partner=cowatch_partner, trackers=trackers
                )
            )
        if self._get_telegram_token() and self._get_telegram_chat_id():
            tasks.append(
                self.send_telegram(
                    media, action, client=client, cowatch_partner=cowatch_partner, trackers=trackers
                )
            )
        if self._get_ntfy_url():
            tasks.append(
                self.send_ntfy(
                    media, action, client=client, cowatch_partner=cowatch_partner, trackers=trackers
                )
            )
        if self._get_pushover_user_key() and self._get_pushover_api_token():
            tasks.append(
                self.send_pushover(
                    media, action, client=client, cowatch_partner=cowatch_partner, trackers=trackers
                )
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_test_notification(
        self,
        channel: str,
        discord_webhook_url: Optional[str] = None,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        ntfy_url: Optional[str] = None,
        ntfy_auth_token: Optional[str] = None,
        pushover_user_key: Optional[str] = None,
        pushover_api_token: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> tuple[bool, str]:
        """Sends an immediate test alert to verify channel connectivity."""
        ch = str(channel).strip().lower()
        http = client or self.get_client()

        if ch == "discord":
            url = discord_webhook_url if (discord_webhook_url and not self._is_masked(discord_webhook_url)) else self._get_discord_url()
            if not url:
                return False, "Discord Webhook URL is not configured."
            payload = {
                "embeds": [
                    {
                        "title": "🔔 Omniscrobble Test Notification",
                        "description": "Your Discord webhook notification channel is connected successfully!",
                        "color": DISCORD_COLOR_SCROBBLE,
                        "fields": [
                            {"name": "Status", "value": "✅ Online", "inline": True},
                            {"name": "Service", "value": "Omniscrobble", "inline": True},
                            {"name": "Mode", "value": "Live Webhook", "inline": True},
                        ],
                        "footer": {"text": "Omniscrobble • Universal Scrobbler"},
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                ]
            }
            try:
                res = await http.post(url, json=payload)
                if 200 <= res.status_code < 300:
                    return True, "Discord test alert delivered successfully!"
                return False, f"Discord returned HTTP {res.status_code}: {res.text[:150]}"
            except Exception as e:
                return False, f"Failed to connect to Discord: {e}"

        elif ch == "telegram":
            bot_token = telegram_bot_token if (telegram_bot_token and not self._is_masked(telegram_bot_token)) else self._get_telegram_token()
            chat_id = telegram_chat_id if (telegram_chat_id and not self._is_masked(telegram_chat_id)) else self._get_telegram_chat_id()
            if not bot_token or not chat_id:
                return False, "Telegram Bot Token or Chat ID is not configured."
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": (
                    "🔔 <b>Omniscrobble Test Notification</b>\n\n"
                    "Your Telegram bot notification channel is connected successfully!\n\n"
                    "✅ <b>Status:</b> <code>Connected</code>\n"
                    "🚀 <b>Service:</b> <code>Omniscrobble</code>"
                ),
                "parse_mode": "HTML",
            }
            try:
                res = await http.post(url, json=payload)
                if 200 <= res.status_code < 300:
                    return True, "Telegram test message delivered successfully!"
                return False, f"Telegram returned HTTP {res.status_code}: {res.text[:150]}"
            except Exception as e:
                return False, f"Failed to connect to Telegram: {e}"

        elif ch == "ntfy":
            url = (ntfy_url if (ntfy_url and not self._is_masked(ntfy_url)) else self._get_ntfy_url()).strip()
            if not url:
                return False, "Ntfy Server URL is not configured."
            headers: dict[str, str] = {
                "Title": "Omniscrobble Test Notification",
                "Tags": "white_check_mark,bell,omniscrobble",
                "Priority": self.config.NTFY_PRIORITY or "default",
            }
            auth_token = ntfy_auth_token if (ntfy_auth_token and not self._is_masked(ntfy_auth_token)) else self._get_ntfy_auth_token()
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            msg = "Your Ntfy notification channel is connected and working successfully!"
            try:
                res = await http.post(url, content=msg.encode("utf-8"), headers=headers)
                if 200 <= res.status_code < 300:
                    return True, "Ntfy test notification delivered successfully!"
                return False, f"Ntfy returned HTTP {res.status_code}: {res.text[:150]}"
            except Exception as e:
                return False, f"Failed to connect to Ntfy: {e}"

        elif ch == "pushover":
            user_key = pushover_user_key if (pushover_user_key and not self._is_masked(pushover_user_key)) else self._get_pushover_user_key()
            api_token = pushover_api_token if (pushover_api_token and not self._is_masked(pushover_api_token)) else self._get_pushover_api_token()
            if not user_key or not api_token:
                return False, "Pushover User Key or API Token is not configured."
            url = "https://api.pushover.net/1/messages.json"
            data = {
                "token": api_token,
                "user": user_key,
                "title": "Omniscrobble Test Notification",
                "message": "Your Pushover notification channel is connected and working successfully!",
                "priority": self.config.PUSHOVER_PRIORITY,
            }
            try:
                res = await http.post(url, data=data)
                if 200 <= res.status_code < 300:
                    return True, "Pushover test notification delivered successfully!"
                return False, f"Pushover returned HTTP {res.status_code}: {res.text[:150]}"
            except Exception as e:
                return False, f"Failed to connect to Pushover: {e}"

        return False, f"Unknown notification channel '{channel}'."

    async def send_token_expiry_alert(
        self,
        service_name: str,
        message: str,
        reauth_url: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> bool:
        """Dispatches proactive token expiration warning across configured channels with 24h throttling."""
        cache_key = f"token_expiry_{service_name.lower().replace(' ', '_')}"
        now = time.time()
        last_sent = self._failure_cache.get(cache_key, 0)
        if now - last_sent < 86400:
            logger.debug("Token expiry alert for %s throttled (sent %ds ago)", service_name, int(now - last_sent))
            return False

        http = client or self.get_client()
        delivered = False

        # 1. Discord
        discord_url = self._get_discord_url()
        if discord_url:
            payload = {
                "embeds": [
                    {
                        "title": f"⚠️ Token Expiration Alert: {service_name}",
                        "description": message,
                        "color": 0xF59E0B,  # Amber/Orange warning
                        "fields": [
                            {"name": "Service", "value": service_name, "inline": True},
                            {"name": "Action Required", "value": f"[Click here to re-authorize]({reauth_url})" if reauth_url.startswith("http") else f"`{reauth_url}`", "inline": False},
                        ],
                        "footer": {"text": "Omniscrobble • Security & Token Health"},
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                ]
            }
            try:
                r = await http.post(discord_url, json=payload)
                if 200 <= r.status_code < 300:
                    delivered = True
            except Exception as e:
                logger.error("Failed to deliver Discord token alert: %s", e)

        # 2. Telegram
        t_token = self._get_telegram_token()
        t_chat = self._get_telegram_chat_id()
        if t_token and t_chat:
            tg_url = f"https://api.telegram.org/bot{t_token}/sendMessage"
            payload = {
                "chat_id": t_chat,
                "text": f"⚠️ <b>Omniscrobble Token Expiration Warning</b>\n\n<b>Service:</b> {service_name}\n<b>Details:</b> {message}\n\n👉 <b>Re-authorize:</b> {reauth_url}",
                "parse_mode": "HTML",
            }
            try:
                r = await http.post(tg_url, json=payload)
                if 200 <= r.status_code < 300:
                    delivered = True
            except Exception as e:
                logger.error("Failed to deliver Telegram token alert: %s", e)

        # 3. Ntfy
        ntfy_url = self._get_ntfy_url()
        if ntfy_url:
            headers = {
                "Title": f"Token Expiry Alert: {service_name}",
                "Priority": "high",
                "Tags": "warning,key,omniscrobble",
                "Click": reauth_url if reauth_url.startswith("http") else "",
            }
            auth_token = self._get_ntfy_auth_token()
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            try:
                r = await http.post(ntfy_url, content=f"{message}\nAction: {reauth_url}".encode("utf-8"), headers=headers)
                if 200 <= r.status_code < 300:
                    delivered = True
            except Exception as e:
                logger.error("Failed to deliver Ntfy token alert: %s", e)

        # 4. Pushover
        p_user = self._get_pushover_user_key()
        p_token = self._get_pushover_api_token()
        if p_user and p_token:
            data = {
                "token": p_token,
                "user": p_user,
                "title": f"Omniscrobble Token Alert: {service_name}",
                "message": message,
                "url": reauth_url if reauth_url.startswith("http") else "",
                "url_title": f"Re-authorize {service_name}",
                "priority": 1,
            }
            try:
                r = await http.post("https://api.pushover.net/1/messages.json", data=data)
                if 200 <= r.status_code < 300:
                    delivered = True
            except Exception as e:
                logger.error("Failed to deliver Pushover token alert: %s", e)

        if delivered:
            self._failure_cache[cache_key] = now
            logger.info("Dispatched token expiration alert for %s", service_name)
        return delivered


notifier = Notifier()
