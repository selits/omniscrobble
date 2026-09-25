import datetime
import json
import logging
from collections import deque
from typing import Any, Optional

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
import uvicorn

from config import Config
from plex_parser import ParsedMedia, parse_plex_webhook
from trakt_client import TraktClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("plex_trakt_scrobbler")

app = FastAPI(title="Plex Trakt Scrobbler", version="1.0.0")
trakt = TraktClient(Config)

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
async def plex_webhook(request: Request, payload: Optional[str] = Form(None)):
    """Receives multipart/form-data webhook notifications from Plex Media Server."""
    raw_data: Optional[dict[str, Any]] = None

    # Plex sends multipart/form-data with a "payload" field containing JSON string
    if payload:
        try:
            raw_data = json.loads(payload)
        except Exception as e:
            logger.error(f"Failed to parse form field 'payload' as JSON: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON in payload form field")
    else:
        # Fallback in case raw JSON body is posted directly
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                raw_data = await request.json()
            except Exception as e:
                logger.error(f"Failed to parse raw JSON body: {e}")
                raise HTTPException(status_code=400, detail="Invalid JSON body")
        else:
            # Check form data directly
            try:
                form = await request.form()
                payload_str = form.get("payload")
                if payload_str and isinstance(payload_str, str):
                    raw_data = json.loads(payload_str)
            except Exception as e:
                logger.error(f"Failed to parse multipart form data: {e}")

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
            scrobble_res = trakt.scrobble_stop(scrobble_payload)

            # 2. Also sync to history to guarantee item is marked as viewed
            history_res = trakt.sync_history(parsed.to_trakt_history_payload())
            result = {"scrobble": scrobble_res, "history": history_res}

        elif Config.SCROBBLE_MODE == "scrobble":
            # Real-time scrobbling on play, pause, resume, stop
            if event in ("media.play", "media.resume"):
                action_taken = "scrobble_start"
                logger.info(f"Scrobble start: {parsed.title} ({parsed.progress:.1f}%)")
                result = trakt.scrobble_start(scrobble_payload)
            elif event == "media.pause":
                action_taken = "scrobble_pause"
                logger.info(f"Scrobble pause: {parsed.title} ({parsed.progress:.1f}%)")
                result = trakt.scrobble_pause(scrobble_payload)
            elif event == "media.stop":
                action_taken = "scrobble_stop"
                logger.info(f"Scrobble stop: {parsed.title} ({parsed.progress:.1f}%)")
                result = trakt.scrobble_stop(scrobble_payload)
        else:
            action_taken = f"skipped_{event}"

        log_event(parsed, action_taken, result)
        return {"status": "success", "event": event, "action": action_taken, "result": result}

    except Exception as e:
        logger.error(f"Error executing Trakt action for {parsed.title}: {e}", exc_info=True)
        log_event(parsed, action_taken, {"error": str(e)})
        return {"status": "error", "error": str(e)}


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "authenticated": trakt.is_authenticated(),
        "allowed_users": Config.PLEX_ALLOWED_USERS or "all",
        "scrobble_mode": Config.SCROBBLE_MODE,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    auth_status = trakt.is_authenticated()
    status_badge = (
        '<span style="background:#10b981;color:#fff;padding:4px 10px;border-radius:9999px;font-size:12px;font-weight:600;">Connected</span>'
        if auth_status
        else '<span style="background:#ef4444;color:#fff;padding:4px 10px;border-radius:9999px;font-size:12px;font-weight:600;">Not Connected (Run `python auth.py`)</span>'
    )

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

    html = f"""
    <!DOCTYPE html>
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
                        <div class="info-value">{'Active (Authenticated)' if auth_status else 'Not Connected'}</div>
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
                </div>
                <div style="margin-top: 18px;">
                    <div class="info-label">Plex Webhook URL</div>
                    <div class="webhook-box">http://&lt;YOUR_SERVER_IP&gt;:{Config.SERVER_PORT}/webhook</div>
                    <div style="font-size: 12px; color: #94a3b8; margin-top: 6px;">Add this URL in Plex Web: <strong>Settings &rarr; Webhooks &rarr; Add Webhook</strong>.</div>
                </div>
            </div>

            <div class="card" style="padding: 0; overflow: hidden;">
                <div style="padding: 16px 20px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center;">
                    <h3 style="margin: 0;">Live Activity & Scrobble History</h3>
                    <button onclick="location.reload()" style="background:#334155;border:none;color:#e2e8f0;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:12px;">Refresh</button>
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
                        <tbody>
                            {rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


if __name__ == "__main__":
    uvicorn.run("main:app", host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=True)
