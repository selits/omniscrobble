"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


def test_emby_parser_handles_null_premiere_date():
    from app.emby_parser import parse_emby_webhook

    media = parse_emby_webhook({
        "Event": "playback.start",
        "Item": {"Type": "Movie", "Name": "Unknown Year", "ProductionYear": None, "PremiereDate": None},
    })
    assert media is not None
    assert media.year is None

def test_get_uptime_str():
    uptime = get_uptime_str()
    assert isinstance(uptime, str)
    assert len(uptime) > 0

@pytest.mark.asyncio(loop_scope="module")
async def test_prometheus_metrics_endpoint():
    """Verify that /metrics exports valid Prometheus text exposition format."""
    client = make_async_test_client(app)
    res = await client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    body = res.text

    # Check for Prometheus metric definitions
    assert "# HELP plex_trakt_uptime_seconds" in body
    assert "# TYPE plex_trakt_uptime_seconds gauge" in body
    assert "# HELP plex_trakt_requests_total" in body
    assert "# HELP plex_trakt_scrobbles_total" in body
    assert "# HELP plex_trakt_collections_total" in body
    assert "# HELP plex_trakt_active_streams" in body
    assert "plex_trakt_uptime_seconds" in body

def test_systemd_service_file_consistency():
    """Verify that the systemd user unit file contains the updated service description."""
    service_path = Path(__file__).resolve().parent.parent / "plex-trakt.service"
    assert service_path.exists(), "plex-trakt.service missing"
    content = service_path.read_text(encoding="utf-8")
    assert "Omniscrobble" in content
    assert "ExecStart=%h/plex-trakt-webhook/.venv/bin/python main.py" in content

@pytest.mark.asyncio(loop_scope="module")
async def test_cross_tracker_sync_manager_scan_and_execution():
    """Verify CrossTrackerSyncManager discrepancy detection and bi-directional reconciliation."""
    import asyncio
    from app.services.cross_tracker_sync import CrossTrackerSyncManager
    from unittest.mock import AsyncMock, MagicMock

    mock_trakt = MagicMock()
    mock_trakt.is_authenticated.return_value = True
    mock_trakt.get_watched_movies = AsyncMock(return_value=[
        {"movie": {"title": "Trakt Movie Only", "year": 2024, "ids": {"imdb": "tt9999001"}}, "last_watched_at": "2026-09-20T00:00:00Z"},
        {"movie": {"title": "Shared Movie", "year": 2020, "ids": {"imdb": "tt9999002"}}, "last_watched_at": "2026-09-21T00:00:00Z"},
    ])
    mock_trakt.get_watched_shows = AsyncMock(return_value=[
        {
            "show": {"title": "Trakt Show", "year": 2023, "ids": {"imdb": "tt8888001"}},
            "seasons": [{"number": 1, "episodes": [{"number": 1, "last_watched_at": "2026-09-22T00:00:00Z"}]}]
        }
    ])
    mock_trakt.get_ratings = AsyncMock(side_effect=lambda media_type: [
        {"movie": {"title": "Shared Movie", "year": 2020, "ids": {"imdb": "tt9999002"}}, "rating": 10}
    ] if media_type == "movies" else [])
    mock_trakt.sync_history = AsyncMock(return_value={"status": 200, "added": {"movies": 1}})
    mock_trakt.sync_ratings = AsyncMock(return_value={"status": 200, "added": {"movies": 1}})

    mock_simkl = MagicMock()
    mock_simkl.is_authenticated.return_value = True
    mock_simkl.get_all_items = AsyncMock(side_effect=lambda media_type: {
        "movies": [
            {"movie": {"title": "Shared Movie", "year": 2020, "ids": {"imdb": "tt9999002"}}, "status": "completed", "user_rating": 8},
            {"movie": {"title": "Simkl Movie Only", "year": 2022, "ids": {"imdb": "tt7777001"}}, "status": "completed", "last_watched_at": "2026-09-23T00:00:00Z"}
        ]
    } if media_type == "movies" else {"shows": [], "anime": []})
    mock_simkl.bulk_sync_history = AsyncMock(return_value={"status": "success"})
    mock_simkl.bulk_sync_ratings = AsyncMock(return_value={"status": "success"})

    manager = CrossTrackerSyncManager(trakt_client=mock_trakt, simkl_client=mock_simkl)
    assert manager.is_configured() is True

    # 1. Scan discrepancies
    diff = await manager.scan_discrepancies(force=True)
    assert len(diff) >= 4

    # Trakt Movie Only -> Trakt to Simkl
    t2s_movies = [d for d in diff if d["direction"] == "trakt_to_simkl" and d["sync_type"] == "watched" and d["title"] == "Trakt Movie Only"]
    assert len(t2s_movies) == 1
    assert t2s_movies[0]["ids"]["imdb"] == "tt9999001"

    # Shared Movie Rating mismatch -> Trakt rating 10 vs Simkl rating 8
    rating_diffs = [d for d in diff if d["sync_type"] == "rating" and d["title"] == "Shared Movie"]
    assert len(rating_diffs) == 2
    t2s_ratings = [d for d in rating_diffs if d["direction"] == "trakt_to_simkl"]
    s2t_ratings = [d for d in rating_diffs if d["direction"] == "simkl_to_trakt"]
    assert len(t2s_ratings) == 1
    assert t2s_ratings[0]["source_rating"] == 10
    assert t2s_ratings[0]["target_rating"] == 8
    assert len(s2t_ratings) == 1
    assert s2t_ratings[0]["source_rating"] == 8
    assert s2t_ratings[0]["target_rating"] == 10
    assert t2s_ratings[0]["rating_conflict_key"] == s2t_ratings[0]["rating_conflict_key"]

    # Simkl Movie Only -> Simkl to Trakt
    s2t_movies = [d for d in diff if d["direction"] == "simkl_to_trakt" and d["sync_type"] == "watched" and d["title"] == "Simkl Movie Only"]
    assert len(s2t_movies) == 1
    assert s2t_movies[0]["ids"]["imdb"] == "tt7777001"

    # 2. Execute reconciliation
    sync_res = await manager.execute_sync(direction="both")
    assert sync_res["status"] == "started"

    # Allow background sync task to complete
    await asyncio.sleep(0.1)

    assert mock_simkl.bulk_sync_history.called
    assert mock_trakt.sync_history.called

