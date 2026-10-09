import json
import io
import zipfile
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest

from app.config import Config
from app.main import app
from app.services.watch_list_manager import WatchListError, WatchListManager
from app.services.dashboard_auth_manager import DashboardAuthManager


def test_crud_persists_lists_and_reorders_items(tmp_path):
    path = tmp_path / "watch_lists.json"
    manager = WatchListManager(path)
    watch_list = manager.create_list("Weekend picks")
    first = manager.add_item(watch_list["id"], {"title": "Arrival", "media_type": "movie", "year": 2016})
    second = manager.add_item(watch_list["id"], {"title": "Frieren", "media_type": "anime", "ids": {"anilist": 154587}})

    manager.reorder(watch_list["id"], [second["id"], first["id"]])
    reloaded = WatchListManager(path)

    assert [item["title"] for item in reloaded.get_all()["lists"][0]["items"]] == ["Frieren", "Arrival"]
    assert json.loads(path.read_text())["version"] == 1


def test_duplicate_and_invalid_items_are_rejected(tmp_path):
    manager = WatchListManager(tmp_path / "watch_lists.json")
    watch_list = manager.create_list("Queue")
    manager.add_item(watch_list["id"], {"title": "Arrival", "media_type": "movie"})

    with pytest.raises(WatchListError, match="already in the list"):
        manager.add_item(watch_list["id"], {"title": "arrival", "media_type": "movie"})
    manager.add_item(watch_list["id"], {"title": "Arrival", "media_type": "movie", "year": 2001})
    with pytest.raises(WatchListError, match="Media type"):
        manager.add_item(watch_list["id"], {"title": "Bad type", "media_type": "episode"})
    item = manager.add_item(watch_list["id"], {"title": "Dune", "media_type": "movie"})
    with pytest.raises(WatchListError, match="already in the list"):
        manager.update_item(watch_list["id"], item["id"], {"title": "Arrival", "year": None})
    with pytest.raises(WatchListError, match="at most 20"):
        manager.add_item(watch_list["id"], {"title": "Too many tags", "media_type": "movie", "tags": [f"tag-{i}" for i in range(21)]})


def test_json_and_text_round_trip_and_merge_duplicates(tmp_path):
    path = tmp_path / "watch_lists.json"
    manager = WatchListManager(path)
    watch_list = manager.create_list("Anime queue")
    manager.add_item(watch_list["id"], {"title": "Frieren", "media_type": "anime", "year": 2023, "ids": {"anilist": 154587}, "tags": ["comfort", "favorites"], "notes": "Watch with family"})

    text = manager.export_text()
    preview = manager.preview_import(text)
    assert preview["list_count"] == 1
    assert preview["item_count"] == 1
    assert preview["lists"][0]["items"][0]["year"] == 2023
    assert preview["lists"][0]["items"][0]["tags"] == ["comfort", "favorites"]
    assert preview["lists"][0]["items"][0]["notes"] == "Watch with family"

    result = manager.apply_import(text)
    assert result["duplicates_skipped"] == 1
    exported = manager.get_all()
    assert exported["lists"][0]["items"][0]["ids"]["anilist"] == 154587

    round_trip = WatchListManager(tmp_path / "copy.json")
    round_trip.apply_import(exported, replace=True)
    assert round_trip.get_all()["lists"][0]["items"][0]["year"] == 2023
    assert round_trip.get_all()["lists"][0]["items"][0]["notes"] == "Watch with family"


def test_import_replace_is_explicit_and_rejects_bad_documents(tmp_path):
    manager = WatchListManager(tmp_path / "watch_lists.json")
    manager.create_list("Keep me")
    incoming = {"version": 1, "lists": [{"name": "Imported", "items": []}]}

    manager.apply_import(incoming)
    assert {item["name"] for item in manager.get_all()["lists"]} == {"Keep me", "Imported"}
    manager.apply_import(incoming, replace=True)
    assert [item["name"] for item in manager.get_all()["lists"]] == ["Imported"]
    with pytest.raises(WatchListError, match="Unsupported"):
        manager.preview_import({"version": 99, "lists": []})
    with pytest.raises(WatchListError, match="items must be an array"):
        manager.preview_import({"version": 1, "lists": [{"name": "Malformed", "items": None}]})


def test_invalid_saved_document_is_preserved_without_blocking_startup(tmp_path):
    path = tmp_path / "watch_lists.json"
    path.write_text('{"version":1,"lists":[{"name":"Bad","items":null}]}', encoding="utf-8")

    manager = WatchListManager(path)

    assert manager.get_all() == {"version": 1, "lists": []}
    backups = list(tmp_path.glob("watch_lists.json.invalid-*"))
    assert len(backups) == 1
    assert '"items":null' in backups[0].read_text(encoding="utf-8")


def test_shared_list_visibility_roles_and_import_cannot_transfer_access(tmp_path):
    manager = WatchListManager(tmp_path / "shared.json")
    shared = manager.create_list("Family picks", owner="alice")
    manager.add_item(shared["id"], {"title": "Arrival", "media_type": "movie"})
    manager.set_members(shared["id"], {"bob": "viewer", "carol": "editor"})
    manager.set_auto_action(shared["id"], "request")

    assert manager.get_all_for("alice")["lists"][0]["can_manage"] is True
    bob_list = manager.get_all_for("bob")["lists"][0]
    assert bob_list["can_edit"] is False
    assert "members" not in bob_list
    assert manager.get_all_for("mallory")["lists"] == []

    imported = manager.preview_import(manager.get_all())["lists"][0]
    assert imported["owner"] is None
    assert imported["members"] == {}
    assert imported["auto_action"] == "manual"


def test_watch_list_auto_action_defaults_to_manual_and_is_persisted(tmp_path):
    path = tmp_path / "automation.json"
    manager = WatchListManager(path)
    watch_list = manager.create_list("Picks", owner="alice")
    assert watch_list["auto_action"] == "manual"
    manager.set_auto_action(watch_list["id"], "request")
    assert WatchListManager(path).get_all()["lists"][0]["auto_action"] == "request"
    with pytest.raises(WatchListError, match="manual, request, or acquire"):
        manager.set_auto_action(watch_list["id"], "anything")


@pytest.mark.asyncio
async def test_shared_watch_list_api_enforces_owner_editor_viewer_and_csrf(tmp_path, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-admin-secret")
    manager = DashboardAuthManager(tmp_path / "accounts.json")
    manager.create_account("alice", "a sufficiently long password", "member")
    manager.create_account("bob", "another sufficiently long password", "member")
    alice_session = manager.authenticate("alice", "a sufficiently long password")
    bob_session = manager.authenticate("bob", "another sufficiently long password")
    assert alice_session and bob_session
    monkeypatch.setattr(main_module, "dashboard_auth_mgr", manager)
    monkeypatch.setattr(main_module, "watch_list_mgr", WatchListManager(tmp_path / "shared_api.json"))
    transport = httpx.ASGITransport(app=app)

    def session_headers(session, csrf=False):
        headers = {"cookie": f"dashboard_session={session.session_token}; csrf_token={session.csrf_token}"}
        if csrf:
            headers["x-csrf-token"] = session.csrf_token
        return headers

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post("/api/watch-lists", json={"name": "Shared"}, headers=session_headers(alice_session, csrf=True))
        assert created.status_code == 200
        list_id = created.json()["id"]
        unshared = await client.get("/api/watch-lists", headers=session_headers(bob_session))
        assert unshared.json()["lists"] == []

        shared = await client.put(f"/api/watch-lists/{list_id}/access", json={"members": {"bob": "viewer"}}, headers=session_headers(alice_session, csrf=True))
        assert shared.status_code == 200
        viewer_lists = await client.get("/api/watch-lists", headers=session_headers(bob_session))
        assert viewer_lists.json()["lists"][0]["can_edit"] is False
        viewer_auto = await client.put(f"/api/watch-lists/{list_id}/automation", json={"action": "request"}, headers=session_headers(bob_session, csrf=True))
        assert viewer_auto.status_code == 404
        denied_edit = await client.post(f"/api/watch-lists/{list_id}/items", json={"title": "Arrival", "media_type": "movie"}, headers=session_headers(bob_session, csrf=True))
        assert denied_edit.status_code == 404

        await client.put(f"/api/watch-lists/{list_id}/access", json={"members": {"bob": "editor"}}, headers=session_headers(alice_session, csrf=True))
        allowed_edit = await client.post(f"/api/watch-lists/{list_id}/items", json={"title": "Arrival", "media_type": "movie"}, headers=session_headers(bob_session, csrf=True))
        assert allowed_edit.status_code == 200
        item_id = allowed_edit.json()["id"]
        annotation = await client.patch(f"/api/watch-lists/{list_id}/items/{item_id}", json={"tags": ["family", "weekend"], "notes": "Pick this for Friday"}, headers=session_headers(bob_session, csrf=True))
        assert annotation.status_code == 200
        assert annotation.json()["tags"] == ["family", "weekend"]
        assert annotation.json()["notes"] == "Pick this for Friday"
        csrf_denied = await client.post(f"/api/watch-lists/{list_id}/items", json={"title": "Dune", "media_type": "movie"}, headers=session_headers(bob_session))
        assert csrf_denied.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_opt_in_to_automatic_request_on_item_add(tmp_path, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-admin-secret")
    auth = DashboardAuthManager(tmp_path / "accounts.json")
    auth.create_account("alice", "a sufficiently long password", "member")
    session = auth.authenticate("alice", "a sufficiently long password")
    assert session
    manager = WatchListManager(tmp_path / "automatic_request.json")
    monkeypatch.setattr(main_module, "dashboard_auth_mgr", auth)
    monkeypatch.setattr(main_module, "watch_list_mgr", manager)
    overseerr = SimpleNamespace(
        is_configured=True, base_url="http://request-service",
        has_media=AsyncMock(return_value=False),
        request_media=AsyncMock(return_value={"success": True}),
    )
    monkeypatch.setattr(main_module, "arr_bridge", SimpleNamespace(
        sonarr=SimpleNamespace(is_configured=False), radarr=SimpleNamespace(is_configured=False), overseerr=overseerr
    ))
    monkeypatch.setattr(main_module.settings_mgr, "is_overseerr_enabled", lambda: True)
    transport = httpx.ASGITransport(app=app)
    headers = {"cookie": f"dashboard_session={session.session_token}; csrf_token={session.csrf_token}", "x-csrf-token": session.csrf_token}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post("/api/watch-lists", json={"name": "Requests"}, headers=headers)
        list_id = created.json()["id"]
        choice = await client.put(f"/api/watch-lists/{list_id}/automation", json={"action": "request"}, headers=headers)
        assert choice.status_code == 200
        added = await client.post(f"/api/watch-lists/{list_id}/items", json={
            "title": "Arrival", "media_type": "movie", "ids": {"tmdb": 329865}
        }, headers=headers)
        assert added.status_code == 200
        assert added.json()["automation"]["status"] == "requested"
        saved_item = WatchListManager(tmp_path / "automatic_request.json").get_all()["lists"][0]["items"][0]
        assert saved_item["automation"]["status"] == "requested"
        overseerr.has_media.assert_awaited_once_with("movie", 329865)
        overseerr.request_media.assert_awaited_once_with("movie", 329865, seasons=None)


@pytest.mark.asyncio
async def test_automatic_arr_acquisition_requires_exact_match_and_uses_saved_defaults(monkeypatch):
    import app.main as main_module

    candidate = {"title": "Arrival", "tmdbId": 329865}
    radarr = SimpleNamespace(is_configured=True, has_movie=AsyncMock(return_value=False))
    monkeypatch.setattr(main_module, "arr_bridge", SimpleNamespace(
        radarr=radarr, sonarr=SimpleNamespace(is_configured=False),
        lookup_media=AsyncMock(return_value=[candidate]),
        acquire_media=AsyncMock(return_value={"success": True}),
    ))
    monkeypatch.setattr(main_module.settings_mgr, "get_arr_settings", lambda mask=False: {
        "radarr_root_folder": "/movies", "radarr_quality_profile_id": 4, "search_on_add": True,
    })
    result = await main_module._run_watch_list_auto_action("acquire", {
        "title": "Arrival", "media_type": "movie", "ids": {"tmdb": 329865}
    })
    assert result["status"] == "acquired"
    main_module.arr_bridge.acquire_media.assert_awaited_once_with(
        "movie", candidate, root_folder_path="/movies", quality_profile_id=4,
        monitored=True, search_now=True, monitor_option="all",
    )

    main_module.arr_bridge.lookup_media.return_value = [{"title": "Wrong Arrival", "tmdbId": 999}]
    no_match = await main_module._run_watch_list_auto_action("acquire", {
        "title": "Arrival", "media_type": "movie", "ids": {"tmdb": 329865}
    })
    assert no_match["status"] == "skipped"
    assert main_module.arr_bridge.acquire_media.await_count == 1


@pytest.mark.asyncio
async def test_watch_list_availability_and_selected_overseerr_request(tmp_path, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-admin-secret")
    manager = WatchListManager(tmp_path / "availability.json")
    watch_list = manager.create_list("Tonight")
    movie = manager.add_item(watch_list["id"], {"title": "Arrival", "media_type": "movie", "ids": {"tmdb": 329865}})
    show = manager.add_item(watch_list["id"], {"title": "Severance", "media_type": "tv", "ids": {"tmdb": 95396, "tvdb": 371980}})
    extra_items = [manager.add_item(watch_list["id"], {"title": f"Extra {index}", "media_type": "movie", "ids": {"tmdb": 400000 + index}}) for index in range(8)]
    radarr = SimpleNamespace(
        is_configured=True,
        check_connection=AsyncMock(return_value={"status": "connected"}),
        get_movies=AsyncMock(return_value=[{"tmdbId": 329865, "hasFile": True}]),
    )
    sonarr = SimpleNamespace(
        is_configured=True,
        check_connection=AsyncMock(return_value={"status": "connected"}),
        get_series=AsyncMock(return_value=[]),
    )
    active_lookups = 0
    maximum_lookups = 0

    async def lookup_media(media_type, tmdb_id):
        nonlocal active_lookups, maximum_lookups
        active_lookups += 1
        maximum_lookups = max(maximum_lookups, active_lookups)
        await asyncio.sleep(0.001)
        active_lookups -= 1
        return {"mediaInfo": {"status": 5 if tmdb_id == 329865 else 2}} if tmdb_id in (329865, 95396) else {}

    overseerr = SimpleNamespace(
        is_configured=True,
        base_url="http://request-service",
        check_connection=AsyncMock(return_value={"status": "connected"}),
        get_media_details=AsyncMock(side_effect=lookup_media),
        has_media=AsyncMock(return_value=False),
        request_media=AsyncMock(return_value={"success": True, "request": {"id": 88}}),
    )
    monkeypatch.setattr(main_module, "watch_list_mgr", manager)
    monkeypatch.setattr(main_module, "arr_bridge", SimpleNamespace(sonarr=sonarr, radarr=radarr, overseerr=overseerr))
    monkeypatch.setattr(main_module.settings_mgr, "is_overseerr_enabled", lambda: True)
    transport = httpx.ASGITransport(app=app)
    headers = {"x-webhook-secret": "test-admin-secret"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        refreshed = await client.post(f"/api/watch-lists/{watch_list['id']}/availability", headers=headers)
        assert refreshed.status_code == 200
        assert refreshed.json()["checked_count"] == 10
        assert 1 < maximum_lookups <= 5
        stored = {item["id"]: item["availability"] for item in manager.get_all()["lists"][0]["items"]}
        assert stored[movie["id"]]["status"] == "available"
        assert stored[show["id"]]["status"] == "requested"
        assert stored[show["id"]]["services"]["overseerr"] == "requested"
        assert all(stored[item["id"]]["status"] == "missing" for item in extra_items)
        reloaded = WatchListManager(tmp_path / "availability.json")
        assert reloaded.get_all()["lists"][0]["items"][1]["availability"] == stored[show["id"]]

        request_headers = {**headers, "cookie": "csrf_token=test-csrf", "x-csrf-token": "test-csrf"}
        requested = await client.post(f"/api/watch-lists/{watch_list['id']}/items/{show['id']}/request", headers=request_headers)
        assert requested.status_code == 200
        assert requested.json()["status"] == "requested"
        overseerr.has_media.assert_awaited_once_with("tv", 95396)
        overseerr.request_media.assert_awaited_once_with("tv", 95396, seasons="all")

        overseerr.has_media.return_value = True
        duplicate = await client.post(f"/api/watch-lists/{watch_list['id']}/items/{show['id']}/request", headers=request_headers)
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "skipped"
        assert overseerr.request_media.await_count == 1

        overseerr.has_media.return_value = False
        overseerr.request_media.return_value = {"success": True, "skipped": True, "reason": "Already requested or available"}
        raced_duplicate = await client.post(f"/api/watch-lists/{watch_list['id']}/items/{show['id']}/request", headers=request_headers)
        assert raced_duplicate.status_code == 200
        assert raced_duplicate.json()["status"] == "skipped"


@pytest.mark.asyncio
async def test_watch_list_api_requires_admin_and_supports_crud(tmp_path, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-admin-secret")
    monkeypatch.setattr(main_module, "watch_list_mgr", WatchListManager(tmp_path / "api_lists.json"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        denied = await client.get("/api/watch-lists")
        assert denied.status_code == 401
        headers = {"x-webhook-secret": "test-admin-secret"}
        created = await client.post("/api/watch-lists", json={"name": "API list"}, headers=headers)
        assert created.status_code == 200
        list_id = created.json()["id"]
        added = await client.post(f"/api/watch-lists/{list_id}/items", json={"title": "Arrival", "media_type": "movie"}, headers=headers)
        assert added.status_code == 200
        fetched = await client.get("/api/watch-lists", headers=headers)
        assert fetched.json()["lists"][0]["items"][0]["title"] == "Arrival"
        cowatch = await client.get("/api/cowatch", headers=headers)
        assert cowatch.status_code == 200
        assert isinstance(cowatch.json()["status"]["shows"], list)
        invalid_import = await client.post("/api/watch-lists/import/preview", json={"data": {"version": 1, "lists": [{"name": "Bad", "items": None}]}}, headers=headers)
        assert invalid_import.status_code == 400


@pytest.mark.asyncio
async def test_full_backup_includes_watch_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-admin-secret")
    monkeypatch.setattr(Config, "BASE_DIR", tmp_path)
    watch_file = tmp_path / "data" / "watch_lists.json"
    watch_file.parent.mkdir()
    watch_file.write_text(json.dumps({"version": 1, "lists": []}), encoding="utf-8")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/backup", headers={"x-webhook-secret": "test-admin-secret"})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert json.loads(archive.read("data/watch_lists.json")) == {"version": 1, "lists": []}
