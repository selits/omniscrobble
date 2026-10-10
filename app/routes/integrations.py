"""HTTP handlers and route registration for tracker integrations."""

import datetime
from collections.abc import Callable, Mapping
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


class DevicePollRequest(BaseModel):
    device_code: str
    user: Optional[str] = None


class SimklPollRequest(BaseModel):
    user_code: str
    device_code: Optional[str] = None
    user: Optional[str] = None


class TokenSubmitRequest(BaseModel):
    token: str
    user: Optional[str] = None


class TrackerAccountHandlers:
    """Personal tracker authorization and account status with explicit dependencies."""

    def __init__(
        self, *, config: Any, demo_mgr: Any, user_mgr: Any, simkl: Any,
        anilist: Any, mal: Any, profile_for_request: Callable[..., Any],
        require_dashboard_permission: Callable[..., Any],
        get_dashboard_principal: Callable[[Request], Any],
        is_admin_request: Callable[[Request], bool], get_cached_trakt_profile: Callable[..., Any],
        invalidate_trakt_profile: Callable[[], None], logger: Any,
        auth_locked_html: str, auth_simkl_html: str, auth_anilist_html: str, auth_mal_html: str,
    ) -> None:
        self.config = config
        self.demo_mgr = demo_mgr
        self.user_mgr = user_mgr
        self.simkl = simkl
        self.anilist = anilist
        self.mal = mal
        self.profile_for_request = profile_for_request
        self.require_dashboard_permission = require_dashboard_permission
        self.get_dashboard_principal = get_dashboard_principal
        self.is_admin_request = is_admin_request
        self.get_cached_trakt_profile = get_cached_trakt_profile
        self.invalidate_trakt_profile = invalidate_trakt_profile
        self.logger = logger
        self.auth_locked_html = auth_locked_html
        self.auth_simkl_html = auth_simkl_html
        self.auth_anilist_html = auth_anilist_html
        self.auth_mal_html = auth_mal_html

    def _user(self, request: Request, explicit: Optional[str] = None) -> Optional[str]:
        return self.profile_for_request(request, request.query_params.get("user") or explicit)

    def _authorize(self, request: Request, user: Optional[str]) -> None:
        self.require_dashboard_permission(request, "manage_personal_trackers", user)

    def _client(self, user: Optional[str], tracker: str):
        if not user:
            return {"simkl": self.simkl, "anilist": self.anilist, "mal": self.mal}[tracker]
        return self.user_mgr.get_tracker_client(user, tracker)

    async def auth_start(self, request: Request, user: Optional[str] = None):
        query_user = self._user(request, user)
        self._authorize(request, query_user)
        try:
            return await self.user_mgr.get_client(query_user).generate_device_code()
        except Exception as exc:
            self.logger.error("Failed to generate device code: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    async def auth_poll(self, payload: DevicePollRequest, request: Request):
        target_user = self.profile_for_request(request, payload.user)
        self._authorize(request, target_user)
        if not payload.device_code or not str(payload.device_code).strip() or payload.device_code == "undefined":
            return {"status": "error", "message": "Missing device_code"}
        try:
            result = await self.user_mgr.get_client(target_user).poll_for_token(payload.device_code)
            if "access_token" in result:
                if not target_user or target_user == "default":
                    self.invalidate_trakt_profile()
                return {"status": "success", "user": target_user or "default"}
            if result.get("status") in ("pending", "slow_down"):
                return result
            return {"status": "pending"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def get_trakt_status(self, request: Request, user: Optional[str] = None):
        if request.query_params.get("demo") == "true":
            return {"enabled": True, "configured": True, "authenticated": True, "user": "demo_viewer"}
        query_user = self._user(request, user)
        if not self.get_dashboard_principal(request) and not query_user:
            client = self.user_mgr.get_client(None)
            return {"enabled": client.is_enabled(), "configured": bool(client.effective_client_id), "authenticated": client.is_authenticated()}
        self._authorize(request, query_user)
        client = self.user_mgr.get_client(query_user)
        authenticated = client.is_authenticated()
        username = None
        if authenticated:
            if not query_user or query_user == "default":
                profile = await self.get_cached_trakt_profile()
                username = profile.get("username") if profile else None
            else:
                username = query_user
        return {"enabled": client.is_enabled(), "configured": bool(client.effective_client_id),
                "authenticated": authenticated, "user": username, "token_info": client.get_token_info()}

    async def disconnect_trakt(self, request: Request, user: Optional[str] = None):
        query_user = self._user(request, user)
        self._authorize(request, query_user)
        self.user_mgr.get_client(query_user).delete_tokens()
        if not query_user or query_user == "default":
            self.invalidate_trakt_profile()
        return {"status": "ok", "message": "Trakt disconnected"}

    async def get_simkl_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return {"enabled": True, "configured": True, "authenticated": True,
                    "user": "demo_viewer", "account_id": 123456, "timezone": "America/New_York"}
        user = self._user(request)
        if not self.get_dashboard_principal(request) and not user:
            return {"enabled": self.simkl.is_enabled(), "configured": bool(self.simkl.effective_client_id),
                    "authenticated": self.simkl.is_authenticated()}
        self._authorize(request, user)
        client = self._client(user, "simkl")
        status = await client.check_connection()
        status["enabled"] = client.is_enabled()
        status["configured"] = bool(client.effective_client_id)
        return status

    async def get_simkl_pin(self, request: Request):
        user = self._user(request)
        self._authorize(request, user)
        try:
            data = await self._client(user, "simkl").get_device_pin()
            if isinstance(data, dict) and "error" in data:
                message = data.get("error", "Failed to obtain Simkl PIN")
                if data.get("detail"):
                    message = f"{message}: {data['detail']}"
                raise HTTPException(status_code=400, detail=message)
            return data
        except HTTPException:
            raise
        except Exception as exc:
            self.logger.error("Failed to generate Simkl device PIN: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    async def poll_simkl_pin(self, payload: SimklPollRequest, request: Request):
        user = self.profile_for_request(request, payload.user or request.query_params.get("user"))
        self._authorize(request, user)
        if not payload.user_code or not str(payload.user_code).strip() or payload.user_code == "undefined":
            return {"status": "error", "result": "error", "message": "Missing user_code"}
        try:
            return await self._client(user, "simkl").poll_device_pin(payload.user_code, device_code=payload.device_code)
        except Exception as exc:
            return {"result": "error", "message": str(exc)}

    async def disconnect_simkl(self, request: Request):
        user = self._user(request)
        self._authorize(request, user)
        if user:
            self.user_mgr.disconnect_user_tracker(user, "simkl")
            return {"status": "ok", "message": f"Simkl disconnected for {user}"}
        self.simkl.delete_tokens()
        return {"status": "ok", "message": "Simkl disconnected"}

    async def auth_simkl_page(self, request: Request):
        if not self.is_admin_request(request):
            return HTMLResponse(content=self.auth_locked_html, status_code=401)
        return HTMLResponse(content=self.auth_simkl_html)

    async def get_anilist_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_anilist_status()
        user = self._user(request)
        if not self.get_dashboard_principal(request) and not user:
            return {"enabled": self.anilist.is_enabled(),
                    "configured": bool(getattr(self.config, "ANILIST_CLIENT_ID", "") or self.anilist.is_authenticated()),
                    "authenticated": self.anilist.is_authenticated()}
        self._authorize(request, user)
        client = self._client(user, "anilist")
        status = await client.check_connection()
        status["enabled"] = client.is_enabled()
        status["configured"] = bool(getattr(self.config, "ANILIST_CLIENT_ID", "") or client.is_authenticated())
        return status

    async def save_anilist_token(self, payload: TokenSubmitRequest, request: Request):
        return await self._save_token(payload, request, "anilist", "AniList")

    async def _save_token(self, payload: TokenSubmitRequest, request: Request, tracker: str, display_name: str):
        user = self.profile_for_request(request, payload.user or request.query_params.get("user"))
        self._authorize(request, user)
        token = payload.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="Token cannot be empty")
        client = self._client(user, tracker)
        client.access_token = token
        connection = await client.check_connection()
        if connection.get("status") == "connected":
            client.save_tokens({"access_token": token, "user_name": connection.get("user"),
                                "user_avatar": connection.get("avatar"), "user_id": connection.get("id")})
            return {"status": "success", "user": connection.get("user"), "id": connection.get("id")}
        client.load_tokens()
        fallback = f"Failed to connect with provided {display_name} token"
        raise HTTPException(status_code=400, detail=connection.get("error", fallback))

    async def disconnect_anilist(self, request: Request):
        user = self._user(request)
        self._authorize(request, user)
        if user:
            self.user_mgr.disconnect_user_tracker(user, "anilist")
            return {"status": "ok", "message": f"AniList disconnected for {user}"}
        self.anilist.delete_tokens()
        return {"status": "ok", "message": "AniList disconnected"}

    async def auth_anilist_page(self, request: Request):
        if not self.is_admin_request(request):
            return HTMLResponse(content=self.auth_locked_html, status_code=401)
        html = self.auth_anilist_html.replace("{{PAGE_TITLE}}", "Connect AniList &bull; Omniscrobble")
        html = html.replace("{{H1_TEXT}}", "AniList Anime Tracker")
        html = html.replace("{{P_DESC}}", "Link your AniList account to enable real-time anime scrobbling and ratings sync via the official GraphQL API.")
        banner = ""
        if self.anilist.is_authenticated():
            username = self.anilist.user_name or "Linked User"
            banner = f'<div class="banner">✓ Currently linked to AniList as <strong>@{username}</strong>. Entering a new token will update credentials.</div>'
        return HTMLResponse(content=html.replace("{{ALREADY_CONNECTED_BANNER}}", banner))


    async def get_mal_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_mal_status()
        user = self._user(request)
        if not self.get_dashboard_principal(request) and not user:
            return {"enabled": self.mal.is_enabled(),
                    "configured": bool(self.mal.effective_client_id or self.mal.is_authenticated()),
                    "authenticated": self.mal.is_authenticated()}
        self._authorize(request, user)
        client = self._client(user, "mal")
        status = await client.check_connection()
        status["enabled"] = client.is_enabled()
        status["configured"] = bool(client.effective_client_id or client.is_authenticated())
        return status

    async def save_mal_token(self, payload: TokenSubmitRequest, request: Request):
        return await self._save_token(payload, request, "mal", "MyAnimeList")

    async def disconnect_mal(self, request: Request):
        user = self._user(request)
        self._authorize(request, user)
        if user:
            self.user_mgr.disconnect_user_tracker(user, "mal")
            return {"status": "ok", "message": f"MyAnimeList disconnected for {user}"}
        self.mal.delete_tokens()
        return {"status": "ok", "message": "MyAnimeList disconnected"}

    async def auth_mal_page(self, request: Request):
        if not self.is_admin_request(request):
            return HTMLResponse(content=self.auth_locked_html, status_code=401)
        html = self.auth_mal_html.replace("{{PAGE_TITLE}}", "Connect MyAnimeList &bull; Omniscrobble")
        html = html.replace("{{H1_TEXT}}", "MyAnimeList (MAL) Integration")
        html = html.replace("{{P_DESC}}", "Link your MyAnimeList account to scrobble anime episode progress and sync ratings via the MAL v2 API.")
        banner = ""
        if self.mal.is_authenticated():
            username = self.mal.user_name or "Linked User"
            banner = f'<div class="banner">✓ Currently linked to MyAnimeList as <strong>@{username}</strong>. Entering a new token will update credentials.</div>'
        return HTMLResponse(content=html.replace("{{ALREADY_CONNECTED_BANNER}}", banner))



class TrackerDiagnosticsHandlers:
    """Status, metadata resolution, and export handlers for cloud trackers."""

    def __init__(self, *, demo_mgr: Any, multi_tracker: Any, settings_mgr: Any,
                 is_admin_request: Callable[[Request], bool], mask_username: Callable[[str], str],
                 anime_resolver: Any, parsed_media_type: Any, tmdb: Any, kitsu: Any,
                 letterboxd: Any, serializd: Any, mdblist: Any) -> None:
        self.demo_mgr = demo_mgr
        self.multi_tracker = multi_tracker
        self.settings_mgr = settings_mgr
        self.is_admin_request = is_admin_request
        self.mask_username = mask_username
        self.anime_resolver = anime_resolver
        self.parsed_media_type = parsed_media_type
        self.tmdb = tmdb
        self.kitsu = kitsu
        self.letterboxd = letterboxd
        self.serializd = serializd
        self.mdblist = mdblist

    async def resolve_anime_api(self, request: Request, title: str, year: Optional[int] = None):
        media = self.parsed_media_type(
            raw_payload={}, event="media.play", media_type="episode", title=title,
            show_title=title, year=year, show_year=year, username="admin", progress=0.0,
        )
        resolved = await self.anime_resolver.resolve(media)
        return {"title": title, "year": year, "is_anime": bool(resolved and resolved.get("is_anime")), "resolved": resolved}

    async def get_multi_trackers_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_trackers_status()
        status = await self.multi_tracker.get_status()
        if not self.is_admin_request(request):
            for info in status.get("trackers", {}).values():
                if isinstance(info, dict):
                    if info.get("user"):
                        info["user"] = self.mask_username(info["user"])
                    if info.get("username"):
                        info["username"] = self.mask_username(info["username"])
                    if info.get("account_id"):
                        info["account_id"] = "******"
        return status

    async def get_tmdb_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_tmdb_status()
        status = await self.tmdb.check_connection()
        status["enabled"] = self.settings_mgr.is_tracker_enabled("tmdb")
        return status

    async def get_kitsu_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_kitsu_status()
        status = await self.kitsu.check_connection()
        status["enabled"] = self.settings_mgr.is_tracker_enabled("kitsu")
        if not self.is_admin_request(request):
            if status.get("user"):
                status["user"] = self.mask_username(status["user"])
            if status.get("username"):
                status["username"] = self.mask_username(status["username"])
            if status.get("message") and self.kitsu.user_name:
                status["message"] = f"Connected as @{self.mask_username(self.kitsu.user_name)}"
        return status

    async def get_letterboxd_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_letterboxd_status()
        status = await self.letterboxd.check_connection()
        status["enabled"] = self.settings_mgr.is_tracker_enabled("letterboxd")
        if not self.is_admin_request(request):
            if status.get("user"):
                status["user"] = self.mask_username(status["user"])
            if status.get("username"):
                status["username"] = self.mask_username(status["username"])
            if status.get("message") and self.letterboxd.username:
                count = status.get("diary_count", 0)
                status["message"] = f"Diary ready ({count} films logged for @{self.mask_username(self.letterboxd.username)})"
        return status

    async def get_serializd_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_serializd_status()
        status = await self.serializd.check_connection()
        status["enabled"] = self.settings_mgr.is_tracker_enabled("serializd")
        if not self.is_admin_request(request):
            if status.get("user"):
                status["user"] = self.mask_username(status["user"])
            if status.get("username"):
                status["username"] = self.mask_username(status["username"])
            if status.get("message") and self.serializd.username:
                status["message"] = f"Connected as @{self.mask_username(self.serializd.username)}"
        return status

    async def get_mdblist_status(self, request: Request):
        if request.query_params.get("demo") == "true":
            return self.demo_mgr.get_demo_mdblist_status()
        status = await self.mdblist.check_connection()
        status["enabled"] = self.settings_mgr.is_tracker_enabled("mdblist")
        if not self.is_admin_request(request):
            if status.get("user"):
                status["user"] = self.mask_username(status["user"])
            if status.get("username"):
                status["username"] = self.mask_username(status["username"])
        return status

    async def get_relay_status(self, request: Request):
        base_url = str(request.base_url).rstrip("/")
        return {
            "status": "active", "supported_apps": ["SeriesGuide", "Showly"],
            "description": "Mobile tracking applications connect via direct Trakt cloud synchronization or native webhook relay.",
            "endpoints": {"trakt_sync": "Automated two-way scrobble via Trakt Cloud OAuth", "generic_webhook": f"{base_url}/webhook"},
            "apps": [
                {"name": "SeriesGuide", "platform": "Android", "mode": "Trakt Cloud Sync", "status": "supported", "docs_url": "https://seriesgui.de/"},
                {"name": "Showly", "platform": "Android", "mode": "Trakt Cloud Sync", "status": "supported", "docs_url": "https://github.com/michaldrabik/Showly-2.0"},
            ],
        }

    async def export_letterboxd_csv(self, request: Request):
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required to export Letterboxd diary")
        content = self.letterboxd.generate_csv_export()
        filename = f"letterboxd_diary_{datetime.datetime.now().strftime('%Y%m%d')}.csv"
        return Response(content=content, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

    async def get_letterboxd_diary(self, request: Request):
        if request.query_params.get("demo") == "true":
            return [
                {"title": "Dune: Part Two", "year": 2024, "rating": 9, "watched_date": "2026-10-01", "imdb_id": "tt15239678"},
                {"title": "Oppenheimer", "year": 2023, "rating": 10, "watched_date": "2026-09-28", "imdb_id": "tt15398776"},
            ]
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required to view Letterboxd diary")
        return self.letterboxd.get_diary_entries()

    async def get_mdblist_ratings(self, request: Request, imdb_id: Optional[str] = None,
                                  tmdb_id: Optional[int] = None, media_type: str = "movie"):
        if request.query_params.get("demo") == "true":
            return {"title": "Dune: Part Two", "year": 2024, "score": 88,
                    "ratings": [{"source": "imdb", "value": 8.6, "score": 86},
                                {"source": "metacritic", "value": 79, "score": 79},
                                {"source": "tomatoes", "value": 92, "score": 92},
                                {"source": "letterboxd", "value": 4.5, "score": 90}]}
        if not self.mdblist.is_configured():
            raise HTTPException(status_code=400, detail="MDBList API key is not configured.")
        if not imdb_id and not tmdb_id:
            raise HTTPException(status_code=400, detail="Either imdb_id or tmdb_id must be provided.")
        result = await self.mdblist.get_item_ratings(imdb_id=imdb_id, tmdb_id=tmdb_id, media_type=media_type)
        if not result or (isinstance(result, dict) and result.get("status") == "error"):
            message = result.get("error", "Failed to retrieve ratings from MDBList.") if isinstance(result, dict) else "Failed to retrieve ratings from MDBList."
            raise HTTPException(status_code=502, detail=message)
        return result



_ROUTES = (
    ("/api/auth/start", "auth_start", ["POST"]),
    ("/api/auth/poll", "auth_poll", ["POST"]),
    ("/api/trakt/status", "get_trakt_status", ["GET"]),
    ("/api/trakt/disconnect", "disconnect_trakt", ["POST"]),
    ("/api/simkl/status", "get_simkl_status", ["GET"]),
    ("/api/simkl/pin", "get_simkl_pin", ["POST"]),
    ("/api/simkl/poll", "poll_simkl_pin", ["POST"]),
    ("/api/simkl/disconnect", "disconnect_simkl", ["POST"]),
    ("/auth/simkl", "auth_simkl_page", ["GET"]),
    ("/api/anilist/status", "get_anilist_status", ["GET"]),
    ("/api/anilist/token", "save_anilist_token", ["POST"]),
    ("/api/anilist/disconnect", "disconnect_anilist", ["POST"]),
    ("/auth/anilist", "auth_anilist_page", ["GET"]),
    ("/api/mal/status", "get_mal_status", ["GET"]),
    ("/api/mal/token", "save_mal_token", ["POST"]),
    ("/api/mal/disconnect", "disconnect_mal", ["POST"]),
    ("/auth/mal", "auth_mal_page", ["GET"]),
    ("/api/anime/resolve", "resolve_anime_api", ["GET"]),
    ("/api/trackers/status", "get_multi_trackers_status", ["GET"]),
    ("/api/tmdb/status", "get_tmdb_status", ["GET"]),
    ("/api/kitsu/status", "get_kitsu_status", ["GET"]),
    ("/api/letterboxd/status", "get_letterboxd_status", ["GET"]),
    ("/api/serializd/status", "get_serializd_status", ["GET"]),
    ("/api/mdblist/status", "get_mdblist_status", ["GET"]),
    ("/api/relay/status", "get_relay_status", ["GET"]),
    ("/api/letterboxd/export", "export_letterboxd_csv", ["GET"]),
    ("/api/letterboxd/diary", "get_letterboxd_diary", ["GET"]),
    ("/api/mdblist/ratings", "get_mdblist_ratings", ["GET"]),
)


def create_router(handlers: Mapping[str, Any]):
    """Register integration handlers supplied explicitly by the application."""
    router = APIRouter(tags=["Integrations"])
    required = {handler_name for _, handler_name, _ in _ROUTES}
    missing = sorted(required - handlers.keys())
    if missing:
        raise ValueError(f"Missing integration route handlers: {', '.join(missing)}")
    for path, handler_name, methods in _ROUTES:
        router.add_api_route(path, handlers[handler_name], methods=methods, name=handler_name)
    return router
