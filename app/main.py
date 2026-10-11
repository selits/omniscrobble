import asyncio
import csv
import datetime
from datetime import timezone
import html
import io
import json
import logging
import os
import secrets
import stat
import sys
import time
import urllib.parse
import zipfile
from collections import deque
from contextlib import asynccontextmanager, nullcontext
from typing import Any, Literal, Optional

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
from app.services.activity_delivery import ActivityDeliveryManager
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
from app.services.result_normalizer import normalize_result
from app.services.dashboard_auth_manager import DashboardAuthManager, DashboardPrincipal
from app.services.automation_rules import AutomationRulesManager, AutomationRuleError
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
from app.services.watch_list_manager import WatchListManager, WatchListError
from app.services.multi_tracker import MultiTrackerManager
from app.services.loop_prevention import loop_prevention
from app.services.reverse_sync_manager import reverse_sync_mgr
from app.services.arr_bridge import arr_bridge
from app.services.cross_tracker_sync import CrossTrackerSyncManager
from app.services.settings_manager import settings_mgr
log_mgr.reconfigure_archive(**{
    "max_bytes": settings_mgr.get_log_archive_settings()["max_bytes"],
    "backup_count": settings_mgr.get_log_archive_settings()["backup_count"],
})
from app.services.compatibility_manager import CompatibilityManager
from app.services.cloud_sync_manager import cloud_sync_mgr
from app.services.webhook_debugger import webhook_debugger
from app.services.analytics_manager import analytics_mgr
from app.services.dashboard_renderer import (
    format_action_label,
    should_display_cowatch_badge,
    render_status_badge,
    render_tracker_delivery_badges,
    dashboard_renderer,
)
from app.routes.recovery import RecoveryConnectionTestRequest, router as recovery_router
from app.routes.watch_lists import create_router as create_watch_lists_router
from app.routes.accounts import DashboardAccountHandlers, create_router as create_accounts_router
from app.routes.reconciliation import (
    CrossSyncExecuteRequest, CrossTrackerSyncHandlers,
    MediaServerTestConnectionRequest, PlexTestConnectionRequest, RegisterWebhookRequest,
    ReconcileRequest, ReconcileSettingsRequest,
    ReconciliationSettingsHandlers, ReconciliationWorkflowHandlers, MediaServerWebhookHandlers,
    create_router as create_reconciliation_router,
)
from app.routes.acquisition import (
    AcquisitionHandlers, ArrAddRequest, ArrTestConnectionRequest,
    create_router as create_acquisition_router,
)
from app.routes.integrations import (
    DevicePollRequest, SimklPollRequest, TokenSubmitRequest, TrackerAccountHandlers,
    TrackerDiagnosticsHandlers, create_router as create_integrations_router,
)
from pathlib import Path
from pathlib import PurePosixPath

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'
COMPONENTS_DIR = TEMPLATES_DIR / 'dashboard'
AUTH_LOCKED_HTML = (TEMPLATES_DIR / 'auth_locked.html').read_text(encoding='utf-8')
AUTH_HTML = (TEMPLATES_DIR / 'auth.html').read_text(encoding='utf-8')
AUTH_SIMKL_HTML = (TEMPLATES_DIR / 'auth_simkl.html').read_text(encoding='utf-8')
AUTH_ANILIST_HTML = (TEMPLATES_DIR / 'auth_anilist.html').read_text(encoding='utf-8')
AUTH_MAL_HTML = (TEMPLATES_DIR / 'auth_mal.html').read_text(encoding='utf-8')
def load_dashboard_template() -> str:
    """Assemble dashboard HTML from its shell and reusable template components."""
    rendered = (TEMPLATES_DIR / 'dashboard.html').read_text(encoding='utf-8')
    marker_start = '<!-- COMPONENT:'
    while marker_start in rendered:
        start = rendered.index(marker_start)
        end = rendered.index('-->', start) + 3
        component_name = rendered[start + len(marker_start):end - 3].strip()
        component_path = COMPONENTS_DIR / f'{component_name}.html'
        if not component_path.is_file():
            raise FileNotFoundError(f'Dashboard template component not found: {component_path}')
        rendered = rendered[:start] + component_path.read_text(encoding='utf-8') + rendered[end:]
    return rendered


DASHBOARD_HTML = load_dashboard_template()
STYLESHEET_PATH = Path(__file__).resolve().parent / 'static' / 'dashboard.css'
THEME_STYLESHEET_PATH = Path(__file__).resolve().parent / 'static' / 'dashboard-theme.css'
SCRIPT_PATH = Path(__file__).resolve().parent / 'static' / 'dashboard.js'
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
watch_list_mgr = WatchListManager(Config.BASE_DIR / "data" / "watch_lists.json")
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
cross_tracker_sync = CrossTrackerSyncManager(
    trakt_client=trakt,
    simkl_client=simkl,
    tmdb_client=tmdb,
    provenance_file=Config.BASE_DIR / "data" / "cross_sync_provenance.json",
)
user_mgr.set_default_client(trakt)
reverse_sync_mgr.set_trakt_client(trakt)
arr_bridge.set_trakt_client(trakt)
queue_mgr = QueueManager(Config.QUEUE_DB_FILE)
dashboard_auth_mgr = DashboardAuthManager()
automation_rules_mgr = AutomationRulesManager(Config.BASE_DIR / "data" / "automation_rules.json")
compatibility_mgr = CompatibilityManager()

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
    return normalize_result(res).retryable


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
    session_token = request.cookies.get("dashboard_session", "")
    local_principal = dashboard_auth_mgr.resolve_session(session_token)
    if local_principal and local_principal.role == "admin":
        if request.method in ("POST", "DELETE", "PUT", "PATCH"):
            csrf_cookie = request.cookies.get("csrf_token", "")
            csrf_header = request.headers.get("x-csrf-token", "")
            if request.headers.get("sec-fetch-site") == "cross-site" or not dashboard_auth_mgr.session_has_csrf(session_token, csrf_cookie) or not secrets.compare_digest(csrf_cookie, csrf_header):
                return False
        return True

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


def get_dashboard_principal(request: Request) -> Optional[DashboardPrincipal]:
    """Resolve the current dashboard identity, preserving legacy admin access."""
    if not Config.WEBHOOK_SECRET:
        return DashboardPrincipal("admin", "admin", "legacy_open_mode")
    session_principal = dashboard_auth_mgr.resolve_session(request.cookies.get("dashboard_session", ""))
    if session_principal and session_principal.role == "admin":
        return session_principal
    if is_admin_request(request):
        return DashboardPrincipal("admin", "admin", "shared_secret")
    return session_principal


def require_dashboard_permission(request: Request, permission: str, profile_owner: Optional[str] = None) -> DashboardPrincipal:
    principal = get_dashboard_principal(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    if principal.role == "admin":
        if principal.source == "local_account" and request.method in ("POST", "DELETE", "PUT", "PATCH") and not is_admin_request(request):
            raise HTTPException(status_code=403, detail="CSRF validation failed.")
        return principal
    if not dashboard_auth_mgr.has_permission(principal.role, permission):
        raise HTTPException(status_code=403, detail="Your dashboard role does not allow this action.")
    if profile_owner is not None and profile_owner.strip().lower() != principal.username.lower():
        raise HTTPException(status_code=403, detail="You can only access your own profile.")
    if request.method in ("POST", "DELETE", "PUT", "PATCH"):
        session_token = request.cookies.get("dashboard_session", "")
        csrf_cookie = request.cookies.get("csrf_token", "")
        csrf_header = request.headers.get("x-csrf-token", "")
        if request.headers.get("sec-fetch-site") == "cross-site" or not dashboard_auth_mgr.session_has_csrf(session_token, csrf_cookie) or not secrets.compare_digest(csrf_cookie, csrf_header):
            raise HTTPException(status_code=403, detail="CSRF validation failed.")
    return principal


def profile_for_request(request: Request, requested_username: Optional[str]) -> Optional[str]:
    principal = get_dashboard_principal(request)
    requested = requested_username.strip() if requested_username else None
    if principal and principal.role == "member":
        if requested and requested.lower() != principal.username.lower():
            raise HTTPException(status_code=403, detail="You can only access your own profile.")
        return principal.username
    if not principal and requested:
        raise HTTPException(status_code=401, detail="Authentication is required to select a profile.")
    return requested


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

            if not queue_mgr._future_schema_version and queue_mgr.get_pending_count() > 0:
                logger.info("Background queue worker draining pending offline items...")
                await process_queue(trakt, queue_mgr, user_mgr=user_mgr, on_result=record_queue_delivery, simkl_client=simkl)
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


APP_VERSION = "3.5.0"
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
        return {"status": "skipped", "reason": "Simkl is disabled or not configured."}
    responses = []
    try:
        if event in ("media.play", "media.resume"):
            responses.append(await simkl.scrobble_start(parsed, progress=parsed.progress))
        elif event == "media.pause":
            responses.append(await simkl.scrobble_pause(parsed, progress=parsed.progress))
        elif event == "media.scrobble" or (action_taken == "scrobble_stop" and parsed.progress >= Config.get_threshold(parsed.media_type)):
            res = await simkl.scrobble_stop(parsed, progress=parsed.progress)
            responses.append(res)
            watched_at_ts = datetime.datetime.now(timezone.utc).isoformat()
            responses.append(await simkl.sync_history(parsed, watched_at=watched_at_ts))
            if isinstance(res, dict) and res.get("status") == "error":
                asyncio.create_task(notifier.send_failure_alert(parsed, "Simkl", str(res.get("error", "Error")), user=parsed.username))
        elif event == "media.rate":
            responses.append(await simkl.sync_ratings(parsed, rating=int(parsed.rating or 10)))
        else:
            return {"status": "skipped", "reason": "This event type is not supported by Simkl."}
        response_states = [_tracker_result_state(response) for response in responses]
        if "queued" in response_states:
            return {"status": "queued", "reason": "Saved for retry after a temporary error."}
        if "failed" in response_states:
            failed_response = next((response for response in responses if _tracker_result_state(response) == "failed"), None)
            return {"status": "failed", "error": _delivery_reason("failed", failed_response)}
        return {"status": "success" if "success" in response_states else "skipped"}
    except Exception as e:
        logger.warning(f"Simkl dispatch error for {parsed.title}: {e}")
        asyncio.create_task(notifier.send_failure_alert(parsed, "Simkl", str(e), user=parsed.username))
        return {"status": "failed", "error": str(e)}


async def execute_multi_tracker_dispatch(parsed: ParsedMedia, action_taken: str, event: str, progress: float, event_id: Optional[str] = None):
    """Dispatch playback scrobble and rating events asynchronously to Simkl, AniList, and MyAnimeList."""
    delivery: dict[str, str] = {}
    delivery_results: dict[str, Any] = {}
    if settings_mgr.is_tracker_enabled("simkl") and simkl.is_enabled() and simkl.is_authenticated():
        simkl_result = await execute_simkl_scrobble(parsed, action_taken, event)
        delivery["simkl"] = _tracker_result_state(simkl_result)
        delivery_results["simkl"] = simkl_result

    ani_active = settings_mgr.is_tracker_enabled("anilist") and anilist.is_enabled() and anilist.is_authenticated()
    mal_active = settings_mgr.is_tracker_enabled("mal") and mal.is_enabled() and mal.is_authenticated()
    anime_action = event == "media.rate" or event == "media.scrobble" or (action_taken == "scrobble_stop" and progress >= Config.get_threshold(parsed.media_type))
    if ani_active or mal_active:
        if not anime_action:
            for key, active in (("anilist", ani_active), ("mal", mal_active)):
                if not active:
                    continue
                delivery[key] = "skipped"
                delivery_results[key] = {
                    "reason": multi_tracker.capability_skip_reason(key, "realtime_playback", parsed.media_type)
                    or "This event does not include an anime progress update."
                }
        else:
            try:
                resolved_anime = await anime_resolver.resolve(parsed)
                if not (resolved_anime and resolved_anime.get("is_anime")):
                    delivery.update({key: "skipped" for key, active in (("anilist", ani_active), ("mal", mal_active)) if active})
                    for key, active in (("anilist", ani_active), ("mal", mal_active)):
                        if active:
                            delivery_results[key] = {"reason": "No matching anime title was resolved."}
                elif event == "media.rate":
                    if ani_active:
                        ani_res = await anilist.update_rating(resolved_anime["anilist_id"], float(parsed.rating or 10))
                        delivery["anilist"] = _tracker_result_state(ani_res)
                        delivery_results["anilist"] = ani_res
                        if delivery["anilist"] == "failed":
                            asyncio.create_task(notifier.send_failure_alert(parsed, "AniList", str(ani_res.get("errors", "Error")), user=parsed.username))
                    if mal_active and resolved_anime.get("mal_id"):
                        mal_res = await mal.update_rating(resolved_anime["mal_id"], int(parsed.rating or 10))
                        delivery["mal"] = _tracker_result_state(mal_res)
                        delivery_results["mal"] = mal_res
                        if delivery["mal"] == "failed":
                            asyncio.create_task(notifier.send_failure_alert(parsed, "MyAnimeList", str(mal_res.get("error", "Error")), user=parsed.username))
                    elif mal_active:
                        delivery["mal"] = "skipped"
                        delivery_results["mal"] = {"reason": "No MyAnimeList ID was available for the matched anime."}
                else:
                    if ani_active:
                        ani_res = await anilist.update_progress(resolved_anime["anilist_id"], resolved_anime["episode_number"], resolved_anime.get("episodes"))
                        delivery["anilist"] = _tracker_result_state(ani_res)
                        delivery_results["anilist"] = ani_res
                        if delivery["anilist"] == "failed":
                            asyncio.create_task(notifier.send_failure_alert(parsed, "AniList", str(ani_res.get("errors", "Error")), user=parsed.username))
                    if mal_active and resolved_anime.get("mal_id"):
                        mal_res = await mal.update_progress(resolved_anime["mal_id"], resolved_anime["episode_number"], resolved_anime.get("episodes"))
                        delivery["mal"] = _tracker_result_state(mal_res)
                        delivery_results["mal"] = mal_res
                        if delivery["mal"] == "failed":
                            asyncio.create_task(notifier.send_failure_alert(parsed, "MyAnimeList", str(mal_res.get("error", "Error")), user=parsed.username))
                    elif mal_active:
                        delivery["mal"] = "skipped"
                        delivery_results["mal"] = {"reason": "No MyAnimeList ID was available for the matched anime."}
            except Exception as e:
                logger.warning(f"Anime multi-tracker dispatch error for {parsed.title}: {e}")
                delivery.update({key: "failed" for key, active in (("anilist", ani_active), ("mal", mal_active)) if active and key not in delivery})
                delivery_results.update({key: {"error": str(e)} for key, active in (("anilist", ani_active), ("mal", mal_active)) if active and key not in delivery_results})

    if event_id and delivery:
        update_event_tracker_delivery(event_id, delivery, delivery_results)


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


activity_delivery_mgr = ActivityDeliveryManager(
    events=recent_events,
    save_events=save_recent_events,
    queue=queue_mgr,
    log_manager=log_mgr,
    notifier=notifier,
)
_tracker_result_state = activity_delivery_mgr._tracker_result_state
_tracker_delivery_from_result = activity_delivery_mgr._tracker_delivery_from_result
update_event_tracker_delivery = activity_delivery_mgr.update_event_tracker_delivery
_aggregate_delivery_status = activity_delivery_mgr._aggregate_delivery_status
_delivery_reason = activity_delivery_mgr._delivery_reason
_delivery_category = activity_delivery_mgr._delivery_category
_initial_delivery_details = activity_delivery_mgr._initial_delivery_details
record_queue_delivery = activity_delivery_mgr.record_queue_delivery
update_event_cowatch_status = activity_delivery_mgr.update_event_cowatch_status
record_mirror_delivery = activity_delivery_mgr.record_mirror_delivery

def log_event(
    media: ParsedMedia,
    action: str,
    result: dict[str, Any],
    cowatch_status: Optional[dict[str, Any]] = None,
    rule_evaluation: Optional[dict[str, Any]] = None,
    review_payload: Optional[dict[str, Any]] = None,
) -> str:
    if media.media_type == "episode":
        title_str = f"{media.show_title} S{media.season:02d}E{media.episode:02d} - {media.title}"
    else:
        title_str = f"{media.title} ({media.year or 'N/A'})"

    action_str = f"{action} ({media.rating}/10)" if action == "rate" and media.rating else action
    progress_str = f"{media.rating}/10" if action == "rate" and media.rating else f"{media.progress:.1f}%"

    event_id = secrets.token_hex(12)
    tracker_delivery = _tracker_delivery_from_result(result, action)
    event_status = result.get("status") or ("ok" if not result.get("error") else "error")
    if action in {"bypassed", "none"}:
        event_status = "ignored"
    elif tracker_delivery.get("trakt") == "queued":
        event_status = "queued"
    elif tracker_delivery.get("trakt") == "failed":
        event_status = "error"
    entry = {
        "event_id": event_id,
        "operation_id": event_id,
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
        "is_anime": anime_resolver.is_explicit_anime(media),
        "result_status": event_status,
        "tracker_delivery": tracker_delivery,
        "delivery_status": _aggregate_delivery_status(tracker_delivery),
        "tracker_delivery_details": _initial_delivery_details(result, tracker_delivery),
        "raw_result": result,
        "cowatch_status": cowatch_status,
        "rule_evaluation": rule_evaluation,
        "review_status": "pending" if action == "pending_review" and review_payload else None,
        "review_payload": review_payload,
    }
    recent_events.appendleft(entry)
    save_recent_events()
    save_scrobble_stats()
    return event_id


async def execute_cowatch_sync(
    parsed: ParsedMedia,
    action: str,
    targets: Optional[list[str]] = None,
    selected_trackers: Optional[list[str]] = None,
    outcome_details: Optional[list[str]] = None,
    event_id: Optional[str] = None,
):
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
        return "skipped"

    tasks = []
    immediate_outcomes: list[str] = []
    watched_at_ts = datetime.datetime.now(timezone.utc).isoformat()

    def enqueue_household(payload: dict[str, Any], reason: str, user: str) -> None:
        item_id = queue_mgr.enqueue("sync_history", payload, error=reason, username=user, event_id=event_id)
        if event_id:
            activity_delivery_mgr.register_cowatch_queue(event_id, item_id, f"{user}:trakt")
            record_queue_delivery(event_id, "sync_history", item_id, "queued", reason, 1)

    async def track_outcome(call, user: str, tracker: str):
        try:
            status = await call
        except Exception:
            if event_id:
                activity_delivery_mgr.update_cowatch_destination(event_id, f"{user}:{tracker}", "failed")
            raise
        if event_id:
            activity_delivery_mgr.update_cowatch_destination(event_id, f"{user}:{tracker}", status)
        return status

    for target_user in target_users:
        # 1. Partner/Profile Trakt
        cw_trakt = user_mgr.get_client(target_user)
        if (selected_trackers is None or "trakt" in selected_trackers) and cw_trakt and cw_trakt.is_authenticated():
            async def _sync_trakt(u=target_user, client=cw_trakt):
                history_payload = parsed.to_trakt_history_payload(watched_at=watched_at_ts)
                try:
                    logger.info(f"Household sync triggering for profile @{u} (Trakt): {parsed.title}")
                    res = await client.sync_history(history_payload)
                    if isinstance(res, dict) and isinstance(res.get("not_found"), dict) and any(res["not_found"].values()):
                        if parsed.media_type == "episode" and parsed.ids:
                            # Some upstream IDs resolve the show but not its episode. Retry with
                            # Trakt's show/season/episode matcher before reporting delivery failure.
                            fallback_media = parsed.model_copy(update={"ids": {}})
                            history_payload = fallback_media.to_trakt_history_payload(watched_at=watched_at_ts)
                            res = await client.sync_history(history_payload)
                        if isinstance(res, dict) and isinstance(res.get("not_found"), dict) and any(res["not_found"].values()):
                            reason = "Trakt could not match this episode by ID or show/season/episode."
                            logger.warning("Household Trakt did not match episode for @%s: %s", u, parsed.title)
                            if outcome_details is not None:
                                outcome_details.append(reason)
                            metrics_registry.record_cowatch("failed")
                            return "failed"
                    if is_temporary_error(res):
                        enqueue_household(history_payload, str(res.get("error", "")), u)
                        metrics_registry.record_cowatch("queued")
                        return "queued"
                    elif isinstance(res, dict) and res.get("status") == "error":
                        logger.warning(f"Household Trakt error for @{u}: {res.get('error')}")
                        if outcome_details is not None:
                            outcome_details.append(str(res.get("error", "Trakt sync failed")))
                        enqueue_household(history_payload, str(res.get("error", "Trakt sync failed")), u)
                        metrics_registry.record_cowatch("queued")
                        return "queued"
                    else:
                        logger.info(f"Household sync succeeded for profile @{u} (Trakt): {parsed.title}")
                        metrics_registry.record_cowatch("success")
                        return "success"
                except Exception as e:
                    logger.error(f"Error during household Trakt sync for @{u}: {e}")
                    if outcome_details is not None:
                        outcome_details.append(str(e))
                    enqueue_household(history_payload, str(e), u)
                    metrics_registry.record_cowatch("queued")
                    return "queued"
            tasks.append(track_outcome(_sync_trakt(), target_user, "trakt"))
        elif selected_trackers is None or "trakt" in selected_trackers:
            history_payload = parsed.to_trakt_history_payload(watched_at=watched_at_ts)
            logger.warning(
                f"Household profile @{target_user} Trakt is not authenticated; queueing watched history for retry."
            )
            enqueue_household(history_payload, "Co-Watch target Trakt profile is not authenticated", target_user)
            metrics_registry.record_cowatch("queued")
            immediate_outcomes.append("queued")

        # 2. Partner/Profile Simkl
        cw_simkl = user_mgr.get_tracker_client(target_user, "simkl")
        if (selected_trackers is None or "simkl" in selected_trackers) and cw_simkl and cw_simkl.is_authenticated():
            async def _sync_simkl(u=target_user, client=cw_simkl):
                try:
                    logger.info(f"Household sync triggering for profile @{u} (Simkl): {parsed.title}")
                    if action == "rate":
                        res = await client.sync_ratings(parsed, rating=int(parsed.rating or 10))
                    else:
                        res = await client.scrobble_stop(parsed, progress=parsed.progress)
                        history_res = await client.sync_history(parsed, watched_at=watched_at_ts)
                        if isinstance(res, dict) and res.get("status") == "error":
                            logger.warning(f"Household Simkl error for @{u}: {res.get('error')}")
                            return "failed"
                        if isinstance(history_res, dict) and history_res.get("status") == "error":
                            return "failed"
                    return "failed" if isinstance(res, dict) and res.get("status") == "error" else "success"
                except Exception as e:
                    logger.warning(f"Error during household Simkl sync for @{u}: {e}")
                    return "failed"
            tasks.append(track_outcome(_sync_simkl(), target_user, "simkl"))

        # 3. Partner/Profile Anime Trackers (AniList & MAL)
        cw_anilist = user_mgr.get_tracker_client(target_user, "anilist")
        cw_mal = user_mgr.get_tracker_client(target_user, "mal")
        ani_auth = bool((selected_trackers is None or "anilist" in selected_trackers) and cw_anilist and cw_anilist.is_authenticated())
        mal_auth = bool((selected_trackers is None or "mal" in selected_trackers) and cw_mal and cw_mal.is_authenticated())

        if ani_auth or mal_auth:
            async def _sync_anime(u=target_user, a_auth=ani_auth, m_auth=mal_auth, c_ani=cw_anilist, c_mal=cw_mal):
                try:
                    resolved_anime = await anime_resolver.resolve(parsed)
                    if not resolved_anime or not resolved_anime.get("is_anime"):
                        return "skipped"
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
                    return "success"
                except Exception as e:
                    logger.warning(f"Error during household anime sync for @{u}: {e}")
                    return "failed"
            tasks.append(track_outcome(_sync_anime(), target_user, "anime"))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        immediate_outcomes.extend(
            "failed" if isinstance(result, BaseException) else result
            for result in results
        )

    outcomes = [outcome for outcome in immediate_outcomes if outcome in {"success", "queued", "failed"}]
    if not outcomes:
        return "skipped"
    if "success" in outcomes and any(outcome != "success" for outcome in outcomes):
        return "partial"
    if "success" in outcomes:
        return "success"
    if "queued" in outcomes and "failed" in outcomes:
        return "partial"
    if "queued" in outcomes:
        return "queued"
    return "failed"


async def execute_cowatch_sync_for_event(
    parsed: ParsedMedia,
    action: str,
    targets: list[str],
    event_id: str,
    selected_trackers: Optional[list[str]] = None,
) -> None:
    """Run Co-Watch delivery and update the originating activity event."""
    try:
        outcome_details: list[str] = []
        status = await execute_cowatch_sync(
            parsed, action, targets=targets, selected_trackers=selected_trackers,
            outcome_details=outcome_details,
            event_id=event_id,
        )
    except Exception:
        logger.exception("Unexpected error while executing Co-Watch sync for event %s", event_id)
        status = "failed"
    if status not in {"success", "queued", "partial", "failed", "skipped"}:
        logger.error("Co-Watch sync for event %s returned an invalid status: %r", event_id, status)
        status = "failed"
    update_event_cowatch_status(event_id, status, "; ".join(outcome_details) or None)


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
            webhook_debugger.record_auth_rejection(endpoint_name, request.url.path)
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


async def execute_automation_route(parsed: ParsedMedia, evaluation: dict[str, Any], endpoint_name: str) -> dict[str, Any]:
    """Dispatch one matched route rule through existing tracker and household services."""
    event = parsed.event
    requested_trackers = [str(item).lower() for item in evaluation.get("trackers", [])]
    profiles = [str(item).strip() for item in evaluation.get("profiles", []) if str(item).strip()]
    aliases = {"myanimelist": "mal"}
    requested_trackers = list(dict.fromkeys(aliases.get(item, item) for item in requested_trackers))
    selected = [tracker for tracker in requested_trackers if settings_mgr.is_tracker_enabled(tracker)]
    disabled_trackers = [tracker for tracker in requested_trackers if tracker not in selected]
    profiles = list(dict.fromkeys(profile for profile in profiles if profile.lower() != (parsed.username or "").lower()))
    result: dict[str, Any] = {"status": "success", "automation": evaluation, "trackers": [], "skipped": {}}
    result["skipped"].update({tracker: {"status": "skipped", "reason": "Tracker is paused in global settings."} for tracker in disabled_trackers})
    action_taken = "automation_route"
    queued_item_ids: list[int] = []

    def enqueue_route(event_type: str, payload: dict[str, Any], reason: str) -> None:
        queued_item_ids.append(queue_mgr.enqueue(event_type, payload, error=reason, username=route_queue_username))

    route_queue_username = parsed.username
    active_client = user_mgr.get_client(parsed.username)
    if not active_client.is_authenticated():
        active_client = trakt
        route_queue_username = "default"
    if active_client is trakt:
        route_queue_username = "default"

    if event in {"media.play", "media.resume"}:
        action_taken = "scrobble_start"
        playback_mgr.update_playback(parsed, state="playing")
        if selected:
            result = await multi_tracker.dispatch_scrobble("start", parsed, active_client, parsed.progress, selected_trackers=selected)
    elif event == "media.pause":
        action_taken = "scrobble_pause"
        playback_mgr.update_playback(parsed, state="paused")
        if selected:
            result = await multi_tracker.dispatch_scrobble("pause", parsed, active_client, parsed.progress, selected_trackers=selected)
    elif event in {"media.stop", "media.scrobble"}:
        finished = playback_mgr.stop_playback(parsed)
        if finished and finished.get("scrobbled"):
            metrics_registry.record_request(endpoint_name, 200)
            return {"status": "ignored", "reason": "Watch already recorded for this playback session", "rule": evaluation.get("matched_rule")}
        if finished:
            parsed = parsed.model_copy(update={
                "progress": max(parsed.progress, float(finished.get("progress") or 0.0)),
                "duration_ms": parsed.duration_ms or finished.get("duration_ms"),
                "view_offset_ms": parsed.view_offset_ms if parsed.view_offset_ms is not None else finished.get("view_offset_ms"),
            })
        if event == "media.scrobble":
            parsed = parsed.model_copy(update={"progress": 100.0})
        action = "scrobble" if event == "media.scrobble" else "stop"
        action_taken = "mark_watched" if event == "media.scrobble" else ("scrobble_stop" if parsed.progress >= settings_mgr.get_effective_threshold(parsed.media_type) else "playback_stopped")
        if selected:
            result = await multi_tracker.dispatch_scrobble(action, parsed, active_client, parsed.progress, selected_trackers=selected)
        threshold = settings_mgr.get_effective_threshold(parsed.media_type)
        if "trakt" in selected:
            trakt_result = result.get("trakt")
            if is_temporary_error(trakt_result):
                payload = parsed.to_trakt_scrobble_payload()
                payload["progress"] = 100.0 if event == "media.scrobble" else parsed.progress
                enqueue_route("scrobble_stop", payload, str(trakt_result.get("error", "")))
                result["queued"] = True
            if event == "media.scrobble":
                watched_at = datetime.datetime.now(timezone.utc).isoformat()
                history_payload = parsed.to_trakt_history_payload(watched_at=watched_at)
                history_result = await active_client.sync_history(history_payload)
                if is_temporary_error(history_result):
                    enqueue_route("sync_history", history_payload, str(history_result.get("error", "")))
                    result["queued"] = True
                result["trakt_history"] = history_result
        if event == "media.scrobble" or (action == "stop" and parsed.progress >= threshold):
            record_watch_stat(parsed)
    elif event == "media.rate":
        if selected:
            result = await multi_tracker.dispatch_rating(parsed, active_client, int(parsed.rating or 10), selected_trackers=selected)
        action_taken = "rate"
        scrobble_stats["ratings"] = scrobble_stats.get("ratings", 0) + 1
        if "trakt" in selected and is_temporary_error(result.get("trakt")):
            enqueue_route("sync_ratings", parsed.to_trakt_rating_payload(), str(result["trakt"].get("error", "")))
            result["queued"] = True
    elif event == "library.new":
        action_taken = "collection"
        if "trakt" in selected and Config.SYNC_COLLECTION:
            collection_payload = parsed.to_trakt_collection_payload()
            collection_result = await active_client.sync_collection(collection_payload)
            result = {"status": "success", "trackers": ["trakt"], "trakt": collection_result, "automation": evaluation}
            if is_temporary_error(collection_result):
                enqueue_route("sync_collection", collection_payload, str(collection_result.get("error", "")))
                result["queued"] = True
            else:
                metrics_registry.record_collection(parsed.media_type, "success")
            scrobble_stats["collections"] = scrobble_stats.get("collections", 0) + 1
        elif selected:
            result = {
                "status": "skipped",
                "reason": "Collection routing is currently supported only by Trakt.",
                "automation": evaluation,
                "trackers": [],
                "skipped": {tracker: {"status": "skipped", "reason": "This tracker does not support collection ingestion."} for tracker in selected},
            }
        else:
            result = {"status": "skipped", "reason": "Collection events require Trakt as a selected destination.", "automation": evaluation, "trackers": [], "skipped": {}}
    else:
        result = {"status": "pending_review", "reason": f"No live route adapter exists for event '{event}'.", "automation": evaluation}
        action_taken = "pending_review"

    if disabled_trackers:
        result.setdefault("skipped", {}).update({tracker: {"status": "skipped", "reason": "Tracker is paused in global settings."} for tracker in disabled_trackers})
    if not result.get("automation"):
        result["automation"] = evaluation
    threshold = settings_mgr.get_effective_threshold(parsed.media_type)
    profile_sync = bool(profiles) and (
        event in {"media.scrobble", "media.rate"}
        or (event == "media.stop" and parsed.progress >= threshold)
    )
    if not selected and not profile_sync and result.get("status") == "success":
        result["status"] = "skipped"
        result["reason"] = "No selected destination handles this event; profile routes apply to completed watches and ratings."
    cowatch_info = None
    if profile_sync:
        cowatch_info = {
            "synced": False,
            "scheduled": True,
            "status": "scheduled",
            "reason": f"Automation rule routed this event to {', '.join(profiles)}.",
            "target": profiles[0],
            "targets": profiles,
        }
    elif profiles:
        cowatch_info = {"synced": False, "scheduled": False, "status": "skipped", "reason": "Profile routes run only for completed watches and ratings.", "targets": profiles}

    review_payload = parsed.model_dump(exclude={"raw_payload", "file_path", "poster_url", "backdrop_url"}) if action_taken == "pending_review" else None
    event_id = log_event(parsed, action_taken, result, cowatch_status=cowatch_info, rule_evaluation=evaluation, review_payload=review_payload)
    queue_mgr.attach_event(queued_item_ids, event_id)
    delivery = _tracker_delivery_from_result(result, action_taken)
    if result.get("queued") and "trakt" in delivery:
        delivery["trakt"] = "queued"
    update_event_tracker_delivery(event_id, delivery, result)
    if profile_sync:
        profile_trackers = selected or [tracker for tracker in ("trakt", "simkl", "anilist", "mal") if settings_mgr.is_tracker_enabled(tracker)]
        asyncio.create_task(execute_cowatch_sync_for_event(
            parsed,
            action_taken,
            profiles,
            event_id,
            selected_trackers=profile_trackers,
        ))
    is_sync_trigger = event == "media.scrobble" or (event == "media.stop" and parsed.progress >= threshold)
    if action_taken in {"mark_watched", "scrobble_stop", "rate"}:
        asyncio.create_task(notifier.dispatch(parsed, action_taken))
    if is_sync_trigger and settings_mgr.is_multi_server_mirroring_enabled():
        source_server = "plex" if endpoint_name == "webhook" else endpoint_name
        asyncio.create_task(record_mirror_delivery(
            event_id,
            reverse_sync_mgr.mirror_watched_status(parsed, source_server=source_server),
        ))
    metrics_registry.record_request(endpoint_name, 200)
    return {"status": result.get("status", "success"), "event_id": event_id, "action": action_taken, "result": result, "rule": evaluation.get("matched_rule")}


async def process_media_event(
    parsed: ParsedMedia,
    endpoint_name: str = "webhook",
    skip_automation_rules: bool = False,
) -> dict[str, Any]:
    queued_item_ids: list[int] = []
    queue_username = parsed.username

    def enqueue_for_event(event_type: str, payload: dict[str, Any], error: str = "") -> int:
        item_id = queue_mgr.enqueue(event_type, payload, error=error, username=queue_username)
        queued_item_ids.append(item_id)
        return item_id

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

    # Existing filters remain the first gate before the ordered automation policy.
    allowed, bypass_reason = settings_mgr.is_media_allowed(parsed)
    if not allowed:
        logger.info(f"Rules filter: bypassing event '{parsed.event}' for '{parsed.title}' ({bypass_reason})")
        metrics_registry.record_request(endpoint_name, 200)
        log_event(parsed, "bypassed", {"reason": bypass_reason})
        return {"status": "ignored", "reason": bypass_reason}

    rule_event = {
        "server": parsed.server_type or endpoint_name,
        "library": parsed.library_section_title,
        "media_type": parsed.media_type,
        "device": parsed.device,
        "player": parsed.player,
        "user": parsed.username,
    }
    rule_evaluation = (
        {"decision": "allow", "reason": "Approved by an administrator.", "matched_rule": None, "conflicts": []}
        if skip_automation_rules
        else automation_rules_mgr.evaluate(rule_event)
    )
    decision = rule_evaluation.get("decision", "allow")
    if decision == "route":
        return await execute_automation_route(parsed, rule_evaluation, endpoint_name)
    if decision in {"suppress", "review"}:
        if decision == "suppress":
            action = "suppressed"
            status = "ignored"
            reason = rule_evaluation["reason"]
        else:
            action = "pending_review"
            status = "pending_review"
            reason = rule_evaluation["reason"]
        review_payload = parsed.model_dump(exclude={"raw_payload", "file_path", "poster_url", "backdrop_url"}) if decision == "review" else None
        event_id = log_event(parsed, action, {"status": status, "reason": reason}, rule_evaluation=rule_evaluation, review_payload=review_payload)
        metrics_registry.record_request(endpoint_name, 200)
        return {"status": status, "event_id": event_id, "reason": reason, "rule": rule_evaluation.get("matched_rule")}

    active_client = user_mgr.get_client(parsed.username)
    if not active_client.is_authenticated():
        active_client = trakt
        queue_username = "default"
    if active_client is trakt:
        queue_username = "default"

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
                enqueue_for_event("sync_collection", col_payload, error=str(result.get("error", "")))
                result["queued"] = True
                metrics_registry.record_collection(parsed.media_type, "queued")
            else:
                metrics_registry.record_collection(parsed.media_type, "success")

            scrobble_stats["collections"] = scrobble_stats.get("collections", 0) + 1
            event_id = log_event(parsed, action_taken, result, rule_evaluation=rule_evaluation)
            queue_mgr.attach_event(queued_item_ids, event_id)
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
                enqueue_for_event("scrobble_stop", scrobble_payload, error=str(scrobble_res.get("error", "")))
                result["queued"] = True
                metrics_registry.record_scrobble(parsed.media_type, "queued")
            else:
                metrics_registry.record_scrobble(parsed.media_type, "success")

            if is_temporary_error(history_res):
                enqueue_for_event("sync_history", parsed.to_trakt_history_payload(watched_at=watched_at_ts), error=str(history_res.get("error", "")))
                result["queued"] = True

            record_watch_stat(parsed)

        elif event == "media.rate":
            action_taken = "rate"
            rating_val = parsed.rating or 10
            logger.info(f"Syncing rating to Trakt: {parsed.title} -> {rating_val}/10 for user {parsed.username}")
            rating_payload = parsed.to_trakt_rating_payload()
            result = await active_client.sync_ratings(rating_payload)
            if is_temporary_error(result):
                enqueue_for_event("sync_ratings", rating_payload, error=str(result.get("error", "")))
                result["queued"] = True
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
                        enqueue_for_event("scrobble_stop", scrobble_payload, error=str(result.get("error", "")))
                        result["queued"] = True
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
                if finished:
                    # Some servers send a stop event with a zero or missing
                    # offset. Use the best position captured during playback.
                    parsed = parsed.model_copy(update={
                        "progress": max(parsed.progress, float(finished.get("progress") or 0.0)),
                        "duration_ms": parsed.duration_ms or finished.get("duration_ms"),
                        "view_offset_ms": parsed.view_offset_ms if parsed.view_offset_ms is not None else finished.get("view_offset_ms"),
                    })
                    scrobble_payload = parsed.to_trakt_scrobble_payload()
                if parsed.progress >= threshold:
                    action_taken = "scrobble_stop"
                    logger.info(f"Scrobble stop (watched past threshold {threshold}%): {parsed.title} ({parsed.progress:.1f}%)")
                    result = await active_client.scrobble_stop(scrobble_payload)
                    if is_temporary_error(result):
                        enqueue_for_event("scrobble_stop", scrobble_payload, error=str(result.get("error", "")))
                        result["queued"] = True
                        metrics_registry.record_scrobble(parsed.media_type, "queued")
                    else:
                        metrics_registry.record_scrobble(parsed.media_type, "success")
                    record_watch_stat(parsed)
                else:
                    action_taken = "playback_stopped"
                    logger.info(f"Playback stopped below threshold {threshold}%: {parsed.title} ({parsed.progress:.1f}%)")
                    # Always close Trakt's in-progress scrobble. The watched
                    # threshold controls history/Co-Watch, not session cleanup.
                    result = await active_client.scrobble_stop(scrobble_payload)
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
        cowatch_targets_to_sync: list[str] = []

        if is_sync_trigger:
            if targets or eligible:
                cowatch_targets_to_sync = targets or ([Config.CO_WATCH_USER] if eligible and Config.CO_WATCH_USER else [])
                primary_target = cowatch_targets_to_sync[0] if cowatch_targets_to_sync else Config.CO_WATCH_USER
                target_str = ", ".join(cowatch_targets_to_sync) if len(cowatch_targets_to_sync) > 1 else primary_target
                sync_reason = f"Household routing to {target_str}" if len(cowatch_targets_to_sync) > 1 else reason
                cowatch_info = {
                    "synced": False,
                    "scheduled": True,
                    "status": "scheduled",
                    "reason": sync_reason,
                    "target": primary_target,
                    "targets": cowatch_targets_to_sync,
                }
            else:
                cowatch_info = {"synced": False, "scheduled": False, "status": "skipped", "reason": reason, "target": Config.CO_WATCH_USER, "targets": []}
        elif eligible or targets:
            primary_target = targets[0] if targets else Config.CO_WATCH_USER
            cowatch_info = {"synced": False, "in_list": True, "reason": reason, "target": primary_target, "targets": targets}

        event_id = log_event(parsed, action_taken, result, cowatch_status=cowatch_info, rule_evaluation=rule_evaluation)
        queue_mgr.attach_event(queued_item_ids, event_id)

        if cowatch_targets_to_sync:
            asyncio.create_task(execute_cowatch_sync_for_event(parsed, action_taken, cowatch_targets_to_sync, event_id))

        if action_taken in ("mark_watched", "scrobble_stop", "rate"):
            partner_target = (", ".join(cowatch_targets_to_sync) if cowatch_targets_to_sync else None) if (cowatch_info and cowatch_info.get("scheduled")) else None
            asyncio.create_task(notifier.dispatch(parsed, action_taken, cowatch_partner=partner_target))

        # Real-time multi-server watched status and rating mirroring
        if is_sync_trigger and settings_mgr.is_multi_server_mirroring_enabled():
            src_server = "plex" if endpoint_name in ("webhook", "plex") else endpoint_name
            asyncio.create_task(record_mirror_delivery(
                event_id, reverse_sync_mgr.mirror_watched_status(parsed, source_server=src_server)
            ))
        elif action_taken == "rate" and settings_mgr.is_multi_server_mirroring_enabled() and parsed.rating:
            src_server = "plex" if endpoint_name in ("webhook", "plex") else endpoint_name
            asyncio.create_task(record_mirror_delivery(
                event_id, reverse_sync_mgr.mirror_rating(parsed, float(parsed.rating), source_server=src_server)
            ))

        asyncio.create_task(execute_multi_tracker_dispatch(parsed, action_taken, event, parsed.progress, event_id=event_id))

        metrics_registry.record_request(endpoint_name, 200)
        return {"status": "success", "event": event, "action": action_taken, "result": result}

    except Exception as e:
        logger.error(f"Error executing Trakt action for {parsed.title}: {e}", exc_info=True)
        if event == "library.new":
            enqueue_for_event("sync_collection", parsed.to_trakt_collection_payload(), error=str(e))
            metrics_registry.record_collection(parsed.media_type, "queued")
        elif event == "media.scrobble":
            enqueue_for_event("scrobble_stop", scrobble_payload, error=str(e))
            enqueue_for_event("sync_history", parsed.to_trakt_history_payload(), error=str(e))
            metrics_registry.record_scrobble(parsed.media_type, "queued")
        elif event == "media.rate":
            enqueue_for_event("sync_ratings", parsed.to_trakt_rating_payload(), error=str(e))
            metrics_registry.record_rating("queued")
        elif action_taken == "scrobble_stop":
            enqueue_for_event("scrobble_stop", scrobble_payload, error=str(e))
            metrics_registry.record_scrobble(parsed.media_type, "queued")
        event_id = log_event(parsed, action_taken, {"error": str(e), "queued": True})
        queue_mgr.attach_event(queued_item_ids, event_id)
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
    verify_webhook_token(request, "sonarr")

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
            queue_mgr.enqueue("sync_collection", trakt_payload, error=str(result.get("error", "")), username="default")
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
        queue_mgr.enqueue("sync_collection", trakt_payload, error=str(e), username="default")
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
    verify_webhook_token(request, "radarr")

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
            queue_mgr.enqueue("sync_collection", trakt_payload, error=str(result.get("error", "")), username="default")
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
        queue_mgr.enqueue("sync_collection", trakt_payload, error=str(e), username="default")
        metrics_registry.record_collection("movie", "queued")
        log_event(parsed, action_taken, {"error": str(e), "queued": True})
        metrics_registry.record_request("radarr", 500)
        webhook_debugger.update_status(debug_entry["id"], status="error", reason=str(e))
        return {"status": "error", "error": str(e), "queued": True}


trakt_user_profile: Optional[dict[str, Any]] = None


def invalidate_trakt_user_profile() -> None:
    global trakt_user_profile
    trakt_user_profile = None


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
            "pending": None if queue_mgr._future_schema_version else queue_mgr.get_pending_count(),
            "status": "unsupported" if queue_mgr._future_schema_version else "supported",
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


def _build_configuration_diagnostics(active_trackers: list[str]) -> dict[str, Any]:
    """Validate effective local settings and report safe source metadata."""
    all_settings = settings_mgr.get_all_settings(mask_token=False)
    rules = all_settings.get("rules", {})
    arr = all_settings.get("arr", {})
    notifications = all_settings.get("notifications", {})
    trackers = all_settings.get("trackers", {})
    persisted: dict[str, Any] = {}
    try:
        from app.services.crypto_manager import crypto_mgr
        persisted = crypto_mgr.read_secure_json(settings_mgr.settings_file) or {}
    except Exception as exc:
        logger.debug("Unable to inspect settings source metadata: %s", exc)

    def source(section: str, key: str, env_key: Optional[str | tuple[str, ...]] = None, default: str = "application default") -> str:
        if key in (persisted.get(section) or {}):
            return "runtime settings"
        env_keys = (env_key,) if isinstance(env_key, str) else (env_key or ())
        if any(os.getenv(name) is not None for name in env_keys):
            return "environment"
        return default

    effective_settings = []
    for name, key, env_key, value in (
        ("Plex listener", "plex", "PLEX_ENABLED", settings_mgr.is_server_enabled("plex")),
        ("Jellyfin listener", "jellyfin", "JELLYFIN_ENABLED", settings_mgr.is_server_enabled("jellyfin")),
        ("Emby listener", "emby", "EMBY_ENABLED", settings_mgr.is_server_enabled("emby")),
    ):
        effective_settings.append({"name": name, "value": bool(value), "source": source("servers", key, env_key, "upgrade detection or clean-install default")})
    for name, key, env_key, value in (
        ("Episode scrobble threshold", "scrobble_threshold", ("EPISODE_SCROBBLE_THRESHOLD", "SCROBBLE_THRESHOLD"), rules.get("scrobble_threshold")),
        ("Movie scrobble threshold", "movie_scrobble_threshold", "MOVIE_SCROBBLE_THRESHOLD", rules.get("movie_scrobble_threshold")),
        ("Minimum playback duration", "min_duration_seconds", "MIN_DURATION_SECONDS", rules.get("min_duration_seconds")),
    ):
        effective_settings.append({"name": name, "value": value, "source": source("rules", key, env_key)})
    effective_settings.append({
        "name": "Scrobble mode", "value": Config.SCROBBLE_MODE,
        "source": "environment" if os.getenv("SCROBBLE_MODE") is not None else "application default",
    })

    findings: list[dict[str, str]] = []
    def add(severity: str, code: str, message: str, setting: str) -> None:
        findings.append({"severity": severity, "code": code, "message": message, "setting": setting})

    if Config.SCROBBLE_MODE not in {"scrobble", "watched_only"}:
        add("error", "invalid_scrobble_mode", "SCROBBLE_MODE must be scrobble or watched_only.", "SCROBBLE_MODE")
    for key, label in (("scrobble_threshold", "Episode scrobble threshold"), ("movie_scrobble_threshold", "Movie scrobble threshold")):
        value = rules.get(key)
        try:
            if not 0 <= float(value) <= 100:
                add("error", "threshold_out_of_range", f"{label} must be between 0 and 100 percent.", key)
        except (TypeError, ValueError):
            add("error", "invalid_threshold", f"{label} must be a number between 0 and 100 percent.", key)
    try:
        if int(rules.get("min_duration_seconds", 0)) < 0:
            add("error", "negative_min_duration", "Minimum playback duration cannot be negative.", "min_duration_seconds")
    except (TypeError, ValueError):
        add("error", "invalid_min_duration", "Minimum playback duration must be a whole number of seconds.", "min_duration_seconds")

    day = str(notifications.get("weekly_digest_day", Config.WEEKLY_DIGEST_DAY)).lower()
    hour = notifications.get("weekly_digest_hour", Config.WEEKLY_DIGEST_HOUR)
    if day not in {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}:
        add("error", "invalid_digest_day", "Weekly digest day must be a weekday name.", "weekly_digest_day")
    try:
        if not 0 <= int(hour) <= 23:
            add("error", "invalid_digest_hour", "Weekly digest hour must be between 0 and 23.", "weekly_digest_hour")
    except (TypeError, ValueError):
        add("error", "invalid_digest_hour", "Weekly digest hour must be between 0 and 23.", "weekly_digest_hour")

    channel_pairs = (
        (("telegram_bot_token", "telegram_chat_id"), "Telegram requires both a bot token and a chat ID."),
        (("pushover_user_key", "pushover_api_token"), "Pushover requires both a user key and an API token."),
        (("gotify_url", "gotify_token"), "Gotify requires both a server URL and an app token."),
        (("matrix_homeserver_url", "matrix_access_token", "matrix_room_id"), "Matrix requires a homeserver, access token, and room ID."),
    )
    for keys, message in channel_pairs:
        present = [bool(str(notifications.get(key) or "").strip()) for key in keys]
        if any(present) and not all(present):
            add("warning", "incomplete_notification", message, keys[0])
    if notifications.get("ntfy_auth_token") and not notifications.get("ntfy_url"):
        add("warning", "incomplete_notification", "Ntfy authentication is configured without a server URL.", "ntfy_url")

    arr_configured = any(bool(str(arr.get(key) or "").strip()) for key in ("sonarr_api_key", "radarr_api_key", "overseerr_api_key"))
    for service, url_key, key_key in (("Sonarr", "sonarr_url", "sonarr_api_key"), ("Radarr", "radarr_url", "radarr_api_key")):
        has_url = bool(str(arr.get(url_key) or "").strip())
        has_key = bool(str(arr.get(key_key) or "").strip())
        if has_url != has_key:
            add("warning", "incomplete_arr_connection", f"{service} requires both a server URL and API key.", url_key)
    if arr.get("auto_add_from_watchlist") and not arr_configured:
        add("warning", "arr_automation_unconfigured", "Watch-list acquisition is enabled, but no Sonarr, Radarr, or request-service credentials are configured.", "auto_add_from_watchlist")
    notification_state = notifier.get_status()
    if notifications.get("weekly_digest_enabled") and not any(notification_state.get(channel) for channel in ("discord", "telegram", "ntfy", "pushover", "gotify", "matrix")):
        add("warning", "digest_without_channel", "Weekly digest is enabled, but no notification channel is configured.", "weekly_digest_enabled")

    credential_keys = {
        "trakt": ("client_id", "client_secret"), "simkl": ("client_id", "client_secret"),
        "tmdb": ("api_key", "access_token", "session_id"), "anilist": ("client_id", "client_secret"),
        "mal": ("client_id", "client_secret"), "kitsu": ("api_token",),
        "letterboxd": ("username",), "serializd": ("token", "username"), "mdblist": ("api_key",),
    }
    credentials = all_settings.get("credentials", {})
    for tracker_id, required_keys in credential_keys.items():
        configured = any(bool(str(credentials.get(tracker_id, {}).get(key) or "").strip()) for key in required_keys)
        enabled_key = "mal" if tracker_id == "myanimelist" else tracker_id
        enabled = trackers.get(enabled_key, True)
        if configured and not enabled:
            add("warning", "configured_tracker_disabled", f"{tracker_id} credentials are configured, but this tracker is disabled in runtime settings.", enabled_key)
        elif configured and enabled and tracker_id != "letterboxd" and tracker_id not in {name.lower().replace("myanimelist", "mal") for name in active_trackers}:
            add("warning", "tracker_not_authenticated", f"{tracker_id} is enabled and configured, but no authenticated connection is available.", enabled_key)

    return {
        "status": "warning" if any(item["severity"] in {"warning", "error"} for item in findings) else "healthy",
        "findings": findings,
        "effective_settings": effective_settings,
        "summary": f"{len(findings)} configuration issue(s) found." if findings else "Configuration values and enabled integrations passed local checks.",
    }


async def _get_recovery_health(request: Request):
    """Return a read-only, secret-free snapshot of local setup and recovery signals."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")

    checked_at = datetime.datetime.now(timezone.utc).isoformat()
    queue_unavailable = queue_mgr._future_schema_version is not None
    queue_counts = {} if queue_unavailable else queue_mgr.get_all_count()
    active_listeners = [
        name for name in ("plex", "jellyfin", "emby")
        if settings_mgr.is_server_enabled(name)
    ]
    tracker_clients = {
        "Trakt": trakt, "Simkl": simkl, "AniList": anilist,
        "MyAnimeList": mal, "Kitsu": kitsu, "Letterboxd": letterboxd,
        "Serializd": serializd, "MDBList": mdblist,
    }
    active_trackers = [
        name for name, client in tracker_clients.items()
        if settings_mgr.is_tracker_enabled(name.lower().replace("myanimelist", "mal"))
        and callable(getattr(client, "is_authenticated", None))
        and client.is_authenticated()
    ]
    token_ready = bool(active_trackers)
    configuration_diagnostics = _build_configuration_diagnostics(active_trackers)
    capability_registry = multi_tracker.get_registered_trackers()
    enabled_tracker_capabilities = [
        {
            "id": tracker_id,
            "name": details["name"],
            "media_types": list(details["media_types"]),
            "capabilities": list(details["capabilities"]),
            "supports_realtime_scrobble": bool(details["supports_realtime_scrobble"]),
            "supports_history": bool(details["supports_history"]),
            "supports_progress": bool(details["supports_progress"]),
            "supports_ratings": bool(details["supports_ratings"]),
            "supports_watchlist": bool(details["supports_watchlist"]),
            "supports_collection": bool(details["supports_collection"]),
            "supports_search": bool(details["supports_search"]),
        }
        for tracker_id, details in capability_registry.items()
        if settings_mgr.is_tracker_enabled("mal" if tracker_id == "myanimelist" else tracker_id)
    ]
    notification_channels = notifier.get_status()
    channels_configured = [
        channel for channel in ("discord", "telegram", "ntfy", "pushover", "gotify", "matrix")
        if notification_channels.get(channel)
    ]
    recent_webhooks = webhook_debugger.get_history(limit=25)
    webhook_processing_errors = sum(1 for item in recent_webhooks if item.get("status") == "error")
    webhook_auth_rejections = webhook_debugger.get_auth_rejection_summary()
    worker_names = (
        "queue_worker_task", "token_monitor_task", "scrobble_heartbeat_worker_task",
        "weekly_digest_worker_task", "reverse_sync_worker_task",
    )
    workers = []
    for name in worker_names:
        task = globals().get(name)
        # A worker that has not been started is inactive; an optional, disabled worker
        # is reported as optional rather than as a system failure.
        status = "healthy" if task is not None and not task.done() else "inactive"
        workers.append({"name": name.removesuffix("_task").replace("_", " ").title(), "status": status})
    required_workers = [worker for worker in workers if worker["name"] in {"Queue Worker", "Token Monitor", "Scrobble Heartbeat"}]
    worker_status = "healthy" if all(worker["status"] == "healthy" for worker in required_workers) else "warning"
    compatibility = compatibility_mgr.inspect(
        app_version=APP_VERSION,
        python_version=(sys.version_info.major, sys.version_info.minor),
        settings_version=settings_mgr._loaded_schema_version,
        settings_exists=settings_mgr.settings_file.exists(),
        watch_lists_path=watch_list_mgr.file_path,
        automation_path=automation_rules_mgr.rules_file,
        accounts_path=dashboard_auth_mgr.accounts_file,
        queue_path=queue_mgr.db_path,
    )

    checks = [
        {
            "id": "compatibility", "label": "Version and data compatibility",
            "status": "warning" if compatibility["status"] in {"warning", "action_required"} else "healthy",
            "summary": compatibility["summary"],
            "compatibility_items": compatibility["schemas"],
            "runtime": compatibility["runtime"],
            "application": compatibility["application"],
            "integrations": compatibility["integrations"],
            "integration_version_policy": compatibility["integration_version_policy"],
            "ruleset": compatibility["ruleset"],
            "next_action": "compatibility", "last_checked": checked_at,
        },
        {
            "id": "configuration", "label": "Configuration validation",
            "status": configuration_diagnostics["status"],
            "summary": configuration_diagnostics["summary"],
            "findings": configuration_diagnostics["findings"],
            "effective_settings": configuration_diagnostics["effective_settings"],
            "next_action": "servers", "last_checked": checked_at,
        },
        {
            "id": "trackers", "label": "Tracker authentication",
            "status": "healthy" if token_ready else "action_required",
            "summary": f"Connected trackers: {', '.join(active_trackers)}." if token_ready else "No enabled tracker account is connected. Link a tracker to deliver playback activity.",
            "next_action": "trackers", "test_integrations": ["trackers"], "last_checked": checked_at,
        },
        {
            "id": "listeners", "label": "Media server listeners",
            "status": "healthy" if active_listeners else "optional",
            "summary": f"Enabled listeners: {', '.join(active_listeners)}." if active_listeners else "No listeners are enabled. Enable one only if you want webhook ingestion.",
            "next_action": "servers", "last_checked": checked_at,
        },
        {
            "id": "webhook_auth", "label": "Webhook authentication",
            "status": "healthy" if Config.WEBHOOK_SECRET and not webhook_processing_errors and not webhook_auth_rejections["count"] else "warning",
            "summary": (f"Webhook secret is configured. {webhook_auth_rejections['count']} authentication rejections were recorded in the last 24 hours; {webhook_processing_errors} captured payloads had processing errors." if Config.WEBHOOK_SECRET else "Set WEBHOOK_SECRET in the environment and restart to require admin and incoming webhook authentication.") + " Failed-auth telemetry stores only bounded counts by endpoint; it does not retain request data, tokens, or client IPs.",
            "next_action": "webhook" if Config.WEBHOOK_SECRET else "webhook_setup", "last_checked": checked_at,
            "auth_rejections": webhook_auth_rejections,
        },
        {
            "id": "tracker_capabilities", "label": "Tracker capabilities",
            "status": "healthy" if active_trackers else "optional",
            "summary": (f"Capabilities are available for {len(enabled_tracker_capabilities)} enabled trackers." if active_trackers else "Enable and connect a tracker to review its supported actions.") + " OAuth permission scopes are not introspected by these checks.",
            "next_action": "trackers", "tracker_capabilities": enabled_tracker_capabilities,
            "last_checked": checked_at,
        },
        {
            "id": "queue", "label": "Offline delivery queue",
            "status": "unsupported" if queue_unavailable else "warning" if queue_counts.get("failed", 0) else "healthy",
            "summary": "Queue access is disabled: its schema is newer than this application supports." if queue_unavailable else f"{queue_counts.get('pending', 0)} pending, {queue_counts.get('failed', 0)} failed.",
            "next_action": "queue", "last_checked": checked_at,
        },
        {
            "id": "notifications", "label": "Notification delivery",
            "status": "healthy" if channels_configured else "optional",
            "summary": f"Configured channels: {', '.join(channels_configured)}." if channels_configured else "No notification channel is configured; alerts are optional.",
            "next_action": "notifications",
            "test_integrations": [f"notification:{channel}" for channel in channels_configured],
            "last_checked": checked_at,
        },
        {
            "id": "integrations", "label": "Integration reachability",
            "status": "optional",
            "summary": "Connection tests contact each service only when you select a test button.",
            "next_action": "servers",
            "test_integrations": ["plex", "jellyfin", "emby", "sonarr", "radarr", "overseerr"],
            "last_checked": checked_at,
        },
        {
            "id": "workers", "label": "Background workers",
            "status": worker_status,
            "summary": "Required background workers are running." if worker_status == "healthy" else "One or more required background workers are not running.",
            "next_action": "logs", "last_checked": checked_at,
            "workers": workers,
        },
    ]
    return {"checked_at": checked_at, "read_only": True, "checks": checks}


async def _test_recovery_connection(payload: RecoveryConnectionTestRequest, request: Request):
    """Explicitly test one configured integration and return a redacted result."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    integration = payload.integration.strip().lower()
    notification_channel = integration.removeprefix("notification:")
    is_notification_test = integration.startswith("notification:")
    if not is_notification_test and integration not in {"plex", "jellyfin", "emby", "sonarr", "radarr", "overseerr", "trackers"}:
        raise HTTPException(status_code=400, detail="Unsupported integration")
    if is_notification_test and notification_channel not in {"discord", "telegram", "ntfy", "pushover", "gotify", "matrix"}:
        raise HTTPException(status_code=400, detail="Unsupported notification channel")
    try:
        if is_notification_test:
            if not notifier.get_status().get(notification_channel):
                return {
                    "integration": integration,
                    "status": "failed",
                    "message": "This notification channel is no longer configured. Review notification settings.",
                    "checked_at": datetime.datetime.now(timezone.utc).isoformat(),
                }
            result = await test_notification_endpoint(NotificationTestRequest(channel=notification_channel), request)
        elif integration == "trackers":
            tracker_status = await get_multi_trackers_status(request)
            tracker_rows = tracker_status.get("trackers", {}) if isinstance(tracker_status, dict) else {}
            enabled_rows = [row for row in tracker_rows.values() if isinstance(row, dict) and row.get("enabled")]
            failed_states = {"error", "failed", "unreachable", "unauthorized", "not_authenticated"}
            tracker_checks_passed = bool(enabled_rows) and all(
                (row.get("authenticated") is True or row.get("configured") is True or row.get("connected") is True)
                and str(row.get("status", "")).lower() not in failed_states
                for row in enabled_rows
            )
            result = {"status": "connected" if tracker_checks_passed else "failed", "success": tracker_checks_passed}
        elif integration in {"plex", "jellyfin", "emby"}:
            result = await test_sync_connection(MediaServerTestConnectionRequest(server=integration), request)
        elif integration in {"sonarr", "radarr", "overseerr"}:
            result = await test_arr_connection(ArrTestConnectionRequest(app=integration), request)
    except HTTPException:
        return {
            "integration": integration,
            "status": "failed",
            "message": "Connection test could not complete. Review the integration settings.",
            "checked_at": datetime.datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        logger.warning("Recovery connection test failed for %s", integration)
        return {
            "integration": integration,
            "status": "failed",
            "message": "Connection test could not complete. Review the integration settings.",
            "checked_at": datetime.datetime.now(timezone.utc).isoformat(),
        }
    connected = (
        isinstance(result, dict) and (
            result.get("status") == "connected"
            or ((is_notification_test or integration == "trackers") and result.get("success") is True)
        )
    ) or (isinstance(result, JSONResponse) and result.status_code < 300)
    compatibility_mgr.record_integration_check(
        integration, "connected" if connected else "failed",
        result.get("version") if isinstance(result, dict) else None,
    )
    return {
        "integration": integration,
        "status": "connected" if connected else "failed",
        "message": "Service responded successfully." if connected else "Service did not respond successfully. Review its address, credentials, and availability.",
        "checked_at": datetime.datetime.now(timezone.utc).isoformat(),
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


@app.get("/static/dashboard.css")
def dashboard_stylesheet():
    """Serves dashboard styles extracted from the HTML template."""
    return Response(
        content=STYLESHEET_PATH.read_text(encoding="utf-8"),
        media_type="text/css",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/static/dashboard-theme.css")
def dashboard_theme_stylesheet():
    """Serves dashboard theme tokens, palettes, accents, and density rules."""
    return Response(
        content=THEME_STYLESHEET_PATH.read_text(encoding="utf-8"),
        media_type="text/css",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/static/dashboard.js")
def dashboard_script():
    """Serves the dashboard interaction code extracted from the HTML template."""
    return Response(
        content=SCRIPT_PATH.read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/metrics")
def get_metrics():
    """Prometheus exposition metrics endpoint."""
    uptime_seconds = time.time() - SERVER_START_TIME
    pending = 0 if queue_mgr._future_schema_version else queue_mgr.get_pending_count()
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


BACKUP_FORMAT_VERSION = 1
MAX_BACKUP_ENTRIES = 500
MAX_BACKUP_ARCHIVE_BYTES = 300 * 1024 * 1024
MAX_BACKUP_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
BACKUP_CATEGORIES = {
    "tracker credentials": lambda name: name == "trakt_tokens.json" or name.startswith("data/tokens/"),
    "settings": lambda name: name == "data/settings.json",
    "activity history": lambda name: name in {"data/events.json", "data/stats.json"},
    "offline queue": lambda name: name == "data/queue.db",
    "watch lists": lambda name: name == "data/watch_lists.json",
    "automation rules": lambda name: name == "data/automation_rules.json",
    "household data": lambda name: name in {"data/cowatch_shows.json", "data/cowatch_devices.json", "data/dashboard_accounts.json", "data/dashboard_accounts_audit.json"},
    "diary data": lambda name: name == "data/letterboxd_diary.json",
    "reconciliation history": lambda name: name == "data/cross_sync_provenance.json",
    "log archives": lambda name: _is_log_archive_path(name),
}


def _is_log_archive_path(name: str) -> bool:
    """Accept only the active Omniscrobble log and numeric RotatingFileHandler archives."""
    prefix = "data/logs/omniscrobble.log."
    if name == "data/logs/omniscrobble.log":
        return True
    suffix = name[len(prefix):] if name.startswith(prefix) else ""
    return bool(suffix) and suffix.isdigit()


def _decode_backup_archive(contents: bytes, passphrase: str = "") -> tuple[dict[str, bytes], dict[str, Any] | None, list[str]]:
    """Decrypt and validate a backup fully before any restore write is attempted."""
    if len(contents) > MAX_BACKUP_ARCHIVE_BYTES:
        raise HTTPException(status_code=400, detail="Backup exceeds the maximum upload size.")
    try:
        candidate = json.loads(contents.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        candidate = None

    from app.services.crypto_manager import decrypt_bytes, is_encrypted_payload
    if isinstance(candidate, dict) and is_encrypted_payload(candidate):
        if not passphrase:
            raise HTTPException(status_code=400, detail="Encrypted backup requires a passphrase to restore.")
        try:
            contents = decrypt_bytes(candidate, passphrase)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Decryption failed - incorrect passphrase or corrupted backup") from exc
        if len(contents) > MAX_BACKUP_ARCHIVE_BYTES:
            raise HTTPException(status_code=400, detail="Backup exceeds the maximum expanded size.")

    try:
        with zipfile.ZipFile(io.BytesIO(contents), "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_BACKUP_ENTRIES:
                raise HTTPException(status_code=400, detail="Backup contains too many entries.")
            if sum(info.file_size for info in infos) > MAX_BACKUP_UNCOMPRESSED_BYTES:
                raise HTTPException(status_code=400, detail="Backup exceeds the maximum expanded size.")
            corrupt = archive.testzip()
            if corrupt:
                raise HTTPException(status_code=400, detail="Backup archive failed its integrity check.")

            files: dict[str, bytes] = {}
            manifest: dict[str, Any] | None = None
            warnings: list[str] = []
            for info in infos:
                name = info.filename
                normalized = name.replace("\\", "/")
                path = PurePosixPath(normalized)
                if not normalized or path.is_absolute() or ".." in path.parts or "\\" in name or (path.parts and ":" in path.parts[0]):
                    raise HTTPException(status_code=400, detail="Backup contains an unsafe file path.")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise HTTPException(status_code=400, detail="Backup contains a symbolic link.")
                if info.is_dir():
                    continue
                if normalized in files:
                    raise HTTPException(status_code=400, detail="Backup contains duplicate file paths.")
                if normalized != "manifest.json" and normalized != "trakt_tokens.json" and not normalized.startswith("data/"):
                    raise HTTPException(status_code=400, detail="Backup contains an unsupported file.")
                raw = archive.read(info)
                if normalized.endswith(".json"):
                    try:
                        json.loads(raw.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise HTTPException(status_code=400, detail=f"Backup contains invalid JSON: {normalized}") from exc
                files[normalized] = raw

            if "manifest.json" in files:
                manifest = json.loads(files.pop("manifest.json").decode("utf-8"))
                if not isinstance(manifest, dict) or type(manifest.get("format_version")) is not int or manifest.get("format_version") != BACKUP_FORMAT_VERSION:
                    raise HTTPException(status_code=400, detail="Backup format version is not supported.")
                manifest_paths = manifest.get("files")
                if not isinstance(manifest_paths, list) or not all(isinstance(path, str) for path in manifest_paths) or len(manifest_paths) != len(files) or set(manifest_paths) != set(files):
                    raise HTTPException(status_code=400, detail="Backup manifest does not match archive contents.")
            else:
                warnings.append("Legacy backup has no version manifest; file structure was validated.")
            if not files:
                raise HTTPException(status_code=400, detail="Backup contains no restorable files.")
            return files, manifest, warnings
    except HTTPException:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Backup is not a valid, readable archive.") from exc


def _build_backup_restore_preview(files: dict[str, bytes], manifest: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
    archive_names = set(files)
    existing: set[str] = set()
    if Config.TRAKT_TOKENS_FILE.exists():
        existing.add("trakt_tokens.json")
    token_dir = Config.BASE_DIR / "data" / "tokens"
    if token_dir.exists():
        existing.update(f"data/tokens/{path.name}" for path in token_dir.glob("*.json"))
    known_targets = (
        "data/settings.json", "data/events.json", "data/stats.json", "data/queue.db",
        "data/watch_lists.json", "data/cowatch_shows.json", "data/cowatch_devices.json",
        "data/letterboxd_diary.json", "data/automation_rules.json",
        "data/cross_sync_provenance.json",
        "data/logs/omniscrobble.log",
    )
    existing.update(name for name in known_targets if (Config.BASE_DIR / name).exists())
    log_directory = Config.BASE_DIR / "data" / "logs"
    if log_directory.is_dir() and not log_directory.is_symlink():
        existing.update(
            path.relative_to(Config.BASE_DIR).as_posix()
            for path in log_directory.glob("omniscrobble.log.*")
            if path.is_file() and not path.is_symlink() and _is_log_archive_path(path.relative_to(Config.BASE_DIR).as_posix())
        )

    categories = {}
    for label, matches in BACKUP_CATEGORIES.items():
        replacing = sorted(name for name in archive_names if matches(name))
        preserved = sorted(name for name in existing - archive_names if matches(name))
        if replacing or preserved:
            categories[label] = {"replace": replacing, "preserve": preserved}

    source_version = manifest.get("app_version") if manifest else None
    return {
        "valid": True,
        "format_version": manifest.get("format_version") if manifest else None,
        "source_version": source_version,
        "source_created_at": manifest.get("created_at") if manifest else None,
        "current_version": APP_VERSION,
        "compatibility": "supported" if manifest else "legacy",
        "files": [
            {"path": name, "size_bytes": len(raw), "category": next((label for label, matches in BACKUP_CATEGORIES.items() if matches(name)), "other")}
            for name, raw in sorted(files.items())
        ],
        "categories": categories,
        "warnings": warnings,
    }


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
        watch_lists_file = Config.BASE_DIR / "data" / "watch_lists.json"
        if watch_lists_file.exists():
            zf.write(watch_lists_file, arcname="data/watch_lists.json")
        automation_rules_file = Config.BASE_DIR / "data" / "automation_rules.json"
        if automation_rules_file.exists():
            zf.write(automation_rules_file, arcname="data/automation_rules.json")
        for filename in ("dashboard_accounts.json", "dashboard_accounts_audit.json"):
            account_file = Config.BASE_DIR / "data" / filename
            if account_file.exists():
                zf.write(account_file, arcname=f"data/{filename}")
        diary_file = getattr(Config, "LETTERBOXD_DIARY_FILE", None)
        if diary_file and diary_file.exists():
            zf.write(diary_file, arcname="data/letterboxd_diary.json")
        provenance_file = Config.BASE_DIR / "data" / "cross_sync_provenance.json"
        if provenance_file.exists():
            zf.write(provenance_file, arcname="data/cross_sync_provenance.json")
        log_directory = Config.BASE_DIR / "data" / "logs"
        if log_directory.is_dir() and not log_directory.is_symlink():
            with log_mgr.archive_handler.lock:
                for path in sorted(log_directory.glob("omniscrobble.log*")):
                    archive_name = path.relative_to(Config.BASE_DIR).as_posix()
                    if path.is_file() and not path.is_symlink() and _is_log_archive_path(archive_name):
                        zf.write(path, arcname=archive_name)
        manifest = json.dumps({
            "format_version": BACKUP_FORMAT_VERSION,
            "app_version": APP_VERSION,
            "created_at": datetime.datetime.now(timezone.utc).isoformat(),
            "files": sorted(zf.namelist()),
        }, indent=2)
        if sum(info.file_size for info in zf.filelist) + len(manifest.encode("utf-8")) > MAX_BACKUP_UNCOMPRESSED_BYTES:
            raise HTTPException(status_code=413, detail="Backup exceeds the maximum expanded size.")
        zf.writestr("manifest.json", manifest)

    buffer.seek(0)
    zip_bytes = buffer.getvalue()
    if len(zip_bytes) > MAX_BACKUP_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="Backup exceeds the maximum download size.")
    key = passphrase or request.headers.get("x-backup-passphrase") or getattr(Config, "CONFIG_ENCRYPTION_KEY", "")

    if key:
        from app.services.crypto_manager import encrypt_bytes
        encrypted_dict = encrypt_bytes(zip_bytes, key)
        encrypted_json = json.dumps(encrypted_dict, indent=2).encode("utf-8")
        if len(encrypted_json) > MAX_BACKUP_ARCHIVE_BYTES:
            raise HTTPException(status_code=413, detail="Encrypted backup exceeds the maximum download size.")
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


@app.get("/api/backup/snapshots")
async def list_backup_snapshots(request: Request):
    """List locally retained backup snapshots without exposing their contents."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    directory = Config.BASE_DIR / "data" / "backups"
    snapshots = []
    if directory.is_dir():
        for path in sorted(directory.glob("omniscrobble-backup-*"), key=lambda item: item.stat().st_mtime, reverse=True):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                snapshots.append({"filename": path.name, "size_bytes": stat.st_size,
                                  "created_at": datetime.datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
    return {"snapshots": snapshots, "retention_count": Config.BACKUP_RETENTION_COUNT}


@app.post("/api/backup/snapshots")
async def create_backup_snapshot(request: Request):
    """Create a local backup and apply the configured bounded retention count."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    response = await export_backup(request)
    content_disposition = response.headers.get("Content-Disposition", "")
    filename = content_disposition.split('filename="')[-1].rstrip('"')
    directory = Config.BASE_DIR / "data" / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise HTTPException(status_code=400, detail="Backup directory must not be a symlink")
    timestamp = datetime.datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = ".json" if filename.endswith(".json") else ".zip"
    path = directory / f"omniscrobble-backup-{timestamp}{suffix}"
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as snapshot_file:
            snapshot_file.write(response.body)
            snapshot_file.flush()
            os.fsync(snapshot_file.fileno())
        os.replace(temporary, path)
        snapshots = sorted((p for p in directory.glob("omniscrobble-backup-*") if p.is_file() and not p.is_symlink()),
                           key=lambda item: item.stat().st_mtime, reverse=True)
        retention_count = Config.BACKUP_RETENTION_COUNT
        for stale in snapshots[retention_count:]:
            stale.unlink()
    finally:
        temporary.unlink(missing_ok=True)
    return {"created": path.name, "retained": min(len(snapshots), retention_count), "retention_count": retention_count}


@app.post("/api/restore/preview")
async def preview_backup_restore(request: Request):
    """Validate an uploaded backup and report what would be replaced or preserved."""
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
    files, manifest, warnings = _decode_backup_archive(contents, str(passphrase))
    return _build_backup_restore_preview(files, manifest, warnings)


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

    files, _manifest, _warnings = _decode_backup_archive(contents, str(passphrase))

    base_path = Config.BASE_DIR.resolve()
    target_paths = {}
    for name in files:
        target_path = (base_path / name).resolve()
        if target_path != base_path and base_path not in target_path.parents:
            raise HTTPException(status_code=400, detail="Restore target resolves outside the application data directory.")
        target_paths[name] = target_path

    restored_logs = [name for name in files if _is_log_archive_path(name)]
    log_directory = base_path / "data" / "logs"
    if restored_logs:
        if log_directory.is_symlink() or any((base_path / name).is_symlink() for name in restored_logs):
            raise HTTPException(status_code=400, detail="Log archive restore targets must not be symbolic links.")
    restored_files = []
    try:
        with log_mgr.archive_handler.lock if restored_logs else nullcontext():
            if restored_logs and log_mgr.archive_handler.stream:
                log_mgr.archive_handler.flush()
                log_mgr.archive_handler.stream.close()
                log_mgr.archive_handler.stream = None
            for name, payload in files.items():
                target_path = target_paths[name]
                is_log_archive = _is_log_archive_path(name)
                target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if is_log_archive else 0o755)
                if is_log_archive:
                    os.chmod(target_path.parent, 0o700)
                    descriptor = os.open(target_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(descriptor, "wb") as target:
                        target.write(payload)
                else:
                    with open(target_path, "wb") as target:
                        target.write(payload)
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
        log_archive_settings = settings_mgr.get_log_archive_settings()
        log_mgr.reconfigure_archive(**log_archive_settings)
        automation_rules_mgr._load()
        dashboard_auth_mgr.reload_accounts()
        if "data/queue.db" in files:
            queue_mgr.reload_schema()
        cross_tracker_sync._provenance = cross_tracker_sync._load_provenance()
        watch_list_mgr._document = watch_list_mgr._load()
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
    principal = get_dashboard_principal(request)
    is_admin = bool(principal and principal.role == "admin")
    raw_events = list(recent_events)
    if principal and principal.role == "member":
        raw_events = [event for event in raw_events if str(event.get("user") or "").strip().lower() == principal.username.lower()]
    total_count = len(raw_events)
    if principal and principal.role == "member":
        events = [
            {
                "event_id": ev.get("event_id"),
                "operation_id": ev.get("operation_id"),
                "timestamp": ev.get("timestamp"),
                "user": ev.get("user"),
                "server": ev.get("server"),
                "event": ev.get("event"),
                "action": ev.get("action"),
                "title": ev.get("title"),
                "type": ev.get("type"),
                "show_title": ev.get("show_title"),
                "progress": ev.get("progress"),
                "is_anime": bool(ev.get("is_anime", False)),
                "tracker_delivery": dict(ev.get("tracker_delivery") or {}),
                "tracker_delivery_details": dict(ev.get("tracker_delivery_details") or {}),
                "delivery_status": ev.get("delivery_status"),
                "result_status": ev.get("result_status"),
                "rule_evaluation": {
                    key: value
                    for key, value in (ev.get("rule_evaluation") or {}).items()
                    if key not in {"profiles", "trackers"}
                },
            }
            for ev in raw_events
        ]
    elif not is_admin:
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
                "is_anime": bool(ev.get("is_anime", False)),
                "tracker_delivery": {
                    str(tracker): state
                    for tracker, state in (ev.get("tracker_delivery") or {}).items()
                    if tracker in {"trakt", "simkl", "anilist", "mal", "kitsu", "tmdb", "letterboxd", "serializd", "mdblist"}
                    and state in {"success", "queued", "failed", "skipped"}
                },
                "delivery_status": ev.get("delivery_status"),
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


@app.post("/api/events/{event_id}/retry")
async def retry_event_delivery(event_id: str, request: Request):
    """Retry one failed queued destination operation linked to an activity event."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    try:
        payload = await request.json()
        queue_item_id = int(payload.get("queue_item_id"))
        confirm_duplicate_history = payload.get("confirm_duplicate_history") is True
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="A valid queue_item_id is required")

    status = queue_mgr.retry_failed_item(queue_item_id, event_id, confirm_duplicate_history)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Failed queue item was not found for this activity event")
    if status == "not_failed":
        raise HTTPException(status_code=409, detail="Only failed queue items can be retried")
    if status == "confirmation_required":
        raise HTTPException(status_code=409, detail="Retry may duplicate watch history; explicit confirmation is required")
    if status == "unsupported":
        raise HTTPException(status_code=400, detail="This queued operation does not support manual retry")
    if status == "not_retryable":
        raise HTTPException(status_code=409, detail="This failure is not transient; fix the integration or credentials before retrying")

    item = queue_mgr.get_item(queue_item_id)
    if item:
        record_queue_delivery(
            event_id,
            item["event_type"],
            queue_item_id,
            "queued",
            "Manually queued for retry.",
            int(item.get("retry_count", 0)) + 2,
        )
    return {"status": "queued", "event_id": event_id, "queue_item_id": queue_item_id}


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
    res = await process_queue(trakt, queue_mgr, user_mgr=user_mgr, on_result=record_queue_delivery, simkl_client=simkl)
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
    principal = get_dashboard_principal(request)
    member_profile = principal.username if principal and principal.role == "member" else None
    return {
        "active_sessions": playback_mgr.get_active_sessions(is_admin=is_admin, profile_username=member_profile),
        "recently_finished": playback_mgr.get_recently_finished(is_admin=is_admin, profile_username=member_profile),
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
    result = await log_mgr.get_logs(lines=lines, source=source)
    result["archive_settings"] = log_mgr.get_archive_settings()
    return result


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
    logs: Optional[dict[str, int]] = None
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


class NotificationRoutePreviewRequest(BaseModel):
    event: str

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
    else:
        all_s["notification_profiles"] = list(Config.PLEX_ALLOWED_USERS or [])
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

    if payload.logs is not None:
        try:
            archive = settings_mgr.get_log_archive_settings()
            log_mgr.reconfigure_archive(
                max_bytes=archive["max_bytes"],
                backup_count=archive["backup_count"],
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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


@app.post("/api/notifications/preview-route")
async def preview_notification_route_endpoint(payload: NotificationRoutePreviewRequest, request: Request):
    """Send a sample alert through one saved event route without creating a media event."""
    if request.query_params.get("demo") == "true":
        return {"status": "success", "success": True, "message": "Demo route preview delivered."}
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    from app.services.settings_manager import NOTIFICATION_ROUTE_EVENTS
    if payload.event not in NOTIFICATION_ROUTE_EVENTS:
        raise HTTPException(status_code=400, detail="Unknown notification event")
    route = settings_mgr.get_notifications(mask=False).get("notification_routes", {}).get(payload.event, {})
    delivered = await notifier.send_operational_notification(
        payload.event,
        f"Omniscrobble route preview: {payload.event.replace('_', ' ').title()}",
        "Sample notification. This preview used the saved destinations and severity for this event.",
        severity="normal",
    )
    return {
        "status": "success" if delivered else "warning",
        "success": delivered,
        "message": "Route preview delivered." if delivered else "No configured selected channel accepted the route preview.",
    }


@app.post("/api/notifications/digest")
async def trigger_weekly_digest_endpoint(request: Request):
    """Trigger an immediate dispatch of the weekly activity digest."""
    is_demo = request.query_params.get("demo") == "true"
    if is_demo:
        return await digest_mgr.send_digest(demo=True)
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    return await digest_mgr.send_digest()


class AutomationEvaluationRequest(BaseModel):
    event: dict[str, Any]


class AutomationRulesPreviewRequest(BaseModel):
    rules: list[dict[str, Any]]
    event: dict[str, Any]


class AutomationReviewDecision(BaseModel):
    decision: str


@app.get("/api/automation/rules")
def get_automation_rules(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Admin access is required to view automation rules.")
    return {"rules": automation_rules_mgr.list_rules(), "default_action": "allow", "precedence": "lowest numeric priority first; ties use saved order"}


@app.put("/api/automation/rules")
def replace_automation_rules(payload: list[dict[str, Any]], request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Admin access is required to change automation rules.")
    try:
        rules = automation_rules_mgr.replace_rules(payload)
    except AutomationRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "rules": rules, "default_action": "allow"}


@app.post("/api/automation/rules/evaluate")
def evaluate_automation_rules(payload: AutomationEvaluationRequest, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Admin access is required to evaluate automation rules.")
    return automation_rules_mgr.evaluate(payload.event)


@app.post("/api/automation/rules/preview")
def preview_automation_rules(payload: AutomationRulesPreviewRequest, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Admin access is required to preview automation rules.")
    try:
        proposed = AutomationRulesManager.validate_rules(payload.rules)
    except AutomationRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    current_result = automation_rules_mgr.evaluate(payload.event)
    proposed_result = automation_rules_mgr.evaluate_rules(payload.event, proposed)
    return {
        "current": current_result,
        "proposed": proposed_result,
        "decision_changes": current_result.get("decision") != proposed_result.get("decision"),
        "writes_applied": False,
    }


@app.get("/api/automation/review")
def list_automation_review_events(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Admin access is required to view the review queue.")
    pending = [
        {
            "event_id": event.get("event_id"),
            "timestamp": event.get("timestamp"),
            "user": event.get("user"),
            "server": event.get("server"),
            "event": event.get("event"),
            "title": event.get("title"),
            "type": event.get("type"),
            "rule_evaluation": event.get("rule_evaluation"),
        }
        for event in recent_events
        if event.get("review_status") == "pending" and event.get("review_payload")
    ]
    return {"pending": pending, "total": len(pending)}


@app.post("/api/automation/review/{event_id}")
async def decide_automation_review(event_id: str, payload: AutomationReviewDecision, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Admin access is required to resolve review events.")
    decision = str(payload.decision or "").lower().strip()
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Decision must be approve or reject.")
    target_event = next((event for event in recent_events if event.get("event_id") == event_id), None)
    if not target_event or target_event.get("review_status") != "pending" or not target_event.get("review_payload"):
        raise HTTPException(status_code=404, detail="Pending review event not found.")
    if decision == "reject":
        target_event["review_status"] = "rejected"
        target_event["reviewed_at"] = datetime.datetime.now(timezone.utc).isoformat()
        target_event["action"] = "review_rejected"
        target_event["result_status"] = "ignored"
        save_recent_events()
        return {"status": "rejected", "event_id": event_id}
    try:
        media = ParsedMedia.model_validate(target_event["review_payload"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Stored review event cannot be reconstructed safely.") from exc
    # Claim synchronously before dispatch yields; a second approval cannot pass pending.
    target_event["review_status"] = "processing"
    try:
        atomic_write_json(EVENTS_FILE, list(recent_events))
        replay_result = await process_media_event(media, endpoint_name=media.server_type, skip_automation_rules=True)
    except BaseException:
        target_event["review_status"] = "pending"
        save_recent_events()
        raise
    target_event["review_status"] = "approved"
    target_event["reviewed_at"] = datetime.datetime.now(timezone.utc).isoformat()
    target_event["approved_event_id"] = replay_result.get("event_id")
    target_event["action"] = "review_approved"
    target_event["result_status"] = "approved"
    save_recent_events()
    return {"status": "approved", "event_id": event_id, "result": replay_result}


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
        watched_at = datetime.datetime.now(timezone.utc).isoformat()
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
            watched_at=watched_at,
        )

        retry_payloads = dispatch_res.pop("retry_payloads", {})
        retryable_trackers = set(dispatch_res.get("retryable_trackers") or [])
        error_codes = dispatch_res.get("tracker_error_codes") or {}
        queued_trackers = list(dispatch_res.get("queued_trackers") or [])
        for tracker in retryable_trackers:
            if error_codes.get(tracker) == 429 and tracker not in queued_trackers:
                queued_trackers.append(tracker)
        dispatch_res["queued_trackers"] = queued_trackers
        dispatch_res["status"] = _aggregate_delivery_status(
            _tracker_delivery_from_result(dispatch_res, "manual_scrobble")
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
        event_id = log_event(media_obj, "manual_scrobble", dispatch_res, cowatch_status=cowatch_status)

        for tracker in retryable_trackers:
            if tracker == "trakt":
                event_type = "sync_history"
                queue_payload = retry_payloads["trakt"]
            elif tracker == "simkl":
                event_type = "simkl_sync_history"
                queue_payload = {"media": media_obj.model_dump(mode="json"), "watched_at": watched_at}
            else:
                continue

            rate_limited = error_codes.get(tracker) == 429
            reason = str((dispatch_res.get("errors") or {}).get(tracker) or "Temporary tracker failure")
            queue_status = "pending" if rate_limited else "failed"
            if not rate_limited:
                reason = "Outcome uncertain after manual delivery; retry may duplicate watch history."
            queue_item_id = queue_mgr.enqueue(
                event_type,
                queue_payload,
                error=reason,
                event_id=event_id,
                status=queue_status,
            )
            record_queue_delivery(
                event_id,
                event_type,
                queue_item_id,
                "queued" if rate_limited else "failed",
                "Rate limited; saved for automatic retry." if rate_limited else reason,
                1,
            )

        synced_list = dispatch_res.get("synced_trackers", ["trakt"])
        asyncio.create_task(
            notifier.dispatch(
                media_obj,
                "mark_watched",
                cowatch_partner=partner_user if cowatch_synced else None,
                trackers=synced_list,
            )
        )
        return {
            "status": dispatch_res.get("status", "success"),
            "action": "watched",
            "result": dispatch_res,
            "cowatch_synced": cowatch_synced,
            "event_id": event_id,
        }


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


class WatchListCreateRequest(BaseModel):
    name: str


class WatchListItemRequest(BaseModel):
    title: str
    media_type: str
    year: Optional[int] = None
    ids: dict[str, Any] = Field(default_factory=dict)
    poster_url: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class WatchListItemUpdateRequest(BaseModel):
    title: Optional[str] = None
    media_type: Optional[str] = None
    year: Optional[int] = None
    ids: Optional[dict[str, Any]] = None
    poster_url: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None


class WatchListOrderRequest(BaseModel):
    item_ids: list[str]


class WatchListImportRequest(BaseModel):
    data: Any
    replace: bool = False


def _watch_list_operation(operation):
    try:
        return operation()
    except WatchListError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _watch_list_actor(request: Request, *, mutate: bool = False):
    permission = "manage_shared_watch_lists" if mutate else "view_shared_watch_lists"
    principal = require_dashboard_permission(request, permission)
    if mutate and principal.source == "local_account":
        session_token = request.cookies.get("dashboard_session", "")
        csrf_cookie = request.cookies.get("csrf_token", "")
        csrf_header = request.headers.get("x-csrf-token", "")
        if (request.headers.get("sec-fetch-site") == "cross-site"
                or not dashboard_auth_mgr.session_has_csrf(session_token, csrf_cookie)
                or not csrf_cookie or not secrets.compare_digest(csrf_cookie, csrf_header)):
            raise HTTPException(status_code=403, detail="CSRF validation failed.")
    return principal


def _require_watch_list_access(list_id: str, principal, *, edit: bool = False, manage: bool = False) -> str:
    try:
        role = watch_list_mgr.access_role(list_id, principal.username, is_admin=principal.role == "admin")
    except WatchListError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    allowed = {"admin", "owner"} if manage else {"admin", "owner", "editor"} if edit else {"admin", "owner", "editor", "viewer"}
    if role not in allowed:
        raise HTTPException(status_code=404, detail="Watch list not found")
    return role


def get_watch_lists(request: Request):
    principal = _watch_list_actor(request)
    return {**watch_list_mgr.get_all_for(principal.username, is_admin=principal.role == "admin"),
            "can_create": True, "can_transfer": principal.role == "admin",
            "can_request": bool(principal.role == "admin" and settings_mgr.is_overseerr_enabled() and arr_bridge.overseerr.is_configured),
            "request_service_name": "Jellyseerr" if "jellyseerr" in arr_bridge.overseerr.base_url.lower() else "Overseerr",
            "automation_options": ["manual"]
                + (["request"] if settings_mgr.is_overseerr_enabled() and arr_bridge.overseerr.is_configured else [])
                + (["acquire"] if arr_bridge.sonarr.is_configured or arr_bridge.radarr.is_configured else []),
            "cowatch_available": bool(Config.CO_WATCH_USER)}


async def search_watch_list_anime(title: str, request: Request, year: Optional[int] = None):
    _watch_list_actor(request)
    clean_title = title.strip()
    if not clean_title or len(clean_title) > 200:
        raise HTTPException(status_code=400, detail="A title of 1 to 200 characters is required")
    match = await anilist.search_anime(clean_title, year=year)
    if not match:
        return {"match": None}
    return {"match": {"title": match.get("title_preferred") or clean_title,
                       "media_type": "anime", "year": match.get("year"),
                       "ids": {"anilist": match.get("id"), "mal": match.get("idMal")},
                       "poster_url": match.get("cover_image")}}


def create_watch_list(payload: WatchListCreateRequest, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    return _watch_list_operation(lambda: watch_list_mgr.create_list(payload.name, owner=principal.username))


def rename_watch_list(list_id: str, payload: WatchListCreateRequest, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal, manage=True)
    return _watch_list_operation(lambda: watch_list_mgr.rename_list(list_id, payload.name))


def delete_watch_list(list_id: str, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal, manage=True)
    _watch_list_operation(lambda: watch_list_mgr.delete_list(list_id))
    return {"status": "ok"}


async def add_watch_list_item(list_id: str, payload: WatchListItemRequest, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal, edit=True)
    item = _watch_list_operation(lambda: watch_list_mgr.add_item(list_id, payload.model_dump()))
    watch_list = watch_list_mgr._find(list_id)
    if watch_list.get("auto_action", "manual") != "manual":
        automation = await _run_watch_list_auto_action(watch_list["auto_action"], item)
        item = _watch_list_operation(lambda: watch_list_mgr.set_item_automation(
            list_id, item["id"], {**automation, "checked_at": datetime.datetime.now(timezone.utc).isoformat()}
        ))
    return item


def reorder_watch_list_items(list_id: str, payload: WatchListOrderRequest, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal, edit=True)
    return _watch_list_operation(lambda: watch_list_mgr.reorder(list_id, payload.item_ids))


def delete_watch_list_item(list_id: str, item_id: str, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal, edit=True)
    _watch_list_operation(lambda: watch_list_mgr.remove_item(list_id, item_id))
    return {"status": "ok"}


def update_watch_list_item(list_id: str, item_id: str, payload: WatchListItemUpdateRequest, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal, edit=True)
    return _watch_list_operation(lambda: watch_list_mgr.update_item(list_id, item_id, payload.model_dump(exclude_unset=True)))


class WatchListAccessRequest(BaseModel):
    members: dict[str, str] = Field(default_factory=dict)


class WatchListAutomationRequest(BaseModel):
    action: Literal["manual", "request", "acquire"]


def set_watch_list_access(list_id: str, payload: WatchListAccessRequest, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal, manage=True)
    for username in payload.members:
        account = dashboard_auth_mgr.get_account(username)
        if not account or not account.get("enabled") or account.get("role") != "member":
            raise HTTPException(status_code=400, detail=f"'{username}' is not an enabled member account")
    return _watch_list_operation(lambda: watch_list_mgr.set_members(list_id, payload.members))


def set_watch_list_automation(list_id: str, payload: WatchListAutomationRequest, request: Request):
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal, manage=True)
    if payload.action == "request" and not (settings_mgr.is_overseerr_enabled() and arr_bridge.overseerr.is_configured):
        raise HTTPException(status_code=409, detail="Enable and configure Overseerr/Jellyseerr before selecting automatic requests")
    if payload.action == "acquire" and not (arr_bridge.sonarr.is_configured or arr_bridge.radarr.is_configured):
        raise HTTPException(status_code=409, detail="Configure Sonarr or Radarr before selecting automatic acquisition")
    return _watch_list_operation(lambda: watch_list_mgr.set_auto_action(list_id, payload.action))


async def _run_watch_list_auto_action(action: str, item: dict[str, Any]) -> dict[str, str]:
    """Run an explicitly owner-enabled action without undoing a successful list addition."""
    ids = item.get("ids") or {}
    if action == "request":
        try:
            tmdb_id = int(ids.get("tmdb"))
            if tmdb_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return {"status": "skipped", "reason": "A TMDb ID is required"}
        if not (settings_mgr.is_overseerr_enabled() and arr_bridge.overseerr.is_configured):
            return {"status": "skipped", "reason": "Overseerr/Jellyseerr is not enabled and configured"}
        media_type = "movie" if item["media_type"] == "movie" else "tv"
        try:
            if await arr_bridge.overseerr.has_media(media_type, tmdb_id):
                return {"status": "skipped", "reason": "Already requested or available"}
            result = await arr_bridge.overseerr.request_media(media_type, tmdb_id, seasons="all" if media_type == "tv" else None)
            if result.get("skipped"):
                return {"status": "skipped", "reason": "Already requested or available"}
            if result.get("success"):
                return {"status": "requested", "reason": "Approval is managed by Overseerr/Jellyseerr"}
            return {"status": "failed", "reason": "The request service did not accept this item"}
        except Exception:
            logger.warning("Automatic watch-list request failed")
            return {"status": "failed", "reason": "The request service could not be reached"}

    if action == "acquire":
        is_movie = item["media_type"] == "movie"
        media_type = "movie" if is_movie else "series"
        client = arr_bridge.radarr if is_movie else arr_bridge.sonarr
        provider_key = "tmdb" if is_movie else "tvdb"
        native_key = "tmdbId" if is_movie else "tvdbId"
        try:
            target_id = ids.get(provider_key)
            if target_id is None and ids.get("imdb") is None:
                return {"status": "skipped", "reason": "A matching provider ID is required"}
            if not client.is_configured:
                return {"status": "skipped", "reason": f"{'Radarr' if is_movie else 'Sonarr'} is not configured"}
            candidates = await arr_bridge.lookup_media(media_type, item["title"])
            match = next((candidate for candidate in candidates if
                          (target_id is not None and _id_matches(target_id, candidate.get(native_key)))
                          or _id_matches(ids.get("imdb"), candidate.get("imdbId"))), None)
            if not match:
                return {"status": "skipped", "reason": "No exact catalog match was found"}
            already_added = await (arr_bridge.radarr.has_movie(tmdb_id=ids.get("tmdb"), imdb_id=ids.get("imdb"))
                                   if is_movie else arr_bridge.sonarr.has_series(tvdb_id=ids.get("tvdb"), imdb_id=ids.get("imdb")))
            if already_added:
                return {"status": "skipped", "reason": "Already in the library"}
            settings = settings_mgr.get_arr_settings(mask=False)
            service = "radarr" if is_movie else "sonarr"
            root = settings.get(f"{service}_root_folder")
            profile = settings.get(f"{service}_quality_profile_id")
            if not root or not profile:
                return {"status": "skipped", "reason": f"Set the {service.title()} root folder and quality profile first"}
            result = await arr_bridge.acquire_media(
                media_type, match, root_folder_path=root, quality_profile_id=profile,
                monitored=True, search_now=bool(settings.get("search_on_add", True)), monitor_option="all",
            )
            if result.get("success"):
                return {"status": "acquired", "reason": f"Added to {service.title()} using its configured defaults"}
            return {"status": "failed", "reason": f"{service.title()} could not add the item"}
        except Exception:
            logger.warning("Automatic watch-list acquisition failed (service=%s)", "Radarr" if is_movie else "Sonarr")
            return {"status": "failed", "reason": "The library service could not complete the acquisition"}
    return {"status": "skipped", "reason": "Automatic actions are disabled"}


def _id_matches(item_id: Any, library_id: Any) -> bool:
    return bool(item_id is not None and library_id is not None and str(item_id).strip().casefold() == str(library_id).strip().casefold())


async def refresh_watch_list_availability(list_id: str, request: Request):
    """Refresh per-item status from configured Sonarr, Radarr, and Overseerr services."""
    principal = _watch_list_actor(request, mutate=True)
    _require_watch_list_access(list_id, principal)
    watch_list = watch_list_mgr._find(list_id)
    items = watch_list.get("items", [])
    sonarr_items: list[dict[str, Any]] = []
    radarr_items: list[dict[str, Any]] = []
    sonarr_state = "not_configured"
    radarr_state = "not_configured"
    if arr_bridge.sonarr.is_configured:
        sonarr_state = "unavailable"
        try:
            if (await arr_bridge.sonarr.check_connection()).get("status") == "connected":
                sonarr_items = await arr_bridge.sonarr.get_series()
                sonarr_state = "connected"
        except Exception:
            logger.warning("Sonarr availability check failed")
    if arr_bridge.radarr.is_configured:
        radarr_state = "unavailable"
        try:
            if (await arr_bridge.radarr.check_connection()).get("status") == "connected":
                radarr_items = await arr_bridge.radarr.get_movies()
                radarr_state = "connected"
        except Exception:
            logger.warning("Radarr availability check failed")

    request_enabled = bool(settings_mgr.is_overseerr_enabled() and arr_bridge.overseerr.is_configured)
    overseerr_state = "not_configured"
    if request_enabled:
        overseerr_state = "unavailable"
        try:
            if (await arr_bridge.overseerr.check_connection()).get("status") == "connected":
                overseerr_state = "connected"
        except Exception:
            logger.warning("Overseerr availability check failed")

    async def lookup_request_status(item: dict[str, Any], semaphore: asyncio.Semaphore) -> tuple[str, str]:
        ids = item.get("ids") or {}
        try:
            tmdb_id = int(ids.get("tmdb"))
            if tmdb_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return item["id"], "unknown"
        async with semaphore:
            try:
                details = await arr_bridge.overseerr.get_media_details(
                    "movie" if item.get("media_type") == "movie" else "tv", tmdb_id
                )
                media_info = (details or {}).get("mediaInfo") or {}
                status = media_info.get("status")
                return item["id"], {2: "requested", 3: "requested", 4: "partially_available", 5: "available"}.get(status, "missing")
            except Exception:
                logger.warning("Overseerr availability lookup failed for a watch-list item")
                return item["id"], "unavailable"

    request_results: dict[str, str] = {}
    if request_enabled and overseerr_state == "connected":
        request_semaphore = asyncio.Semaphore(5)
        # Keep both the number of live requests and each gather batch bounded for large lists.
        for batch_start in range(0, len(items), 100):
            batch = [item for item in items[batch_start:batch_start + 100] if (item.get("ids") or {}).get("tmdb")]
            results = await asyncio.gather(*(lookup_request_status(item, request_semaphore) for item in batch))
            request_results.update(results)

    checked_at = datetime.datetime.now(timezone.utc).isoformat()
    snapshots: dict[str, dict[str, Any]] = {}
    for item in items:
        ids = item.get("ids") or {}
        media_type = item.get("media_type")
        is_movie = media_type == "movie"
        service_name = "radarr" if is_movie else "sonarr"
        service_state = radarr_state if is_movie else sonarr_state
        catalog = radarr_items if is_movie else sonarr_items
        provider_id = "tmdb" if is_movie else "tvdb"
        native_key = "tmdbId" if is_movie else "tvdbId"
        matching = next((entry for entry in catalog if
            (_id_matches(ids.get(provider_id), entry.get(native_key))
             or _id_matches(ids.get("imdb"), entry.get("imdbId")))), None)
        if service_state == "not_configured":
            service_result = "not_configured"
        elif service_state == "unavailable":
            service_result = "unavailable"
        elif matching:
            stats = matching.get("statistics") or {}
            has_files = bool(matching.get("hasFile") or stats.get("episodeFileCount", 0))
            service_result = "available" if has_files else "in_library"
        else:
            has_matching_id = bool(ids.get(provider_id) or ids.get("imdb"))
            service_result = "missing" if has_matching_id else "unknown"

        if not request_enabled:
            request_result = "not_configured"
        elif overseerr_state != "connected":
            request_result = "unavailable"
        else:
            request_result = request_results.get(item["id"], "unknown")

        candidates = [service_result, request_result]
        status = next((state for state in ("available", "partially_available", "requested", "in_library") if state in candidates), None)
        if status is None:
            status = "missing" if "missing" in candidates else "unknown"
        snapshots[item["id"]] = {
            "status": status,
            "checked_at": checked_at,
            "services": {service_name: service_result, "overseerr": request_result},
        }

    saved = _watch_list_operation(lambda: watch_list_mgr.set_availability(list_id, snapshots))
    return {"status": "ok", "checked_count": len(snapshots), "checked_at": checked_at,
            "availability": {item["id"]: item.get("availability") for item in saved["items"]}}


async def request_watch_list_item(list_id: str, item_id: str, request: Request):
    """Submit one selected movie/show to the configured Overseerr/Jellyseerr instance."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    csrf_cookie = request.cookies.get("csrf_token", "")
    csrf_header = request.headers.get("x-csrf-token", "")
    if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
    try:
        item = next(item for item in watch_list_mgr._find(list_id)["items"] if item["id"] == item_id)
    except (WatchListError, StopIteration) as exc:
        raise HTTPException(status_code=404, detail="Watch-list item not found") from exc
    if not (settings_mgr.is_overseerr_enabled() and arr_bridge.overseerr.is_configured):
        raise HTTPException(status_code=409, detail="Overseerr/Jellyseerr requests are not enabled and configured")
    tmdb_id = (item.get("ids") or {}).get("tmdb")
    try:
        tmdb_id = int(tmdb_id)
        if tmdb_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="This item needs a TMDb ID before it can be requested")
    media_type = "movie" if item["media_type"] == "movie" else "tv"
    if await arr_bridge.overseerr.has_media(media_type, tmdb_id):
        return {"status": "skipped", "reason": "Already requested, processing, or available"}
    result = await arr_bridge.overseerr.request_media(media_type, tmdb_id, seasons="all" if media_type == "tv" else None)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail="The request service could not accept this item. Check its status and request permissions.")
    if result.get("skipped"):
        return {"status": "skipped", "reason": "Already requested or available"}
    return {"status": "requested", "service": "Jellyseerr" if "jellyseerr" in arr_bridge.overseerr.base_url.lower() else "Overseerr",
            "approval_flow": "managed_by_request_service"}


def preview_watch_list_import(payload: WatchListImportRequest, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    return _watch_list_operation(lambda: watch_list_mgr.preview_import(payload.data, payload.replace))


def import_watch_lists(payload: WatchListImportRequest, request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    return _watch_list_operation(lambda: watch_list_mgr.apply_import(payload.data, payload.replace))


def export_watch_lists(request: Request, format: str = "json"):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
    if format == "json":
        content, media_type, filename = json.dumps(watch_list_mgr.get_all(), indent=2, ensure_ascii=False), "application/json", "watch-lists.json"
    elif format == "txt":
        content, media_type, filename = watch_list_mgr.export_text(), "text/plain; charset=utf-8", "watch-lists.txt"
    else:
        raise HTTPException(status_code=400, detail="format must be json or txt")
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Admin access is required to inspect household tracker status.")
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


async def get_sync_settings(request: Request):
    return await reconciliation_settings_handlers.get_settings(request)


async def save_sync_settings(payload: ReconcileSettingsRequest, request: Request):
    return await reconciliation_settings_handlers.save_settings(payload, request)


async def test_sync_connection(payload: MediaServerTestConnectionRequest, request: Request):
    return await reconciliation_settings_handlers.test_connection(payload, request)


async def get_sync_status(request: Request, server: Optional[str] = None):
    """Return status of two-way sync and media server direct connection."""
    is_demo = request.query_params.get("demo") == "true"
    return await reverse_sync_mgr.get_status(demo=is_demo, server=server)


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


async def preview_reconciliation(payload: ReconcileRequest, request: Request):
    return await reconciliation_workflow_handlers.preview(payload, request)


async def trigger_reconciliation(payload: ReconcileRequest, request: Request):
    return await reconciliation_workflow_handlers.execute(payload, request)


async def get_sync_progress(request: Request):
    return await reconciliation_workflow_handlers.get_progress(request)


async def get_background_sync_status(request: Request):
    return await reconciliation_workflow_handlers.get_background_status(request)


async def trigger_background_sync(request: Request):
    return await reconciliation_workflow_handlers.trigger_background_sync(request)


async def register_server_webhook(payload: RegisterWebhookRequest, request: Request):
    return await media_server_webhook_handlers.register(payload, request)


async def lookup_arr_media(request: Request, type: str, term: str):
    return await acquisition_handlers.lookup_media(request, type, term)


async def get_arr_acquisition_config(request: Request):
    return await acquisition_handlers.get_config(request)


async def add_arr_media(payload: ArrAddRequest, request: Request):
    return await acquisition_handlers.add_media(payload, request)


async def test_arr_connection(payload: ArrTestConnectionRequest, request: Request):
    return await acquisition_handlers.test_connection(payload, request)


async def get_arr_status(request: Request):
    return await acquisition_handlers.get_status(request)


async def trigger_arr_sync(request: Request):
    return await acquisition_handlers.trigger_sync(request)


async def get_ecosystem_status(request: Request):
    return await acquisition_handlers.get_ecosystem_status(request)


def invalidate_arr_acquisition_config_cache() -> None:
    acquisition_handlers.invalidate_config_cache()


async def get_cross_sync_status(request: Request):
    return await cross_tracker_sync_handlers.get_status(request)


async def get_cross_sync_diff(request: Request, force: bool = False):
    return await cross_tracker_sync_handlers.get_diff(request, force=force)


async def scan_cross_sync_discrepancies(request: Request):
    return await cross_tracker_sync_handlers.scan(request)


async def execute_cross_sync(payload: CrossSyncExecuteRequest, request: Request):
    return await cross_tracker_sync_handlers.execute(payload, request)


async def get_cross_sync_progress(request: Request):
    return await cross_tracker_sync_handlers.get_progress(request)


async def get_cross_sync_provenance(request: Request, limit: int = 100):
    return await cross_tracker_sync_handlers.get_provenance(request, limit=limit)


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


def admin_lock(request: Request, response: Response):
    dashboard_auth_mgr.revoke_session(request.cookies.get("dashboard_session", ""))
    response.delete_cookie(key="admin_token")
    response.delete_cookie(key="dashboard_session", path="/")
    response.delete_cookie(key="csrf_token")
    return {"status": "ok", "message": "Admin mode locked"}


async def auth_start(request: Request, user: Optional[str] = None):
    return await tracker_account_handlers.auth_start(request, user)


async def auth_poll(payload: DevicePollRequest, request: Request):
    return await tracker_account_handlers.auth_poll(payload, request)


async def get_trakt_status(request: Request, user: Optional[str] = None):
    return await tracker_account_handlers.get_trakt_status(request, user)


async def disconnect_trakt(request: Request, user: Optional[str] = None):
    return await tracker_account_handlers.disconnect_trakt(request, user)


async def get_simkl_status(request: Request):
    return await tracker_account_handlers.get_simkl_status(request)


async def get_simkl_pin(request: Request):
    return await tracker_account_handlers.get_simkl_pin(request)


async def poll_simkl_pin(payload: SimklPollRequest, request: Request):
    return await tracker_account_handlers.poll_simkl_pin(payload, request)


async def disconnect_simkl(request: Request):
    return await tracker_account_handlers.disconnect_simkl(request)


async def auth_simkl_page(request: Request):
    return await tracker_account_handlers.auth_simkl_page(request)


async def get_anilist_status(request: Request):
    return await tracker_account_handlers.get_anilist_status(request)


async def save_anilist_token(payload: TokenSubmitRequest, request: Request):
    return await tracker_account_handlers.save_anilist_token(payload, request)


async def disconnect_anilist(request: Request):
    return await tracker_account_handlers.disconnect_anilist(request)


async def auth_anilist_page(request: Request):
    return await tracker_account_handlers.auth_anilist_page(request)


async def get_mal_status(request: Request):
    return await tracker_account_handlers.get_mal_status(request)


async def save_mal_token(payload: TokenSubmitRequest, request: Request):
    return await tracker_account_handlers.save_mal_token(payload, request)


async def disconnect_mal(request: Request):
    return await tracker_account_handlers.disconnect_mal(request)


async def auth_mal_page(request: Request):
    return await tracker_account_handlers.auth_mal_page(request)


async def resolve_anime_api(request: Request, title: str, year: Optional[int] = None):
    return await tracker_diagnostics_handlers.resolve_anime_api(request, title, year)


async def get_multi_trackers_status(request: Request):
    return await tracker_diagnostics_handlers.get_multi_trackers_status(request)


async def get_tmdb_status(request: Request):
    return await tracker_diagnostics_handlers.get_tmdb_status(request)


async def get_kitsu_status(request: Request):
    return await tracker_diagnostics_handlers.get_kitsu_status(request)


async def get_letterboxd_status(request: Request):
    return await tracker_diagnostics_handlers.get_letterboxd_status(request)


async def get_serializd_status(request: Request):
    return await tracker_diagnostics_handlers.get_serializd_status(request)


async def get_mdblist_status(request: Request):
    return await tracker_diagnostics_handlers.get_mdblist_status(request)


async def get_relay_status(request: Request):
    return await tracker_diagnostics_handlers.get_relay_status(request)


async def export_letterboxd_csv(request: Request):
    return await tracker_diagnostics_handlers.export_letterboxd_csv(request)


async def get_letterboxd_diary(request: Request):
    return await tracker_diagnostics_handlers.get_letterboxd_diary(request)


async def get_mdblist_ratings(request: Request, imdb_id: Optional[str] = None,
                              tmdb_id: Optional[int] = None, media_type: str = "movie"):
    return await tracker_diagnostics_handlers.get_mdblist_ratings(
        request, imdb_id=imdb_id, tmdb_id=tmdb_id, media_type=media_type,
    )


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
async def get_analytics_summary(
    request: Request, period: str = "all", demo: bool = False,
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    profile: Optional[str] = None, media_type: Optional[str] = None,
    server: Optional[str] = None, tracker: Optional[str] = None,
    viewing: Optional[str] = None,
):
    """Retrieve viewing analytics metrics across a given time window (all, year, month, week)."""
    is_demo = demo or request.query_params.get("demo") == "true"
    principal = get_dashboard_principal(request)
    is_admin = bool(principal and principal.role == "admin")
    profile_username = principal.username if principal and principal.role == "member" else None
    if is_admin and profile:
        configured = {name.casefold(): name for name in Config.PLEX_ALLOWED_USERS}
        if configured and profile.casefold() not in configured:
            raise HTTPException(status_code=400, detail="profile must be one of the configured media profiles.")
        profile_username = configured.get(profile.casefold(), profile) if configured else profile
    def parse_summary_date(value: Optional[str], field: str) -> Optional[datetime.date]:
        if not value:
            return None
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{field} must use YYYY-MM-DD.") from exc
    summary_start = parse_summary_date(start_date, "start_date")
    summary_end = parse_summary_date(end_date, "end_date")
    if summary_start and summary_end and summary_start > summary_end:
        raise HTTPException(status_code=400, detail="start_date must not be after end_date.")
    summary = analytics_mgr.get_summary(
        period=period, demo=is_demo, is_admin=is_admin, profile_username=profile_username,
        start_date=summary_start, end_date=summary_end, media_type_filter=media_type,
        server_filter=server, tracker_filter=tracker, viewing_filter=viewing,
    )
    return summary


@app.get("/api/analytics/wrapped")
async def get_analytics_wrapped(request: Request, year: Optional[int] = None, demo: bool = False):
    """Retrieve the OmniWrapped annual viewing retrospective and archetype summary."""
    is_demo = demo or request.query_params.get("demo") == "true"
    principal = get_dashboard_principal(request)
    is_admin = bool(principal and principal.role == "admin")
    profile_username = principal.username if principal and principal.role == "member" else None
    wrapped = analytics_mgr.get_omniwrapped(year=year, demo=is_demo, is_admin=is_admin, profile_username=profile_username)
    return wrapped


@app.get("/api/analytics/export")
async def export_analytics(
    request: Request, format: str = "json", kind: str = "activity",
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    profile: Optional[str] = None, media_type: Optional[str] = None,
    server: Optional[str] = None, tracker: Optional[str] = None,
    viewing: Optional[str] = None,
):
    """Export filtered activity or dashboard-consistent analytics as CSV or versioned JSON."""
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized: Admin access required to export analytics")
    export_format = str(format).strip().lower()
    export_kind = str(kind).strip().lower()
    if export_format not in {"csv", "json"} or export_kind not in {"activity", "summary"}:
        raise HTTPException(status_code=400, detail="Choose format csv/json and kind activity/summary.")

    def parse_filter_date(value: Optional[str], field: str) -> Optional[datetime.date]:
        if not value:
            return None
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{field} must use YYYY-MM-DD.") from exc

    start = parse_filter_date(start_date, "start_date")
    end = parse_filter_date(end_date, "end_date")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start_date must not be after end_date.")
    clean_profile = str(profile or "").strip()
    if clean_profile and Config.PLEX_ALLOWED_USERS:
        canonical_profiles = {name.casefold(): name for name in Config.PLEX_ALLOWED_USERS}
        if clean_profile.casefold() not in canonical_profiles:
            raise HTTPException(status_code=400, detail="profile must be one of the configured media profiles.")
        clean_profile = canonical_profiles[clean_profile.casefold()]
    clean_media_type = str(media_type or "").strip().lower() or None
    if clean_media_type and clean_media_type not in {"movie", "episode", "show", "tv", "anime"}:
        raise HTTPException(status_code=400, detail="Unsupported media_type filter.")
    clean_viewing = str(viewing or "").strip().lower() or None
    if clean_viewing and clean_viewing not in {"solo", "shared"}:
        raise HTTPException(status_code=400, detail="viewing must be solo or shared.")
    clean_server = str(server or "").strip() or None
    clean_tracker = str(tracker or "").strip().lower() or None
    filter_values = {
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "profile": clean_profile or None,
        "media_type": clean_media_type,
        "server": clean_server,
        "tracker": clean_tracker,
        "viewing": clean_viewing,
    }
    local_zone = datetime.datetime.now().astimezone().tzname() or "server local time"
    timestamp_semantics = "Original event timestamp text is preserved. Naive event timestamps use server local time; date filters compare offset-aware timestamps in server local time while retaining their original offset in the export."
    generated_at = datetime.datetime.now(timezone.utc).isoformat()
    if export_kind == "activity":
        rows = analytics_mgr.get_activity_export_rows(
            start_date=start, end_date=end, profile=clean_profile,
            media_type=clean_media_type, server=clean_server, tracker=clean_tracker, viewing=clean_viewing,
        )
        payload: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": generated_at,
            "timezone": local_zone,
            "timestamp_semantics": timestamp_semantics,
            "filters": filter_values,
            "records": rows,
        }
    else:
        summary = analytics_mgr.get_summary(
            period="all", is_admin=True, profile_username=clean_profile,
            start_date=start, end_date=end, media_type_filter=clean_media_type,
            server_filter=clean_server, tracker_filter=clean_tracker, viewing_filter=clean_viewing,
        )
        payload = {
            "schema_version": 1,
            "generated_at": generated_at,
            "timezone": local_zone,
            "timestamp_semantics": timestamp_semantics,
            "filters": filter_values,
            "summary": summary,
        }

    filename = f"omniscrobble_{export_kind}_{datetime.datetime.now().strftime('%Y%m%d')}.{export_format}"
    if export_format == "json":
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    buffer = io.StringIO(newline="")
    if export_kind == "activity":
        fields = ["operation_id", "timestamp", "profile", "server", "action", "title", "media_type", "progress", "delivery_status", "tracker_delivery", "viewing", "player"]
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n")
        writer.writeheader()
        for row in payload["records"]:
            safe_row = {}
            for field in fields:
                raw_value = row.get(field, "")
                value = json.dumps(raw_value, ensure_ascii=False, sort_keys=True) if isinstance(raw_value, (dict, list)) else str(raw_value)
                safe_row[field] = f"'{value}" if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
            writer.writerow(safe_row)
    else:
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(["metric", "value"])
        for name, value in payload["summary"].items():
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            safe_name = f"'{name}" if name.startswith(("=", "+", "-", "@", "\t", "\r")) else name
            safe_value = f"'{serialized}" if serialized.startswith(("=", "+", "-", "@", "\t", "\r")) else serialized
            writer.writerow([safe_name, safe_value])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get('/auth', response_class=HTMLResponse)
async def auth_page(request: Request, user: Optional[str] = None):
    principal = get_dashboard_principal(request)
    if not principal or (principal.role != "admin" and user and user.strip().lower() != principal.username.lower()):
        return HTMLResponse(content=AUTH_LOCKED_HTML, status_code=401)

    target_uname = profile_for_request(request, user) or ''
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


@app.get("/login", response_class=HTMLResponse)
async def dashboard_login_page(request: Request):
    principal = get_dashboard_principal(request)
    if principal:
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(content="""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in • Omniscrobble</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1220;color:#e2e8f0;font:16px system-ui,sans-serif}.card{width:min(420px,calc(100% - 40px));box-sizing:border-box;padding:28px;border:1px solid #263449;border-radius:14px;background:#111c2e}h1{margin:0 0 8px;font-size:24px}p{color:#94a3b8;line-height:1.5}label{display:block;margin:16px 0 6px}input,button{width:100%;box-sizing:border-box;padding:12px;border:1px solid #334155;border-radius:7px;font:inherit}input{background:#0b1220;color:#f8fafc}button{margin-top:20px;background:#2563eb;color:white;border:0;font-weight:650;cursor:pointer}#error{color:#fca5a5;min-height:1.4em}.back{color:#93c5fd;text-decoration:none}</style></head>
<body><main class="card"><h1>Sign in</h1><p>Use the household account created for you by the administrator.</p><form id="login"><label for="username">Username</label><input id="username" name="username" autocomplete="username" required><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required><div id="error" role="alert"></div><button type="submit">Sign in</button></form><a class="back" href="/">Back to dashboard</a></main>
<script>document.getElementById('login').addEventListener('submit',async e=>{e.preventDefault();const error=document.getElementById('error');error.textContent='';const form=new FormData(e.currentTarget);try{const response=await fetch('/api/account/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.get('username'),password:form.get('password')})});const data=await response.json();if(!response.ok){error.textContent=data.detail||'Sign in failed.';return}location.assign('/') }catch(_){error.textContent='Unable to sign in right now.'}});</script></body></html>""")

@app.get("/demo", response_class=HTMLResponse)
async def demo_dashboard(request: Request, response: Response):
    return await render_dashboard_response(request, response, is_demo=True)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, response: Response):
    is_demo = request.query_params.get("demo") == "true"
    return await render_dashboard_response(request, response, is_demo=is_demo)


async def render_dashboard_response(request: Request, response: Response, is_demo: bool = False) -> HTMLResponse:
    principal = None if is_demo else get_dashboard_principal(request)
    member_profile = principal.username if principal and principal.role == "member" else None
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
        token_health_class = "healthy"
        status_badge = '<span role="status" style="background:#047857;color:#fff;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;">Connected as @demo_viewer &bull; Demo</span>'
        admin_btn = '<span style="font-size:12px;color:#38bdf8;background:#1e293b;border:1px solid #334155;padding:4px 10px;border-radius:6px;font-weight:600;">👑 Demo Admin</span>'
        full_webhook_url = "https://plex.example.com/webhook?token=demo_webhook_secret_xyz"
        full_jellyfin_url = "https://jellyfin.example.com/webhook/jellyfin?token=demo_webhook_secret_xyz"
        full_emby_url = "https://emby.example.com/webhook/emby?token=demo_webhook_secret_xyz"
        masked_webhook_url = full_webhook_url
        demo_banner = '<div style="background:linear-gradient(90deg, #1e3a8a, #0284c7);color:#ffffff;padding:12px 18px;border-radius:10px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 4px 6px -1px rgba(0,0,0,0.3);flex-wrap:wrap;gap:10px;"><div style="display:flex;align-items:center;gap:10px;"><span style="font-size:18px;">🎭</span><div><strong style="color:#ffffff;">Demo Mode Active:</strong><span style="color:#e0f2fe;font-size:13px;margin-left:4px;">Simulated authenticated view with mock information. No real accounts or tokens are exposed.</span></div></div><a href="/" style="background:rgba(255,255,255,0.2);color:#ffffff;text-decoration:none;padding:5px 12px;border-radius:6px;font-weight:600;font-size:12px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(255,255,255,0.3)\'" onmouseout="this.style.background=\'rgba(255,255,255,0.2)\'">Exit Demo &rarr;</a></div>'
        demo_header_btn = '<a href="/" class="btn-sm" style="background:#0369a1;border:1px solid #38bdf8;color:#ffffff;text-decoration:none;font-weight:600;display:inline-flex;align-items:center;gap:5px;transition:opacity 0.15s;" onmouseover="this.style.opacity=\'0.9\'" onmouseout="this.style.opacity=\'1\'" title="Exit demo mode">✕ Exit Demo</a>'
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
        dashboard_trakt = user_mgr.get_client(member_profile) if member_profile else trakt
        auth_status = dashboard_trakt.is_authenticated()
        profile = await get_cached_trakt_profile() if auth_status and not member_profile else None
        raw_username = member_profile or (profile.get("username") if profile else None)

        display_username = raw_username if is_admin else mask_username(raw_username)
        pending_queue = 0 if member_profile or queue_mgr._future_schema_version else queue_mgr.get_pending_count()

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
        token_info = dashboard_trakt.get_token_info()
        if auth_status:
            if token_info.get("healthy"):
                days = token_info.get("days_remaining", 0)
                token_health_str = f"Healthy • Auto-renews in {days}d"
                token_health_class = "healthy"
            else:
                token_health_str = "Token Expired / Refresh Needed"
                token_health_class = "expired"
        else:
            token_health_str = "Not Linked"
            token_health_class = "unlinked"

        # Header status badge
        trakt_enabled = settings_mgr.is_tracker_enabled("trakt")
        if auth_status:
            user_label = f"Connected as @{display_username}" if display_username else "Connected"
            if not trakt_enabled:
                paused_label = f"Paused (@{display_username})" if display_username else "Trakt Paused"
                if is_admin:
                    status_badge = f'<a href="#settings-section-trakt" onclick="openSettingsModal(\'trackers\', \'trakt\'); return false;" class="header-status-badge is-paused">⏸️ {paused_label} &bull; Manage</a>'
                else:
                    status_badge = f'<a href="javascript:void(0)" onclick="openUnlockModal()" class="header-status-badge is-locked" title="Click to unlock admin access">⏸️ {paused_label} &bull; 🔒 Locked</a>'
            elif is_admin:
                status_badge = f'<a href="#settings-section-trakt" onclick="openSettingsModal(\'trackers\', \'trakt\'); return false;" class="header-status-badge is-connected">{user_label} &bull; Manage</a>'
            else:
                status_badge = f'<a href="javascript:void(0)" onclick="openUnlockModal()" class="header-status-badge is-locked" title="Click to unlock admin access">{user_label} &bull; 🔒 Locked</a>'
        else:
            if is_admin:
                status_badge = '<a href="/auth" class="header-status-badge is-disconnected">Not Connected &bull; Link Trakt &rarr;</a>'
            else:
                status_badge = '<a href="javascript:void(0)" onclick="openUnlockModal()" class="header-status-badge is-locked" title="Click to unlock admin access">Not Connected &bull; 🔒 Unlock &rarr;</a>'

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
        '<button onclick="openSettingsModal()" class="btn-sm settings-hub-header-button" '
        'title="Configure Media Servers, Trackers, and Automation"><span>⚙️</span><span>Settings Hub</span></button>'
    )

    if member_profile:
        raw_username = member_profile
        display_username = member_profile
        status_badge = '<button onclick="openAccountModal()" class="btn-sm">My trackers</button>'
    if not is_demo and Config.WEBHOOK_SECRET and principal:
        admin_btn += '<button onclick="openAccountModal()" class="btn-sm">Accounts</button>' if is_admin else '<button onclick="openAccountModal()" class="btn-sm">My account</button>'
        if principal.source == "local_account":
            admin_btn += '<button onclick="logoutDashboardAccount()" class="btn-sm">Sign out</button>'

    # Events rows & pagination metadata
    events_list = demo_mgr.get_demo_events() if is_demo else list(recent_events)
    if member_profile:
        events_list = [ev for ev in events_list if str(ev.get("user") or "").strip().lower() == member_profile.lower()]
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
        active_sessions = playback_mgr.get_active_sessions(is_admin=is_admin, profile_username=member_profile)
        recently_finished = playback_mgr.get_recently_finished(is_admin=is_admin, profile_username=member_profile)

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

    if member_profile:
        cw_user, cw_user_display = "", ""
        cw_shows, cw_devices, configured_users, household_rules = [], [], [], []
        cw_trackers = {}

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
        analytics_data = analytics_mgr.get_summary(period="all", is_admin=is_admin, profile_username=member_profile)
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

    if member_profile:
        cowatch_card_html = ""
        reconcile_card_html = ""
        backup_card_html = ""
        ecosystem_card_html = ""
        arr_bridge_card_html = ""
        simkl_card_html = '<section class="card" id="card-multi-tracker"><h2>My trackers</h2><p>Manage tracker connections for your profile.</p><button onclick="openAccountModal()" class="btn-sm">Manage my connections</button></section>'
    trackers_dict = trk_status_all.get("trackers", {})
    simkl_status = trackers_dict.get("simkl", {})
    simkl_auth = simkl_status.get("authenticated", False)
    ani_status = trackers_dict.get("anilist", {})
    mal_status = trackers_dict.get("myanimelist", {})
    ani_auth = ani_status.get("authenticated", False)
    mal_auth = mal_status.get("authenticated", False)

    stats_data = demo_mgr.get_demo_stats() if is_demo else scrobble_stats
    if member_profile:
        stats_data = {
            "total": analytics_data.get("total_scrobbles", 0),
            "movies": analytics_data.get("movies_watched", 0),
            "episodes": analytics_data.get("episodes_watched", 0),
            "ratings": analytics_data.get("ratings_submitted", 0),
            "collections": sum(ev.get("action") == "collection" for ev in events_list),
        }

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

    if member_profile:
        tmdb_configured = kitsu_configured = letterboxd_configured = serializd_configured = mdblist_configured = False
        own_trackers = user_mgr.get_user_trackers_status(member_profile)
        simkl_configured = bool(own_trackers.get("simkl", {}).get("authenticated"))
        anilist_configured = bool(own_trackers.get("anilist", {}).get("authenticated"))
        mal_configured = bool(own_trackers.get("mal", {}).get("authenticated"))

    cowatch_user = "" if member_profile else Config.CO_WATCH_USER
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
        '{{TOKEN_HEALTH_CLASS}}': token_health_class,
        '{{TOKEN_HEALTH_STR}}': token_health_str,
        '{{ALLOWED_USERS_DISPLAY}}': allowed_users_display,
        '{{ALLOWED_LIBS_DISPLAY}}': allowed_libs_display,
        '{{SYNC_COLLECTION_DISPLAY}}': ('On' if Config.SYNC_COLLECTION else 'Off'),
        '{{UPTIME_STR}}': get_uptime_str(),
        '{{QUEUE_CLASS}}': ('pending' if pending_queue > 0 else 'clear'),
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
        '{{ACTIONS_HEADER}}': ('<th class="activity-actions-header">Actions</th>' if is_admin else ''),
        '{{EVENT_ROWS}}': rows,
        '{{EVENTS_PAGE_INFO}}': events_page_info,
        '{{EVENTS_PAGE_NUM}}': events_page_num,
        '{{EVENTS_NEXT_DISABLED}}': events_next_disabled,
        '{{IS_ADMIN_JS}}': ('true' if is_admin else 'false'),
        '{{IS_DEMO_JS}}': ('true' if is_demo else 'false'),
        '{{IS_MEMBER_JS}}': ('true' if member_profile else 'false'),
        '{{ACCOUNT_USERNAME_JS}}': json.dumps(principal.username if principal and principal.source == 'local_account' else None),
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


# Recovery HTTP routes live in app.routes.recovery. Their implementations remain
# here while they share the application's orchestration dependencies.
app.state.recovery_health_handler = _get_recovery_health
app.state.recovery_connection_test_handler = _test_recovery_connection
app.include_router(recovery_router)
app.include_router(create_watch_lists_router({
    "get_watch_lists": get_watch_lists,
    "search_watch_list_anime": search_watch_list_anime,
    "create_watch_list": create_watch_list,
    "rename_watch_list": rename_watch_list,
    "delete_watch_list": delete_watch_list,
    "add_watch_list_item": add_watch_list_item,
    "reorder_watch_list_items": reorder_watch_list_items,
    "delete_watch_list_item": delete_watch_list_item,
    "update_watch_list_item": update_watch_list_item,
    "set_watch_list_access": set_watch_list_access,
    "set_watch_list_automation": set_watch_list_automation,
    "refresh_watch_list_availability": refresh_watch_list_availability,
    "request_watch_list_item": request_watch_list_item,
    "preview_watch_list_import": preview_watch_list_import,
    "import_watch_lists": import_watch_lists,
    "export_watch_lists": export_watch_lists,
}))
dashboard_account_handlers = DashboardAccountHandlers(
    config=Config,
    get_auth_manager=lambda: dashboard_auth_mgr,
    is_admin_request=is_admin_request,
    is_https_request=is_https_request,
    require_dashboard_permission=require_dashboard_permission,
    get_dashboard_principal=get_dashboard_principal,
    logger=logger,
)
app.include_router(create_accounts_router({
    "dashboard_account_login": dashboard_account_handlers.login,
    "dashboard_account_logout": dashboard_account_handlers.logout,
    "dashboard_account_me": dashboard_account_handlers.me,
    "list_dashboard_accounts": dashboard_account_handlers.list_accounts,
    "create_dashboard_account": dashboard_account_handlers.create_account,
    "update_dashboard_account": dashboard_account_handlers.update_account,
    "delete_dashboard_account": dashboard_account_handlers.delete_account,
    "admin_unlock": admin_unlock,
    "admin_lock": admin_lock,
}))
acquisition_handlers = AcquisitionHandlers(
    config=Config,
    arr_bridge=arr_bridge,
    settings_mgr=settings_mgr,
    cowatch_mgr=cowatch_mgr,
    is_admin_request=is_admin_request,
    watch_list_actor=_watch_list_actor,
    sonarr_client=SonarrClient,
    radarr_client=RadarrClient,
    overseerr_client=OverseerrClient,
    ecosystem_clients={
        "plex_client": reverse_sync_mgr.plex,
        "jellyfin_client": reverse_sync_mgr.jellyfin,
        "emby_client": reverse_sync_mgr.emby,
        "simkl_client": simkl,
        "anilist_client": anilist,
        "mal_client": mal,
    },
    logger=logger,
)
_arr_acquisition_config_cache = acquisition_handlers._config_cache
reconciliation_settings_handlers = ReconciliationSettingsHandlers(
    settings_mgr=settings_mgr,
    reverse_sync_mgr=reverse_sync_mgr,
    config_updated=reverse_sync_config_updated,
    is_admin_request=is_admin_request,
)
reconciliation_workflow_handlers = ReconciliationWorkflowHandlers(
    reverse_sync_mgr=reverse_sync_mgr,
    cloud_sync_mgr=cloud_sync_mgr,
    demo_mgr=demo_mgr,
    is_admin_request=is_admin_request,
    letterboxd_client=letterboxd,
    cross_tracker_sync=cross_tracker_sync,
    arr_bridge=arr_bridge,
)
cross_tracker_sync_handlers = CrossTrackerSyncHandlers(
    cross_tracker_sync=cross_tracker_sync,
    demo_mgr=demo_mgr,
    is_admin_request=is_admin_request,
    sync_mutex=cloud_sync_mgr.sync_mutex,
)
media_server_webhook_handlers = MediaServerWebhookHandlers(
    reverse_sync_mgr=reverse_sync_mgr,
    is_admin_request=is_admin_request,
)
_reconciliation_previews = reconciliation_workflow_handlers.previews
app.include_router(create_reconciliation_router({
    "get_sync_settings": reconciliation_settings_handlers.get_settings,
    "save_sync_settings": reconciliation_settings_handlers.save_settings,
    "test_sync_connection": reconciliation_settings_handlers.test_connection,
    "get_sync_status": get_sync_status,
    "get_sync_diff": get_sync_diff,
    "preview_reconciliation": reconciliation_workflow_handlers.preview,
    "trigger_reconciliation": reconciliation_workflow_handlers.execute,
    "get_sync_progress": reconciliation_workflow_handlers.get_progress,
    "get_background_sync_status": reconciliation_workflow_handlers.get_background_status,
    "trigger_background_sync": reconciliation_workflow_handlers.trigger_background_sync,
    "register_server_webhook": media_server_webhook_handlers.register,
    "get_cross_sync_status": cross_tracker_sync_handlers.get_status,
    "get_cross_sync_diff": cross_tracker_sync_handlers.get_diff,
    "scan_cross_sync_discrepancies": cross_tracker_sync_handlers.scan,
    "execute_cross_sync": cross_tracker_sync_handlers.execute,
    "get_cross_sync_progress": cross_tracker_sync_handlers.get_progress,
    "get_cross_sync_provenance": cross_tracker_sync_handlers.get_provenance,
}))
tracker_account_handlers = TrackerAccountHandlers(
    config=Config,
    demo_mgr=demo_mgr,
    user_mgr=user_mgr,
    simkl=simkl,
    anilist=anilist,
    mal=mal,
    profile_for_request=profile_for_request,
    require_dashboard_permission=require_dashboard_permission,
    get_dashboard_principal=get_dashboard_principal,
    is_admin_request=is_admin_request,
    get_cached_trakt_profile=get_cached_trakt_profile,
    invalidate_trakt_profile=invalidate_trakt_user_profile,
    logger=logger,
    auth_locked_html=AUTH_LOCKED_HTML,
    auth_simkl_html=AUTH_SIMKL_HTML,
    auth_anilist_html=AUTH_ANILIST_HTML,
    auth_mal_html=AUTH_MAL_HTML,
)
tracker_diagnostics_handlers = TrackerDiagnosticsHandlers(
    demo_mgr=demo_mgr,
    multi_tracker=multi_tracker,
    settings_mgr=settings_mgr,
    is_admin_request=is_admin_request,
    mask_username=mask_username,
    anime_resolver=anime_resolver,
    parsed_media_type=ParsedMedia,
    tmdb=tmdb,
    kitsu=kitsu,
    letterboxd=letterboxd,
    serializd=serializd,
    mdblist=mdblist,
)
app.include_router(create_integrations_router({
    "auth_start": tracker_account_handlers.auth_start,
    "auth_poll": tracker_account_handlers.auth_poll,
    "get_trakt_status": tracker_account_handlers.get_trakt_status,
    "disconnect_trakt": tracker_account_handlers.disconnect_trakt,
    "get_simkl_status": tracker_account_handlers.get_simkl_status,
    "get_simkl_pin": tracker_account_handlers.get_simkl_pin,
    "poll_simkl_pin": tracker_account_handlers.poll_simkl_pin,
    "disconnect_simkl": tracker_account_handlers.disconnect_simkl,
    "auth_simkl_page": tracker_account_handlers.auth_simkl_page,
    "get_anilist_status": tracker_account_handlers.get_anilist_status,
    "save_anilist_token": tracker_account_handlers.save_anilist_token,
    "disconnect_anilist": tracker_account_handlers.disconnect_anilist,
    "auth_anilist_page": tracker_account_handlers.auth_anilist_page,
    "get_mal_status": tracker_account_handlers.get_mal_status,
    "save_mal_token": tracker_account_handlers.save_mal_token,
    "disconnect_mal": tracker_account_handlers.disconnect_mal,
    "auth_mal_page": tracker_account_handlers.auth_mal_page,
    "resolve_anime_api": tracker_diagnostics_handlers.resolve_anime_api,
    "get_multi_trackers_status": tracker_diagnostics_handlers.get_multi_trackers_status,
    "get_tmdb_status": tracker_diagnostics_handlers.get_tmdb_status,
    "get_kitsu_status": tracker_diagnostics_handlers.get_kitsu_status,
    "get_letterboxd_status": tracker_diagnostics_handlers.get_letterboxd_status,
    "get_serializd_status": tracker_diagnostics_handlers.get_serializd_status,
    "get_mdblist_status": tracker_diagnostics_handlers.get_mdblist_status,
    "get_relay_status": tracker_diagnostics_handlers.get_relay_status,
    "export_letterboxd_csv": tracker_diagnostics_handlers.export_letterboxd_csv,
    "get_letterboxd_diary": tracker_diagnostics_handlers.get_letterboxd_diary,
    "get_mdblist_ratings": tracker_diagnostics_handlers.get_mdblist_ratings,
}))
app.include_router(create_acquisition_router({
    "lookup_arr_media": acquisition_handlers.lookup_media,
    "get_arr_acquisition_config": acquisition_handlers.get_config,
    "add_arr_media": acquisition_handlers.add_media,
    "test_arr_connection": acquisition_handlers.test_connection,
    "get_arr_status": acquisition_handlers.get_status,
    "trigger_arr_sync": acquisition_handlers.trigger_sync,
    "get_ecosystem_status": acquisition_handlers.get_ecosystem_status,
}))


if __name__ == '__main__':
    ssl_kwargs = {}
    if Config.SSL_CERTFILE and Config.SSL_KEYFILE:
        ssl_kwargs["ssl_certfile"] = Config.SSL_CERTFILE
        ssl_kwargs["ssl_keyfile"] = Config.SSL_KEYFILE
    if Config.DEBUG:
        uvicorn.run('app.main:app', host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=True, reload_excludes=['*.json', 'data/*'], **ssl_kwargs)
    else:
        uvicorn.run('app.main:app', host=Config.SERVER_HOST, port=Config.SERVER_PORT, reload=False, **ssl_kwargs)
