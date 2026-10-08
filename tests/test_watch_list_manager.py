import json
import io
import zipfile
import httpx
import pytest

from app.config import Config
from app.main import app
from app.services.watch_list_manager import WatchListError, WatchListManager


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


def test_json_and_text_round_trip_and_merge_duplicates(tmp_path):
    path = tmp_path / "watch_lists.json"
    manager = WatchListManager(path)
    watch_list = manager.create_list("Anime queue")
    manager.add_item(watch_list["id"], {"title": "Frieren", "media_type": "anime", "year": 2023, "ids": {"anilist": 154587}})

    text = manager.export_text()
    preview = manager.preview_import(text)
    assert preview["list_count"] == 1
    assert preview["item_count"] == 1
    assert preview["lists"][0]["items"][0]["year"] == 2023

    result = manager.apply_import(text)
    assert result["duplicates_skipped"] == 1
    exported = manager.get_all()
    assert exported["lists"][0]["items"][0]["ids"]["anilist"] == 154587

    round_trip = WatchListManager(tmp_path / "copy.json")
    round_trip.apply_import(exported, replace=True)
    assert round_trip.get_all()["lists"][0]["items"][0]["year"] == 2023


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
