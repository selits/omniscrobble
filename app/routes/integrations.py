"""HTTP route registration for tracker and media acquisition integrations."""

from fastapi import APIRouter


_ROUTES = (
    ("/api/arr/lookup", "lookup_arr_media", ["GET"]),
    ("/api/arr/config", "get_arr_acquisition_config", ["GET"]),
    ("/api/arr/add", "add_arr_media", ["POST"]),
    ("/api/arr/test-connection", "test_arr_connection", ["POST"]),
    ("/api/arr/status", "get_arr_status", ["GET"]),
    ("/api/arr/sync", "trigger_arr_sync", ["POST"]),
    ("/api/ecosystem", "get_ecosystem_status", ["GET"]),
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


def create_router(handlers):
    """Register the existing typed integration handlers on a focused router."""
    router = APIRouter(tags=["Integrations"])
    for path, handler_name, methods in _ROUTES:
        router.add_api_route(path, handlers[handler_name], methods=methods, name=handler_name)
    return router
