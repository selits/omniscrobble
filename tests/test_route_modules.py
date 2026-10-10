"""Contract checks for the focused FastAPI route modules."""

import httpx
import pytest
from app.routes.watch_lists import create_router as create_watch_lists_router

from app.config import Config
from app.main import app
from app.routes.acquisition import create_router as create_acquisition_router
from app.routes.reconciliation import create_router as create_reconciliation_router
from app.routes.integrations import create_router as create_integrations_router


def test_route_modules_preserve_representative_paths_and_methods():
    paths = app.openapi()["paths"]
    expected = {
        "/api/health/recovery": {"get"},
        "/api/health/recovery/test": {"post"},
        "/api/watch-lists": {"get", "post"},
        "/api/admin/accounts": {"get", "post"},
        "/api/admin/accounts/{username}": {"patch", "delete"},
        "/api/sync/reconcile": {"post"},
        "/api/cross-sync/execute": {"post"},
        "/api/arr/test-connection": {"post"},
        "/api/trackers/status": {"get"},
    }
    for path, methods in expected.items():
        assert {method.lower() for method in paths[path]} == methods


def test_watch_list_router_requires_explicit_handler_bindings():
    with pytest.raises(ValueError, match="Missing watch-list route handlers: add_watch_list_item"):
        create_watch_lists_router({"get_watch_lists": lambda: None})


def test_acquisition_router_requires_explicit_handler_bindings():
    with pytest.raises(ValueError, match="Missing acquisition route handler: lookup_arr_media"):
        create_acquisition_router({"lookup_media": lambda: None})


def test_reconciliation_router_requires_explicit_handler_bindings():
    with pytest.raises(ValueError, match="Missing reconciliation route handlers: execute_cross_sync"):
        create_reconciliation_router({"get_sync_settings": lambda: None})


def test_integrations_router_requires_explicit_handler_bindings():
    with pytest.raises(ValueError, match="Missing integration route handlers: auth_anilist_page"):
        create_integrations_router({"auth_start": lambda: None})


@pytest.mark.asyncio
async def test_recovery_route_keeps_its_admin_gate(monkeypatch):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "route-test-secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/health/recovery")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_reconciliation_and_acquisition_mutations_keep_admin_gates(monkeypatch):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "route-test-secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        reconciliation = await client.get("/api/sync/diff")
        acquisition = await client.post("/api/arr/sync")
    assert reconciliation.status_code == 401
    assert acquisition.status_code == 401
