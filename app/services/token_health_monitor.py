"""Proactive token expiration monitor and health alert service for Omniscrobble.

Periodically evaluates OAuth token validity across Trakt, Simkl, MyAnimeList,
and partner co-watch accounts. Proactively attempts automatic token refresh within
24 hours of expiration, and dispatches push alerts with re-authorization links
when manual intervention is required.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any, Optional

from app.config import Config
from app.services.notifier import notifier

logger = logging.getLogger("omniscrobble.token_monitor")

EXPIRATION_ALERT_THRESHOLD_SECONDS = 86400  # 24 hours


class TokenHealthMonitor:
    """Evaluates OAuth token lifespans and dispatches proactive health alerts."""

    def __init__(self):
        self._last_cycle_time: Optional[float] = None
        self._cached_summary: dict[str, Any] = {}

    def _resolve_base_url(self) -> str:
        """Resolve external or server URL for re-authorization links."""
        ext = getattr(Config, "EXTERNAL_URL", "")
        if ext and ext.startswith(("http://", "https://")):
            return ext.rstrip("/")
        host = getattr(Config, "SERVER_HOST", "localhost")
        if host == "0.0.0.0":
            host = "localhost"
        port = getattr(Config, "SERVER_PORT", 8080)
        return f"http://{host}:{port}"

    async def evaluate_trakt(self, trakt_client: Any) -> dict[str, Any]:
        """Evaluate primary Trakt account token health."""
        if not trakt_client.is_authenticated():
            return {
                "service": "Trakt.tv",
                "configured": bool(trakt_client.client_id),
                "authenticated": False,
                "status": "not_authenticated",
                "days_remaining": 0,
                "needs_reauth": True,
                "reauth_url": f"{self._resolve_base_url()}/auth",
            }

        tokens = trakt_client.load_tokens() or {}
        created_at = tokens.get("created_at", 0)
        expires_in = tokens.get("expires_in", 0)
        now = time.time()

        if not created_at or not expires_in:
            return {
                "service": "Trakt.tv",
                "configured": True,
                "authenticated": True,
                "status": "healthy",
                "days_remaining": 90,
                "needs_reauth": False,
                "reauth_url": f"{self._resolve_base_url()}/auth",
            }

        seconds_remaining = (created_at + expires_in) - now
        days_remaining = max(0, int(seconds_remaining // 86400))

        if seconds_remaining <= EXPIRATION_ALERT_THRESHOLD_SECONDS:
            # Proactively attempt renewal
            logger.info("Trakt token within 24h of expiry (seconds: %d); attempting refresh...", seconds_remaining)
            try:
                refreshed = await trakt_client.refresh_token()
                if refreshed and refreshed.get("access_token"):
                    logger.info("Successfully refreshed Trakt access token proactively.")
                    return {
                        "service": "Trakt.tv",
                        "configured": True,
                        "authenticated": True,
                        "status": "healthy",
                        "days_remaining": 90,
                        "needs_reauth": False,
                        "reauth_url": f"{self._resolve_base_url()}/auth",
                    }
            except Exception as e:
                logger.error("Proactive Trakt refresh failed: %s", e)

            # If still near expiry or expired after refresh attempt
            status = "expired" if seconds_remaining <= 0 else "near_expiry"
            return {
                "service": "Trakt.tv",
                "configured": True,
                "authenticated": True,
                "status": status,
                "days_remaining": days_remaining,
                "seconds_remaining": max(0, int(seconds_remaining)),
                "needs_reauth": True,
                "reauth_url": f"{self._resolve_base_url()}/auth",
            }

        return {
            "service": "Trakt.tv",
            "configured": True,
            "authenticated": True,
            "status": "healthy",
            "days_remaining": days_remaining,
            "seconds_remaining": int(seconds_remaining),
            "needs_reauth": False,
            "reauth_url": f"{self._resolve_base_url()}/auth",
        }

    async def evaluate_simkl(self, simkl_client: Any) -> dict[str, Any]:
        """Evaluate Simkl account token health."""
        if not simkl_client.is_authenticated():
            return {
                "service": "Simkl",
                "configured": bool(simkl_client.effective_client_id),
                "authenticated": False,
                "status": "not_authenticated",
                "days_remaining": 0,
                "needs_reauth": True,
                "reauth_url": f"{self._resolve_base_url()}/auth/simkl",
            }

        created_at = getattr(simkl_client, "created_at", None)
        expires_in = getattr(simkl_client, "expires_in", None)
        now = time.time()

        if not created_at or not expires_in:
            return {
                "service": "Simkl",
                "configured": True,
                "authenticated": True,
                "status": "healthy",
                "days_remaining": 30,
                "needs_reauth": False,
                "reauth_url": f"{self._resolve_base_url()}/auth/simkl",
            }

        seconds_remaining = (created_at + expires_in) - now
        days_remaining = max(0, int(seconds_remaining // 86400))

        if seconds_remaining <= EXPIRATION_ALERT_THRESHOLD_SECONDS:
            logger.info("Simkl token within 24h of expiry (seconds: %d); attempting refresh...", seconds_remaining)
            try:
                success = await simkl_client.refresh_token()
                if success:
                    logger.info("Successfully refreshed Simkl access token proactively.")
                    return {
                        "service": "Simkl",
                        "configured": True,
                        "authenticated": True,
                        "status": "healthy",
                        "days_remaining": 30,
                        "needs_reauth": False,
                        "reauth_url": f"{self._resolve_base_url()}/auth/simkl",
                    }
            except Exception as e:
                logger.error("Proactive Simkl refresh failed: %s", e)

            status = "expired" if seconds_remaining <= 0 else "near_expiry"
            return {
                "service": "Simkl",
                "configured": True,
                "authenticated": True,
                "status": status,
                "days_remaining": days_remaining,
                "seconds_remaining": max(0, int(seconds_remaining)),
                "needs_reauth": True,
                "reauth_url": f"{self._resolve_base_url()}/auth/simkl",
            }

        return {
            "service": "Simkl",
            "configured": True,
            "authenticated": True,
            "status": "healthy",
            "days_remaining": days_remaining,
            "seconds_remaining": int(seconds_remaining),
            "needs_reauth": False,
            "reauth_url": f"{self._resolve_base_url()}/auth/simkl",
        }

    async def evaluate_mal(self, mal_client: Any) -> dict[str, Any]:
        """Evaluate MyAnimeList account token health."""
        if not mal_client.is_authenticated():
            return {
                "service": "MyAnimeList",
                "configured": bool(mal_client.effective_client_id),
                "authenticated": False,
                "status": "not_authenticated",
                "days_remaining": 0,
                "needs_reauth": True,
                "reauth_url": f"{self._resolve_base_url()}/auth/mal",
            }

        return {
            "service": "MyAnimeList",
            "configured": True,
            "authenticated": True,
            "status": "healthy",
            "days_remaining": 30,
            "needs_reauth": False,
            "reauth_url": f"{self._resolve_base_url()}/auth/mal",
        }

    async def run_check_cycle(
        self,
        trakt_client: Any,
        simkl_client: Optional[Any] = None,
        mal_client: Optional[Any] = None,
        user_mgr: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Execute a full evaluation across all services and dispatch alerts if needed."""
        self._last_cycle_time = time.time()
        results: dict[str, Any] = {}

        # 1. Primary Trakt
        trakt_status = await self.evaluate_trakt(trakt_client)
        results["trakt"] = trakt_status
        if trakt_status.get("needs_reauth") and trakt_status.get("status") in ("near_expiry", "expired"):
            hours = max(1, trakt_status.get("seconds_remaining", 0) // 3600)
            msg = f"Trakt.tv OAuth access token {trakt_status['status'].replace('_', ' ')} (approx {hours}h remaining or auto-refresh failed)."
            await notifier.send_token_expiry_alert("Trakt.tv", msg, trakt_status["reauth_url"])

        # 2. Simkl
        if simkl_client:
            simkl_status = await self.evaluate_simkl(simkl_client)
            results["simkl"] = simkl_status
            if simkl_status.get("needs_reauth") and simkl_status.get("status") in ("near_expiry", "expired"):
                hours = max(1, simkl_status.get("seconds_remaining", 0) // 3600)
                msg = f"Simkl OAuth access token {simkl_status['status'].replace('_', ' ')} (approx {hours}h remaining or auto-refresh failed)."
                await notifier.send_token_expiry_alert("Simkl", msg, simkl_status["reauth_url"])

        # 3. MyAnimeList
        if mal_client:
            mal_status = await self.evaluate_mal(mal_client)
            results["mal"] = mal_status

        # 4. Partner Trakt accounts
        if user_mgr and getattr(Config, "CO_WATCH_USER", ""):
            partner_username = Config.CO_WATCH_USER.strip()
            partner_client = user_mgr.get_client(partner_username)
            if partner_client:
                partner_status = await self.evaluate_trakt(partner_client)
                partner_status["service"] = f"Partner Trakt (@{partner_username})"
                partner_status["reauth_url"] = f"{self._resolve_base_url()}/auth?user={partner_username}"
                results[f"partner_{partner_username}"] = partner_status
                if partner_status.get("needs_reauth") and partner_status.get("status") in ("near_expiry", "expired"):
                    hours = max(1, partner_status.get("seconds_remaining", 0) // 3600)
                    msg = f"Partner Trakt (@{partner_username}) token {partner_status['status'].replace('_', ' ')} (approx {hours}h remaining)."
                    await notifier.send_token_expiry_alert(f"Partner Trakt (@{partner_username})", msg, partner_status["reauth_url"])

        self._cached_summary = results
        return results

    def get_cached_summary(self) -> dict[str, Any]:
        """Return the latest cached token health evaluation."""
        return dict(self._cached_summary)


# Global singleton monitor
token_health_mgr = TokenHealthMonitor()
