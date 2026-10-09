import httpx
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.clients.tmdb_client import TMDbClient
from app.services.cross_tracker_sync import CrossTrackerSyncManager


@pytest.mark.asyncio
async def test_tmdb_rated_items_follows_pages_sequentially():
    requested_pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)
        return httpx.Response(200, json={
            "page": page,
            "total_pages": 2,
            "results": [{"id": page, "rating": page * 2}],
        })

    client = TMDbClient(api_key="test", session_id="session", account_id="42", transport=httpx.MockTransport(handler))
    try:
        items = await client.get_rated_items("movie")
    finally:
        await client.close()

    assert requested_pages == [1, 2]
    assert [item["id"] for item in items] == [1, 2]


@pytest.mark.asyncio
async def test_tmdb_rated_items_stops_on_rate_limit():
    client = TMDbClient(
        api_key="test", session_id="session", account_id="42",
        transport=httpx.MockTransport(lambda request: httpx.Response(429)),
    )
    try:
        with pytest.raises(RuntimeError, match="HTTP 429"):
            await client.get_rated_items("tv")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_trakt_tmdb_scan_requires_exact_tmdb_ids_and_applies_selected_rating():
    trakt = MagicMock()
    trakt.is_authenticated.return_value = True
    trakt.get_ratings = AsyncMock(side_effect=[
        [
            {"movie": {"title": "Dune", "year": 2021, "ids": {"tmdb": 438631}}, "rating": 9},
            {"movie": {"title": "No shared ID", "year": 2020, "ids": {"imdb": "tt123"}}, "rating": 8},
        ],
        [{"show": {"title": "Severance", "year": 2022, "ids": {"tmdb": 95396}}, "rating": 8}],
    ])
    tmdb = MagicMock()
    tmdb.is_authenticated.return_value = True
    tmdb.get_rated_items = AsyncMock(side_effect=[
        [{"id": 438631, "rating": 7}],
        [{"id": 95396, "rating": 8}],
    ])
    tmdb.sync_rating = AsyncMock(return_value={"status": "success"})
    manager = CrossTrackerSyncManager(trakt_client=trakt, tmdb_client=tmdb)

    diff = await manager.scan_discrepancies(force=True)
    trakt_action = next(item for item in diff if item["direction"] == "trakt_to_tmdb")
    tmdb_action = next(item for item in diff if item["direction"] == "tmdb_to_trakt")
    assert trakt_action["source_of_truth"] == "trakt"
    assert trakt_action["match_confidence"] == "exact_tmdb_id"
    assert trakt_action["target_rating"] == 7
    assert tmdb_action["source_rating"] == 7

    result = await manager.execute_sync(item_ids=[trakt_action["id"]])
    assert result["status"] == "started"
    await asyncio.sleep(0.01)
    tmdb.sync_rating.assert_awaited_once_with(media_type="movie", tmdb_id="438631", rating=9.0)
    assert manager._sync_progress["success"] == 1


@pytest.mark.asyncio
async def test_tmdb_only_rating_can_be_applied_to_trakt():
    trakt = MagicMock()
    trakt.is_authenticated.return_value = True
    trakt.get_ratings = AsyncMock(side_effect=[[], []])
    trakt.sync_ratings = AsyncMock(return_value={"added": {"movies": 1}})
    tmdb = MagicMock()
    tmdb.is_authenticated.return_value = True
    tmdb.get_rated_items = AsyncMock(side_effect=[
        [{"id": 550, "title": "Fight Club", "rating": 8.5}], []
    ])
    manager = CrossTrackerSyncManager(trakt_client=trakt, tmdb_client=tmdb)

    diff = await manager.scan_discrepancies(force=True)
    assert len(diff) == 1
    assert diff[0]["direction"] == "tmdb_to_trakt"
    assert diff[0]["ids"] == {"tmdb": "550"}
    result = await manager.execute_sync(item_ids=[diff[0]["id"]])
    assert result["status"] == "started"
    await asyncio.sleep(0.01)
    trakt.sync_ratings.assert_awaited_once_with({"movies": [{
        "title": "Fight Club", "rating": 9, "ids": {"tmdb": "550"},
    }]})


def test_rating_conflict_policies_never_apply_both_sides_of_one_mismatch():
    manager = CrossTrackerSyncManager()
    trakt_action = {
        "id": "trakt-side", "direction": "trakt_to_simkl", "sync_type": "rating",
        "rating_conflict_key": "movie:imdb:tt1", "trakt_rated_at": "2026-09-20T00:00:00Z",
        "simkl_rated_at": "2026-09-21T00:00:00Z",
    }
    simkl_action = {
        "id": "simkl-side", "direction": "simkl_to_trakt", "sync_type": "rating",
        "rating_conflict_key": "movie:imdb:tt1", "trakt_rated_at": "2026-09-20T00:00:00Z",
        "simkl_rated_at": "2026-09-21T00:00:00Z",
    }
    manager._last_diff = [trakt_action, simkl_action]

    manual_all, skipped, error = manager._resolve_rating_conflicts(
        [trakt_action, simkl_action], "manual", allow_manual_skip=True,
    )
    assert manual_all == []
    assert skipped == 1
    assert error is None

    manual_selected, _, error = manager._resolve_rating_conflicts(
        [trakt_action, simkl_action], "manual", allow_manual_skip=False,
    )
    assert manual_selected == [trakt_action, simkl_action]
    assert "Both directions" in error

    newest, _, error = manager._resolve_rating_conflicts(
        [trakt_action, simkl_action], "newest", allow_manual_skip=False,
    )
    assert [item["id"] for item in newest] == ["simkl-side"]
    assert error is None

    trakt_preferred, _, error = manager._resolve_rating_conflicts(
        [simkl_action], "trakt", allow_manual_skip=False,
    )
    assert [item["id"] for item in trakt_preferred] == ["trakt-side"]
    assert error is None


def test_newest_rating_policy_requires_both_timestamps():
    manager = CrossTrackerSyncManager()
    left = {"id": "left", "direction": "trakt_to_simkl", "sync_type": "rating", "rating_conflict_key": "k"}
    right = {"id": "right", "direction": "simkl_to_trakt", "sync_type": "rating", "rating_conflict_key": "k"}
    manager._last_diff = [left, right]
    _, _, error = manager._resolve_rating_conflicts([left, right], "newest", allow_manual_skip=False)
    assert "rated_at timestamps" in error


def test_tmdb_conflict_policy_selects_one_direction_by_source_or_timestamp():
    manager = CrossTrackerSyncManager()
    trakt_action = {
        "id": "trakt", "direction": "trakt_to_tmdb", "sync_type": "rating",
        "rating_conflict_key": "movie:tmdb:550", "trakt_rated_at": "2026-09-20T00:00:00Z",
        "tmdb_rated_at": "2026-09-21T00:00:00Z",
    }
    tmdb_action = {
        **trakt_action, "id": "tmdb", "direction": "tmdb_to_trakt",
    }
    manager._last_diff = [trakt_action, tmdb_action]

    preferred, _, error = manager._resolve_rating_conflicts([trakt_action], "tmdb", allow_manual_skip=False)
    assert [item["id"] for item in preferred] == ["tmdb"]
    assert error is None
    newest, _, error = manager._resolve_rating_conflicts([trakt_action, tmdb_action], "newest", allow_manual_skip=False)
    assert [item["id"] for item in newest] == ["tmdb"]
    assert error is None


def test_applied_rating_conflict_provenance_is_bounded_and_persistent(tmp_path):
    path = tmp_path / "cross_sync_provenance.json"
    manager = CrossTrackerSyncManager(provenance_file=path)
    manager._record_provenance([{
        "id": "action-1", "rating_conflict_key": "movie:tmdb:550",
        "media_type": "movie", "title": "Fight Club", "year": 1999,
        "direction": "tmdb_to_trakt", "resolution_policy": "tmdb", "source_rating": 8.5,
    }])

    record = manager.get_provenance()[0]
    assert record["source"] == "tmdb"
    assert record["policy"] == "tmdb"
    assert record["rating"] == 8.5
    assert record["applied_at"]
    reloaded = CrossTrackerSyncManager(provenance_file=path)
    assert reloaded.get_provenance() == [record]
