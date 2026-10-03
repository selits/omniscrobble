"""API client implementations for Omniscrobble."""
from __future__ import annotations

from app.clients.anilist_client import AniListClient
from app.clients.emby_api_client import EmbyApiClient
from app.clients.jellyfin_api_client import JellyfinApiClient
from app.clients.kitsu_client import KitsuClient
from app.clients.letterboxd_client import LetterboxdClient
from app.clients.mal_client import MyAnimeListClient
from app.clients.mdblist_client import MDBListClient
from app.clients.mediabrowser_api_client import BaseMediaBrowserClient
from app.clients.plex_api_client import PlexApiClient
from app.clients.radarr_client import RadarrClient, parse_radarr_webhook
from app.clients.serializd_client import SerializdClient
from app.clients.simkl_client import SimklClient
from app.clients.sonarr_client import SonarrClient, parse_sonarr_webhook
from app.clients.tmdb_client import TMDbClient
from app.clients.trakt_client import TraktClient

__all__ = [
    "AniListClient",
    "BaseMediaBrowserClient",
    "EmbyApiClient",
    "JellyfinApiClient",
    "KitsuClient",
    "LetterboxdClient",
    "MediaBrowserApiClient",
    "MDBListClient",
    "MyAnimeListClient",
    "PlexApiClient",
    "RadarrClient",
    "SerializdClient",
    "SimklClient",
    "SonarrClient",
    "TMDbClient",
    "TraktClient",
    "parse_radarr_webhook",
    "parse_sonarr_webhook",
]
