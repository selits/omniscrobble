import sys
import app.main

# Expose app.main as the 'main' module for backwards compatibility
sys.modules["main"] = app.main

from app.config import Config
from app.main import (
    app,
    cowatch_mgr,
    get_uptime_str,
    is_admin_request,
    is_temporary_error,
    lifespan,
    log_event,
    mask_username,
    notifier,
    playback_mgr,
    queue_mgr,
    recent_events,
    scrobble_stats,
    sonarr,
    trakt,
    user_mgr,
)

__all__ = [
    "app",
    "Config",
    "cowatch_mgr",
    "get_uptime_str",
    "is_admin_request",
    "is_temporary_error",
    "lifespan",
    "log_event",
    "mask_username",
    "notifier",
    "playback_mgr",
    "queue_mgr",
    "recent_events",
    "scrobble_stats",
    "sonarr",
    "trakt",
    "user_mgr",
]

if __name__ == "__main__":
    import uvicorn

    if Config.DEBUG:
        uvicorn.run("app.main:app", host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=True, reload_excludes=["*.json", "data/*"])
    else:
        uvicorn.run("app.main:app", host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=False)
