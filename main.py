import datetime
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn


from config import Config
from plex_parser import ParsedMedia, parse_plex_webhook
from trakt_client import TraktClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("plex_trakt_scrobbler")

trakt = TraktClient(Config)

SERVER_START_TIME = time.time()

# In-memory scrobble counter
scrobble_stats: dict[str, int] = {
    "total": 0,
    "movies": 0,
    "episodes": 0,
    "ratings": 0,
}


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
    or the admin_token HTTP-only cookie.
    """
    if not Config.WEBHOOK_SECRET:
        return True

    # 1. Query parameter (?token=...)
    token = request.query_params.get("token")
    if token and token == Config.WEBHOOK_SECRET:
        return True

    # 2. Request header (x-webhook-secret: ...)
    header_token = request.headers.get("x-webhook-secret")
    if header_token and header_token == Config.WEBHOOK_SECRET:
        return True

    # 3. Secure cookie (admin_token=...)
    cookie_token = request.cookies.get("admin_token")
    if cookie_token and cookie_token == Config.WEBHOOK_SECRET:
        return True

    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await trakt.close()


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
        "progress": progress_str,
        "result_status": result.get("status") or ("ok" if not result.get("error") else "error"),
        "raw_result": result,
    }
    recent_events.appendleft(entry)


@app.post("/webhook")
async def plex_webhook(request: Request):
    """Receives multipart/form-data or json webhook notifications from Plex Media Server."""
    if Config.WEBHOOK_SECRET:
        token = request.query_params.get("token") or request.headers.get("x-webhook-secret")
        if not token or token != Config.WEBHOOK_SECRET:
            logger.warning("Rejected unauthorized webhook request: invalid or missing token.")
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing webhook token")

    raw_data: Optional[dict[str, Any]] = None

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            raw_data = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse raw JSON body: {e}")
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
            raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    if not raw_data:
        raise HTTPException(status_code=400, detail="No payload found in request")

    parsed = parse_plex_webhook(raw_data, Config.PLEX_ALLOWED_USERS)
    if not parsed:
        return {"status": "ignored", "reason": "Non-media event, filtered user, or unsupported media type"}

    if not trakt.is_authenticated():
        logger.warning("Trakt is not authenticated! Run 'python auth.py' or visit /auth to authorize.")
        return {"status": "error", "message": "Trakt not authenticated"}

    event = parsed.event
    scrobble_payload = parsed.to_trakt_scrobble_payload()
    result: dict[str, Any] = {}
    action_taken = "none"

    try:
        if event == "media.scrobble":
            # Plex determined the user watched the show/movie (>90%)
            action_taken = "mark_watched"
            logger.info(f"Marking as watched in Trakt: {parsed.title} for user {parsed.username}")

            # 1. Stop scrobble with 100% progress
            scrobble_payload["progress"] = 100.0
            scrobble_res = await trakt.scrobble_stop(scrobble_payload)

            # 2. Also sync to history to guarantee item is marked as viewed
            history_res = await trakt.sync_history(parsed.to_trakt_history_payload())
            result = {"scrobble": scrobble_res, "history": history_res}
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
            result = await trakt.sync_ratings(rating_payload)
            scrobble_stats["ratings"] += 1

        elif Config.SCROBBLE_MODE == "scrobble":
            # Real-time scrobbling on play, pause, resume, stop
            if event in ("media.play", "media.resume"):
                action_taken = "scrobble_start"
                logger.info(f"Scrobble start: {parsed.title} ({parsed.progress:.1f}%)")
                result = await trakt.scrobble_start(scrobble_payload)
            elif event == "media.pause":
                if parsed.progress >= Config.SCROBBLE_THRESHOLD:
                    action_taken = "scrobble_stop"
                    logger.info(f"Scrobble stop (paused past threshold): {parsed.title} ({parsed.progress:.1f}%)")
                    result = await trakt.scrobble_stop(scrobble_payload)
                    scrobble_stats["total"] += 1
                    if parsed.media_type == "movie":
                        scrobble_stats["movies"] += 1
                    elif parsed.media_type == "episode":
                        scrobble_stats["episodes"] += 1
                else:
                    action_taken = "scrobble_pause"
                    logger.info(f"Scrobble pause: {parsed.title} ({parsed.progress:.1f}%)")
                    result = await trakt.scrobble_pause(scrobble_payload)
            elif event == "media.stop":
                action_taken = "scrobble_stop"
                logger.info(f"Scrobble stop: {parsed.title} ({parsed.progress:.1f}%)")
                result = await trakt.scrobble_stop(scrobble_payload)
                scrobble_stats["total"] += 1
                if parsed.media_type == "movie":
                    scrobble_stats["movies"] += 1
                elif parsed.media_type == "episode":
                    scrobble_stats["episodes"] += 1
        else:
            action_taken = f"skipped_{event}"


        log_event(parsed, action_taken, result)
        return {"status": "success", "event": event, "action": action_taken, "result": result}

    except Exception as e:
        logger.error(f"Error executing Trakt action for {parsed.title}: {e}", exc_info=True)
        log_event(parsed, action_taken, {"error": str(e)})
        return {"status": "error", "error": str(e)}


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
        "scrobble_mode": Config.SCROBBLE_MODE,
        "webhook_secret_enabled": bool(Config.WEBHOOK_SECRET),
        "uptime": get_uptime_str(),
        "token_health": token_info,
        "stats": scrobble_stats,
    }


@app.get("/api/events")
def get_events(request: Request):
    is_admin = is_admin_request(request)
    events = list(recent_events)
    if not is_admin:
        events = [{**ev, "user": mask_username(ev.get("user"))} for ev in events]
    return {"events": events}


@app.post("/api/events/clear")
def clear_events(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    recent_events.clear()
    return {"status": "cleared"}


class AdminUnlockRequest(BaseModel):
    token: str


@app.post("/api/admin/unlock")
def admin_unlock(payload: AdminUnlockRequest, response: Response):
    if not Config.WEBHOOK_SECRET:
        return {"status": "ok", "message": "Admin authentication not required"}
    if payload.token != Config.WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin secret")
    response.set_cookie(
        key="admin_token",
        value=Config.WEBHOOK_SECRET,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
    )
    return {"status": "ok", "message": "Admin mode unlocked"}


@app.post("/api/admin/lock")
def admin_lock(response: Response):
    response.delete_cookie(key="admin_token")
    return {"status": "ok", "message": "Admin mode locked"}


class DevicePollRequest(BaseModel):
    device_code: str


@app.post("/api/auth/start")
async def auth_start(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    try:
        data = await trakt.generate_device_code()
        return data
    except Exception as e:
        logger.error(f"Failed to generate device code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/poll")
async def auth_poll(payload: DevicePollRequest, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    global trakt_user_profile
    try:
        res = await trakt.poll_for_token(payload.device_code)
        if "access_token" in res:
            trakt_user_profile = None  # Invalidate cached profile on new login
            return {"status": "success"}
        elif res.get("status") in ("pending", "slow_down"):
            return res
        return {"status": "pending"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request):
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
    profile = await get_cached_trakt_profile()
    username = profile.get("username") if profile else ""
    already_connected = trakt.is_authenticated()
    already_connected_banner = (
        f'<div style="background:#064e3b;border:1px solid #059669;color:#6ee7b7;padding:12px;border-radius:8px;margin-bottom:20px;font-size:14px;">'
        f'Currently linked to <strong>@{username}</strong>. You can authorize again below to reconnect or switch accounts.</div>'
        if already_connected and username
        else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Link Trakt Account</title>
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
        <h1>Link Trakt Account</h1>
        <p>Authorize this scrobbler to record playback and sync history with your Trakt profile.</p>
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
        let pollInterval = null;
        async function initAuth() {{
            try {{
                const res = await fetch('/api/auth/start', {{ method: 'POST' }});
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
                            body: JSON.stringify({{ device_code: data.device_code }})
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
    if query_token and Config.WEBHOOK_SECRET and query_token == Config.WEBHOOK_SECRET:
        response.set_cookie(
            key="admin_token",
            value=Config.WEBHOOK_SECRET,
            httponly=True,
            samesite="lax",
            max_age=86400 * 30,
        )

    is_admin = is_admin_request(request)
    auth_status = trakt.is_authenticated()
    profile = await get_cached_trakt_profile() if auth_status else None
    raw_username = profile.get("username") if profile else None

    display_username = raw_username if is_admin else mask_username(raw_username)

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
    if not recent_events:
        rows = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#94a3b8;">No scrobble events received yet. Start playing media on Plex to test!</td></tr>'
    else:
        for ev in recent_events:
            color = "#10b981" if ev["result_status"] in ("ok", 200, 201) else "#f59e0b"
            u = ev["user"] if is_admin else mask_username(ev["user"])
            rows += f"""
            <tr style="border-bottom: 1px solid #334155;">
                <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">{ev['timestamp']}</td>
                <td style="padding:12px 16px;color:#f8fafc;font-weight:500;">{ev['title']}</td>
                <td style="padding:12px 16px;"><span style="background:#0f172a;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">{ev['type']}</span></td>
                <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">{u}</td>
                <td style="padding:12px 16px;"><span style="background:#0f172a;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;">{ev['action']} ({ev['progress']})</span></td>
                <td style="padding:12px 16px;"><span style="color:{color};font-weight:600;font-size:13px;">{ev['result_status']}</span></td>
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
        #unlock-modal {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.75); z-index: 9999; align-items: center; justify-content: center; backdrop-filter: blur(2px); }}
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

        <div class="card">
            <h3>Server & Account Configuration</h3>
            <div class="info-grid">
                <div class="info-item">
                    <div class="info-label">Trakt Connection</div>
                    <div class="info-value">{'@' + display_username if display_username else ('Connected' if auth_status else 'Not Connected')}</div>
                    <div><span style="color:{token_health_color};font-size:12px;">● {token_health_str}</span></div>
                </div>
                <div class="info-item">
                    <div class="info-label">Allowed Plex Users</div>
                    <div class="info-value">{allowed_users_display}</div>
                    <div style="font-size:12px;color:#94a3b8;">{'Filter active' if Config.PLEX_ALLOWED_USERS else 'All users permitted'}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Server Health</div>
                    <div class="info-value">🟢 Online</div>
                    <div style="font-size:12px;color:#94a3b8;">Uptime: {get_uptime_str()}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Playback Activity</div>
                    <div class="info-value">🍿 {scrobble_stats['total']} Scrobble(s)</div>
                    <div style="font-size:12px;color:#94a3b8;">🎬 {scrobble_stats['movies']} &bull; 📺 {scrobble_stats['episodes']} &bull; ⭐ {scrobble_stats['ratings']} ratings</div>
                </div>
            </div>
            {webhook_html_section}
        </div>

        <div class="card" style="padding: 0; overflow: hidden;">
            <div style="padding: 16px 20px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center;">
                <h3 style="margin: 0;">Live Activity & Scrobble History</h3>
                <div style="display:flex;align-items:center;gap:12px;">
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
    <div id="unlock-modal">
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
                <button onclick="submitUnlock()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">Unlock</button>
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
            if (!secret) return;
            errDiv.style.display = 'none';
            try {{
                const res = await fetch('/api/admin/unlock', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ token: secret }})
                }});
                if (res.ok) {{
                    window.location.reload();
                }} else {{
                    const data = await res.json();
                    errDiv.textContent = data.detail || 'Invalid secret token';
                    errDiv.style.display = 'block';
                }}
            }} catch (e) {{
                errDiv.textContent = 'Connection error: ' + e.message;
                errDiv.style.display = 'block';
            }}
        }}

        async function lockAdmin() {{
            await fetch('/api/admin/lock', {{ method: 'POST' }});
            window.location.reload();
        }}

        function renderRows(events) {{
            const tbody = document.getElementById('events-tbody');
            if (!events || events.length === 0) {{
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#94a3b8;">No scrobble events received yet. Start playing media on Plex to test!</td></tr>';
                return;
            }}
            let html = '';
            for (const ev of events) {{
                const color = (ev.result_status === 'ok' || ev.result_status === 200 || ev.result_status === 201) ? '#10b981' : '#f59e0b';
                html += `
                <tr style="border-bottom: 1px solid #334155;">
                    <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">${{ev.timestamp}}</td>
                    <td style="padding:12px 16px;color:#f8fafc;font-weight:500;">${{ev.title}}</td>
                    <td style="padding:12px 16px;"><span style="background:#0f172a;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">${{ev.type}}</span></td>
                    <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">${{ev.user}}</td>
                    <td style="padding:12px 16px;"><span style="background:#0f172a;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;">${{ev.action}} (${{ev.progress}})</span></td>
                    <td style="padding:12px 16px;"><span style="color:${{color}};font-weight:600;font-size:13px;">${{ev.result_status}}</span></td>
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


