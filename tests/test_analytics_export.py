import json

import httpx
import pytest

from app.config import Config
from app.services.analytics_manager import AnalyticsManager


def _event(event_id, *, user, server, title, shared, timestamp="2025-01-02 20:00:00"):
    return {
        "event_id": event_id,
        "operation_id": event_id,
        "timestamp": timestamp,
        "user": user,
        "server": server,
        "action": "mark_watched",
        "title": title,
        "type": "movie",
        "duration_ms": 7_200_000,
        "cowatch_status": {"synced": shared},
        "tracker_delivery": {"trakt": "success"},
        "delivery_status": "success",
        "player": "Living Room",
    }


@pytest.mark.asyncio
async def test_admin_activity_csv_applies_filters_and_escapes_unicode(tmp_path, monkeypatch):
    from app.main import app
    import app.main as main_module

    events_file = tmp_path / "events.json"
    events_file.write_text(json.dumps([
        _event("one", user="alice", server="Plex", title='Café, "film"', shared=False),
        _event("two", user="bob", server="Jellyfin", title="Shared title", shared=True),
        _event("formula", user="alice", server="Plex", title="=HYPERLINK(\"https://example.invalid\")", shared=False),
    ]), encoding="utf-8")
    monkeypatch.setattr(main_module, "analytics_mgr", AnalyticsManager(events_file=events_file, stats_file=tmp_path / "stats.json"))
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "admin_secret")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/analytics/export?format=csv&kind=activity")
        response = await client.get(
            "/api/analytics/export?token=admin_secret&format=csv&kind=activity&start_date=2025-01-01&end_date=2025-01-31&profile=alice&server=Plex&tracker=trakt&viewing=solo"
        )

    assert denied.status_code == 401
    assert response.status_code == 200
    assert "Café, \"\"film\"\"" in response.text
    assert "'=HYPERLINK" in response.text
    assert "Shared title" not in response.text
    assert "Content-Disposition" in response.headers


@pytest.mark.asyncio
async def test_json_summary_export_uses_filtered_dashboard_calculation(tmp_path, monkeypatch):
    from app.main import app
    import app.main as main_module

    events_file = tmp_path / "events.json"
    events_file.write_text(json.dumps([
        _event("one", user="alice", server="Plex", title="Arrival", shared=False),
        _event("two", user="bob", server="Jellyfin", title="Dune", shared=True),
    ]), encoding="utf-8")
    analytics = AnalyticsManager(events_file=events_file, stats_file=tmp_path / "stats.json")
    monkeypatch.setattr(main_module, "analytics_mgr", analytics)
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "admin_secret")
    monkeypatch.setattr(Config, "PLEX_ALLOWED_USERS", ["alice", "bob"])
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/export?token=admin_secret&format=json&kind=summary&profile=alice&media_type=movie&viewing=solo"
        )
        dashboard_response = await client.get(
            "/api/analytics/summary?token=admin_secret&profile=alice&media_type=movie&viewing=solo"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == 1
    assert data["filters"]["profile"] == "alice"
    assert data["summary"]["total_scrobbles"] == 1
    assert data["summary"]["movies_watched"] == 1
    assert "server local time" in data["timestamp_semantics"]
    assert dashboard_response.status_code == 200
    assert data["summary"]["total_scrobbles"] == dashboard_response.json()["total_scrobbles"]
