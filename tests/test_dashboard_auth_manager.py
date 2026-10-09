from app.services.dashboard_auth_manager import DashboardAuthManager
from app.config import Config
from app.main import app, recent_events
import httpx
import pytest
from unittest.mock import AsyncMock


def test_account_storage_and_password_authentication(tmp_path):
    manager = DashboardAuthManager(tmp_path / "accounts.json")
    account = manager.create_account("Casey", "correct horse battery staple", "member")

    assert account["username"] == "casey"
    assert account["role"] == "member"
    assert "password_hash" not in account
    assert manager.authenticate("CASEY", "wrong password") is None

    session = manager.authenticate("casey", "correct horse battery staple")
    assert session is not None
    assert session.principal.username == "casey"
    assert manager.resolve_session(session.session_token) == session.principal
    assert manager.session_has_csrf(session.session_token, session.csrf_token)
    assert not manager.session_has_csrf(session.session_token, "wrong csrf")


def test_failed_account_persistence_rolls_back_in_memory_mutation(tmp_path, monkeypatch):
    import app.services.dashboard_auth_manager as auth_module

    manager = DashboardAuthManager(tmp_path / "accounts.json")

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(auth_module, "atomic_write_json", fail_write)

    with pytest.raises(OSError, match="disk full"):
        manager.create_account("casey", "correct horse battery staple", "member")

    assert manager.list_accounts() == []


def test_account_password_change_and_disable_revoke_sessions(tmp_path):
    manager = DashboardAuthManager(tmp_path / "accounts.json")
    manager.create_account("jordan", "old password phrase", "member")
    session = manager.authenticate("jordan", "old password phrase")
    assert session is not None

    manager.update_account("jordan", password="new password phrase")
    assert manager.resolve_session(session.session_token) is None
    assert manager.authenticate("jordan", "old password phrase") is None
    assert manager.authenticate("jordan", "new password phrase") is not None

    manager.update_account("jordan", enabled=False)
    assert manager.authenticate("jordan", "new password phrase") is None


def test_role_permissions_keep_global_actions_admin_only():
    assert DashboardAuthManager.has_permission("admin", "edit_household_rules")
    assert DashboardAuthManager.has_permission("member", "view_activity")
    assert DashboardAuthManager.has_permission("member", "manage_personal_trackers")
    assert not DashboardAuthManager.has_permission("member", "edit_household_rules")
    assert not DashboardAuthManager.has_permission("member", "reconcile")
    assert not DashboardAuthManager.has_permission("unknown", "view_activity")


@pytest.mark.asyncio
async def test_local_account_api_session_and_profile_scoping(tmp_path, monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "admin-bootstrap-secret")
    monkeypatch.setattr(main_mod, "dashboard_auth_mgr", DashboardAuthManager(tmp_path / "api-accounts.json"))
    original_events = list(recent_events)
    recent_events.clear()
    recent_events.extend([
        {"event_id": "member-event", "user": "casey", "title": "Casey Film", "tracker_delivery": {"trakt": "success"}, "raw_result": {"private": "upstream payload"}, "rule_evaluation": {"decision": "route", "profiles": ["secret-sibling"], "trackers": ["trakt"]}},
        {"event_id": "other-event", "user": "jordan", "title": "Jordan Film", "tracker_delivery": {"trakt": "success"}},
    ])
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            login_page = await client.get("/login")
            assert login_page.status_code == 200
            assert "household account" in login_page.text.lower()
            created = await client.post(
                "/api/admin/accounts",
                headers={"x-webhook-secret": "admin-bootstrap-secret"},
                json={"username": "casey", "password": "correct horse battery staple", "role": "member"},
            )
            assert created.status_code == 200
            assert "password_hash" not in created.json()["account"]

            login = await client.post("/api/account/login", json={"username": "casey", "password": "correct horse battery staple"})
            assert login.status_code == 200
            assert login.json()["role"] == "member"
            assert client.cookies.get("dashboard_session")

            identity = await client.get("/api/account/me")
            assert identity.json()["username"] == "casey"
            events = await client.get("/api/events")
            assert events.json()["total"] == 1
            assert events.json()["events"][0]["title"] == "Casey Film"
            assert "raw_result" not in events.json()["events"][0]
            assert "profiles" not in events.json()["events"][0]["rule_evaluation"]
            assert "secret-sibling" not in str(events.json())

            # Keep the member's initial server-rendered page on the same profile
            # scope as the activity API, without contacting upstream services.
            monkeypatch.setattr(main_mod.trakt, "is_authenticated", lambda: False)
            monkeypatch.setattr(main_mod.reverse_sync_mgr, "get_status", AsyncMock(return_value={}))
            monkeypatch.setattr(main_mod.cloud_sync_mgr, "get_status", lambda: {})
            monkeypatch.setattr(main_mod.arr_bridge, "get_ecosystem_status", AsyncMock(return_value={}))
            monkeypatch.setattr(main_mod.arr_bridge, "get_status", AsyncMock(return_value={}))
            monkeypatch.setattr(main_mod.multi_tracker, "get_status", AsyncMock(return_value={"trackers": {}}))
            dashboard = await client.get("/")
            assert dashboard.status_code == 200
            assert "Casey Film" in dashboard.text
            assert "Jordan Film" not in dashboard.text
            assert "My account" in dashboard.text
            assert "logoutDashboardAccount" in dashboard.text

            own_tracker_status = await client.get("/api/trakt/status")
            assert own_tracker_status.status_code == 200

            cross_profile = await client.get("/api/trakt/status?user=jordan")
            assert cross_profile.status_code == 403
            admin_only = await client.get("/api/health/recovery")
            assert admin_only.status_code == 401
            member_mutation_without_csrf = await client.post("/api/trakt/disconnect")
            assert member_mutation_without_csrf.status_code == 403

            csrf_token = client.cookies.get("csrf_token")
            logout = await client.post("/api/account/logout", headers={"x-csrf-token": csrf_token})
            assert logout.status_code == 200
            assert (await client.get("/api/account/me")).status_code == 401
    finally:
        recent_events.clear()
        recent_events.extend(original_events)
