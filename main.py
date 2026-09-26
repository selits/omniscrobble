import datetime
import json
import logging
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await trakt.close()


app = FastAPI(title="Plex Trakt Scrobbler", version="1.0.0", lifespan=lifespan)


# In-memory log of recent webhook events for the status dashboard
MAX_HISTORY = 30
recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)


def log_event(media: ParsedMedia, action: str, result: dict[str, Any]):
    title_str = (
        f"{media.show_title} S{media.season:02d}E{media.episode:02d} - {media.title}"
        if media.media_type == "episode"
        else f"{media.title} ({media.year or 'N/A'})"
    )
    entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": media.username,
        "event": media.event,
        "action": action,
        "title": title_str,
        "type": media.media_type,
        "progress": f"{media.progress:.1f}%",
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
                else:
                    action_taken = "scrobble_pause"
                    logger.info(f"Scrobble pause: {parsed.title} ({parsed.progress:.1f}%)")
                    result = await trakt.scrobble_pause(scrobble_payload)
            elif event == "media.stop":
                action_taken = "scrobble_stop"
                logger.info(f"Scrobble stop: {parsed.title} ({parsed.progress:.1f}%)")
                result = await trakt.scrobble_stop(scrobble_payload)
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
    return {
        "status": "healthy",
        "authenticated": trakt.is_authenticated(),
        "trakt_user": profile.get("username") if profile else None,
        "allowed_users": Config.PLEX_ALLOWED_USERS or "all",
        "scrobble_mode": Config.SCROBBLE_MODE,
        "webhook_secret_enabled": bool(Config.WEBHOOK_SECRET),
    }


@app.get("/api/events")
def get_events():
    return {"events": list(recent_events)}


@app.post("/api/events/clear")
def clear_events():
    recent_events.clear()
    return {"status": "cleared"}


class DevicePollRequest(BaseModel):
    device_code: str


@app.post("/api/auth/start")
async def auth_start():
    try:
        data = await trakt.generate_device_code()
        return data
    except Exception as e:
        logger.error(f"Failed to generate device code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/poll")
async def auth_poll(payload: DevicePollRequest):
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
async def auth_page():
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
async def dashboard():
    auth_status = trakt.is_authenticated()
    profile = await get_cached_trakt_profile() if auth_status else None
    username = profile.get("username") if profile else None

    if auth_status:
        user_label = f"Connected as @{username}" if username else "Connected"
        status_badge = f'<a href="/auth" style="background:#10b981;color:#fff;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;">{user_label} &bull; Manage</a>'
    else:
        status_badge = '<a href="/auth" style="background:#ef4444;color:#fff;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;text-decoration:none;">Not Connected &bull; Link Trakt &rarr;</a>'

    webhook_path = f"/webhook?token={Config.WEBHOOK_SECRET}" if Config.WEBHOOK_SECRET else "/webhook"

    rows = ""
    if not recent_events:
        rows = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#94a3b8;">No scrobble events received yet. Start playing media on Plex to test!</td></tr>'
    else:
        for ev in recent_events:
            color = "#10b981" if ev["result_status"] in ("ok", 200, 201) else "#f59e0b"
            rows += f"""
            <tr style="border-bottom: 1px solid #334155;">
                <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">{ev['timestamp']}</td>
                <td style="padding:12px 16px;color:#f8fafc;font-weight:500;">{ev['title']}</td>
                <td style="padding:12px 16px;"><span style="background:#1e293b;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">{ev['type']}</span></td>
                <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">{ev['user']}</td>
                <td style="padding:12px 16px;"><span style="background:#1e293b;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;">{ev['action']} ({ev['progress']})</span></td>
                <td style="padding:12px 16px;"><span style="color:{color};font-weight:600;font-size:13px;">{ev['result_status']}</span></td>
            </tr>
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
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 20px; border-bottom: 1px solid #1e293b; }}
        .title {{ font-size: 24px; font-weight: 700; color: #f8fafc; display: flex; align-items: center; gap: 12px; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 24px; border: 1px solid #334155; }}
        .card h3 {{ font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #e2e8f0; }}
        .info-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }}
        .info-item {{ background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #334155; }}
        .info-label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
        .info-value {{ font-size: 15px; font-weight: 600; margin-top: 4px; color: #f1f5f9; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        th {{ background: #0f172a; padding: 12px 16px; font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.05em; border-bottom: 1px solid #334155; }}
        .webhook-box {{ background: #0f172a; border: 1px dashed #64748b; border-radius: 8px; padding: 14px; font-family: monospace; color: #38bdf8; word-break: break-all; margin-top: 8px; }}
        .btn-sm {{ background: #334155; border: none; color: #e2e8f0; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; transition: background 0.15s; }}
        .btn-sm:hover {{ background: #475569; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#ed1c24" stroke-width="2"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8" fill="#ed1c24"/></svg>
                Plex &rarr; Trakt Scrobbler
            </div>
            <div>{status_badge}</div>
        </div>

        <div class="card">
            <h3>Server & Account Configuration</h3>
            <div class="info-grid">
                <div class="info-item">
                    <div class="info-label">Trakt Connection</div>
                    <div class="info-value">{'@' + username if username else ('Connected' if auth_status else 'Not Connected')}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Allowed Plex Users</div>
                    <div class="info-value">{", ".join(Config.PLEX_ALLOWED_USERS) if Config.PLEX_ALLOWED_USERS else 'All Users'}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Scrobble Mode</div>
                    <div class="info-value">{Config.SCROBBLE_MODE.capitalize()}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Scrobble Threshold</div>
                    <div class="info-value">{Config.SCROBBLE_THRESHOLD}%</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Webhook Secret</div>
                    <div class="info-value">{'Enabled (Secured)' if Config.WEBHOOK_SECRET else 'Disabled (Open)'}</div>
                </div>
            </div>
            <div style="margin-top: 18px;">
                <div class="info-label">Plex Webhook URL</div>
                <div class="webhook-box">http://&lt;YOUR_SERVER_IP&gt;:{Config.SERVER_PORT}{webhook_path}</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 6px;">Add this URL in Plex Web: <strong>Settings &rarr; Webhooks &rarr; Add Webhook</strong>.</div>
            </div>
        </div>

        <div class="card" style="padding: 0; overflow: hidden;">
            <div style="padding: 16px 20px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center;">
                <h3 style="margin: 0;">Live Activity & Scrobble History</h3>
                <div style="display:flex;align-items:center;gap:12px;">
                    <label style="font-size:12px;color:#94a3b8;cursor:pointer;display:flex;align-items:center;gap:6px;">
                        <input type="checkbox" id="auto-refresh-toggle" checked onchange="toggleAutoRefresh(this)"> Auto-refresh (5s)
                    </label>
                    <button onclick="fetchEvents()" class="btn-sm">Refresh</button>
                    <button onclick="clearHistory()" class="btn-sm" style="color:#f87171;">Clear</button>
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

    <script>
        let refreshTimer = null;

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
                    <td style="padding:12px 16px;"><span style="background:#1e293b;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">${{ev.type}}</span></td>
                    <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">${{ev.user}}</td>
                    <td style="padding:12px 16px;"><span style="background:#1e293b;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;">${{ev.action}} (${{ev.progress}})</span></td>
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


