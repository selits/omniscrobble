"""HTTP route registration for server and cross-tracker reconciliation."""

from fastapi import APIRouter


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


def create_router(handlers):
    """Register the existing typed reconciliation handlers on a focused router."""
    router = APIRouter(tags=["Reconciliation"])
    for path, handler_name, methods in _ROUTES:
        router.add_api_route(path, handlers[handler_name], methods=methods, name=handler_name)
    return router
