"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
async def test_queue_collection_retry(tmp_path):
    """Verify that queued sync_collection events are successfully processed by background worker."""
    queue = QueueManager(tmp_path / "collection_retry.db")
    col_payload = {"movies": [{"title": "Gladiator II", "year": 2024}]}
    item_id = queue.enqueue("sync_collection", col_payload, username="default")

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_collection", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = {"added": {"movies": 1}}

        stats = await process_queue(trakt, queue)
        assert stats["succeeded"] >= 1
        assert queue.get_pending_count() == 0

@pytest.mark.asyncio(loop_scope="module")
async def test_process_queue_sync_watchlist():
    """Verify that queued sync_watchlist events are properly dequeued and sent to Trakt."""
    queue_mgr.clear_queue()
    watchlist_payload = {"movies": [{"title": "Inception", "year": 2010}]}
    queue_mgr.enqueue("sync_watchlist", watchlist_payload, username="default")
    assert queue_mgr.get_pending_count() == 1

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_watchlist", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = {"added": {"movies": 1}}

        stats = await process_queue(trakt, queue_mgr)
        assert stats["succeeded"] >= 1
        assert queue_mgr.get_pending_count() == 0
        mock_sync.assert_awaited_once_with(watchlist_payload)

@pytest.mark.asyncio(loop_scope="module")
async def test_watchlist_transient_error_enqueues():
    """Verify POST /api/watchlist enqueues the payload if Trakt returns a transient HTTP error."""
    client = make_async_test_client(app)
    queue_mgr.clear_queue()

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_watchlist", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = {"error": "Service Unavailable", "status_code": 503}

        res = await client.post("/api/watchlist", json={"media_type": "movie", "title": "Interstellar", "year": 2014})
        assert res.status_code == 200
        assert queue_mgr.get_pending_count() == 1
        pending = queue_mgr.get_pending()
        assert pending[0]["event_type"] == "sync_watchlist"
        assert pending[0]["payload"]["movies"][0]["title"] == "Interstellar"

    queue_mgr.clear_queue()

@pytest.mark.asyncio(loop_scope="module")
async def test_jellyfin_and_emby_offline_queue_on_trakt_temporary_error():
    """Verify offline retry queue persists failed Jellyfin/Emby scrobbles during Trakt outages."""
    client = make_async_test_client(app)
    jf_payload = {
        "NotificationType": "PlaybackStop",
        "NotificationUsername": "selits",
        "ItemType": "Movie",
        "Name": "Oppenheimer",
        "Year": 2023,
        "RunTimeTicks": 100000000000,
        "PlaybackPositionTicks": 95000000000,  # 95% -> scrobble_stop
        "ProviderIds": {"Imdb": "tt15398776"},
    }

    initial_queue_count = queue_mgr.get_pending_count()

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop:
        # Simulate Trakt 503 outage
        mock_stop.return_value = {"error": "503 Service Unavailable: Trakt maintenance"}

        res = await client.post("/webhook/jellyfin", json=jf_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["action"] == "scrobble_stop"

        # Verify offline queue preserved the event
        assert queue_mgr.get_pending_count() == initial_queue_count + 1
        pending = queue_mgr.get_pending()
        assert any(item["event_type"] == "scrobble_stop" and item.get("username") == "default" for item in pending)

def test_async_tracker_delivery_updates_existing_event(monkeypatch):
    """Async tracker outcomes update and persist the originating activity event."""
    from app.main import _delivery_category, _tracker_result_state, log_event, recent_events, update_event_tracker_delivery

    assert _tracker_result_state({"status": 503, "error": "upstream unavailable"}) == "failed"
    assert _tracker_result_state({"scrobble": {"status": 201}, "history": {"status": 503}}) == "failed"
    assert _tracker_result_state({"status": "error", "error": "network timeout"}) == "failed"
    assert _tracker_result_state({"status": "error", "error": "network timeout", "queued": True}) == "queued"
    assert _tracker_result_state({"status": 400, "error": "invalid payload"}) == "failed"
    assert _delivery_category("failed", {"status": 503}) == "transient"
    assert _delivery_category("failed", {"status": 401}) == "authorization_or_configuration"
    assert _delivery_category("skipped", {"reason": "unsupported media type"}) == "unsupported"

    original_events = list(recent_events)
    monkeypatch.setattr("app.main.save_recent_events", lambda: None)
    monkeypatch.setattr("app.main.activity_delivery_mgr.save_events", lambda: None)
    monkeypatch.setattr("app.main.save_scrobble_stats", lambda: None)
    media = ParsedMedia(event="media.scrobble", username="delivery-test", media_type="movie", title="Delivery Test", progress=100)
    recent_events.clear()
    try:
        event_id = log_event(media, "mark_watched", {"status": "success"})
        assert recent_events[0]["tracker_delivery"] == {"trakt": "success"}
        assert recent_events[0]["operation_id"] == event_id
        assert recent_events[0]["tracker_delivery_details"]["trakt"]["attempts"] == 1

        update_event_tracker_delivery(event_id, {"simkl": "success", "anilist": "failed"})
        assert recent_events[0]["tracker_delivery"] == {
            "trakt": "success", "simkl": "success", "anilist": "failed"
        }
        assert recent_events[0]["delivery_status"] == "partial"
        update_event_tracker_delivery(event_id, {"simkl": "queued"})
        simkl_detail = recent_events[0]["tracker_delivery_details"]["simkl"]
        assert simkl_detail["attempts"] == 2
        assert [entry["state"] for entry in simkl_detail["history"]] == ["success", "queued"]
        assert "retry" in simkl_detail["reason"]
    finally:
        recent_events.clear()
        recent_events.extend(original_events)

@pytest.mark.asyncio
async def test_mirror_delivery_is_recorded_on_originating_event(monkeypatch):
    import asyncio

    from app.main import log_event, recent_events, record_mirror_delivery

    original_events = list(recent_events)
    monkeypatch.setattr("app.main.save_recent_events", lambda: None)
    monkeypatch.setattr("app.main.activity_delivery_mgr.save_events", lambda: None)
    monkeypatch.setattr("app.main.save_scrobble_stats", lambda: None)
    media = ParsedMedia(event="media.scrobble", username="mirror-test", media_type="movie", title="Mirror Test", progress=100)
    recent_events.clear()
    try:
        event_id = log_event(media, "mark_watched", {"status": "success"})
        await record_mirror_delivery(event_id, asyncio.sleep(0, result={
            "status": "completed",
            "results": {
                "mirrored": [{"server": "jellyfin"}],
                "failed": [{"server": "emby", "error": "api_call_failed"}],
                "skipped": [],
            },
        }))
        mirror = recent_events[0]["tracker_delivery_details"]["media_servers"]
        assert mirror["state"] == "partial"
        assert "api_call_failed" in mirror["reason"]
    finally:
        recent_events.clear()
        recent_events.extend(original_events)

def test_queue_delivery_updates_originating_event(monkeypatch):
    from app.main import log_event, recent_events, record_queue_delivery

    original_events = list(recent_events)
    monkeypatch.setattr("app.main.save_recent_events", lambda: None)
    monkeypatch.setattr("app.main.activity_delivery_mgr.save_events", lambda: None)
    monkeypatch.setattr("app.main.save_scrobble_stats", lambda: None)
    monkeypatch.setattr("app.main.queue_mgr.get_event_queue_state", lambda _event_id, tracker=None, exclude_ids=None: "success")
    media = ParsedMedia(event="media.scrobble", username="queue-test", media_type="movie", title="Queue Link Test", progress=100)
    recent_events.clear()
    try:
        event_id = log_event(media, "mark_watched", {"status": 503, "error": "HTTP 503"})
        record_queue_delivery(event_id, "sync_history", 42, "success", "", 2)
        details = recent_events[0]["tracker_delivery_details"]
        assert details["Trakt · sync history · queue 42"]["attempts"] == 2
        assert details["Trakt · sync history · queue 42"]["history"][-1]["state"] == "success"
        assert recent_events[0]["tracker_delivery"]["trakt"] == "success"
    finally:
        recent_events.clear()
        recent_events.extend(original_events)

@pytest.mark.asyncio(loop_scope="module")
async def test_event_delivery_retry_endpoint_requires_admin_and_duplicate_confirmation(tmp_path, monkeypatch):
    import app.main as main_mod

    client = make_async_test_client(app)
    qm = QueueManager(tmp_path / "event_retry.db")
    original_events = list(recent_events)
    monkeypatch.setattr(main_mod, "queue_mgr", qm)
    monkeypatch.setattr(main_mod.activity_delivery_mgr, "queue", qm)
    monkeypatch.setattr(main_mod, "save_recent_events", lambda: None)
    monkeypatch.setattr(main_mod.activity_delivery_mgr, "save_events", lambda: None)
    monkeypatch.setattr(main_mod, "save_scrobble_stats", lambda: None)
    media = ParsedMedia(event="media.scrobble", username="retry-test", media_type="movie", title="Retry Test", progress=100)
    recent_events.clear()
    try:
        event_id = main_mod.log_event(media, "mark_watched", {"status": 503, "error": "HTTP 503"})
        queue_id = qm.enqueue("sync_history", {"movies": []}, event_id=event_id)
        qm.mark_failure(queue_id, "HTTP 503", max_retries=1)

        with patch.object(Config, "WEBHOOK_SECRET", "retry-secret"):
            denied = await client.post(f"/api/events/{event_id}/retry", json={"queue_item_id": queue_id})
            assert denied.status_code == 401

            confirmation_required = await client.post(
                f"/api/events/{event_id}/retry",
                json={"queue_item_id": queue_id},
                headers={"x-webhook-secret": "retry-secret"},
            )
            assert confirmation_required.status_code == 409

            retried = await client.post(
                f"/api/events/{event_id}/retry",
                json={"queue_item_id": queue_id, "confirm_duplicate_history": True},
                headers={"x-webhook-secret": "retry-secret"},
            )
            assert retried.status_code == 200
            assert retried.json()["status"] == "queued"
            assert qm.get_pending_count() == 1
            details = recent_events[0]["tracker_delivery_details"]
            assert details[f"Trakt · sync history · queue {queue_id}"]["state"] == "queued"
    finally:
        recent_events.clear()
        recent_events.extend(original_events)

@pytest.mark.asyncio(loop_scope="module")
async def test_queue_prune_and_completed_status(tmp_path):
    """Verify QueueManager transitions items to status 'completed' and prunes records older than retention threshold."""
    from app.services.queue_manager import QueueManager
    import time

    db_file = tmp_path / "test_prune_queue.db"
    qm = QueueManager(db_file)

    now = int(time.time())
    day_sec = 86400

    # 1. Enqueue 4 items
    id_old_completed = qm.enqueue("sync_history", {"title": "Old Completed"}, error="", username="alice")
    id_recent_completed = qm.enqueue("sync_history", {"title": "Recent Completed"}, error="", username="alice")
    id_old_failed = qm.enqueue("sync_history", {"title": "Old Failed"}, error="Timeout", username="alice")
    id_pending = qm.enqueue("sync_history", {"title": "Still Pending"}, error="", username="alice")

    # Mark success for completed items
    qm.mark_success(id_old_completed)
    qm.mark_success(id_recent_completed)

    # Mark failure for old failed item
    qm.mark_failure(id_old_failed, error="Server error", max_retries=1)

    # Manually backdate old_completed (100 days ago) and old_failed (100 days ago)
    with qm._get_connection() as conn:
        old_time = now - (100 * day_sec)
        conn.execute("UPDATE queued_events SET created_at = ?, completed_at = ? WHERE id = ?", (old_time, old_time, id_old_completed))
        conn.execute("UPDATE queued_events SET created_at = ? WHERE id = ?", (old_time, id_old_failed))
        conn.commit()

    # Verify counts before pruning
    counts = qm.get_all_count()
    assert counts["completed"] == 2
    assert counts["failed"] == 1
    assert counts["pending"] == 1

    # Prune records older than 90 days
    pruned_count = qm.prune_queue(days=90)
    assert pruned_count == 2  # old_completed and old_failed

    # Verify state after pruning
    with qm._get_connection() as conn:
        cursor = conn.execute("SELECT id, status FROM queued_events ORDER BY id ASC")
        remaining = cursor.fetchall()
        remaining_ids = [r["id"] for r in remaining]
        assert id_old_completed not in remaining_ids
        assert id_old_failed not in remaining_ids
        assert id_recent_completed in remaining_ids
        assert id_pending in remaining_ids

    # Test POST /api/queue/prune endpoint
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        # Unauthenticated -> 401
        res_unauth = await client.post("/api/queue/prune")
        assert res_unauth.status_code == 401

        # Demo mode -> 200
        res_demo = await client.post("/api/queue/prune?demo=true")
        assert res_demo.status_code == 200
        assert res_demo.json()["pruned"] == 0

        # Admin authorized
        client.cookies.set("admin_token", "admin_secret")
        res_admin = await client.post("/api/queue/prune", json={"days": 90})
        assert res_admin.status_code == 200
        assert "pruned" in res_admin.json()
        assert res_admin.json()["retention_days"] == 90

@pytest.mark.asyncio(loop_scope="module")
async def test_process_queue_keeps_user_history_pending_until_authenticated(tmp_path):
    db_file = tmp_path / "process_queue_unauthenticated.db"
    qm = QueueManager(db_file)
    qm.enqueue("sync_history", {"episodes": [{"title": "Carrie"}]}, username="partner")

    disconnected_client = MagicMock(spec=TraktClient)
    disconnected_client.is_authenticated.return_value = False
    profiles = MagicMock()
    profiles.get_client.return_value = disconnected_client

    stats = await process_queue(MagicMock(spec=TraktClient), qm, user_mgr=profiles)

    assert stats["succeeded"] == 0
    assert stats["failed"] == 0
    assert qm.get_pending_count() == 1

@pytest.mark.asyncio(loop_scope="module")
async def test_process_queue_marks_permanent_http_errors_failed(tmp_path):
    qm = QueueManager(tmp_path / "process_queue_permanent.db")
    qm.enqueue("sync_ratings", {"movies": []}, event_id="event-permanent")
    mock_client = MagicMock(spec=TraktClient)
    mock_client.sync_ratings = AsyncMock(return_value={"status": 401, "error": "Unauthorized"})
    reported = []

    stats = await process_queue(mock_client, qm, on_result=lambda *args: reported.append(args))

    assert stats["failed"] == 1
    assert qm.get_pending_count() == 0
    assert qm.get_event_queue_state("event-permanent") == "failed"
    assert reported[0][3:5] == ("failed", "Unauthorized")

@pytest.mark.asyncio(loop_scope="module")
async def test_process_queue_reports_linked_event_outcome(tmp_path):
    qm = QueueManager(tmp_path / "process_queue_linked.db")
    qm.enqueue("sync_ratings", {"movies": []}, event_id="event-linked")
    mock_client = MagicMock(spec=TraktClient)
    mock_client.sync_ratings = AsyncMock(return_value={"status": 201})
    reported = []

    stats = await process_queue(mock_client, qm, on_result=lambda *args: reported.append(args))

    assert stats["succeeded"] == 1
    assert reported == [("event-linked", "sync_ratings", 1, "success", "", 2)]
    assert qm.get_event_queue_state("event-linked") == "success"

@pytest.mark.asyncio(loop_scope="module")
async def test_process_queue_success(tmp_path):
    db_file = tmp_path / "process_queue.db"
    qm = QueueManager(db_file)
    qm.enqueue("scrobble_stop", {"movie": {"title": "Interstellar"}})
    qm.enqueue("sync_ratings", {"movies": [{"title": "Inception", "rating": 10}]})

    mock_client = MagicMock(spec=TraktClient)
    mock_client.scrobble_stop = AsyncMock(return_value={"action": "scrobble"})
    mock_client.sync_ratings = AsyncMock(return_value={"added": {"movies": 1}})

    stats = await process_queue(mock_client, qm)
    assert stats["processed"] == 2
    assert stats["succeeded"] == 2
    assert stats["failed"] == 0
    assert qm.get_pending_count() == 0

@pytest.mark.asyncio(loop_scope="module")
async def test_process_queue_transient_failure(tmp_path):
    db_file = tmp_path / "process_queue_fail.db"
    qm = QueueManager(db_file)
    qm.enqueue("scrobble_stop", {"movie": {"title": "Tenet"}})
    qm.enqueue("sync_history", {"episodes": []})

    mock_client = MagicMock(spec=TraktClient)
    # First item encounters 503
    mock_client.scrobble_stop = AsyncMock(return_value={"status": 503, "error": "Service Unavailable"})

    stats = await process_queue(mock_client, qm)
    assert stats["succeeded"] == 0
    assert stats["failed"] == 1
    # Ambiguous history delivery becomes failed for manual confirmation; the
    # following item remains pending and was not dispatched in this pass.
    assert qm.get_pending_count() == 1
    assert qm.get_item(1)["status"] == "failed"

@pytest.mark.asyncio(loop_scope="module")
async def test_queue_endpoints():
    client = make_async_test_client(app)

    with patch.object(Config, "WEBHOOK_SECRET", "super_secret"):
        # 1. POST /api/queue/retry without auth -> 401
        res_retry_denied = await client.post("/api/queue/retry")
        assert res_retry_denied.status_code == 401

        # 2. POST /api/queue/clear without auth -> 401
        res_clear_denied = await client.post("/api/queue/clear")
        assert res_clear_denied.status_code == 401

        # 3. With admin cookie -> 200
        client.cookies.set("admin_token", "super_secret")
        with patch("app.main.process_queue", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = {"processed": 0, "succeeded": 0, "failed": 0}
            res_retry_ok = await client.post("/api/queue/retry")
            assert res_retry_ok.status_code == 200
            assert "pending_count" in res_retry_ok.json()

        res_clear_ok = await client.post("/api/queue/clear")
        assert res_clear_ok.status_code == 200
        assert res_clear_ok.json()["pending_count"] == 0
        client.cookies.clear()

def test_queue_manager_crud(tmp_path):
    db_file = tmp_path / "test_queue.db"
    qm = QueueManager(db_file)

    # 1. Initially empty
    assert qm.get_pending_count() == 0
    assert qm.get_pending() == []

    # 2. Enqueue items
    id1 = qm.enqueue("scrobble_stop", {"movie": {"title": "Matrix"}}, error="Timeout", event_id="event-1")
    id2 = qm.enqueue("sync_ratings", {"shows": [{"title": "Dark"}]}, error="503 Service Unavailable")
    assert id1 > 0
    assert id2 > 0
    assert qm.get_pending_count() == 2

    # 3. Retrieve pending
    items = qm.get_pending(limit=10)
    assert len(items) == 2
    assert items[0]["id"] == id1
    assert items[0]["event_type"] == "scrobble_stop"
    assert items[0]["payload"]["movie"]["title"] == "Matrix"
    assert items[0]["event_id"] == "event-1"
    assert items[1]["id"] == id2
    assert items[1]["event_type"] == "sync_ratings"

    # 4. Mark failure and max_retries
    qm.mark_failure(id1, error="Still down", max_retries=2)
    items = qm.get_pending()
    assert len(items) == 2
    assert items[0]["retry_count"] == 1
    assert items[0]["status"] == "pending"

    # Second failure triggers status="failed"
    qm.mark_failure(id1, error="Permanent fail", max_retries=2)
    assert qm.get_pending_count() == 1  # Only id2 is still pending
    assert qm.get_event_queue_state("event-1") == "failed"

    # 5. Bulk retry leaves history writes failed for duplicate-aware recovery.
    reset_count = qm.retry_all_failed()
    assert reset_count == 0
    assert qm.get_pending_count() == 1
    assert qm.get_item(id1)["status"] == "failed"

    # 6. Mark success
    qm.mark_success(id1)
    assert qm.get_pending_count() == 1

    # 7. Clear queue
    qm.clear_queue()
    assert qm.get_pending_count() == 0

def test_queue_manager_scoped_retry_requires_event_and_duplicate_confirmation(tmp_path):
    qm = QueueManager(tmp_path / "scoped_retry.db")
    risky_id = qm.enqueue("sync_history", {"movies": []}, event_id="event-1")
    qm.mark_failure(risky_id, "HTTP 503", max_retries=1)

    assert qm.retry_failed_item(risky_id, "wrong-event", confirm_duplicate_history=True) == "not_found"
    assert qm.retry_failed_item(risky_id, "event-1") == "confirmation_required"
    assert qm.retry_failed_item(risky_id, "event-1", confirm_duplicate_history=True) == "queued"
    assert qm.get_pending_count() == 1

    auth_id = qm.enqueue("sync_ratings", {"movies": []}, error="Unauthorized", event_id="event-2")
    qm.mark_failure(auth_id, "Unauthorized", max_retries=1)
    assert qm.retry_failed_item(auth_id, "event-2") == "not_retryable"

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_automatic_enqueue_on_failure():
    client = make_async_test_client(app)
    plex_sample = {
        "event": "media.scrobble",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "movie",
            "title": "Gladiator",
            "year": 2000,
            "duration": 9000000,
            "viewOffset": 8500000,
            "Guid": [{"id": "imdb://tt0172495"}],
        },
    }

    initial_pending = queue_mgr.get_pending_count()
    initial_queue_ids = {item["id"] for item in queue_mgr.get_pending(limit=1000)}

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync:

        # Simulate 503 Service Unavailable from Trakt
        mock_stop.return_value = {"status": 503, "error": "Trakt API Unavailable"}
        mock_sync.return_value = {"status": 503, "error": "Trakt API Unavailable"}

        res = await client.post("/webhook", data={"payload": json.dumps(plex_sample)})
        assert res.status_code == 200
        # Check that both stop and history events were enqueued
        assert queue_mgr.get_pending_count() >= initial_pending + 2
        linked_items = [item for item in queue_mgr.get_pending(limit=1000) if item["id"] not in initial_queue_ids]
        assert len(linked_items) >= 2
        assert all(item["event_id"] == recent_events[0]["event_id"] for item in linked_items)

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_filter_dispatch_and_queue_retry_end_to_end(tmp_path, monkeypatch):
    """A parsed webhook is filtered or recorded, dispatched, queued on outage, then retried."""
    import asyncio
    import copy
    import app.main as main_mod

    original_stats = dict(main_mod.scrobble_stats)
    original_events = list(main_mod.recent_events)
    original_sessions = copy.deepcopy(main_mod.playback_mgr.sessions)
    original_finished = copy.deepcopy(main_mod.playback_mgr.recently_finished)
    test_queue = QueueManager(tmp_path / "webhook-pipeline-queue.db")
    monkeypatch.setattr(main_mod, "queue_mgr", test_queue)
    monkeypatch.setattr(main_mod.activity_delivery_mgr, "queue", test_queue)
    monkeypatch.setattr(main_mod, "save_recent_events", lambda: None)
    monkeypatch.setattr(main_mod.activity_delivery_mgr, "save_events", lambda: None)
    monkeypatch.setattr(main_mod, "save_scrobble_stats", lambda: None)

    filter_titles: list[str] = []
    original_filter = main_mod.settings_mgr.is_media_allowed

    def filter_media(media):
        filter_titles.append(media.title)
        if media.title == "Filtered Film":
            return False, "excluded by integration test"
        return original_filter(media)

    monkeypatch.setattr(main_mod.settings_mgr, "is_media_allowed", filter_media)
    monkeypatch.setattr(main_mod.settings_mgr, "is_tracker_enabled", lambda tracker: tracker == "simkl")
    dispatch_finished = asyncio.Event()
    original_dispatch = main_mod.execute_multi_tracker_dispatch

    async def dispatch_and_signal(*args, **kwargs):
        await original_dispatch(*args, **kwargs)
        dispatch_finished.set()

    monkeypatch.setattr(main_mod, "execute_multi_tracker_dispatch", dispatch_and_signal)
    client = make_async_test_client(app)
    base_payload = {
        "event": "media.scrobble", "user": True,
        "Account": {"id": 192, "title": "pipeline-viewer"},
        "Metadata": {
            "librarySectionType": "movie", "type": "movie", "year": 2025,
            "duration": 9_000_000, "viewOffset": 8_500_000,
            "Guid": [{"id": "imdb://tt1234567"}],
        },
    }

    try:
        with patch.object(main_mod.trakt, "is_authenticated", return_value=True), \
             patch.object(main_mod.trakt, "scrobble_stop", new_callable=AsyncMock, return_value={"status": 503, "error": "Service Unavailable"}) as trakt_stop, \
             patch.object(main_mod.trakt, "sync_history", new_callable=AsyncMock, return_value={"status": 503, "error": "Service Unavailable"}) as trakt_history, \
             patch.object(main_mod.simkl, "is_enabled", return_value=True), \
             patch.object(main_mod.simkl, "is_authenticated", return_value=True), \
             patch.object(main_mod.simkl, "scrobble_stop", new_callable=AsyncMock, return_value={"status": "success"}) as simkl_stop, \
             patch.object(main_mod.simkl, "sync_history", new_callable=AsyncMock, return_value={"status": "success"}) as simkl_history:
            filtered_payload = {**base_payload, "Metadata": {**base_payload["Metadata"], "title": "Filtered Film"}}
            filtered = await client.post("/webhook", data={"payload": json.dumps(filtered_payload)})
            assert filtered.json()["status"] == "ignored"
            assert "excluded by integration test" in filtered.json()["reason"]
            assert trakt_stop.await_count == 0

            accepted_payload = {**base_payload, "Metadata": {**base_payload["Metadata"], "title": "Queued Film"}}
            accepted = await client.post("/webhook", data={"payload": json.dumps(accepted_payload)})
            assert accepted.status_code == 200
            assert accepted.json()["status"] == "success"
            event = main_mod.recent_events[0]
            event_id = event["event_id"]
            assert event["title"] == "Queued Film (2025)"
            assert event["media_payload"]["ids"]["imdb"] == "tt1234567"
            assert event["tracker_delivery"]["trakt"] == "queued"
            assert test_queue.get_event_queue_state(event_id) == "queued"
            assert [row["event_id"] for row in test_queue.get_pending(limit=10)] == [event_id, event_id]
            await asyncio.wait_for(dispatch_finished.wait(), timeout=2)
            simkl_stop.assert_awaited_once()
            simkl_history.assert_awaited_once()
            assert main_mod.recent_events[0]["tracker_delivery"]["simkl"] == "success"

        retry_client = MagicMock(spec=TraktClient)
        retry_client.scrobble_stop = AsyncMock(return_value={"status": 201})
        retry_client.sync_history = AsyncMock(return_value={"status": 201})
        retry_result = await process_queue(retry_client, test_queue, on_result=main_mod.record_queue_delivery)

        assert retry_result == {"processed": 2, "succeeded": 2, "failed": 0}
        final_event = next(item for item in main_mod.recent_events if item["event_id"] == event_id)
        assert final_event["delivery_status"] == "success"
        assert final_event["tracker_delivery"]["trakt"] == "success"
        assert final_event["tracker_delivery"]["simkl"] == "success"
        assert test_queue.get_event_queue_state(event_id) == "success"
        assert filter_titles == ["Filtered Film", "Queued Film"]
        assert trakt_stop.await_count == trakt_history.await_count == 1
    finally:
        await client.aclose()
        main_mod.playback_mgr.sessions.clear()
        main_mod.playback_mgr.sessions.update(original_sessions)
        main_mod.playback_mgr.recently_finished = original_finished
        main_mod.scrobble_stats.clear()
        main_mod.scrobble_stats.update(original_stats)
        main_mod.recent_events.clear()
        main_mod.recent_events.extend(original_events)

