"""Emby Server REST API client for two-way library reconciliation and scrobbling."""

from typing import Optional
import httpx

from app.clients.mediabrowser_api_client import BaseMediaBrowserClient
from app.config import Config


class EmbyApiClient(BaseMediaBrowserClient):
    """Asynchronous client for interacting with the local Emby Server REST API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        user_id: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        target_url = base_url if base_url is not None else Config.EMBY_URL
        target_token = token if token is not None else Config.EMBY_TOKEN
        target_user_id = user_id if user_id is not None else Config.EMBY_USER_ID
        super().__init__(
            base_url=target_url,
            token=target_token,
            user_id=target_user_id,
            client=client,
            server_type="emby",
        )
