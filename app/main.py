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
from fastapi.responses import HTMLResponse
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
from app.services.queue_manager import QueueManager, process_queue
from app.services.user_manager import user_mgr
from app.services.demo_manager import demo_mgr
from app.services.log_manager import log_mgr
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'
AUTH_LOCKED_HTML = (TEMPLATES_DIR / 'auth_locked.html').read_text(encoding='utf-8')
AUTH_HTML = (TEMPLATES_DIR / 'auth.html').read_text(encoding='utf-8')
DASHBOARD_HTML = (TEMPLATES_DIR / 'dashboard.html').read_text(encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("plex_trakt_scrobbler")

trakt = TraktClient(Config)
sonarr = SonarrClient()
user_mgr.set_default_client(trakt)
queue_mgr = QueueManager(Config.QUEUE_DB_FILE)

SERVER_START_TIME = time.time()

# In-memory scrobble counter
scrobble_stats: dict[str, int] = {
    "total": 0,
    "movies": 0,
    "episodes": 0,
    "ratings": 0,
    "collections": 0,
}


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global queue_worker_task
    queue_worker_task = asyncio.create_task(queue_worker_loop())

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

    yield
    if queue_worker_task:
        queue_worker_task.cancel()
        try:
            await queue_worker_task
        except asyncio.CancelledError:
            pass
    await trakt.close()
    await user_mgr.close_all()
    await notifier.close()


APP_VERSION = "1.2.0"
REPO_URL = "https://github.com/selits/plex-trakt-webhook"

app = FastAPI(title="Plex Trakt Scrobbler", version=APP_VERSION, lifespan=lifespan)


# In-memory log of recent webhook events for the status dashboard
MAX_HISTORY = 30
recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)


def log_event(media: ParsedMedia, action: str, result: dict[str, Any]):
    if media.media_type == "episode":
        title_str = f"{media.show_title} S{media.season:02d}E{media.episode:02d} - {media.title}"
    else:
        title_str = f"{media.title} ({media.year or 'N/A'})"

    action_str = f"{action} ({media.rating}/10)" if action == "rate" and media.rating else action
    progress_str = f"{media.rating}/10" if action == "rate" and media.rating else f"{media.progress:.1f}%"

    entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": media.username,
        "event": media.event,
        "action": action_str,
        "title": title_str,
        "type": media.media_type,
        "show_title": media.show_title if media.media_type == "episode" else None,
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
    }
    recent_events.appendleft(entry)


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


@app.post("/webhook")
async def plex_webhook(request: Request):
    """Receives multipart/form-data or json webhook notifications from Plex Media Server."""
    if Config.WEBHOOK_SECRET:
        token = request.query_params.get("token") or request.headers.get("x-webhook-secret")
        if not token or not secrets.compare_digest(token, Config.WEBHOOK_SECRET):
            logger.warning("Rejected unauthorized webhook request: invalid or missing token.")
            metrics_registry.record_request("webhook", 401)
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing webhook token")

    raw_data: Optional[dict[str, Any]] = None

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            raw_data = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse raw JSON body: {e}")
            metrics_registry.record_request("webhook", 400)
            raise HTTPException(status_code=400, detail="Invalid JSON body")
    else:
        try:
            form = await request.form()
            payload_field = form.get("payload")
            if payload_field is not None:
                if hasattr(payload_field, "read"):
                    content = await payload_field.read()
                    if isinstance(content, bytes):
                        content = content.decode("utf-8")
                    raw_data = json.loads(content)
                elif isinstance(payload_field, str):
                    raw_data = json.loads(payload_field)
                else:
                    raw_data = json.loads(str(payload_field))
        except Exception as e:
            logger.error(f"Failed to parse multipart form data: {e}")
            metrics_registry.record_request("webhook", 400)
            raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

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

    # Dynamic multi-user client resolution:
    # 1. Use user-specific Trakt client if authenticated for this Plex user
    # 2. Fall back to default Trakt client
    active_client = user_mgr.get_client(parsed.username)
    if not active_client.is_authenticated():
        active_client = trakt

    if not active_client.is_authenticated():
        logger.warning(f"Trakt is not authenticated for user '{parsed.username}' or default! Run 'python auth.py' or visit /auth to authorize.")
        metrics_registry.record_request("webhook", 200)
        return {"status": "error", "message": "Trakt not authenticated"}

    event = parsed.event
    scrobble_payload = parsed.to_trakt_scrobble_payload()
    result: dict[str, Any] = {}
    action_taken = "none"

    try:
        if event == "library.new":
            if not Config.SYNC_COLLECTION:
                metrics_registry.record_request("webhook", 200)
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
            metrics_registry.record_request("webhook", 200)
            return {"status": "success", "event": "library.new", "action": "collection", "result": result}

        elif event == "media.scrobble":
            # Plex determined the user watched the show/movie (>90%)
            action_taken = "mark_watched"
            logger.info(f"Marking as watched in Trakt: {parsed.title} for user {parsed.username}")
            playback_mgr.stop_playback(parsed)

            # 1. Stop scrobble with 100% progress
            scrobble_payload["progress"] = 100.0
            scrobble_res = await active_client.scrobble_stop(scrobble_payload)

            # 2. Also sync to history to guarantee item is marked as viewed
            history_res = await active_client.sync_history(parsed.to_trakt_history_payload())
            result = {"scrobble": scrobble_res, "history": history_res}

            # Enqueue to offline retry if transient error occurred
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
            # Real-time scrobbling on play, pause, resume, stop
            if event in ("media.play", "media.resume"):
                action_taken = "scrobble_start"
                logger.info(f"Scrobble start: {parsed.title} ({parsed.progress:.1f}%)")
                playback_mgr.update_playback(parsed, state="playing")
                result = await active_client.scrobble_start(scrobble_payload)
            elif event == "media.pause":
                playback_mgr.update_playback(parsed, state="paused")
                if parsed.progress >= Config.SCROBBLE_THRESHOLD:
                    action_taken = "scrobble_stop"
                    logger.info(f"Scrobble stop (paused past threshold): {parsed.title} ({parsed.progress:.1f}%)")
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
                if parsed.progress >= Config.SCROBBLE_THRESHOLD:
                    action_taken = "scrobble_stop"
                    logger.info(f"Scrobble stop (watched): {parsed.title} ({parsed.progress:.1f}%)")
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
                    logger.info(f"Playback stopped below threshold: {parsed.title} ({parsed.progress:.1f}%)")
                    if parsed.progress >= 1.0:
                        result = await active_client.scrobble_stop(scrobble_payload)
                    else:
                        result = {"status": "ignored", "reason": "Progress below 1.0%"}
        else:
            action_taken = f"skipped_{event}"

        log_event(parsed, action_taken, result)

        # Trigger outgoing notifications on scrobble or rating
        if action_taken in ("mark_watched", "scrobble_stop", "rate"):
            asyncio.create_task(notifier.dispatch(parsed, action_taken))

        # Trigger Co-Watching dual-sync if event qualifies
        if (event == "media.scrobble" or (action_taken == "scrobble_stop" and parsed.progress >= Config.SCROBBLE_THRESHOLD)) and cowatch_mgr.should_cowatch(parsed):
            asyncio.create_task(execute_cowatch_sync(parsed, action_taken))

        metrics_registry.record_request("webhook", 200)
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
        metrics_registry.record_request("webhook", 500)
        return {"status": "error", "error": str(e), "queued": True}


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
        "authenticated": trakt.is_authenticated(),
        "trakt_user": profile.get("username") if profile else None,
        "allowed_users": Config.PLEX_ALLOWED_USERS or "all",
        "allowed_libraries": Config.ALLOWED_LIBRARIES or "all",
        "excluded_libraries": Config.EXCLUDED_LIBRARIES or "none",
        "sync_collection": Config.SYNC_COLLECTION,
        "scrobble_mode": Config.SCROBBLE_MODE,
        "webhook_secret_enabled": bool(Config.WEBHOOK_SECRET),
        "sonarr": {
            "configured": sonarr.is_configured,
        },
        "uptime": get_uptime_str(),
        "token_health": token_info,
        "stats": scrobble_stats,
        "queue": {
            "pending": queue_mgr.get_pending_count(),
        },
        "notifications": notifier.get_status(),
    }


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

        # 3. Co-watch shows file
        if Config.CO_WATCH_DATA_FILE.exists():
            zf.write(Config.CO_WATCH_DATA_FILE, arcname="data/cowatch_shows.json")

        # 4. SQLite queue database
        if Config.QUEUE_DB_FILE.exists():
            zf.write(Config.QUEUE_DB_FILE, arcname="data/queue.db")

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

    except Exception as e:
        logger.error(f"Error restoring backup: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to restore backup: {e}")

    return {"status": "success", "restored": restored_files}



@app.get("/api/events")
def get_events(request: Request):
    if request.query_params.get("demo") == "true":
        return {"events": demo_mgr.get_demo_events()}
    is_admin = is_admin_request(request)
    events = list(recent_events)
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
            }
            for ev in events
        ]
    return {"events": events}


@app.post("/api/events/clear")
def clear_events(request: Request):
    if request.query_params.get("demo") == "true":
        return {"status": "cleared"}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    recent_events.clear()
    return {"status": "cleared"}


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
    media_type: str  # "movie" or "episode" or "show"
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    ids: dict[str, Any] = {}


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
    if request and request.query_params.get("demo") == "true":
        return {"status": "success", "result": {"scrobbled": True}}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not trakt.is_authenticated():
        raise HTTPException(status_code=400, detail="Trakt is not authenticated")

    if payload.media_type == "episode":
        history_payload: dict[str, Any] = {
            "shows": [
                {
                    "title": payload.title,
                    "seasons": [
                        {
                            "number": payload.season if payload.season is not None else 1,
                            "episodes": [
                                {
                                    "number": payload.episode if payload.episode is not None else 1
                                }
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

    res = await trakt.sync_history(history_payload)
    if is_temporary_error(res):
        queue_mgr.enqueue("sync_history", history_payload, error=str(res.get("error", "")))

    scrobble_stats["total"] += 1
    if payload.media_type == "movie":
        scrobble_stats["movies"] += 1
    elif payload.media_type == "episode":
        scrobble_stats["episodes"] += 1

    profile = await get_cached_trakt_profile()
    admin_user = (profile.get("username") if profile else None) or "admin"
    media_obj = ParsedMedia(
        event="manual.scrobble",
        username=admin_user,
        media_type=payload.media_type,
        title=payload.title,
        year=payload.year,
        season=payload.season,
        episode=payload.episode,
        progress=100.0,
        ids=payload.ids,
    )
    log_event(media_obj, "manual_scrobble", res)
    asyncio.create_task(notifier.dispatch(media_obj, "mark_watched"))

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


class AdminUnlockRequest(BaseModel):
    token: str


@app.post("/api/admin/unlock")
def admin_unlock(payload: AdminUnlockRequest, response: Response):
    if not Config.WEBHOOK_SECRET:
        return {"status": "ok", "message": "Admin authentication not required"}
    if not secrets.compare_digest(payload.token, Config.WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid admin secret")
    response.set_cookie(
        key="admin_token",
        value=Config.WEBHOOK_SECRET,
        httponly=True,
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


@app.get("/auth", response_class=HTMLResponse)

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
        masked_webhook_url = full_webhook_url
        demo_banner = '<div style="background:linear-gradient(90deg, #1e3a8a, #0284c7);color:#ffffff;padding:12px 18px;border-radius:10px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 4px 6px -1px rgba(0,0,0,0.3);flex-wrap:wrap;gap:10px;"><div style="display:flex;align-items:center;gap:10px;"><span style="font-size:18px;">🎭</span><div><strong style="color:#ffffff;">Demo Mode Active:</strong><span style="color:#e0f2fe;font-size:13px;margin-left:4px;">Simulated authenticated view with mock information. No real accounts or tokens are exposed.</span></div></div><a href="/" style="background:rgba(255,255,255,0.2);color:#ffffff;text-decoration:none;padding:5px 12px;border-radius:6px;font-weight:600;font-size:12px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(255,255,255,0.3)\'" onmouseout="this.style.background=\'rgba(255,255,255,0.2)\'">Exit Demo &rarr;</a></div>'
        demo_footer_link = '<a href="/" style="color:#38bdf8;text-decoration:none;font-weight:600;">Exit Demo</a>'
    else:
        demo_banner = ""
        demo_footer_link = f'<a href="/demo" style="color: #64748b; text-decoration: none; font-weight: 500; transition: color 0.15s;" onmouseover="this.style.color=\'#f8fafc\'" onmouseout="this.style.color=\'#64748b\'">🎭 Demo Mode</a>'
        # Auto-login admin if valid ?token= passed in URL
        query_token = request.query_params.get("token")
        if query_token and Config.WEBHOOK_SECRET and secrets.compare_digest(query_token, Config.WEBHOOK_SECRET):
            response.set_cookie(
                key="admin_token",
                value=Config.WEBHOOK_SECRET,
                httponly=True,
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
        if auth_status:
            user_label = f"Connected as @{display_username}" if display_username else "Connected"
            if is_admin:
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

        # Base URL for webhook
        base_url = str(request.base_url).rstrip("/")
        if Config.WEBHOOK_SECRET:
            full_webhook_url = f"{base_url}/webhook?token={Config.WEBHOOK_SECRET}"
            scheme = request.base_url.scheme or "http"
            port_suffix = ":●●●●" if request.base_url.port else ""
            masked_webhook_url = f"{scheme}://●●●●●●●●{port_suffix}/webhook?token=●●●●●●●●"
        else:
            full_webhook_url = f"{base_url}/webhook"
            masked_webhook_url = full_webhook_url

    # Events rows
    events_list = demo_mgr.get_demo_events() if is_demo else recent_events
    rows = ""
    col_span = 7 if is_admin else 6
    if not events_list:
        rows = f'<tr><td colspan="{col_span}" style="text-align:center;padding:24px;color:#94a3b8;">No scrobble events received yet. Start playing media on Plex to test!</td></tr>'
    else:
        for ev in events_list:
            color = "#10b981" if ev["result_status"] in ("ok", 200, 201) else "#f59e0b"
            u = ev["user"] if is_admin else mask_username(ev["user"])
            action_col = ""
            if is_admin:
                show_title = ev.get("show_title")
                action_buttons = []
                if show_title:
                    show_esc = urllib.parse.quote(show_title)
                    action_buttons.append(f'<button data-show="{show_esc}" onclick="quickAddShow(decodeURIComponent(this.dataset.show), this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#1e293b;border:1px solid #334155;" title="Always co-watch this show">+ Co-Watch</button>')
                if (Config.CO_WATCH_USER or is_demo) and ev.get("media_payload"):
                    media_enc = urllib.parse.quote(json.dumps(ev["media_payload"]))
                    action_buttons.append(f'<button onclick="quickSyncPartner(\'{media_enc}\', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#701a75;color:#f5d0fe;" title="Sync to partner">+ Sync Partner</button>')
                action_col = f'<td style="padding:12px 16px;white-space:nowrap;display:flex;gap:4px;">{"".join(action_buttons)}</td>'

            rows += f"""
            <tr style="border-bottom: 1px solid #334155;">
                <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">{ev['timestamp']}</td>
                <td style="padding:12px 16px;color:#f8fafc;font-weight:500;">{ev['title']}</td>
                <td style="padding:12px 16px;"><span style="background:#0f172a;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">{ev['type']}</span></td>
                <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">{u}</td>
                <td style="padding:12px 16px;"><span style="background:#0f172a;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;">{ev['action']} ({ev['progress']})</span></td>
                <td style="padding:12px 16px;"><span style="color:{color};font-weight:600;font-size:13px;">{ev['result_status']}</span></td>
                {action_col}
            </tr>
            """

    webhook_html_section = f"""
    <div style="margin-top: 18px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <div class="info-label">Plex Webhook URL</div>
            <span style="color:#10b981;font-size:11px;font-weight:600;">✓ Admin Unlocked</span>
        </div>
        <div style="display:flex;gap:8px;">
            <input type="text" readonly id="webhook-url-input" value="{full_webhook_url}"
                   style="flex:1;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;color:#38bdf8;font-family:monospace;font-size:13px;outline:none;" />
            <button onclick="copyWebhookUrl()" id="copy-btn" class="btn-copy">
                📋 Copy URL
            </button>
        </div>
        <div style="font-size: 12px; color: #94a3b8; margin-top: 6px;">Add this URL in Plex Web: <strong>Settings &rarr; Webhooks &rarr; Add Webhook</strong>.</div>
    </div>
    """ if is_admin else f"""
    <div style="margin-top: 18px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <div class="info-label">Plex Webhook URL</div>
            <span style="color:#f59e0b;font-size:11px;font-weight:600;">🔒 Secret Masked</span>
        </div>
        <div style="display:flex;gap:8px;">
            <input type="text" readonly value="{masked_webhook_url}"
                   style="flex:1;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;color:#64748b;font-family:monospace;font-size:13px;outline:none;user-select:none;" />
            <button onclick="openUnlockModal()" class="btn-copy" style="background:#2563eb;">
                🔓 Unlock
            </button>
        </div>
        <div style="font-size: 12px; color: #94a3b8; margin-top: 6px;">Admin authorization required to reveal and copy webhook URL.</div>
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
        stream_prog_text = "100.0%"
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

    active_playback_card_html = f"""
        <div id="active-playback-card" class="card" style="border-left: 4px solid {card_border}; margin-bottom: 24px; display: {card_display};">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px;">
                <div>
                    <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                        <span id="stream-pulse-indicator" class="pulse-indicator" style="background:{badge_color};"></span>
                        <span style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; color:{badge_color};" id="stream-state-badge">{badge_text}</span>
                        <span style="font-size:12px; color:#94a3b8;" id="stream-user-device">{user_dev}</span>
                    </div>
                    <h2 style="margin:4px 0 8px 0; font-size:18px; color:#f8fafc;" id="stream-title">{stream_title}</h2>
                </div>
                <div id="stream-actions">
                    <a id="stream-trakt-link" href="{stream_url}" target="_blank" rel="noopener" class="btn-sm" style="background:#334155; color:#38bdf8; text-decoration:none; display:inline-flex; align-items:center; gap:4px;">View on Trakt ↗</a>
                </div>
            </div>
            <div style="margin-top:12px;">
                <div style="display:flex; justify-content:space-between; font-size:12px; color:#94a3b8; margin-bottom:6px;">
                    <span>Playback Progress</span>
                    <span id="stream-progress-text" style="font-weight:600; color:#f8fafc;">{stream_prog_text}</span>
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
        configured_users = demo_mgr.get_demo_users()
    else:
        cw_user = Config.CO_WATCH_USER
        cw_user_display = cw_user if is_admin else "●●●●●●●●"
        cw_shows = sorted(cowatch_mgr.get_shows(), key=lambda x: x.lower())
        configured_users = user_mgr.list_configured_users()

    # Shared show chips
    if not is_admin:
        count = len(cw_shows)
        chips_html = f'<div style="color:#94a3b8;font-size:13px;display:flex;align-items:center;gap:8px;padding:4px 2px;"><span>🔒</span><span><strong>{count} shared show{"s" if count != 1 else ""} configured</strong> &bull; Unlock admin access to view titles and manage whitelist.</span></div>'
    else:
        chips_html = ""
        for s in cw_shows:
            s_enc = urllib.parse.quote(s)
            del_btn = f'<button data-show="{s_enc}" onclick="removeCowatchShow(decodeURIComponent(this.dataset.show))" title="Remove {html.escape(s)}" style="background:none;border:none;color:#f87171;cursor:pointer;margin-left:6px;font-size:13px;font-weight:700;line-height:1;padding:0;" onmouseover="this.style.color=\'#ef4444\'" onmouseout="this.style.color=\'#f87171\'">&times;</button>'
            chips_html += f'<span class="cowatch-chip" data-title="{html.escape(s.lower())}" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:3px 9px;border-radius:9999px;font-size:12px;display:inline-flex;align-items:center;margin:2px 3px;">{html.escape(s)}{del_btn}</span>'
        if not chips_html:
            chips_html = '<span style="color:#64748b;font-size:12px;font-style:italic;">No shows added yet. Add shows below or directly from recent activity.</span>'

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
                link_btn = f' <a href="{link_url}" class="btn-sm" style="background:#2563eb;color:#fff;text-decoration:none;padding:2px 8px;font-size:11px;margin-left:6px;">Link &rarr;</a>'
            else:
                link_btn = f' <a href="{link_url}" class="btn-sm" style="background:#334155;color:#94a3b8;text-decoration:none;padding:2px 8px;font-size:11px;margin-left:6px;">Reconnect</a>'

        role_label = ""
        if is_def:
            role_label = '<span style="background:#1e3a8a;color:#93c5fd;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:4px;">Default</span>'
        elif is_cw:
            role_label = '<span style="background:#701a75;color:#f5d0fe;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:4px;">Partner</span>'

        if is_def and raw_username:
            display_name = raw_username if is_admin else mask_username(raw_username)
        elif is_cw and not is_admin:
            display_name = "●●●●●●●●"
        else:
            display_name = u_name if is_admin else mask_username(u_name)

        users_badges_html += f"""
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div style="display:flex;align-items:center;gap:6px;">
                <span style="font-weight:600;color:#f8fafc;font-size:13px;">@{display_name}</span>
                {role_label}
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
                <span style="color:{status_color};font-size:12px;font-weight:500;">● {status_text}</span>
                {link_btn}
            </div>
        </div>
        """

    if is_admin:
        rule_players_str = ", ".join(Config.CO_WATCH_PLAYERS) if Config.CO_WATCH_PLAYERS else "All Devices"
        devices_rule_html = f" &bull; Devices: <strong>{rule_players_str}</strong>"
    else:
        devices_rule_html = ""
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
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));gap:20px;">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">
                    <div style="font-size:13px;font-weight:600;color:#f1f5f9;display:flex;align-items:center;gap:6px;">
                        <span>Shared Shows Whitelist</span>
                        <span id="cowatch-count-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 6px;border-radius:9999px;font-size:11px;font-weight:700;">{len(cw_shows)}</span>
                    </div>
                    {f'<input type="text" id="cowatch-filter-input" placeholder="Filter list..." oninput="filterCowatchChips(this.value)" style="background:#0f172a;border:1px solid #334155;border-radius:4px;padding:3px 8px;color:#f8fafc;font-size:11px;outline:none;width:110px;" />' if is_admin else ''}
                </div>
                <div id="cowatch-chips-container" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 10px;min-height:54px;max-height:180px;overflow-y:auto;margin-bottom:10px;display:flex;flex-wrap:wrap;align-content:flex-start;align-items:center;">
                    {chips_html}
                </div>
                {f'''
                <form onsubmit="event.preventDefault();addCowatchShow();" autocomplete="off" style="margin:0;">
                    <div style="display:flex;gap:8px;position:relative;">
                        <div style="flex:1;position:relative;">
                            <input type="search" id="cowatch-show-input" name="cowatch_show_search" placeholder="Add show (e.g. Severance, The Bear)..."
                                   style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 12px;color:#f8fafc;font-size:13px;outline:none;"
                                   oninput="onCowatchShowInput(this.value)"
                                   onfocus="onCowatchShowInput(this.value)"
                                   autocomplete="off"
                                   data-lpignore="true"
                                   data-1p-ignore="true"
                                   onkeydown="if(event.key==='Enter')addCowatchShow()" />
                            <div id="sonarr-suggestions" style="display:none;position:absolute;top:100%;left:0;right:0;background:#1e293b;border:1px solid #3b82f6;border-radius:6px;margin-top:4px;max-height:220px;overflow-y:auto;z-index:100;box-shadow:0 10px 15px -3px rgba(0,0,0,0.7);"></div>
                        </div>
                        <button type="submit" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:8px 14px;">+ Add Show</button>
                    </div>
                </form>
                <div style="margin-top:4px;">{sonarr_status_note}</div>
                ''' if is_admin else '<div style="font-size:12px;color:#64748b;">Admin access required to add or remove shared shows.</div>'}
                <div style="margin-top:10px;font-size:12px;color:#94a3b8;">
                    Movies: <strong>{rule_movies_str}</strong>{devices_rule_html}
                </div>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <div style="font-size:13px;font-weight:600;color:#f1f5f9;">Linked Trakt Accounts</div>
                    {f'<button onclick="promptLinkAccount()" class="btn-sm" style="background:#334155;color:#38bdf8;">+ Link Account</button>' if is_admin else ''}
                </div>
                <div>
                    {users_badges_html}
                </div>
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
            ''' if is_admin else '<div style="font-size:12px;color:#64748b;">Admin authorization required to download or restore server backups.</div>'}
        </div>
    </div>
    """

    stats_data = demo_mgr.get_demo_stats() if is_demo else scrobble_stats

    rendered = DASHBOARD_HTML
    replacements = {
        '{{DEMO_BANNER}}': demo_banner,
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
        '{{COWATCH_CARD}}': cowatch_card_html,
        '{{BACKUP_CARD}}': backup_card_html,
        '{{MANUAL_SCROBBLE_BTN}}': manual_scrobble_btn_html,
        '{{RETRY_QUEUE_BTN}}': (f'<button onclick="retryQueue()" class="btn-sm" style="background:#d97706;color:#fff;font-weight:600;">🔄 Retry Queue ({pending_queue})</button>' if pending_queue > 0 else ''),
        '{{CLEAR_BUTTON}}': clear_button_html,
        '{{ACTIONS_HEADER}}': ('<th>Actions</th>' if is_admin else ''),
        '{{EVENT_ROWS}}': rows,
        '{{IS_ADMIN_JS}}': ('true' if is_admin else 'false'),
        '{{IS_DEMO_JS}}': ('true' if is_demo else 'false'),
        '{{APP_VERSION}}': APP_VERSION,
        '{{REPO_URL}}': REPO_URL,
    }
    for k, v in replacements.items():
        rendered = rendered.replace(k, v)
    return HTMLResponse(content=rendered)


if __name__ == '__main__':
    if Config.DEBUG:
        uvicorn.run('app.main:app', host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=True, reload_excludes=['*.json', 'data/*'])
    else:
        uvicorn.run('app.main:app', host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=False)
