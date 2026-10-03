import asyncio
import datetime
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
from pydantic import BaseModel
import uvicorn


from app.config import Config
from app.clients.sonarr_client import (
    SonarrClient,
    parse_sonarr_webhook,
    parse_radarr_webhook,
)
from app.clients.trakt_client import TraktClient
from app.metrics import metrics_registry
from app.services.cowatch_manager import cowatch_mgr
from app.services.notifier import notifier
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
from app.clients.mal_client import MyAnimeListClient
from app.clients.radarr_client import RadarrClient
from app.clients.simkl_client import SimklClient
from app.services.anime_resolver import AnimeResolver
from app.services.multi_tracker import MultiTrackerManager
from app.services.loop_prevention import loop_prevention
from app.services.reverse_sync_manager import reverse_sync_mgr
from app.services.arr_bridge import arr_bridge
from app.services.cross_tracker_sync import CrossTrackerSyncManager
from app.services.settings_manager import settings_mgr
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
anime_resolver = AnimeResolver(Config, anilist_client=anilist, mal_client=mal)
multi_tracker = MultiTrackerManager(
    Config,
    simkl_client=simkl,
    anilist_client=anilist,
    mal_client=mal,
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
    stats = {"total": 0, "movies": 0, "episodes": 0, "ratings": 0, "collections": 0}
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k in stats:
                        stats[k] = int(data.get(k, 0))
        except Exception as e:
            logger.warning(f"Could not load stats from {STATS_FILE}: {e}")
    return stats


def save_scrobble_stats() -> None:
    """Persist scrobble counters to data/stats.json."""
    try:
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(scrobble_stats, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save stats to {STATS_FILE}: {e}")


# Persistent scrobble counter across restarts and upgrades
scrobble_stats: dict[str, int] = {"total": 0, "movies": 0, "episodes": 0, "ratings": 0, "collections": 0}
scrobble_stats.update(load_scrobble_stats())


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
        return True

    return False


queue_worker_task: Optional[asyncio.Task] = None
reverse_sync_worker_task: Optional[asyncio.Task] = None


async def queue_worker_loop():
    """Background worker periodically checking and retrying offline queued events."""
    while True:
        try:
            interval = max(5, Config.QUEUE_RETRY_INTERVAL)
            await asyncio.sleep(interval)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global queue_worker_task, reverse_sync_worker_task, arr_watchlist_worker_task
    queue_worker_task = asyncio.create_task(queue_worker_loop())
    reverse_sync_worker_task = asyncio.create_task(reverse_sync_worker_loop())
    if Config.AUTO_ADD_FROM_WATCHLIST and Config.ARR_WATCHLIST_INTERVAL > 0 and (arr_bridge.sonarr.is_configured or arr_bridge.radarr.is_configured):
        arr_watchlist_worker_task = asyncio.create_task(arr_watchlist_worker_loop())

    # Startup self-diagnostics check
    token_info = trakt.get_token_info()
    if not trakt.is_authenticated():
        logger.warning("Startup Diagnostics: Trakt is not authenticated. Visit /auth to link your account.")
    elif not token_info.get("healthy", False):
        logger.warning(f"Startup Diagnostics: Trakt token status is {token_info.get('status')}.")
    else:
        logger.info(f"Startup Diagnostics: Trakt token healthy (~{token_info.get('days_remaining')} days remaining).")

    active_notifiers = [k for k, v in notifier.get_status().items() if v and k in ("discord", "telegram", "ntfy", "pushover")]
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
    await reverse_sync_mgr.plex.close()
    await arr_bridge.sonarr.close()
    await arr_bridge.radarr.close()
    await simkl.close()
    await anilist.close()
    await mal.close()
    await trakt.close()
    await user_mgr.close_all()
    await notifier.close()


APP_VERSION = "2.5.0"
REPO_URL = "https://github.com/selits/omniscrobble"

app = FastAPI(title="Omniscrobble", version=APP_VERSION, lifespan=lifespan)


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
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(recent_events), f, indent=2)
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
        "media_payload": {
            "media_type": media.media_type,
            "title": media.title,
            "year": media.year,
            "season": media.season,
            "episode": media.episode,
            "ids": media.ids,
        },
        "progress": progress_str,
        "result_status": result.get("status") or ("ok" if not result.get("error") else "error"),
        "raw_result": result,
        "cowatch_status": cowatch_status,
    }
    recent_events.appendleft(entry)
    save_recent_events()
    save_scrobble_stats()


async def execute_cowatch_sync(parsed: ParsedMedia, action: str):
    """Dual-scrobble/sync watched history to the partner Trakt account (CO_WATCH_USER)."""
    target_user = Config.CO_WATCH_USER
    if not target_user:
        return
    cw_client = user_mgr.get_client(target_user)
    if not cw_client.is_authenticated():
        logger.warning(f"Co-watch target user '{target_user}' Trakt is not authenticated. Skipping co-watch.")
        return
    try:
        logger.info(f"Co-watching dual-sync triggering for partner @{target_user}: {parsed.title}")
        history_payload = parsed.to_trakt_history_payload()
        res = await cw_client.sync_history(history_payload)
        if is_temporary_error(res):
            queue_mgr.enqueue("sync_history", history_payload, error=str(res.get("error", "")), username=target_user)
            metrics_registry.record_cowatch("queued")
        else:
            logger.info(f"Co-watch dual-sync succeeded for partner @{target_user}: {parsed.title}")
            metrics_registry.record_cowatch("success")
    except Exception as e:
        logger.error(f"Error during co-watch dual-sync for @{target_user}: {e}")
        queue_mgr.enqueue("sync_history", parsed.to_trakt_history_payload(), error=str(e), username=target_user)
        metrics_registry.record_cowatch("failed")


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
    threshold = Config.get_threshold(parsed.media_type)

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
            action_taken = "mark_watched"
            logger.info(f"Marking as watched in Trakt: {parsed.title} for user {parsed.username}")
            playback_mgr.stop_playback(parsed)

            scrobble_payload["progress"] = 100.0
            scrobble_res = await active_client.scrobble_stop(scrobble_payload)
            history_res = await active_client.sync_history(parsed.to_trakt_history_payload())
            result = {"scrobble": scrobble_res, "history": history_res}

            if is_temporary_error(scrobble_res):
                queue_mgr.enqueue("scrobble_stop", scrobble_payload, error=str(scrobble_res.get("error", "")), username=parsed.username)
                metrics_registry.record_scrobble(parsed.media_type, "queued")
            else:
                metrics_registry.record_scrobble(parsed.media_type, "success")

            if is_temporary_error(history_res):
                queue_mgr.enqueue("sync_history", parsed.to_trakt_history_payload(), error=str(history_res.get("error", "")), username=parsed.username)

            scrobble_stats["total"] += 1
            if parsed.media_type == "movie":
                scrobble_stats["movies"] += 1
            elif parsed.media_type == "episode":
                scrobble_stats["episodes"] += 1

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
                    action_taken = "scrobble_stop"
                    logger.info(f"Scrobble stop (paused past threshold {threshold}%): {parsed.title} ({parsed.progress:.1f}%)")
                    result = await active_client.scrobble_stop(scrobble_payload)
                    if is_temporary_error(result):
                        queue_mgr.enqueue("scrobble_stop", scrobble_payload, error=str(result.get("error", "")), username=parsed.username)
                        metrics_registry.record_scrobble(parsed.media_type, "queued")
                    else:
                        metrics_registry.record_scrobble(parsed.media_type, "success")
                    scrobble_stats["total"] += 1
                    if parsed.media_type == "movie":
                        scrobble_stats["movies"] += 1
                    elif parsed.media_type == "episode":
                        scrobble_stats["episodes"] += 1
                else:
                    action_taken = "scrobble_pause"
                    logger.info(f"Scrobble pause: {parsed.title} ({parsed.progress:.1f}%)")
                    result = await active_client.scrobble_pause(scrobble_payload)
            elif event == "media.stop":
                playback_mgr.stop_playback(parsed)
                if parsed.progress >= threshold:
                    action_taken = "scrobble_stop"
                    logger.info(f"Scrobble stop (watched past threshold {threshold}%): {parsed.title} ({parsed.progress:.1f}%)")
                    result = await active_client.scrobble_stop(scrobble_payload)
                    if is_temporary_error(result):
                        queue_mgr.enqueue("scrobble_stop", scrobble_payload, error=str(result.get("error", "")), username=parsed.username)
                        metrics_registry.record_scrobble(parsed.media_type, "queued")
                    else:
                        metrics_registry.record_scrobble(parsed.media_type, "success")
                    scrobble_stats["total"] += 1
                    if parsed.media_type == "movie":
                        scrobble_stats["movies"] += 1
                    elif parsed.media_type == "episode":
                        scrobble_stats["episodes"] += 1
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
        is_sync_trigger = (event == "media.scrobble" or (action_taken == "scrobble_stop" and parsed.progress >= threshold))
        cowatch_info = None

        if is_sync_trigger:
            if eligible:
                cowatch_info = {"synced": True, "reason": reason, "target": Config.CO_WATCH_USER}
                asyncio.create_task(execute_cowatch_sync(parsed, action_taken))
            else:
                cowatch_info = {"synced": False, "reason": reason, "target": Config.CO_WATCH_USER}
        elif eligible:
            cowatch_info = {"synced": False, "in_list": True, "reason": reason, "target": Config.CO_WATCH_USER}

        log_event(parsed, action_taken, result, cowatch_status=cowatch_info)

        if action_taken in ("mark_watched", "scrobble_stop", "rate"):
            partner_target = Config.CO_WATCH_USER if (cowatch_info and cowatch_info.get("synced")) else None
            asyncio.create_task(notifier.dispatch(parsed, action_taken, cowatch_partner=partner_target))

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

    parsed = parse_plex_webhook(
        raw_data,
        allowed_users=Config.PLEX_ALLOWED_USERS,
        allowed_libraries=Config.ALLOWED_LIBRARIES,
        excluded_libraries=Config.EXCLUDED_LIBRARIES,
    )
    if not parsed:
        metrics_registry.record_request("webhook", 200)
        return {"status": "ignored", "reason": "Non-media event, filtered user/library, or unsupported media type"}

    return await process_media_event(parsed, endpoint_name="webhook")


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

    parsed = parse_jellyfin_webhook(
        raw_data,
        allowed_users=Config.PLEX_ALLOWED_USERS,
        allowed_libraries=Config.ALLOWED_LIBRARIES,
        excluded_libraries=Config.EXCLUDED_LIBRARIES,
    )
    if not parsed:
        metrics_registry.record_request("webhook_jellyfin", 200)
        return {"status": "ignored", "reason": "Non-media event, filtered user/library, or unsupported media type"}

    return await process_media_event(parsed, endpoint_name="webhook_jellyfin")


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

    parsed = parse_emby_webhook(
        raw_data,
        allowed_users=Config.PLEX_ALLOWED_USERS,
        allowed_libraries=Config.ALLOWED_LIBRARIES,
        excluded_libraries=Config.EXCLUDED_LIBRARIES,
    )
    if not parsed:
        metrics_registry.record_request("webhook_emby", 200)
        return {"status": "ignored", "reason": "Non-media event, filtered user/library, or unsupported media type"}

    return await process_media_event(parsed, endpoint_name="webhook_emby")


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

    event_type, trakt_payload, parsed = parse_sonarr_webhook(payload)

    if event_type == "test":
        logger.info("Received Sonarr test webhook - connection verified!")
        metrics_registry.record_request("sonarr", 200)
        return {"status": "success", "message": "Sonarr webhook received successfully"}

    if event_type == "ignored" or not trakt_payload or not parsed:
        metrics_registry.record_request("sonarr", 200)
        return {"status": "ignored", "reason": f"Event '{payload.get('eventType')}' ignored"}

    if not Config.SYNC_COLLECTION:
        metrics_registry.record_request("sonarr", 200)
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
        return {"status": "success", "event": "sonarr.download", "action": "collection", "result": result}
    except Exception as e:
        logger.error(f"Error processing Sonarr collection sync: {e}")
        queue_mgr.enqueue("sync_collection", trakt_payload, error=str(e), username="Sonarr")
        metrics_registry.record_collection("episode", "queued")
        log_event(parsed, action_taken, {"error": str(e), "queued": True})
        metrics_registry.record_request("sonarr", 500)
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

    event_type, trakt_payload, parsed = parse_radarr_webhook(payload)

    if event_type == "test":
        logger.info("Received Radarr test webhook - connection verified!")
        metrics_registry.record_request("radarr", 200)
        return {"status": "success", "message": "Radarr webhook received successfully"}

    if event_type == "ignored" or not trakt_payload or not parsed:
        metrics_registry.record_request("radarr", 200)
        return {"status": "ignored", "reason": f"Event '{payload.get('eventType')}' ignored"}

    if not Config.SYNC_COLLECTION:
        metrics_registry.record_request("radarr", 200)
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
        return {"status": "success", "event": "radarr.download", "action": "collection", "result": result}
    except Exception as e:
        logger.error(f"Error processing Radarr collection sync: {e}")
        queue_mgr.enqueue("sync_collection", trakt_payload, error=str(e), username="Radarr")
        metrics_registry.record_collection("movie", "queued")
        log_event(parsed, action_taken, {"error": str(e), "queued": True})
        metrics_registry.record_request("radarr", 500)
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
async def export_backup(request: Request):
    """Download a zip archive containing server configuration, tokens, and databases."""
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

    buffer.seek(0)
    filename = f"plex-trakt-backup-{datetime.date.today().isoformat()}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/restore")
async def import_backup(request: Request):
    """Restore server configuration and tokens from an uploaded zip backup."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    form = await request.form()
    file = form.get("backup_file")
    if not file or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="Missing backup_file in form")

    contents = await file.read()
    if isinstance(contents, str):
        contents = contents.encode("utf-8")

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
    notifications: Optional[dict[str, Any]] = None

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

    model_config = {"extra": "ignore"}


@app.get("/api/settings")
def get_settings_endpoint(request: Request):
    """Retrieve current runtime enablement settings for servers and trackers."""
    all_s = settings_mgr.get_all_settings()
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
        )

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
    )
    if not success:
        return JSONResponse(status_code=400, content={"status": "error", "success": False, "message": msg})
    return {"status": "success", "success": True, "message": msg}


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

        scrobble_stats["total"] += 1
        if m_type == "movie":
            scrobble_stats["movies"] += 1
        elif m_type == "episode":
            scrobble_stats["episodes"] += 1

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
async def get_sync_diff(request: Request, force: bool = False, server: Optional[str] = None):
    """Scan and return discrepancies between media server and Trakt."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return {"status": "ok", "diff": demo_mgr.get_demo_reconciliation(), "count": len(demo_mgr.get_demo_reconciliation())}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    diff = await reverse_sync_mgr.scan_discrepancies(force=force, server=server)
    return {"status": "ok", "diff": diff, "count": len(diff)}


@app.post("/api/sync/reconcile")
async def trigger_reconciliation(payload: ReconcileRequest, request: Request):
    """Execute two-way reconciliation for discrepancies."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return await reverse_sync_mgr.execute_reconciliation(demo=True, server=payload.server)
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
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


class ArrTestConnectionRequest(BaseModel):
    app: Optional[str] = "sonarr"
    url: Optional[str] = None
    api_key: Optional[str] = None

    model_config = {"extra": "ignore"}


@app.post("/api/arr/test-connection")
async def test_arr_connection(payload: ArrTestConnectionRequest, request: Request):
    """Test connectivity to Sonarr or Radarr with provided or active credentials."""
    is_demo = request.query_params.get("demo") == "true"
    app_type = (payload.app or "sonarr").lower().strip()
    if is_demo:
        return {"status": "connected", "app": app_type, "version": "4.0.9" if app_type == "sonarr" else "5.9.1"}
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
            "type": payload.media_type,
            "title": payload.title,
            "year": payload.year,
            "duration": 3600000,
            "viewOffset": int(3600000 * (payload.progress / 100.0)),
            "grandparentTitle": payload.show_title if payload.media_type == "episode" else None,
            "parentIndex": payload.season if payload.media_type == "episode" else None,
            "index": payload.episode if payload.media_type == "episode" else None,
            "Guid": [{"id": "imdb://tt0000001"}],
        }
    }

    parsed = parse_plex_webhook(
        mock_payload,
        allowed_users=Config.PLEX_ALLOWED_USERS,
        allowed_libraries=Config.ALLOWED_LIBRARIES,
        excluded_libraries=Config.EXCLUDED_LIBRARIES,
    )
    if not parsed:
        return {"status": "ignored", "reason": "Filtered or invalid media payload"}

    eligible, reason = cowatch_mgr.check_cowatch_eligibility(parsed)
    simulated_result: dict[str, Any] = {"status": "ok", "mode": "simulated"}

    if payload.execute_trakt:
        if trakt.is_authenticated():
            simulated_result = await trakt.sync_history(parsed.to_trakt_history_payload())
            if eligible and Config.CO_WATCH_USER:
                await execute_cowatch_sync(parsed, "test_webhook")
        else:
            simulated_result = {"status": "warning", "message": "Trakt not authenticated"}

    action_name = "test_webhook"
    cw_info = {"synced": eligible and payload.execute_trakt, "reason": reason, "target": Config.CO_WATCH_USER}
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
            "eligible": eligible,
            "reason": reason,
            "partner": Config.CO_WATCH_USER,
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
    response.set_cookie(
        key="admin_token",
        value=Config.WEBHOOK_SECRET,
        httponly=True,
        secure=is_https_request(request),
        samesite="lax",
        path="/",
        max_age=86400 * 30,
    )
    return {"status": "ok", "message": "Admin mode unlocked"}


@app.post("/api/admin/lock")
def admin_lock(response: Response):
    response.delete_cookie(key="admin_token")
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
    status = await simkl.check_connection()
    status["enabled"] = simkl.is_enabled()
    status["configured"] = bool(simkl.effective_client_id)
    return status


@app.post("/api/simkl/pin")
async def get_simkl_pin(request: Request):
    """Obtain a new Device PIN / user_code to authorize Simkl via browser."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    try:
        data = await simkl.get_device_pin()
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
    try:
        res = await simkl.poll_device_pin(payload.user_code, device_code=payload.device_code)
        return res
    except Exception as e:
        return {"result": "error", "message": str(e)}


@app.post("/api/simkl/disconnect")
async def disconnect_simkl(request: Request):
    """Disconnect Simkl account and delete saved tokens."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
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


# --- AniList Anime Tracker Endpoints ---
@app.get("/api/anilist/status")
async def get_anilist_status(request: Request):
    """Return current connection and authentication status for AniList."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return demo_mgr.get_demo_anilist_status()
    status = await anilist.check_connection()
    status["enabled"] = anilist.is_enabled()
    status["configured"] = bool(getattr(Config, "ANILIST_CLIENT_ID", "") or anilist.is_authenticated())
    return status


@app.post("/api/anilist/token")
async def save_anilist_token(payload: TokenSubmitRequest, request: Request):
    """Save an AniList personal access token and test connectivity."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    token_str = payload.token.strip()
    if not token_str:
        raise HTTPException(status_code=400, detail="Token cannot be empty")
    anilist.access_token = token_str
    conn = await anilist.check_connection()
    if conn.get("status") == "connected":
        anilist.save_tokens({
            "access_token": token_str,
            "user_name": conn.get("user"),
            "user_avatar": conn.get("avatar"),
            "user_id": conn.get("id"),
        })
        return {"status": "success", "user": conn.get("user"), "id": conn.get("id")}
    else:
        anilist.load_tokens()
        raise HTTPException(status_code=400, detail=conn.get("error", "Failed to connect with provided AniList token"))


@app.post("/api/anilist/disconnect")
async def disconnect_anilist(request: Request):
    """Disconnect AniList account and delete stored tokens."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
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
    status = await mal.check_connection()
    status["enabled"] = mal.is_enabled()
    status["configured"] = bool(mal.effective_client_id or mal.is_authenticated())
    return status


@app.post("/api/mal/token")
async def save_mal_token(payload: TokenSubmitRequest, request: Request):
    """Save a MyAnimeList access token and test connectivity."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    token_str = payload.token.strip()
    if not token_str:
        raise HTTPException(status_code=400, detail="Token cannot be empty")
    mal.access_token = token_str
    conn = await mal.check_connection()
    if conn.get("status") == "connected":
        mal.save_tokens({
            "access_token": token_str,
            "user_name": conn.get("user"),
            "user_avatar": conn.get("avatar"),
            "user_id": conn.get("id"),
        })
        return {"status": "success", "user": conn.get("user"), "id": conn.get("id")}
    else:
        mal.load_tokens()
        raise HTTPException(status_code=400, detail=conn.get("error", "Failed to connect with provided MyAnimeList token"))


@app.post("/api/mal/disconnect")
async def disconnect_mal(request: Request):
    """Disconnect MyAnimeList account and delete stored tokens."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
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


def format_action_label(raw_action: str) -> str:
    clean = str(raw_action or "").strip()
    action_map = {
        "scrobble_start": "play",
        "scrobble_pause": "pause",
        "scrobble_stop": "scrobble",
        "mark_watched": "scrobble",
        "playback_stopped": "stop",
        "test_webhook": "test",
        "none": "ignored",
    }
    return action_map.get(clean, clean)


def should_display_cowatch_badge(action: str, result_status: str, progress: str) -> bool:
    act = str(action or "").lower().strip()
    status = str(result_status or "").lower().strip()
    prog = str(progress or "").strip()
    if status in ("ignored", "error") or prog == "0.0%":
        return False
    return act.startswith(("mark_watched", "scrobble_stop", "test_webhook")) or act in ("scrobble", "watched")


def render_status_badge(action: str, result_status: str, progress: str = "", cowatch_status: dict | None = None) -> str:
    raw_act = str(action or "").lower().strip()
    clean_act = format_action_label(raw_act).lower()
    stat = str(result_status or "").lower().strip()
    cw = cowatch_status or {}

    if cw.get("synced") and should_display_cowatch_badge(action, result_status, progress):
        target_txt = html.escape(f"@{cw['target']}" if cw.get("target") else "partner")
        reason_txt = html.escape(cw.get("reason") or "Shared show whitelist match")
        return f'<span style="background:#701a75;color:#f5d0fe;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;" title="Synced to {target_txt}: {reason_txt}">👥 Co-Watched</span>'

    if stat in ("ok", "200", "201"):
        label = "✓ OK"
        tooltip = "Action successful"
        if clean_act == "scrobble" or raw_act.startswith(("mark_watched", "scrobble_stop")):
            label = "✓ Scrobbled"
            if cw.get("reason"):
                tooltip = f"Scrobbled (Solo: {html.escape(cw.get('reason'))})"
            else:
                tooltip = "Scrobbled to connected trackers"
        elif clean_act == "collection" or raw_act == "collection":
            label = "✓ Added"
            tooltip = "Added to collection"
        elif clean_act == "rate" or raw_act.startswith("rate"):
            label = "✓ Rated"
            tooltip = "Rating synchronized"
        return f'<span style="background:#064e3b;color:#a7f3d0;border:1px solid #059669;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;" title="{tooltip}">{label}</span>'

    if stat == "ignored":
        return '<span style="background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;white-space:nowrap;" title="Playback or event skipped">Ignored</span>'

    if stat == "queued":
        return '<span style="background:#78350f;color:#fde68a;border:1px solid #d97706;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;" title="Saved to offline retry queue">⏳ Queued</span>'

    if stat in ("error", "500", "502", "503", "504"):
        return '<span style="background:#7f1d1d;color:#fecaca;border:1px solid #ef4444;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;" title="Action failed">✕ Failed</span>'

    return f'<span style="background:#1e293b;border:1px solid #334155;color:#cbd5e1;padding:2px 8px;border-radius:4px;font-size:11px;white-space:nowrap;">{html.escape(str(result_status))}</span>'


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
        base_url = str(request.base_url).rstrip("/")
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
    total_events = len(events_list)
    initial_page_size = 10
    total_pages = max(1, (total_events + initial_page_size - 1) // initial_page_size) if total_events > 0 else 1
    events_page_info = f"Showing 1–{min(initial_page_size, total_events)} of {total_events} events" if total_events > 0 else "0 events"
    events_page_num = f"Page 1 of {total_pages}"
    events_next_disabled = "" if total_pages > 1 else "disabled"

    ssr_events = events_list[:initial_page_size]
    rows = ""
    col_span = 7 if is_admin else 6
    if not ssr_events:
        rows = f'<tr><td colspan="{col_span}" style="text-align:center;padding:24px;color:#94a3b8;">No scrobble events received yet. Start playing media on Plex, Jellyfin, or Emby to test!</td></tr>'
    else:
        for ev in ssr_events:
            color = "#10b981" if ev["result_status"] in ("ok", 200, 201) else "#f59e0b"
            u = ev["user"] if is_admin else mask_username(ev["user"])
            server_raw = ev.get("server", "plex").lower()
            if server_raw == "jellyfin":
                server_badge = '<span style="background:#3b0764;color:#d8b4fe;border:1px solid #7e22ce;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Jellyfin</span>'
            elif server_raw == "emby":
                server_badge = '<span style="background:#064e3b;color:#a7f3d0;border:1px solid #059669;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Emby</span>'
            else:
                server_badge = '<span style="background:#1e293b;color:#94a3b8;border:1px solid #334155;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Plex</span>'

            action_col = ""
            if is_admin:
                show_title = ev.get("show_title") or (ev.get("title") if ev.get("type") == "show" else None)
                if not show_title and ev.get("media_payload") and ev.get("media_payload", {}).get("media_type") == "show":
                    show_title = ev["media_payload"].get("title")
                action_buttons = []
                if show_title:
                    show_esc = urllib.parse.quote(show_title)
                    if not cowatch_mgr.is_cowatch_show(show_title):
                        action_buttons.append(f'<button data-show="{show_esc}" onclick="quickAddShow(decodeURIComponent(this.dataset.show), this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#1e293b;border:1px solid #334155;white-space:nowrap;" title="Add show to co-watch whitelist">+ Co-Watch</button>')
                if (Config.CO_WATCH_USER or is_demo) and ev.get("media_payload"):
                    media_enc = urllib.parse.quote(json.dumps(ev["media_payload"]))
                    raw_act = str(ev.get("action", "")).lower().strip()
                    res_stat = str(ev.get("result_status", "")).lower().strip()
                    prog_val = str(ev.get("progress", "")).strip()
                    is_completion = raw_act.startswith(("mark_watched", "scrobble_stop", "collection", "rate")) or raw_act in ("scrobble", "watched")
                    if is_completion and res_stat != "ignored" and prog_val != "0.0%":
                        cw = ev.get("cowatch_status") or {}
                        if not cw.get("synced"):
                            action_buttons.append(f'<button onclick="quickSyncPartner(\'{media_enc}\', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#701a75;color:#f5d0fe;white-space:nowrap;" title="Manually push this watch event to partner account">+ Sync Partner</button>')
                        if raw_act.startswith(("mark_watched", "scrobble_stop")) or raw_act in ("scrobble", "watched"):
                            action_buttons.append(f'<button onclick="quickUnscrobble(\'{media_enc}\', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#7f1d1d;color:#fee2e2;border:1px solid #ef4444;white-space:nowrap;" title="Unscrobble / Remove from connected trackers">🗑️ Unscrobble</button>')
                action_col = f'<td style="padding:10px 12px;white-space:nowrap;"><div style="display:inline-flex;flex-wrap:nowrap;gap:6px;align-items:center;">{"".join(action_buttons)}</div></td>'

            # Status column: show unified status badge
            status_badge_html = render_status_badge(
                ev.get("action"),
                ev.get("result_status"),
                ev.get("progress", ""),
                ev.get("cowatch_status"),
            )

            title_disp = html.escape(str(ev.get('title', '')))
            type_disp = html.escape(str(ev.get('type', '')))
            user_disp = html.escape(str(u))
            action_raw = str(ev.get('action', ''))
            progress_raw = str(ev.get('progress', '')).strip()
            clean_action = format_action_label(action_raw)
            action_disp = html.escape(clean_action)
            progress_disp = html.escape(progress_raw)
            if progress_disp and progress_raw not in clean_action and "(" not in clean_action and clean_action.lower() not in ("collection", "ignored"):
                action_text = f"{action_disp} ({progress_disp})"
            else:
                action_text = action_disp
            time_disp = html.escape(str(ev.get('timestamp', '')))

            rows += f"""
            <tr style="border-bottom: 1px solid #334155;">
                <td style="padding:10px 12px;color:#cbd5e1;font-size:13px;">{time_disp}</td>
                <td style="padding:10px 12px;color:#f8fafc;font-weight:500;">{title_disp}</td>
                <td style="padding:10px 12px;"><span style="background:#0f172a;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">{type_disp}</span></td>
                <td style="padding:10px 12px;color:#cbd5e1;font-size:13px;"><div style="display:inline-flex;align-items:center;">{server_badge}<span>{user_disp}</span></div></td>
                <td style="padding:10px 12px;"><span style="background:#0f172a;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;white-space:nowrap;">{action_text}</span></td>
                <td style="padding:10px 12px;white-space:nowrap;"><div style="display:inline-flex;align-items:center;gap:6px;white-space:nowrap;">{status_badge_html}</div></td>
                {action_col}
            </tr>
            """

    webhook_html_section = f"""
    <div style="margin-top: 18px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:8px;">
            <div class="info-label">Universal Webhook URLs</div>
            <div style="display:flex;gap:6px;">
                <button type="button" onclick="switchWebhookTab('plex')" id="btn-tab-plex" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">Plex</button>
                <button type="button" onclick="switchWebhookTab('jellyfin')" id="btn-tab-jellyfin" class="btn-sm" style="background:#1e293b;color:#94a3b8;">Jellyfin</button>
                <button type="button" onclick="switchWebhookTab('emby')" id="btn-tab-emby" class="btn-sm" style="background:#1e293b;color:#94a3b8;">Emby</button>
            </div>
        </div>
        <div class="webhook-row">
            <input type="text" readonly id="webhook-url-input" value="{full_webhook_url}"
                   data-plex="{full_webhook_url}" data-jellyfin="{full_jellyfin_url}" data-emby="{full_emby_url}"
                   style="flex:1;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;color:#38bdf8;font-family:monospace;font-size:13px;outline:none;" />
            <button onclick="copyWebhookUrl()" id="copy-btn" class="btn-copy">
                📋 Copy URL
            </button>
        </div>
        <div id="webhook-instructions" style="font-size: 12px; color: #94a3b8; margin-top: 6px;">
            Add in Plex: <strong>Settings &rarr; Webhooks &rarr; Add Webhook</strong> &bull; Jellyfin (<code>/webhook/jellyfin</code>) &bull; Emby (<code>/webhook/emby</code>) &bull; Sonarr (<code>/sonarr</code>) &bull; Radarr (<code>/radarr</code>).
        </div>
    </div>
    """ if is_admin else f"""
    <div style="margin-top: 18px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <div class="info-label">Universal Webhook URLs (Plex • Jellyfin • Emby)</div>
            <span style="color:#f59e0b;font-size:11px;font-weight:600;">🔒 Secret Masked</span>
        </div>
        <div class="webhook-row">
            <input type="text" readonly value="{masked_webhook_url}"
                   style="flex:1;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;color:#64748b;font-family:monospace;font-size:13px;outline:none;user-select:none;" />
            <button onclick="openUnlockModal()" class="btn-copy" style="background:#2563eb;">
                🔓 Unlock
            </button>
        </div>
        <div style="font-size: 12px; color: #94a3b8; margin-top: 6px;">Admin authorization required to reveal webhook URLs. Supports Plex, Jellyfin, Emby, Sonarr, and Radarr.</div>
    </div>
    """

    clear_button_html = '<button onclick="clearHistory()" class="btn-sm" style="color:#f87171;">Clear</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="color:#64748b;" title="Admin unlock required to clear logs">🔒 Clear</button>'
    manual_scrobble_btn_html = '<button onclick="openManualScrobbleModal()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">🔍 Manual Scrobble</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;">🔍 Manual Scrobble</button>'

    if is_demo:
        active_sessions = [demo_mgr.get_demo_playback()]
        recently_finished = None
    else:
        active_sessions = playback_mgr.get_active_sessions(is_admin=is_admin)
        recently_finished = playback_mgr.get_recently_finished(is_admin=is_admin)

    if active_sessions:
        s = active_sessions[0]
        card_display = "block"
        card_border = "#10b981" if s["state"] == "playing" else "#f59e0b"
        badge_text = "Currently Streaming" if s["state"] == "playing" else "Paused"
        badge_color = card_border
        user_dev = f"• {s['username']}" + (f" on {s['player']}" if (is_admin and s['player']) else "") + (f" ({s['device']})" if (is_admin and s['device']) else "")
        stream_title = s['title']
        stream_url = s['trakt_url']
        stream_prog_text = f"{s['progress']:.1f}%"
        if s.get("remaining_str"):
            stream_prog_text += f" • {s['remaining_str']}"
        stream_prog_width = f"{s['progress']}%"
    elif recently_finished:
        f = recently_finished
        card_display = "block"
        card_border = "#38bdf8"
        badge_text = "Recently Finished"
        badge_color = "#38bdf8"
        user_dev = f"• {f['username']}" + (f" on {f['player']}" if (is_admin and f['player']) else "")
        stream_title = f['title']
        stream_url = f['trakt_url']
        stream_prog_text = "100.0% • Finished"
        stream_prog_width = "100%"
    else:
        card_display = "none"
        card_border = "#10b981"
        badge_text = "Currently Streaming"
        badge_color = "#10b981"
        user_dev = ""
        stream_title = ""
        stream_url = "https://trakt.tv"
        stream_prog_text = "0.0%"
        stream_prog_width = "0%"

    stream_title_esc = html.escape(str(stream_title))
    user_dev_esc = html.escape(str(user_dev))
    stream_prog_text_esc = html.escape(str(stream_prog_text))
    clean_stream_url = stream_url if str(stream_url).startswith(("https://", "http://")) else "https://trakt.tv"
    clean_stream_url_esc = html.escape(clean_stream_url)

    active_playback_card_html = f"""
        <div id="active-playback-card" class="card" style="border-left: 4px solid {card_border}; margin-bottom: 24px; display: {card_display};">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px;">
                <div>
                    <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                        <span id="stream-pulse-indicator" class="pulse-indicator" style="background:{badge_color};"></span>
                        <span style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; color:{badge_color};" id="stream-state-badge">{badge_text}</span>
                        <span style="font-size:12px; color:#94a3b8;" id="stream-user-device">{user_dev_esc}</span>
                    </div>
                    <h2 style="margin:4px 0 8px 0; font-size:18px; color:#f8fafc;" id="stream-title">{stream_title_esc}</h2>
                </div>
                <div id="stream-actions">
                    <a id="stream-trakt-link" href="{clean_stream_url_esc}" target="_blank" rel="noopener noreferrer" class="btn-sm" style="background:#334155; color:#38bdf8; text-decoration:none; display:inline-flex; align-items:center; gap:4px;">View on Trakt ↗</a>
                </div>
            </div>
            <div style="margin-top:12px;">
                <div style="display:flex; justify-content:space-between; font-size:12px; color:#94a3b8; margin-bottom:6px;">
                    <span>Playback Progress</span>
                    <span id="stream-progress-text" style="font-weight:600; color:#f8fafc;">{stream_prog_text_esc}</span>
                </div>
                <div style="background:#0f172a; border-radius:9999px; height:8px; overflow:hidden; border:1px solid #334155;">
                    <div id="stream-progress-bar" style="background:{badge_color}; height:100%; width:{stream_prog_width}; border-radius:9999px; transition: width 0.4s ease;"></div>
                </div>
            </div>
        </div>
    """

    # Co-Watching & Multi-User configuration
    if is_demo:
        cw_user = "demo_partner"
        cw_user_display = "demo_partner"
        cw_shows = demo_mgr.get_demo_cowatch_shows()
        cw_devices = demo_mgr.get_demo_cowatch_devices()
        configured_users = demo_mgr.get_demo_users()
    else:
        cw_user = Config.CO_WATCH_USER
        cw_user_display = cw_user if is_admin else "●●●●●●●●"
        cw_shows = sorted(cowatch_mgr.get_shows(), key=lambda x: x.lower())
        cw_devices = cowatch_mgr.get_devices()
        configured_users = user_mgr.list_configured_users()

    # Shared show chips
    if not is_admin:
        count = len(cw_shows)
        chips_html = f'<div style="color:#94a3b8;font-size:13px;display:flex;align-items:center;gap:8px;padding:4px 2px;"><span>🔒</span><span><strong>{count} shared show{"s" if count != 1 else ""} configured</strong> &bull; Unlock admin access to view titles and manage whitelist.</span></div>'
    else:
        chips_html = ""
        for s in cw_shows:
            s_enc = urllib.parse.quote(s)
            del_btn = f'<button data-show="{s_enc}" onclick="removeCowatchShow(decodeURIComponent(this.dataset.show))" title="Remove {html.escape(s)}" class="cowatch-chip-del">&times;</button>'
            chips_html += f'<span class="cowatch-chip" data-title="{html.escape(s.lower())}" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:3px 9px;border-radius:9999px;font-size:12px;display:inline-flex;align-items:center;margin:2px 3px;">{html.escape(s)}{del_btn}</span>'
        if not chips_html:
            chips_html = '<span style="color:#64748b;font-size:12px;font-style:italic;">No shows added yet. Add shows below or directly from recent activity.</span>'

    # Allowed devices chips
    if not is_admin:
        device_chips_html = '<div style="color:#94a3b8;font-size:13px;display:flex;align-items:center;gap:8px;padding:4px 2px;"><span>🔒</span><span>Unlock admin access to manage allowed devices.</span></div>'
        devices_count_badge = "🔒"
    else:
        devices_count_badge = str(len(cw_devices)) if cw_devices else "All"
        if not cw_devices:
            device_chips_html = '<span style="color:#64748b;font-size:12px;font-style:italic;">All devices allowed (no device filtering). Playback on any player triggers co-watch.</span>'
        else:
            device_chips_html = ""
            for d in cw_devices:
                d_enc = urllib.parse.quote(d)
                del_btn = f'<button data-device="{d_enc}" onclick="removeCowatchDevice(decodeURIComponent(this.dataset.device))" title="Remove {html.escape(d)}" class="cowatch-chip-del">&times;</button>'
                device_chips_html += f'<span class="cowatch-device-chip" data-title="{html.escape(d.lower())}" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:3px 9px;border-radius:9999px;font-size:12px;display:inline-flex;align-items:center;margin:2px 3px;">📺 {html.escape(d)}{del_btn}</span>'

    device_form_html = f'''
    <form onsubmit="event.preventDefault();addCowatchDevice();" autocomplete="off" style="margin:0;">
        <div class="cowatch-form-row">
            <input type="text" id="cowatch-device-input" name="cowatch_device" placeholder="Add device (e.g. Apple TV, Shield TV)..."
                   style="flex:1;min-width:0;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 12px;color:#f8fafc;font-size:13px;outline:none;"
                   autocomplete="off" />
            <button type="submit" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:8px 14px;white-space:nowrap;flex-shrink:0;">+ Add Device</button>
        </div>
    </form>
    <div style="margin-top:4px;font-size:11px;color:#64748b;">
        Leave empty to allow all devices. When configured, co-watching only dual-scrobbles on these players.
    </div>
    ''' if is_admin else '<div style="font-size:12px;color:#64748b;">Admin access required to configure allowed devices.</div>'

    # Multi-user accounts list
    users_badges_html = ""
    for u in configured_users:
        u_name = u["username"]
        is_def = u.get("is_default", False)
        is_cw = u.get("is_cowatch_target", False)
        auth = u.get("authenticated", False)
        status_color = "#10b981" if auth else "#ef4444"
        status_text = "Connected" if auth else "Not Linked"
        link_url = f"/auth?user={u_name}" if not is_def else "/auth"

        link_btn = ""
        if is_admin:
            if not auth:
                link_btn = f'<a href="{link_url}" class="btn-sm" style="background:#2563eb;color:#fff;text-decoration:none;padding:2px 8px;font-size:11px;">Link &rarr;</a>'
            else:
                link_btn = f'<a href="{link_url}" class="btn-sm" style="background:#334155;color:#94a3b8;text-decoration:none;padding:2px 8px;font-size:11px;">Reconnect</a>'

        role_label = ""
        if is_def:
            role_label = '<span style="background:#1e3a8a;color:#93c5fd;font-size:10px;padding:2px 6px;border-radius:4px;flex-shrink:0;">Default</span>'
        elif is_cw:
            role_label = '<span style="background:#701a75;color:#f5d0fe;font-size:10px;padding:2px 6px;border-radius:4px;flex-shrink:0;">Partner</span>'

        if is_def and raw_username:
            display_name = raw_username if is_admin else mask_username(raw_username)
        elif is_cw and not is_admin:
            display_name = "●●●●●●●●"
        else:
            display_name = u_name if is_admin else mask_username(u_name)

        users_badges_html += f"""
        <div class="cowatch-account-row">
            <div class="cowatch-account-info">
                <span class="cowatch-account-name">@{display_name}</span>
                {role_label}
            </div>
            <div class="cowatch-account-status">
                <span style="color:{status_color};font-size:12px;font-weight:500;">● {status_text}</span>
                {link_btn}
            </div>
        </div>
        """

    rule_movies_str = "Enabled" if Config.CO_WATCH_MOVIES else "Disabled"
    sonarr_status_note = (
        '<span style="color:#10b981;font-size:11px;font-weight:500;display:inline-flex;align-items:center;gap:4px;">'
        '✓ Connected to Sonarr (type to search library)</span>'
        if sonarr.is_configured
        else '<span style="color:#64748b;font-size:11px;">Configure SONARR_URL & SONARR_API_KEY in .env for library search</span>'
    )

    cowatch_card_html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>👥</span> Watch Together & Multi-User Accounts
            </h3>
            <span style="background:#0f172a;border:1px solid #334155;color:#38bdf8;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;">
                {f"Partner: @{cw_user_display}" if cw_user else "Single-User Mode"}
            </span>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
            Dual-scrobble watched shows to your partner's Trakt account automatically, without syncing your solo shows.
        </p>
        <!-- Top Section: Targeting & Destinations (Accounts & Devices side-by-side) -->
        <div class="cowatch-grid">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <div style="font-size:13px;font-weight:600;color:#f1f5f9;">Linked Trakt Accounts</div>
                    {f'<button onclick="promptLinkAccount()" class="btn-sm" style="background:#334155;color:#38bdf8;">+ Link Account</button>' if is_admin else ''}
                </div>
                <div>
                    {users_badges_html}
                </div>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">
                    <div style="font-size:13px;font-weight:600;color:#f1f5f9;display:flex;align-items:center;gap:6px;">
                        <span>Allowed Devices Whitelist</span>
                        <span id="cowatch-devices-count-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 6px;border-radius:9999px;font-size:11px;font-weight:700;">{devices_count_badge}</span>
                    </div>
                </div>
                <div id="cowatch-devices-chips-container" class="custom-scroll" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 10px;min-height:44px;max-height:140px;overflow-y:auto;margin-bottom:10px;display:flex;flex-wrap:wrap;align-content:flex-start;align-items:center;">
                    {device_chips_html}
                </div>
                {device_form_html}
            </div>
        </div>

        <!-- Bottom Section: Shared Media & Shows Whitelist (Full Width) -->
        <div style="margin-top:20px;border-top:1px solid #334155;padding-top:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap;">
                <div style="font-size:13px;font-weight:600;color:#f1f5f9;display:flex;align-items:center;gap:6px;">
                    <span>Shared Shows Whitelist</span>
                    <span id="cowatch-count-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 6px;border-radius:9999px;font-size:11px;font-weight:700;">{len(cw_shows)}</span>
                </div>
                {f'<input type="text" id="cowatch-filter-input" placeholder="Filter list..." oninput="filterCowatchChips(this.value)" style="background:#0f172a;border:1px solid #334155;border-radius:4px;padding:3px 8px;color:#f8fafc;font-size:11px;outline:none;width:130px;" />' if is_admin else ''}
            </div>
            <div id="cowatch-chips-container" class="custom-scroll" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;min-height:54px;max-height:220px;overflow-y:auto;margin-bottom:10px;display:flex;flex-wrap:wrap;align-content:flex-start;align-items:center;">
                {chips_html}
            </div>
            {f'''
            <form onsubmit="event.preventDefault();addCowatchShow();" autocomplete="off" style="margin:0;">
                <div class="cowatch-form-row">
                    <div style="flex:1;min-width:0;position:relative;">
                        <input type="search" id="cowatch-show-input" name="cowatch_show_search" placeholder="Add show (e.g. Severance, Lanterns)..."
                               style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 12px;color:#f8fafc;font-size:13px;outline:none;"
                               oninput="onCowatchShowInput(this.value)"
                               onfocus="onCowatchShowInput(this.value)"
                               autocomplete="off"
                               data-lpignore="true"
                               data-1p-ignore="true"
                               onkeydown="if(event.key==='Enter')addCowatchShow()" />
                        <div id="sonarr-suggestions" style="display:none;position:absolute;top:100%;left:0;right:0;background:#1e293b;border:1px solid #3b82f6;border-radius:6px;margin-top:4px;max-height:220px;overflow-y:auto;z-index:100;box-shadow:0 10px 15px -3px rgba(0,0,0,0.7);"></div>
                    </div>
                    <button type="submit" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:8px 14px;white-space:nowrap;flex-shrink:0;">+ Add Show</button>
                </div>
            </form>
            <div style="margin-top:4px;">{sonarr_status_note}</div>
            ''' if is_admin else '<div style="font-size:12px;color:#64748b;">Admin access required to add or remove shared shows.</div>'}
            <div style="margin-top:10px;font-size:12px;color:#94a3b8;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span>Movies: <strong id="cowatch-movies-status">{rule_movies_str}</strong></span>
                {f'<button id="cowatch-movies-btn" onclick="toggleCowatchMovies()" class="btn-sm" style="padding:2px 8px;font-size:11px;background:#334155;border:1px solid #475569;">Toggle Movies ({ "Disable" if Config.CO_WATCH_MOVIES else "Enable" })</button>' if is_admin else ''}
            </div>
        </div>
    </div>
    """

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

    active_srv = sync_status.get("active_server", "plex")
    plex_cfg = sync_status.get("plex_configured", False)
    plex_conn = sync_status.get("plex_connected", False)
    jf_cfg = sync_status.get("jellyfin_configured", False)
    jf_conn = sync_status.get("jellyfin_connected", False)
    emby_cfg = sync_status.get("emby_configured", False)
    emby_conn = sync_status.get("emby_connected", False)

    server_status_badges = []
    if plex_cfg:
        col = "#10b981" if plex_conn else "#f59e0b"
        st = "Online" if plex_conn else "Unreachable"
        server_status_badges.append(f'<span style="color:{col};font-size:12px;font-weight:600;">● Plex {st}</span>')
    if jf_cfg:
        col = "#10b981" if jf_conn else "#f59e0b"
        st = "Online" if jf_conn else "Unreachable"
        server_status_badges.append(f'<span style="color:{col};font-size:12px;font-weight:600;">● Jellyfin {st}</span>')
    if emby_cfg:
        col = "#10b981" if emby_conn else "#f59e0b"
        st = "Online" if emby_conn else "Unreachable"
        server_status_badges.append(f'<span style="color:{col};font-size:12px;font-weight:600;">● Emby {st}</span>')

    if not server_status_badges:
        server_status_badges.append('<span style="color:#94a3b8;font-size:12px;">● Direct API Not Configured</span>')
    server_badges_html = " ".join(server_status_badges)

    diff_count = sync_status.get("diff_count", 0)
    int_mins = sync_status.get("interval_minutes", 0)
    auto_sync_badge = f'<span style="background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">Periodic: Every {int_mins}m</span>' if int_mins > 0 else '<span style="background:#0f172a;border:1px solid #334155;color:#64748b;padding:2px 8px;border-radius:4px;font-size:11px;">Periodic: Manual</span>'

    any_server_configured = plex_cfg or jf_cfg or emby_cfg
    if any_server_configured:
        reconcile_card_html = f"""
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                    <span>🔄</span> Two-Way Library Reconciliation & Reverse Sync
                </h3>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    {server_badges_html}
                    {auto_sync_badge}
                </div>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
                Bi-directional sync matches watched history and ratings between your media servers (Plex, Jellyfin, Emby) and Trakt with automatic echo-loop suppression.
            </p>
            <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
                <div>
                    <div style="font-size:14px;font-weight:600;color:#f8fafc;display:flex;align-items:center;gap:6px;">
                        <span>Pending Discrepancies</span>
                        <span id="reconcile-diff-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 8px;border-radius:9999px;font-size:12px;font-weight:700;">{diff_count}</span>
                    </div>
                    <div style="font-size:12px;color:#94a3b8;margin-top:4px;">
                        Ratings sync: {'Enabled' if sync_status.get('sync_ratings') else 'Disabled'} &bull; Startup sync: {'Active' if sync_status.get('sync_on_startup') else 'Off'}
                    </div>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    {f'<button onclick="openReconcileSettingsModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#f8fafc;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚙️ Configure</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔒 Configure</button>'}
                    {f'<button onclick="openReconcileModal(true)" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔍 Review Discrepancies</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔒 Review Discrepancies</button>'}
                    {f'<button onclick="quickReconcileTraktToPlex(this)" class="btn-sm" style="background:#10b981;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚡ Quick Sync (Trakt &rarr; {active_srv.capitalize()})</button>' if is_admin else ''}
                </div>
            </div>
        </div>
        """
    else:
        reconcile_card_html = f"""
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                    <span>🔄</span> Two-Way Library Reconciliation
                </h3>
                <span style="color:#94a3b8;font-size:12px;">● Direct API Not Configured</span>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin-bottom:12px;line-height:1.5;">
                Enable direct media server reconciliation (Plex, Jellyfin, Emby) to pull watched history and user ratings from Trakt back to your media server with loop prevention.
            </p>
            <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;font-size:13px;color:#cbd5e1;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                <span>Configure your media server direct connection to activate two-way reconciliation and rating synchronization.</span>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    {f'<button onclick="openReconcileSettingsModal()" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚙️ Set Up Connection</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔒 Set Up Connection</button>'}
                    <a href="{REPO_URL}#readme" target="_blank" rel="noopener" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;text-decoration:none;">View Guide &rarr;</a>
                </div>
            </div>
        </div>
        """

    backup_card_html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>💾</span> System Operations & Observability
            </h3>
            <div style="display:flex;gap:8px;align-items:center;">
                {f'<button onclick="openLogsModal()" class="btn-sm" style="background:#1e293b;border:1px solid #3b82f6;color:#60a5fa;display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-weight:600;">📜 View Logs</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;cursor:pointer;" title="Admin unlock required to view logs">🔒 View Logs</button>'}
                <a href="/metrics" target="_blank" rel="noopener" class="btn-sm" style="background:#0f172a;border:1px solid #334155;color:#38bdf8;text-decoration:none;">📊 Prometheus /metrics ↗</a>
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
            Export or restore your configuration, multi-user Trakt tokens, co-watch whitelist, and inspect live service logs.
        </p>
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;">
            {f'''
            <a href="/api/backup" download class="btn-sm" style="background:#0284c7;color:#fff;text-decoration:none;padding:8px 16px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">
                💾 Download Backup (.zip)
            </a>
            <label class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:#e2e8f0;padding:8px 16px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px;">
                📤 Restore Backup (.zip)
                <input type="file" id="backup-file-input" accept=".zip" onchange="uploadBackup(this)" style="display:none;" />
            </label>
            <button onclick="openTestWebhookModal()" class="btn-sm" style="background:#4338ca;color:#fff;border:1px solid #6366f1;padding:8px 16px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px;">
                🧪 Test Webhook
            </button>
            ''' if is_admin else '<div style="font-size:12px;color:#64748b;">Admin authorization required to download or restore server backups.</div>'}
        </div>
    </div>
    """

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
    eco_servers = eco_data.get("servers", [])
    eco_healthy = eco_data.get("healthy_count", 0)
    eco_total = eco_data.get("total_count", len(eco_servers))

    eco_cards_html = ""
    for srv in eco_servers:
        st = srv.get("status", "unknown")
        if st == "connected":
            st_color = "#10b981"
            st_bg = "#064e3b"
            st_border = "#059669"
        elif st == "available":
            st_color = "#38bdf8"
            st_bg = "#0c4a6e"
            st_border = "#0284c7"
        elif st == "disabled":
            st_color = "#cbd5e1"
            st_bg = "#334155"
            st_border = "#64748b"
        elif st == "error":
            st_color = "#f87171"
            st_bg = "#7f1d1d"
            st_border = "#dc2626"
        else:
            st_color = "#94a3b8"
            st_bg = "#1e293b"
            st_border = "#334155"

        srv_icon = "🎬"
        sid = srv.get("id", "")
        if sid == "plex":
            srv_icon = "🔶"
        elif sid == "jellyfin":
            srv_icon = "🟣"
        elif sid == "emby":
            srv_icon = "🟢"
        elif sid == "trakt":
            srv_icon = "🔴"
        elif sid == "simkl":
            srv_icon = "✨"
        elif sid == "anilist":
            srv_icon = "⚡"
        elif sid == "myanimelist":
            srv_icon = "🎌"
        elif sid == "sonarr":
            srv_icon = "📺"
        elif sid == "radarr":
            srv_icon = "🍿"

        srv_name = html.escape(srv.get('name', ''))
        is_disabled = (not srv.get("enabled", True)) or st == "disabled" or srv.get("badge") in ("Disabled", "Paused")
        card_class = "eco-card eco-card-disabled" if is_disabled else "eco-card"
        card_extra_style = "opacity:0.65;transition:opacity 0.2s ease,border-color 0.2s ease;" if is_disabled else ""
        card_extra_attrs = 'onmouseenter="this.style.opacity=\'1\'" onmouseleave="this.style.opacity=\'0.65\'"' if is_disabled else ""

        toggle_btn = ""
        if is_admin and sid in ("plex", "jellyfin", "emby", "trakt", "simkl", "anilist", "myanimelist"):
            cat = "server" if sid in ("plex", "jellyfin", "emby") else "tracker"
            key = "mal" if sid == "myanimelist" else sid
            is_en = srv.get("enabled", True)
            if cat == "server":
                config_gear = f'<button onclick="openReconcileSettingsModal(\'{sid}\')" class="btn-sm" style="display:inline-flex;align-items:center;padding:3px 7px;font-size:11px;background:#1e293b;border:1px solid #475569;color:#38bdf8;cursor:pointer;" title="Configure {srv_name} Direct API">⚙️</button>'
                if is_en:
                    toggle_btn = f'{config_gear} <button onclick="toggleSetting(\'{cat}\', \'{key}\', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Disable {srv_name}"><span>⏸</span><span>Disable</span></button>'
                else:
                    toggle_btn = f'{config_gear} <button onclick="toggleSetting(\'{cat}\', \'{key}\', true, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:600;border-radius:6px;background:#064e3b;border:1px solid #059669;color:#6ee7b7;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Enable {srv_name}"><span>▶</span><span>Enable</span></button>'
            else:
                if is_en:
                    toggle_btn = f'<button onclick="toggleSetting(\'{cat}\', \'{key}\', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Pause {srv_name}"><span>⏸</span><span>Pause</span></button>'
                else:
                    toggle_btn = f'<button onclick="toggleSetting(\'{cat}\', \'{key}\', true, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:600;border-radius:6px;background:#064e3b;border:1px solid #059669;color:#6ee7b7;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Resume {srv_name}"><span>▶</span><span>Resume</span></button>'

        eco_cards_html += f"""
        <div class="{card_class}" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;{card_extra_style}" {card_extra_attrs}>
            <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                    <span style="font-size:18px;flex-shrink:0;">{srv_icon}</span>
                    <div style="min-width:0;">
                        <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{html.escape(srv.get('name', ''))}</div>
                        <div style="font-size:11px;color:#64748b;">{html.escape(srv.get('category', ''))}</div>
                    </div>
                </div>
                <span style="background:{st_bg};border:1px solid {st_border};color:{st_color};font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">
                    {html.escape(srv.get('badge', st.capitalize()))}
                </span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="{html.escape(srv.get('details', ''))}">
                    {html.escape(srv.get('details', ''))}
                </div>
                {toggle_btn}
            </div>
        </div>
        """

    ecosystem_card_html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>🌐</span> Multi-Server Ecosystem
            </h3>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="background:#0f172a;border:1px solid #334155;color:#10b981;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">
                    <span style="width:7px;height:7px;border-radius:50%;background:#10b981;display:inline-block;"></span>
                    {eco_healthy}/{eco_total} Services Healthy
                </span>
                <button onclick="openSettingsModal('servers')" class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:#cbd5e1;padding:4px 10px;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:5px;">⚙️ Manage Servers</button>
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;line-height:1.5;">
            Unified operational topology across all media servers, Trakt scrobble tracker, and automated media acquisition engines.
        </p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(270px, 1fr));gap:10px;">
            {eco_cards_html}
        </div>
    </div>
    """

    # Multi-Tracker Architecture Card (Simkl)
    if is_demo:
        simkl_status = {
            "enabled": True,
            "configured": True,
            "authenticated": True,
            "user": "demo_viewer",
            "account_id": 987654,
        }
    else:
        simkl_status = await simkl.check_connection()
        simkl_status["enabled"] = simkl.is_enabled()
        simkl_status["configured"] = bool(simkl.effective_client_id)

    simkl_cfg = simkl_status.get("configured", False)
    simkl_auth = simkl_status.get("authenticated", False)
    simkl_user = simkl_status.get("user")
    simkl_disp_user = (simkl_user if is_admin else mask_username(simkl_user)) if simkl_user else "Linked"

    if not settings_mgr.is_tracker_enabled("simkl"):
        simkl_badge = f'<span style="background:#334155;border:1px solid #64748b;color:#cbd5e1;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">⏸️ Paused (@{simkl_disp_user})</span>'
    elif simkl_auth:
        simkl_badge = f'<span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● Active (@{simkl_disp_user})</span>'
    elif simkl_cfg:
        simkl_badge = '<span style="background:#1e293b;border:1px solid #eab308;color:#fde047;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● PIN Required</span>'
    else:
        simkl_badge = '<span style="background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">● Optional Tracker</span>'

    quick_scrobble_btn = '<button onclick="openManualScrobbleModal()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;">🍿 Quick Scrobble</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🔒 Quick Scrobble</button>'

    simkl_action_btn = ""
    if is_admin:
        if simkl_auth:
            simkl_paused = not settings_mgr.is_tracker_enabled("simkl")
            simkl_toggle_btn = f'<button onclick="toggleSetting(\'tracker\', \'simkl\', {str(simkl_paused).lower()}, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:5px;background:#1e293b;border:1px solid #475569;color:{"#a7f3d0" if simkl_paused else "#cbd5e1"};padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;line-height:1.2;">{"▶ Resume Simkl" if simkl_paused else "⏸ Pause Simkl"}</button>'
            simkl_action_btn = f'{simkl_toggle_btn} <button onclick="disconnectSimkl(this)" class="btn-sm" style="background:#7f1d1d;border:1px solid #ef4444;color:#fee2e2;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">Disconnect</button>'
        else:
            simkl_action_btn = '<button onclick="openSimklModal()" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🔑 Link Simkl Account</button>'
    else:
        simkl_action_btn = '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🔒 Manage Simkl</button>'

    cross_sync_btn = ""
    if simkl_auth and (is_demo or trakt.is_authenticated()):
        if is_admin:
            cross_sync_btn = '<button onclick="openCrossSyncModal(true)" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;">🔄 Reconcile Trakt & Simkl</button>'
        else:
            cross_sync_btn = '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🔒 Reconcile</button>'

    simkl_card_html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>✨</span> Multi-Tracker Architecture &bull; Simkl Integration
            </h3>
            <div style="display:flex;align-items:center;gap:8px;">
                {simkl_badge}
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;line-height:1.5;">
            Broadcast playback scrobbles and ratings across both Trakt and Simkl simultaneously. Cross-tracker two-way sync reconciles historical watch states and ratings bi-directionally across Movies, TV Shows, and Anime.
        </p>
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
            <div style="font-size:12px;color:#cbd5e1;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span>Simkl Dual-Scrobbler: <strong>{"Active" if simkl_auth else "Ready to link" if simkl_cfg else "Disabled in .env"}</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>Cross-Tracker Sync: <strong>{"Ready" if simkl_auth and (is_demo or trakt.is_authenticated()) else "Requires Trakt + Simkl Auth"}</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>Supported: <strong>Movies, Shows, Anime</strong></span>
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                {quick_scrobble_btn}
                {cross_sync_btn}
                {simkl_action_btn}
                <button onclick="openSettingsModal('trackers', 'simkl')" class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:#38bdf8;padding:6px 12px;font-size:12px;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;cursor:pointer;">⚙️ Simkl Settings</button>
                <a href="/auth/simkl" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;text-decoration:none;padding:6px 12px;font-size:12px;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;">PIN Portal ↗</a>
            </div>
        </div>
    </div>
    """

    # Anime Tracking Engine Card (AniList & MyAnimeList)
    if is_demo:
        ani_status = demo_mgr.get_demo_anilist_status()
        mal_status = demo_mgr.get_demo_mal_status()
    else:
        ani_status = await anilist.check_connection()
        ani_status["enabled"] = anilist.is_enabled()
        ani_status["configured"] = bool(getattr(Config, "ANILIST_CLIENT_ID", "") or anilist.is_authenticated())

        mal_status = await mal.check_connection()
        mal_status["enabled"] = mal.is_enabled()
        mal_status["configured"] = bool(mal.effective_client_id or mal.is_authenticated())

    ani_auth = ani_status.get("authenticated", False)
    ani_user = ani_status.get("user")
    ani_disp_user = (ani_user if is_admin else mask_username(ani_user)) if ani_user else "Linked"

    if not settings_mgr.is_tracker_enabled("anilist"):
        ani_badge = f'<span style="background:#334155;border:1px solid #64748b;color:#cbd5e1;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">⏸️ AniList Paused</span>'
    elif ani_auth:
        ani_badge = f'<span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● AniList Active (@{ani_disp_user})</span>'
    else:
        ani_badge = '<span style="background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">● AniList Unlinked</span>'

    mal_auth = mal_status.get("authenticated", False)
    mal_user = mal_status.get("user")
    mal_disp_user = (mal_user if is_admin else mask_username(mal_user)) if mal_user else "Linked"

    if not settings_mgr.is_tracker_enabled("mal"):
        mal_badge = f'<span style="background:#334155;border:1px solid #64748b;color:#cbd5e1;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">⏸️ MAL Paused</span>'
    elif mal_auth:
        mal_badge = f'<span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● MAL Active (@{mal_disp_user})</span>'
    else:
        mal_badge = '<span style="background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">● MAL Unlinked</span>'

    ani_action_btn = ""
    mal_action_btn = ""
    if is_admin:
        if ani_auth:
            ani_paused = not settings_mgr.is_tracker_enabled("anilist")
            ani_toggle_btn = f'<button onclick="toggleSetting(\'tracker\', \'anilist\', {str(ani_paused).lower()}, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:5px;background:#1e293b;border:1px solid #475569;color:{"#a7f3d0" if ani_paused else "#cbd5e1"};padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;line-height:1.2;">{"▶ Resume" if ani_paused else "⏸ Pause"}</button>'
            ani_action_btn = f'{ani_toggle_btn} <button onclick="disconnectAnilist(this)" class="btn-sm" style="background:#7f1d1d;border:1px solid #ef4444;color:#fee2e2;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">Disconnect AniList</button>'
        else:
            ani_action_btn = '<button onclick="openAnilistModal()" class="btn-sm" style="background:#02a9ff;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">⚡ Link AniList</button>'

        if mal_auth:
            mal_paused = not settings_mgr.is_tracker_enabled("mal")
            mal_toggle_btn = f'<button onclick="toggleSetting(\'tracker\', \'mal\', {str(mal_paused).lower()}, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:5px;background:#1e293b;border:1px solid #475569;color:{"#a7f3d0" if mal_paused else "#cbd5e1"};padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;line-height:1.2;">{"▶ Resume" if mal_paused else "⏸ Pause"}</button>'
            mal_action_btn = f'{mal_toggle_btn} <button onclick="disconnectMal(this)" class="btn-sm" style="background:#7f1d1d;border:1px solid #ef4444;color:#fee2e2;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">Disconnect MAL</button>'
        else:
            mal_action_btn = '<button onclick="openMalModal()" class="btn-sm" style="background:#2e51a2;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🎌 Link MAL</button>'
    else:
        ani_action_btn = '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🔒 Manage AniList</button>'
        mal_action_btn = '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🔒 Manage MAL</button>'

    anime_card_html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>⚡</span> Anime Tracking Engine &bull; AniList &amp; MyAnimeList
            </h3>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                {ani_badge}
                {mal_badge}
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;line-height:1.5;">
            Specialized anime detection with automatic ID resolution across AniList and MyAnimeList. Scrobbles anime episode progress and synchronizes ratings in real-time with zero media playback latency.
        </p>
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
            <div style="font-size:12px;color:#cbd5e1;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span>Detection: <strong>{"Auto-Detect Active" if Config.ANIME_AUTO_DETECT else "Explicit Only"}</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>AniList: <strong>{"Connected" if ani_auth else "Unlinked"}</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>MAL: <strong>{"Connected" if mal_auth else "Unlinked"}</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>API: <strong>GraphQL &amp; REST v2</strong></span>
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                {ani_action_btn}
                {mal_action_btn}
                <button onclick="openSettingsModal('trackers', 'anilist')" class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:#38bdf8;padding:6px 12px;font-size:12px;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;cursor:pointer;">⚙️ Anime Settings</button>
                <a href="/auth/anilist" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;text-decoration:none;padding:6px 12px;font-size:12px;display:inline-flex;align-items:center;gap:4px;">AniList Portal ↗</a>
                <a href="/auth/mal" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#818cf8;text-decoration:none;padding:6px 12px;font-size:12px;display:inline-flex;align-items:center;gap:4px;">MAL Portal ↗</a>
            </div>
        </div>
    </div>
    """

    # Arr Watchlist Automation Bridge Card
    arr_status = await arr_bridge.get_status(demo=is_demo)
    arr_cfg = arr_status.get("configured", False)
    sonarr_cfg = arr_status.get("sonarr_configured", False)
    sonarr_conn = arr_status.get("sonarr_connected", False)
    radarr_cfg = arr_status.get("radarr_configured", False)
    radarr_conn = arr_status.get("radarr_connected", False)
    auto_add_on = arr_status.get("auto_add_enabled", False)
    arr_interval = arr_status.get("interval_seconds", 1800)
    int_mins = max(1, arr_interval // 60) if arr_interval else 0

    if arr_cfg:
        auto_badge = (
            f'<span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">Auto-Add: Every {int_mins}m</span>'
            if auto_add_on
            else '<span style="background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">Auto-Add: Manual</span>'
        )

        sonarr_desc = f"{arr_status.get('sonarr_series_count', 0)} Series" if sonarr_conn else ("Connected" if sonarr_conn else "Offline" if sonarr_cfg else "Disabled")
        radarr_desc = f"{arr_status.get('radarr_movies_count', 0)} Movies" if radarr_conn else ("Connected" if radarr_conn else "Offline" if radarr_cfg else "Disabled")

        sonarr_pill = (
            f'<span style="background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:3px 9px;border-radius:6px;font-size:12px;display:inline-flex;align-items:center;gap:6px;"><span style="color:#38bdf8;">📺 Sonarr</span><span style="color:#10b981;font-weight:600;">{sonarr_desc}</span></span>'
            if sonarr_cfg
            else '<span style="background:#0f172a;border:1px solid #334155;color:#64748b;padding:3px 9px;border-radius:6px;font-size:12px;">📺 Sonarr: Off</span>'
        )

        radarr_pill = (
            f'<span style="background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:3px 9px;border-radius:6px;font-size:12px;display:inline-flex;align-items:center;gap:6px;"><span style="color:#f59e0b;">🍿 Radarr</span><span style="color:#10b981;font-weight:600;">{radarr_desc}</span></span>'
            if radarr_cfg
            else '<span style="background:#0f172a;border:1px solid #334155;color:#64748b;padding:3px 9px;border-radius:6px;font-size:12px;">🍿 Radarr: Off</span>'
        )

        sync_btn_html = (
            '<button onclick="triggerArrWatchlistSync(this)" class="btn-sm" style="background:#10b981;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚡ Sync Watchlist Now</button>'
            if is_admin
            else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔒 Sync Watchlist</button>'
        )

        arr_bridge_card_html = f"""
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                    <span>🎬</span> Content Bridge & *Arr Watchlist Automation
                </h3>
                <div style="display:flex;align-items:center;gap:8px;">
                    {auto_badge}
                </div>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
                Automatically monitors your Trakt Watchlist, checks library duplicates, and acquires new movies and shows into Radarr and Sonarr with automatic search and notification dispatch.
            </p>
            <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                    {sonarr_pill}
                    {radarr_pill}
                    <span style="font-size:12px;color:#94a3b8;">Search on add: <strong>{'Enabled' if arr_status.get('search_on_add') else 'Disabled'}</strong> &bull; Alerts: <strong>{'On' if Config.ARR_NOTIFY_ON_ADD else 'Off'}</strong></span>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    {sync_btn_html}
                    <button onclick="openArrModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:8px 14px;display:inline-flex;align-items:center;gap:6px;">📋 View Log</button>
                    <button onclick="openSettingsModal('automation')" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:8px 14px;display:inline-flex;align-items:center;gap:6px;cursor:pointer;">⚙️ Configure</button>
                </div>
            </div>
        </div>
        """
    else:
        arr_bridge_card_html = f"""
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                    <span>🎬</span> Content Bridge & *Arr Automation
                </h3>
                <span style="color:#94a3b8;font-size:12px;">● Not Configured</span>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin-bottom:12px;line-height:1.5;">
                Connect Trakt Watchlists directly to Sonarr and Radarr. When you add movies or shows to your Trakt Watchlist, Omniscrobble automatically looks them up and queues them for acquisition.
            </p>
            <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;font-size:13px;color:#cbd5e1;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                <span>Configure Sonarr and Radarr connections directly in the Settings Hub or via <code>.env</code>.</span>
                <div style="display:flex;gap:6px;align-items:center;">
                    <button onclick="openSettingsModal('automation')" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;padding:6px 12px;border:none;cursor:pointer;">⚙️ Setup *Arr Bridge</button>
                    <a href="{REPO_URL}#readme" target="_blank" rel="noopener" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;text-decoration:none;">View Guide &rarr;</a>
                </div>
            </div>
        </div>
        """

    stats_data = demo_mgr.get_demo_stats() if is_demo else scrobble_stats

    trakt_configured = bool(auth_status)
    simkl_configured = bool(simkl_auth)
    anilist_configured = bool(ani_auth)
    mal_configured = bool(mal_auth)
    cowatch_user = Config.CO_WATCH_USER
    cowatch_disp = (cowatch_user if is_admin else mask_username(cowatch_user)) if cowatch_user else ""
    has_cowatch_partner = bool(cowatch_user and (is_demo or user_mgr.is_user_authenticated(cowatch_user)))

    rendered = DASHBOARD_HTML
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
        '{{SCROBBLE_CHECKED_ANILIST}}': ('checked' if anilist_configured else ''),
        '{{SCROBBLE_CHECKED_MAL}}': ('checked' if mal_configured else ''),
        '{{SCROBBLE_CHECKED_TRAKT_JS}}': ('true' if trakt_configured else 'false'),
        '{{SCROBBLE_CHECKED_SIMKL_JS}}': ('true' if simkl_configured else 'false'),
        '{{SCROBBLE_CHECKED_ANILIST_JS}}': ('true' if anilist_configured else 'false'),
        '{{SCROBBLE_CHECKED_MAL_JS}}': ('true' if mal_configured else 'false'),
        '{{SCROBBLE_BADGE_TRAKT}}': ('' if trakt_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_SIMKL}}': ('' if simkl_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_ANILIST}}': ('' if anilist_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_MAL}}': ('' if mal_configured else ' <span style="font-size:10px;color:#64748b;">(Not Linked)</span>'),
        '{{SCROBBLE_BADGE_COWATCH}}': (f' <span style="font-size:10px;color:#d8b4fe;">(@{cowatch_disp})</span>' if has_cowatch_partner else ' <span style="font-size:10px;color:#64748b;">(No partner linked)</span>'),
    }
    for k, v in replacements.items():
        rendered = rendered.replace(k, v)
    return HTMLResponse(
        content=rendered,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


if __name__ == '__main__':
    if Config.DEBUG:
        uvicorn.run('app.main:app', host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=True, reload_excludes=['*.json', 'data/*'])
    else:
        uvicorn.run('app.main:app', host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=False)
