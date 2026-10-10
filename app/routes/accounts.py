"""HTTP route registration for dashboard accounts and access controls."""

from fastapi import APIRouter


_ROUTES = (
    ("/api/account/login", "dashboard_account_login", ["POST"]),
    ("/api/account/logout", "dashboard_account_logout", ["POST"]),
    ("/api/account/me", "dashboard_account_me", ["GET"]),
    ("/api/admin/accounts", "list_dashboard_accounts", ["GET"]),
    ("/api/admin/accounts", "create_dashboard_account", ["POST"]),
    ("/api/admin/accounts/{username}", "update_dashboard_account", ["PATCH"]),
    ("/api/admin/accounts/{username}", "delete_dashboard_account", ["DELETE"]),
    ("/api/admin/unlock", "admin_unlock", ["POST"]),
    ("/api/admin/lock", "admin_lock", ["POST"]),
)


def create_router(handlers):
    """Register the existing typed account handlers on a focused router."""
    router = APIRouter(tags=["Dashboard Accounts"])
    for path, handler_name, methods in _ROUTES:
        router.add_api_route(path, handlers[handler_name], methods=methods, name=handler_name)
    return router
