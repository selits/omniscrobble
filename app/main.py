import asyncio
import datetime
from datetime import timezone
import html
import io
import json
import logging
import secrets
import time
import urllib.parse
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
import uvicorn
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


from app.config import Config
from app.clients.sonarr_client import (
    SonarrClient,
    parse_sonarr_webhook,
    parse_radarr_webhook,
)
from app.clients.trakt_client import TraktClient
from app.metrics import metrics_registry
from app.services.atomic_writer import atomic_write_json
from app.services.cowatch_manager import cowatch_mgr
household_mgr = cowatch_mgr
from app.services.household_manager import HouseholdManager
from app.services.notifier import notifier
from app.services.digest_manager import digest_mgr
from app.services.playback_manager import playback_mgr
from app.plex_parser import ParsedMedia, parse_plex_webhook
from app.jellyfin_parser import parse_jellyfin_webhook
from app.emby_parser import parse_emby_webhook
from app.services.queue_manager import QueueManager, process_queue
from app.services.user_manager import user_mgr
from app.services.demo_manager import demo_mgr
from app.services.log_manager import log_mgr
from app.clients.plex_api_client import PlexApiClient
from app.clients.anilist_client import AniListClient
from app.clients.kitsu_client import KitsuClient
from app.clients.letterboxd_client import LetterboxdClient
from app.clients.mal_client import MyAnimeListClient
from app.clients.mdblist_client import MDBListClient
from app.clients.overseerr_client import OverseerrClient
from app.clients.radarr_client import RadarrClient
from app.clients.serializd_client import SerializdClient
from app.clients.simkl_client import SimklClient
from app.clients.tmdb_client import TMDbClient
from app.services.anime_resolver import AnimeResolver
from app.services.multi_tracker import MultiTrackerManager
from app.services.loop_prevention import loop_prevention
from app.services.reverse_sync_manager import reverse_sync_mgr
from app.services.arr_bridge import arr_bridge
from app.services.cross_tracker_sync import CrossTrackerSyncManager
from app.services.settings_manager import settings_mgr
from app.services.cloud_sync_manager import cloud_sync_mgr
from app.services.webhook_debugger import webhook_debugger
from app.services.analytics_manager import analytics_mgr
from app.services.dashboard_renderer import (
    format_action_label,
    should_display_cowatch_badge,
    render_status_badge,
    dashboard_renderer,
)
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'
AUTH_LOCKED_HTML = (TEMPLATES_DIR / 'auth_locked.html').read_text(encoding='utf-8')
AUTH_HTML = (TEMPLATES_DIR / 'auth.html').read_text(encoding='utf-8')
AUTH_SIMKL_HTML = (TEMPLATES_DIR / 'auth_simkl.html').read_text(encoding='utf-8')
AUTH_ANILIST_HTML = (TEMPLATES_DIR / 'auth_anilist.html').read_text(encoding='utf-8')
AUTH_MAL_HTML = (TEMPLATES_DIR / 'auth_mal.html').read_text(encoding='utf-8')
DASHBOARD_HTML = (TEMPLATES_DIR / 'dashboard.html').read_text(encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("plex_trakt_scrobbler")

trakt = TraktClient(Config)
sonarr = SonarrClient()
radarr = RadarrClient()
simkl = SimklClient(Config)
anilist = AniListClient(Config)
mal = MyAnimeListClient(Config)
kitsu = KitsuClient(Config)
tmdb = TMDbClient(Config)
letterboxd = LetterboxdClient(Config)
serializd = SerializdClient(Config)
mdblist = MDBListClient(Config)

anime_resolver = AnimeResolver(
    Config,
    anilist_client=anilist,
    mal_client=mal,
    kitsu_client=kitsu,
)
multi_tracker = MultiTrackerManager(
    Config,
    simkl_client=simkl,
    anilist_client=anilist,
    mal_client=mal,
    kitsu_client=kitsu,
    tmdb_client=tmdb,
    letterboxd_client=letterboxd,
    serializd_client=serializd,
    mdblist_client=mdblist,
    anime_resolver=anime_resolver,
)
cross_tracker_sync = CrossTrackerSyncManager(trakt_client=trakt, simkl_client=simkl)
user_mgr.set_default_client(trakt)
reverse_sync_mgr.set_trakt_client(trakt)
arr_bridge.set_trakt_client(trakt)
queue_mgr = QueueManager(Config.QUEUE_DB_FILE)

SERVER_START_TIME = time.time()

STATS_FILE = Config.BASE_DIR / "data" / "stats.json"
EVENTS_FILE = Config.BASE_DIR / "data" / "events.json"


def load_scrobble_stats() -> dict[str, int]:
    """Load persistent scrobble counters from data/stats.json."""
    stats = {"total": 0, "movies": 0, "episodes": 0, "ratings": 0, "collections": 0, "watch_minutes": 0}
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k in stats:
                        stats[k] = int(data.get(k, 0))
                    # Upgrade path: stats.json predates watch_minutes, so estimate it from lifetime counts
                    if "watch_minutes" not in data and stats["total"] > 0:
                        stats["watch_minutes"] = stats["movies"] * 90 + stats["episodes"] * 35
        except Exception as e:
            logger.warning(f"Could not load stats from {STATS_FILE}: {e}")
    return stats


def save_scrobble_stats() -> None:
    """Persist scrobble counters to data/stats.json."""
    try:
        atomic_write_json(STATS_FILE, scrobble_stats)
    except Exception as e:
        logger.warning(f"Could not save stats to {STATS_FILE}: {e}")


# Persistent scrobble counter across restarts and upgrades
scrobble_stats: dict[str, int] = {"total": 0, "movies": 0, "episodes": 0, "ratings": 0, "collections": 0, "watch_minutes": 0}
scrobble_stats.update(load_scrobble_stats())


def record_watch_stat(media: ParsedMedia) -> None:
    """Increment scrobble counters and accumulate exact or estimated watch minutes."""
    scrobble_stats["total"] = scrobble_stats.get("total", 0) + 1
    if media.media_type == "movie":
        scrobble_stats["movies"] = scrobble_stats.get("movies", 0) + 1
        default_mins = 90
    else:
        scrobble_stats["episodes"] = scrobble_stats.get("episodes", 0) + 1
        default_mins = 35

    item_mins = default_mins
    if media.duration_ms and media.duration_ms > 0:
        item_mins = max(1, round(media.duration_ms / 60000.0))

    scrobble_stats["watch_minutes"] = scrobble_stats.get("watch_minutes", 0) + item_mins


def is_temporary_error(res: dict[str, Any]) -> bool:
    """Detect transient errors (5xx server errors, 429 rate limits, network timeouts)."""
    status = res.get("status")
    err = res.get("error")
    return (
        status in (500, 502, 503, 504, 429)
        or (isinstance(err, str) and any(x in err.lower() for x in ("connect", "timeout", "network", "service unavailable", "rate limit")))
    )


def get_uptime_str() -> str:
    """Return formatted server uptime (e.g. '2d 5h 12m' or '45m 10s')."""
    uptime_seconds = int(time.time() - SERVER_START_TIME)
    days, rem = divmod(uptime_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if not parts or (days == 0 and hours == 0):
        parts.append(f"{seconds}s")
    return " ".join(parts)


def mask_username(username: Optional[str]) -> str:
    """Mask a username for privacy (e.g. 'selits' -> 'se****', 'bob' -> 'b**', 'a' -> '*')."""
    if not username:
        return ""
    if len(username) <= 2:
        return username[0] + "*" * (len(username) - 1) if len(username) == 2 else "*"
    if len(username) <= 4:
        return username[:1] + "*" * (len(username) - 1)
    return username[:2] + "*" * (len(username) - 2)


def is_admin_request(request: Request) -> bool:
    """Check if the incoming request has administrative privileges.

    If WEBHOOK_SECRET is not configured, admin mode is granted by default.
    Otherwise, verifies against query param ?token=, x-webhook-secret header,
    or the admin_token HTTP-only cookie using timing-safe comparison.
    When authenticated via cookie on state-mutating requests (POST, DELETE, PUT, PATCH),
    validates CSRF protection (X-CSRF-Token header matching csrf_token cookie).
    """
    if not Config.WEBHOOK_SECRET:
        return True

    # 1. Query parameter (?token=...)
    token = request.query_params.get("token")
    if token and secrets.compare_digest(token, Config.WEBHOOK_SECRET):
        return True

    # 2. Request header (x-webhook-secret: ...)
    header_token = request.headers.get("x-webhook-secret")
    if header_token and secrets.compare_digest(header_token, Config.WEBHOOK_SECRET):
        return True

    # 3. Secure cookie (admin_token=...)
    cookie_token = request.cookies.get("admin_token")
    if cookie_token and secrets.compare_digest(cookie_token, Config.WEBHOOK_SECRET):
        # Enforce CSRF protection for cookie-authenticated mutating requests
        if request.method in ("POST", "DELETE", "PUT", "PATCH"):
            fetch_site = request.headers.get("sec-fetch-site")
            if fetch_site == "cross-site":
                logger.warning("CSRF check blocked cross-site mutating admin request from %s", request.client.host if request.client else "unknown")
                return False
            header_csrf = request.headers.get("x-csrf-token")
            cookie_csrf = request.cookies.get("csrf_token")
            if cookie_csrf:
                if not header_csrf or not secrets.compare_digest(header_csrf, cookie_csrf):
                    logger.warning("CSRF check failed (missing or mismatched token) on cookie-authenticated admin request from %s", request.client.host if request.client else "unknown")
                    return False
        return True

    return False


queue_worker_task: Optional[asyncio.Task] = None
reverse_sync_worker_task: Optional[asyncio.Task] = None
scrobble_heartbeat_worker_task: Optional[asyncio.Task] = None
weekly_digest_worker_task: Optional[asyncio.Task] = None


async def queue_worker_loop():
    """Background worker periodically checking and retrying offline queued events."""
    last_prune_time = 0.0
    while True:
        try:
            interval = max(5, Config.QUEUE_RETRY_INTERVAL)
            await asyncio.sleep(interval)
            now = time.time()
            if now - last_prune_time > 86400:  # Daily queue retention maintenance (90-day retention)
                try:
                    queue_mgr.prune_queue(days=90)
                except Exception as pe:
                    logger.warning(f"Error during scheduled offline queue pruning: {pe}")
                last_prune_time = now

            if queue_mgr.get_pending_count() > 0:
                logger.info("Background queue worker draining pending offline items...")
                await process_queue(trakt, queue_mgr, user_mgr=user_mgr)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in background queue retry worker: {e}")


reverse_sync_config_updated: asyncio.Event = asyncio.Event()


async def reverse_sync_worker_loop():
    """Background worker periodically executing reverse sync reconciliation if enabled."""
    logger.info("Reverse sync background worker started...")
    while True:
        try:
            recon = settings_mgr.get_reconciliation_settings(mask_token=False)
            interval_minutes = recon.get("interval_minutes", 0)
            enabled = recon.get("enabled", True)

            if enabled and interval_minutes > 0 and reverse_sync_mgr.is_configured():
                interval_seconds = max(60, interval_minutes * 60)
                try:
                    await asyncio.wait_for(reverse_sync_config_updated.wait(), timeout=interval_seconds)
                    reverse_sync_config_updated.clear()
                    continue
                except asyncio.TimeoutError:
                    pass

                if reverse_sync_mgr.is_configured() and not reverse_sync_mgr._is_syncing:
                    target_srv = recon.get("server_type", "plex")
                    logger.info("Periodic reverse sync worker: scanning for discrepancies on %s...", target_srv)
                    diff = await reverse_sync_mgr.scan_discrepancies(server=target_srv)
                    if diff:
                        dir_def = recon.get("direction_default", "trakt_to_server")
                        if dir_def in ("all", "trakt_to_plex"):
                            dir_def = "trakt_to_server"
                        logger.info(f"Periodic reverse sync worker: reconciling {len(diff)} items on {target_srv}...")
                        await reverse_sync_mgr.execute_reconciliation(direction=dir_def, server=target_srv)
            else:
                await reverse_sync_config_updated.wait()
                reverse_sync_config_updated.clear()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic reverse sync worker: {e}")
            await asyncio.sleep(5.0)


arr_watchlist_worker_task: Optional[asyncio.Task] = None
partner_refresh_worker_task: Optional[asyncio.Task] = None
background_cloud_sync_task: Optional[asyncio.Task] = None
token_monitor_task: Optional[asyncio.Task] = None


async def token_monitor_worker_loop():
    """Background worker periodically evaluating token validity and dispatching proactive health alerts."""
    from app.services.token_health_monitor import token_health_mgr
    # Initial pause after boot before first proactive cycle
    await asyncio.sleep(10.0)
    while True:
        try:
            await token_health_mgr.run_check_cycle(
                trakt_client=trakt,
                simkl_client=simkl,
                mal_client=mal,
                user_mgr=user_mgr,
            )
            # Evaluate every 6 hours
            await asyncio.sleep(21600.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in token monitor worker: {e}")
            await asyncio.sleep(60.0)


async def arr_watchlist_worker_loop():
    """Background worker periodically polling Trakt Watchlist and adding missing items to Sonarr/Radarr."""
    if not Config.AUTO_ADD_FROM_WATCHLIST or Config.ARR_WATCHLIST_INTERVAL <= 0:
        return
    interval_seconds = max(60, Config.ARR_WATCHLIST_INTERVAL)
    logger.info(f"Arr watchlist automation worker started (running every {interval_seconds}s)...")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            if (arr_bridge.sonarr.is_configured or arr_bridge.radarr.is_configured) and not arr_bridge._is_syncing:
                logger.info("Periodic Arr watchlist worker: checking Trakt watchlist...")
                await arr_bridge.sync_watchlist()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic Arr watchlist worker: {e}")


async def partner_token_refresh_loop():
    """Background worker periodically refreshing secondary partner and multi-user OAuth tokens before expiration."""
    while True:
        try:
            # Check every 12 hours
            await asyncio.sleep(43200)
            users_to_check = set()
            if Config.CO_WATCH_USER:
                users_to_check.add(Config.CO_WATCH_USER)
            for u in user_mgr.list_configured_users():
                uname = u.get("username")
                if uname:
                    users_to_check.add(uname)

            for target_user in users_to_check:
                # 1. Partner Trakt token refresh
                trakt_client = user_mgr.get_client(target_user)
                if trakt_client and trakt_client.is_authenticated():
                    token_info = trakt_client.get_token_info()
                    if token_info.get("days_remaining", 999) <= 2:
                        logger.info(f"Proactive token refresh: refreshing Trakt token for @{target_user}...")
                        await trakt_client.refresh_token()
                # 2. Partner MAL token refresh
                mal_client = user_mgr.get_tracker_client(target_user, "mal")
                if mal_client and mal_client.is_authenticated() and hasattr(mal_client, "refresh_token"):
                    token_info = getattr(mal_client, "get_token_info", lambda: {})()
                    if token_info.get("expires_in", 999999) < 86400:
                        logger.info(f"Proactive token refresh: refreshing MAL token for @{target_user}...")
                        await mal_client.refresh_token()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in partner token refresh loop: {e}")


async def background_cloud_sync_worker_loop():
    """Background worker periodically executing automated cloud reconciliation and Letterboxd diary export."""
    interval_hours = max(1, getattr(Config, "BACKGROUND_CLOUD_SYNC_INTERVAL_HOURS", 24))
    interval_seconds = interval_hours * 3600
    logger.info(f"Background cloud sync worker started (running every {interval_hours}h)...")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            logger.info("Executing periodic background cloud sync cycle...")
            await cloud_sync_mgr.run_sync_cycle(
                trigger="scheduled",
                letterboxd_client=letterboxd,
                reverse_sync_mgr=reverse_sync_mgr,
                cross_tracker_sync=cross_tracker_sync,
                arr_bridge=arr_bridge,
            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic background cloud sync worker: {e}")


async def scrobble_heartbeat_worker_loop():
    """Background worker periodically sending scrobble keep-alives to Trakt and Simkl for active sessions."""
    logger.info("Scrobble heartbeat worker started (checking active streaming sessions every 60s)...")
    while True:
        try:
            await asyncio.sleep(60.0)
            candidates = playback_mgr.get_heartbeat_candidates(interval_seconds=600)
            if not candidates:
                continue

            now = time.time()
            for candidate in candidates:
                try:
                    key = candidate.get("key", "")
                    media = candidate.get("parsed_media")
                    if not media:
                        playback_mgr.record_heartbeat(key)
                        continue

                    est_prog = candidate.get("progress", 0.0)
                    pos = playback_mgr.estimate_position(candidate, now)
                    if pos:
                        current_sec, dur_sec = pos
                        est_prog = min(99.0, max(0.0, (current_sec / dur_sec) * 100.0))

                    # 1. Trakt keep-alive scrobble_start
                    user_client = user_mgr.get_client(media.username)
                    if not user_client.is_authenticated():
                        user_client = trakt
                    if user_client.is_authenticated():
                        payload = media.to_trakt_scrobble_payload()
                        payload["progress"] = round(est_prog, 1)
                        await user_client.scrobble_start(payload)

                    # 2. Simkl keep-alive scrobble_start
                    if settings_mgr.is_tracker_enabled("simkl") and simkl.is_enabled() and simkl.is_authenticated():
                        await simkl.scrobble_start(media, progress=est_prog)

                    playback_mgr.record_heartbeat(key, progress=est_prog)
                    logger.debug(f"Heartbeat keep-alive refreshed for {candidate.get('title')} ({est_prog:.1f}%)")
                except Exception as exc:
                    logger.debug(f"Error in heartbeat keep-alive for {candidate.get('title')}: {exc}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in scrobble heartbeat loop: {e}")


async def weekly_digest_worker_loop() -> None:
    """Background task that dispatches a weekly viewing activity digest on the configured day & hour."""
    day_mapping = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    last_sent_day = -1
    while True:
        try:
            await asyncio.sleep(60)  # Check every 60 seconds
            if not settings_mgr.is_weekly_digest_enabled():
                continue

            cfg_notif = settings_mgr.get_notifications(mask=False)
            target_day_name = str(cfg_notif.get("weekly_digest_day", Config.WEEKLY_DIGEST_DAY) or "sunday").lower().strip()
            target_day = day_mapping.get(target_day_name, 6)
            target_hour = int(cfg_notif.get("weekly_digest_hour", Config.WEEKLY_DIGEST_HOUR) or 20)

            now = datetime.datetime.now()
            today_day = now.weekday()
            current_hour = now.hour

            if today_day == target_day and current_hour == target_hour and last_sent_day != today_day:
                logger.info(f"Weekly Digest: Triggering scheduled digest for {target_day_name.capitalize()} at {target_hour}:00...")
                await digest_mgr.send_digest()
                last_sent_day = today_day
            elif today_day != target_day:
                last_sent_day = -1
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in weekly digest worker loop: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global queue_worker_task, reverse_sync_worker_task, arr_watchlist_worker_task
    global partner_refresh_worker_task, background_cloud_sync_task, token_monitor_task
    global scrobble_heartbeat_worker_task, weekly_digest_worker_task
    queue_worker_task = asyncio.create_task(queue_worker_loop())
    reverse_sync_worker_task = asyncio.create_task(reverse_sync_worker_loop())
    scrobble_heartbeat_worker_task = asyncio.create_task(scrobble_heartbeat_worker_loop())
    weekly_digest_worker_task = asyncio.create_task(weekly_digest_worker_loop())
    if Config.AUTO_ADD_FROM_WATCHLIST and Config.ARR_WATCHLIST_INTERVAL > 0 and (arr_bridge.sonarr.is_configured or arr_bridge.radarr.is_configured or arr_bridge.overseerr.is_configured):
        arr_watchlist_worker_task = asyncio.create_task(arr_watchlist_worker_loop())
    partner_refresh_worker_task = asyncio.create_task(partner_token_refresh_loop())
    token_monitor_task = asyncio.create_task(token_monitor_worker_loop())
    if getattr(Config, "BACKGROUND_CLOUD_SYNC_INTERVAL_HOURS", 24) > 0:
        background_cloud_sync_task = asyncio.create_task(background_cloud_sync_worker_loop())

    # Startup self-diagnostics check
    if Config.CONFIG_ENCRYPTION_KEY:
        logger.info("Startup Diagnostics: Configuration encryption key active (AES-256-GCM tokens at rest).")
    token_info = trakt.get_token_info()
    if not trakt.is_authenticated():
        logger.warning("Startup Diagnostics: Trakt is not authenticated. Visit /auth to link your account.")
    elif not token_info.get("healthy", False):
        logger.warning(f"Startup Diagnostics: Trakt token status is {token_info.get('status')}.")
    else:
        logger.info(f"Startup Diagnostics: Trakt token healthy (~{token_info.get('days_remaining')} days remaining).")

    active_notifiers = [k for k, v in notifier.get_status().items() if v and k in ("discord", "telegram", "ntfy", "pushover", "gotify", "matrix")]
    logger.info(f"Startup Diagnostics: Active notification channels: {active_notifiers or 'None'}")
    if Config.ALLOWED_LIBRARIES:
        logger.info(f"Startup Diagnostics: Library whitelist active: {Config.ALLOWED_LIBRARIES}")
    if Config.EXCLUDED_LIBRARIES:
        logger.info(f"Startup Diagnostics: Library denylist active: {Config.EXCLUDED_LIBRARIES}")
    if Config.SYNC_COLLECTION:
        logger.info("Startup Diagnostics: Trakt Collection sync enabled for library.new events.")
    if sonarr.is_configured:
        logger.info(f"Startup Diagnostics: Sonarr integration enabled ({Config.SONARR_URL}).")
    if radarr.is_configured:
        logger.info(f"Startup Diagnostics: Radarr integration enabled ({Config.RADARR_URL}).")
    if Config.AUTO_ADD_FROM_WATCHLIST:
        logger.info(f"Startup Diagnostics: Watchlist acquisition enabled (interval: {Config.ARR_WATCHLIST_INTERVAL}s).")
    recon_startup = settings_mgr.get_reconciliation_settings(mask_token=False)
    if reverse_sync_mgr.plex.is_configured():
        logger.info(f"Startup Diagnostics: Plex API direct connection configured ({reverse_sync_mgr.plex.base_url}).")
        if recon_startup.get("sync_on_startup", Config.REVERSE_SYNC_ON_STARTUP):
            asyncio.create_task(reverse_sync_mgr.run_startup_sync())
    int_mins = recon_startup.get("interval_minutes", Config.REVERSE_SYNC_INTERVAL)
    if int_mins > 0 and reverse_sync_mgr.plex.is_configured():
        logger.info(f"Startup Diagnostics: Reverse sync interval active ({int_mins}m).")
    bg_sync_interval = getattr(Config, "BACKGROUND_CLOUD_SYNC_INTERVAL_HOURS", 24)
    if bg_sync_interval > 0:
        logger.info(f"Startup Diagnostics: Background cloud reconciliation enabled ({bg_sync_interval}h interval).")
    if simkl.is_enabled():
        if simkl.is_authenticated():
            logger.info(f"Startup Diagnostics: Simkl multi-tracker connected (@{simkl.user_name or 'user'}).")
        else:
            logger.info("Startup Diagnostics: Simkl multi-tracker enabled (visit /auth/simkl to link account).")
    if anilist.is_enabled():
        if anilist.is_authenticated():
            logger.info(f"Startup Diagnostics: AniList anime tracker connected (@{anilist.user_name or 'user'}).")
        else:
            logger.info("Startup Diagnostics: AniList anime tracker enabled (visit /auth/anilist to link account).")
    if mal.is_enabled():
        if mal.is_authenticated():
            logger.info(f"Startup Diagnostics: MyAnimeList anime tracker connected (@{mal.user_name or 'user'}).")
        else:
            logger.info("Startup Diagnostics: MyAnimeList anime tracker enabled (visit /auth/mal to link account).")
    if kitsu.is_configured():
        logger.info(f"Startup Diagnostics: Kitsu anime tracker connected (@{kitsu.username or 'user'}).")
    if tmdb.is_configured():
        logger.info("Startup Diagnostics: TMDb universal tracker configured (v3/v4 sync enabled).")
    if letterboxd.is_configured():
        logger.info(f"Startup Diagnostics: Letterboxd diary store active (@{letterboxd.username or 'user'}).")
    if serializd.is_configured():
        logger.info(f"Startup Diagnostics: Serializd TV diary tracker connected (@{serializd.username or 'user'}).")
    if mdblist.is_configured():
        logger.info("Startup Diagnostics: MDBList multi-source rating aggregator configured.")

    yield
    if queue_worker_task:
        queue_worker_task.cancel()
        try:
            await queue_worker_task
        except asyncio.CancelledError:
            pass
    if reverse_sync_worker_task:
        reverse_sync_worker_task.cancel()
        try:
            await reverse_sync_worker_task
        except asyncio.CancelledError:
            pass
    if arr_watchlist_worker_task:
        arr_watchlist_worker_task.cancel()
        try:
            await arr_watchlist_worker_task
        except asyncio.CancelledError:
            pass
    if partner_refresh_worker_task:
        partner_refresh_worker_task.cancel()
        try:
            await partner_refresh_worker_task
        except asyncio.CancelledError:
            pass
    if background_cloud_sync_task:
        background_cloud_sync_task.cancel()
        try:
            await background_cloud_sync_task
        except asyncio.CancelledError:
            pass
    if token_monitor_task:
        token_monitor_task.cancel()
        try:
            await token_monitor_task
        except asyncio.CancelledError:
            pass
    if scrobble_heartbeat_worker_task:
        scrobble_heartbeat_worker_task.cancel()
        try:
            await scrobble_heartbeat_worker_task
        except asyncio.CancelledError:
            pass
    if weekly_digest_worker_task:
        weekly_digest_worker_task.cancel()
        try:
            await weekly_digest_worker_task
        except asyncio.CancelledError:
            pass
    await reverse_sync_mgr.plex.close()
    await arr_bridge.sonarr.close()
    await arr_bridge.radarr.close()
    await arr_bridge.overseerr.close()
    await simkl.close()
    await anilist.close()
    await mal.close()
    await kitsu.close()
    await tmdb.close()
    await letterboxd.close()
    await serializd.close()
    await mdblist.close()
    await trakt.close()
    await user_mgr.close_all()
    await notifier.close()


APP_VERSION = "3.0.0"
REPO_URL = "https://github.com/selits/omniscrobble"

app = FastAPI(title="Omniscrobble", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


def is_https_request(request: Request) -> bool:
    """Check if the request was made over HTTPS directly or via an SSL reverse proxy."""
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https"


@app.middleware("http")
async def security_and_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    # Standard defensive security headers
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Strict Transport Security (HSTS) - only emit when served over HTTPS
    if is_https_request(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Cache-busting headers for dynamic and control endpoints
    path = request.url.path
    if path == "/" or path == "/demo" or path == "/sw.js" or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


async def execute_simkl_scrobble(parsed: ParsedMedia, action_taken: str, event: str):
    """Dispatch playback scrobble event asynchronously to Simkl."""
    if not (settings_mgr.is_tracker_enabled("simkl") and simkl.is_enabled() and simkl.is_authenticated()):
        return
    try:
        if event in ("media.play", "media.resume"):
            await simkl.scrobble_start(parsed, progress=parsed.progress)
        elif event == "media.pause":
            await simkl.scrobble_pause(parsed, progress=parsed.progress)
        elif event == "media.scrobble" or (action_taken == "scrobble_stop" and parsed.progress >= Config.get_threshold(parsed.media_type)):
            res = await simkl.scrobble_stop(parsed, progress=parsed.progress)
            watched_at_ts = datetime.datetime.now(timezone.utc).isoformat()
            await simkl.sync_history(parsed, watched_at=watched_at_ts)
            if isinstance(res, dict) and res.get("status") == "error":
                asyncio.create_task(notifier.send_failure_alert(parsed, "Simkl", str(res.get("error", "Error")), user=parsed.username))
        elif event == "media.rate":
            await simkl.sync_ratings(parsed, rating=int(parsed.rating or 10))
    except Exception as e:
        logger.warning(f"Simkl dispatch error for {parsed.title}: {e}")
        asyncio.create_task(notifier.send_failure_alert(parsed, "Simkl", str(e), user=parsed.username))


async def execute_multi_tracker_dispatch(parsed: ParsedMedia, action_taken: str, event: str, progress: float):
    """Dispatch playback scrobble and rating events asynchronously to Simkl, AniList, and MyAnimeList."""
    # 1. Simkl
    if settings_mgr.is_tracker_enabled("simkl") and simkl.is_enabled() and simkl.is_authenticated():
        await execute_simkl_scrobble(parsed, action_taken, event)

    # 2. Anime tracking dispatch (AniList & MAL)
    try:
        ani_active = settings_mgr.is_tracker_enabled("anilist") and anilist.is_enabled() and anilist.is_authenticated()
        mal_active = settings_mgr.is_tracker_enabled("mal") and mal.is_enabled() and mal.is_authenticated()
        if ani_active or mal_active:
            resolved_anime = await anime_resolver.resolve(parsed)
            if resolved_anime and resolved_anime.get("is_anime"):
                threshold = Config.get_threshold(parsed.media_type)
                if event == "media.rate":
                    if ani_active:
                        ani_res = await anilist.update_rating(resolved_anime["anilist_id"], float(parsed.rating or 10))
                        if isinstance(ani_res, dict) and ani_res.get("status") == "error":
                            asyncio.create_task(notifier.send_failure_alert(parsed, "AniList", str(ani_res.get("errors", "Error")), user=parsed.username))
                    if mal_active and resolved_anime.get("mal_id"):
                        mal_res = await mal.update_rating(resolved_anime["mal_id"], int(parsed.rating or 10))
                        if isinstance(mal_res, dict) and mal_res.get("status") == "error":
                            asyncio.create_task(notifier.send_failure_alert(parsed, "MyAnimeList", str(mal_res.get("error", "Error")), user=parsed.username))
                elif event == "media.scrobble" or (action_taken == "scrobble_stop" and progress >= threshold):
                    if ani_active:
                        ani_res = await anilist.update_progress(
                            resolved_anime["anilist_id"],
                            resolved_anime["episode_number"],
                            resolved_anime.get("episodes"),
                        )
                        if isinstance(ani_res, dict) and ani_res.get("status") == "error":
                            asyncio.create_task(notifier.send_failure_alert(parsed, "AniList", str(ani_res.get("errors", "Error")), user=parsed.username))
                    if mal_active and resolved_anime.get("mal_id"):
                        mal_res = await mal.update_progress(
                            resolved_anime["mal_id"],
                            resolved_anime["episode_number"],
                            resolved_anime.get("episodes"),
                        )
                        if isinstance(mal_res, dict) and mal_res.get("status") == "error":
                            asyncio.create_task(notifier.send_failure_alert(parsed, "MyAnimeList", str(mal_res.get("error", "Error")), user=parsed.username))
    except Exception as e:
        logger.warning(f"Anime multi-tracker dispatch error for {parsed.title}: {e}")


# Persistent log of recent webhook events for the status dashboard
MAX_HISTORY = max(10, getattr(Config, "MAX_EVENT_HISTORY", 100))


recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)


def reload_recent_events_in_place() -> None:
    """Load persistent stream history into recent_events deque."""
    recent_events.clear()
    if EVENTS_FILE.exists():
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for ev in reversed(data[:MAX_HISTORY]):
                        recent_events.appendleft(ev)
        except Exception as e:
            logger.warning(f"Could not load events from {EVENTS_FILE}: {e}")


def save_recent_events() -> None:
    """Persist stream history to data/events.json."""
    try:
        atomic_write_json(EVENTS_FILE, list(recent_events))
    except Exception as e:
        logger.warning(f"Could not save events to {EVENTS_FILE}: {e}")


reload_recent_events_in_place()


def log_event(media: ParsedMedia, action: str, result: dict[str, Any], cowatch_status: Optional[dict[str, Any]] = None):
    if media.media_type == "episode":
        title_str = f"{media.show_title} S{media.season:02d}E{media.episode:02d} - {media.title}"
    else:
        title_str = f"{media.title} ({media.year or 'N/A'})"

    action_str = f"{action} ({media.rating}/10)" if action == "rate" and media.rating else action
    progress_str = f"{media.rating}/10" if action == "rate" and media.rating else f"{media.progress:.1f}%"

    entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": media.username,
        "server": getattr(media, "server_type", "plex"),
        "event": media.event,
        "action": action_str,
        "title": title_str,
        "type": media.media_type,
        "show_title": media.show_title if media.media_type == "episode" else (media.title or media.show_title if media.media_type == "show" else None),
        "duration_ms": media.duration_ms,
        "view_offset_ms": media.view_offset_ms,
        "media_payload": {
            "media_type": media.media_type,
            "title": media.title,
            "year": media.year,
            "season": media.season,
            "episode": media.episode,
            "ids": media.ids,
            "duration_ms": media.duration_ms,
        },
        "progress": progress_str,
        "result_status": result.get("status") or ("ok" if not result.get("error") else "error"),
        "raw_result": result,
        "cowatch_status": cowatch_status,
    }
    recent_events.appendleft(entry)
    save_recent_events()
    save_scrobble_stats()


async def execute_cowatch_sync(parsed: ParsedMedia, action: str, targets: Optional[list[str]] = None):
    """Dual-scrobble/sync watched history to the partner's or household target profiles' authenticated cloud trackers."""
    if targets is not None:
        target_users = list(targets)
    else:
        resolved = household_mgr.resolve_targets(parsed)
        if resolved:
            target_users = resolved
        elif Config.CO_WATCH_USER:
            target_users = [Config.CO_WATCH_USER]
        else:
            target_users = []

    if not target_users:
        return

    tasks = []
    watched_at_ts = datetime.datetime.now(timezone.utc).isoformat()

    for target_user in target_users:
        # 1. Partner/Profile Trakt
        cw_trakt = user_mgr.get_client(target_user)
        if cw_trakt and cw_trakt.is_authenticated():
            async def _sync_trakt(u=target_user, client=cw_trakt):
                try:
                    logger.info(f"Household sync triggering for profile @{u} (Trakt): {parsed.title}")
                    history_payload = parsed.to_trakt_history_payload(watched_at=watched_at_ts)
                    res = await client.sync_history(history_payload)
                    if is_temporary_error(res):
                        queue_mgr.enqueue("sync_history", history_payload, error=str(res.get("error", "")), username=u)
                        metrics_registry.record_cowatch("queued")
                    elif isinstance(res, dict) and res.get("status") == "error":
                        logger.warning(f"Household Trakt error for @{u}: {res.get('error')}")
                        metrics_registry.record_cowatch("failed")
                    else:
                        logger.info(f"Household sync succeeded for profile @{u} (Trakt): {parsed.title}")
                        metrics_registry.record_cowatch("success")
                except Exception as e:
                    logger.error(f"Error during household Trakt sync for @{u}: {e}")
                    queue_mgr.enqueue("sync_history", parsed.to_trakt_history_payload(watched_at=watched_at_ts), error=str(e), username=u)
                    metrics_registry.record_cowatch("failed")
            tasks.append(_sync_trakt())
        else:
            logger.debug(f"Household profile @{target_user} Trakt is not authenticated.")

        # 2. Partner/Profile Simkl
        cw_simkl = user_mgr.get_tracker_client(target_user, "simkl")
        if cw_simkl and cw_simkl.is_authenticated():
            async def _sync_simkl(u=target_user, client=cw_simkl):
                try:
                    logger.info(f"Household sync triggering for profile @{u} (Simkl): {parsed.title}")
                    if action == "rate":
                        await client.sync_ratings(parsed, rating=int(parsed.rating or 10))
                    else:
                        res = await client.scrobble_stop(parsed, progress=parsed.progress)
                        await client.sync_history(parsed, watched_at=watched_at_ts)
                        if isinstance(res, dict) and res.get("status") == "error":
                            logger.warning(f"Household Simkl error for @{u}: {res.get('error')}")
                except Exception as e:
                    logger.warning(f"Error during household Simkl sync for @{u}: {e}")
            tasks.append(_sync_simkl())

        # 3. Partner/Profile Anime Trackers (AniList & MAL)
        cw_anilist = user_mgr.get_tracker_client(target_user, "anilist")
        cw_mal = user_mgr.get_tracker_client(target_user, "mal")
        ani_auth = bool(cw_anilist and cw_anilist.is_authenticated())
        mal_auth = bool(cw_mal and cw_mal.is_authenticated())

        if ani_auth or mal_auth:
            async def _sync_anime(u=target_user, a_auth=ani_auth, m_auth=mal_auth, c_ani=cw_anilist, c_mal=cw_mal):
                try:
                    resolved_anime = await anime_resolver.resolve(parsed)
                    if resolved_anime and resolved_anime.get("is_anime"):
                        if action == "rate":
                            if a_auth:
                                await c_ani.update_rating(resolved_anime["anilist_id"], float(parsed.rating or 10))
                            if m_auth and resolved_anime.get("mal_id"):
                                await c_mal.update_rating(resolved_anime["mal_id"], int(parsed.rating or 10))
                        else:
                            if a_auth:
                                await c_ani.update_progress(
                                    resolved_anime["anilist_id"],
                                    resolved_anime["episode_number"],
                                    resolved_anime.get("episodes"),
                                )
                            if m_auth and resolved_anime.get("mal_id"):
                                await c_mal.update_progress(
                                    resolved_anime["mal_id"],
                                    resolved_anime["episode_number"],
                                    resolved_anime.get("episodes"),
                                )
                except Exception as e:
                    logger.warning(f"Error during household anime sync for @{u}: {e}")
            tasks.append(_sync_anime())

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@app.get("/webhook")
def get_webhook_info():
    """Friendly information endpoint when /webhook is visited in a browser (HTTP GET)."""
    return {
        "status": "online",
        "message": "Plex Webhook endpoint is active and waiting for HTTP POST events from Plex Media Server.",
        "dashboard_url": "/",
    }


def verify_webhook_token(request: Request, endpoint_name: str = "webhook") -> None:
    if Config.WEBHOOK_SECRET:
        token = request.query_params.get("token") or request.headers.get("x-webhook-secret")
        if not token or not secrets.compare_digest(token, Config.WEBHOOK_SECRET):
            logger.warning(f"Rejected unauthorized {endpoint_name} request: invalid or missing token.")
            metrics_registry.record_request(endpoint_name, 401)
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing webhook token")


async def extract_webhook_payload(request: Request, endpoint_name: str = "webhook") -> Optional[dict[str, Any]]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return await request.json()
        except Exception as e:
            logger.error(f"Failed to parse raw JSON body: {e}")
            metrics_registry.record_request(endpoint_name, 400)
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    try:
        form = await request.form()
        payload_field = form.get("payload") or form.get("data")
        if payload_field is not None:
            if hasattr(payload_field, "read"):
                content = await payload_field.read()
                if isinstance(content, bytes):
                    content = content.decode("utf-8")
                return json.loads(content)
            elif isinstance(payload_field, str):
                return json.loads(payload_field)
            else:
                return json.loads(str(payload_field))
    except Exception as e:
        logger.error(f"Failed to parse multipart form data: {e}")
        metrics_registry.record_request(endpoint_name, 400)
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    try:
        body = await request.body()
        if body:
            return json.loads(body.decode("utf-8"))
    except Exception:
        pass

    return None


def get_effective_excluded_libraries() -> list[str]:
    """Combines static Config.EXCLUDED_LIBRARIES with dynamic rules ignore_libraries."""
    base = list(Config.EXCLUDED_LIBRARIES or [])
    rules_libs = settings_mgr.get_rules_settings().get("ignore_libraries", [])
    for lib in rules_libs:
        if lib and lib not in base:
            base.append(lib)
    return base


async def process_media_event(parsed: ParsedMedia, endpoint_name: str = "webhook") -> dict[str, Any]:
    # Loop Prevention: suppress bounce-back echo webhooks from media servers
    if parsed.rating_key and loop_prevention.is_ignored(parsed.rating_key):
        logger.info(f"Loop prevention: suppressing echo event '{parsed.event}' for rating_key {parsed.rating_key} ({parsed.title})")
        metrics_registry.record_request(endpoint_name, 200)
        return {"status": "ignored", "reason": "loop_prevention", "key": parsed.rating_key}
    for id_val in (parsed.ids or {}).values():
        if id_val and loop_prevention.is_ignored(str(id_val)):
            logger.info(f"Loop prevention: suppressing echo event '{parsed.event}' for ID {id_val} ({parsed.title})")
            metrics_registry.record_request(endpoint_name, 200)
            return {"status": "ignored", "reason": "loop_prevention", "key": str(id_val)}

    # Dynamic Rules & Filters: check duration, library exclusions, and file path patterns
    allowed, bypass_reason = settings_mgr.is_media_allowed(parsed)
    if not allowed:
        logger.info(f"Rules filter: bypassing event '{parsed.event}' for '{parsed.title}' ({bypass_reason})")
        metrics_registry.record_request(endpoint_name, 200)
        log_event(parsed, "bypassed", {"reason": bypass_reason})
        return {"status": "ignored", "reason": bypass_reason}

    active_client = user_mgr.get_client(parsed.username)
    if not active_client.is_authenticated():
        active_client = trakt

    if not active_client.is_authenticated():
        logger.warning(
            f"Trakt is not authenticated for user '{parsed.username}' or default! Run 'python auth.py' or visit /auth to authorize."
        )
        metrics_registry.record_request(endpoint_name, 200)
        return {"status": "error", "message": "Trakt not authenticated"}

    event = parsed.event
    scrobble_payload = parsed.to_trakt_scrobble_payload()
    result: dict[str, Any] = {}
    action_taken = "none"
    threshold = settings_mgr.get_effective_threshold(parsed.media_type)

    try:
        if event == "library.new":
            if not Config.SYNC_COLLECTION:
                metrics_registry.record_request(endpoint_name, 200)
                return {"status": "ignored", "reason": "Collection sync is disabled (SYNC_COLLECTION=false)"}

            action_taken = "collection"
            logger.info(f"Adding new media to Trakt collection: {parsed.title}")
            col_payload = parsed.to_trakt_collection_payload()
            result = await active_client.sync_collection(col_payload)
            if is_temporary_error(result):
                queue_mgr.enqueue("sync_collection", col_payload, error=str(result.get("error", "")), username=parsed.username)
                metrics_registry.record_collection(parsed.media_type, "queued")
            else:
                metrics_registry.record_collection(parsed.media_type, "success")

            scrobble_stats["collections"] = scrobble_stats.get("collections", 0) + 1
            log_event(parsed, action_taken, result)
            if Config.NOTIFY_ON_COLLECTION:
                asyncio.create_task(notifier.dispatch(parsed, "collection"))
            metrics_registry.record_request(endpoint_name, 200)
            return {"status": "success", "event": "library.new", "action": "collection", "result": result}

        elif event == "media.scrobble":
            finished = playback_mgr.stop_playback(parsed)
            if finished and finished.get("scrobbled"):
                metrics_registry.record_request(endpoint_name, 200)
                return {"status": "ignored", "reason": "Watch already recorded for this playback session"}
            action_taken = "mark_watched"
            logger.info(f"Marking as watched in Trakt: {parsed.title} for user {parsed.username}")

            scrobble_payload["progress"] = 100.0
            scrobble_res = await active_client.scrobble_stop(scrobble_payload)
            watched_at_ts = datetime.datetime.now(timezone.utc).isoformat()
            history_res = await active_client.sync_history(parsed.to_trakt_history_payload(watched_at=watched_at_ts))
            result = {"scrobble": scrobble_res, "history": history_res}

            if is_temporary_error(scrobble_res):
                queue_mgr.enqueue("scrobble_stop", scrobble_payload, error=str(scrobble_res.get("error", "")), username=parsed.username)
                metrics_registry.record_scrobble(parsed.media_type, "queued")
            else:
                metrics_registry.record_scrobble(parsed.media_type, "success")

            if is_temporary_error(history_res):
                queue_mgr.enqueue("sync_history", parsed.to_trakt_history_payload(watched_at=watched_at_ts), error=str(history_res.get("error", "")), username=parsed.username)

            record_watch_stat(parsed)

        elif event == "media.rate":
            action_taken = "rate"
            rating_val = parsed.rating or 10
            logger.info(f"Syncing rating to Trakt: {parsed.title} -> {rating_val}/10 for user {parsed.username}")
            rating_payload = parsed.to_trakt_rating_payload()
            result = await active_client.sync_ratings(rating_payload)
            if is_temporary_error(result):
                queue_mgr.enqueue("sync_ratings", rating_payload, error=str(result.get("error", "")), username=parsed.username)
                metrics_registry.record_rating("queued")
            else:
                metrics_registry.record_rating("success")
            scrobble_stats["ratings"] += 1

        elif Config.SCROBBLE_MODE == "scrobble":
            if event in ("media.play", "media.resume"):
                action_taken = "scrobble_start"
                logger.info(f"Scrobble start: {parsed.title} ({parsed.progress:.1f}%)")
                playback_mgr.update_playback(parsed, state="playing")
                result = await active_client.scrobble_start(scrobble_payload)
            elif event == "media.pause":
                playback_mgr.update_playback(parsed, state="paused")
                if parsed.progress >= threshold:
                    if playback_mgr.is_scrobbled(parsed):
                        metrics_registry.record_request(endpoint_name, 200)
                        return {"status": "ignored", "reason": "Watch already recorded for this playback session"}
                    action_taken = "scrobble_stop"
                    logger.info(f"Scrobble stop (paused past threshold {threshold}%): {parsed.title} ({parsed.progress:.1f}%)")
                    result = await active_client.scrobble_stop(scrobble_payload)
                    if is_temporary_error(result):
                        queue_mgr.enqueue("scrobble_stop", scrobble_payload, error=str(result.get("error", "")), username=parsed.username)
                        metrics_registry.record_scrobble(parsed.media_type, "queued")
                    else:
                        metrics_registry.record_scrobble(parsed.media_type, "success")
                    record_watch_stat(parsed)
                    playback_mgr.mark_scrobbled(parsed)
                else:
                    action_taken = "scrobble_pause"
                    logger.info(f"Scrobble pause: {parsed.title} ({parsed.progress:.1f}%)")
                    result = await active_client.scrobble_pause(scrobble_payload)
            elif event == "media.stop":
                finished = playback_mgr.stop_playback(parsed)
                if finished and finished.get("scrobbled"):
                    metrics_registry.record_request(endpoint_name, 200)
                    return {"status": "ignored", "reason": "Watch already recorded for this playback session"}
                if parsed.progress >= threshold:
                    action_taken = "scrobble_stop"
                    logger.info(f"Scrobble stop (watched past threshold {threshold}%): {parsed.title} ({parsed.progress:.1f}%)")
                    result = await active_client.scrobble_stop(scrobble_payload)
                    if is_temporary_error(result):
                        queue_mgr.enqueue("scrobble_stop", scrobble_payload, error=str(result.get("error", "")), username=parsed.username)
                        metrics_registry.record_scrobble(parsed.media_type, "queued")
                    else:
                        metrics_registry.record_scrobble(parsed.media_type, "success")
                    record_watch_stat(parsed)
                else:
                    action_taken = "playback_stopped"
                    logger.info(f"Playback stopped below threshold {threshold}%: {parsed.title} ({parsed.progress:.1f}%)")
                    if parsed.progress >= 1.0:
                        result = await active_client.scrobble_stop(scrobble_payload)
                    else:
                        result = {"status": "ignored", "reason": "Progress below 1.0%"}
            else:
                action_taken = f"skipped_{event}"
        else:
            action_taken = f"skipped_{event}"

        if action_taken == "none" or action_taken.startswith("skipped_"):
            metrics_registry.record_request(endpoint_name, 200)
            return {"status": "ignored", "event": event, "action": action_taken, "reason": f"Event '{event}' is not a scrobble playback trigger"}

        eligible, reason = cowatch_mgr.check_cowatch_eligibility(parsed)
        targets = cowatch_mgr.resolve_targets(parsed)
        is_sync_trigger = (event == "media.scrobble" or (action_taken == "scrobble_stop" and parsed.progress >= threshold))
        cowatch_info = None

        if is_sync_trigger:
            if targets or eligible:
                primary_target = targets[0] if targets else Config.CO_WATCH_USER
                target_str = ", ".join(targets) if len(targets) > 1 else primary_target
                sync_reason = f"Household routing to {target_str}" if len(targets) > 1 else reason
                cowatch_info = {
                    "synced": bool(targets or eligible),
                    "reason": sync_reason,
                    "target": primary_target,
                    "targets": targets,
                }
                asyncio.create_task(execute_cowatch_sync(parsed, action_taken, targets=targets))
            else:
                cowatch_info = {"synced": False, "reason": reason, "target": Config.CO_WATCH_USER, "targets": []}
        elif eligible or targets:
            primary_target = targets[0] if targets else Config.CO_WATCH_USER
            cowatch_info = {"synced": False, "in_list": True, "reason": reason, "target": primary_target, "targets": targets}

        log_event(parsed, action_taken, result, cowatch_status=cowatch_info)

        if action_taken in ("mark_watched", "scrobble_stop", "rate"):
            partner_target = (", ".join(targets) if targets else Config.CO_WATCH_USER) if (cowatch_info and cowatch_info.get("synced")) else None
            asyncio.create_task(notifier.dispatch(parsed, action_taken, cowatch_partner=partner_target))

        # Real-time multi-server watched status and rating mirroring
        if is_sync_trigger and settings_mgr.is_multi_server_mirroring_enabled():
            src_server = "plex" if endpoint_name in ("webhook", "plex") else endpoint_name
            asyncio.create_task(reverse_sync_mgr.mirror_watched_status(parsed, source_server=src_server))
        elif action_taken == "rate" and settings_mgr.is_multi_server_mirroring_enabled() and parsed.rating:
            src_server = "plex" if endpoint_name in ("webhook", "plex") else endpoint_name
            asyncio.create_task(reverse_sync_mgr.mirror_rating(parsed, float(parsed.rating), source_server=src_server))

        asyncio.create_task(execute_multi_tracker_dispatch(parsed, action_taken, event, parsed.progress))

        metrics_registry.record_request(endpoint_name, 200)
        return {"status": "success", "event": event, "action": action_taken, "result": result}

    except Exception as e:
        logger.error(f"Error executing Trakt action for {parsed.title}: {e}", exc_info=True)
        if event == "library.new":
            queue_mgr.enqueue("sync_collection", parsed.to_trakt_collection_payload(), error=str(e), username=parsed.username)
            metrics_registry.record_collection(parsed.media_type, "queued")
        elif event == "media.scrobble":
            queue_mgr.enqueue("scrobble_stop", scrobble_payload, error=str(e), username=parsed.username)
            queue_mgr.enqueue("sync_history", parsed.to_trakt_history_payload(), error=str(e), username=parsed.username)
            metrics_registry.record_scrobble(parsed.media_type, "queued")
        elif event == "media.rate":
            queue_mgr.enqueue("sync_ratings", parsed.to_trakt_rating_payload(), error=str(e), username=parsed.username)
            metrics_registry.record_rating("queued")
        elif action_taken == "scrobble_stop":
            queue_mgr.enqueue("scrobble_stop", scrobble_payload, error=str(e), username=parsed.username)
            metrics_registry.record_scrobble(parsed.media_type, "queued")
        log_event(parsed, action_taken, {"error": str(e), "queued": True})
        metrics_registry.record_request(endpoint_name, 500)
        return {"status": "error", "error": str(e), "queued": True}


@app.post("/webhook")
async def plex_webhook(request: Request):
    """Receives multipart/form-data or json webhook notifications from Plex Media Server."""
    verify_webhook_token(request, "webhook")

    if not settings_mgr.is_server_enabled("plex"):
        metrics_registry.record_request("webhook", 200)
        return {"status": "ignored", "reason": "Plex ingestion is paused in settings"}

    raw_data = await extract_webhook_payload(request, "webhook")
    if not raw_data:
        metrics_registry.record_request("webhook", 400)
        raise HTTPException(status_code=400, detail="No payload found in request")

    debug_entry = webhook_debugger.record(
        source="plex",
        endpoint="/webhook",
        payload=raw_data if isinstance(raw_data, dict) else {"raw": str(raw_data)},
        headers=dict(request.headers),
        status="processing",
    )

    parsed = parse_plex_webhook(
        raw_data,
        allowed_users=Config.PLEX_ALLOWED_USERS,
        allowed_libraries=Config.ALLOWED_LIBRARIES,
        excluded_libraries=get_effective_excluded_libraries(),
    )
    if not parsed:
        metrics_registry.record_request("webhook", 200)
        webhook_debugger.update_status(debug_entry["id"], status="ignored", reason="Non-media event, filtered user/library, or unsupported media type")
        return {"status": "ignored", "reason": "Non-media event, filtered user/library, or unsupported media type"}

    try:
        res = await process_media_event(parsed, endpoint_name="webhook")
        webhook_debugger.update_status(debug_entry["id"], status="processed", reason=res.get("status"))
        return res
    except Exception as exc:
        webhook_debugger.update_status(debug_entry["id"], status="error", reason=str(exc))
        raise


@app.get("/webhook/jellyfin")
@app.get("/jellyfin")
def jellyfin_info():
    """Information endpoint for Jellyfin Webhook plugin integration."""
    return {
        "status": "online",
        "service": "Omniscrobble Jellyfin Webhook Handler",
        "method": "POST",
        "instructions": "In Jellyfin, install the Webhook plugin and configure a Generic Webhook targeting this endpoint (e.g. /webhook/jellyfin or /webhook/jellyfin?token=...).",
    }


@app.post("/webhook/jellyfin")
@app.post("/jellyfin")
async def jellyfin_webhook(request: Request):
    """Receives webhook notifications from Jellyfin Media Server."""
    verify_webhook_token(request, "webhook_jellyfin")

    if not settings_mgr.is_server_enabled("jellyfin"):
        metrics_registry.record_request("webhook_jellyfin", 200)
        return {"status": "ignored", "reason": "Jellyfin ingestion is paused in settings"}

    raw_data = await extract_webhook_payload(request, "webhook_jellyfin")
    if not raw_data:
        metrics_registry.record_request("webhook_jellyfin", 400)
        raise HTTPException(status_code=400, detail="No payload found in request")

    debug_entry = webhook_debugger.record(
        source="jellyfin",
        endpoint="/webhook/jellyfin",
        payload=raw_data if isinstance(raw_data, dict) else {"raw": str(raw_data)},
        headers=dict(request.headers),
        status="processing",
    )

    parsed = parse_jellyfin_webhook(
        raw_data,
        allowed_users=Config.PLEX_ALLOWED_USERS,
        allowed_libraries=Config.ALLOWED_LIBRARIES,
        excluded_libraries=get_effective_excluded_libraries(),
    )
    if not parsed:
        metrics_registry.record_request("webhook_jellyfin", 200)
        webhook_debugger.update_status(debug_entry["id"], status="ignored", reason="Non-media event, filtered user/library, or unsupported media type")
        return {"status": "ignored", "reason": "Non-media event, filtered user/library, or unsupported media type"}

    try:
        res = await process_media_event(parsed, endpoint_name="webhook_jellyfin")
        webhook_debugger.update_status(debug_entry["id"], status="processed", reason=res.get("status"))
        return res
    except Exception as exc:
        webhook_debugger.update_status(debug_entry["id"], status="error", reason=str(exc))
        raise


@app.get("/webhook/emby")
@app.get("/emby")
def emby_info():
    """Information endpoint for Emby Server Webhooks integration."""
    return {
        "status": "online",
        "service": "Omniscrobble Emby Webhook Handler",
        "method": "POST",
        "instructions": "In Emby, go to Server Settings -> Webhooks -> Add Webhook and point the URL to this endpoint (e.g. /webhook/emby or /webhook/emby?token=...).",
    }


@app.post("/webhook/emby")
@app.post("/emby")
async def emby_webhook(request: Request):
    """Receives webhook notifications from Emby Media Server."""
    verify_webhook_token(request, "webhook_emby")

    if not settings_mgr.is_server_enabled("emby"):
        metrics_registry.record_request("webhook_emby", 200)
        return {"status": "ignored", "reason": "Emby ingestion is paused in settings"}

    raw_data = await extract_webhook_payload(request, "webhook_emby")
    if not raw_data:
        metrics_registry.record_request("webhook_emby", 400)
        raise HTTPException(status_code=400, detail="No payload found in request")

    debug_entry = webhook_debugger.record(
        source="emby",
        endpoint="/webhook/emby",
        payload=raw_data if isinstance(raw_data, dict) else {"raw": str(raw_data)},
        headers=dict(request.headers),
        status="processing",
    )

    parsed = parse_emby_webhook(
        raw_data,
        allowed_users=Config.PLEX_ALLOWED_USERS,
        allowed_libraries=Config.ALLOWED_LIBRARIES,
        excluded_libraries=get_effective_excluded_libraries(),
    )
    if not parsed:
        metrics_registry.record_request("webhook_emby", 200)
        webhook_debugger.update_status(debug_entry["id"], status="ignored", reason="Non-media event, filtered user/library, or unsupported media type")
        return {"status": "ignored", "reason": "Non-media event, filtered user/library, or unsupported media type"}

    try:
        res = await process_media_event(parsed, endpoint_name="webhook_emby")
        webhook_debugger.update_status(debug_entry["id"], status="processed", reason=res.get("status"))
        return res
    except Exception as exc:
        webhook_debugger.update_status(debug_entry["id"], status="error", reason=str(exc))
        raise


class StandaloneScrobblePayload(BaseModel):
    title: str = Field(..., description="Movie title or episode/show title")
    year: Optional[int] = Field(None, description="Release year")
    progress: float = Field(0.0, description="Playback progress percentage (0.0 - 100.0)")
    action: str = Field("play", description="Playback action: play, start, resume, pause, stop, scrobble, rate")
    media_type: str = Field("movie", description="Media type: 'movie' or 'episode'")
    show_title: Optional[str] = Field(None, description="Show title (for episodes)")
    season: Optional[int] = Field(None, description="Season number (for episodes)")
    episode: Optional[int] = Field(None, description="Episode number (for episodes)")
    episode_title: Optional[str] = Field(None, description="Episode title")
    ids: Optional[dict[str, Any]] = Field(default_factory=dict, description="External provider IDs (imdb, tmdb, tvdb, trakt)")
    player: Optional[str] = Field("Standalone Player", description="Client player name (e.g. Infuse, Kodi, VLC, Stremio)")
    device: Optional[str] = Field(None, description="Device name or client platform")
    user: Optional[str] = Field(None, description="Username or user identifier")
    rating: Optional[float] = Field(None, description="User rating (1-10) for rating events")
    duration_ms: Optional[int] = Field(None, description="Duration in milliseconds")
    view_offset_ms: Optional[int] = Field(None, description="Playback offset in milliseconds")


@app.get("/api/scrobble")
def standalone_scrobble_info():
    """Information and contract specification for the Standalone Player Scrobble REST bridge."""
    return {
        "status": "online",
        "service": "Omniscrobble Standalone Player REST Bridge",
        "method": "POST",
        "description": "Direct scrobbler endpoint for standalone video players (Infuse, Kodi, VLC, Stremio, MPV, etc.) without requiring a media server.",
        "payload_schema": {
            "title": "string (required)",
            "year": "integer (optional)",
            "progress": "float (0-100, optional, default: 0.0)",
            "action": "string ('play', 'pause', 'stop', 'scrobble', 'rate', optional, default: 'play')",
            "media_type": "string ('movie' or 'episode', optional, default: 'movie')",
            "show_title": "string (optional for episodes)",
            "season": "integer (optional for episodes)",
            "episode": "integer (optional for episodes)",
            "ids": "object (optional e.g. {'imdb': 'tt...', 'tmdb': '...', 'tvdb': '...'})",
            "player": "string (optional e.g. 'Infuse', 'Kodi', 'VLC', 'Stremio')",
            "user": "string (optional username)",
            "rating": "float (optional rating 1-10)",
        },
    }


@app.post("/api/scrobble")
async def standalone_scrobble_endpoint(request: Request, payload: StandaloneScrobblePayload):
    """Direct scrobble bridge endpoint for standalone players (Infuse, Kodi, VLC, Stremio)."""
    verify_webhook_token(request, "api_scrobble")

    debug_entry = webhook_debugger.record(
        source=payload.player or "standalone",
        endpoint="/api/scrobble",
        payload=payload.model_dump(),
        headers=dict(request.headers),
        status="processing",
    )

    action_lower = payload.action.strip().lower()
    if action_lower in ("play", "start", "resume"):
        event = "media.play"
    elif action_lower in ("pause",):
        event = "media.pause"
    elif action_lower in ("stop",):
        event = "media.stop"
    elif action_lower in ("scrobble", "finish", "watched", "complete"):
        event = "media.scrobble"
    elif action_lower in ("rate", "rating"):
        event = "media.rate"
    else:
        event = f"media.{action_lower}"

    clean_ids: dict[str, str] = {}
    if payload.ids:
        for k, v in payload.ids.items():
            if v:
                clean_ids[str(k).lower().strip()] = str(v).strip()

    title_val = payload.title
    show_title_val = payload.show_title
    if payload.media_type == "episode" and not show_title_val and payload.episode_title:
        show_title_val = payload.title
        title_val = payload.episode_title

    default_user = Config.PLEX_ALLOWED_USERS[0] if Config.PLEX_ALLOWED_USERS else "user"
    user_val = (payload.user or default_user).strip()

    parsed = ParsedMedia(
        event=event,
        media_type=payload.media_type,
        title=title_val,
        year=payload.year,
        progress=payload.progress,
        rating=payload.rating,
        season=payload.season,
        episode=payload.episode,
        show_title=show_title_val,
        grandparent_title=show_title_val,
        ids=clean_ids,
        player=payload.player or "Standalone Player",
        device=payload.device or payload.player or "Standalone Device",
        username=user_val,
        duration_ms=payload.duration_ms,
        view_offset_ms=payload.view_offset_ms,
    )

    try:
        res = await process_media_event(parsed, endpoint_name="api_scrobble")
        webhook_debugger.update_status(debug_entry["id"], status="processed", reason=res.get("status"))
        return res
    except Exception as exc:
        webhook_debugger.update_status(debug_entry["id"], status="error", reason=str(exc))
        raise


@app.get("/sonarr")
def sonarr_info():
    """Friendly information endpoint for Sonarr webhook setup."""
    return {
        "status": "online",
        "service": "Sonarr Webhook Handler",
        "method": "POST",
        "instructions": "In Sonarr, go to Settings -> Connect -> Add Webhook and point the URL to this endpoint (e.g. /sonarr or /sonarr?token=...).",
    }


@app.post("/sonarr")
async def sonarr_webhook(request: Request):
    """Receives webhooks from Sonarr for instant Trakt collection sync."""
    if Config.WEBHOOK_SECRET:
        token = request.query_params.get("token") or request.headers.get("x-webhook-secret")
        if not token or not secrets.compare_digest(token, Config.WEBHOOK_SECRET):
            logger.warning("Rejected unauthorized Sonarr webhook request: invalid or missing token.")
            metrics_registry.record_request("sonarr", 401)
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing webhook token")

    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse Sonarr JSON payload: {e}")
        metrics_registry.record_request("sonarr", 400)
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    debug_entry = webhook_debugger.record(
        source="sonarr",
        endpoint="/sonarr",
        payload=payload if isinstance(payload, dict) else {"raw": str(payload)},
        headers=dict(request.headers),
        status="processing",
    )

    event_type, trakt_payload, parsed = parse_sonarr_webhook(payload)

    if event_type == "test":
        logger.info("Received Sonarr test webhook - connection verified!")
        metrics_registry.record_request("sonarr", 200)
        webhook_debugger.update_status(debug_entry["id"], status="test", reason="Connection verified")
        return {"status": "success", "message": "Sonarr webhook received successfully"}

    if event_type == "ignored" or not trakt_payload or not parsed:
        metrics_registry.record_request("sonarr", 200)
        webhook_debugger.update_status(debug_entry["id"], status="ignored", reason=f"Event '{payload.get('eventType')}' ignored")
        return {"status": "ignored", "reason": f"Event '{payload.get('eventType')}' ignored"}

    if not Config.SYNC_COLLECTION:
        metrics_registry.record_request("sonarr", 200)
        webhook_debugger.update_status(debug_entry["id"], status="ignored", reason="Collection sync is disabled (SYNC_COLLECTION=false)")
        return {"status": "ignored", "reason": "Collection sync is disabled (SYNC_COLLECTION=false)"}

    active_client = user_mgr.get_client()
    action_taken = "collection"
    logger.info(f"Sonarr: Adding new media to Trakt collection: {parsed.show_title} S{parsed.season:02d}E{parsed.episode:02d}")
    try:
        result = await active_client.sync_collection(trakt_payload)
        if is_temporary_error(result):
            queue_mgr.enqueue("sync_collection", trakt_payload, error=str(result.get("error", "")), username="Sonarr")
            metrics_registry.record_collection("episode", "queued")
        else:
            metrics_registry.record_collection("episode", "success")

        scrobble_stats["collections"] = scrobble_stats.get("collections", 0) + 1
        log_event(parsed, action_taken, result)

        if Config.NOTIFY_ON_COLLECTION:
            asyncio.create_task(notifier.dispatch(parsed, "collection"))

        metrics_registry.record_request("sonarr", 200)
        webhook_debugger.update_status(debug_entry["id"], status="processed", reason="Collection sync success")
        return {"status": "success", "event": "sonarr.download", "action": "collection", "result": result}
    except Exception as e:
        logger.error(f"Error processing Sonarr collection sync: {e}")
        queue_mgr.enqueue("sync_collection", trakt_payload, error=str(e), username="Sonarr")
        metrics_registry.record_collection("episode", "queued")
        log_event(parsed, action_taken, {"error": str(e), "queued": True})
        metrics_registry.record_request("sonarr", 500)
        webhook_debugger.update_status(debug_entry["id"], status="error", reason=str(e))
        return {"status": "error", "error": str(e), "queued": True}


@app.get("/radarr")
def radarr_info():
    """Friendly information endpoint for Radarr webhook setup."""
    return {
        "status": "online",
        "service": "Radarr Webhook Handler",
        "method": "POST",
        "instructions": "In Radarr, go to Settings -> Connect -> Add Webhook and point the URL to this endpoint (e.g. /radarr or /radarr?token=...).",
    }


@app.post("/radarr")
async def radarr_webhook(request: Request):
    """Receives webhooks from Radarr for instant Trakt collection sync."""
    if Config.WEBHOOK_SECRET:
        token = request.query_params.get("token") or request.headers.get("x-webhook-secret")
        if not token or not secrets.compare_digest(token, Config.WEBHOOK_SECRET):
            logger.warning("Rejected unauthorized Radarr webhook request: invalid or missing token.")
            metrics_registry.record_request("radarr", 401)
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing webhook token")

    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse Radarr JSON payload: {e}")
        metrics_registry.record_request("radarr", 400)
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    debug_entry = webhook_debugger.record(
        source="radarr",
        endpoint="/radarr",
        payload=payload if isinstance(payload, dict) else {"raw": str(payload)},
        headers=dict(request.headers),
        status="processing",
    )

    event_type, trakt_payload, parsed = parse_radarr_webhook(payload)

    if event_type == "test":
        logger.info("Received Radarr test webhook - connection verified!")
        metrics_registry.record_request("radarr", 200)
        webhook_debugger.update_status(debug_entry["id"], status="test", reason="Connection verified")
        return {"status": "success", "message": "Radarr webhook received successfully"}

    if event_type == "ignored" or not trakt_payload or not parsed:
        metrics_registry.record_request("radarr", 200)
        webhook_debugger.update_status(debug_entry["id"], status="ignored", reason=f"Event '{payload.get('eventType')}' ignored")
        return {"status": "ignored", "reason": f"Event '{payload.get('eventType')}' ignored"}

    if not Config.SYNC_COLLECTION:
        metrics_registry.record_request("radarr", 200)
        webhook_debugger.update_status(debug_entry["id"], status="ignored", reason="Collection sync is disabled (SYNC_COLLECTION=false)")
        return {"status": "ignored", "reason": "Collection sync is disabled (SYNC_COLLECTION=false)"}

    active_client = user_mgr.get_client()
    action_taken = "collection"
    logger.info(f"Radarr: Adding new movie to Trakt collection: {parsed.title} ({parsed.year})")
    try:
        result = await active_client.sync_collection(trakt_payload)
        if is_temporary_error(result):
            queue_mgr.enqueue("sync_collection", trakt_payload, error=str(result.get("error", "")), username="Radarr")
            metrics_registry.record_collection("movie", "queued")
        else:
            metrics_registry.record_collection("movie", "success")

        scrobble_stats["collections"] = scrobble_stats.get("collections", 0) + 1
        log_event(parsed, action_taken, result)

        if Config.NOTIFY_ON_COLLECTION:
            asyncio.create_task(notifier.dispatch(parsed, "collection"))

        metrics_registry.record_request("radarr", 200)
        webhook_debugger.update_status(debug_entry["id"], status="processed", reason="Collection sync success")
        return {"status": "success", "event": "radarr.download", "action": "collection", "result": result}
    except Exception as e:
        logger.error(f"Error processing Radarr collection sync: {e}")
        queue_mgr.enqueue("sync_collection", trakt_payload, error=str(e), username="Radarr")
        metrics_registry.record_collection("movie", "queued")
        log_event(parsed, action_taken, {"error": str(e), "queued": True})
        metrics_registry.record_request("radarr", 500)
        webhook_debugger.update_status(debug_entry["id"], status="error", reason=str(e))
        return {"status": "error", "error": str(e), "queued": True}


trakt_user_profile: Optional[dict[str, Any]] = None


async def get_cached_trakt_profile() -> Optional[dict[str, Any]]:
    global trakt_user_profile
    if trakt_user_profile:
        return trakt_user_profile
    if trakt.is_authenticated():
        settings = await trakt.get_user_settings()
        if settings and "user" in settings:
            trakt_user_profile = settings["user"]
    return trakt_user_profile


@app.get("/health")
async def health_check():
    profile = await get_cached_trakt_profile()
    token_info = trakt.get_token_info()
    return {
        "status": "healthy",
        "app_name": "Omniscrobble",
        "version": APP_VERSION,
        "authenticated": trakt.is_authenticated(),
        "trakt_user": profile.get("username") if profile else None,
        "servers_supported": ["plex", "jellyfin", "emby"],
        "scrobble_thresholds": {
            "episode": Config.EPISODE_SCROBBLE_THRESHOLD,
            "movie": Config.MOVIE_SCROBBLE_THRESHOLD,
        },
        "allowed_users": Config.PLEX_ALLOWED_USERS or "all",
        "allowed_libraries": Config.ALLOWED_LIBRARIES or "all",
        "excluded_libraries": Config.EXCLUDED_LIBRARIES or "none",
        "sync_collection": Config.SYNC_COLLECTION,
        "scrobble_mode": Config.SCROBBLE_MODE,
        "webhook_secret_enabled": bool(Config.WEBHOOK_SECRET),
        "sonarr": {
            "configured": sonarr.is_configured,
        },
        "radarr": {
            "endpoint": "/radarr",
        },
        "uptime": get_uptime_str(),
        "token_health": token_info,
        "stats": scrobble_stats,
        "queue": {
            "pending": queue_mgr.get_pending_count(),
        },
        "notifications": notifier.get_status(),
    }


@app.get("/api/health/tokens")
async def get_token_health(request: Request):
    """Retrieve detailed expiration and health status across all configured tracker and partner tokens."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    from app.services.token_health_monitor import token_health_mgr
    return await token_health_mgr.run_check_cycle(
        trakt_client=trakt,
        simkl_client=simkl,
        mal_client=mal,
        user_mgr=user_mgr,
    )


OMNISCROBBLE_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0f172a" />
      <stop offset="100%" stop-color="#090d16" />
    </linearGradient>
    <linearGradient id="arrowGradTop" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="arrowGradBottom" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="highlightGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#7dd3fc" />
      <stop offset="100%" stop-color="#0284c7" />
    </linearGradient>
    <linearGradient id="innerDiscGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#1e293b" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <filter id="subtleDrop" x="-15%" y="-15%" width="130%" height="130%">
      <feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#000000" flood-opacity="0.4" />
    </filter>
  </defs>

  <rect width="512" height="512" rx="112" fill="url(#bgGrad)" />

  <g filter="url(#subtleDrop)">
    <!-- Top-Right Clockwise Arrow -->
    <path d="M 405 285 C 418 200 360 98 256 98 C 190 98 135 135 110 188 L 86 160 L 98 238 L 174 220 L 148 194 C 168 152 210 126 256 126 C 340 126 388 206 376 280 Z" fill="url(#arrowGradTop)" />
    
    <!-- Bottom-Left Clockwise Arrow -->
    <path d="M 107 227 C 94 312 152 414 256 414 C 322 414 377 377 402 324 L 426 352 L 414 274 L 338 292 L 364 318 C 344 360 302 386 256 386 C 172 386 124 306 136 232 Z" fill="url(#arrowGradBottom)" />

    <!-- Motion Echo Arcs -->
    <path d="M 390 220 C 378 160 326 122 260 122" fill="none" stroke="url(#highlightGrad)" stroke-width="6" stroke-linecap="round" opacity="0.75" />
    <path d="M 122 292 C 134 352 186 390 252 390" fill="none" stroke="url(#highlightGrad)" stroke-width="6" stroke-linecap="round" opacity="0.75" />

    <!-- Center Disc & Film Frame -->
    <circle cx="256" cy="256" r="114" fill="url(#innerDiscGrad)" stroke="#334155" stroke-width="5" />
    <rect x="182" y="174" width="148" height="164" rx="22" fill="#1e293b" stroke="#475569" stroke-width="3.5" />

    <!-- Left Sprockets -->
    <rect x="193" y="188" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="217" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="247" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="277" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="306" width="14" height="18" rx="3.5" fill="#0f172a" />

    <!-- Right Sprockets -->
    <rect x="305" y="188" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="217" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="247" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="277" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="306" width="14" height="18" rx="3.5" fill="#0f172a" />

    <!-- Play Button Triangle -->
    <polygon points="242,216 296,256 242,296" fill="#f8fafc" stroke="#f8fafc" stroke-width="6" stroke-linejoin="round" />
  </g>
</svg>"""

def get_sw_js() -> str:
    """Returns dynamic PWA service worker script tied to APP_VERSION."""
    return f"""// Omniscrobble PWA Service Worker
const CACHE_NAME = 'omniscrobble-v{APP_VERSION}';
const STATIC_ASSETS = [
  '/manifest.json',
  '/static/icons/icon-192.svg',
  '/static/icons/icon-512.svg'
];

self.addEventListener('install', (event) => {{
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).catch(() => {{}})
  );
  self.skipWaiting();
}});

self.addEventListener('activate', (event) => {{
  event.waitUntil(
    caches.keys().then((keys) => {{
      return Promise.all(
        keys.map((key) => {{
          if (key !== CACHE_NAME) {{
            return caches.delete(key);
          }}
        }})
      );
    }}).then(() => {{
      // Always purge dynamic dashboard HTML '/' from any cache to prevent stale UI
      return caches.open(CACHE_NAME).then((cache) => cache.delete('/'));
    }})
  );
  self.clients.claim();
}});

self.addEventListener('fetch', (event) => {{
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  // Never intercept or cache navigation or API requests - always fetch live
  if (event.request.mode === 'navigate' || url.pathname === '/' || url.pathname.startsWith('/api/')) {{
    return;
  }}
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
}});
"""


@app.get("/manifest.json")
def pwa_manifest():
    """Serves Web App Manifest for mobile installation (PWA)."""
    manifest = {
        "name": "Omniscrobble",
        "short_name": "Omniscrobble",
        "description": "Universal Scrobbler & Webhook Bridge for Plex, Jellyfin, and Emby to Trakt",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": [
            {
                "src": "/static/icons/icon-192.svg",
                "sizes": "192x192",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            },
            {
                "src": "/static/icons/icon-512.svg",
                "sizes": "512x512",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            }
        ]
    }
    return Response(content=json.dumps(manifest), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    """Serves PWA service worker script with explicit no-cache headers."""
    return Response(
        content=get_sw_js(),
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/static/icons/icon-192.svg")
@app.get("/static/icons/icon-512.svg")
def pwa_icon():
    """Serves dynamic scalable vector icon for PWA."""
    return Response(content=OMNISCROBBLE_ICON_SVG, media_type="image/svg+xml")


@app.get("/metrics")
def get_metrics():
    """Prometheus exposition metrics endpoint."""
    uptime_seconds = time.time() - SERVER_START_TIME
    pending = queue_mgr.get_pending_count()
    active_streams = playback_mgr.get_active_count()
    metrics_text = metrics_registry.generate_prometheus_text(
        uptime_seconds=uptime_seconds,
        queue_pending=pending,
        active_streams=active_streams,
    )
    return Response(
        content=metrics_text,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/backup")
async def export_backup(request: Request, passphrase: Optional[str] = None):
    """Download a zip archive containing server configuration, tokens, and databases.
    
    If passphrase, x-backup-passphrase header, or CONFIG_ENCRYPTION_KEY is provided,
    encrypts the archive with AES-256-GCM.
    """
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Primary trakt_tokens.json
        if Config.TRAKT_TOKENS_FILE.exists():
            zf.write(Config.TRAKT_TOKENS_FILE, arcname="trakt_tokens.json")

        # 2. Multi-user tokens in data/tokens
        tokens_dir = Config.BASE_DIR / "data" / "tokens"
        if tokens_dir.exists():
            for p in tokens_dir.glob("*.json"):
                zf.write(p, arcname=f"data/tokens/{p.name}")

        # 3. Co-watch shows and devices files
        if Config.CO_WATCH_DATA_FILE.exists():
            zf.write(Config.CO_WATCH_DATA_FILE, arcname="data/cowatch_shows.json")
        if Config.CO_WATCH_DEVICES_DATA_FILE.exists():
            zf.write(Config.CO_WATCH_DEVICES_DATA_FILE, arcname="data/cowatch_devices.json")

        # 4. SQLite queue database
        if Config.QUEUE_DB_FILE.exists():
            zf.write(Config.QUEUE_DB_FILE, arcname="data/queue.db")

        # 5. Playback stats, event history, and dynamic settings
        if STATS_FILE.exists():
            zf.write(STATS_FILE, arcname="data/stats.json")
        if EVENTS_FILE.exists():
            zf.write(EVENTS_FILE, arcname="data/events.json")
        if Config.SETTINGS_FILE.exists():
            zf.write(Config.SETTINGS_FILE, arcname="data/settings.json")
        diary_file = getattr(Config, "LETTERBOXD_DIARY_FILE", None)
        if diary_file and diary_file.exists():
            zf.write(diary_file, arcname="data/letterboxd_diary.json")

    buffer.seek(0)
    zip_bytes = buffer.getvalue()
    key = passphrase or request.headers.get("x-backup-passphrase") or getattr(Config, "CONFIG_ENCRYPTION_KEY", "")

    if key:
        from app.services.crypto_manager import encrypt_bytes
        encrypted_dict = encrypt_bytes(zip_bytes, key)
        encrypted_json = json.dumps(encrypted_dict, indent=2).encode("utf-8")
        filename = f"omniscrobble-backup-encrypted-{datetime.date.today().isoformat()}.json"
        return Response(
            content=encrypted_json,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    filename = f"omniscrobble-backup-{datetime.date.today().isoformat()}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/restore")
async def import_backup(request: Request):
    """Restore server configuration and tokens from an uploaded zip or encrypted backup."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    form = await request.form()
    file = form.get("backup_file")
    passphrase = form.get("passphrase") or request.headers.get("x-backup-passphrase") or getattr(Config, "CONFIG_ENCRYPTION_KEY", "")
    if not file or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="Missing backup_file in form")

    contents = await file.read()
    if isinstance(contents, str):
        contents = contents.encode("utf-8")

    # Transparently decrypt if backup is an encrypted JSON envelope
    try:
        cand = json.loads(contents.decode("utf-8"))
        from app.services.crypto_manager import is_encrypted_payload, decrypt_bytes
        if is_encrypted_payload(cand):
            if not passphrase:
                raise HTTPException(status_code=400, detail="Encrypted backup requires a passphrase to restore.")
            try:
                contents = decrypt_bytes(cand, str(passphrase))
            except Exception as e:
                raise HTTPException(status_code=400, detail="Decryption failed - incorrect passphrase or corrupted backup") from e
    except HTTPException:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass  # Standard zip archive

    restored_files = []
    try:
        with zipfile.ZipFile(io.BytesIO(contents), "r") as zf:
            for zip_info in zf.infolist():
                name = zip_info.filename.replace("\\", "/")
                # Strict path traversal / zip slip protection
                if ".." in name or name.startswith("/"):
                    continue
                # Only allow specific safe targets
                if name != "trakt_tokens.json" and not name.startswith("data/"):
                    continue

                target_path = Config.BASE_DIR / name
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(zip_info) as source, open(target_path, "wb") as target:
                    target.write(source.read())
                restored_files.append(name)

        # Reload clients and caches
        for c in list(user_mgr._clients.values()):
            c._tokens = None
        user_mgr._clients.clear()
        user_mgr.set_default_client(trakt)
        trakt._tokens = None
        trakt.load_tokens()
        cowatch_mgr._load_shows()
        cowatch_mgr._load_devices()
        settings_mgr._load_settings()
        scrobble_stats.clear()
        scrobble_stats.update(load_scrobble_stats())
        reload_recent_events_in_place()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error restoring backup: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to restore backup: {e}")

    return {"status": "success", "restored": restored_files}



@app.get("/api/events")
def get_events(request: Request, limit: Optional[int] = None, offset: int = 0):
    if request.query_params.get("demo") == "true":
        all_demo = demo_mgr.get_demo_events()
        total_demo = len(all_demo)
        paged_demo = all_demo[offset : offset + limit] if limit is not None else all_demo
        return {"events": paged_demo, "total": total_demo}
    is_admin = is_admin_request(request)
    raw_events = list(recent_events)
    total_count = len(raw_events)
    if not is_admin:
        events = [
            {
                "timestamp": ev.get("timestamp"),
                "user": mask_username(ev.get("user")),
                "event": ev.get("event"),
                "action": ev.get("action"),
                "title": ev.get("title"),
                "type": ev.get("type"),
                "show_title": None,
                "progress": ev.get("progress"),
                "result_status": ev.get("result_status"),
                "cowatch_status": None,
            }
            for ev in raw_events
        ]
    else:
        events = raw_events
        for ev in events:
            show = ev.get("show_title") or (ev.get("title") if ev.get("type") == "show" else None)
            if not show and ev.get("media_payload") and ev.get("media_payload", {}).get("media_type") == "show":
                show = ev["media_payload"].get("title")
            ev["is_cowatch_show"] = cowatch_mgr.is_cowatch_show(show) if show else False
    if limit is not None:
        events = events[offset : offset + limit]
    return {"events": events, "total": total_count}


@app.post("/api/events/clear")
def clear_events(request: Request):
    if request.query_params.get("demo") == "true":
        return {"status": "cleared"}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    recent_events.clear()
    save_recent_events()
    return {"status": "cleared"}


@app.post("/api/stats/reset")
def reset_stats_endpoint(request: Request):
    """Reset scrobble statistics counters."""
    if request.query_params.get("demo") == "true":
        return {"status": "ok", "stats": scrobble_stats}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    for k in scrobble_stats:
        scrobble_stats[k] = 0
    save_scrobble_stats()
    return {"status": "ok", "stats": scrobble_stats}


@app.post("/api/queue/retry")
async def trigger_queue_retry(request: Request):
    if request.query_params.get("demo") == "true":
        return {"status": "ok", "result": {}, "pending_count": 0}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    res = await process_queue(trakt, queue_mgr, user_mgr=user_mgr)
    return {"status": "ok", "result": res, "pending_count": queue_mgr.get_pending_count()}


@app.post("/api/queue/clear")
def trigger_queue_clear(request: Request):
    if request.query_params.get("demo") == "true":
        return {"status": "ok", "pending_count": 0}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    queue_mgr.clear_queue()
    return {"status": "ok", "pending_count": 0}


class QueuePruneRequest(BaseModel):
    days: Optional[int] = 90


@app.post("/api/queue/prune")
def trigger_queue_prune(request: Request, payload: Optional[QueuePruneRequest] = None):
    """Prune completed and old failed records from the offline retry queue."""
    if request.query_params.get("demo") == "true":
        return {"status": "ok", "pruned": 0, "retention_days": 90}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    retention_days = (payload.days if payload and payload.days is not None else 90)
    pruned = queue_mgr.prune_queue(days=retention_days)
    return {"status": "ok", "pruned": pruned, "retention_days": retention_days}


@app.get("/api/playback")
def get_playback_status(request: Request):
    if request.query_params.get("demo") == "true":
        return {
            "active_sessions": [demo_mgr.get_demo_playback()],
            "recently_finished": None,
        }
    is_admin = is_admin_request(request)
    return {
        "active_sessions": playback_mgr.get_active_sessions(is_admin=is_admin),
        "recently_finished": playback_mgr.get_recently_finished(is_admin=is_admin),
    }


@app.get("/api/logs")
async def get_system_logs(
    request: Request,
    lines: int = 150,
    source: str = "auto",
):
    """Return sanitized server logs from journalctl or in-memory ring buffer."""
    if request.query_params.get("demo") == "true":
        return {
            "source": "demo (simulated journal)",
            "lines": demo_mgr.get_demo_logs(lines=lines),
        }
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required to view logs")
    return await log_mgr.get_logs(lines=lines, source=source)


class ManualScrobbleRequest(BaseModel):
    action: Optional[str] = "watched"  # "watched" or "start"
    media_type: Optional[str] = None
    title: Optional[str] = None
    show_title: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    ids: dict[str, Any] = {}
    duration_ms: Optional[int] = None
    trackers: Optional[list[str]] = None
    cowatch: Optional[bool] = False
    media: Optional[dict[str, Any]] = None

    model_config = {"extra": "ignore"}


class RemoveHistoryRequest(BaseModel):
    media_type: Optional[str] = None
    title: Optional[str] = None
    show_title: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    ids: dict[str, Any] = {}
    trackers: Optional[list[str]] = None
    cowatch: Optional[bool] = False
    media: Optional[dict[str, Any]] = None

    model_config = {"extra": "ignore"}


class SettingsToggleRequest(BaseModel):
    category: str  # "servers" or "trackers"
    key: str       # "plex", "jellyfin", "emby", "trakt", "simkl", "anilist", "mal"
    enabled: bool


class SettingsUpdateRequest(BaseModel):
    servers: Optional[dict[str, bool]] = None
    trackers: Optional[dict[str, bool]] = None
    credentials: Optional[dict[str, dict[str, Any]]] = None
    reconciliation: Optional[dict[str, Any]] = None
    arr: Optional[dict[str, Any]] = None
    rules: Optional[dict[str, Any]] = None
    notifications: Optional[dict[str, Any]] = None
    multi_server_mirroring: Optional[bool] = None

    model_config = {"extra": "ignore"}


class NotificationTestRequest(BaseModel):
    channel: str
    discord_webhook_url: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    ntfy_url: Optional[str] = None
    ntfy_auth_token: Optional[str] = None
    pushover_user_key: Optional[str] = None
    pushover_api_token: Optional[str] = None
    gotify_url: Optional[str] = None
    gotify_token: Optional[str] = None
    gotify_priority: Optional[int] = None
    matrix_homeserver_url: Optional[str] = None
    matrix_access_token: Optional[str] = None
    matrix_room_id: Optional[str] = None

    model_config = {"extra": "ignore"}


@app.get("/api/settings")
def get_settings_endpoint(request: Request):
    """Retrieve current runtime enablement settings for servers and trackers."""
    all_s = settings_mgr.get_all_settings()
    if not is_admin_request(request):
        # Public dashboard views only need feature enablement flags. Keep private
        # server URLs, usernames, routing rules, and credential metadata private.
        all_s = {
            "servers": all_s.get("servers", {}),
            "trackers": all_s.get("trackers", {}),
            "multi_server_mirroring": all_s.get("multi_server_mirroring", False),
        }
    return {
        "status": "success",
        "settings": all_s,
        **all_s,
    }


@app.post("/api/settings")
@app.put("/api/settings")
def update_settings_endpoint(payload: SettingsUpdateRequest, request: Request):
    """Update runtime settings, tracker credentials, reconciliation, or arr settings."""
    if request and request.query_params.get("demo") == "true":
        return {"status": "success", "settings": settings_mgr.get_all_settings()}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    data = payload.model_dump(exclude_unset=True)
    updated = settings_mgr.update_all_settings(data)

    # Dynamic in-memory reconfiguration
    if payload.credentials:
        if "trakt" in payload.credentials:
            trakt_creds = settings_mgr.get_tracker_credentials("trakt", mask=False)
            trakt.update_credentials(client_id=trakt_creds.get("client_id"), client_secret=trakt_creds.get("client_secret"))
            user_mgr.update_credentials(client_id=trakt_creds.get("client_id"), client_secret=trakt_creds.get("client_secret"))
        if "simkl" in payload.credentials:
            simkl_creds = settings_mgr.get_tracker_credentials("simkl", mask=False)
            simkl.update_credentials(client_id=simkl_creds.get("client_id"), client_secret=simkl_creds.get("client_secret"))
        if "mal" in payload.credentials:
            mal_creds = settings_mgr.get_tracker_credentials("mal", mask=False)
            mal.update_credentials(client_id=mal_creds.get("client_id"), client_secret=mal_creds.get("client_secret"))

    if payload.reconciliation:
        raw_recon = settings_mgr.get_reconciliation_settings(mask_token=False)
        reverse_sync_mgr.update_config(
            server=raw_recon.get("server_type"),
            plex_url=raw_recon.get("plex_url"),
            plex_token=raw_recon.get("plex_token"),
            jellyfin_url=raw_recon.get("jellyfin_url"),
            jellyfin_token=raw_recon.get("jellyfin_token"),
            jellyfin_user_id=raw_recon.get("jellyfin_user_id"),
            emby_url=raw_recon.get("emby_url"),
            emby_token=raw_recon.get("emby_token"),
            emby_user_id=raw_recon.get("emby_user_id"),
        )
        reverse_sync_config_updated.set()

    if payload.arr:
        arr_cfg = settings_mgr.get_arr_settings(mask=False)
        arr_bridge.update_config(
            sonarr_url=arr_cfg.get("sonarr_url"),
            sonarr_api_key=arr_cfg.get("sonarr_api_key"),
            radarr_url=arr_cfg.get("radarr_url"),
            radarr_api_key=arr_cfg.get("radarr_api_key"),
            overseerr_url=arr_cfg.get("overseerr_url"),
            overseerr_api_key=arr_cfg.get("overseerr_api_key"),
        )
        invalidate_arr_acquisition_config_cache()

    return {"status": "success", "settings": updated}


@app.post("/api/notifications/test")
async def test_notification_endpoint(payload: NotificationTestRequest, request: Request):
    """Send an immediate test alert to verify notification channel setup."""
    if request and request.query_params.get("demo") == "true":
        return {
            "status": "success",
            "success": True,
            "message": f"Demo Mode: Test notification dispatched successfully to {payload.channel.capitalize()}!",
        }
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    success, msg = await notifier.send_test_notification(
        channel=payload.channel,
        discord_webhook_url=payload.discord_webhook_url,
        telegram_bot_token=payload.telegram_bot_token,
        telegram_chat_id=payload.telegram_chat_id,
        ntfy_url=payload.ntfy_url,
        ntfy_auth_token=payload.ntfy_auth_token,
        pushover_user_key=payload.pushover_user_key,
        pushover_api_token=payload.pushover_api_token,
        gotify_url=payload.gotify_url,
        gotify_token=payload.gotify_token,
        gotify_priority=payload.gotify_priority,
        matrix_homeserver_url=payload.matrix_homeserver_url,
        matrix_access_token=payload.matrix_access_token,
        matrix_room_id=payload.matrix_room_id,
    )
    if not success:
        return JSONResponse(status_code=400, content={"status": "error", "success": False, "message": msg})
    return {"status": "success", "success": True, "message": msg}


@app.post("/api/notifications/digest")
async def trigger_weekly_digest_endpoint(request: Request):
    """Trigger an immediate dispatch of the weekly activity digest."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return await digest_mgr.send_digest(demo=True)
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    return await digest_mgr.send_digest()


@app.post("/api/settings/save-all")
def save_all_settings_alias(payload: SettingsUpdateRequest, request: Request):
    """Atomically save all Settings Hub configurations to data/settings.json."""
    return update_settings_endpoint(payload, request)


@app.get("/api/settings/rules")
def get_rules_endpoint(request: Request):
    """Retrieve current dynamic scrobble rules and filters configuration."""
    return {
        "status": "success",
        "rules": settings_mgr.get_rules_settings(),
    }


@app.post("/api/settings/rules")
def update_rules_endpoint(payload: dict[str, Any], request: Request):
    """Update dynamic scrobble rules and filters configuration."""
    if request and request.query_params.get("demo") == "true":
        return {"status": "success", "rules": settings_mgr.get_rules_settings()}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    updated = settings_mgr.update_rules_settings(payload)
    return {"status": "success", "rules": updated}


@app.post("/api/settings/toggle")
def toggle_setting_endpoint(payload: SettingsToggleRequest, request: Request):
    """Toggle enablement state for a media server or tracker dynamically."""
    if request and request.query_params.get("demo") == "true":
        return {"status": "success", "settings": settings_mgr.get_all_settings()}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    cat = payload.category.lower().strip()
    k = payload.key.lower().strip()
    if cat in ("servers", "server"):
        updated = settings_mgr.set_server_enabled(k, payload.enabled)
    elif cat in ("trackers", "tracker"):
        updated = settings_mgr.set_tracker_enabled(k, payload.enabled)
    else:
        raise HTTPException(status_code=400, detail=f"Invalid category '{payload.category}'")

    return {"status": "success", "category": cat, "key": k, "enabled": payload.enabled, "settings": updated}


@app.get("/api/search")
async def search_media_endpoint(query: str, type: Optional[str] = None, request: Request = None):
    if request and request.query_params.get("demo") == "true":
        results = [
            {"type": "show", "show": {"title": s["title"], "year": s.get("year", 2024), "overview": f"A critically acclaimed show about {s['title']}.", "ids": {"trakt": 1000}}}
            for s in demo_mgr.get_demo_sonarr_shows(query)
        ]
        return {"results": results}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not query.strip():
        return {"results": []}
    results = await trakt.search_media(query.strip(), media_type=type)
    return {"results": results}


@app.post("/api/scrobble/manual")
async def manual_scrobble(payload: ManualScrobbleRequest, request: Request):
    """Manually scrobble or start playback for a movie or episode across selected/all trackers."""
    req_action = (payload.action or "watched").lower().strip()
    is_start = req_action == "start"

    if request and request.query_params.get("demo") == "true":
        targets = payload.trackers or ["trakt", "simkl"]
        return {"status": "success", "action": req_action, "result": {"synced_trackers": targets}, "cowatch_synced": bool(payload.cowatch)}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    profile = await get_cached_trakt_profile()
    admin_user = (profile.get("username") if profile else None) or "admin"

    m_dict = payload.media or {}
    m_type = payload.media_type or m_dict.get("media_type") or "movie"
    m_title = payload.title or m_dict.get("title") or m_dict.get("show_title") or "Unknown"
    m_show_title = payload.show_title or m_dict.get("show_title")
    m_year = payload.year if payload.year is not None else m_dict.get("year")
    m_season = payload.season if payload.season is not None else m_dict.get("season")
    m_episode = payload.episode if payload.episode is not None else m_dict.get("episode")
    m_ids = payload.ids or m_dict.get("ids") or {}
    m_duration_ms = payload.duration_ms if payload.duration_ms is not None else m_dict.get("duration_ms")

    if is_start:
        prog = 1.0
        media_obj = ParsedMedia(
            event="media.play",
            username=admin_user,
            media_type=m_type,
            title=m_title,
            show_title=m_show_title,
            year=m_year,
            season=m_season,
            episode=m_episode,
            duration_ms=m_duration_ms,
            progress=prog,
            ids=m_ids,
            player="Web Dashboard",
        )
        # Register in active streaming sessions widget
        playback_mgr.update_playback(media_obj, state="playing")

        dispatch_res = await multi_tracker.dispatch_scrobble(
            action="start",
            media=media_obj,
            trakt_client=trakt,
            progress=prog,
            selected_trackers=payload.trackers,
        )

        cowatch_synced = False
        partner_user = Config.CO_WATCH_USER
        if payload.cowatch and partner_user:
            try:
                cw_client = user_mgr.get_client(partner_user)
                if cw_client.is_authenticated():
                    partner_media = ParsedMedia(
                        event="media.play",
                        username=partner_user,
                        media_type=m_type,
                        title=m_title,
                        show_title=m_show_title,
                        year=m_year,
                        season=m_season,
                        episode=m_episode,
                        duration_ms=m_duration_ms,
                        progress=prog,
                        ids=m_ids,
                        player="Web Dashboard (Co-Watch)",
                    )
                    await multi_tracker.dispatch_scrobble(
                        action="start",
                        media=partner_media,
                        trakt_client=cw_client,
                        progress=prog,
                        selected_trackers=payload.trackers,
                    )
                    cowatch_synced = True
                    metrics_registry.record_cowatch("success")
            except Exception as e:
                logger.error(f"Error starting playback for co-watch partner @{partner_user}: {e}")

        cowatch_status = {"synced": cowatch_synced, "target": partner_user} if cowatch_synced else None
        log_event(media_obj, "manual_start", dispatch_res, cowatch_status=cowatch_status)

        synced_list = dispatch_res.get("trackers", ["trakt"])
        asyncio.create_task(
            notifier.dispatch(
                media_obj,
                "playback_start",
                cowatch_partner=partner_user if cowatch_synced else None,
                trackers=synced_list,
            )
        )
        return {"status": "success", "action": "start", "result": dispatch_res, "cowatch_synced": cowatch_synced}

    else:
        media_obj = ParsedMedia(
            event="manual.scrobble",
            username=admin_user,
            media_type=m_type,
            title=m_title,
            show_title=m_show_title,
            year=m_year,
            season=m_season,
            episode=m_episode,
            duration_ms=m_duration_ms,
            progress=100.0,
            ids=m_ids,
            player="Web Dashboard",
        )
        # Clear any active playback session for this item
        playback_mgr.stop_playback(media_obj)

        dispatch_res = await multi_tracker.dispatch_manual_scrobble(
            media=media_obj,
            trakt_client=trakt,
            selected_trackers=payload.trackers,
        )

        record_watch_stat(media_obj)

        cowatch_synced = False
        partner_user = Config.CO_WATCH_USER
        if payload.cowatch and partner_user:
            try:
                cw_client = user_mgr.get_client(partner_user)
                if cw_client.is_authenticated():
                    partner_media = ParsedMedia(
                        event="manual.scrobble",
                        username=partner_user,
                        media_type=m_type,
                        title=m_title,
                        show_title=m_show_title,
                        year=m_year,
                        season=m_season,
                        episode=m_episode,
                        duration_ms=m_duration_ms,
                        progress=100.0,
                        ids=m_ids,
                    )
                    await multi_tracker.dispatch_manual_scrobble(
                        media=partner_media,
                        trakt_client=cw_client,
                        selected_trackers=payload.trackers,
                    )
                    cowatch_synced = True
                    metrics_registry.record_cowatch("success")
            except Exception as e:
                logger.error(f"Error dual-scrobbling manual watch for co-watch partner @{partner_user}: {e}")

        cowatch_status = {"synced": cowatch_synced, "target": partner_user} if cowatch_synced else None
        log_event(media_obj, "manual_scrobble", dispatch_res, cowatch_status=cowatch_status)

        synced_list = dispatch_res.get("synced_trackers", ["trakt"])
        asyncio.create_task(
            notifier.dispatch(
                media_obj,
                "mark_watched",
                cowatch_partner=partner_user if cowatch_synced else None,
                trackers=synced_list,
            )
        )
        return {"status": "success", "action": "watched", "result": dispatch_res, "cowatch_synced": cowatch_synced}


@app.post("/api/history/remove")
async def remove_history_endpoint(payload: RemoveHistoryRequest, request: Request):
    """Remove a movie or episode from watched history across selected/all trackers with optional co-watch."""
    if request and request.query_params.get("demo") == "true":
        targets = payload.trackers or ["trakt", "simkl"]
        return {"status": "success", "result": {"removed_trackers": targets}, "cowatch_removed": bool(payload.cowatch)}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    profile = await get_cached_trakt_profile()
    admin_user = (profile.get("username") if profile else None) or "admin"

    m_dict = payload.media or {}
    m_type = payload.media_type or m_dict.get("media_type") or "movie"
    m_title = payload.title or m_dict.get("title") or m_dict.get("show_title") or "Unknown"
    m_show_title = payload.show_title or m_dict.get("show_title")
    m_year = payload.year if payload.year is not None else m_dict.get("year")
    m_season = payload.season if payload.season is not None else m_dict.get("season")
    m_episode = payload.episode if payload.episode is not None else m_dict.get("episode")
    m_ids = payload.ids or m_dict.get("ids") or {}

    media_obj = ParsedMedia(
        event="manual.unscrobble",
        username=admin_user,
        media_type=m_type,
        title=m_title,
        show_title=m_show_title,
        year=m_year,
        season=m_season,
        episode=m_episode,
        progress=0.0,
        ids=m_ids,
    )

    dispatch_res = await multi_tracker.dispatch_unscrobble(
        media=media_obj,
        trakt_client=trakt,
        selected_trackers=payload.trackers,
    )

    cowatch_removed = False
    partner_user = Config.CO_WATCH_USER
    if payload.cowatch and partner_user:
        try:
            cw_client = user_mgr.get_client(partner_user)
            if cw_client.is_authenticated():
                partner_media = ParsedMedia(
                    event="manual.unscrobble",
                    username=partner_user,
                    media_type=m_type,
                    title=m_title,
                    show_title=m_show_title,
                    year=m_year,
                    season=m_season,
                    episode=m_episode,
                    progress=0.0,
                    ids=m_ids,
                )
                await multi_tracker.dispatch_unscrobble(
                    media=partner_media,
                    trakt_client=cw_client,
                    selected_trackers=payload.trackers,
                )
                cowatch_removed = True
        except Exception as e:
            logger.error(f"Error removing history for co-watch partner @{partner_user}: {e}")

    cowatch_status = {"synced": cowatch_removed, "target": partner_user} if cowatch_removed else None
    log_event(media_obj, "unscrobble", dispatch_res, cowatch_status=cowatch_status)

    return {"status": "success", "result": dispatch_res, "cowatch_removed": cowatch_removed}


class WatchlistRequest(BaseModel):
    media_type: str  # "movie" or "show" or "episode"
    title: str
    year: Optional[int] = None
    ids: dict[str, Any] = {}


@app.post("/api/watchlist")
async def add_to_watchlist(payload: WatchlistRequest, request: Request):
    if request and request.query_params.get("demo") == "true":
        return {"status": "success", "result": {"added": {"movies": 1 if payload.media_type == "movie" else 0, "shows": 1 if payload.media_type != "movie" else 0}}}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not trakt.is_authenticated():
        raise HTTPException(status_code=400, detail="Trakt is not authenticated")

    item: dict[str, Any] = {"title": payload.title}
    if payload.year:
        item["year"] = payload.year
    if payload.ids:
        item["ids"] = payload.ids

    if payload.media_type == "movie":
        watchlist_payload = {"movies": [item]}
    else:
        watchlist_payload = {"shows": [item]}

    res = await trakt.sync_watchlist(watchlist_payload)
    if is_temporary_error(res):
        queue_mgr.enqueue("sync_watchlist", watchlist_payload, error=str(res.get("error", "")))
    return {"status": "success", "result": res}


class AddShowRequest(BaseModel):
    show: str


class CowatchSyncRequest(BaseModel):
    media_type: str
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    ids: dict[str, Any] = {}
    target_user: Optional[str] = None


@app.get("/api/cowatch")
def get_cowatch_details(request: Request):
    is_admin = is_admin_request(request)
    status = cowatch_mgr.get_status()
    users = user_mgr.list_configured_users()
    if not is_admin:
        if status.get("co_watch_user"):
            status["co_watch_user"] = mask_username(status["co_watch_user"])
        # Privacy shielding: hide show titles and player devices for non-admin viewers
        status["shows_count"] = len(status.get("shows", []))
        status["shows"] = []
        status["co_watch_players"] = []
        users = [
            {**u, "username": mask_username(u["username"])}
            for u in users
        ]
    return {
        "status": status,
        "configured_users": users,
    }


@app.post("/api/cowatch/shows")
def add_cowatch_show(payload: AddShowRequest, request: Request):
    if request and request.query_params.get("demo") == "true":
        current = demo_mgr.get_demo_cowatch_shows()
        if payload.show and payload.show not in current:
            current.append(payload.show)
        return {"status": "ok", "shows": sorted(current, key=lambda x: x.lower())}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not payload.show.strip():
        raise HTTPException(status_code=400, detail="Show name cannot be empty")
    shows = cowatch_mgr.add_show(payload.show)
    return {"status": "ok", "shows": shows}


@app.delete("/api/cowatch/shows")
def delete_cowatch_show(show: str, request: Request):
    if request and request.query_params.get("demo") == "true":
        current = [s for s in demo_mgr.get_demo_cowatch_shows() if s.lower() != show.lower()]
        return {"status": "ok", "shows": sorted(current, key=lambda x: x.lower())}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    shows = cowatch_mgr.remove_show(show)
    return {"status": "ok", "shows": shows}


class AddDeviceRequest(BaseModel):
    device: str


@app.post("/api/cowatch/devices")
def add_cowatch_device(payload: AddDeviceRequest, request: Request):
    if request and request.query_params.get("demo") == "true":
        devices = demo_mgr.add_demo_cowatch_device(payload.device)
        return {"status": "ok", "devices": devices}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not payload.device.strip():
        raise HTTPException(status_code=400, detail="Device name cannot be empty")
    devices = cowatch_mgr.add_device(payload.device)
    return {"status": "ok", "devices": devices}


@app.delete("/api/cowatch/devices")
def delete_cowatch_device(device: str, request: Request):
    if request and request.query_params.get("demo") == "true":
        devices = demo_mgr.remove_demo_cowatch_device(device)
        return {"status": "ok", "devices": devices}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    devices = cowatch_mgr.remove_device(device)
    return {"status": "ok", "devices": devices}


@app.get("/api/sonarr/shows")
async def get_sonarr_shows(
    request: Request, q: str = "", limit: int = 15, exclude_shared: bool = True
):
    """Search series from Sonarr for Co-Watch autocomplete, excluding already whitelisted shows."""
    if request and request.query_params.get("demo") == "true":
        return {"configured": True, "shows": demo_mgr.get_demo_sonarr_shows(q)}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not sonarr.is_configured:
        return {"configured": False, "shows": []}
    exclude = cowatch_mgr.get_shows() if exclude_shared else None
    shows = await sonarr.search_series(query=q, limit=limit, exclude=exclude)
    return {"configured": True, "shows": shows}


@app.post("/api/cowatch/sync")
async def cowatch_manual_sync(payload: CowatchSyncRequest, request: Request):
    if request and request.query_params.get("demo") == "true":
        return {"status": "success", "result": {"synced": True}}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    target_user = payload.target_user or Config.CO_WATCH_USER
    if not target_user:
        raise HTTPException(status_code=400, detail="No co-watch target user configured")
    cw_client = user_mgr.get_client(target_user)
    if not cw_client.is_authenticated():
        raise HTTPException(status_code=400, detail=f"User '{target_user}' Trakt account is not authenticated")

    if payload.media_type == "episode":
        history_payload: dict[str, Any] = {
            "shows": [
                {
                    "title": payload.title,
                    "seasons": [
                        {
                            "number": payload.season if payload.season is not None else 1,
                            "episodes": [
                                {"number": payload.episode if payload.episode is not None else 1}
                            ]
                        }
                    ]
                }
            ]
        }
        if payload.year:
            history_payload["shows"][0]["year"] = payload.year
        if payload.ids:
            history_payload["shows"][0]["ids"] = payload.ids
    else:
        movie_item: dict[str, Any] = {"title": payload.title}
        if payload.year:
            movie_item["year"] = payload.year
        if payload.ids:
            movie_item["ids"] = payload.ids
        history_payload = {"movies": [movie_item]}

    res = await cw_client.sync_history(history_payload)
    if is_temporary_error(res):
        queue_mgr.enqueue("sync_history", history_payload, error=str(res.get("error", "")), username=target_user)
    return {"status": "success", "target_user": target_user, "result": res}


class CowatchSettingsRequest(BaseModel):
    co_watch_movies: Optional[bool] = None


@app.post("/api/cowatch/settings")
def update_cowatch_settings(payload: CowatchSettingsRequest, request: Request):
    if request and request.query_params.get("demo") == "true":
        return {"status": "ok", "co_watch_movies": payload.co_watch_movies if payload.co_watch_movies is not None else False}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if payload.co_watch_movies is not None:
        cowatch_mgr.set_cowatch_movies(payload.co_watch_movies)
    return {"status": "ok", "co_watch_movies": cowatch_mgr.config.CO_WATCH_MOVIES}


@app.get("/api/cowatch/trackers")
def get_cowatch_trackers_status(request: Request):
    """Return configured and authenticated trackers matrix for the co-watch partner."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_cowatch_trackers()
    target_user = request.query_params.get("user") or Config.CO_WATCH_USER
    if not target_user:
        return {"configured": False, "user": None, "trackers": {}}
    trackers = user_mgr.get_user_trackers_status(target_user)
    is_admin = is_admin_request(request)
    display_user = target_user if is_admin else mask_username(target_user)
    return {
        "configured": True,
        "user": display_user,
        "raw_user": target_user if is_admin else None,
        "trackers": trackers,
    }


class HouseholdRuleRequest(BaseModel):
    id: Optional[str] = None
    name: str
    targets: list[str]
    devices: Optional[list[str]] = None
    shows: Optional[list[str]] = None
    media_types: Optional[list[str]] = None
    enabled: Optional[bool] = True


@app.get("/api/household/rules")
def get_household_rules(request: Request):
    """Return configured household multi-tenant routing rules."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return {"status": "ok", "rules": demo_mgr.get_demo_household_rules()}
    is_admin = is_admin_request(request)
    rules = household_mgr.get_rules()
    if not is_admin:
        masked_rules = []
        for r in rules:
            r_copy = dict(r)
            r_copy["targets"] = [mask_username(t) for t in r.get("targets", [])]
            masked_rules.append(r_copy)
        rules = masked_rules
    return {"status": "ok", "rules": rules}


@app.post("/api/household/rules")
def save_household_rule(payload: HouseholdRuleRequest, request: Request):
    """Create or update a household multi-tenant routing rule."""
    if request.query_params.get("demo") == "true":
        demo_rule = demo_mgr.add_demo_household_rule(payload.model_dump())
        return {"status": "ok", "rule": demo_rule}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    clean_targets = [t.strip() for t in payload.targets if t and t.strip()]
    if not clean_targets:
        raise HTTPException(status_code=400, detail="Rule must have at least one target username")

    if payload.id:
        existing = household_mgr.update_rule(payload.id, payload.model_dump(exclude_unset=True))
        if existing:
            return {"status": "ok", "rule": existing}

    rule = household_mgr.add_rule(
        name=payload.name,
        targets=clean_targets,
        devices=payload.devices,
        shows=payload.shows,
        media_types=payload.media_types,
        enabled=True if payload.enabled is None else payload.enabled,
        rule_id=payload.id,
    )
    return {"status": "ok", "rule": rule}


@app.delete("/api/household/rules/{rule_id}")
def delete_household_rule(rule_id: str, request: Request):
    """Delete a household routing rule by ID."""
    if request.query_params.get("demo") == "true":
        demo_mgr.delete_demo_household_rule(rule_id)
        return {"status": "ok", "deleted": True}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    deleted = household_mgr.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "ok", "deleted": True}


@app.post("/api/household/rules/{rule_id}/toggle")
def toggle_household_rule(rule_id: str, request: Request):
    """Toggle enabled status of a household routing rule."""
    if request.query_params.get("demo") == "true":
        new_state = demo_mgr.toggle_demo_household_rule(rule_id)
        return {"status": "ok", "enabled": new_state}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    new_state = household_mgr.toggle_rule(rule_id)
    if new_state is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "ok", "enabled": new_state}


class ReconcileRequest(BaseModel):
    item_ids: Optional[list[str]] = None
    direction: str = "all"  # "all", "trakt_to_plex", "plex_to_trakt", "trakt_to_server", "server_to_trakt"
    server: Optional[str] = None


class ReconcileSettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    server_type: Optional[str] = None
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    jellyfin_url: Optional[str] = None
    jellyfin_token: Optional[str] = None
    jellyfin_user_id: Optional[str] = None
    emby_url: Optional[str] = None
    emby_token: Optional[str] = None
    emby_user_id: Optional[str] = None
    interval_minutes: Optional[int] = None
    sync_on_startup: Optional[bool] = None
    sync_ratings: Optional[bool] = None
    direction_default: Optional[str] = None
    clear_token: Optional[bool] = False
    clear_plex_token: Optional[bool] = False
    clear_jellyfin_token: Optional[bool] = False
    clear_emby_token: Optional[bool] = False

    model_config = {"extra": "ignore"}


class MediaServerTestConnectionRequest(BaseModel):
    server: Optional[str] = "plex"
    url: Optional[str] = None
    token: Optional[str] = None
    user_id: Optional[str] = None
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    jellyfin_url: Optional[str] = None
    jellyfin_token: Optional[str] = None
    emby_url: Optional[str] = None
    emby_token: Optional[str] = None

    model_config = {"extra": "ignore"}


PlexTestConnectionRequest = MediaServerTestConnectionRequest


@app.get("/api/sync/settings")
async def get_sync_settings(request: Request):
    """Return persistent two-way reconciliation settings."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return {
            "enabled": True,
            "server_type": "plex",
            "plex_url": "http://<your-server-ip-or-domain>:32400",
            "plex_token": "••••••••abcd",
            "jellyfin_url": "http://<your-server-ip-or-domain>:8096",
            "jellyfin_token": "••••••••efgh",
            "jellyfin_user_id": "",
            "emby_url": "http://<your-server-ip-or-domain>:8096",
            "emby_token": "••••••••ijkl",
            "emby_user_id": "",
            "masked_token": "••••••••abcd",
            "is_token_set": True,
            "has_token": True,
            "has_plex_token": True,
            "has_jellyfin_token": True,
            "has_emby_token": True,
            "interval_minutes": 60,
            "sync_on_startup": False,
            "sync_ratings": True,
            "direction_default": "all",
        }
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    return settings_mgr.get_reconciliation_settings(mask_token=True)


@app.post("/api/sync/settings")
async def save_sync_settings(payload: ReconcileSettingsRequest, request: Request):
    """Save persistent two-way reconciliation settings and reconfigure in-memory worker."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return {
            "status": "success",
            "settings": {
                "enabled": True if payload.enabled is None else payload.enabled,
                "server_type": payload.server_type or "plex",
                "plex_url": payload.plex_url or "http://<your-server-ip-or-domain>:32400",
                "plex_token": "••••••••abcd",
                "jellyfin_url": payload.jellyfin_url or "http://<your-server-ip-or-domain>:8096",
                "jellyfin_token": "••••••••efgh",
                "emby_url": payload.emby_url or "http://<your-server-ip-or-domain>:8096",
                "emby_token": "••••••••ijkl",
                "masked_token": "••••••••abcd",
                "is_token_set": True,
                "has_token": True,
                "interval_minutes": payload.interval_minutes if payload.interval_minutes is not None else 60,
                "sync_on_startup": bool(payload.sync_on_startup),
                "sync_ratings": True if payload.sync_ratings is None else payload.sync_ratings,
                "direction_default": payload.direction_default or "all",
            },
        }
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    data = payload.model_dump(exclude_unset=True)
    updated = settings_mgr.update_reconciliation_settings(data)

    raw_recon = settings_mgr.get_reconciliation_settings(mask_token=False)
    reverse_sync_mgr.update_config(
        server=raw_recon.get("server_type"),
        plex_url=raw_recon.get("plex_url"),
        plex_token=raw_recon.get("plex_token"),
        jellyfin_url=raw_recon.get("jellyfin_url"),
        jellyfin_token=raw_recon.get("jellyfin_token"),
        jellyfin_user_id=raw_recon.get("jellyfin_user_id"),
        emby_url=raw_recon.get("emby_url"),
        emby_token=raw_recon.get("emby_token"),
        emby_user_id=raw_recon.get("emby_user_id"),
    )
    reverse_sync_config_updated.set()

    return {"status": "success", "settings": updated}


@app.post("/api/sync/test-connection")
async def test_sync_connection(payload: MediaServerTestConnectionRequest, request: Request):
    """Test connectivity to media server (Plex, Jellyfin, Emby) with provided or active credentials."""
    is_demo = request.query_params.get("demo") == "true"
    srv = (payload.server or "plex").lower().strip()
    if is_demo:
        if srv == "jellyfin":
            return {"status": "connected", "server_name": "Demo Jellyfin Server", "version": "10.9.11"}
        elif srv == "emby":
            return {"status": "connected", "server_name": "Demo Emby Server", "version": "4.8.8"}
        return {
            "status": "connected",
            "machine_identifier": "demo-plex-server",
            "version": "1.40.2.8395",
        }
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    raw_recon = settings_mgr.get_reconciliation_settings(mask_token=False)
    if srv == "jellyfin":
        url_candidate = payload.url or payload.jellyfin_url or ""
        target_url = url_candidate.strip() if url_candidate else raw_recon.get("jellyfin_url", "")
        if target_url and not target_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
        token_candidate = payload.token or payload.jellyfin_token or ""
        target_token = token_candidate.strip() if token_candidate else ""
        if not target_token or target_token.startswith("••••") or target_token.startswith("●●●●"):
            target_token = raw_recon.get("jellyfin_token", "")
        target_user = payload.user_id or raw_recon.get("jellyfin_user_id", "")
        res = await reverse_sync_mgr.test_connection(server="jellyfin", url=target_url, token=target_token, user_id=target_user)
    elif srv == "emby":
        url_candidate = payload.url or payload.emby_url or ""
        target_url = url_candidate.strip() if url_candidate else raw_recon.get("emby_url", "")
        if target_url and not target_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
        token_candidate = payload.token or payload.emby_token or ""
        target_token = token_candidate.strip() if token_candidate else ""
        if not target_token or target_token.startswith("••••") or target_token.startswith("●●●●"):
            target_token = raw_recon.get("emby_token", "")
        target_user = payload.user_id or raw_recon.get("emby_user_id", "")
        res = await reverse_sync_mgr.test_connection(server="emby", url=target_url, token=target_token, user_id=target_user)
    else:
        url_candidate = payload.url or payload.plex_url or ""
        target_url = url_candidate.strip() if url_candidate else raw_recon.get("plex_url", "")
        if target_url and not target_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
        token_candidate = payload.token or payload.plex_token or ""
        target_token = token_candidate.strip() if token_candidate else ""
        if not target_token or target_token.startswith("••••") or target_token.startswith("●●●●"):
            target_token = raw_recon.get("plex_token", "")
        res = await reverse_sync_mgr.test_connection(server="plex", url=target_url, token=target_token)

    return res


@app.get("/api/sync/status")
async def get_sync_status(request: Request, server: Optional[str] = None):
    """Return status of two-way sync and media server direct connection."""
    is_demo = request.query_params.get("demo") == "true"
    return await reverse_sync_mgr.get_status(demo=is_demo, server=server)


@app.get("/api/sync/diff")
async def get_sync_diff(
    request: Request,
    force: bool = False,
    server: Optional[str] = None,
    cursor: Optional[int] = None,
    limit: Optional[int] = None,
):
    """Scan and return discrepancies between media server and Trakt with optional chunked cursor pagination."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        demo_diff = demo_mgr.get_demo_reconciliation()
        total_cnt = len(demo_diff)
        if cursor is not None or limit is not None:
            c = max(0, cursor or 0)
            lim = max(1, limit or 50)
            chunk = demo_diff[c : c + lim]
            next_cursor = (c + lim) if (c + lim) < total_cnt else None
            return {
                "status": "ok",
                "diff": chunk,
                "count": len(chunk),
                "total": total_cnt,
                "cursor": next_cursor,
                "has_more": next_cursor is not None,
            }
        return {"status": "ok", "diff": demo_diff, "count": total_cnt}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    diff = await reverse_sync_mgr.scan_discrepancies(force=force, server=server)
    total_cnt = len(diff)
    if cursor is not None or limit is not None:
        c = max(0, cursor or 0)
        lim = max(1, limit or 50)
        chunk = diff[c : c + lim]
        next_cursor = (c + lim) if (c + lim) < total_cnt else None
        return {
            "status": "ok",
            "diff": chunk,
            "count": len(chunk),
            "total": total_cnt,
            "cursor": next_cursor,
            "has_more": next_cursor is not None,
        }
    return {"status": "ok", "diff": diff, "count": total_cnt}


@app.post("/api/sync/reconcile")
async def trigger_reconciliation(payload: ReconcileRequest, request: Request):
    """Execute two-way reconciliation for discrepancies."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return await reverse_sync_mgr.execute_reconciliation(demo=True, server=payload.server)
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if cloud_sync_mgr.sync_mutex.locked():
        raise HTTPException(status_code=409, detail="A cloud or reconciliation sync is already in progress")
    async with cloud_sync_mgr.sync_mutex:
        res = await reverse_sync_mgr.execute_reconciliation(
            item_ids=payload.item_ids,
            direction=payload.direction,
            server=payload.server,
        )
        return res


@app.get("/api/sync/progress")
async def get_sync_progress(request: Request):
    """Poll reconciliation progress."""
    return reverse_sync_mgr._sync_progress


@app.get("/api/sync/background/status")
async def get_background_sync_status(request: Request):
    """Return current background synchronization state and telemetry."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_background_sync_status()
    return cloud_sync_mgr.get_status()


@app.post("/api/sync/background/run")
async def trigger_background_sync(request: Request):
    """Manually trigger an automated background cloud reconciliation cycle."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return {
            "status": "success",
            "message": "Demo background cloud reconciliation completed successfully.",
            "last_run_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_reconciled": 3,
        }
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if cloud_sync_mgr.sync_mutex.locked():
        raise HTTPException(status_code=409, detail="A cloud synchronization or reconciliation is already in progress")
    result = await cloud_sync_mgr.run_sync_cycle(
        trigger="manual",
        letterboxd_client=letterboxd,
        reverse_sync_mgr=reverse_sync_mgr,
        cross_tracker_sync=cross_tracker_sync,
        arr_bridge=arr_bridge,
    )
    if isinstance(result, dict) and result.get("status") == "conflict":
        raise HTTPException(status_code=409, detail=result.get("message"))
    return result


class RegisterWebhookRequest(BaseModel):
    server: str = "plex"
    webhook_url: Optional[str] = None

    model_config = {"extra": "ignore"}


@app.post("/api/sync/register-webhook")
async def register_server_webhook(payload: RegisterWebhookRequest, request: Request):
    """Automatically register Omniscrobble webhook URL with the target media server (Plex, Jellyfin, Emby)."""
    is_demo = request.query_params.get("demo") == "true"
    srv = (payload.server or "plex").lower().strip()
    if is_demo:
        demo_url = payload.webhook_url or f"https://omniscrobble.demo.internal/webhook{'/' + srv if srv != 'plex' else ''}"
        return {
            "status": "success",
            "success": True,
            "server": srv,
            "url": demo_url,
            "message": f"Demo Mode: Successfully registered webhook on {srv.capitalize()}!",
        }

    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    res = await reverse_sync_mgr.register_webhook(server=srv, webhook_url=payload.webhook_url)
    if not res.get("success", False):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to register webhook"))
    return res


class ArrTestConnectionRequest(BaseModel):
    app: Optional[str] = "sonarr"
    url: Optional[str] = None
    api_key: Optional[str] = None

    model_config = {"extra": "ignore"}


class ArrAddRequest(BaseModel):
    type: str
    item_data: dict[str, Any]
    root_folder_path: Optional[str] = None
    quality_profile_id: Optional[int] = None
    monitored: bool = True
    monitor_option: str = "all"
    search_now: bool = True
    enable_cowatch: bool = False

    model_config = {"extra": "ignore"}


_arr_acquisition_config_cache: dict[str, Any] = {"expires_at": 0.0, "data": None}


def invalidate_arr_acquisition_config_cache() -> None:
    """Discard cached service folders and profiles after Arr settings change."""
    _arr_acquisition_config_cache.update({"expires_at": 0.0, "data": None})


@app.get("/api/arr/lookup")
async def lookup_arr_media(request: Request, type: str, term: str):
    """Search Sonarr/Radarr catalog and mark items already in the local library."""
    if request.query_params.get("demo") == "true":
        return {"results": []}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    kind = type.strip().lower()
    search = term.strip()[:120]
    if kind not in {"series", "movie"}:
        raise HTTPException(status_code=400, detail="type must be 'series' or 'movie'")
    if not search:
        return {"results": []}
    client = arr_bridge.sonarr if kind == "series" else arr_bridge.radarr
    if not client.is_configured:
        return {"results": [], "configured": False}
    candidates = await arr_bridge.lookup_media(kind, search)
    results = []
    for item in candidates[:20]:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if kind == "series":
            exists = await arr_bridge.sonarr.has_series(tvdb_id=item.get("tvdbId"), imdb_id=item.get("imdbId"), title=title)
            external_id = item.get("tvdbId")
            network = item.get("network") or ""
        else:
            exists = await arr_bridge.radarr.has_movie(tmdb_id=item.get("tmdbId"), imdb_id=item.get("imdbId"), title=title)
            external_id = item.get("tmdbId")
            network = item.get("studio") or ""
        images = item.get("images") or []
        poster = next((img.get("remoteUrl") or img.get("url") for img in images if img.get("coverType") == "poster"), None)
        results.append({"title": title, "year": item.get("year"), "overview": item.get("overview", ""),
                        "network": network, "poster_url": poster, "in_library": exists,
                        "payload": item, "external_id": external_id})
    return {"results": results, "configured": True}


@app.get("/api/arr/config")
async def get_arr_acquisition_config(request: Request):
    """Return the configured root folders and quality profiles for each Arr service."""
    if request.query_params.get("demo") == "true":
        return {"sonarr": {"configured": True, "root_folders": [], "quality_profiles": []},
                "radarr": {"configured": True, "root_folders": [], "quality_profiles": []}}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    cached = _arr_acquisition_config_cache.get("data")
    if cached is not None and time.monotonic() < _arr_acquisition_config_cache["expires_at"]:
        return cached
    arr_settings = settings_mgr.get_arr_settings(mask=False)
    async def details(client, root_default, profile_default):
        if not client.is_configured:
            return {"configured": False, "root_folders": [], "quality_profiles": [], "default_root": root_default, "default_profile": profile_default}
        folders, profiles = await asyncio.gather(client.get_root_folders(), client.get_quality_profiles())
        return {"configured": True, "root_folders": folders, "quality_profiles": profiles,
                "default_root": root_default, "default_profile": profile_default}
    config = {
        "sonarr": await details(arr_bridge.sonarr, arr_settings.get("sonarr_root_folder"), arr_settings.get("sonarr_quality_profile_id")),
        "radarr": await details(arr_bridge.radarr, arr_settings.get("radarr_root_folder"), arr_settings.get("radarr_quality_profile_id")),
        "co_watch_user": Config.CO_WATCH_USER or None,
    }
    _arr_acquisition_config_cache.update({"expires_at": time.monotonic() + 300.0, "data": config})
    return config


@app.post("/api/arr/add")
async def add_arr_media(payload: ArrAddRequest, request: Request):
    """Add a selected catalog item to Sonarr or Radarr with optional show co-watch enrollment."""
    if request.query_params.get("demo") == "true":
        return {"status": "ok", "success": True, "cowatch_enrolled": False}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    csrf_cookie = request.cookies.get("csrf_token", "")
    csrf_header = request.headers.get("x-csrf-token", "")
    if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
    kind = payload.type.strip().lower()
    if kind not in {"series", "movie"}:
        raise HTTPException(status_code=400, detail="type must be 'series' or 'movie'")
    if kind == "series" and payload.enable_cowatch and not Config.CO_WATCH_USER:
        raise HTTPException(status_code=400, detail="Co-Watch enrollment requires a configured partner account")
    item = payload.item_data
    title = str(item.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Selected item is missing a title")
    result = await arr_bridge.acquire_media(
        kind,
        item,
        root_folder_path=payload.root_folder_path,
        quality_profile_id=payload.quality_profile_id,
        monitored=payload.monitored,
        search_now=payload.search_now,
        monitor_option=payload.monitor_option,
    )
    if not result.get("success"):
        logger.warning("Interactive *Arr addition failed for %s item (service=%s)", kind, "Sonarr" if kind == "series" else "Radarr")
        raise HTTPException(status_code=400, detail="The item could not be added. Check the *Arr service connection and configuration.")
    enrolled = False
    if kind == "series" and payload.enable_cowatch:
        cowatch_mgr.add_show(title)
        enrolled = True
    logger.info("Interactive media acquisition completed (type=%s, service=%s, co-watch=%s)", kind, "Sonarr" if kind == "series" else "Radarr", enrolled)
    return {"status": "ok", "success": True, "title": title,
            "service": "Sonarr" if kind == "series" else "Radarr", "cowatch_enrolled": enrolled,
            "item_id": (result.get("data") or {}).get("id")}


@app.post("/api/arr/test-connection")
async def test_arr_connection(payload: ArrTestConnectionRequest, request: Request):
    """Test connectivity to Sonarr, Radarr, or Overseerr/Jellyseerr with provided or active credentials."""
    is_demo = request.query_params.get("demo") == "true"
    app_type = (payload.app or "sonarr").lower().strip()
    if is_demo:
        ver = "4.0.9" if app_type == "sonarr" else ("5.9.1" if app_type == "radarr" else "1.33.2")
        return {"status": "connected", "app": app_type, "version": ver}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    arr_cfg = settings_mgr.get_arr_settings(mask=False)
    if app_type == "sonarr":
        target_url = (payload.url or "").strip() or arr_cfg.get("sonarr_url", "")
        if target_url and not target_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
        target_key = (payload.api_key or "").strip()
        if not target_key or settings_mgr._is_masked(target_key):
            target_key = arr_cfg.get("sonarr_api_key", "")
        temp_client = SonarrClient(base_url=target_url, api_key=target_key)
        try:
            return await temp_client.check_connection()
        finally:
            await temp_client.close()
    elif app_type == "radarr":
        target_url = (payload.url or "").strip() or arr_cfg.get("radarr_url", "")
        if target_url and not target_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
        target_key = (payload.api_key or "").strip()
        if not target_key or settings_mgr._is_masked(target_key):
            target_key = arr_cfg.get("radarr_api_key", "")
        temp_client = RadarrClient(base_url=target_url, api_key=target_key)
        try:
            return await temp_client.check_connection()
        finally:
            await temp_client.close()
    elif app_type in ("overseerr", "jellyseerr"):
        target_url = (payload.url or "").strip() or arr_cfg.get("overseerr_url", "")
        if target_url and not target_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
        target_key = (payload.api_key or "").strip()
        if not target_key or settings_mgr._is_masked(target_key):
            target_key = arr_cfg.get("overseerr_api_key", "")
        temp_client = OverseerrClient(base_url=target_url, api_key=target_key)
        try:
            return await temp_client.check_connection()
        finally:
            await temp_client.close()
    else:
        raise HTTPException(status_code=400, detail=f"Invalid app '{payload.app}'")


@app.get("/api/arr/status")
async def get_arr_status(request: Request):
    """Return status of Sonarr, Radarr, and Trakt Watchlist automation bridge."""
    is_demo = request.query_params.get("demo") == "true"
    return await arr_bridge.get_status(demo=is_demo)


@app.post("/api/arr/sync")
async def trigger_arr_sync(request: Request):
    """Trigger a synchronization of the Trakt Watchlist to Sonarr & Radarr."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return await arr_bridge.sync_watchlist(demo=True)
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    return await arr_bridge.sync_watchlist()


@app.get("/api/ecosystem")
async def get_ecosystem_status(request: Request):
    is_demo = request.query_params.get("demo") == "true"
    return await arr_bridge.get_ecosystem_status(
        demo=is_demo,
        plex_client=reverse_sync_mgr.plex,
        jellyfin_client=reverse_sync_mgr.jellyfin,
        emby_client=reverse_sync_mgr.emby,
        simkl_client=simkl,
        anilist_client=anilist,
        mal_client=mal,
    )


class CrossSyncExecuteRequest(BaseModel):
    item_ids: Optional[list[str]] = None
    direction: str = "both"  # "both", "trakt_to_simkl", "simkl_to_trakt"


@app.get("/api/cross-sync/status")
async def get_cross_sync_status(request: Request):
    """Return operational status and reconciliation metrics for Trakt <-> Simkl."""
    is_demo = request.query_params.get("demo") == "true"
    return await cross_tracker_sync.get_status(demo=is_demo)


@app.get("/api/cross-sync/diff")
async def get_cross_sync_diff(request: Request, force: bool = False):
    """Scan and return cross-tracker discrepancies between Trakt and Simkl."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        demo_diff = demo_mgr.get_demo_cross_tracker_diff()
        return {"status": "ok", "diff": demo_diff, "count": len(demo_diff)}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    diff = await cross_tracker_sync.scan_discrepancies(force=force)
    return {"status": "ok", "diff": diff, "count": len(diff)}


@app.post("/api/cross-sync/scan")
async def scan_cross_sync_discrepancies(request: Request):
    """Trigger an on-demand scan of discrepancies between Trakt and Simkl."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        demo_diff = demo_mgr.get_demo_cross_tracker_diff()
        return {"status": "ok", "diff": demo_diff, "count": len(demo_diff)}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    diff = await cross_tracker_sync.scan_discrepancies(force=True)
    return {"status": "ok", "diff": diff, "count": len(diff)}


@app.post("/api/cross-sync/execute")
async def execute_cross_sync(payload: CrossSyncExecuteRequest, request: Request):
    """Execute cross-tracker reconciliation for selected items or all in a direction."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return await cross_tracker_sync.execute_sync(item_ids=payload.item_ids, direction=payload.direction, demo=True)
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if cloud_sync_mgr.sync_mutex.locked():
        raise HTTPException(status_code=409, detail="A cloud or reconciliation sync is already in progress")
    async with cloud_sync_mgr.sync_mutex:
        return await cross_tracker_sync.execute_sync(item_ids=payload.item_ids, direction=payload.direction)


@app.get("/api/cross-sync/progress")
async def get_cross_sync_progress(request: Request):
    """Poll live cross-tracker sync progress."""
    return cross_tracker_sync._sync_progress


class TestWebhookRequest(BaseModel):
    event: str = "media.scrobble"
    media_type: str = "episode"
    title: str = "Synthetic Test Title"
    show_title: Optional[str] = "Synthetic Test Show"
    season: Optional[int] = 1
    episode: Optional[int] = 1
    year: Optional[int] = 2025
    progress: float = 100.0
    execute_trakt: bool = False
    library_section_title: Optional[str] = None
    file_path: Optional[str] = None


@app.post("/api/test/webhook")
async def trigger_test_webhook(payload: TestWebhookRequest, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    username = (Config.PLEX_ALLOWED_USERS[0] if Config.PLEX_ALLOWED_USERS else "admin")
    mock_payload = {
        "event": payload.event,
        "user": True,
        "Account": {"id": 1, "title": username},
        "Server": {"title": "SyntheticPlexServer", "uuid": "synthetic-uuid"},
        "Player": {"title": Config.CO_WATCH_PLAYERS[0] if Config.CO_WATCH_PLAYERS else "Living Room TV", "local": True},
        "Metadata": {
            "librarySectionType": "show" if payload.media_type == "episode" else "movie",
            "librarySectionTitle": payload.library_section_title or ("TV Shows" if payload.media_type == "episode" else "Movies"),
            "type": payload.media_type,
            "title": payload.title,
            "year": payload.year,
            "duration": 3600000,
            "viewOffset": int(3600000 * (payload.progress / 100.0)),
            "grandparentTitle": payload.show_title if payload.media_type == "episode" else None,
            "parentIndex": payload.season if payload.media_type == "episode" else None,
            "index": payload.episode if payload.media_type == "episode" else None,
            "Guid": [{"id": "imdb://tt0000001"}],
            "Media": [{"Part": [{"file": payload.file_path}]}] if payload.file_path else [],
        }
    }

    parsed = parse_plex_webhook(
        mock_payload,
        allowed_users=Config.PLEX_ALLOWED_USERS,
        allowed_libraries=Config.ALLOWED_LIBRARIES,
        excluded_libraries=get_effective_excluded_libraries(),
    )
    if not parsed:
        return {"status": "ignored", "reason": "Filtered or invalid media payload"}

    allowed, bypass_reason = settings_mgr.is_media_allowed(parsed)
    if not allowed:
        return {"status": "ignored", "reason": bypass_reason}

    eligible, reason = cowatch_mgr.check_cowatch_eligibility(parsed)
    targets = cowatch_mgr.resolve_targets(parsed)
    simulated_result: dict[str, Any] = {"status": "ok", "mode": "simulated"}

    if payload.execute_trakt:
        if trakt.is_authenticated():
            simulated_result = await trakt.sync_history(parsed.to_trakt_history_payload())
            if targets or (eligible and Config.CO_WATCH_USER):
                await execute_cowatch_sync(parsed, "test_webhook", targets=targets)
        else:
            simulated_result = {"status": "warning", "message": "Trakt not authenticated"}

    action_name = "test_webhook"
    primary_target = targets[0] if targets else Config.CO_WATCH_USER
    cw_info = {"synced": bool(targets or eligible) and payload.execute_trakt, "reason": reason, "target": primary_target, "targets": targets}
    log_event(parsed, action_name, simulated_result, cowatch_status=cw_info)

    return {
        "status": "success",
        "parsed": {
            "title": parsed.title,
            "media_type": parsed.media_type,
            "show_title": parsed.show_title,
            "season": parsed.season,
            "episode": parsed.episode,
            "progress": parsed.progress,
            "duration_ms": parsed.duration_ms,
            "view_offset_ms": parsed.view_offset_ms,
        },
        "cowatch": {
            "eligible": eligible or bool(targets),
            "reason": reason,
            "partner": Config.CO_WATCH_USER,
            "targets": targets,
        },
        "result": simulated_result,
    }


_failed_unlock_attempts: dict[str, list[float]] = {}
MAX_FAILED_UNLOCK_ATTEMPTS = 5
UNLOCK_LOCKOUT_SECONDS = 60


def check_unlock_rate_limit(client_ip: str) -> None:
    now = time.time()
    # Prune all stale entries across all IPs to prevent unbounded growth
    for ip in list(_failed_unlock_attempts):
        _failed_unlock_attempts[ip] = [t for t in _failed_unlock_attempts[ip] if now - t < UNLOCK_LOCKOUT_SECONDS]
        if not _failed_unlock_attempts[ip]:
            del _failed_unlock_attempts[ip]
    # Safety valve: if still oversized (e.g. legitimate traffic spike), evict oldest half
    if len(_failed_unlock_attempts) > 10_000:
        evict = sorted(_failed_unlock_attempts, key=lambda ip: min(_failed_unlock_attempts[ip]))[:5_000]
        for ip in evict:
            del _failed_unlock_attempts[ip]
    recent_attempts = _failed_unlock_attempts.get(client_ip, [])
    if len(recent_attempts) >= MAX_FAILED_UNLOCK_ATTEMPTS:
        retry_after = int(UNLOCK_LOCKOUT_SECONDS - (now - recent_attempts[0]))
        logger.warning(
            "Admin unlock rate limit exceeded for client %s (%d attempts). Locked out for %ds.",
            log_mgr.sanitize_line(client_ip),
            len(recent_attempts),
            max(1, retry_after),
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed unlock attempts. Please wait {max(1, retry_after)} seconds before trying again.",
            headers={"Retry-After": str(max(1, retry_after))},
        )


def record_failed_unlock(client_ip: str) -> None:
    now = time.time()
    if client_ip not in _failed_unlock_attempts:
        _failed_unlock_attempts[client_ip] = []
    _failed_unlock_attempts[client_ip].append(now)
    logger.warning("Failed admin unlock attempt from %s", log_mgr.sanitize_line(client_ip))


def record_successful_unlock(client_ip: str) -> None:
    _failed_unlock_attempts.pop(client_ip, None)


class AdminUnlockRequest(BaseModel):
    token: str


@app.post("/api/admin/unlock")
def admin_unlock(payload: AdminUnlockRequest, request: Request, response: Response):
    if not Config.WEBHOOK_SECRET:
        return {"status": "ok", "message": "Admin authentication not required"}

    client_ip = request.client.host if request.client else "unknown"
    check_unlock_rate_limit(client_ip)

    if not secrets.compare_digest(payload.token, Config.WEBHOOK_SECRET):
        record_failed_unlock(client_ip)
        raise HTTPException(status_code=401, detail="Invalid admin secret")

    record_successful_unlock(client_ip)
    samesite_policy = getattr(Config, "COOKIE_SAMESITE", "lax") or "lax"
    csrf_token = secrets.token_hex(16)
    response.set_cookie(
        key="admin_token",
        value=Config.WEBHOOK_SECRET,
        httponly=True,
        secure=is_https_request(request),
        samesite=samesite_policy,
        path="/",
        max_age=86400 * 30,
    )
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=is_https_request(request),
        samesite=samesite_policy,
        path="/",
        max_age=86400 * 30,
    )
    return {"status": "ok", "message": "Admin mode unlocked", "csrf_token": csrf_token}


@app.post("/api/admin/lock")
def admin_lock(response: Response):
    response.delete_cookie(key="admin_token")
    response.delete_cookie(key="csrf_token")
    return {"status": "ok", "message": "Admin mode locked"}


class DevicePollRequest(BaseModel):
    device_code: str
    user: Optional[str] = None


@app.post("/api/auth/start")
async def auth_start(request: Request, user: Optional[str] = None):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    query_user = request.query_params.get("user") or user
    target_client = user_mgr.get_client(query_user)
    try:
        data = await target_client.generate_device_code()
        return data
    except Exception as e:
        logger.error(f"Failed to generate device code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/poll")
async def auth_poll(payload: DevicePollRequest, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not payload.device_code or not str(payload.device_code).strip() or payload.device_code == "undefined":
        return {"status": "error", "message": "Missing device_code"}
    global trakt_user_profile
    target_client = user_mgr.get_client(payload.user)
    try:
        res = await target_client.poll_for_token(payload.device_code)
        if "access_token" in res:
            if not payload.user or payload.user == "default":
                trakt_user_profile = None  # Invalidate cached profile on default login
            return {"status": "success", "user": payload.user or "default"}
        elif res.get("status") in ("pending", "slow_down"):
            return res
        return {"status": "pending"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/trakt/status")
async def get_trakt_status(request: Request, user: Optional[str] = None):
    """Return current connection and authentication status for Trakt."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return {
            "enabled": True,
            "configured": True,
            "authenticated": True,
            "user": "demo_viewer",
        }
    query_user = request.query_params.get("user") or user
    target_client = user_mgr.get_client(query_user)
    auth = target_client.is_authenticated()
    username = None
    if auth:
        if not query_user or query_user == "default":
            prof = await get_cached_trakt_profile()
            username = prof.get("username") if prof else None
        else:
            username = query_user
    return {
        "enabled": target_client.is_enabled(),
        "configured": bool(target_client.effective_client_id),
        "authenticated": auth,
        "user": username,
        "token_info": target_client.get_token_info(),
    }


@app.post("/api/trakt/disconnect")
async def disconnect_trakt(request: Request, user: Optional[str] = None):
    """Disconnect Trakt account and delete saved tokens."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    query_user = request.query_params.get("user") or user
    target_client = user_mgr.get_client(query_user)
    target_client.delete_tokens()
    global trakt_user_profile
    if not query_user or query_user == "default":
        trakt_user_profile = None
    return {"status": "ok", "message": "Trakt disconnected"}


class SimklPollRequest(BaseModel):
    user_code: str
    device_code: Optional[str] = None
    user: Optional[str] = None


@app.get("/api/simkl/status")
async def get_simkl_status(request: Request):
    """Return current connection and authentication status for Simkl."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return {
            "enabled": True,
            "configured": True,
            "authenticated": True,
            "user": "demo_viewer",
            "account_id": 123456,
            "timezone": "America/New_York",
        }
    target_user = request.query_params.get("user")
    target_client = user_mgr.get_tracker_client(target_user, "simkl") if target_user else simkl
    status = await target_client.check_connection()
    status["enabled"] = target_client.is_enabled()
    status["configured"] = bool(target_client.effective_client_id)
    return status


@app.post("/api/simkl/pin")
async def get_simkl_pin(request: Request):
    """Obtain a new Device PIN / user_code to authorize Simkl via browser."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    target_user = request.query_params.get("user")
    target_client = user_mgr.get_tracker_client(target_user, "simkl") if target_user else simkl
    try:
        data = await target_client.get_device_pin()
        if isinstance(data, dict) and "error" in data:
            error_msg = data.get("error", "Failed to obtain Simkl PIN")
            if data.get("detail"):
                error_msg = f"{error_msg}: {data['detail']}"
            raise HTTPException(status_code=400, detail=error_msg)
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate Simkl device PIN: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/simkl/poll")
async def poll_simkl_pin(payload: SimklPollRequest, request: Request):
    """Poll Simkl to check if the user authorized the device PIN."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not payload.user_code or not str(payload.user_code).strip() or payload.user_code == "undefined":
        return {"status": "error", "result": "error", "message": "Missing user_code"}
    target_user = payload.user or request.query_params.get("user")
    target_client = user_mgr.get_tracker_client(target_user, "simkl") if target_user else simkl
    try:
        res = await target_client.poll_device_pin(payload.user_code, device_code=payload.device_code)
        return res
    except Exception as e:
        return {"result": "error", "message": str(e)}


@app.post("/api/simkl/disconnect")
async def disconnect_simkl(request: Request):
    """Disconnect Simkl account and delete saved tokens."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    target_user = request.query_params.get("user")
    if target_user:
        user_mgr.disconnect_user_tracker(target_user, "simkl")
        return {"status": "ok", "message": f"Simkl disconnected for {target_user}"}
    simkl.delete_tokens()
    return {"status": "ok", "message": "Simkl disconnected"}


@app.get("/auth/simkl", response_class=HTMLResponse)
async def auth_simkl_page(request: Request):
    """Render dedicated Simkl authorization page."""
    if not is_admin_request(request):
        return HTMLResponse(content=AUTH_LOCKED_HTML, status_code=401)
    return HTMLResponse(content=AUTH_SIMKL_HTML)


class TokenSubmitRequest(BaseModel):
    token: str
    user: Optional[str] = None


# --- AniList Anime Tracker Endpoints ---
@app.get("/api/anilist/status")
async def get_anilist_status(request: Request):
    """Return current connection and authentication status for AniList."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_anilist_status()
    target_user = request.query_params.get("user")
    target_client = user_mgr.get_tracker_client(target_user, "anilist") if target_user else anilist
    status = await target_client.check_connection()
    status["enabled"] = target_client.is_enabled()
    status["configured"] = bool(getattr(Config, "ANILIST_CLIENT_ID", "") or target_client.is_authenticated())
    return status


@app.post("/api/anilist/token")
async def save_anilist_token(payload: TokenSubmitRequest, request: Request):
    """Save an AniList personal access token and test connectivity."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    token_str = payload.token.strip()
    if not token_str:
        raise HTTPException(status_code=400, detail="Token cannot be empty")
    target_user = payload.user or request.query_params.get("user")
    target_client = user_mgr.get_tracker_client(target_user, "anilist") if target_user else anilist
    target_client.access_token = token_str
    conn = await target_client.check_connection()
    if conn.get("status") == "connected":
        target_client.save_tokens({
            "access_token": token_str,
            "user_name": conn.get("user"),
            "user_avatar": conn.get("avatar"),
            "user_id": conn.get("id"),
        })
        return {"status": "success", "user": conn.get("user"), "id": conn.get("id")}
    else:
        target_client.load_tokens()
        raise HTTPException(status_code=400, detail=conn.get("error", "Failed to connect with provided AniList token"))


@app.post("/api/anilist/disconnect")
async def disconnect_anilist(request: Request):
    """Disconnect AniList account and delete stored tokens."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    target_user = request.query_params.get("user")
    if target_user:
        user_mgr.disconnect_user_tracker(target_user, "anilist")
        return {"status": "ok", "message": f"AniList disconnected for {target_user}"}
    anilist.delete_tokens()
    return {"status": "ok", "message": "AniList disconnected"}


@app.get("/auth/anilist", response_class=HTMLResponse)
async def auth_anilist_page(request: Request):
    """Render dedicated AniList authorization page."""
    if not is_admin_request(request):
        return HTMLResponse(content=AUTH_LOCKED_HTML, status_code=401)

    html = AUTH_ANILIST_HTML.replace("{{PAGE_TITLE}}", "Connect AniList &bull; Omniscrobble")
    html = html.replace("{{H1_TEXT}}", "AniList Anime Tracker")
    html = html.replace(
        "{{P_DESC}}",
        "Link your AniList account to enable real-time anime scrobbling and ratings sync via the official GraphQL API.",
    )
    banner = ""
    if anilist.is_authenticated():
        uname = anilist.user_name or "Linked User"
        banner = f'<div class="banner">✓ Currently linked to AniList as <strong>@{uname}</strong>. Entering a new token will update credentials.</div>'
    html = html.replace("{{ALREADY_CONNECTED_BANNER}}", banner)
    return HTMLResponse(content=html)


# --- MyAnimeList (MAL) Anime Tracker Endpoints ---
@app.get("/api/mal/status")
async def get_mal_status(request: Request):
    """Return current connection and authentication status for MyAnimeList."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_mal_status()
    target_user = request.query_params.get("user")
    target_client = user_mgr.get_tracker_client(target_user, "mal") if target_user else mal
    status = await target_client.check_connection()
    status["enabled"] = target_client.is_enabled()
    status["configured"] = bool(target_client.effective_client_id or target_client.is_authenticated())
    return status


@app.post("/api/mal/token")
async def save_mal_token(payload: TokenSubmitRequest, request: Request):
    """Save a MyAnimeList access token and test connectivity."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    token_str = payload.token.strip()
    if not token_str:
        raise HTTPException(status_code=400, detail="Token cannot be empty")
    target_user = payload.user or request.query_params.get("user")
    target_client = user_mgr.get_tracker_client(target_user, "mal") if target_user else mal
    target_client.access_token = token_str
    conn = await target_client.check_connection()
    if conn.get("status") == "connected":
        target_client.save_tokens({
            "access_token": token_str,
            "user_name": conn.get("user"),
            "user_avatar": conn.get("avatar"),
            "user_id": conn.get("id"),
        })
        return {"status": "success", "user": conn.get("user"), "id": conn.get("id")}
    else:
        target_client.load_tokens()
        raise HTTPException(status_code=400, detail=conn.get("error", "Failed to connect with provided MyAnimeList token"))


@app.post("/api/mal/disconnect")
async def disconnect_mal(request: Request):
    """Disconnect MyAnimeList account and delete stored tokens."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    target_user = request.query_params.get("user")
    if target_user:
        user_mgr.disconnect_user_tracker(target_user, "mal")
        return {"status": "ok", "message": f"MyAnimeList disconnected for {target_user}"}
    mal.delete_tokens()
    return {"status": "ok", "message": "MyAnimeList disconnected"}


@app.get("/auth/mal", response_class=HTMLResponse)
async def auth_mal_page(request: Request):
    """Render dedicated MyAnimeList authorization page."""
    if not is_admin_request(request):
        return HTMLResponse(content=AUTH_LOCKED_HTML, status_code=401)

    html = AUTH_MAL_HTML.replace("{{PAGE_TITLE}}", "Connect MyAnimeList &bull; Omniscrobble")
    html = html.replace("{{H1_TEXT}}", "MyAnimeList (MAL) Integration")
    html = html.replace(
        "{{P_DESC}}",
        "Link your MyAnimeList account to scrobble anime episode progress and sync ratings via the MAL v2 API.",
    )
    banner = ""
    if mal.is_authenticated():
        uname = mal.user_name or "Linked User"
        banner = f'<div class="banner">✓ Currently linked to MyAnimeList as <strong>@{uname}</strong>. Entering a new token will update credentials.</div>'
    html = html.replace("{{ALREADY_CONNECTED_BANNER}}", banner)
    return HTMLResponse(content=html)


# --- Anime Inspection Diagnostic Endpoint ---
@app.get("/api/anime/resolve")
async def resolve_anime_api(request: Request, title: str, year: Optional[int] = None):
    """Diagnostic endpoint to test anime heuristics and AniList/MAL metadata matching."""
    test_media = ParsedMedia(
        raw_payload={},
        event="media.play",
        media_type="episode",
        title=title,
        show_title=title,
        year=year,
        show_year=year,
        username="admin",
        progress=0.0,
    )
    resolved = await anime_resolver.resolve(test_media)
    return {
        "title": title,
        "year": year,
        "is_anime": bool(resolved and resolved.get("is_anime")),
        "resolved": resolved,
    }


# --- Multi-Tracker Hub & Cloud Tracker Diagnostic Endpoints ---
@app.get("/api/trackers/status")
async def get_multi_trackers_status(request: Request):
    """Return categorized diagnostics and connectivity status across all 9 supported trackers."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_trackers_status()
    status = await multi_tracker.get_status()
    if not is_admin_request(request):
        for tracker_info in status.get("trackers", {}).values():
            if isinstance(tracker_info, dict):
                if tracker_info.get("user"):
                    tracker_info["user"] = mask_username(tracker_info["user"])
                if tracker_info.get("username"):
                    tracker_info["username"] = mask_username(tracker_info["username"])
                if tracker_info.get("account_id"):
                    tracker_info["account_id"] = "******"
    return status


@app.get("/api/tmdb/status")
async def get_tmdb_status(request: Request):
    """Return current connection and authentication status for TMDb."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_tmdb_status()
    status = await tmdb.check_connection()
    status["enabled"] = settings_mgr.is_tracker_enabled("tmdb")
    return status


@app.get("/api/kitsu/status")
async def get_kitsu_status(request: Request):
    """Return current connection and authentication status for Kitsu."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_kitsu_status()
    status = await kitsu.check_connection()
    status["enabled"] = settings_mgr.is_tracker_enabled("kitsu")
    if not is_admin_request(request):
        if status.get("user"):
            status["user"] = mask_username(status["user"])
        if status.get("username"):
            status["username"] = mask_username(status["username"])
        if status.get("message") and kitsu.user_name:
            status["message"] = f"Connected as @{mask_username(kitsu.user_name)}"
    return status


@app.get("/api/letterboxd/status")
async def get_letterboxd_status(request: Request):
    """Return current status and diary telemetry for Letterboxd."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_letterboxd_status()
    status = await letterboxd.check_connection()
    status["enabled"] = settings_mgr.is_tracker_enabled("letterboxd")
    if not is_admin_request(request):
        if status.get("user"):
            status["user"] = mask_username(status["user"])
        if status.get("username"):
            status["username"] = mask_username(status["username"])
        if status.get("message") and letterboxd.username:
            count = status.get("diary_count", 0)
            status["message"] = f"Diary ready ({count} films logged for @{mask_username(letterboxd.username)})"
    return status


@app.get("/api/serializd/status")
async def get_serializd_status(request: Request):
    """Return current connection and authentication status for Serializd."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_serializd_status()
    status = await serializd.check_connection()
    status["enabled"] = settings_mgr.is_tracker_enabled("serializd")
    if not is_admin_request(request):
        if status.get("user"):
            status["user"] = mask_username(status["user"])
        if status.get("username"):
            status["username"] = mask_username(status["username"])
        if status.get("message") and serializd.username:
            status["message"] = f"Connected as @{mask_username(serializd.username)}"
    return status


@app.get("/api/mdblist/status")
async def get_mdblist_status(request: Request):
    """Return current status and configuration for MDBList."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_mdblist_status()
    status = await mdblist.check_connection()
    status["enabled"] = settings_mgr.is_tracker_enabled("mdblist")
    if not is_admin_request(request):
        if status.get("user"):
            status["user"] = mask_username(status["user"])
        if status.get("username"):
            status["username"] = mask_username(status["username"])
    return status


@app.get("/api/relay/status")
async def get_relay_status(request: Request):
    """Return connection instructions and compatibility status for SeriesGuide & Showly cloud relay."""
    base_url = str(request.base_url).rstrip("/")
    return {
        "status": "active",
        "supported_apps": ["SeriesGuide", "Showly"],
        "description": "Mobile tracking applications connect via direct Trakt cloud synchronization or native webhook relay.",
        "endpoints": {
            "trakt_sync": "Automated two-way scrobble via Trakt Cloud OAuth",
            "generic_webhook": f"{base_url}/webhook",
        },
        "apps": [
            {
                "name": "SeriesGuide",
                "platform": "Android",
                "mode": "Trakt Cloud Sync",
                "status": "supported",
                "docs_url": "https://seriesgui.de/",
            },
            {
                "name": "Showly",
                "platform": "Android",
                "mode": "Trakt Cloud Sync",
                "status": "supported",
                "docs_url": "https://github.com/michaldrabik/Showly-2.0",
            },
        ],
    }


@app.get("/api/letterboxd/export")
async def export_letterboxd_csv(request: Request):
    """Generate and download RFC-4180 CSV export for 1-click import into Letterboxd."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required to export Letterboxd diary")
    csv_content = letterboxd.generate_csv_export()
    filename = f"letterboxd_diary_{datetime.datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/letterboxd/diary")
async def get_letterboxd_diary(request: Request):
    """Return logged Letterboxd diary entries."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return [
            {"title": "Dune: Part Two", "year": 2024, "rating": 9, "watched_date": "2026-10-01", "imdb_id": "tt15239678"},
            {"title": "Oppenheimer", "year": 2023, "rating": 10, "watched_date": "2026-09-28", "imdb_id": "tt15398776"},
        ]
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required to view Letterboxd diary")
    return letterboxd.get_diary_entries()


@app.get("/api/mdblist/ratings")
async def get_mdblist_ratings(
    request: Request,
    imdb_id: Optional[str] = None,
    tmdb_id: Optional[int] = None,
    media_type: str = "movie",
):
    """Enrich media with Rotten Tomatoes, Metacritic, Letterboxd, and IMDb scores via MDBList."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return {
            "title": "Dune: Part Two",
            "year": 2024,
            "score": 88,
            "ratings": [
                {"source": "imdb", "value": 8.6, "score": 86},
                {"source": "metacritic", "value": 79, "score": 79},
                {"source": "tomatoes", "value": 92, "score": 92},
                {"source": "letterboxd", "value": 4.5, "score": 90},
            ],
        }
    if not mdblist.is_configured():
        raise HTTPException(status_code=400, detail="MDBList API key is not configured.")
    if not imdb_id and not tmdb_id:
        raise HTTPException(status_code=400, detail="Either imdb_id or tmdb_id must be provided.")
    res = await mdblist.get_item_ratings(imdb_id=imdb_id, tmdb_id=tmdb_id, media_type=media_type)
    if not res or (isinstance(res, dict) and res.get("status") == "error"):
        err_msg = res.get("error", "Failed to retrieve ratings from MDBList.") if isinstance(res, dict) else "Failed to retrieve ratings from MDBList."
        raise HTTPException(status_code=502, detail=err_msg)
    return res


class ReplayWebhookRequest(BaseModel):
    source: str = Field(..., description="Source format: plex, jellyfin, emby, radarr, sonarr, or standalone")
    payload: dict[str, Any] = Field(..., description="Raw JSON webhook payload")
    dispatch: bool = Field(False, description="If True, dispatches to actual scrobbler / multi_tracker pipeline; if False, only performs parser dry-run simulation")


@app.get("/api/debug/webhooks")
async def get_debug_webhooks(request: Request, limit: int = 15, demo: bool = False):
    """Retrieve recent captured raw webhook payloads for in-browser inspection."""
    is_demo = demo or request.query_params.get("demo") == "true"
    if is_demo:
        return {"webhooks": demo_mgr.get_demo_webhook_debug_history()[:limit]}
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin authorization required to view raw webhook payloads")
    return {"webhooks": webhook_debugger.get_history(limit=limit)}


@app.delete("/api/debug/webhooks")
async def clear_debug_webhooks(request: Request):
    """Purge the in-memory raw webhook ring buffer."""
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin authorization required to clear webhook payloads")
    webhook_debugger.clear()
    return {"status": "ok", "message": "Webhook debugger buffer cleared"}


@app.post("/api/debug/replay")
async def replay_debug_webhook(request: Request, body: ReplayWebhookRequest):
    """Replay or simulate a captured webhook payload through parser and pipeline."""
    is_demo = request.query_params.get("demo") == "true"
    if not is_admin_request(request) and not is_demo:
        raise HTTPException(status_code=403, detail="Admin authorization required to replay webhooks")

    source = body.source.lower().strip()
    raw = body.payload
    parsed: Optional[ParsedMedia] = None

    if source == "plex":
        parsed = parse_plex_webhook(
            raw,
            allowed_users=Config.PLEX_ALLOWED_USERS,
            allowed_libraries=Config.ALLOWED_LIBRARIES,
            excluded_libraries=get_effective_excluded_libraries(),
        )
    elif source == "jellyfin":
        parsed = parse_jellyfin_webhook(
            raw,
            allowed_users=Config.PLEX_ALLOWED_USERS,
            allowed_libraries=Config.ALLOWED_LIBRARIES,
            excluded_libraries=get_effective_excluded_libraries(),
        )
    elif source == "emby":
        parsed = parse_emby_webhook(
            raw,
            allowed_users=Config.PLEX_ALLOWED_USERS,
            allowed_libraries=Config.ALLOWED_LIBRARIES,
            excluded_libraries=get_effective_excluded_libraries(),
        )
    elif source == "sonarr":
        _, _, parsed = parse_sonarr_webhook(raw)
    elif source == "radarr":
        _, _, parsed = parse_radarr_webhook(raw)
    elif source in ("standalone", "player"):
        try:
            sp = StandaloneScrobblePayload(**raw)
            action_lower = sp.action.strip().lower()
            if action_lower in ("play", "start", "resume"):
                event = "media.play"
            elif action_lower in ("pause",):
                event = "media.pause"
            elif action_lower in ("stop",):
                event = "media.stop"
            elif action_lower in ("scrobble", "finish", "watched", "complete"):
                event = "media.scrobble"
            elif action_lower in ("rate", "rating"):
                event = "media.rate"
            else:
                event = f"media.{action_lower}"

            clean_ids = {str(k).lower().strip(): str(v).strip() for k, v in (sp.ids or {}).items() if v}
            parsed = ParsedMedia(
                event=event,
                media_type=sp.media_type,
                title=sp.title,
                year=sp.year,
                progress=sp.progress,
                rating=sp.rating,
                season=sp.season,
                episode=sp.episode,
                show_title=sp.show_title,
                grandparent_title=sp.show_title,
                ids=clean_ids,
                player=sp.player or "Standalone Player",
                device=sp.device or sp.player or "Standalone Device",
                username=sp.user or (Config.PLEX_ALLOWED_USERS[0] if Config.PLEX_ALLOWED_USERS else "user"),
                duration_ms=sp.duration_ms,
                view_offset_ms=sp.view_offset_ms,
            )
        except Exception as e:
            return {"status": "error", "message": f"Invalid standalone scrobble payload: {e}"}
    else:
        return {"status": "error", "message": f"Unsupported webhook source '{source}'"}

    if not parsed:
        return {
            "status": "filtered",
            "message": "Payload was filtered out or ignored (non-media event, excluded user/library, or invalid format)",
            "source": source,
        }

    parsed_dict = {
        "event": parsed.event,
        "media_type": parsed.media_type,
        "title": parsed.title,
        "year": parsed.year,
        "progress": parsed.progress,
        "show_title": parsed.show_title,
        "season": parsed.season,
        "episode": parsed.episode,
        "user": parsed.username or parsed.user,
        "player": parsed.player,
        "ids": parsed.ids,
        "rating": parsed.rating,
    }

    if not body.dispatch:
        return {
            "status": "simulated",
            "message": "Payload successfully parsed in dry-run mode (not dispatched)",
            "parsed": parsed_dict,
        }

    # Pipeline dispatch strictly requires admin authorization (cannot be bypassed via ?demo=true)
    if not is_admin_request(request):
        raise HTTPException(
            status_code=403,
            detail="Admin authorization required to dispatch replayed webhooks into the pipeline",
        )

    dispatch_res = await process_media_event(parsed, endpoint_name=f"debug_replay_{source}")
    return {
        "status": "dispatched",
        "message": "Payload parsed and dispatched through pipeline",
        "parsed": parsed_dict,
        "result": dispatch_res,
    }


@app.get("/api/analytics/summary")
async def get_analytics_summary(request: Request, period: str = "all", demo: bool = False):
    """Retrieve viewing analytics metrics across a given time window (all, year, month, week)."""
    is_demo = demo or request.query_params.get("demo") == "true"
    is_admin = is_admin_request(request)
    summary = analytics_mgr.get_summary(period=period, demo=is_demo, is_admin=is_admin)
    return summary


@app.get("/api/analytics/wrapped")
async def get_analytics_wrapped(request: Request, year: Optional[int] = None, demo: bool = False):
    """Retrieve the OmniWrapped annual viewing retrospective and archetype summary."""
    is_demo = demo or request.query_params.get("demo") == "true"
    is_admin = is_admin_request(request)
    wrapped = analytics_mgr.get_omniwrapped(year=year, demo=is_demo, is_admin=is_admin)
    return wrapped


@app.get('/auth', response_class=HTMLResponse)
async def auth_page(request: Request, user: Optional[str] = None):
    if not is_admin_request(request):
        return HTMLResponse(content=AUTH_LOCKED_HTML, status_code=401)

    target_uname = user.strip() if user else ''
    target_client = user_mgr.get_client(target_uname)
    already_connected = target_client.is_authenticated()

    if target_uname:
        page_title = f'Link Trakt • @{target_uname}'
        h1_text = f'Link Trakt for @{target_uname}'
        p_desc = f'Authorize this scrobbler to record playback and sync history with Trakt for Plex user <strong>@{target_uname}</strong>.'
        banner_user_str = f'Account for <strong>@{target_uname}</strong> is currently linked'
    else:
        profile = await get_cached_trakt_profile()
        default_username = profile.get('username') if profile else ''
        page_title = 'Link Trakt Account'
        h1_text = 'Link Trakt Account'
        p_desc = 'Authorize this scrobbler to record playback and sync history with your Trakt profile.'
        banner_user_str = f'Currently linked to <strong>@{default_username}</strong>' if default_username else 'Currently linked'

    already_connected_banner = (
        f'<div style="background:#064e3b;border:1px solid #059669;color:#6ee7b7;padding:12px;border-radius:8px;margin-bottom:20px;font-size:14px;">'
        f'{banner_user_str}. You can authorize again below to reconnect or switch accounts.</div>'
        if already_connected
        else ''
    )

    rendered = (
        AUTH_HTML
        .replace('{{PAGE_TITLE}}', page_title)
        .replace('{{H1_TEXT}}', h1_text)
        .replace('{{P_DESC}}', p_desc)
        .replace('{{ALREADY_CONNECTED_BANNER}}', already_connected_banner)
        .replace('{{TARGET_UNAME}}', target_uname)
    )
    return HTMLResponse(content=rendered)

@app.get("/demo", response_class=HTMLResponse)
async def demo_dashboard(request: Request, response: Response):
    return await render_dashboard_response(request, response, is_demo=True)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, response: Response):
    is_demo = request.query_params.get("demo") == "true"
    return await render_dashboard_response(request, response, is_demo=is_demo)


async def render_dashboard_response(request: Request, response: Response, is_demo: bool = False) -> HTMLResponse:
    if is_demo:
        is_admin = True
        auth_status = True
        raw_username = "demo_viewer"
        display_username = "demo_viewer"
        pending_queue = 0
        notif_summary = "Discord, Telegram"
        allowed_users_display = "demo_viewer, demo_partner"
        allowed_libs_display = "Movies, TV Shows, Anime"
        sync_collection_display = "On"
        token_health_str = "Healthy • Auto-renews in 84d"
        token_health_color = "#10b981"
        status_badge = '<a href="javascript:void(0)" style="background:#10b981;color:#fff;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;">Connected as @demo_viewer &bull; Demo</a>'
        admin_btn = '<span style="font-size:12px;color:#38bdf8;background:#1e293b;border:1px solid #334155;padding:4px 10px;border-radius:6px;font-weight:600;">👑 Demo Admin</span>'
        full_webhook_url = "https://plex.example.com/webhook?token=demo_webhook_secret_xyz"
        full_jellyfin_url = "https://jellyfin.example.com/webhook/jellyfin?token=demo_webhook_secret_xyz"
        full_emby_url = "https://emby.example.com/webhook/emby?token=demo_webhook_secret_xyz"
        masked_webhook_url = full_webhook_url
        demo_banner = '<div style="background:linear-gradient(90deg, #1e3a8a, #0284c7);color:#ffffff;padding:12px 18px;border-radius:10px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 4px 6px -1px rgba(0,0,0,0.3);flex-wrap:wrap;gap:10px;"><div style="display:flex;align-items:center;gap:10px;"><span style="font-size:18px;">🎭</span><div><strong style="color:#ffffff;">Demo Mode Active:</strong><span style="color:#e0f2fe;font-size:13px;margin-left:4px;">Simulated authenticated view with mock information. No real accounts or tokens are exposed.</span></div></div><a href="/" style="background:rgba(255,255,255,0.2);color:#ffffff;text-decoration:none;padding:5px 12px;border-radius:6px;font-weight:600;font-size:12px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(255,255,255,0.3)\'" onmouseout="this.style.background=\'rgba(255,255,255,0.2)\'">Exit Demo &rarr;</a></div>'
        demo_header_btn = '<a href="/" class="btn-sm" style="background:#0284c7;border:1px solid #38bdf8;color:#ffffff;text-decoration:none;font-weight:600;display:inline-flex;align-items:center;gap:5px;transition:opacity 0.15s;" onmouseover="this.style.opacity=\'0.9\'" onmouseout="this.style.opacity=\'1\'" title="Exit demo mode">✕ Exit Demo</a>'
        demo_footer_link = '<a href="/" style="color:#38bdf8;text-decoration:none;font-weight:600;">Exit Demo</a>'
    else:
        demo_banner = ""
        demo_header_btn = '<a href="/demo" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#94a3b8;text-decoration:none;font-weight:600;display:inline-flex;align-items:center;gap:5px;transition:color 0.15s, border-color 0.15s;" onmouseover="this.style.color=\'#f8fafc\';this.style.borderColor=\'#475569\'" onmouseout="this.style.color=\'#94a3b8\';this.style.borderColor=\'#334155\'" title="Preview dashboard with mock data">🎭 Demo</a>'
        demo_footer_link = f'<a href="/demo" style="color: #64748b; text-decoration: none; font-weight: 500; transition: color 0.15s;" onmouseover="this.style.color=\'#f8fafc\'" onmouseout="this.style.color=\'#64748b\'">🎭 Demo Mode</a>'
        # Auto-login admin if valid ?token= passed in URL
        query_token = request.query_params.get("token")
        if query_token and Config.WEBHOOK_SECRET and secrets.compare_digest(query_token, Config.WEBHOOK_SECRET):
            response.set_cookie(
                key="admin_token",
                value=Config.WEBHOOK_SECRET,
                httponly=True,
                secure=is_https_request(request),
                samesite="lax",
                path="/",
                max_age=86400 * 30,
            )

        is_admin = is_admin_request(request)
        auth_status = trakt.is_authenticated()
        profile = await get_cached_trakt_profile() if auth_status else None
        raw_username = profile.get("username") if profile else None

        display_username = raw_username if is_admin else mask_username(raw_username)
        pending_queue = queue_mgr.get_pending_count()

        notif_status = notifier.get_status()
        enabled_notifs = []
        if notif_status.get("discord"):
            enabled_notifs.append("Discord")
        if notif_status.get("telegram"):
            enabled_notifs.append("Telegram")
        if notif_status.get("ntfy"):
            enabled_notifs.append("Ntfy")
        if notif_status.get("pushover"):
            enabled_notifs.append("Pushover")
        notif_summary = ", ".join(enabled_notifs) if enabled_notifs else "Off"

        # Library filtering display
        if Config.ALLOWED_LIBRARIES:
            allowed_libs_display = ", ".join(Config.ALLOWED_LIBRARIES)
        elif Config.EXCLUDED_LIBRARIES:
            allowed_libs_display = "All except " + ", ".join(Config.EXCLUDED_LIBRARIES)
        else:
            allowed_libs_display = "All Libraries"

        sync_collection_display = "On" if Config.SYNC_COLLECTION else "Off"

        # Token health calculation
        token_info = trakt.get_token_info()
        if auth_status:
            if token_info.get("healthy"):
                days = token_info.get("days_remaining", 0)
                token_health_str = f"Healthy • Auto-renews in {days}d"
                token_health_color = "#10b981"
            else:
                token_health_str = "Token Expired / Refresh Needed"
                token_health_color = "#ef4444"
        else:
            token_health_str = "Not Linked"
            token_health_color = "#94a3b8"

        # Header status badge
        trakt_enabled = settings_mgr.is_tracker_enabled("trakt")
        if auth_status:
            user_label = f"Connected as @{display_username}" if display_username else "Connected"
            if not trakt_enabled:
                paused_label = f"Paused (@{display_username})" if display_username else "Trakt Paused"
                if is_admin:
                    status_badge = f'<a href="/auth" style="background:#334155;border:1px solid #64748b;color:#cbd5e1;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;">⏸️ {paused_label} &bull; Manage</a>'
                else:
                    status_badge = f'<a href="javascript:void(0)" onclick="openUnlockModal()" style="background:#1e293b;border:1px solid #475569;color:#94a3b8;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;" title="Click to unlock admin access">⏸️ {paused_label} &bull; 🔒 Locked</a>'
            elif is_admin:
                status_badge = f'<a href="/auth" style="background:#10b981;color:#fff;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;">{user_label} &bull; Manage</a>'
            else:
                status_badge = f'<a href="javascript:void(0)" onclick="openUnlockModal()" style="background:#065f46;color:#a7f3d0;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;" title="Click to unlock admin access">{user_label} &bull; 🔒 Locked</a>'
        else:
            if is_admin:
                status_badge = '<a href="/auth" style="background:#ef4444;color:#fff;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;">Not Connected &bull; Link Trakt &rarr;</a>'
            else:
                status_badge = '<a href="javascript:void(0)" onclick="openUnlockModal()" style="background:#991b1b;color:#fecaca;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;" title="Click to unlock admin access">Not Connected &bull; 🔒 Unlock &rarr;</a>'

        # Admin header controls
        if Config.WEBHOOK_SECRET:
            if is_admin:
                admin_btn = '<button onclick="lockAdmin()" class="btn-sm" style="background:#334155;color:#f87171;font-weight:600;border:1px solid #475569;">🔒 Lock Admin</button>'
            else:
                admin_btn = '<button onclick="openUnlockModal()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">🔓 Unlock Admin</button>'
        else:
            admin_btn = '<span style="font-size:12px;color:#94a3b8;background:#1e293b;padding:4px 10px;border-radius:6px;border:1px solid #334155;">Open Mode</span>'

        # Allowed Plex Users display
        if Config.PLEX_ALLOWED_USERS:
            if is_admin:
                allowed_users_display = ", ".join(Config.PLEX_ALLOWED_USERS)
            else:
                allowed_users_display = ", ".join(mask_username(u) for u in Config.PLEX_ALLOWED_USERS)
        else:
            allowed_users_display = "All Users"

        # Base URL for webhooks
        if Config.EXTERNAL_URL:
            base_url = Config.EXTERNAL_URL.rstrip("/")
        else:
            base_url = str(request.base_url).rstrip("/")
            if is_https_request(request) and base_url.startswith("http://"):
                base_url = "https://" + base_url[len("http://"):]
        if Config.WEBHOOK_SECRET:
            full_webhook_url = f"{base_url}/webhook?token={Config.WEBHOOK_SECRET}"
            full_jellyfin_url = f"{base_url}/webhook/jellyfin?token={Config.WEBHOOK_SECRET}"
            full_emby_url = f"{base_url}/webhook/emby?token={Config.WEBHOOK_SECRET}"
            scheme = request.base_url.scheme or "http"
            port_suffix = ":●●●●" if request.base_url.port else ""
            masked_webhook_url = f"{scheme}://●●●●●●●●{port_suffix}/webhook?token=●●●●●●●●"
        else:
            full_webhook_url = f"{base_url}/webhook"
            full_jellyfin_url = f"{base_url}/webhook/jellyfin"
            full_emby_url = f"{base_url}/webhook/emby"
            masked_webhook_url = full_webhook_url

    settings_header_btn = (
        '<button onclick="openSettingsModal()" class="btn-sm" '
        'style="background:#1e293b;border:1px solid #475569;color:#f8fafc;padding:6px 12px;font-size:12px;cursor:pointer;'
        'display:inline-flex;align-items:center;gap:6px;white-space:nowrap;font-weight:600;" '
        'title="Configure Media Servers, Trackers, and Automation"><span>⚙️</span><span>Settings Hub</span></button>'
    )

    # Events rows & pagination metadata
    events_list = demo_mgr.get_demo_events() if is_demo else list(recent_events)
    rows, events_page_info, events_page_num, events_next_disabled = dashboard_renderer.render_activity_table_rows(
        events_list=events_list,
        is_admin=is_admin,
        is_demo=is_demo,
        cowatch_user=Config.CO_WATCH_USER,
        is_cowatch_show_fn=cowatch_mgr.is_cowatch_show,
        mask_username_fn=mask_username,
    )

    webhook_html_section = dashboard_renderer.render_webhook_section(
        full_webhook_url=full_webhook_url,
        full_jellyfin_url=full_jellyfin_url,
        full_emby_url=full_emby_url,
        masked_webhook_url=masked_webhook_url,
        is_admin=is_admin,
    )

    clear_button_html = '<button onclick="clearHistory()" class="btn-sm" style="color:#f87171;">Clear</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="color:#64748b;" title="Admin unlock required to clear logs">🔒 Clear</button>'
    manual_scrobble_btn_html = '<button onclick="openManualScrobbleModal()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">🔍 Manual Scrobble</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;">🔍 Manual Scrobble</button>'

    if is_demo:
        active_sessions = [demo_mgr.get_demo_playback()]
        recently_finished = None
    else:
        active_sessions = playback_mgr.get_active_sessions(is_admin=is_admin)
        recently_finished = playback_mgr.get_recently_finished(is_admin=is_admin)

    active_playback_card_html = dashboard_renderer.render_active_playback_card(
        active_sessions=active_sessions,
        recently_finished=recently_finished,
        is_admin=is_admin,
    )

    # Co-Watching & Multi-User configuration
    if is_demo:
        cw_user = "demo_partner"
        cw_user_display = "demo_partner"
        cw_shows = demo_mgr.get_demo_cowatch_shows()
        cw_devices = demo_mgr.get_demo_cowatch_devices()
        configured_users = demo_mgr.get_demo_users()
        cw_trackers = demo_mgr.get_demo_cowatch_trackers()["trackers"]
        household_rules = demo_mgr.get_demo_household_rules()
    else:
        cw_user = Config.CO_WATCH_USER
        cw_user_display = cw_user if is_admin else "●●●●●●●●"
        cw_shows = sorted(cowatch_mgr.get_shows(), key=lambda x: x.lower())
        cw_devices = cowatch_mgr.get_devices()
        configured_users = user_mgr.list_configured_users()
        cw_trackers = user_mgr.get_user_trackers_status(cw_user) if cw_user else {}
        household_rules = household_mgr.get_rules()

    cowatch_card_html = dashboard_renderer.render_cowatch_card(
        cw_user=cw_user,
        cw_user_display=cw_user_display,
        cw_shows=cw_shows,
        cw_devices=cw_devices,
        configured_users=configured_users,
        cw_trackers=cw_trackers,
        sonarr_configured=sonarr.is_configured,
        cowatch_movies=Config.CO_WATCH_MOVIES,
        is_admin=is_admin,
        is_demo=is_demo,
        raw_username=raw_username,
        mask_username_fn=mask_username,
        household_rules=household_rules,
    )

    # Two-Way Library Reconciliation Card
    sync_status = await reverse_sync_mgr.get_status() if not is_demo else {
        "configured": True,
        "plex_configured": True,
        "plex_connected": True,
        "trakt_authenticated": True,
        "diff_count": 4,
        "interval_minutes": 0,
        "sync_on_startup": False,
        "sync_ratings": True,
    }
    bg_sync_state = cloud_sync_mgr.get_status() if not is_demo else demo_mgr.get_demo_background_sync_status()
    bg_interval_hours = getattr(Config, "BACKGROUND_CLOUD_SYNC_INTERVAL_HOURS", 24)

    reconcile_card_html = dashboard_renderer.render_reconciliation_card(
        sync_status=sync_status,
        bg_sync_state=bg_sync_state,
        bg_interval_hours=bg_interval_hours,
        is_admin=is_admin,
        repo_url=REPO_URL,
    )

    backup_card_html = dashboard_renderer.render_backup_card(is_admin=is_admin)

    # Personal Analytics & Statistics Hub Card
    if is_demo:
        analytics_data = demo_mgr.get_demo_analytics_summary()
    else:
        analytics_data = analytics_mgr.get_summary(period="all")
    analytics_card_html = dashboard_renderer.render_analytics_card(analytics_data, is_admin=is_admin)

    # Multi-Server Ecosystem Health Card
    eco_data = await arr_bridge.get_ecosystem_status(
        demo=is_demo,
        plex_client=reverse_sync_mgr.plex,
        jellyfin_client=reverse_sync_mgr.jellyfin,
        emby_client=reverse_sync_mgr.emby,
        simkl_client=simkl,
        anilist_client=anilist,
        mal_client=mal,
    )
    ecosystem_card_html = dashboard_renderer.render_ecosystem_card(
        eco_data=eco_data,
        is_admin=is_admin,
    )

    # Multi-Tracker Architecture Hub Card & Cloud Diagnostics
    if is_demo:
        trk_status_all = demo_mgr.get_demo_trackers_status()
    else:
        trk_status_all = await multi_tracker.get_status()

    simkl_card_html = dashboard_renderer.render_multi_tracker_hub_card(
        trk_status_all=trk_status_all,
        is_admin=is_admin,
        is_demo=is_demo,
        trakt_authenticated=trakt.is_authenticated(),
        mask_username_fn=mask_username,
    )
    anime_card_html = ""

    # Arr Watchlist Automation Bridge Card
    arr_status = await arr_bridge.get_status(demo=is_demo)
    arr_bridge_card_html = dashboard_renderer.render_arr_bridge_card(
        arr_status=arr_status,
        is_admin=is_admin,
        repo_url=REPO_URL,
    )

    trackers_dict = trk_status_all.get("trackers", {})
    simkl_status = trackers_dict.get("simkl", {})
    simkl_auth = simkl_status.get("authenticated", False)
    ani_status = trackers_dict.get("anilist", {})
    mal_status = trackers_dict.get("myanimelist", {})
    ani_auth = ani_status.get("authenticated", False)
    mal_auth = mal_status.get("authenticated", False)

    stats_data = demo_mgr.get_demo_stats() if is_demo else scrobble_stats

    if is_demo:
        trakt_configured = True
        simkl_configured = True
        tmdb_configured = True
        anilist_configured = True
        mal_configured = True
        kitsu_configured = True
        letterboxd_configured = True
        serializd_configured = True
        mdblist_configured = True
    else:
        trakt_configured = bool(auth_status and settings_mgr.is_tracker_enabled("trakt"))
        simkl_configured = bool(simkl_auth and settings_mgr.is_tracker_enabled("simkl"))
        tmdb_configured = bool(tmdb.is_configured() and settings_mgr.is_tracker_enabled("tmdb"))
        anilist_configured = bool(ani_auth and settings_mgr.is_tracker_enabled("anilist"))
        mal_configured = bool(mal_auth and settings_mgr.is_tracker_enabled("mal"))
        kitsu_configured = bool(kitsu.is_configured() and settings_mgr.is_tracker_enabled("kitsu"))
        letterboxd_configured = bool(letterboxd.is_configured() and settings_mgr.is_tracker_enabled("letterboxd"))
        serializd_configured = bool(serializd.is_configured() and settings_mgr.is_tracker_enabled("serializd"))
        mdblist_configured = bool(mdblist.is_configured() and settings_mgr.is_tracker_enabled("mdblist"))

    cowatch_user = Config.CO_WATCH_USER
    cowatch_disp = (cowatch_user if is_admin else mask_username(cowatch_user)) if cowatch_user else ""
    has_cowatch_partner = bool(cowatch_user and (is_demo or user_mgr.is_user_authenticated(cowatch_user)))

    replacements = {
        '{{DEMO_BANNER}}': demo_banner,
        '{{DEMO_HEADER_BTN}}': demo_header_btn,
        '{{SETTINGS_HEADER_BTN}}': settings_header_btn,
        '{{DEMO_FOOTER_LINK}}': demo_footer_link,
        '{{STATUS_BADGE}}': status_badge,
        '{{ADMIN_BTN}}': admin_btn,
        '{{ACTIVE_PLAYBACK_CARD}}': active_playback_card_html,
        '{{ACCOUNT_DISPLAY}}': ('@' + display_username if display_username else 'Connected' if auth_status else 'Not Connected'),
        '{{TOKEN_HEALTH_COLOR}}': token_health_color,
        '{{TOKEN_HEALTH_STR}}': token_health_str,
        '{{ALLOWED_USERS_DISPLAY}}': allowed_users_display,
        '{{ALLOWED_LIBS_DISPLAY}}': allowed_libs_display,
        '{{SYNC_COLLECTION_DISPLAY}}': ('On' if Config.SYNC_COLLECTION else 'Off'),
        '{{UPTIME_STR}}': get_uptime_str(),
        '{{QUEUE_COLOR}}': ('#f59e0b' if pending_queue > 0 else '#94a3b8'),
        '{{PENDING_QUEUE}}': str(pending_queue),
        '{{NOTIF_SUMMARY}}': notif_summary,
        '{{STAT_TOTAL}}': str(stats_data['total']),
        '{{STAT_MOVIES}}': str(stats_data['movies']),
        '{{STAT_EPISODES}}': str(stats_data['episodes']),
        '{{STAT_RATINGS}}': str(stats_data['ratings']),
        '{{STAT_COLLECTIONS}}': str(stats_data.get('collections', 0)),
        '{{WEBHOOK_CARD}}': webhook_html_section,
        '{{ECOSYSTEM_CARD}}': ecosystem_card_html,
        '{{SIMKL_CARD}}': simkl_card_html,
        '{{ANIME_CARD}}': anime_card_html,
        '{{ARR_BRIDGE_CARD}}': arr_bridge_card_html,
        '{{COWATCH_CARD}}': cowatch_card_html,
        '{{ANALYTICS_CARD}}': analytics_card_html,
        '{{RECONCILIATION_CARD}}': reconcile_card_html,
        '{{BACKUP_CARD}}': backup_card_html,
        '{{MANUAL_SCROBBLE_BTN}}': manual_scrobble_btn_html,
        '{{RETRY_QUEUE_BTN}}': (f'<button onclick="retryQueue()" class="btn-sm" style="background:#d97706;color:#fff;font-weight:600;">🔄 Retry Queue ({pending_queue})</button>' if pending_queue > 0 else ''),
        '{{CLEAR_BUTTON}}': clear_button_html,
        '{{ACTIONS_HEADER}}': ('<th style="min-width:220px;white-space:nowrap;">Actions</th>' if is_admin else ''),
        '{{EVENT_ROWS}}': rows,
        '{{EVENTS_PAGE_INFO}}': events_page_info,
        '{{EVENTS_PAGE_NUM}}': events_page_num,
        '{{EVENTS_NEXT_DISABLED}}': events_next_disabled,
        '{{IS_ADMIN_JS}}': ('true' if is_admin else 'false'),
        '{{IS_DEMO_JS}}': ('true' if is_demo else 'false'),
        '{{APP_VERSION}}': APP_VERSION,
        '{{REPO_URL}}': REPO_URL,
        '{{SCROBBLE_CHECKED_TRAKT}}': ('checked' if trakt_configured else ''),
        '{{SCROBBLE_CHECKED_SIMKL}}': ('checked' if simkl_configured else ''),
        '{{SCROBBLE_CHECKED_TMDB}}': ('checked' if tmdb_configured else ''),
        '{{SCROBBLE_CHECKED_ANILIST}}': ('checked' if anilist_configured else ''),
        '{{SCROBBLE_CHECKED_MAL}}': ('checked' if mal_configured else ''),
        '{{SCROBBLE_CHECKED_KITSU}}': ('checked' if kitsu_configured else ''),
        '{{SCROBBLE_CHECKED_LETTERBOXD}}': ('checked' if letterboxd_configured else ''),
        '{{SCROBBLE_CHECKED_SERIALIZD}}': ('checked' if serializd_configured else ''),
        '{{SCROBBLE_CHECKED_MDBLIST}}': ('checked' if mdblist_configured else ''),
        '{{SCROBBLE_CHECKED_TRAKT_JS}}': ('true' if trakt_configured else 'false'),
        '{{SCROBBLE_CHECKED_SIMKL_JS}}': ('true' if simkl_configured else 'false'),
        '{{SCROBBLE_CHECKED_TMDB_JS}}': ('true' if tmdb_configured else 'false'),
        '{{SCROBBLE_CHECKED_ANILIST_JS}}': ('true' if anilist_configured else 'false'),
        '{{SCROBBLE_CHECKED_MAL_JS}}': ('true' if mal_configured else 'false'),
        '{{SCROBBLE_CHECKED_KITSU_JS}}': ('true' if kitsu_configured else 'false'),
        '{{SCROBBLE_CHECKED_LETTERBOXD_JS}}': ('true' if letterboxd_configured else 'false'),
        '{{SCROBBLE_CHECKED_SERIALIZD_JS}}': ('true' if serializd_configured else 'false'),
        '{{SCROBBLE_CHECKED_MDBLIST_JS}}': ('true' if mdblist_configured else 'false'),
        '{{SCROBBLE_BADGE_TRAKT}}': ('' if trakt_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_SIMKL}}': ('' if simkl_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_TMDB}}': ('' if tmdb_configured else ' <span style="font-size:10px;color:#64748b;">(Not Configured)</span>'),
        '{{SCROBBLE_BADGE_ANILIST}}': ('' if anilist_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_MAL}}': ('' if mal_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_KITSU}}': ('' if kitsu_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_LETTERBOXD}}': ('' if letterboxd_configured else ' <span style="font-size:10px;color:#64748b;">(Not Configured)</span>'),
        '{{SCROBBLE_BADGE_SERIALIZD}}': ('' if serializd_configured else ' <span style="font-size:10px;color:#64748b;">(Not Configured)</span>'),
        '{{SCROBBLE_BADGE_MDBLIST}}': ('' if mdblist_configured else ' <span style="font-size:10px;color:#64748b;">(Not Configured)</span>'),
        '{{SCROBBLE_BADGE_COWATCH}}': (f' <span style="font-size:10px;color:#d8b4fe;">(@{cowatch_disp})</span>' if has_cowatch_partner else ' <span style="font-size:10px;color:#64748b;">(No partner linked)</span>'),
    }
    rendered = dashboard_renderer.render_template(DASHBOARD_HTML, replacements)
    response = HTMLResponse(
        content=rendered,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
    if is_admin and not request.cookies.get("csrf_token") and Config.WEBHOOK_SECRET:
        new_csrf = secrets.token_hex(16)
        samesite_policy = getattr(Config, "COOKIE_SAMESITE", "lax") or "lax"
        response.set_cookie(
            key="csrf_token",
            value=new_csrf,
            httponly=False,
            secure=is_https_request(request),
            samesite=samesite_policy,
            path="/",
            max_age=86400 * 30,
        )
    return response


if __name__ == '__main__':
    ssl_kwargs = {}
    if Config.SSL_CERTFILE and Config.SSL_KEYFILE:
        ssl_kwargs["ssl_certfile"] = Config.SSL_CERTFILE
        ssl_kwargs["ssl_keyfile"] = Config.SSL_KEYFILE
    if Config.DEBUG:
        uvicorn.run('app.main:app', host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=True, reload_excludes=['*.json', 'data/*'], **ssl_kwargs)
    else:
        uvicorn.run('app.main:app', host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=False, **ssl_kwargs)
