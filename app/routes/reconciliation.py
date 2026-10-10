"""HTTP route registration for server and cross-tracker reconciliation."""

import datetime
import secrets
import time
from collections.abc import Mapping
from datetime import timezone
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class ReconcileRequest(BaseModel):
    item_ids: Optional[list[str]] = None
    direction: str = "all"
    server: Optional[str] = None
    preview_id: Optional[str] = None


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


class RegisterWebhookRequest(BaseModel):
    server: str = "plex"
    webhook_url: Optional[str] = None

    model_config = {"extra": "ignore"}


class CrossSyncExecuteRequest(BaseModel):
    item_ids: Optional[list[str]] = None
    direction: str = "both"
    conflict_policy: Literal["manual", "trakt", "simkl", "tmdb", "newest"] = "manual"


class ReconciliationSettingsHandlers:
    """Reconciliation settings and media-server connection-test behavior."""

    def __init__(
        self, *, settings_mgr: Any, reverse_sync_mgr: Any,
        config_updated: Any, is_admin_request: Callable[[Request], bool],
    ) -> None:
        self.settings_mgr = settings_mgr
        self.reverse_sync_mgr = reverse_sync_mgr
        self.config_updated = config_updated
        self.is_admin_request = is_admin_request

    async def get_settings(self, request: Request):
        """Return persistent two-way reconciliation settings."""
        if request.query_params.get("demo") == "true":
            return {
                "enabled": True, "server_type": "plex",
                "plex_url": "http://<your-server-ip-or-domain>:32400", "plex_token": "••••••••abcd",
                "jellyfin_url": "http://<your-server-ip-or-domain>:8096", "jellyfin_token": "••••••••efgh",
                "jellyfin_user_id": "", "emby_url": "http://<your-server-ip-or-domain>:8096",
                "emby_token": "••••••••ijkl", "emby_user_id": "", "masked_token": "••••••••abcd",
                "is_token_set": True, "has_token": True, "has_plex_token": True,
                "has_jellyfin_token": True, "has_emby_token": True, "interval_minutes": 60,
                "sync_on_startup": False, "sync_ratings": True, "direction_default": "all",
            }
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        return self.settings_mgr.get_reconciliation_settings(mask_token=True)

    async def save_settings(self, payload: ReconcileSettingsRequest, request: Request):
        """Save reconciliation settings and reconfigure the live media-server clients."""
        if request.query_params.get("demo") == "true":
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
                    "emby_token": "••••••••ijkl", "masked_token": "••••••••abcd",
                    "is_token_set": True, "has_token": True,
                    "interval_minutes": payload.interval_minutes if payload.interval_minutes is not None else 60,
                    "sync_on_startup": bool(payload.sync_on_startup),
                    "sync_ratings": True if payload.sync_ratings is None else payload.sync_ratings,
                    "direction_default": payload.direction_default or "all",
                },
            }
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        updated = self.settings_mgr.update_reconciliation_settings(payload.model_dump(exclude_unset=True))
        raw_recon = self.settings_mgr.get_reconciliation_settings(mask_token=False)
        self.reverse_sync_mgr.update_config(
            server=raw_recon.get("server_type"), plex_url=raw_recon.get("plex_url"),
            plex_token=raw_recon.get("plex_token"), jellyfin_url=raw_recon.get("jellyfin_url"),
            jellyfin_token=raw_recon.get("jellyfin_token"), jellyfin_user_id=raw_recon.get("jellyfin_user_id"),
            emby_url=raw_recon.get("emby_url"), emby_token=raw_recon.get("emby_token"),
            emby_user_id=raw_recon.get("emby_user_id"),
        )
        self.config_updated.set()
        return {"status": "success", "settings": updated}

    async def test_connection(self, payload: MediaServerTestConnectionRequest, request: Request):
        """Test a Plex, Jellyfin, or Emby connection with supplied or saved credentials."""
        server = (payload.server or "plex").lower().strip()
        if request.query_params.get("demo") == "true":
            if server == "jellyfin":
                return {"status": "connected", "server_name": "Demo Jellyfin Server", "version": "10.9.11"}
            if server == "emby":
                return {"status": "connected", "server_name": "Demo Emby Server", "version": "4.8.8"}
            return {"status": "connected", "machine_identifier": "demo-plex-server", "version": "1.40.2.8395"}
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        raw = self.settings_mgr.get_reconciliation_settings(mask_token=False)
        if server == "jellyfin":
            url_candidate, token_candidate = payload.url or payload.jellyfin_url or "", payload.token or payload.jellyfin_token or ""
            target_url = url_candidate.strip() if url_candidate else raw.get("jellyfin_url", "")
            if target_url and not target_url.startswith(("http://", "https://")):
                raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
            target_token = token_candidate.strip()
            if not target_token or target_token.startswith(("••••", "●●●●")):
                target_token = raw.get("jellyfin_token", "")
            return await self.reverse_sync_mgr.test_connection(
                server="jellyfin", url=target_url, token=target_token,
                user_id=payload.user_id or raw.get("jellyfin_user_id", ""),
            )
        if server == "emby":
            url_candidate, token_candidate = payload.url or payload.emby_url or "", payload.token or payload.emby_token or ""
            target_url = url_candidate.strip() if url_candidate else raw.get("emby_url", "")
            if target_url and not target_url.startswith(("http://", "https://")):
                raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
            target_token = token_candidate.strip()
            if not target_token or target_token.startswith(("••••", "●●●●")):
                target_token = raw.get("emby_token", "")
            return await self.reverse_sync_mgr.test_connection(
                server="emby", url=target_url, token=target_token,
                user_id=payload.user_id or raw.get("emby_user_id", ""),
            )
        url_candidate, token_candidate = payload.url or payload.plex_url or "", payload.token or payload.plex_token or ""
        target_url = url_candidate.strip() if url_candidate else raw.get("plex_url", "")
        if target_url and not target_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
        target_token = token_candidate.strip()
        if not target_token or target_token.startswith(("••••", "●●●●")):
            target_token = raw.get("plex_token", "")
        return await self.reverse_sync_mgr.test_connection(server="plex", url=target_url, token=target_token)


class ReconciliationWorkflowHandlers:
    """Preview, apply, and background-run flows for media-server reconciliation."""

    def __init__(
        self, *, reverse_sync_mgr: Any, cloud_sync_mgr: Any, demo_mgr: Any,
        is_admin_request: Callable[[Request], bool], letterboxd_client: Any,
        cross_tracker_sync: Any, arr_bridge: Any,
    ) -> None:
        self.reverse_sync_mgr = reverse_sync_mgr
        self.cloud_sync_mgr = cloud_sync_mgr
        self.demo_mgr = demo_mgr
        self.is_admin_request = is_admin_request
        self.letterboxd_client = letterboxd_client
        self.cross_tracker_sync = cross_tracker_sync
        self.arr_bridge = arr_bridge
        self.previews: dict[str, dict[str, Any]] = {}

    async def preview(self, payload: ReconcileRequest, request: Request):
        """Return and retain the exact reconciliation actions eligible for explicit apply."""
        is_demo = request.query_params.get("demo") == "true"
        if not is_demo and not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        diff = await self.reverse_sync_mgr.scan_discrepancies(force=True, demo=is_demo, server=payload.server)
        selected = set(payload.item_ids or [])
        proposed = [item for item in diff if not selected or item.get("id") in selected]
        if payload.direction in ("trakt_to_plex", "trakt_to_server"):
            proposed = [item for item in proposed if item.get("action_recommended", "").startswith(("mark_", "sync_rating_to_"))]
        elif payload.direction in ("plex_to_trakt", "server_to_trakt"):
            proposed = [item for item in proposed if item.get("action_recommended") in ("sync_to_trakt", "sync_rating_to_trakt")]
        warnings = []
        for item in proposed:
            ids = item.get("ids") or {}
            stable_ids = any(str(ids.get(key) or "").strip() for key in ("imdb", "tmdb", "tvdb"))
            item["match_confidence"] = "high" if stable_ids else "title-based"
            if not stable_ids:
                warnings.append({"id": item.get("id"), "title": item.get("title"), "reason": "No IMDb, TMDB, or TVDB ID; match relies on title and year/episode details."})
        now = time.monotonic()
        for key, entry in list(self.previews.items()):
            if now - entry["created"] > 600:
                self.previews.pop(key, None)
        preview_id = secrets.token_urlsafe(24)
        self.previews[preview_id] = {"created": now, "items": proposed, "demo": is_demo}
        action_counts: dict[str, int] = {}
        for item in proposed:
            action = item.get("action_recommended", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
        return {
            "preview_id": preview_id, "server": payload.server, "direction": payload.direction,
            "total": len(proposed), "actions": action_counts, "items": proposed, "warnings": warnings,
            "unresolved": [], "created_at": datetime.datetime.now(timezone.utc).isoformat(),
            "expires_in_seconds": 600,
        }

    async def execute(self, payload: ReconcileRequest, request: Request):
        """Execute two-way reconciliation, optionally applying a retained preview."""
        is_demo = request.query_params.get("demo") == "true"
        if is_demo:
            return await self.reverse_sync_mgr.execute_reconciliation(demo=True, server=payload.server)
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        preview = None
        if payload.preview_id:
            preview = self.previews.pop(payload.preview_id, None)
            if not preview or time.monotonic() - preview["created"] > 600:
                raise HTTPException(status_code=409, detail="Reconciliation preview expired or was already applied. Run a new preview.")
        if self.cloud_sync_mgr.sync_mutex.locked():
            raise HTTPException(status_code=409, detail="A cloud or reconciliation sync is already in progress")
        async with self.cloud_sync_mgr.sync_mutex:
            return await self.reverse_sync_mgr.execute_reconciliation(
                item_ids=payload.item_ids, direction=payload.direction, server=payload.server,
                preview_items=preview["items"] if preview else None,
            )

    async def get_progress(self, request: Request):
        """Return current media-server reconciliation progress."""
        return self.reverse_sync_mgr._sync_progress

    async def get_background_status(self, request: Request):
        """Return current background synchronization state and telemetry."""
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_background_sync_status()
        return self.cloud_sync_mgr.get_status()

    async def trigger_background_sync(self, request: Request):
        """Manually trigger an automated background cloud reconciliation cycle."""
        if request.query_params.get("demo") == "true":
            return {
                "status": "success",
                "message": "Demo background cloud reconciliation completed successfully.",
                "last_run_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items_reconciled": 3,
            }
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        if self.cloud_sync_mgr.sync_mutex.locked():
            raise HTTPException(status_code=409, detail="A cloud synchronization or reconciliation is already in progress")
        result = await self.cloud_sync_mgr.run_sync_cycle(
            trigger="manual", letterboxd_client=self.letterboxd_client,
            reverse_sync_mgr=self.reverse_sync_mgr, cross_tracker_sync=self.cross_tracker_sync,
            arr_bridge=self.arr_bridge,
        )
        if isinstance(result, dict) and result.get("status") == "conflict":
            raise HTTPException(status_code=409, detail=result.get("message"))
        return result


class CrossTrackerSyncHandlers:
    """HTTP behavior for Trakt/Simkl and Trakt/TMDb reconciliation."""

    def __init__(self, *, cross_tracker_sync: Any, demo_mgr: Any,
                 is_admin_request: Callable[[Request], bool], sync_mutex: Any) -> None:
        self.cross_tracker_sync = cross_tracker_sync
        self.demo_mgr = demo_mgr
        self.is_admin_request = is_admin_request
        self.sync_mutex = sync_mutex

    async def get_status(self, request: Request):
        """Return status for Trakt–Simkl reconciliation and Trakt/TMDb ratings."""
        return await self.cross_tracker_sync.get_status(demo=request.query_params.get("demo") == "true")

    async def get_diff(self, request: Request, force: bool = False):
        """Scan and return cross-tracker discrepancies between Trakt and Simkl."""
        if request.query_params.get("demo") == "true":
            demo_diff = self.demo_mgr.get_demo_cross_tracker_diff()
            return {"status": "ok", "diff": demo_diff, "count": len(demo_diff)}
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        diff = await self.cross_tracker_sync.scan_discrepancies(force=force)
        return {"status": "ok", "diff": diff, "count": len(diff)}

    async def scan(self, request: Request):
        """Trigger an on-demand scan of discrepancies between Trakt and Simkl."""
        if request.query_params.get("demo") == "true":
            demo_diff = self.demo_mgr.get_demo_cross_tracker_diff()
            return {"status": "ok", "diff": demo_diff, "count": len(demo_diff)}
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        diff = await self.cross_tracker_sync.scan_discrepancies(force=True)
        return {"status": "ok", "diff": diff, "count": len(diff)}

    async def execute(self, payload: CrossSyncExecuteRequest, request: Request):
        """Execute selected cross-tracker reconciliation with a conflict policy."""
        is_demo = request.query_params.get("demo") == "true"
        if is_demo:
            return await self.cross_tracker_sync.execute_sync(
                item_ids=payload.item_ids, direction=payload.direction,
                conflict_policy=payload.conflict_policy, demo=True,
            )
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        if self.sync_mutex.locked():
            raise HTTPException(status_code=409, detail="A cloud or reconciliation sync is already in progress")
        async with self.sync_mutex:
            return await self.cross_tracker_sync.execute_sync(
                item_ids=payload.item_ids, direction=payload.direction,
                conflict_policy=payload.conflict_policy,
            )

    async def get_progress(self, request: Request):
        """Return live cross-tracker sync progress."""
        return self.cross_tracker_sync._sync_progress

    async def get_provenance(self, request: Request, limit: int = 100):
        """Return recent admin-only rating conflict decisions that were applied."""
        if request.query_params.get("demo") == "true":
            return {"records": []}
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        return {"records": self.cross_tracker_sync.get_provenance(limit)}


class MediaServerWebhookHandlers:
    """Registration of Omniscrobble webhooks with supported media servers."""

    def __init__(self, *, reverse_sync_mgr: Any,
                 is_admin_request: Callable[[Request], bool]) -> None:
        self.reverse_sync_mgr = reverse_sync_mgr
        self.is_admin_request = is_admin_request

    async def register(self, payload: RegisterWebhookRequest, request: Request):
        """Register the webhook URL with Plex, Jellyfin, or Emby."""
        is_demo = request.query_params.get("demo") == "true"
        server = (payload.server or "plex").lower().strip()
        if is_demo:
            demo_url = payload.webhook_url or f"https://omniscrobble.demo.internal/webhook{'/' + server if server != 'plex' else ''}"
            return {
                "status": "success", "success": True, "server": server, "url": demo_url,
                "message": f"Demo Mode: Successfully registered webhook on {server.capitalize()}!",
            }
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        result = await self.reverse_sync_mgr.register_webhook(server=server, webhook_url=payload.webhook_url)
        if not result.get("success", False):
            raise HTTPException(status_code=400, detail=result.get("error", "Failed to register webhook"))
        return result


_ROUTES = (
    ("/api/sync/settings", "get_sync_settings", ["GET"]),
    ("/api/sync/settings", "save_sync_settings", ["POST"]),
    ("/api/sync/test-connection", "test_sync_connection", ["POST"]),
    ("/api/sync/status", "get_sync_status", ["GET"]),
    ("/api/sync/diff", "get_sync_diff", ["GET"]),
    ("/api/sync/reconcile/preview", "preview_reconciliation", ["POST"]),
    ("/api/sync/reconcile", "trigger_reconciliation", ["POST"]),
    ("/api/sync/progress", "get_sync_progress", ["GET"]),
    ("/api/sync/background/status", "get_background_sync_status", ["GET"]),
    ("/api/sync/background/run", "trigger_background_sync", ["POST"]),
    ("/api/sync/register-webhook", "register_server_webhook", ["POST"]),
    ("/api/cross-sync/status", "get_cross_sync_status", ["GET"]),
    ("/api/cross-sync/diff", "get_cross_sync_diff", ["GET"]),
    ("/api/cross-sync/scan", "scan_cross_sync_discrepancies", ["POST"]),
    ("/api/cross-sync/execute", "execute_cross_sync", ["POST"]),
    ("/api/cross-sync/progress", "get_cross_sync_progress", ["GET"]),
    ("/api/cross-sync/provenance", "get_cross_sync_provenance", ["GET"]),
)


def create_router(handlers: Mapping[str, Any]):
    """Register reconciliation handlers supplied explicitly by the application."""
    router = APIRouter(tags=["Reconciliation"])
    required = {handler_name for _, handler_name, _ in _ROUTES}
    missing = sorted(required - handlers.keys())
    if missing:
        raise ValueError(f"Missing reconciliation route handlers: {', '.join(missing)}")
    for path, handler_name, methods in _ROUTES:
        router.add_api_route(path, handlers[handler_name], methods=methods, name=handler_name)
    return router
