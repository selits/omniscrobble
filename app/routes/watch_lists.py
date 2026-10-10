"""HTTP route registration for personal and shared watch lists."""

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter


_ROUTES = (
    ("/api/watch-lists", "get_watch_lists", ["GET"]),
    ("/api/watch-lists/anime-search", "search_watch_list_anime", ["GET"]),
    ("/api/watch-lists", "create_watch_list", ["POST"]),
    ("/api/watch-lists/{list_id}", "rename_watch_list", ["PATCH"]),
    ("/api/watch-lists/{list_id}", "delete_watch_list", ["DELETE"]),
    ("/api/watch-lists/{list_id}/items", "add_watch_list_item", ["POST"]),
    ("/api/watch-lists/{list_id}/items/order", "reorder_watch_list_items", ["PATCH"]),
    ("/api/watch-lists/{list_id}/items/{item_id}", "delete_watch_list_item", ["DELETE"]),
    ("/api/watch-lists/{list_id}/items/{item_id}", "update_watch_list_item", ["PATCH"]),
    ("/api/watch-lists/{list_id}/access", "set_watch_list_access", ["PUT"]),
    ("/api/watch-lists/{list_id}/automation", "set_watch_list_automation", ["PUT"]),
    ("/api/watch-lists/{list_id}/availability", "refresh_watch_list_availability", ["POST"]),
    ("/api/watch-lists/{list_id}/items/{item_id}/request", "request_watch_list_item", ["POST"]),
    ("/api/watch-lists/import/preview", "preview_watch_list_import", ["POST"]),
    ("/api/watch-lists/import", "import_watch_lists", ["POST"]),
    ("/api/watch-lists/export", "export_watch_lists", ["GET"]),
)


def create_router(handlers: Mapping[str, Callable[..., Any]]):
    """Register explicitly supplied handlers under this route family's router."""
    required = {handler_name for _, handler_name, _ in _ROUTES}
    missing = sorted(required - handlers.keys())
    if missing:
        raise ValueError(f"Missing watch-list route handlers: {', '.join(missing)}")

    router = APIRouter(tags=["Watch Lists"])
    for path, handler_name, methods in _ROUTES:
        router.add_api_route(path, handlers[handler_name], methods=methods, name=handler_name)
    return router
