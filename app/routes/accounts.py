"""Dashboard account HTTP handlers and route registration."""

from collections.abc import Callable
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel


class DashboardAccountLoginRequest(BaseModel):
    username: str
    password: str


class DashboardAccountCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "member"


class DashboardAccountUpdateRequest(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None
    enabled: Optional[bool] = None


class DashboardAccountHandlers:
    """Account endpoint behavior with application policies supplied explicitly."""

    def __init__(
        self,
        *,
        config: Any,
        get_auth_manager: Callable[[], Any],
        is_admin_request: Callable[[Request], bool],
        is_https_request: Callable[[Request], bool],
        require_dashboard_permission: Callable[..., Any],
        get_dashboard_principal: Callable[[Request], Any],
        logger: Any,
    ) -> None:
        self.config = config
        self.get_auth_manager = get_auth_manager
        self.is_admin_request = is_admin_request
        self.is_https_request = is_https_request
        self.require_dashboard_permission = require_dashboard_permission
        self.get_dashboard_principal = get_dashboard_principal
        self.logger = logger

    def _require_local_account_admin(self, request: Request) -> None:
        if not self.config.WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Local accounts require WEBHOOK_SECRET to be configured.")
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Admin access is required to manage dashboard accounts.")

    def login(self, payload: DashboardAccountLoginRequest, request: Request, response: Response):
        if not self.config.WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Local accounts require WEBHOOK_SECRET to be configured.")
        auth_manager = self.get_auth_manager()
        client_key = request.client.host if request.client else "unknown"
        session = auth_manager.authenticate(payload.username, payload.password, client_key=client_key)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        same_site = getattr(self.config, "COOKIE_SAMESITE", "lax") or "lax"
        response.set_cookie("dashboard_session", session.session_token, httponly=True, secure=self.is_https_request(request), samesite=same_site, path="/", max_age=auth_manager.session_lifetime_seconds)
        response.set_cookie("csrf_token", session.csrf_token, httponly=False, secure=self.is_https_request(request), samesite=same_site, path="/", max_age=auth_manager.session_lifetime_seconds)
        return {"status": "ok", "username": session.principal.username, "role": session.principal.role, "csrf_token": session.csrf_token}

    def logout(self, request: Request, response: Response):
        auth_manager = self.get_auth_manager()
        session_token = request.cookies.get("dashboard_session", "")
        principal = auth_manager.resolve_session(session_token)
        if not principal and not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Authentication is required.")
        if principal:
            self.require_dashboard_permission(request, "view_activity")
            auth_manager.record_audit(principal.username, "logout", principal.username)
        auth_manager.revoke_session(session_token)
        response.delete_cookie("dashboard_session", path="/")
        response.delete_cookie("csrf_token", path="/")
        return {"status": "ok"}

    def me(self, request: Request):
        principal = self.get_dashboard_principal(request)
        if not principal:
            raise HTTPException(status_code=401, detail="Authentication is required.")
        return {"username": principal.username, "role": principal.role, "source": principal.source}

    def list_accounts(self, request: Request):
        self._require_local_account_admin(request)
        return {"accounts": self.get_auth_manager().list_accounts()}

    def create_account(self, payload: DashboardAccountCreateRequest, request: Request):
        self._require_local_account_admin(request)
        actor = self.get_dashboard_principal(request)
        auth_manager = self.get_auth_manager()
        try:
            account = auth_manager.create_account(payload.username, payload.password, payload.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        auth_manager.record_audit(actor.username if actor else "admin", "account_created", account["username"])
        self.logger.info("Dashboard account created: %s (role=%s)", account["username"], account["role"])
        return {"status": "created", "account": account}

    def update_account(self, username: str, payload: DashboardAccountUpdateRequest, request: Request):
        self._require_local_account_admin(request)
        actor = self.get_dashboard_principal(request)
        auth_manager = self.get_auth_manager()
        try:
            account = auth_manager.update_account(username, role=payload.role, password=payload.password, enabled=payload.enabled)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not account:
            raise HTTPException(status_code=404, detail="Dashboard account not found.")
        auth_manager.record_audit(actor.username if actor else "admin", "account_updated", account["username"])
        self.logger.info("Dashboard account updated: %s (role=%s, enabled=%s)", account["username"], account["role"], account["enabled"])
        return {"status": "updated", "account": account}

    def delete_account(self, username: str, request: Request):
        self._require_local_account_admin(request)
        actor = self.get_dashboard_principal(request)
        auth_manager = self.get_auth_manager()
        if not auth_manager.delete_account(username):
            raise HTTPException(status_code=404, detail="Dashboard account not found.")
        auth_manager.record_audit(actor.username if actor else "admin", "account_deleted", username.lower())
        self.logger.info("Dashboard account deleted: %s", username.lower())
        return {"status": "deleted"}


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
