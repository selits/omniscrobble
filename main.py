import asyncio
import datetime
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


from config import Config
from cowatch_manager import cowatch_mgr
from metrics import metrics_registry
from notifier import notifier
from playback_manager import playback_mgr
from plex_parser import ParsedMedia, parse_plex_webhook
from queue_manager import QueueManager, process_queue
from trakt_client import TraktClient
from user_manager import user_mgr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("plex_trakt_scrobbler")

trakt = TraktClient(Config)
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


app = FastAPI(title="Plex Trakt Scrobbler", version="1.0.0", lifespan=lifespan)


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
                action_taken = "scrobble_stop"
                logger.info(f"Scrobble stop: {parsed.title} ({parsed.progress:.1f}%)")
                playback_mgr.stop_playback(parsed)
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
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    recent_events.clear()
    return {"status": "cleared"}


@app.post("/api/queue/retry")
async def trigger_queue_retry(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    res = await process_queue(trakt, queue_mgr, user_mgr=user_mgr)
    return {"status": "ok", "result": res, "pending_count": queue_mgr.get_pending_count()}


@app.post("/api/queue/clear")
def trigger_queue_clear(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    queue_mgr.clear_queue()
    return {"status": "ok", "pending_count": 0}


@app.get("/api/playback")
def get_playback_status(request: Request):
    is_admin = is_admin_request(request)
    return {
        "active_sessions": playback_mgr.get_active_sessions(is_admin=is_admin),
        "recently_finished": playback_mgr.get_recently_finished(is_admin=is_admin),
    }


class ManualScrobbleRequest(BaseModel):
    media_type: str  # "movie" or "episode" or "show"
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    ids: dict[str, Any] = {}


@app.get("/api/search")
async def search_media_endpoint(query: str, type: Optional[str] = None, request: Request = None):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not query.strip():
        return {"results": []}
    results = await trakt.search_media(query.strip(), media_type=type)
    return {"results": results}


@app.post("/api/scrobble/manual")
async def manual_scrobble(payload: ManualScrobbleRequest, request: Request):
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
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if not payload.show.strip():
        raise HTTPException(status_code=400, detail="Show name cannot be empty")
    shows = cowatch_mgr.add_show(payload.show)
    return {"status": "ok", "shows": shows}


@app.delete("/api/cowatch/shows")
def delete_cowatch_show(show: str, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    shows = cowatch_mgr.remove_show(show)
    return {"status": "ok", "shows": shows}


@app.post("/api/cowatch/sync")
async def cowatch_manual_sync(payload: CowatchSyncRequest, request: Request):
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
async def auth_page(request: Request, user: Optional[str] = None):
    if not is_admin_request(request):
        locked_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Locked • Admin Authorization Required</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Inter', sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 36px; max-width: 440px; width: 100%; text-align: center; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.4); }
        .icon { width: 56px; height: 56px; margin: 0 auto 16px; display: block; }
        h1 { font-size: 20px; font-weight: 700; margin-bottom: 8px; color: #f8fafc; }
        p { color: #94a3b8; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; background: #3b82f6; color: #fff; text-decoration: none; padding: 12px 20px; border-radius: 8px; font-size: 14px; font-weight: 600; border: none; cursor: pointer; width: 100%; transition: background 0.15s; }
        .btn:hover { background: #2563eb; }
    </style>
</head>
<body>
    <div class="card">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
        <h1>Admin Authorization Required</h1>
        <p>Linking or managing the Trakt connection requires administrative privileges. Please return to the dashboard and unlock admin mode first.</p>
        <a href="/" class="btn">&larr; Return to Dashboard</a>
    </div>
</body>
</html>"""
        return HTMLResponse(content=locked_html, status_code=401)

    target_uname = user.strip() if user else ""
    target_client = user_mgr.get_client(target_uname)
    already_connected = target_client.is_authenticated()

    if target_uname:
        page_title = f"Link Trakt • @{target_uname}"
        h1_text = f"Link Trakt for @{target_uname}"
        p_desc = f"Authorize this scrobbler to record playback and sync history with Trakt for Plex user <strong>@{target_uname}</strong>."
        banner_user_str = f"Account for <strong>@{target_uname}</strong> is currently linked"
    else:
        profile = await get_cached_trakt_profile()
        default_username = profile.get("username") if profile else ""
        page_title = "Link Trakt Account"
        h1_text = "Link Trakt Account"
        p_desc = "Authorize this scrobbler to record playback and sync history with your Trakt profile."
        banner_user_str = f"Currently linked to <strong>@{default_username}</strong>" if default_username else "Currently linked"

    already_connected_banner = (
        f'<div style="background:#064e3b;border:1px solid #059669;color:#6ee7b7;padding:12px;border-radius:8px;margin-bottom:20px;font-size:14px;">'
        f'{banner_user_str}. You can authorize again below to reconnect or switch accounts.</div>'
        if already_connected
        else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{page_title}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: 'Inter', sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 36px; max-width: 480px; width: 100%; text-align: center; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.4); }}
        .icon {{ width: 56px; height: 56px; margin: 0 auto 16px; display: block; }}
        h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 8px; color: #f8fafc; }}
        p {{ color: #94a3b8; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }}
        .code-box {{ background: #0f172a; border: 2px dashed #ed1c24; border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
        .code-label {{ font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.05em; margin-bottom: 6px; }}
        .code-val {{ font-size: 32px; font-weight: 700; letter-spacing: 0.15em; color: #f8fafc; font-family: monospace; }}
        .btn {{ display: inline-flex; align-items: center; justify-content: center; gap: 8px; background: #ed1c24; color: #fff; text-decoration: none; padding: 12px 20px; border-radius: 8px; font-size: 14px; font-weight: 600; border: none; cursor: pointer; width: 100%; transition: background 0.15s; }}
        .btn:hover {{ background: #c81017; }}
        .btn-back {{ background: #334155; margin-top: 12px; color: #cbd5e1; }}
        .btn-back:hover {{ background: #475569; }}
        .status-row {{ margin-top: 18px; font-size: 13px; color: #38bdf8; display: flex; align-items: center; justify-content: center; gap: 8px; }}
        .spinner {{ width: 14px; height: 14px; border: 2px solid #38bdf8; border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite; }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body>
    <div class="card">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#ed1c24" stroke-width="2"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8" fill="#ed1c24"/></svg>
        <h1>{h1_text}</h1>
        <p>{p_desc}</p>
        {already_connected_banner}
        <div id="loading-view">
            <div class="status-row"><div class="spinner"></div> Requesting device code from Trakt...</div>
        </div>
        <div id="auth-view" style="display:none;">
            <div class="code-box">
                <div class="code-label">Your Activation Code</div>
                <div class="code-val" id="code-display">--------</div>
            </div>
            <a id="activate-link" class="btn" href="https://trakt.tv/activate" target="_blank" rel="noopener">
                1. Open trakt.tv/activate &rarr;
            </a>
            <div class="status-row" id="status-text">
                <div class="spinner"></div> Waiting for authorization on Trakt...
            </div>
        </div>
        <a href="/" class="btn btn-back">&larr; Return to Dashboard</a>
    </div>
    <script>
        const targetUser = "{target_uname}";
        let pollInterval = null;
        async function initAuth() {{
            try {{
                const url = targetUser ? ('/api/auth/start?user=' + encodeURIComponent(targetUser)) : '/api/auth/start';
                const res = await fetch(url, {{ method: 'POST' }});
                if (!res.ok) throw new Error('Failed to generate device code from Trakt. Check client credentials in .env.');
                const data = await res.json();
                document.getElementById('code-display').textContent = data.user_code;
                document.getElementById('activate-link').href = data.verification_url || 'https://trakt.tv/activate';
                document.getElementById('loading-view').style.display = 'none';
                document.getElementById('auth-view').style.display = 'block';

                const intervalMs = (data.interval || 5) * 1000;
                pollInterval = setInterval(async () => {{
                    try {{
                        const pollRes = await fetch('/api/auth/poll', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{ device_code: data.device_code, user: targetUser || null }})
                        }});
                        const pollData = await pollRes.json();
                        if (pollData.status === 'success') {{
                            clearInterval(pollInterval);
                            document.getElementById('status-text').innerHTML = '<span style="color:#10b981;font-weight:600;">&#10003; Successfully Authorized! Redirecting...</span>';
                            setTimeout(() => {{ window.location.href = '/'; }}, 1500);
                        }} else if (pollData.status === 'error') {{
                            clearInterval(pollInterval);
                            document.getElementById('status-text').innerHTML = '<span style="color:#ef4444;">Error: ' + pollData.message + '</span>';
                        }}
                    }} catch (e) {{
                        console.error('Polling error', e);
                    }}
                }}, intervalMs);
            }} catch (err) {{
                document.getElementById('loading-view').innerHTML = '<span style="color:#ef4444;">' + err.message + '</span>';
            }}
        }}
        window.onload = initAuth;
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, response: Response):
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
        masked_webhook_url = f"{base_url}/webhook?token=●●●●●●●●"
    else:
        full_webhook_url = f"{base_url}/webhook"
        masked_webhook_url = full_webhook_url

    # Events rows
    rows = ""
    col_span = 7 if is_admin else 6
    if not recent_events:
        rows = f'<tr><td colspan="{col_span}" style="text-align:center;padding:24px;color:#94a3b8;">No scrobble events received yet. Start playing media on Plex to test!</td></tr>'
    else:
        for ev in recent_events:
            color = "#10b981" if ev["result_status"] in ("ok", 200, 201) else "#f59e0b"
            u = ev["user"] if is_admin else mask_username(ev["user"])
            action_col = ""
            if is_admin:
                show_title = ev.get("show_title")
                action_buttons = []
                if show_title:
                    show_esc = show_title.replace("'", "\\'")
                    action_buttons.append(f'<button onclick="quickAddShow(\'{show_esc}\', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#1e293b;border:1px solid #334155;" title="Always co-watch this show">+ Co-Watch</button>')
                if Config.CO_WATCH_USER and ev.get("media_payload"):
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
    cw_user = Config.CO_WATCH_USER
    cw_user_display = cw_user if is_admin else mask_username(cw_user)
    cw_shows = cowatch_mgr.get_shows()
    configured_users = user_mgr.list_configured_users()

    # Shared show chips
    if not is_admin:
        count = len(cw_shows)
        chips_html = f'<div style="color:#94a3b8;font-size:13px;display:flex;align-items:center;gap:8px;padding:4px 2px;"><span>🔒</span><span><strong>{count} shared show{"s" if count != 1 else ""} configured</strong> &bull; Unlock admin access to view titles and manage whitelist.</span></div>'
    else:
        chips_html = ""
        for s in cw_shows:
            s_safe = s.replace("'", "\\'")
            del_btn = f'<button onclick="removeCowatchShow(\'{s_safe}\')" title="Remove show" style="background:none;border:none;color:#f87171;cursor:pointer;margin-left:6px;font-size:13px;font-weight:700;">&times;</button>'
            chips_html += f'<span style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:4px 10px;border-radius:9999px;font-size:12px;display:inline-flex;align-items:center;margin:3px;">{s}{del_btn}</span>'
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
                <div style="font-size:13px;font-weight:600;color:#f1f5f9;margin-bottom:8px;">Shared Shows Whitelist</div>
                <div id="cowatch-chips-container" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px;min-height:54px;margin-bottom:10px;display:flex;flex-wrap:wrap;align-items:center;">
                    {chips_html}
                </div>
                {f'''
                <div style="display:flex;gap:8px;">
                    <input type="text" id="cowatch-show-input" placeholder="Add show (e.g. Severance, The Bear)..."
                           style="flex:1;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 12px;color:#f8fafc;font-size:13px;outline:none;"
                           onkeydown="if(event.key==='Enter')addCowatchShow()" />
                    <button onclick="addCowatchShow()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:8px 14px;">+ Add Show</button>
                </div>
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
                <span>💾</span> System Backup & Prometheus Observability
            </h3>
            <a href="/metrics" target="_blank" rel="noopener" class="btn-sm" style="background:#0f172a;border:1px solid #334155;color:#38bdf8;text-decoration:none;">📊 Prometheus /metrics ↗</a>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
            Export or restore your configuration, multi-user Trakt tokens, co-watch whitelist, and offline retry queue.
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

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Plex to Trakt Scrobbler</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 32px 20px; }}
        .container {{ max-width: 1000px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 20px; border-bottom: 1px solid #1e293b; gap: 12px; flex-wrap: wrap; }}
        .title {{ font-size: 24px; font-weight: 700; color: #f8fafc; display: flex; align-items: center; gap: 12px; }}
        .header-actions {{ display: flex; align-items: center; gap: 10px; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 24px; border: 1px solid #334155; }}
        .card h3 {{ font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #e2e8f0; }}
        .info-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; }}
        .info-item {{ background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #334155; }}
        .info-label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
        .info-value {{ font-size: 15px; font-weight: 600; margin: 4px 0 2px 0; color: #f1f5f9; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        th {{ background: #0f172a; padding: 12px 16px; font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.05em; border-bottom: 1px solid #334155; }}
        .btn-sm {{ background: #334155; border: none; color: #e2e8f0; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; transition: background 0.15s, opacity 0.15s; }}
        .btn-sm:hover {{ opacity: 0.9; }}
        .btn-copy {{ background: #0284c7; color: #fff; border: none; border-radius: 8px; padding: 10px 16px; font-size: 13px; font-weight: 600; cursor: pointer; transition: background 0.15s; white-space: nowrap; }}
        .btn-copy:hover {{ opacity: 0.9; }}
        .pulse-indicator {{ width: 8px; height: 8px; border-radius: 50%; background: #10b981; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); animation: pulse 1.8s infinite; display: inline-block; }}
        @keyframes pulse {{ 0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }} 70% {{ transform: scale(1); box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }} 100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }} }}
        #scrobble-modal, #unlock-modal {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.75); z-index: 9999; align-items: center; justify-content: center; backdrop-filter: blur(2px); }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#ed1c24" stroke-width="2"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8" fill="#ed1c24"/></svg>
                Plex &rarr; Trakt Scrobbler
            </div>
            <div class="header-actions">
                {status_badge}
                {admin_btn}
            </div>
        </div>

        {active_playback_card_html}

        <div class="card">
            <h3>Server & Account Configuration</h3>
            <div class="info-grid">
                <div class="info-item">
                    <div class="info-label">Trakt Connection</div>
                    <div class="info-value">{'@' + display_username if display_username else ('Connected' if auth_status else 'Not Connected')}</div>
                    <div><span style="color:{token_health_color};font-size:12px;">● {token_health_str}</span></div>
                </div>
                <div class="info-item">
                    <div class="info-label">Plex Users & Libraries</div>
                    <div class="info-value">{allowed_users_display}</div>
                    <div style="font-size:12px;color:#94a3b8;">Libs: {allowed_libs_display} &bull; Coll: {'On' if Config.SYNC_COLLECTION else 'Off'}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Server Health</div>
                    <div class="info-value">🟢 Online</div>
                    <div style="font-size:12px;color:#94a3b8;">Uptime: {get_uptime_str()} &bull; <span style="color:{'#f59e0b' if pending_queue > 0 else '#94a3b8'};">Queue: {pending_queue} pending</span> &bull; <span>Alerts: {notif_summary}</span></div>
                </div>
                <div class="info-item">
                    <div class="info-label">Playback Activity</div>
                    <div class="info-value">🍿 {scrobble_stats['total']} Scrobble(s)</div>
                    <div style="font-size:12px;color:#94a3b8;">🎬 {scrobble_stats['movies']} &bull; 📺 {scrobble_stats['episodes']} &bull; ⭐ {scrobble_stats['ratings']} &bull; 📦 {scrobble_stats.get('collections', 0)} coll</div>
                </div>
            </div>
            {webhook_html_section}
        </div>

        {cowatch_card_html}

        {backup_card_html}

        <div class="card" style="padding: 0; overflow: hidden;">
            <div style="padding: 16px 20px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap;">
                <h3 style="margin: 0;">Live Activity & Scrobble History</h3>
                <div style="display:flex;align-items:center;gap:12px;">
                    {manual_scrobble_btn_html}
                    {f'<button onclick="retryQueue()" class="btn-sm" style="background:#d97706;color:#fff;font-weight:600;">🔄 Retry Queue ({pending_queue})</button>' if pending_queue > 0 else ''}
                    <label style="font-size:12px;color:#94a3b8;cursor:pointer;display:flex;align-items:center;gap:6px;">
                        <input type="checkbox" id="auto-refresh-toggle" checked onchange="toggleAutoRefresh(this)"> Auto-refresh (5s)
                    </label>
                    <button onclick="fetchEvents()" class="btn-sm">Refresh</button>
                    {clear_button_html}
                </div>
            </div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>Media Title</th>
                            <th>Type</th>
                            <th>Plex User</th>
                            <th>Action</th>
                            <th>Trakt Status</th>
                            {f'<th>Actions</th>' if is_admin else ''}
                        </tr>
                    </thead>
                    <tbody id="events-tbody">
                        {rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Admin Unlock Modal -->
    <div id="unlock-modal" onclick="if(event.target===this)closeUnlockModal()">
        <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:28px;max-width:400px;width:90%;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
            <h3 style="margin-top:0;font-size:18px;color:#f8fafc;display:flex;align-items:center;gap:8px;">
                <span>🔓</span> Unlock Admin Access
            </h3>
            <p style="color:#94a3b8;font-size:13px;margin:8px 0 16px;line-height:1.5;">
                Enter your <code>WEBHOOK_SECRET</code> from your <code>.env</code> file to reveal webhook URLs and unlock administration features.
            </p>
            <input type="password" id="admin-secret-input" placeholder="Enter webhook secret..."
                   style="width:100%;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:10px 12px;color:#f8fafc;font-size:14px;box-sizing:border-box;margin-bottom:12px;outline:none;"
                   onkeydown="if(event.key==='Enter')submitUnlock()" />
            <div id="unlock-error" style="color:#ef4444;font-size:12px;margin-bottom:12px;display:none;"></div>
            <div style="display:flex;justify-content:flex-end;gap:8px;">
                <button onclick="closeUnlockModal()" class="btn-sm" style="background:#334155;color:#cbd5e1;">Cancel</button>
                <button id="unlock-submit-btn" onclick="submitUnlock()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">Unlock</button>
            </div>
        </div>
    </div>

    <!-- Manual Scrobble Modal -->
    <div id="scrobble-modal" onclick="if(event.target===this)closeScrobbleModal()">
        <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:28px;max-width:560px;width:92%;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);max-height:85vh;display:flex;flex-direction:column;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h3 style="margin:0;font-size:18px;color:#f8fafc;display:flex;align-items:center;gap:8px;">
                    <span>🍿</span> Manual Scrobble to Trakt
                </h3>
                <button onclick="closeScrobbleModal()" style="background:none;border:none;color:#94a3b8;font-size:20px;cursor:pointer;">&times;</button>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin:0 0 16px;line-height:1.4;">
                Search Trakt's global database to quickly mark any movie or show as watched in your history.
            </p>
            <div style="display:flex;gap:8px;margin-bottom:16px;">
                <input type="text" id="scrobble-search-input" placeholder="Search title (e.g. Severance, Dune, The Bear)..."
                       style="flex:1;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:10px 12px;color:#f8fafc;font-size:14px;outline:none;"
                       onkeydown="if(event.key==='Enter')executeTraktSearch()" />
                <button onclick="executeTraktSearch()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:10px 16px;">Search</button>
            </div>
            <div id="scrobble-search-loading" style="display:none;color:#38bdf8;font-size:13px;text-align:center;padding:12px;">
                Searching Trakt catalog...
            </div>
            <div id="scrobble-search-results" style="overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:8px;max-height:360px;padding-right:4px;">
                <div style="color:#94a3b8;text-align:center;padding:24px;font-size:13px;">Type a title above and press Search</div>
            </div>
        </div>
    </div>

    <script>
        const isAdmin = {"true" if is_admin else "false"};
        let refreshTimer = null;

        function copyWebhookUrl() {{
            const input = document.getElementById('webhook-url-input');
            if (!input) return;
            navigator.clipboard.writeText(input.value).then(() => {{
                const btn = document.getElementById('copy-btn');
                const orig = btn.innerHTML;
                btn.innerHTML = '✓ Copied!';
                btn.style.background = '#10b981';
                setTimeout(() => {{
                    btn.innerHTML = orig;
                    btn.style.background = '#0284c7';
                }}, 2000);
            }}).catch(() => {{
                input.select();
                document.execCommand('copy');
            }});
        }}

        function openUnlockModal() {{
            const modal = document.getElementById('unlock-modal');
            modal.style.display = 'flex';
            const input = document.getElementById('admin-secret-input');
            input.value = '';
            document.getElementById('unlock-error').style.display = 'none';
            setTimeout(() => input.focus(), 50);
        }}

        function closeUnlockModal() {{
            document.getElementById('unlock-modal').style.display = 'none';
        }}

        async function submitUnlock() {{
            const secret = document.getElementById('admin-secret-input').value.trim();
            const errDiv = document.getElementById('unlock-error');
            const btn = document.getElementById('unlock-submit-btn');
            if (!secret) return;
            errDiv.style.display = 'none';
            if (btn) {{
                btn.disabled = true;
                btn.textContent = 'Unlocking...';
            }}
            try {{
                const res = await fetch('/api/admin/unlock', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ token: secret }})
                }});
                if (res.ok) {{
                    window.location.href = window.location.pathname;
                }} else {{
                    if (btn) {{
                        btn.disabled = false;
                        btn.textContent = 'Unlock';
                    }}
                    const data = await res.json();
                    errDiv.textContent = data.detail || 'Invalid secret token';
                    errDiv.style.display = 'block';
                }}
            }} catch (e) {{
                if (btn) {{
                    btn.disabled = false;
                    btn.textContent = 'Unlock';
                }}
                errDiv.textContent = 'Connection error: ' + e.message;
                errDiv.style.display = 'block';
            }}
        }}

        window.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') {{
                closeUnlockModal();
                closeScrobbleModal();
            }}
        }});

        async function lockAdmin() {{
            await fetch('/api/admin/lock', {{ method: 'POST' }});
            window.location.reload();
        }}

        function openManualScrobbleModal() {{
            if (!isAdmin) {{
                openUnlockModal();
                return;
            }}
            document.getElementById('scrobble-modal').style.display = 'flex';
            const input = document.getElementById('scrobble-search-input');
            input.value = '';
            document.getElementById('scrobble-search-results').innerHTML = '<div style="color:#94a3b8;text-align:center;padding:24px;font-size:13px;">Type a title above and press Search</div>';
            setTimeout(() => input.focus(), 50);
        }}

        function closeScrobbleModal() {{
            document.getElementById('scrobble-modal').style.display = 'none';
        }}

        async function executeTraktSearch() {{
            const query = document.getElementById('scrobble-search-input').value.trim();
            if (!query) return;
            const loading = document.getElementById('scrobble-search-loading');
            const resultsContainer = document.getElementById('scrobble-search-results');
            loading.style.display = 'block';
            resultsContainer.innerHTML = '';

            try {{
                const res = await fetch('/api/search?query=' + encodeURIComponent(query));
                loading.style.display = 'none';
                if (!res.ok) throw new Error('Search failed');
                const data = await res.json();
                renderSearchResults(data.results);
            }} catch (err) {{
                loading.style.display = 'none';
                resultsContainer.innerHTML = '<div style="color:#ef4444;text-align:center;padding:12px;">' + err.message + '</div>';
            }}
        }}

        function renderSearchResults(results) {{
            const container = document.getElementById('scrobble-search-results');
            if (!results || results.length === 0) {{
                container.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:24px;font-size:13px;">No results found on Trakt.</div>';
                return;
            }}
            let html = '';
            for (const item of results) {{
                const type = item.type || (item.movie ? 'movie' : 'show');
                const media = item.movie || item.show || item;
                const title = media.title;
                const year = media.year ? `(${{media.year}})` : '';
                const payloadData = {{
                    media_type: type,
                    title: media.title,
                    year: media.year,
                    ids: media.ids || {{}}
                }};
                const mediaJson = encodeURIComponent(JSON.stringify(payloadData));

                html += `
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px;display:flex;justify-content:space-between;align-items:center;gap:12px;">
                    <div>
                        <div style="display:flex;align-items:center;gap:8px;">
                            <span style="background:#334155;color:#93c5fd;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px;text-transform:uppercase;">${{type}}</span>
                            <span style="font-weight:600;color:#f8fafc;font-size:14px;">${{title}} ${{year}}</span>
                        </div>
                        ${{media.overview ? `<p style="color:#94a3b8;font-size:12px;margin:4px 0 0;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">${{media.overview}}</p>` : ''}}
                    </div>
                    <button onclick="submitManualScrobble('${{mediaJson}}', this)" class="btn-sm" style="background:#10b981;color:#fff;font-weight:600;white-space:nowrap;padding:8px 12px;">
                        ✓ Mark Watched
                    </button>
                </div>`;
            }}
            container.innerHTML = html;
        }}

        async function submitManualScrobble(mediaJsonEncoded, btn) {{
            try {{
                const payload = JSON.parse(decodeURIComponent(mediaJsonEncoded));
                btn.disabled = true;
                btn.innerHTML = 'Syncing...';
                const res = await fetch('/api/scrobble/manual', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                if (res.ok) {{
                    btn.innerHTML = '✓ Watched!';
                    btn.style.background = '#059669';
                    fetchEvents();
                    fetchPlayback();
                    setTimeout(() => closeScrobbleModal(), 1200);
                }} else {{
                    const err = await res.json();
                    btn.innerHTML = 'Error';
                    btn.style.background = '#ef4444';
                    alert('Failed to scrobble: ' + (err.detail || 'Unknown error'));
                }}
            }} catch (e) {{
                alert('Error: ' + e.message);
            }}
        }}

        async function fetchPlayback() {{
            try {{
                const res = await fetch('/api/playback');
                if (res.ok) {{
                    const data = await res.json();
                    renderPlayback(data);
                }}
            }} catch (e) {{
                console.error('Error fetching playback:', e);
            }}
        }}

        function renderPlayback(data) {{
            const card = document.getElementById('active-playback-card');
            if (!card) return;
            const sessions = data.active_sessions || [];
            if (sessions.length > 0) {{
                const s = sessions[0];
                card.style.display = 'block';
                card.style.borderLeftColor = s.state === 'playing' ? '#10b981' : '#f59e0b';
                document.getElementById('stream-state-badge').textContent = s.state === 'playing' ? 'Currently Streaming' : 'Paused';
                document.getElementById('stream-state-badge').style.color = s.state === 'playing' ? '#10b981' : '#f59e0b';
                const devStr = (isAdmin && s.player) ? (` on ${{s.player}}${{s.device ? ' (' + s.device + ')' : ''}}`) : '';
                document.getElementById('stream-user-device').textContent = `• ${{s.username}}${{devStr}}`;
                document.getElementById('stream-title').textContent = s.title;
                document.getElementById('stream-trakt-link').href = s.trakt_url || 'https://trakt.tv';
                document.getElementById('stream-progress-text').textContent = `${{s.progress.toFixed(1)}}%`;
                document.getElementById('stream-progress-bar').style.width = `${{s.progress}}%`;
                document.getElementById('stream-progress-bar').style.background = s.state === 'playing' ? '#10b981' : '#f59e0b';
            }} else if (data.recently_finished) {{
                const f = data.recently_finished;
                card.style.display = 'block';
                card.style.borderLeftColor = '#38bdf8';
                document.getElementById('stream-state-badge').textContent = 'Recently Finished';
                document.getElementById('stream-state-badge').style.color = '#38bdf8';
                const indicator = document.getElementById('stream-pulse-indicator');
                if (indicator) indicator.style.background = '#38bdf8';
                const fDevStr = (isAdmin && f.player) ? (` on ${{f.player}}`) : '';
                document.getElementById('stream-user-device').textContent = `• ${{f.username}}${{fDevStr}}`;
                document.getElementById('stream-title').textContent = f.title;
                document.getElementById('stream-trakt-link').href = f.trakt_url || 'https://trakt.tv';
                document.getElementById('stream-progress-text').textContent = '100.0%';
                document.getElementById('stream-progress-bar').style.width = '100%';
                document.getElementById('stream-progress-bar').style.background = '#38bdf8';
            }} else {{
                card.style.display = 'none';
            }}
        }}

        function renderRows(events) {{
            const tbody = document.getElementById('events-tbody');
            const colSpan = isAdmin ? 7 : 6;
            if (!events || events.length === 0) {{
                tbody.innerHTML = `<tr><td colspan="${{colSpan}}" style="text-align:center;padding:24px;color:#94a3b8;">No scrobble events received yet. Start playing media on Plex to test!</td></tr>`;
                return;
            }}
            let html = '';
            for (const ev of events) {{
                const color = (ev.result_status === 'ok' || ev.result_status === 200 || ev.result_status === 201) ? '#10b981' : '#f59e0b';
                let actionBtns = '';
                if (isAdmin) {{
                    if (ev.show_title) {{
                        const showEsc = ev.show_title.replace(/'/g, "\\'");
                        actionBtns += `<button onclick="quickAddShow('${{showEsc}}', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#1e293b;border:1px solid #334155;" title="Always co-watch this show">+ Co-Watch</button>`;
                    }}
                    if (ev.media_payload) {{
                        const mediaEnc = encodeURIComponent(JSON.stringify(ev.media_payload));
                        actionBtns += `<button onclick="quickSyncPartner('${{mediaEnc}}', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#701a75;color:#f5d0fe;margin-left:4px;" title="Sync to partner">+ Sync Partner</button>`;
                    }}
                }}
                const actionCol = isAdmin ? `<td style="padding:12px 16px;white-space:nowrap;display:flex;gap:4px;">${{actionBtns}}</td>` : '';
                html += `
                <tr style="border-bottom: 1px solid #334155;">
                    <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">${{ev.timestamp}}</td>
                    <td style="padding:12px 16px;color:#f8fafc;font-weight:500;">${{ev.title}}</td>
                    <td style="padding:12px 16px;"><span style="background:#0f172a;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">${{ev.type}}</span></td>
                    <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">${{ev.user}}</td>
                    <td style="padding:12px 16px;"><span style="background:#0f172a;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;">${{ev.action}} (${{ev.progress}})</span></td>
                    <td style="padding:12px 16px;"><span style="color:${{color}};font-weight:600;font-size:13px;">${{ev.result_status}}</span></td>
                    ${{actionCol}}
                </tr>`;
            }}
            tbody.innerHTML = html;
        }}

        async function fetchEvents() {{
            try {{
                const res = await fetch('/api/events');
                if (res.ok) {{
                    const data = await res.json();
                    renderRows(data.events);
                }}
            }} catch (e) {{
                console.error('Error fetching live events:', e);
            }}
            fetchPlayback();
        }}

        async function retryQueue() {{
            if (!isAdmin) {{
                openUnlockModal();
                return;
            }}
            try {{
                const res = await fetch('/api/queue/retry', {{ method: 'POST' }});
                if (res.ok) {{
                    window.location.reload();
                }}
            }} catch (e) {{
                console.error('Error retrying queue:', e);
            }}
        }}

        async function clearHistory() {{
            if (!isAdmin) {{
                openUnlockModal();
                return;
            }}
            if (confirm('Clear all recent activity logs?')) {{
                await fetch('/api/events/clear', {{ method: 'POST' }});
                fetchEvents();
            }}
        }}

        function toggleAutoRefresh(cb) {{
            if (cb.checked) {{
                refreshTimer = setInterval(fetchEvents, 5000);
            }} else {{
                if (refreshTimer) clearInterval(refreshTimer);
            }}
        }}

        async function addCowatchShow() {{
            if (!isAdmin) {{ openUnlockModal(); return; }}
            const input = document.getElementById('cowatch-show-input');
            const showName = input ? input.value.trim() : '';
            if (!showName) return;
            try {{
                const res = await fetch('/api/cowatch/shows', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ show: showName }})
                }});
                if (res.ok) {{
                    if (input) input.value = '';
                    window.location.reload();
                }} else {{
                    const err = await res.json();
                    alert('Failed to add show: ' + (err.detail || 'Error'));
                }}
            }} catch (e) {{
                alert('Error: ' + e.message);
            }}
        }}

        async function removeCowatchShow(showName) {{
            if (!isAdmin) {{ openUnlockModal(); return; }}
            if (!confirm(`Remove "${{showName}}" from shared shows?`)) return;
            try {{
                const res = await fetch('/api/cowatch/shows?show=' + encodeURIComponent(showName), {{
                    method: 'DELETE'
                }});
                if (res.ok) {{
                    window.location.reload();
                }} else {{
                    const err = await res.json();
                    alert('Failed to remove show: ' + (err.detail || 'Error'));
                }}
            }} catch (e) {{
                alert('Error: ' + e.message);
            }}
        }}

        async function quickAddShow(showName, btn) {{
            if (!isAdmin) {{ openUnlockModal(); return; }}
            btn.disabled = true;
            btn.textContent = 'Adding...';
            try {{
                const res = await fetch('/api/cowatch/shows', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ show: showName }})
                }});
                if (res.ok) {{
                    btn.textContent = '✓ Added';
                    btn.style.background = '#059669';
                }} else {{
                    btn.textContent = 'Error';
                }}
            }} catch (e) {{
                btn.textContent = 'Error';
            }}
        }}

        async function quickSyncPartner(payloadEnc, btn) {{
            if (!isAdmin) {{ openUnlockModal(); return; }}
            btn.disabled = true;
            btn.textContent = 'Syncing...';
            try {{
                const payload = JSON.parse(decodeURIComponent(payloadEnc));
                const res = await fetch('/api/cowatch/sync', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                if (res.ok) {{
                    btn.textContent = '✓ Synced';
                    btn.style.background = '#059669';
                }} else {{
                    const err = await res.json();
                    alert('Sync failed: ' + (err.detail || 'Error'));
                    btn.textContent = 'Error';
                }}
            }} catch (e) {{
                alert('Error: ' + e.message);
                btn.textContent = 'Error';
            }}
        }}

        function promptLinkAccount() {{
            if (!isAdmin) {{ openUnlockModal(); return; }}
            const uname = prompt('Enter Plex username to link a Trakt account for:');
            if (uname && uname.trim()) {{
                window.location.href = '/auth?user=' + encodeURIComponent(uname.trim());
            }}
        }}

        async function uploadBackup(input) {{
            if (!isAdmin) {{ openUnlockModal(); return; }}
            if (!input.files || !input.files[0]) return;
            const file = input.files[0];
            if (!confirm(`Restore system configuration and tokens from "${{file.name}}"? This will overwrite existing tokens and configuration.`)) {{
                input.value = '';
                return;
            }}
            const formData = new FormData();
            formData.append('backup_file', file);
            try {{
                const res = await fetch('/api/restore', {{
                    method: 'POST',
                    body: formData
                }});
                if (res.ok) {{
                    alert('✓ Backup successfully restored!');
                    window.location.reload();
                }} else {{
                    const err = await res.json();
                    alert('Restore failed: ' + (err.detail || 'Error'));
                }}
            }} catch (e) {{
                alert('Restore error: ' + e.message);
            }}
            input.value = '';
        }}

        refreshTimer = setInterval(fetchEvents, 5000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)



if __name__ == "__main__":
    if Config.DEBUG:
        uvicorn.run("main:app", host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=True, reload_excludes=["*.json", "data/*"])
    else:
        uvicorn.run("main:app", host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=False)


