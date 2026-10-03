#!/usr/bin/env python3
"""Omniscrobble Local Webhook Simulator CLI.

Crafts and dispatches media server webhook payloads (Plex, Jellyfin, Emby,
Sonarr, Radarr) to test ingestion, scrobbling, ratings, and edge cases locally
without waiting for playback on a physical media server.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # Fallback to urllib.request if httpx is unavailable

import urllib.error
import urllib.request


def build_plex_payload(
    scenario: str,
    title: str,
    year: int | None,
    show: str | None,
    season: int,
    episode: int,
    rating: int,
    progress: float,
    user: str,
) -> dict[str, Any]:
    is_episode = bool(show) or "episode" in scenario
    duration_ms = 3000000 if is_episode else 7200000
    view_offset_ms = int(duration_ms * (progress / 100.0))

    if scenario in ("movie-finish", "episode-finish", "scrobble"):
        event = "media.scrobble"
        progress = 100.0
        view_offset_ms = duration_ms
    elif scenario in ("movie-start", "episode-start", "play"):
        event = "media.play"
        progress = 0.0
        view_offset_ms = 0
    elif scenario in ("movie-pause", "episode-pause", "pause"):
        event = "media.pause"
    elif scenario in ("movie-stop", "episode-stop", "stop"):
        event = "media.stop"
    elif scenario in ("rate-movie", "rate-episode", "rate"):
        event = "media.rate"
    elif scenario in ("library-new", "download"):
        event = "library.new"
    else:
        event = "media.scrobble"

    meta: dict[str, Any] = {
        "title": title or ("Pilot" if is_episode else "Inception"),
        "duration": duration_ms,
        "viewOffset": view_offset_ms,
        "Guid": [{"id": "imdb://tt1375666"}, {"id": "tmdb://27205"}],
    }

    if is_episode:
        meta["type"] = "episode"
        meta["librarySectionType"] = "show"
        meta["grandparentTitle"] = show or "Severance"
        meta["parentTitle"] = f"Season {season}"
        meta["parentIndex"] = season
        meta["index"] = episode
        if year:
            meta["year"] = year
    else:
        meta["type"] = "movie"
        meta["librarySectionType"] = "movie"
        meta["year"] = year or 2010

    if event == "media.rate":
        meta["rating"] = rating

    return {
        "event": event,
        "user": True,
        "Account": {"id": 1, "title": user},
        "Metadata": meta,
    }


def build_jellyfin_payload(
    scenario: str,
    title: str,
    year: int | None,
    show: str | None,
    season: int,
    episode: int,
    rating: int,
    progress: float,
    user: str,
) -> dict[str, Any]:
    is_episode = bool(show) or "episode" in scenario
    item_title = title or ("Pilot" if is_episode else "Inception")
    item_year = year or (2022 if is_episode else 2010)

    payload: dict[str, Any] = {
        "NotificationUsername": user,
        "ItemType": "Episode" if is_episode else "Movie",
        "Name": item_title,
        "Year": item_year,
        "Provider_imdb": "tt1375666",
        "Provider_tmdb": 27205,
    }

    if is_episode:
        payload["SeriesName"] = show or "Severance"
        payload["SeasonNumber"] = season
        payload["EpisodeNumber"] = episode

    if scenario in ("movie-start", "episode-start", "play"):
        payload["NotificationType"] = "PlaybackStart"
        payload["PlaybackPositionTicks"] = 0
    elif scenario in ("movie-pause", "episode-pause", "pause"):
        payload["NotificationType"] = "PlaybackProgress"
        payload["IsPaused"] = True
        payload["PlaybackPositionTicks"] = int(progress * 1000000)
    elif scenario in ("movie-stop", "episode-stop", "stop"):
        payload["NotificationType"] = "PlaybackStop"
        payload["PlaybackPositionTicks"] = int(progress * 1000000)
    elif scenario in ("movie-finish", "episode-finish", "scrobble"):
        payload["NotificationType"] = "UserDataSaved"
        payload["PlayedToCompletion"] = True
        payload["Played"] = True
    elif scenario in ("rate-movie", "rate-episode", "rate"):
        payload["NotificationType"] = "UserDataSaved"
        payload["UserRating"] = rating
    elif scenario in ("library-new", "download"):
        payload["NotificationType"] = "ItemAdded"
    else:
        payload["NotificationType"] = "UserDataSaved"
        payload["PlayedToCompletion"] = True

    return payload


def build_emby_payload(
    scenario: str,
    title: str,
    year: int | None,
    show: str | None,
    season: int,
    episode: int,
    rating: int,
    progress: float,
    user: str,
) -> dict[str, Any]:
    is_episode = bool(show) or "episode" in scenario
    item_title = title or ("Pilot" if is_episode else "Inception")
    item_year = year or (2022 if is_episode else 2010)

    item: dict[str, Any] = {
        "Type": "Episode" if is_episode else "Movie",
        "Name": item_title,
        "ProductionYear": item_year,
        "ProviderIds": {"Imdb": "tt1375666", "Tmdb": "27205"},
    }

    if is_episode:
        item["SeriesName"] = show or "Severance"
        item["ParentIndexNumber"] = season
        item["IndexNumber"] = episode

    if scenario in ("movie-start", "episode-start", "play"):
        event = "playback.start"
    elif scenario in ("movie-pause", "episode-pause", "pause"):
        event = "playback.pause"
    elif scenario in ("movie-stop", "episode-stop", "stop"):
        event = "playback.stop"
    elif scenario in ("movie-finish", "episode-finish", "scrobble"):
        event = "playback.scrobble"
    elif scenario in ("rate-movie", "rate-episode", "rate"):
        event = "user.rating"
        item["UserRating"] = rating
    elif scenario in ("library-new", "download"):
        event = "library.new"
    else:
        event = "playback.scrobble"

    return {
        "Event": event,
        "User": {"Name": user},
        "Item": item,
        "PlaybackPositionTicks": int(progress * 1000000),
    }


def build_sonarr_payload(
    scenario: str,
    title: str,
    year: int | None,
    show: str | None,
    season: int,
    episode: int,
) -> dict[str, Any]:
    if scenario == "test":
        return {"eventType": "Test"}

    return {
        "eventType": "Download",
        "series": {
            "title": show or "Severance",
            "year": year or 2022,
            "tvdbId": 371980,
            "imdbId": "tt11280740",
        },
        "episodes": [
            {
                "seasonNumber": season,
                "episodeNumber": episode,
                "title": title or "Good News About Hell",
            }
        ],
    }


def build_radarr_payload(
    scenario: str,
    title: str,
    year: int | None,
) -> dict[str, Any]:
    if scenario == "test":
        return {"eventType": "Test"}

    return {
        "eventType": "Download",
        "movie": {
            "title": title or "Inception",
            "year": year or 2010,
            "tmdbId": 27205,
            "imdbId": "tt1375666",
        },
    }


def send_http_request(
    url: str,
    headers: dict[str, str],
    json_data: dict[str, Any] | None = None,
    form_data: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Dispatch HTTP request using httpx if available, otherwise urllib.request."""
    if httpx is not None:
        with httpx.Client(timeout=10.0) as client:
            if form_data is not None:
                resp = client.post(url, headers=headers, data=form_data)
            else:
                resp = client.post(url, headers=headers, json=json_data)
            return resp.status_code, resp.text

    # Standard library fallback
    if form_data is not None:
        import urllib.parse
        encoded_data = urllib.parse.urlencode(form_data).encode("utf-8")
        req_headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    else:
        encoded_data = json.dumps(json_data).encode("utf-8")
        req_headers = {**headers, "Content-Type": "application/json"}

    req = urllib.request.Request(url, data=encoded_data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, body


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Omniscrobble Local Webhook Simulator CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--server",
        choices=["plex", "jellyfin", "emby", "sonarr", "radarr"],
        default="plex",
        help="Target media server or acquisition tool to simulate",
    )
    parser.add_argument(
        "--scenario",
        default="movie-finish",
        help="Scenario preset: movie-finish, movie-start, movie-pause, movie-stop, "
             "episode-start, episode-finish, episode-pause, episode-stop, "
             "rate-movie, rate-episode, download, test",
    )
    parser.add_argument("--title", default="", help="Custom media title (e.g. 'Inception' or 'Pilot')")
    parser.add_argument("--show", default="", help="Custom show title (e.g. 'Severance')")
    parser.add_argument("--season", type=int, default=1, help="TV season number")
    parser.add_argument("--episode", type=int, default=1, help="TV episode number")
    parser.add_argument("--year", type=int, default=None, help="Release year")
    parser.add_argument("--rating", type=int, default=10, help="Rating (1-10) for rating events")
    parser.add_argument("--progress", type=float, default=100.0, help="Playback progress (0.0 to 100.0)")
    parser.add_argument("--user", default="testuser", help="Media server username")
    parser.add_argument("--url", default="http://localhost:8000", help="Omniscrobble base URL")
    parser.add_argument("--token", default="", help="Webhook secret token (?token=...)")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without dispatching HTTP request")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print full HTTP response details")

    args = parser.parse_args()

    # Determine endpoint path
    base_url = args.url.rstrip("/")
    if args.server == "plex":
        endpoint_path = "/webhook"
    elif args.server == "jellyfin":
        endpoint_path = "/webhook/jellyfin"
    elif args.server == "emby":
        endpoint_path = "/webhook/emby"
    elif args.server == "sonarr":
        endpoint_path = "/sonarr"
    elif args.server == "radarr":
        endpoint_path = "/radarr"
    else:
        endpoint_path = "/webhook"

    target_url = f"{base_url}{endpoint_path}"
    if args.token:
        sep = "&" if "?" in target_url else "?"
        target_url = f"{target_url}{sep}token={args.token}"

    headers: dict[str, str] = {}
    if args.token:
        headers["x-webhook-secret"] = args.token

    # Build payload
    form_data: dict[str, str] | None = None
    json_data: dict[str, Any] | None = None

    if args.server == "plex":
        plex_dict = build_plex_payload(
            scenario=args.scenario,
            title=args.title,
            year=args.year,
            show=args.show,
            season=args.season,
            episode=args.episode,
            rating=args.rating,
            progress=args.progress,
            user=args.user,
        )
        # Plex sends multipart form data with 'payload' key
        form_data = {"payload": json.dumps(plex_dict)}
        display_payload = plex_dict
    elif args.server == "jellyfin":
        json_data = build_jellyfin_payload(
            scenario=args.scenario,
            title=args.title,
            year=args.year,
            show=args.show,
            season=args.season,
            episode=args.episode,
            rating=args.rating,
            progress=args.progress,
            user=args.user,
        )
        display_payload = json_data
    elif args.server == "emby":
        json_data = build_emby_payload(
            scenario=args.scenario,
            title=args.title,
            year=args.year,
            show=args.show,
            season=args.season,
            episode=args.episode,
            rating=args.rating,
            progress=args.progress,
            user=args.user,
        )
        display_payload = json_data
    elif args.server == "sonarr":
        json_data = build_sonarr_payload(
            scenario=args.scenario,
            title=args.title,
            year=args.year,
            show=args.show,
            season=args.season,
            episode=args.episode,
        )
        display_payload = json_data
    elif args.server == "radarr":
        json_data = build_radarr_payload(
            scenario=args.scenario,
            title=args.title,
            year=args.year,
        )
        display_payload = json_data

    print(f"==> Omniscrobble Webhook Simulator")
    print(f"Target:   {target_url}")
    print(f"Server:   {args.server}")
    print(f"Scenario: {args.scenario}")
    print(f"Payload:\n{json.dumps(display_payload, indent=2)}")

    if args.dry_run:
        print("\n[Dry Run] Request not dispatched.")
        return 0

    print(f"\n==> Dispatching POST request to {target_url}...")
    try:
        status_code, body = send_http_request(
            url=target_url,
            headers=headers,
            json_data=json_data,
            form_data=form_data,
        )
        if 200 <= status_code < 300:
            print(f"✓ Success! Status: {status_code}")
        else:
            print(f"✗ Failed! Status: {status_code}")

        if args.verbose or status_code >= 400:
            print(f"Response:\n{body}")
        else:
            try:
                parsed_res = json.loads(body)
                print(f"Response Summary: {json.dumps(parsed_res, indent=2)}")
            except Exception:
                print(f"Response: {body[:300]}")
        return 0 if status_code < 400 else 1
    except Exception as e:
        print(f"✗ Connection error: {e}", file=sys.stderr)
        print("Hint: Make sure Omniscrobble is running (e.g. uvicorn app.main:app --port 8000).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
