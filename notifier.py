import asyncio
import datetime
import html
import logging
from typing import Any, Optional
import httpx

from config import Config
from plex_parser import ParsedMedia

logger = logging.getLogger("notifier")

DISCORD_COLOR_SCROBBLE = 0xED1C24  # Trakt Red
DISCORD_COLOR_RATE = 0xF5A623      # Gold


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
    """Resolve direct Trakt search or web URL based on available external IDs."""
    if media.ids.get("imdb"):
        return f"https://trakt.tv/search/imdb/{media.ids['imdb']}"
    if media.ids.get("tmdb"):
        return f"https://trakt.tv/search/tmdb/{media.ids['tmdb']}"
    if media.ids.get("tvdb"):
        return f"https://trakt.tv/search/tvdb/{media.ids['tvdb']}"
    return "https://trakt.tv"


class Notifier:
    """Handles dispatching outgoing webhook notifications to Discord and Telegram."""

    def __init__(self, config: type[Config] = Config):
        self.config = config
        self._http_client: Optional[httpx.AsyncClient] = None

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
            "notify_on_scrobble": self.config.NOTIFY_ON_SCROBBLE,
            "notify_on_rate": self.config.NOTIFY_ON_RATE,
        }

    def build_discord_payload(self, media: ParsedMedia, action: str) -> dict[str, Any]:
        """Constructs a Discord rich embed notification payload."""
        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)
        type_str = media.media_type.capitalize()

        if action == "rate":
            rating_val = media.rating or 10
            color = DISCORD_COLOR_RATE
            description = f"Rated **{rating_val}/10** on Trakt"
            fields = [
                {"name": "Rating", "value": f"⭐ {rating_val}/10", "inline": True},
                {"name": "User", "value": media.username, "inline": True},
                {"name": "Type", "value": type_str, "inline": True},
            ]
        else:
            color = DISCORD_COLOR_SCROBBLE
            progress_val = f"{media.progress:.1f}%" if media.progress else "100.0%"
            description = f"Scrobbled to Trakt ({progress_val} watched)"
            fields = [
                {"name": "Status", "value": "Watched", "inline": True},
                {"name": "Progress", "value": progress_val, "inline": True},
                {"name": "User", "value": media.username, "inline": True},
                {"name": "Type", "value": type_str, "inline": True},
            ]

        embed = {
            "title": title_str,
            "url": trakt_url,
            "color": color,
            "description": description,
            "fields": fields,
            "footer": {
                "text": f"Plex Trakt Webhook • {media.username}"
            },
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        return {
            "username": "Plex Trakt Webhook",
            "avatar_url": "https://walter-2.trakt.tv/assets/logos/logomark.square.gradient-c38a2e5d93e1a7428800244cf5308630dbda3a03bf5bf7c858b9fdf5bacc3710.png",
            "embeds": [embed],
        }

    def build_telegram_payload(self, media: ParsedMedia, action: str) -> dict[str, Any]:
        """Constructs a Telegram HTML formatted message payload."""
        title_str = html.escape(format_media_title(media))
        user_str = html.escape(media.username)
        trakt_url = get_trakt_url(media)

        if action == "rate":
            rating_val = media.rating or 10
            text = (
                f"⭐ <b>Rated on Trakt</b>\n\n"
                f"🎬 <b>{title_str}</b>\n"
                f"👤 <b>User:</b> <code>{user_str}</code>\n"
                f"⭐ <b>Rating:</b> <b>{rating_val}/10</b>\n"
                f"🔗 <a href=\"{trakt_url}\">View on Trakt</a>"
            )
        else:
            progress_val = f"{media.progress:.1f}%" if media.progress else "100.0%"
            text = (
                f"🎬 <b>Scrobbled to Trakt</b>\n\n"
                f"🍿 <b>{title_str}</b>\n"
                f"👤 <b>User:</b> <code>{user_str}</code>\n"
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
        client: Optional[httpx.AsyncClient] = None
    ) -> bool:
        """Sends a rich embed notification to the configured Discord webhook URL."""
        webhook_url = self.config.DISCORD_WEBHOOK_URL
        if not webhook_url:
            return False

        payload = self.build_discord_payload(media, action)
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
        client: Optional[httpx.AsyncClient] = None
    ) -> bool:
        """Sends a formatted HTML notification message via Telegram Bot API."""
        bot_token = self.config.TELEGRAM_BOT_TOKEN
        chat_id = self.config.TELEGRAM_CHAT_ID
        if not bot_token or not chat_id:
            return False

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = self.build_telegram_payload(media, action)
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

    async def dispatch(
        self,
        media: ParsedMedia,
        action: str,
        client: Optional[httpx.AsyncClient] = None
    ) -> None:
        """Dispatches notifications across all enabled channels concurrently."""
        # Check action filters
        if action in ("mark_watched", "scrobble_stop"):
            if not self.config.NOTIFY_ON_SCROBBLE:
                return
        elif action == "rate":
            if not self.config.NOTIFY_ON_RATE:
                return
        else:
            return

        tasks = []
        if self.config.DISCORD_WEBHOOK_URL:
            tasks.append(self.send_discord(media, action, client=client))
        if self.config.TELEGRAM_BOT_TOKEN and self.config.TELEGRAM_CHAT_ID:
            tasks.append(self.send_telegram(media, action, client=client))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


notifier = Notifier()
