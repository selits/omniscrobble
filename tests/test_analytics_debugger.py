"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
async def test_chunked_discrepancy_streamer():
    """Verify cursor-based discrepancy pagination on reverse_sync_mgr and /api/sync/diff endpoint."""
    from app.services.reverse_sync_manager import reverse_sync_mgr

    client = make_async_test_client(app)

    # 1. ReverseSyncManager.get_chunked_diff
    mock_diff = [
        {"id": f"plex:movie:{i}", "title": f"Movie {i}", "action_recommended": "sync_to_trakt"}
        for i in range(10)
    ]
    with patch.object(reverse_sync_mgr, "_last_diff", mock_diff):
        chunk1 = reverse_sync_mgr.get_chunked_diff(cursor=0, limit=4)
        assert chunk1["count"] == 4
        assert chunk1["total"] == 10
        assert chunk1["cursor"] == 4
        assert chunk1["has_more"] is True
        assert chunk1["diff"][0]["id"] == "plex:movie:0"
        assert chunk1["diff"][3]["id"] == "plex:movie:3"

        chunk2 = reverse_sync_mgr.get_chunked_diff(cursor=4, limit=4)
        assert chunk2["count"] == 4
        assert chunk2["cursor"] == 8
        assert chunk2["has_more"] is True
        assert chunk2["diff"][0]["id"] == "plex:movie:4"

        chunk3 = reverse_sync_mgr.get_chunked_diff(cursor=8, limit=4)
        assert chunk3["count"] == 2
        assert chunk3["cursor"] is None
        assert chunk3["has_more"] is False

    # 2. GET /api/sync/diff with cursor and limit (Demo mode)
    res_demo = await client.get("/api/sync/diff?demo=true&cursor=0&limit=2")
    assert res_demo.status_code == 200
    d_data = res_demo.json()
    assert d_data["status"] == "ok"
    assert d_data["count"] == 2
    assert d_data["cursor"] == 2
    assert d_data["has_more"] is True

    # 3. GET /api/sync/diff with cursor and limit (Admin authenticated live)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"), \
         patch.object(reverse_sync_mgr, "scan_discrepancies", new_callable=AsyncMock) as mock_scan:
        mock_scan.return_value = mock_diff
        client.cookies.set("admin_token", "admin_secret")

        # Full diff when cursor/limit omitted
        res_full = await client.get("/api/sync/diff")
        assert res_full.status_code == 200
        assert res_full.json()["count"] == 10
        assert "cursor" not in res_full.json()

        # Chunked diff when cursor/limit provided
        res_chunked = await client.get("/api/sync/diff?cursor=2&limit=3")
        assert res_chunked.status_code == 200
        c_data = res_chunked.json()
        assert c_data["count"] == 3
        assert c_data["total"] == 10
        assert c_data["cursor"] == 5
        assert c_data["has_more"] is True
        assert c_data["diff"][0]["id"] == "plex:movie:2"

def test_webhook_debugger_unit():
    """Verify in-memory ring buffer recording, sanitization, and history management."""
    from app.services.webhook_debugger import WebhookDebugger, sanitize_headers, sanitize_payload

    # 1. Header and payload sanitization
    headers = {
        "User-Agent": "PlexMediaServer/1.32",
        "Authorization": "Bearer secret_oauth_token",
        "X-Plex-Token": "secret_plex_token",
        "X-Webhook-Secret": "my_webhook_secret",
        "Content-Type": "application/json",
    }
    clean_h = sanitize_headers(headers)
    assert clean_h["User-Agent"] == "PlexMediaServer/1.32"
    assert clean_h["Authorization"] == "[REDACTED]"
    assert clean_h["X-Plex-Token"] == "[REDACTED]"
    assert clean_h["X-Webhook-Secret"] == "[REDACTED]"

    payload = {
        "event": "media.scrobble",
        "token": "sensitive_plex_token_xyz",
        "nested": {
            "password": "secret_password",
            "api_key": "sensitive_key",
            "title": "Severance",
        },
        "list": [{"secret": "item_secret", "name": "val"}],
    }
    clean_p = sanitize_payload(payload)
    assert clean_p["event"] == "media.scrobble"
    assert clean_p["token"] == "[REDACTED]"
    assert clean_p["nested"]["password"] == "[REDACTED]"
    assert clean_p["nested"]["api_key"] == "[REDACTED]"
    assert clean_p["nested"]["title"] == "Severance"
    assert clean_p["list"][0]["secret"] == "[REDACTED]"
    assert clean_p["list"][0]["name"] == "val"

    # 2. Ring buffer operations
    debugger = WebhookDebugger(maxlen=3)
    e1 = debugger.record(source="plex", endpoint="/webhook", payload={"title": "Movie 1", "token": "p1"}, status="received")
    e2 = debugger.record(source="jellyfin", endpoint="/webhook/jellyfin", payload={"Item": {"title": "Show 1"}}, status="received")
    e3 = debugger.record(source="emby", endpoint="/webhook/emby", payload={"Item": {"title": "Show 2"}}, status="received")

    history = debugger.get_history()
    assert len(history) == 3
    assert history[0]["id"] == e3["id"]  # Most recent first
    assert history[0]["payload"]["Item"]["title"] == "Show 2"

    # Update status
    updated = debugger.update_status(e2["id"], status="processed", reason="Scrobbled successfully")
    assert updated is True
    found = debugger.get_payload(e2["id"])
    assert found is not None
    assert found["status"] == "processed"
    assert found["reason"] == "Scrobbled successfully"

    # Maxlen eviction
    e4 = debugger.record(source="radarr", endpoint="/radarr", payload={"title": "Movie 2"}, status="received")
    history_after = debugger.get_history()
    assert len(history_after) == 3
    assert debugger.get_payload(e1["id"]) is None  # e1 evicted

    # Clear
    debugger.clear()
    assert len(debugger.get_history()) == 0

def test_analytics_threshold_stop_uses_played_duration():
    from app.services.analytics_manager import AnalyticsManager

    event = {
        "duration_ms": 120 * 60 * 1000,
        "view_offset_ms": 90 * 60 * 1000,
        "progress": "75.0%",
    }
    assert AnalyticsManager._extract_item_minutes(event, "movie", "scrobble_stop") == 90
    event["view_offset_ms"] = 0
    assert AnalyticsManager._extract_item_minutes(event, "movie", "scrobble_stop") == 90
    assert AnalyticsManager._extract_item_minutes(event, "movie", "manual_scrobble") == 120

def test_analytics_manager_unit(tmp_path):
    """Verify watch statistics aggregation, period filtering, and OmniWrapped retrospectives."""
    from app.services.analytics_manager import AnalyticsManager

    events_file = tmp_path / "test_events.json"
    stats_file = tmp_path / "test_stats.json"

    # Demo summary
    mgr = AnalyticsManager(events_file=events_file, stats_file=stats_file)
    demo_sum = mgr.get_summary(demo=True)
    assert demo_sum["total_scrobbles"] == 182
    assert demo_sum["movies_watched"] == 42
    assert demo_sum["cowatch_ratio_percent"] == 37
    assert "Plex" in demo_sum["server_distribution"]

    # Demo OmniWrapped
    demo_wrapped = mgr.get_omniwrapped(year=2026, demo=True)
    assert demo_wrapped["year"] == 2026
    assert "OmniWrapped" in demo_wrapped["headline"]
    assert demo_wrapped["archetype"] in [
        "The Living Room Co-Watcher",
        "The Silver Screen Cinephile",
        "The Grand Homelab Binger",
        "The Curated Media Connoisseur",
    ]
    assert demo_wrapped["total_scrobbles"] == 182

    # Real data calculation
    import datetime
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    events_data = [
        {
            "timestamp": now_str,
            "action": "scrobble (100.0%)",
            "type": "movie",
            "title": "Dune: Part Two",
            "details": "Plex Scrobbler",
            "player": "Apple TV",
            "cowatch_status": {"synced": False},
        },
        {
            "timestamp": now_str,
            "action": "scrobble (100.0%)",
            "type": "episode",
            "title": "Severance S01E01",
            "details": "Jellyfin Scrobbler",
            "player": "Shield TV",
            "cowatch_status": {"synced": True, "reason": "Whitelisted"},
        },
        {
            "timestamp": now_str,
            "action": "rating",
            "type": "movie",
            "title": "Dune: Part Two",
            "rating": 9,
        },
    ]
    with open(events_file, "w", encoding="utf-8") as f:
        json.dump(events_data, f)

    summary = mgr.get_summary(period="all")
    assert summary["total_scrobbles"] == 2
    assert summary["movies_watched"] == 1
    assert summary["episodes_watched"] == 1
    assert summary["ratings_submitted"] == 1
    assert summary["cowatch_ratio_percent"] > 0
    assert "Plex" in summary["server_distribution"]
    assert "Jellyfin" in summary["server_distribution"]

    wrapped = mgr.get_omniwrapped(year=2026)
    assert wrapped["movies_watched"] == 1
    assert wrapped["episodes_watched"] == 1
    assert wrapped["cowatch_breakdown"]["shared_hours"] > 0

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_debugger_and_replay_endpoints():
    """Verify REST API endpoints for /api/debug/webhooks and /api/debug/replay."""
    client = make_async_test_client(app)

    # 1. Access control when WEBHOOK_SECRET is active
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        # Unauthorized without admin token
        res_unauth = await client.get("/api/debug/webhooks")
        assert res_unauth.status_code == 403

        res_del_unauth = await client.delete("/api/debug/webhooks")
        assert res_del_unauth.status_code == 403

        res_rep_unauth = await client.post("/api/debug/replay", json={"source": "plex", "payload": {}})
        assert res_rep_unauth.status_code == 403

        # Demo mode bypasses authorization
        res_demo = await client.get("/api/debug/webhooks?demo=true")
        assert res_demo.status_code == 200
        assert "webhooks" in res_demo.json()
        assert len(res_demo.json()["webhooks"]) > 0

        # Authorized with admin cookie
        client.cookies.set("admin_token", "admin_secret")
        res_auth = await client.get("/api/debug/webhooks")
        assert res_auth.status_code == 200

        # Delete / clear buffer
        res_del = await client.delete("/api/debug/webhooks")
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "ok"

        # Replay dry-run simulation
        replay_plex = {
            "source": "plex",
            "dispatch": False,
            "payload": {
                "event": "media.scrobble",
                "Account": {"title": "selits"},
                "Metadata": {
                    "type": "movie",
                    "title": "Replay Sci-Fi Movie",
                    "year": 2026,
                    "librarySectionTitle": "Movies",
                },
                "Player": {"title": "Living Room TV"},
            },
        }
        res_rep_sim = await client.post("/api/debug/replay", json=replay_plex)
        assert res_rep_sim.status_code == 200
        data_sim = res_rep_sim.json()
        assert data_sim["status"] == "simulated"
        assert data_sim["parsed"]["title"] == "Replay Sci-Fi Movie"

        # Replay with unsupported source
        res_rep_bad = await client.post("/api/debug/replay", json={"source": "unknown_app", "payload": {}})
        assert res_rep_bad.status_code == 200
        assert res_rep_bad.json()["status"] == "error"

        # Replay standalone player payload
        replay_standalone = {
            "source": "standalone",
            "dispatch": False,
            "payload": {
                "title": "Standalone Stream",
                "media_type": "movie",
                "year": 2025,
                "player": "Infuse",
            },
        }
        res_rep_st = await client.post("/api/debug/replay", json=replay_standalone)
        assert res_rep_st.status_code == 200
        assert res_rep_st.json()["status"] == "simulated"
        assert res_rep_st.json()["parsed"]["title"] == "Standalone Stream"

@pytest.mark.asyncio(loop_scope="module")
async def test_analytics_endpoints_and_dashboard_integration():
    """Verify /api/analytics/summary, /api/analytics/wrapped, and dashboard rendering."""
    client = make_async_test_client(app)

    # 1. GET /api/analytics/summary
    res_sum = await client.get("/api/analytics/summary?period=all&demo=true")
    assert res_sum.status_code == 200
    sum_data = res_sum.json()
    assert "total_watch_hours" in sum_data
    assert "server_distribution" in sum_data
    assert "top_shows" in sum_data

    # 2. GET /api/analytics/wrapped
    res_wrp = await client.get("/api/analytics/wrapped?year=2026&demo=true")
    assert res_wrp.status_code == 200
    wrp_data = res_wrp.json()
    assert wrp_data["year"] == 2026
    assert "archetype" in wrp_data
    assert "cowatch_breakdown" in wrp_data

    # 3. Dashboard rendering includes Analytics Card and Inspector Modal
    res_dash = await client.get("/")
    assert res_dash.status_code == 200
    html = res_dash.text
    assert "Personal Analytics & Viewing Habits" in html
    assert "OmniWrapped" in html
    assert "webhook-debugger-modal" in html
    assert "omniwrapped-modal" in html

def test_webhook_debugger_redaction_comprehensive():
    """Verify that all variants of secrets/credentials in headers and payloads are redacted."""
    from app.services.webhook_debugger import sanitize_headers, sanitize_payload

    headers = {
        "ApiKey": "abc-key",
        "accessToken": "bearer-token",
        "Proxy-Authorization": "Basic 123",
        "X-Api-Key": "xyz-key",
        "X-Emby-Token": "emby-token",
        "Set-Cookie": "session=123",
        "Normal-Header": "Normal-Value",
    }
    cleaned_h = sanitize_headers(headers)
    assert cleaned_h["ApiKey"] == "[REDACTED]"
    assert cleaned_h["accessToken"] == "[REDACTED]"
    assert cleaned_h["Proxy-Authorization"] == "[REDACTED]"
    assert cleaned_h["X-Api-Key"] == "[REDACTED]"
    assert cleaned_h["X-Emby-Token"] == "[REDACTED]"
    assert cleaned_h["Set-Cookie"] == "[REDACTED]"
    assert cleaned_h["Normal-Header"] == "Normal-Value"

    payload = {
        "apiKey": "123",
        "api_key": "456",
        "oauth_token": "789",
        "admin_password": "pass",
        "user_passwd": "pw",
        "client_secret": "sec",
        "credentials": {"cert": "data"},
        "title": "Inception",
    }
    cleaned_p = sanitize_payload(payload)
    assert cleaned_p["apiKey"] == "[REDACTED]"
    assert cleaned_p["api_key"] == "[REDACTED]"
    assert cleaned_p["oauth_token"] == "[REDACTED]"
    assert cleaned_p["admin_password"] == "[REDACTED]"
    assert cleaned_p["user_passwd"] == "[REDACTED]"
    assert cleaned_p["client_secret"] == "[REDACTED]"
    assert cleaned_p["credentials"] == "[REDACTED]"
    assert cleaned_p["title"] == "Inception"

@pytest.mark.asyncio(loop_scope="module")
async def test_replay_dispatch_security_gate():
    """Verify that dispatching replayed webhooks requires strict admin authorization."""
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        body = {
            "source": "standalone",
            "dispatch": True,
            "payload": {
                "title": "Replay Movie",
                "media_type": "movie",
                "action": "scrobble",
            },
        }

        # 1. Non-admin with ?demo=true attempting dispatch should get 403 Forbidden
        res_demo_dispatch = await client.post("/api/debug/replay?demo=true", json=body)
        assert res_demo_dispatch.status_code == 403
        assert "Admin authorization required" in res_demo_dispatch.json()["detail"]

        # 2. Non-admin with ?demo=true with dispatch=False (dry-run simulation) is allowed
        body_sim = dict(body, dispatch=False)
        res_demo_sim = await client.post("/api/debug/replay?demo=true", json=body_sim)
        assert res_demo_sim.status_code == 200
        assert res_demo_sim.json()["status"] == "simulated"

        # 3. Admin with cookie can dispatch
        client.cookies.set("admin_token", "admin_secret")
        with patch("app.main.process_media_event", new_callable=AsyncMock) as mock_pme:
            mock_pme.return_value = {"status": "scrobbled"}
            res_admin_dispatch = await client.post("/api/debug/replay", json=body)
            assert res_admin_dispatch.status_code == 200
            assert res_admin_dispatch.json()["status"] == "dispatched"
            assert mock_pme.await_count == 1

def test_analytics_privacy_masking_and_calendar_filtering():
    """Verify non-admin privacy masking for devices/partner and dynamic year calendar filtering."""
    from app.services.analytics_manager import AnalyticsManager

    events_data = [
        {
            "action": "scrobble",
            "media_type": "episode",
            "title": "Better Call Saul S01E01",
            "player": "Living Room Shield TV",
            "server": "plex",
            "cowatch_reason": "eligible: matched user and device",
            "timestamp": "2025-06-15T12:00:00Z",
            "duration": 50,
            "genres": ["Crime", "Drama"],
        },
        {
            "action": "scrobble",
            "media_type": "episode",
            "title": "The Simpsons S35E01",
            "player": "Bedroom Apple TV",
            "server": "jellyfin",
            "cowatch_reason": "Ineligible: device not whitelisted",
            "timestamp": "2026-03-10T12:00:00Z",
            "duration": 22,
            "genres": ["Animation", "Comedy"],
        },
    ]

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(events_data))), \
         patch.object(Config, "CO_WATCH_USER", "jane_doe"):

        am = AnalyticsManager()

        # Calendar year 2025: should only include Better Call Saul
        sum_2025 = am.get_summary(period="all", year=2025, is_admin=True)
        assert sum_2025["episodes_watched"] == 1
        assert sum_2025["top_shows"][0]["show"] == "Better Call Saul"
        assert sum_2025["top_genres"][0]["genre"] == "Crime"
        assert sum_2025["cowatch_hours"] > 0

        # Calendar year 2026: should only include The Simpsons
        sum_2026 = am.get_summary(period="all", year=2026, is_admin=True)
        assert sum_2026["episodes_watched"] == 1
        assert sum_2026["top_shows"][0]["show"] == "The Simpsons"
        assert sum_2026["cowatch_hours"] == 0  # Ineligible was correctly not counted as cowatch

        # Non-admin privacy masking
        sum_masked = am.get_summary(period="all", year=2025, is_admin=False)
        assert sum_masked["top_devices"][0]["device"] == "Player 1"

        wrapped_masked = am.get_omniwrapped(year=2025, is_admin=False)
        assert wrapped_masked["cowatch_breakdown"]["partner_user"] == "ja****"

        # Admin unmasked
        wrapped_admin = am.get_omniwrapped(year=2025, is_admin=True)
        assert wrapped_admin["cowatch_breakdown"]["partner_user"] == "jane_doe"

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_error_marks_debugger_entry():
    """Verify that an exception in process_media_event updates webhook_debugger status to 'error'."""
    from app.services.webhook_debugger import webhook_debugger
    client = make_async_test_client(app, raise_server_exceptions=False)

    webhook_debugger.clear()

    payload = {
        "event": "media.scrobble",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "movie",
            "title": "Crash Movie",
            "year": 2026,
            "librarySectionTitle": "Movies",
        },
        "Player": {"title": "Living Room TV"},
    }

    with patch.object(Config, "PLEX_ALLOWED_USERS", ["selits"]), \
         patch("app.main.settings_mgr.is_server_enabled", return_value=True), \
         patch("app.main.process_media_event", side_effect=RuntimeError("Simulated pipeline crash")):
        res = await client.post("/webhook", data={"payload": json.dumps(payload)})
        assert res.status_code == 500

    history = webhook_debugger.get_history()
    assert len(history) > 0
    latest = history[0]
    assert latest["status"] == "error"
    assert "Simulated pipeline crash" in latest["reason"]

def test_analytics_ignores_non_watch_events(tmp_path):
    """Start/pause/bypassed/unscrobble/collection events must not count as completed watches."""
    import datetime
    from app.services.analytics_manager import AnalyticsManager

    events_file = tmp_path / "events.json"
    stats_file = tmp_path / "stats.json"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def ev(action, **extra):
        base = {"timestamp": now_str, "action": action, "type": "movie", "title": "Film (2024)", "duration_ms": 6000000}
        base.update(extra)
        return base

    # events.json is newest-first; the unscrobble is the oldest event so it has no earlier watch to cancel
    events_file.write_text(json.dumps([
        ev("scrobble_start"),
        ev("scrobble_pause"),
        ev("playback_stopped"),
        ev("bypassed"),
        ev("manual_start"),
        ev("collection"),
        ev("skipped_media.play"),
        ev("scrobble_stop"),
        ev("mark_watched"),
        ev("manual_scrobble"),
        ev("unscrobble"),
    ]), encoding="utf-8")

    summary = AnalyticsManager(events_file=events_file, stats_file=stats_file).get_summary(period="all")
    assert summary["total_scrobbles"] == 3
    assert summary["total_watch_formatted"] == "5h 0m"  # 3 x 100 min

def test_analytics_unscrobble_cancels_matching_watch(tmp_path):
    """An unscrobble cancels the older matching watch; a newer rewatch and other titles are kept."""
    import datetime
    from app.services.analytics_manager import AnalyticsManager

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def ev(action, title):
        return {"timestamp": now_str, "action": action, "type": "movie", "title": title, "duration_ms": 6000000}

    # events.json is stored newest-first
    events_file = tmp_path / "events.json"
    events_file.write_text(json.dumps([
        ev("mark_watched", "Film A (2024)"),   # newest: rewatch, kept
        ev("unscrobble", "Film A (2024)"),     # undoes the older watch below
        ev("mark_watched", "Film A (2024)"),   # cancelled
        ev("mark_watched", "Film B (2023)"),   # unrelated, kept
    ]), encoding="utf-8")

    summary = AnalyticsManager(events_file=events_file, stats_file=tmp_path / "stats.json").get_summary(period="all")
    assert summary["total_scrobbles"] == 2
    assert summary["unique_titles"] == 2
    assert summary["total_watch_formatted"] == "3h 20m"  # 2 x 100 min

def test_load_scrobble_stats_backfills_watch_minutes(tmp_path, monkeypatch):
    """Legacy stats.json without watch_minutes is backfilled from lifetime counts on upgrade."""
    import app.main as main_mod

    legacy = tmp_path / "stats.json"
    legacy.write_text(json.dumps({"total": 10, "movies": 4, "episodes": 6, "ratings": 1, "collections": 0}), encoding="utf-8")
    monkeypatch.setattr(main_mod, "STATS_FILE", legacy)
    assert main_mod.load_scrobble_stats()["watch_minutes"] == 4 * 90 + 6 * 35

    legacy.write_text(json.dumps({"total": 10, "movies": 4, "episodes": 6, "watch_minutes": 123}), encoding="utf-8")
    assert main_mod.load_scrobble_stats()["watch_minutes"] == 123

