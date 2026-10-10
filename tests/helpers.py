"""Shared setup for legacy integration tests split into focused modules."""

import json
import time
from pathlib import Path

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from app.config import Config
from app.main import (
    app,
    get_uptime_str,
    mask_username,
    queue_mgr,
    recent_events,
    scrobble_stats,
    trakt,
    sonarr,
    simkl,
    notifier,
    playback_mgr,
    reverse_sync_mgr,
    demo_mgr,
)
from app.services.queue_manager import QueueManager, process_queue
from app.clients.trakt_client import TraktClient
from app.services.notifier import Notifier, format_media_title, get_trakt_url
from app.services.playback_manager import PlaybackManager
from app.plex_parser import ParsedMedia, parse_plex_ids, parse_plex_webhook
from app.services.user_manager import UserClientManager, user_mgr
from app.services.cowatch_manager import CowatchManager, cowatch_mgr
from app.services.log_manager import log_mgr
from app.clients.anilist_client import AniListClient
from app.clients.mal_client import MyAnimeListClient
from app.services.anime_resolver import AnimeResolver
from app.services.multi_tracker import MultiTrackerManager
from app.main import anilist, mal, anime_resolver, multi_tracker


def make_async_test_client(app, **kwargs):
    """Build an HTTPX async client for API tests."""
    raise_server_exceptions = kwargs.pop("raise_server_exceptions", True)
    transport = httpx.ASGITransport(
        app=app,
        raise_app_exceptions=raise_server_exceptions,
        client=("testclient", 50000),
    )
    return AsyncASGITestClient(
        transport=transport, base_url="http://testserver", **kwargs
    )


async def dashboard_asset_text(client):
    """Return the separately served dashboard assets for source assertions."""
    css = await client.get("/static/dashboard.css")
    themes = await client.get("/static/dashboard-theme.css")
    script = await client.get("/static/dashboard.js")
    assert css.status_code == themes.status_code == script.status_code == 200
    return themes.text + css.text + script.text


class AsyncASGITestClient(httpx.AsyncClient):
    """Keep test requests separate from mocks of outbound HTTPX methods."""

    async def get(self, url, **kwargs):
        return await super().request("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await super().request("POST", url, **kwargs)

    async def put(self, url, **kwargs):
        return await super().request("PUT", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await super().request("DELETE", url, **kwargs)

    async def patch(self, url, **kwargs):
        return await super().request("PATCH", url, **kwargs)


@pytest.fixture
def _ensure_servers_enabled_for_tests():
    """Isolate settings and enable media server ingestion for webhook tests."""
    import copy
    from app.services.settings_manager import settings_mgr

    playback_mgr.clear()
    orig_settings = copy.deepcopy(settings_mgr._settings)
    orig_custom_notif = copy.deepcopy(settings_mgr._custom_notifications)
    settings_mgr.set_server_enabled("plex", True)
    settings_mgr.set_server_enabled("jellyfin", True)
    settings_mgr.set_server_enabled("emby", True)
    yield
    settings_mgr._settings = orig_settings
    settings_mgr._custom_notifications = orig_custom_notif
    settings_mgr._save_settings()
    orig_recon = orig_settings.get("reconciliation", {})
    reverse_sync_mgr.update_config(
        plex_url=orig_recon.get("plex_url", ""),
        plex_token=orig_recon.get("plex_token", ""),
        jellyfin_url=orig_recon.get("jellyfin_url", ""),
        jellyfin_token=orig_recon.get("jellyfin_token", ""),
        emby_url=orig_recon.get("emby_url", ""),
        emby_token=orig_recon.get("emby_token", ""),
    )
    playback_mgr.clear()
