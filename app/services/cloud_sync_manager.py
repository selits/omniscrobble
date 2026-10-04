"""Automated Background Cloud Reconciliation Manager for Omniscrobble.

Manages periodic background reconciliation tasks:
- Automated Letterboxd watch diary RFC-4180 CSV snapshots (data/exports/letterboxd_diary.csv)
- Two-way media server (Plex/Jellyfin/Emby) watched status reconciliation
- Cross-tracker reconciliation (Trakt <-> Simkl)
- Content Bridge (*Arr) watchlist acquisition
- Concurrency mutex lock (asyncio.Lock) preventing collisions with manual reconciliation
- Persistent telemetry state in data/sync_state.json
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any, Optional

try:
    from app.config import Config
    from app.services.atomic_writer import atomic_write_json, atomic_write_text
    from app.services.settings_manager import settings_mgr
except ImportError:
    from config import Config
    from atomic_writer import atomic_write_json, atomic_write_text
    from settings_manager import settings_mgr

logger = logging.getLogger("omniscrobble.cloud_sync")


class CloudSyncManager:
    """Orchestrates automated background cloud synchronization, exports, and reconciliation."""

    def __init__(self, config: type[Config] = Config):
        self.config = config
        self.base_dir = getattr(config, "BASE_DIR", Path("."))
        self.data_dir = self.base_dir / "data"
        self.exports_dir = self.data_dir / "exports"
        self.state_file = self.data_dir / "sync_state.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

        self.sync_mutex = asyncio.Lock()
        self._is_running = False
        self._last_state: dict[str, Any] = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        """Load persistent sync state from disk with safe defaults."""
        default_state: dict[str, Any] = {
            "last_run_timestamp": None,
            "last_run_status": "never_run",
            "items_reconciled": 0,
            "next_scheduled_run": None,
            "errors": [],
            "tasks": {
                "letterboxd_export": None,
                "server_reconciliation": None,
                "cross_tracker_sync": None,
                "arr_watchlist": None,
            },
        }
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        default_state.update(data)
            except Exception as e:
                logger.error(f"Failed to read sync state file {self.state_file}: {e}")
        return default_state

    def _save_state(self) -> None:
        """Persist sync telemetry to disk."""
        try:
            atomic_write_json(self.state_file, self._last_state)
        except Exception as e:
            logger.error(f"Failed to save sync state file {self.state_file}: {e}")

    def get_status(self) -> dict[str, Any]:
        """Return current background sync telemetry."""
        state = dict(self._last_state)
        state["is_running"] = self._is_running or self.sync_mutex.locked()
        return state

    async def run_sync_cycle(
        self,
        trigger: str = "scheduled",
        letterboxd_client: Optional[Any] = None,
        reverse_sync_mgr: Optional[Any] = None,
        cross_tracker_sync: Optional[Any] = None,
        arr_bridge: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Execute a full background synchronization and export cycle with concurrency protection."""
        if self.sync_mutex.locked():
            return {
                "status": "conflict",
                "message": "A synchronization operation is already in progress.",
            }

        async with self.sync_mutex:
            self._is_running = True
            start_time = datetime.now(timezone.utc)
            items_reconciled = 0
            errors: list[str] = []
            task_results: dict[str, Any] = {}

            logger.info(f"Starting automated cloud reconciliation cycle (trigger: {trigger})")

            # 1. Automated Letterboxd Diary Export Snapshot
            try:
                if letterboxd_client:
                    lb_client = letterboxd_client
                else:
                    from app.main import letterboxd_client as lb_client
                csv_data = lb_client.generate_csv()
                export_file = self.exports_dir / "letterboxd_diary.csv"
                atomic_write_text(export_file, csv_data)
                task_results["letterboxd_export"] = {
                    "status": "success",
                    "file": str(export_file.name),
                    "bytes": len(csv_data.encode("utf-8")),
                }
                logger.info(f"Letterboxd diary exported to {export_file} ({len(csv_data)} bytes)")
            except Exception as e:
                err_msg = f"Letterboxd export failed: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                task_results["letterboxd_export"] = {"status": "error", "error": str(e)}

            # 2. Automated Two-Way Media Server Reconciliation
            try:
                if reverse_sync_mgr:
                    r_mgr = reverse_sync_mgr
                else:
                    from app.main import reverse_sync_mgr as r_mgr
                
                recon_settings = settings_mgr.get_reconciliation_settings(mask_token=False)
                active_server = recon_settings.get("server_type", "plex")
                server_client = getattr(r_mgr, active_server, None)

                if server_client and getattr(server_client, "is_configured", lambda: False)():
                    diff = await r_mgr.scan_discrepancies(server=active_server)
                    discrepancies = diff.get("discrepancies", [])
                    if discrepancies:
                        recon_res = await r_mgr.execute_reconciliation(server=active_server)
                        count = recon_res.get("reconciled_count", len(discrepancies))
                        items_reconciled += count
                        task_results["server_reconciliation"] = {
                            "status": "success",
                            "server": active_server,
                            "items_reconciled": count,
                        }
                    else:
                        task_results["server_reconciliation"] = {
                            "status": "clean",
                            "server": active_server,
                            "items_reconciled": 0,
                        }
                else:
                    task_results["server_reconciliation"] = {
                        "status": "skipped",
                        "reason": f"Server '{active_server}' not configured",
                    }
            except Exception as e:
                err_msg = f"Server reconciliation failed: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                task_results["server_reconciliation"] = {"status": "error", "error": str(e)}

            # 3. Cross-Tracker Reconciliation (Trakt <-> Simkl)
            try:
                if cross_tracker_sync:
                    c_sync = cross_tracker_sync
                else:
                    from app.main import cross_tracker_sync as c_sync

                if settings_mgr.is_tracker_enabled("simkl") and c_sync.simkl.is_authenticated():
                    cross_diff = await c_sync.scan_discrepancies()
                    discrepancies = cross_diff.get("discrepancies", [])
                    if discrepancies:
                        cross_res = await c_sync.execute_sync(direction="all")
                        count = cross_res.get("synced_count", len(discrepancies))
                        items_reconciled += count
                        task_results["cross_tracker_sync"] = {
                            "status": "success",
                            "items_reconciled": count,
                        }
                    else:
                        task_results["cross_tracker_sync"] = {
                            "status": "clean",
                            "items_reconciled": 0,
                        }
                else:
                    task_results["cross_tracker_sync"] = {
                        "status": "skipped",
                        "reason": "Simkl not enabled or unauthenticated",
                    }
            except Exception as e:
                err_msg = f"Cross-tracker sync failed: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                task_results["cross_tracker_sync"] = {"status": "error", "error": str(e)}

            # 4. Content Bridge Watchlist Synchronization
            try:
                if arr_bridge:
                    a_bridge = arr_bridge
                else:
                    from app.main import arr_bridge as a_bridge

                if Config.AUTO_ADD_FROM_WATCHLIST and (a_bridge.sonarr.is_configured or a_bridge.radarr.is_configured):
                    arr_res = await a_bridge.sync_watchlist()
                    task_results["arr_watchlist"] = {"status": "success", "result": arr_res}
                else:
                    task_results["arr_watchlist"] = {"status": "skipped", "reason": "Watchlist acquisition disabled"}
            except Exception as e:
                err_msg = f"Arr watchlist sync failed: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                task_results["arr_watchlist"] = {"status": "error", "error": str(e)}

            # Finalize Telemetry State
            end_time = datetime.now(timezone.utc)
            duration_s = round((end_time - start_time).total_seconds(), 2)
            cycle_status = "error" if len(errors) == len(task_results) and errors else ("partial_error" if errors else "success")

            interval_hours = int(getattr(self.config, "BACKGROUND_CLOUD_SYNC_INTERVAL_HOURS", 24))
            next_run = (end_time + timedelta(hours=interval_hours)).isoformat() if interval_hours > 0 else None

            self._last_state = {
                "last_run_timestamp": end_time.isoformat(),
                "last_run_status": cycle_status,
                "duration_seconds": duration_s,
                "items_reconciled": items_reconciled,
                "next_scheduled_run": next_run,
                "trigger": trigger,
                "errors": errors,
                "tasks": task_results,
            }
            self._save_state()
            self._is_running = False

            logger.info(
                f"Completed cloud reconciliation cycle ({cycle_status}): "
                f"{items_reconciled} items reconciled in {duration_s}s. Next run: {next_run}"
            )
            return self._last_state


cloud_sync_mgr = CloudSyncManager()
