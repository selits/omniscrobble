"""Server-Side Dashboard HTML Rendering Engine for Omniscrobble.

Decouples HTML markup generation, activity feed formatting, status badge styling,
and responsive dashboard card templates from the FastAPI application controller.
"""

from __future__ import annotations

import datetime
import html
import json
from typing import Any, Callable, Optional
import urllib.parse

try:
    from app.config import Config
    from app.services.settings_manager import settings_mgr
except ImportError:
    from config import Config
    from settings_manager import settings_mgr


def format_action_label(raw_action: str) -> str:
    """Map internal webhook scrobble action names to human-readable UI labels."""
    clean = str(raw_action or "").strip()
    action_map = {
        "scrobble_start": "play",
        "scrobble_pause": "pause",
        "scrobble_stop": "scrobble",
        "mark_watched": "scrobble",
        "playback_stopped": "stop",
        "test_webhook": "test",
        "none": "ignored",
    }
    return action_map.get(clean, clean)


def should_display_cowatch_badge(action: str, result_status: str, progress: str) -> bool:
    """Determine whether an activity row qualifies for a Co-Watched status badge."""
    act = str(action or "").lower().strip()
    status = str(result_status or "").lower().strip()
    prog = str(progress or "").strip()
    if status in ("ignored", "error") or prog == "0.0%":
        return False
    return act.startswith(("mark_watched", "scrobble_stop", "test_webhook")) or act in ("scrobble", "watched")


def render_status_badge(action: str, result_status: str, progress: str = "", cowatch_status: dict | None = None) -> str:
    """Render an HTML status badge for scrobble actions and partner co-watch syncs."""
    raw_act = str(action or "").lower().strip()
    clean_act = format_action_label(raw_act).lower()
    stat = str(result_status or "").lower().strip()
    cw = cowatch_status or {}

    if cw.get("synced") and should_display_cowatch_badge(action, result_status, progress):
        target_txt = html.escape(f"@{cw['target']}" if cw.get("target") else "partner")
        reason_txt = html.escape(cw.get("reason") or "Shared show whitelist match")
        return f'<span style="background:#701a75;color:#f5d0fe;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;" title="Synced to {target_txt}: {reason_txt}">👥 Co-Watched</span>'

    if stat in ("ok", "200", "201"):
        label = "✓ OK"
        tooltip = "Action successful"
        if clean_act == "scrobble" or raw_act.startswith(("mark_watched", "scrobble_stop")):
            label = "✓ Scrobbled"
            if cw.get("reason"):
                tooltip = f"Scrobbled (Solo: {html.escape(cw.get('reason'))})"
            else:
                tooltip = "Scrobbled to connected trackers"
        elif clean_act == "collection" or raw_act == "collection":
            label = "✓ Added"
            tooltip = "Added to collection"
        elif clean_act == "rate" or raw_act.startswith("rate"):
            label = "✓ Rated"
            tooltip = "Rating synchronized"
        return f'<span style="background:#064e3b;color:#a7f3d0;border:1px solid #059669;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;" title="{tooltip}">{label}</span>'

    if stat == "ignored":
        return '<span style="background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;white-space:nowrap;" title="Playback or event skipped">Ignored</span>'

    if stat == "queued":
        return '<span style="background:#78350f;color:#fde68a;border:1px solid #d97706;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;" title="Saved to offline retry queue">⏳ Queued</span>'

    if stat in ("error", "500", "502", "503", "504"):
        return '<span style="background:#7f1d1d;color:#fecaca;border:1px solid #ef4444;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;" title="Action failed">✕ Failed</span>'

    return f'<span style="background:#1e293b;border:1px solid #334155;color:#cbd5e1;padding:2px 8px;border-radius:4px;font-size:11px;white-space:nowrap;">{html.escape(str(result_status))}</span>'


class DashboardRenderer:
    """Renders server-side HTML components and cards for the Omniscrobble dashboard."""

    @staticmethod
    def render_activity_table_rows(
        events_list: list[dict[str, Any]],
        is_admin: bool,
        is_demo: bool,
        cowatch_user: str,
        is_cowatch_show_fn: Callable[[Optional[str]], bool],
        mask_username_fn: Callable[[Optional[str]], str],
    ) -> tuple[str, str, str, str]:
        """Render recent activity table rows and pagination metadata."""
        total_events = len(events_list)
        initial_page_size = 10
        total_pages = max(1, (total_events + initial_page_size - 1) // initial_page_size) if total_events > 0 else 1
        events_page_info = f"Showing 1–{min(initial_page_size, total_events)} of {total_events} events" if total_events > 0 else "0 events"
        events_page_num = f"Page 1 of {total_pages}"
        events_next_disabled = "" if total_pages > 1 else "disabled"

        ssr_events = events_list[:initial_page_size]
        rows = ""
        col_span = 7 if is_admin else 6
        if not ssr_events:
            rows = f'<tr><td colspan="{col_span}" style="text-align:center;padding:24px;color:#94a3b8;">No scrobble events received yet. Start playing media on Plex, Jellyfin, or Emby to test!</td></tr>'
        else:
            for ev in ssr_events:
                u = ev["user"] if is_admin else mask_username_fn(ev["user"])
                server_raw = ev.get("server", "plex").lower()
                if server_raw == "jellyfin":
                    server_badge = '<span style="background:#3b0764;color:#d8b4fe;border:1px solid #7e22ce;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Jellyfin</span>'
                elif server_raw == "emby":
                    server_badge = '<span style="background:#064e3b;color:#a7f3d0;border:1px solid #059669;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Emby</span>'
                else:
                    server_badge = '<span style="background:#1e293b;color:#94a3b8;border:1px solid #334155;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Plex</span>'

                action_col = ""
                if is_admin:
                    show_title = ev.get("show_title") or (ev.get("title") if ev.get("type") == "show" else None)
                    if not show_title and ev.get("media_payload") and ev.get("media_payload", {}).get("media_type") == "show":
                        show_title = ev["media_payload"].get("title")
                    action_buttons = []
                    if show_title:
                        show_esc = urllib.parse.quote(show_title)
                        if not is_cowatch_show_fn(show_title):
                            action_buttons.append(f'<button data-show="{show_esc}" onclick="quickAddShow(decodeURIComponent(this.dataset.show), this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#1e293b;border:1px solid #334155;white-space:nowrap;" title="Add show to co-watch whitelist">+ Co-Watch</button>')
                    if (cowatch_user or is_demo) and ev.get("media_payload"):
                        media_enc = urllib.parse.quote(json.dumps(ev["media_payload"]))
                        raw_act = str(ev.get("action", "")).lower().strip()
                        res_stat = str(ev.get("result_status", "")).lower().strip()
                        prog_val = str(ev.get("progress", "")).strip()
                        is_completion = raw_act.startswith(("mark_watched", "scrobble_stop", "collection", "rate")) or raw_act in ("scrobble", "watched")
                        if is_completion and res_stat != "ignored" and prog_val != "0.0%":
                            cw = ev.get("cowatch_status") or {}
                            if not cw.get("synced"):
                                action_buttons.append(f'<button onclick="quickSyncPartner(\'{media_enc}\', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#701a75;color:#f5d0fe;white-space:nowrap;" title="Manually push this watch event to partner account">+ Sync Partner</button>')
                            if raw_act.startswith(("mark_watched", "scrobble_stop")) or raw_act in ("scrobble", "watched"):
                                action_buttons.append(f'<button onclick="quickUnscrobble(\'{media_enc}\', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#7f1d1d;color:#fee2e2;border:1px solid #ef4444;white-space:nowrap;" title="Unscrobble / Remove from connected trackers">🗑️ Unscrobble</button>')
                    action_col = f'<td style="padding:10px 12px;white-space:nowrap;"><div style="display:inline-flex;flex-wrap:nowrap;gap:6px;align-items:center;">{"".join(action_buttons)}</div></td>'

                status_badge_html = render_status_badge(
                    ev.get("action"),
                    ev.get("result_status"),
                    ev.get("progress", ""),
                    ev.get("cowatch_status"),
                )

                title_disp = html.escape(str(ev.get('title', '')))
                type_disp = html.escape(str(ev.get('type', '')))
                user_disp = html.escape(str(u))
                action_raw = str(ev.get('action', ''))
                progress_raw = str(ev.get('progress', '')).strip()
                clean_action = format_action_label(action_raw)
                action_disp = html.escape(clean_action)
                progress_disp = html.escape(progress_raw)
                if progress_disp and progress_raw not in clean_action and "(" not in clean_action and clean_action.lower() not in ("collection", "ignored"):
                    action_text = f"{action_disp} ({progress_disp})"
                else:
                    action_text = action_disp
                time_disp = html.escape(str(ev.get('timestamp', '')))

                rows += f"""
                <tr style="border-bottom: 1px solid #334155;">
                    <td style="padding:10px 12px;color:#cbd5e1;font-size:13px;">{time_disp}</td>
                    <td style="padding:10px 12px;color:#f8fafc;font-weight:500;">{title_disp}</td>
                    <td style="padding:10px 12px;"><span style="background:#0f172a;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">{type_disp}</span></td>
                    <td style="padding:10px 12px;color:#cbd5e1;font-size:13px;"><div style="display:inline-flex;align-items:center;">{server_badge}<span>{user_disp}</span></div></td>
                    <td style="padding:10px 12px;"><span style="background:#0f172a;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;white-space:nowrap;">{action_text}</span></td>
                    <td style="padding:10px 12px;white-space:nowrap;"><div style="display:inline-flex;align-items:center;gap:6px;white-space:nowrap;">{status_badge_html}</div></td>
                    {action_col}
                </tr>
                """

        return rows, events_page_info, events_page_num, events_next_disabled

    @staticmethod
    def render_webhook_section(
        full_webhook_url: str,
        full_jellyfin_url: str,
        full_emby_url: str,
        masked_webhook_url: str,
        is_admin: bool,
    ) -> str:
        """Render media server webhook URL card with tab switching or masked lock badge."""
        if is_admin:
            return f"""
            <div style="margin-top: 18px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:8px;">
                    <div class="info-label">Media Server Webhook Endpoints</div>
                    <div style="display:flex;gap:6px;">
                        <button type="button" onclick="switchWebhookTab('plex')" id="btn-tab-plex" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">Plex</button>
                        <button type="button" onclick="switchWebhookTab('jellyfin')" id="btn-tab-jellyfin" class="btn-sm" style="background:#1e293b;color:#94a3b8;">Jellyfin</button>
                        <button type="button" onclick="switchWebhookTab('emby')" id="btn-tab-emby" class="btn-sm" style="background:#1e293b;color:#94a3b8;">Emby</button>
                    </div>
                </div>
                <div class="webhook-row">
                    <input type="text" readonly id="webhook-url-input" value="{full_webhook_url}"
                           data-plex="{full_webhook_url}" data-jellyfin="{full_jellyfin_url}" data-emby="{full_emby_url}"
                           style="flex:1;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;color:#38bdf8;font-family:monospace;font-size:13px;outline:none;" />
                    <button onclick="copyWebhookUrl()" id="copy-btn" class="btn-copy">
                        📋 Copy URL
                    </button>
                </div>
                <div id="webhook-instructions" style="font-size: 12px; color: #94a3b8; margin-top: 6px;">
                    Add in Plex: <strong>Settings &rarr; Webhooks &rarr; Add Webhook</strong> &bull; Jellyfin (<code>/webhook/jellyfin</code>) &bull; Emby (<code>/webhook/emby</code>) &bull; Sonarr (<code>/sonarr</code>) &bull; Radarr (<code>/radarr</code>).
                </div>
            </div>
            """
        else:
            return f"""
            <div style="margin-top: 18px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                    <div class="info-label">Media Server Webhook Endpoints</div>
                    <span style="color:#f59e0b;font-size:11px;font-weight:600;">🔒 Secret Masked</span>
                </div>
                <div class="webhook-row">
                    <input type="text" readonly value="{masked_webhook_url}"
                           style="flex:1;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;color:#64748b;font-family:monospace;font-size:13px;outline:none;user-select:none;" />
                    <button onclick="openUnlockModal()" class="btn-copy" style="background:#2563eb;">
                        🔓 Unlock
                    </button>
                </div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 6px;">Admin authorization required to reveal webhook URLs. Supports Plex, Jellyfin, Emby, Sonarr, and Radarr.</div>
            </div>
            """

    @staticmethod
    def render_active_playback_card(
        active_sessions: list[dict[str, Any]],
        recently_finished: Optional[dict[str, Any]],
        is_admin: bool,
    ) -> str:
        """Render active playback stream banner card with progress bar."""
        poster_url = None
        backdrop_url = None
        if active_sessions:
            s = active_sessions[0]
            card_display = "block"
            card_border = "#10b981" if s["state"] == "playing" else "#f59e0b"
            badge_text = "Currently Streaming" if s["state"] == "playing" else "Paused"
            badge_color = card_border
            user_dev = f"• {s['username']}" + (f" on {s['player']}" if (is_admin and s['player']) else "") + (f" ({s['device']})" if (is_admin and s['device']) else "")
            stream_title = s['title']
            stream_url = s['trakt_url']
            stream_prog_text = f"{s['progress']:.1f}%"
            if s.get("remaining_str"):
                stream_prog_text += f" • {s['remaining_str']}"
            stream_prog_width = f"{s['progress']}%"
            poster_url = s.get("poster_url")
            backdrop_url = s.get("backdrop_url") or poster_url
        elif recently_finished:
            f = recently_finished
            card_display = "block"
            card_border = "#38bdf8"
            badge_text = "Recently Finished"
            badge_color = "#38bdf8"
            user_dev = f"• {f['username']}" + (f" on {f['player']}" if (is_admin and f['player']) else "")
            stream_title = f['title']
            stream_url = f['trakt_url']
            stream_prog_text = "100.0% • Finished"
            stream_prog_width = "100%"
            poster_url = f.get("poster_url")
            backdrop_url = f.get("backdrop_url") or poster_url
        else:
            card_display = "none"
            card_border = "#10b981"
            badge_text = "Currently Streaming"
            badge_color = "#10b981"
            user_dev = ""
            stream_title = ""
            stream_url = "https://trakt.tv"
            stream_prog_text = "0.0%"
            stream_prog_width = "0%"

        stream_title_esc = html.escape(str(stream_title))
        user_dev_esc = html.escape(str(user_dev))
        stream_prog_text_esc = html.escape(str(stream_prog_text))
        clean_stream_url = stream_url if str(stream_url).startswith(("https://", "http://")) else "https://trakt.tv"
        clean_stream_url_esc = html.escape(clean_stream_url)

        poster_display = "block" if poster_url else "none"
        fallback_display = "none" if poster_url else "flex"
        poster_img_src = html.escape(str(poster_url)) if poster_url else ""
        if backdrop_url:
            backdrop_style = f"background-image: url('{html.escape(str(backdrop_url))}');"
        else:
            backdrop_style = "background: radial-gradient(circle at top right, var(--accent-glow, rgba(56, 189, 248, 0.3)), transparent 60%);"

        return f"""
        <div id="active-playback-card" class="card" style="position: relative; overflow: hidden; border-left: 4px solid {card_border}; margin-bottom: 24px; display: {card_display};">
            <!-- Frosted Ambient Backdrop -->
            <div id="stream-ambient-backdrop" style="position: absolute; inset: 0; {backdrop_style} background-size: cover; background-position: center; filter: blur(35px); opacity: 0.22; pointer-events: none; z-index: 0; transition: all 0.5s ease;"></div>

            <!-- Content Area -->
            <div style="position: relative; z-index: 1; display: flex; gap: 16px; align-items: center; flex-wrap: wrap;">
                <!-- Leading Poster Thumbnail -->
                <div id="stream-poster-container" style="flex-shrink: 0; width: 68px; height: 96px; border-radius: 8px; overflow: hidden; background: var(--bg-subtle); border: 1px solid var(--border-color); display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 12px rgba(0,0,0,0.35);">
                    <img id="stream-poster-img" src="{poster_img_src}" alt="Poster" style="width: 100%; height: 100%; object-fit: cover; display: {poster_display};" onerror="this.style.display='none'; document.getElementById('stream-poster-fallback').style.display='flex';" />
                    <div id="stream-poster-fallback" style="display: {fallback_display}; width: 100%; height: 100%; align-items: center; justify-content: center; font-size: 26px; color: var(--text-muted); background: linear-gradient(135deg, rgba(255,255,255,0.05), rgba(0,0,0,0.2));">
                        🎬
                    </div>
                </div>

                <!-- Playback Details & Progress -->
                <div style="flex: 1; min-width: 240px;">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px;">
                        <div>
                            <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                                <span id="stream-pulse-indicator" class="pulse-indicator" style="background:{badge_color};"></span>
                                <span style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; color:{badge_color};" id="stream-state-badge">{badge_text}</span>
                                <span style="font-size:12px; color:var(--text-muted);" id="stream-user-device">{user_dev_esc}</span>
                            </div>
                            <h2 style="margin:4px 0 8px 0; font-size:18px; color:var(--text-main);" id="stream-title">{stream_title_esc}</h2>
                        </div>
                        <div id="stream-actions">
                            <a id="stream-trakt-link" href="{clean_stream_url_esc}" target="_blank" rel="noopener noreferrer" class="btn-sm" style="background:var(--bg-subtle); border:1px solid var(--border-color); color:var(--accent-color); text-decoration:none; display:inline-flex; align-items:center; gap:4px;">View on Trakt ↗</a>
                        </div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="display:flex; justify-content:space-between; font-size:12px; color:var(--text-muted); margin-bottom:6px;">
                            <span>Playback Progress</span>
                            <span id="stream-progress-text" style="font-weight:600; color:var(--text-main);">{stream_prog_text_esc}</span>
                        </div>
                        <div style="background:var(--bg-subtle); border-radius:9999px; height:8px; overflow:hidden; border:1px solid var(--border-color);">
                            <div id="stream-progress-bar" style="background:{badge_color}; height:100%; width:{stream_prog_width}; border-radius:9999px; transition: width 0.4s ease;"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        """

    @staticmethod
    def render_cowatch_card(
        cw_user: str,
        cw_user_display: str,
        cw_shows: list[str],
        cw_devices: list[str],
        configured_users: list[dict[str, Any]],
        cw_trackers: dict[str, Any],
        sonarr_configured: bool,
        cowatch_movies: bool,
        is_admin: bool,
        is_demo: bool,
        raw_username: Optional[str],
        mask_username_fn: Callable[[Optional[str]], str],
        household_rules: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """Render Watch Together & Multi-User Accounts management card."""
        # Shared show chips
        if not is_admin:
            count = len(cw_shows)
            chips_html = f'<div style="color:#94a3b8;font-size:13px;display:flex;align-items:center;gap:8px;padding:4px 2px;"><span>🔒</span><span><strong>{count} shared show{"s" if count != 1 else ""} configured</strong> &bull; Unlock admin access to view titles and manage whitelist.</span></div>'
        else:
            chips_html = ""
            for s in cw_shows:
                s_enc = urllib.parse.quote(s)
                del_btn = f'<button data-show="{s_enc}" onclick="removeCowatchShow(decodeURIComponent(this.dataset.show))" title="Remove {html.escape(s)}" class="cowatch-chip-del">&times;</button>'
                chips_html += f'<span class="cowatch-chip" data-title="{html.escape(s.lower())}" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:3px 9px;border-radius:9999px;font-size:12px;display:inline-flex;align-items:center;margin:2px 3px;">{html.escape(s)}{del_btn}</span>'
            if not chips_html:
                chips_html = '<span style="color:#64748b;font-size:12px;font-style:italic;">No shows added yet. Add shows below or directly from recent activity.</span>'

        # Allowed devices chips
        if not is_admin:
            device_chips_html = '<div style="color:#94a3b8;font-size:13px;display:flex;align-items:center;gap:8px;padding:4px 2px;"><span>🔒</span><span>Unlock admin access to manage allowed devices.</span></div>'
            devices_count_badge = "🔒"
        else:
            devices_count_badge = str(len(cw_devices)) if cw_devices else "All"
            if not cw_devices:
                device_chips_html = '<span style="color:#64748b;font-size:12px;font-style:italic;">All devices allowed (no device filtering). Playback on any player triggers co-watch.</span>'
            else:
                device_chips_html = ""
                for d in cw_devices:
                    d_enc = urllib.parse.quote(d)
                    del_btn = f'<button data-device="{d_enc}" onclick="removeCowatchDevice(decodeURIComponent(this.dataset.device))" title="Remove {html.escape(d)}" class="cowatch-chip-del">&times;</button>'
                    device_chips_html += f'<span class="cowatch-device-chip" data-title="{html.escape(d.lower())}" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:3px 9px;border-radius:9999px;font-size:12px;display:inline-flex;align-items:center;margin:2px 3px;">📺 {html.escape(d)}{del_btn}</span>'

        device_form_html = f'''
        <form onsubmit="event.preventDefault();addCowatchDevice();" autocomplete="off" style="margin:0;">
            <div class="cowatch-form-row">
                <input type="text" id="cowatch-device-input" name="cowatch_device" placeholder="Add device (e.g. Apple TV, Shield TV)..."
                       style="flex:1;min-width:0;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 12px;color:#f8fafc;font-size:13px;outline:none;"
                       autocomplete="off" />
                <button type="submit" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:8px 14px;white-space:nowrap;flex-shrink:0;">+ Add Device</button>
            </div>
        </form>
        <div style="margin-top:4px;font-size:11px;color:#64748b;">
            Leave empty to allow all devices. When configured, co-watching only dual-scrobbles on these players.
        </div>
        ''' if is_admin else '<div style="font-size:12px;color:#64748b;">Admin access required to configure allowed devices.</div>'

        # Multi-user accounts list
        users_badges_html = ""
        for u in configured_users:
            u_name = u["username"]
            is_def = u.get("is_default", False)
            is_cw = u.get("is_cowatch_target", False)
            auth = u.get("authenticated", False)
            status_color = "#10b981" if auth else "#ef4444"
            status_text = "Connected" if auth else "Not Linked"
            link_url = f"/auth?user={u_name}" if not is_def else "/auth"

            link_btn = ""
            if is_admin:
                if not auth:
                    link_btn = f'<a href="{link_url}" class="btn-sm" style="background:#2563eb;color:#fff;text-decoration:none;padding:2px 8px;font-size:11px;">Link &rarr;</a>'
                else:
                    link_btn = f'<a href="{link_url}" class="btn-sm" style="background:#334155;color:#94a3b8;text-decoration:none;padding:2px 8px;font-size:11px;">Reconnect</a>'

            role_label = ""
            if is_def:
                role_label = '<span style="background:#1e3a8a;color:#93c5fd;font-size:10px;padding:2px 6px;border-radius:4px;flex-shrink:0;">Default</span>'
            elif is_cw:
                role_label = '<span style="background:#701a75;color:#f5d0fe;font-size:10px;padding:2px 6px;border-radius:4px;flex-shrink:0;">Partner</span>'

            if is_def and raw_username:
                display_name = raw_username if is_admin else mask_username_fn(raw_username)
            elif is_cw and not is_admin:
                display_name = "●●●●●●●●"
            else:
                display_name = u_name if is_admin else mask_username_fn(u_name)

            users_badges_html += f"""
            <div class="cowatch-account-row">
                <div class="cowatch-account-info">
                    <span class="cowatch-account-name">@{display_name}</span>
                    {role_label}
                </div>
                <div class="cowatch-account-status">
                    <span style="color:{status_color};font-size:12px;font-weight:500;">● {status_text}</span>
                    {link_btn}
                </div>
            </div>
            """

        rule_movies_str = "Enabled" if cowatch_movies else "Disabled"
        sonarr_status_note = (
            '<span style="color:#10b981;font-size:11px;font-weight:500;display:inline-flex;align-items:center;gap:4px;">'
            '✓ Connected to Sonarr (type to search library)</span>'
            if sonarr_configured
            else '<span style="color:#64748b;font-size:11px;">Configure SONARR_URL & SONARR_API_KEY in .env for library search</span>'
        )

        partner_trackers_html = ""
        if cw_user:
            tracker_badges = []
            for trk_key, trk_name, trk_color in [
                ("trakt", "Trakt", "#ed1c24"),
                ("simkl", "Simkl", "#00e054"),
                ("anilist", "AniList", "#02a9ff"),
                ("mal", "MyAnimeList", "#2e51a2"),
            ]:
                info = cw_trackers.get(trk_key, {})
                auth = info.get("authenticated", False)
                badge_color = "#10b981" if auth else "#64748b"
                status_txt = "Connected" if auth else "Not Linked"

                act_btn = ""
                if is_admin:
                    if trk_key == "trakt":
                        link_href = f"/auth?user={cw_user}"
                        act_btn = f'<a href="{link_href}" class="btn-sm" style="padding:2px 7px;font-size:11px;background:#1e293b;border:1px solid #475569;color:#f8fafc;text-decoration:none;">{"Reconnect" if auth else "Link &rarr;"}</a>'
                    elif trk_key == "simkl":
                        act_btn = f'<button onclick="openSimklModal(\'{cw_user}\')" class="btn-sm" style="padding:2px 7px;font-size:11px;background:#1e293b;border:1px solid #475569;color:#38bdf8;cursor:pointer;">{"PIN Reconnect" if auth else "Link PIN"}</button>'
                    elif trk_key == "anilist":
                        act_btn = f'<button onclick="openAnilistModal(\'{cw_user}\')" class="btn-sm" style="padding:2px 7px;font-size:11px;background:#1e293b;border:1px solid #475569;color:#60a5fa;cursor:pointer;">{"Token" if auth else "Link &rarr;"}</button>'
                    elif trk_key == "mal":
                        act_btn = f'<button onclick="openMalModal(\'{cw_user}\')" class="btn-sm" style="padding:2px 7px;font-size:11px;background:#1e293b;border:1px solid #475569;color:#818cf8;cursor:pointer;">{"Token" if auth else "Link &rarr;"}</button>'

                tracker_badges.append(f"""
                <div style="background:#0f172a;border:1px solid #334155;border-radius:6px;padding:8px 10px;display:flex;align-items:center;justify-content:space-between;gap:8px;">
                    <div style="display:flex;align-items:center;gap:6px;">
                        <span style="width:8px;height:8px;border-radius:50%;background:{badge_color};"></span>
                        <strong style="font-size:12px;color:#f8fafc;">{trk_name}</strong>
                        <span style="font-size:11px;color:#94a3b8;">({status_txt})</span>
                    </div>
                    {act_btn}
                </div>
                """)

            partner_trackers_html = f"""
            <div style="margin-top:16px;border-top:1px solid #334155;padding-top:14px;">
                <div style="font-size:13px;font-weight:600;color:#f1f5f9;margin-bottom:8px;display:flex;align-items:center;gap:6px;">
                    <span>Partner Cloud Tracker Credentials</span>
                    <span style="font-size:11px;color:#38bdf8;font-weight:normal;">(@{cw_user_display})</span>
                </div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:8px;">
                    {''.join(tracker_badges)}
                </div>
            </div>
            """

        # Section 3: Household Multi-Tenant Routing Rules (3+ Profiles)
        rules = household_rules or []
        rules_badge = str(len(rules))
        if not rules:
            rules_html = '<div style="color:#64748b;font-size:12px;font-style:italic;padding:8px 4px;">No custom household routing rules configured. Secondary scrobbles follow the default partner settings above.</div>'
        else:
            rule_items = []
            for r in rules:
                rid = r.get("id", "")
                rname = r.get("name", "Rule")
                renabled = r.get("enabled", True)
                rtargets = r.get("targets", [])
                rdevices = r.get("devices", [])
                rshows = r.get("shows", [])
                rmedia = r.get("media_types", [])

                if not is_admin:
                    targets_str = ", ".join(f"@{mask_username_fn(t)}" for t in rtargets)
                else:
                    targets_str = ", ".join(f"@{t}" for t in rtargets)
                if not targets_str:
                    targets_str = "None"

                devices_str = ", ".join(rdevices) if rdevices else "All Devices"
                shows_str = ", ".join(rshows) if rshows else "All Shows"
                media_str = ", ".join(m.capitalize() for m in rmedia) if rmedia else "All Media"

                status_bg = "#065f46" if renabled else "#334155"
                status_col = "#34d399" if renabled else "#94a3b8"
                status_txt = "Active" if renabled else "Paused"

                actions_html = ""
                if is_admin:
                    rid_esc = urllib.parse.quote(rid)
                    actions_html = f'''
                    <div style="display:flex;align-items:center;gap:6px;">
                        <button onclick="toggleHouseholdRule(decodeURIComponent('{rid_esc}'))" class="btn-sm" style="padding:2px 8px;font-size:11px;background:#1e293b;border:1px solid #475569;color:#e2e8f0;cursor:pointer;">
                            {"Pause" if renabled else "Activate"}
                        </button>
                        <button onclick="deleteHouseholdRule(decodeURIComponent('{rid_esc}'))" class="btn-sm" style="padding:2px 8px;font-size:11px;background:#7f1d1d;color:#fecaca;border:none;cursor:pointer;">
                            &times; Delete
                        </button>
                    </div>
                    '''

                rule_items.append(f'''
                <div class="household-rule-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
                        <div style="display:flex;align-items:center;gap:8px;">
                            <strong style="color:#f8fafc;font-size:13px;">{html.escape(rname)}</strong>
                            <span style="background:{status_bg};color:{status_col};font-size:10px;font-weight:600;padding:1px 6px;border-radius:4px;">{status_txt}</span>
                        </div>
                        {actions_html}
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));gap:6px;font-size:11px;color:#94a3b8;">
                        <div><span style="color:#64748b;">Targets:</span> <strong style="color:#cbd5e1;">{html.escape(targets_str)}</strong></div>
                        <div><span style="color:#64748b;">Players:</span> <strong style="color:#cbd5e1;">📺 {html.escape(devices_str)}</strong></div>
                        <div><span style="color:#64748b;">Media:</span> <strong style="color:#cbd5e1;">🎬 {html.escape(media_str)}</strong></div>
                        <div><span style="color:#64748b;">Shows:</span> <strong style="color:#cbd5e1;">📺 {html.escape(shows_str)}</strong></div>
                    </div>
                </div>
                ''')
            rules_html = "".join(rule_items)

        household_section_html = f'''
        <!-- Household Multi-Tenant Routing Rules (3+ Profiles) -->
        <div style="margin-top:20px;border-top:1px solid #334155;padding-top:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
                <div style="font-size:13px;font-weight:600;color:#f1f5f9;display:flex;align-items:center;gap:6px;">
                    <span>🏡 Household Multi-Tenant Routing Rules</span>
                    <span id="household-rules-count-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 6px;border-radius:9999px;font-size:11px;font-weight:700;">{rules_badge}</span>
                </div>
                {f'<button onclick="openHouseholdRuleModal()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:4px 10px;font-size:11px;">+ New Routing Rule</button>' if is_admin else ''}
            </div>
            <p style="color:#94a3b8;font-size:12px;margin:0 0 10px 0;line-height:1.4;">
                Route scrobbles to specific family members or kids profiles based on player devices (e.g. Living Room TV vs Bedroom TV) and media types.
            </p>
            <div id="household-rules-container">
                {rules_html}
            </div>
        </div>
        '''

        return f"""
        <div class="card" id="card-cowatch">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                    <span>👥</span> Watch Together & Household Multi-Tenancy
                </h3>
                <span style="background:#0f172a;border:1px solid #334155;color:#38bdf8;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;">
                    {f"Partner: @{cw_user_display}" if cw_user else "Multi-Profile Routing"}
                </span>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
                Dual-scrobble watched shows to your partner's Trakt account and route household playback across arbitrary user profiles.
            </p>
            <!-- Top Section: Targeting & Destinations (Accounts & Devices side-by-side) -->
            <div class="cowatch-grid">
                <div>
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                        <div style="font-size:13px;font-weight:600;color:#f1f5f9;">Linked Trakt Accounts</div>
                        {f'<button onclick="promptLinkAccount()" class="btn-sm" style="background:#334155;color:#38bdf8;">+ Link Account</button>' if is_admin else ''}
                    </div>
                    <div>
                        {users_badges_html}
                    </div>
                </div>
                <div>
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">
                        <div style="font-size:13px;font-weight:600;color:#f1f5f9;display:flex;align-items:center;gap:6px;">
                            <span>Allowed Devices Whitelist</span>
                            <span id="cowatch-devices-count-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 6px;border-radius:9999px;font-size:11px;font-weight:700;">{devices_count_badge}</span>
                        </div>
                    </div>
                    <div id="cowatch-devices-chips-container" class="custom-scroll" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 10px;min-height:44px;max-height:140px;overflow-y:auto;margin-bottom:10px;display:flex;flex-wrap:wrap;align-content:flex-start;align-items:center;">
                        {device_chips_html}
                    </div>
                    {device_form_html}
                </div>
            </div>
            {partner_trackers_html}

            <!-- Bottom Section: Shared Media & Shows Whitelist (Full Width) -->
            <div style="margin-top:20px;border-top:1px solid #334155;padding-top:16px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap;">
                    <div style="font-size:13px;font-weight:600;color:#f1f5f9;display:flex;align-items:center;gap:6px;">
                        <span>Shared Shows Whitelist</span>
                        <span id="cowatch-count-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 6px;border-radius:9999px;font-size:11px;font-weight:700;">{len(cw_shows)}</span>
                    </div>
                    {f'<input type="text" id="cowatch-filter-input" placeholder="Filter list..." oninput="filterCowatchChips(this.value)" style="background:#0f172a;border:1px solid #334155;border-radius:4px;padding:3px 8px;color:#f8fafc;font-size:11px;outline:none;width:130px;" />' if is_admin else ''}
                </div>
                <div id="cowatch-chips-container" class="custom-scroll" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;min-height:54px;max-height:220px;overflow-y:auto;margin-bottom:10px;display:flex;flex-wrap:wrap;align-content:flex-start;align-items:center;">
                    {chips_html}
                </div>
                {f'''
                <form onsubmit="event.preventDefault();addCowatchShow();" autocomplete="off" style="margin:0;">
                    <div class="cowatch-form-row">
                        <div style="flex:1;min-width:0;position:relative;">
                            <input type="search" id="cowatch-show-input" name="cowatch_show_search" placeholder="Add show (e.g. Severance, Lanterns)..."
                                    style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 12px;color:#f8fafc;font-size:13px;outline:none;"
                                    oninput="onCowatchShowInput(this.value)"
                                    onfocus="onCowatchShowInput(this.value)"
                                    autocomplete="off"
                                    data-lpignore="true"
                                    data-1p-ignore="true"
                                    onkeydown="if(event.key==='Enter')addCowatchShow()" />
                            <div id="sonarr-suggestions" style="display:none;position:absolute;top:100%;left:0;right:0;background:#1e293b;border:1px solid #3b82f6;border-radius:6px;margin-top:4px;max-height:220px;overflow-y:auto;z-index:100;box-shadow:0 10px 15px -3px rgba(0,0,0,0.7);"></div>
                        </div>
                        <button type="submit" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:8px 14px;white-space:nowrap;flex-shrink:0;">+ Add Show</button>
                    </div>
                </form>
                <div style="margin-top:4px;">{sonarr_status_note}</div>
                ''' if is_admin else '<div style="font-size:12px;color:#64748b;">Admin access required to add or remove shared shows.</div>'}
                <div style="margin-top:10px;font-size:12px;color:#94a3b8;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                    <span>Movies: <strong id="cowatch-movies-status">{rule_movies_str}</strong></span>
                    {f'<button id="cowatch-movies-btn" onclick="toggleCowatchMovies()" class="btn-sm" style="padding:2px 8px;font-size:11px;background:#334155;border:1px solid #475569;">Toggle Movies ({ "Disable" if cowatch_movies else "Enable" })</button>' if is_admin else ''}
                </div>
            </div>
            {household_section_html}
        </div>
        """

    @staticmethod
    def render_reconciliation_card(
        sync_status: dict[str, Any],
        bg_sync_state: dict[str, Any],
        bg_interval_hours: int,
        is_admin: bool,
        repo_url: str,
    ) -> str:
        """Render two-way library reconciliation and automated cloud sync card."""
        active_srv = sync_status.get("active_server", "plex")
        plex_cfg = sync_status.get("plex_configured", False)
        plex_conn = sync_status.get("plex_connected", False)
        jf_cfg = sync_status.get("jellyfin_configured", False)
        jf_conn = sync_status.get("jellyfin_connected", False)
        emby_cfg = sync_status.get("emby_configured", False)
        emby_conn = sync_status.get("emby_connected", False)

        server_status_badges = []
        if plex_cfg:
            col = "#10b981" if plex_conn else "#f59e0b"
            st = "Online" if plex_conn else "Unreachable"
            server_status_badges.append(f'<span style="color:{col};font-size:12px;font-weight:600;">● Plex {st}</span>')
        if jf_cfg:
            col = "#10b981" if jf_conn else "#f59e0b"
            st = "Online" if jf_conn else "Unreachable"
            server_status_badges.append(f'<span style="color:{col};font-size:12px;font-weight:600;">● Jellyfin {st}</span>')
        if emby_cfg:
            col = "#10b981" if emby_conn else "#f59e0b"
            st = "Online" if emby_conn else "Unreachable"
            server_status_badges.append(f'<span style="color:{col};font-size:12px;font-weight:600;">● Emby {st}</span>')

        if not server_status_badges:
            server_status_badges.append('<span style="color:#94a3b8;font-size:12px;">● Direct API Not Configured</span>')
        server_badges_html = " ".join(server_status_badges)

        diff_count = sync_status.get("diff_count", 0)
        int_mins = sync_status.get("interval_minutes", 0)
        auto_sync_badge = f'<span style="background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">Periodic: Every {int_mins}m</span>' if int_mins > 0 else '<span style="background:#0f172a;border:1px solid #334155;color:#64748b;padding:2px 8px;border-radius:4px;font-size:11px;">Periodic: Manual</span>'

        # Background cloud sync telemetry
        bg_last_run = bg_sync_state.get("last_run_timestamp")
        bg_next_run = bg_sync_state.get("next_scheduled_run")
        bg_status_txt = bg_sync_state.get("last_run_status", "never_run")
        bg_run_display = "Never"
        if bg_last_run:
            try:
                dt = datetime.datetime.fromisoformat(bg_last_run.replace("Z", "+00:00"))
                bg_run_display = dt.strftime("%b %d, %H:%M UTC")
            except Exception:
                bg_run_display = str(bg_last_run)[:16]

        bg_next_display = "Manual"
        if bg_next_run:
            try:
                dt = datetime.datetime.fromisoformat(bg_next_run.replace("Z", "+00:00"))
                bg_next_display = dt.strftime("%b %d, %H:%M UTC")
            except Exception:
                bg_next_display = str(bg_next_run)[:16]

        bg_status_color = "#10b981" if bg_status_txt == "success" else ("#f59e0b" if bg_status_txt == "partial_error" else "#64748b")

        cloud_sync_panel_html = f"""
        <div style="margin-top:12px;background:#090d16;border:1px solid #1e293b;border-radius:6px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;font-size:12px;">
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;color:#94a3b8;">
                <span>Automated Cloud Sync: <strong style="color:#f1f5f9;">Every {bg_interval_hours}h</strong></span>
                <span>Last Run: <strong style="color:#f1f5f9;">{bg_run_display}</strong> (<span style="color:{bg_status_color};">{bg_status_txt}</span>)</span>
                <span>Next: <strong style="color:#38bdf8;">{bg_next_display}</strong></span>
                <span>Export: <strong style="color:#10b981;">Letterboxd CSV</strong></span>
            </div>
            <div>
                {f'<button onclick="triggerBackgroundCloudSync(this)" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;font-weight:600;padding:4px 10px;">⚡ Run Cloud Sync Now</button>' if is_admin else ''}
            </div>
        </div>
        """

        any_server_configured = plex_cfg or jf_cfg or emby_cfg
        if any_server_configured:
            return f"""
            <div class="card" id="card-reconciliation">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                    <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                        <span>🔄</span> Two-Way Library Reconciliation & Reverse Sync
                    </h3>
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        {server_badges_html}
                        {auto_sync_badge}
                    </div>
                </div>
                <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
                    Bi-directional sync matches watched history and ratings between your media servers (Plex, Jellyfin, Emby) and Trakt with automatic echo-loop suppression.
                </p>
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
                    <div>
                        <div style="font-size:14px;font-weight:600;color:#f8fafc;display:flex;align-items:center;gap:6px;">
                            <span>Pending Discrepancies</span>
                            <span id="reconcile-diff-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 8px;border-radius:9999px;font-size:12px;font-weight:700;">{diff_count}</span>
                        </div>
                        <div style="font-size:12px;color:#94a3b8;margin-top:4px;">
                            Ratings sync: {'Enabled' if sync_status.get('sync_ratings') else 'Disabled'} &bull; Startup sync: {'Active' if sync_status.get('sync_on_startup') else 'Off'}
                        </div>
                    </div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        {f'<button onclick="openReconcileSettingsModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#f8fafc;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚙️ Configure</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔒 Configure</button>'}
                        {f'<button onclick="openReconcileModal(true)" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔍 Review Discrepancies</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔒 Review Discrepancies</button>'}
                        {f'<button onclick="quickReconcileTraktToPlex(this)" class="btn-sm" style="background:#10b981;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚡ Quick Sync (Trakt &rarr; {active_srv.capitalize()})</button>' if is_admin else ''}
                    </div>
                </div>
                {cloud_sync_panel_html}
            </div>
            """
        else:
            return f"""
            <div class="card" id="card-reconciliation">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                    <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                        <span>🔄</span> Two-Way Library Reconciliation
                    </h3>
                    <span style="color:#94a3b8;font-size:12px;">● Direct API Not Configured</span>
                </div>
                <p style="color:#94a3b8;font-size:13px;margin-bottom:12px;line-height:1.5;">
                    Enable direct media server reconciliation (Plex, Jellyfin, Emby) to pull watched history and user ratings from Trakt back to your media server with loop prevention.
                </p>
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;font-size:13px;color:#cbd5e1;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                    <span>Configure your media server direct connection to activate two-way reconciliation and rating synchronization.</span>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        {f'<button onclick="openReconcileSettingsModal()" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚙️ Set Up Connection</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔒 Set Up Connection</button>'}
                        <a href="{repo_url}#readme" target="_blank" rel="noopener" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;text-decoration:none;">View Guide &rarr;</a>
                    </div>
                </div>
                {cloud_sync_panel_html}
            </div>
            """

    @staticmethod
    def render_backup_card(is_admin: bool) -> str:
        """Render system operations, backup download/restore, and observability card."""
        return f"""
        <div class="card" id="card-backup">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                    <span>💾</span> System Operations & Observability
                </h3>
                <div style="display:flex;gap:8px;align-items:center;">
                    {f'<button onclick="openLogsModal()" class="btn-sm" style="background:#1e293b;border:1px solid #3b82f6;color:#60a5fa;display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-weight:600;">📜 View Logs</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;cursor:pointer;" title="Admin unlock required to view logs">🔒 View Logs</button>'}
                    <a href="/metrics" target="_blank" rel="noopener" class="btn-sm" style="background:#0f172a;border:1px solid #334155;color:#38bdf8;text-decoration:none;">📊 Prometheus /metrics ↗</a>
                </div>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
                Export or restore your configuration, multi-user Trakt tokens, co-watch whitelist, and inspect live service logs.
            </p>
            <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;">
                {f'''
                <a href="/api/backup" download class="btn-sm" style="background:#0284c7;color:#fff;text-decoration:none;padding:8px 16px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">
                    💾 Download Backup (.zip)
                </a>
                <label class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:#e2e8f0;padding:8px 16px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px;">
                    📤 Restore Backup (.zip)
                    <input type="file" id="backup-file-input" accept=".zip" onchange="uploadBackup(this)" style="display:none;" />
                </label>
                <button onclick="openTestWebhookModal()" class="btn-sm" style="background:#4338ca;color:#fff;border:1px solid #6366f1;padding:8px 16px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px;">
                    🧪 Test Webhook
                </button>
                ''' if is_admin else '<div style="font-size:12px;color:#64748b;">Admin authorization required to download or restore server backups.</div>'}
            </div>
        </div>
        """

    @staticmethod
    def render_ecosystem_card(eco_data: dict[str, Any], is_admin: bool) -> str:
        """Render multi-server ecosystem health topology card."""
        eco_servers = eco_data.get("servers", [])
        eco_healthy = eco_data.get("healthy_count", 0)
        eco_total = eco_data.get("total_count", len(eco_servers))

        eco_cards_html = ""
        for srv in eco_servers:
            st = srv.get("status", "unknown")
            if st == "connected":
                st_color = "#10b981"
                st_bg = "#064e3b"
                st_border = "#059669"
            elif st == "available":
                st_color = "#38bdf8"
                st_bg = "#0c4a6e"
                st_border = "#0284c7"
            elif st == "disabled":
                st_color = "#cbd5e1"
                st_bg = "#334155"
                st_border = "#64748b"
            elif st == "error":
                st_color = "#f87171"
                st_bg = "#7f1d1d"
                st_border = "#dc2626"
            else:
                st_color = "#94a3b8"
                st_bg = "#1e293b"
                st_border = "#334155"

            srv_icon = "🎬"
            sid = srv.get("id", "")
            if sid == "plex":
                srv_icon = "🔶"
            elif sid == "jellyfin":
                srv_icon = "🟣"
            elif sid == "emby":
                srv_icon = "🟢"
            elif sid == "trakt":
                srv_icon = "🔴"
            elif sid == "simkl":
                srv_icon = "✨"
            elif sid == "anilist":
                srv_icon = "⚡"
            elif sid == "myanimelist":
                srv_icon = "🎌"
            elif sid == "sonarr":
                srv_icon = "📺"
            elif sid == "radarr":
                srv_icon = "🍿"

            srv_name = html.escape(srv.get('name', ''))
            is_disabled = (not srv.get("enabled", True)) or st == "disabled" or srv.get("badge") in ("Disabled", "Paused")
            card_class = "eco-card eco-card-disabled" if is_disabled else "eco-card"
            card_extra_style = "opacity:0.65;transition:opacity 0.2s ease,border-color 0.2s ease;" if is_disabled else ""
            card_extra_attrs = 'onmouseenter="this.style.opacity=\'1\'" onmouseleave="this.style.opacity=\'0.65\'"' if is_disabled else ""

            toggle_btn = ""
            if is_admin and sid in ("plex", "jellyfin", "emby"):
                cat = "server"
                key = sid
                is_en = srv.get("enabled", True)
                config_gear = f'<button onclick="openReconcileSettingsModal(\'{sid}\')" class="btn-sm" style="display:inline-flex;align-items:center;padding:3px 7px;font-size:11px;background:#1e293b;border:1px solid #475569;color:#38bdf8;cursor:pointer;" title="Configure {srv_name} Direct API">⚙️</button>'
                if is_en:
                    toggle_btn = f'{config_gear} <button onclick="toggleSetting(\'{cat}\', \'{key}\', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Disable {srv_name}"><span>⏸</span><span>Disable</span></button>'
                else:
                    toggle_btn = f'{config_gear} <button onclick="toggleSetting(\'{cat}\', \'{key}\', true, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:600;border-radius:6px;background:#064e3b;border:1px solid #059669;color:#6ee7b7;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Enable {srv_name}"><span>▶</span><span>Enable</span></button>'

            eco_cards_html += f"""
            <div class="{card_class}" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;{card_extra_style}" {card_extra_attrs}>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">{srv_icon}</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{html.escape(srv.get('name', ''))}</div>
                            <div style="font-size:11px;color:#64748b;">{html.escape(srv.get('category', ''))}</div>
                        </div>
                    </div>
                    <span style="background:{st_bg};border:1px solid {st_border};color:{st_color};font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">
                        {html.escape(srv.get('badge', st.capitalize()))}
                    </span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                    <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="{html.escape(srv.get('details', ''))}">
                        {html.escape(srv.get('details', ''))}
                    </div>
                    {toggle_btn}
                </div>
            </div>
            """

        return f"""
        <div class="card" id="card-ecosystem">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                    <span>🌐</span> Multi-Server Ecosystem
                </h3>
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="background:#0f172a;border:1px solid #334155;color:#10b981;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">
                        <span style="width:7px;height:7px;border-radius:50%;background:#10b981;display:inline-block;"></span>
                        {eco_healthy}/{eco_total} Services Healthy
                    </span>
                    <button onclick="openSettingsModal('servers')" class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:#cbd5e1;padding:4px 10px;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:5px;">⚙️ Manage Servers</button>
                </div>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;line-height:1.5;">
                Unified operational topology across all media servers and automated acquisition engines.
            </p>
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(270px, 1fr));gap:10px;">
                {eco_cards_html}
            </div>
        </div>
        """

    @staticmethod
    def render_multi_tracker_hub_card(
        trk_status_all: dict[str, Any],
        is_admin: bool,
        is_demo: bool,
        trakt_authenticated: bool,
        mask_username_fn: Callable[[Optional[str]], str],
    ) -> str:
        """Render unified Multi-Tracker Architecture Hub card with 9 cloud tracker cards."""
        trackers_dict = trk_status_all.get("trackers", {})
        simkl_status = trackers_dict.get("simkl", {})
        simkl_auth = simkl_status.get("authenticated", False)

        quick_scrobble_btn = '<button onclick="openManualScrobbleModal()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;">🍿 Quick Scrobble</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🔒 Quick Scrobble</button>'

        cross_sync_btn = ""
        if simkl_auth and (is_demo or trakt_authenticated):
            if is_admin:
                cross_sync_btn = '<button onclick="openCrossSyncModal(true)" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;">🔄 Reconcile Trakt & Simkl</button>'
            else:
                cross_sync_btn = '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🔒 Reconcile</button>'

        trackers_meta = [
            {"id": "trakt", "cat": "universal", "icon": "🔴", "name": "Trakt.tv", "desc": "Universal &bull; Movies &amp; Shows"},
            {"id": "simkl", "cat": "universal", "icon": "🔵", "name": "Simkl", "desc": "Universal &bull; Movies, Shows, Anime"},
            {"id": "tmdb", "cat": "universal", "icon": "🟡", "name": "TMDb", "desc": "Universal &bull; Watchlist &amp; Ratings"},
            {"id": "anilist", "cat": "anime", "icon": "🔷", "name": "AniList", "desc": "Anime &bull; Episodes &amp; Ratings"},
            {"id": "myanimelist", "cat": "anime", "icon": "🟦", "name": "MyAnimeList", "desc": "Anime &bull; Episodes &amp; Ratings"},
            {"id": "kitsu", "cat": "anime", "icon": "🟠", "name": "Kitsu", "desc": "Anime &bull; Progress &amp; Ratings"},
            {"id": "letterboxd", "cat": "social_diary", "icon": "🟢", "name": "Letterboxd", "desc": "Social Diary &bull; Film Diary &amp; CSV"},
            {"id": "serializd", "cat": "social_diary", "icon": "🟨", "name": "Serializd", "desc": "Social Diary &bull; TV Episode Diary"},
            {"id": "mdblist", "cat": "lists_ratings", "icon": "🟣", "name": "MDBList", "desc": "Lists &amp; Ratings &bull; Score Aggregation"},
        ]

        active_trackers_count = 0
        hub_items_html = ""
        for tm in trackers_meta:
            t_info = trackers_dict.get(tm["id"], {})
            t_auth = t_info.get("authenticated", False)
            t_cfg = t_info.get("configured", False)
            t_user = t_info.get("user")
            t_disp_user = (t_user if is_admin else mask_username_fn(t_user)) if t_user else None
            t_enabled = settings_mgr.is_tracker_enabled(tm["id"])

            if not t_enabled and (t_auth or t_cfg):
                t_badge = '<span style="background:#1e293b;border:1px solid #475569;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">⏸ Paused</span>'
            elif t_auth:
                active_trackers_count += 1
                u_suffix = f" (@{t_disp_user})" if t_disp_user else ""
                t_badge = f'<span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● Active{u_suffix}</span>'
            elif t_cfg:
                active_trackers_count += 1
                t_badge = '<span style="background:#1e293b;border:1px solid #eab308;color:#fde047;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● Ready</span>'
            else:
                t_badge = '<span style="background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">● Optional</span>'

            tm_id = tm["id"]
            tm_name = tm["name"]
            color_map = {
                "trakt": "#f87171",
                "simkl": "#38bdf8",
                "tmdb": "#eab308",
                "anilist": "#60a5fa",
                "myanimelist": "#818cf8",
                "kitsu": "#fb923c",
                "letterboxd": "#34d399",
                "serializd": "#facc15",
                "mdblist": "#c084fc",
            }
            accent = color_map.get(tm_id, "#94a3b8")
            cfg_btn = f'<button onclick="openSettingsModal(\'trackers\', \'{tm_id}\')" class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:{accent};padding:2px 7px;font-size:11px;border-radius:4px;cursor:pointer;" title="{tm_name} Settings">⚙️</button>'

            hub_items_html += f"""
            <div class="hub-tracker-item" data-cat="{tm['cat']}" style="display:flex;align-items:center;justify-content:space-between;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;gap:8px;">
                <div style="display:flex;align-items:center;gap:10px;min-width:0;">
                    <span style="font-size:18px;flex-shrink:0;">{tm['icon']}</span>
                    <div style="min-width:0;">
                        <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{tm['name']}</div>
                        <div style="font-size:11px;color:#64748b;">{tm['desc']}</div>
                    </div>
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
                    {t_badge}
                    {cfg_btn}
                </div>
            </div>
            """

        return f"""
        <div class="card" id="card-multi-tracker">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                    <span>🌐</span> Multi-Tracker Hub &bull; Cloud Synchronization
                </h3>
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="background:#0f172a;border:1px solid #334155;color:#10b981;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">
                        <span style="width:7px;height:7px;border-radius:50%;background:#10b981;display:inline-block;"></span>
                        {active_trackers_count}/9 Trackers Active
                    </span>
                </div>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;line-height:1.5;">
                Broadcast playback scrobbles, ratings, and diary entries across universal trackers, dedicated anime services, social diaries, and curated lists in real time.
            </p>
            <div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap;">
                <button class="btn-sm hub-cat-tab" onclick="filterHubTrackers('all', this)" style="background:#0284c7;border:1px solid #0284c7;color:#fff;font-weight:600;padding:5px 12px;font-size:12px;border-radius:6px;cursor:pointer;">All Trackers (9)</button>
                <button class="btn-sm hub-cat-tab" onclick="filterHubTrackers('universal', this)" style="background:#1e293b;border:1px solid #334155;color:#cbd5e1;padding:5px 12px;font-size:12px;border-radius:6px;cursor:pointer;">Universal (3)</button>
                <button class="btn-sm hub-cat-tab" onclick="filterHubTrackers('anime', this)" style="background:#1e293b;border:1px solid #334155;color:#cbd5e1;padding:5px 12px;font-size:12px;border-radius:6px;cursor:pointer;">Anime (3)</button>
                <button class="btn-sm hub-cat-tab" onclick="filterHubTrackers('social_diary', this)" style="background:#1e293b;border:1px solid #334155;color:#cbd5e1;padding:5px 12px;font-size:12px;border-radius:6px;cursor:pointer;">Social Diaries (2)</button>
                <button class="btn-sm hub-cat-tab" onclick="filterHubTrackers('lists_ratings', this)" style="background:#1e293b;border:1px solid #334155;color:#cbd5e1;padding:5px 12px;font-size:12px;border-radius:6px;cursor:pointer;">Lists &amp; Ratings (1)</button>
            </div>
            <div id="hub-trackers-grid" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));gap:10px;margin-bottom:14px;">
                {hub_items_html}
            </div>
            <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
                <div style="font-size:12px;color:#cbd5e1;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                    <span>Active Trackers: <strong>{active_trackers_count}/9 Connected</strong></span>
                    <span style="color:#64748b;">&bull;</span>
                    <span>Anime Tracking Engine: <strong>{"Auto-Detect Active" if Config.ANIME_AUTO_DETECT else "Explicit Only"}</strong></span>
                    <span style="color:#64748b;">&bull;</span>
                    <span>Cross-Tracker Sync: <strong>{"Ready" if simkl_auth and (is_demo or trakt_authenticated) else "Requires Trakt + Simkl Auth"}</strong></span>
                </div>
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                    {quick_scrobble_btn}
                    {cross_sync_btn}
                    <button onclick="openSettingsModal('trackers')" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;padding:6px 14px;font-size:12px;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;cursor:pointer;">⚙️ Configure Trackers</button>
                    <a href="/api/letterboxd/export" download="letterboxd_diary.csv" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#34d399;text-decoration:none;padding:6px 12px;font-size:12px;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;" title="Export Letterboxd Watch Diary as CSV">📥 Letterboxd CSV</a>
                    <div style="display:inline-flex;gap:4px;align-items:center;flex-wrap:wrap;">
                        <a href="/auth" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#f87171;text-decoration:none;padding:6px 10px;font-size:11px;display:inline-flex;align-items:center;gap:3px;white-space:nowrap;" title="Trakt Auth Portal">Trakt ↗</a>
                        <button onclick="openSimklModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:6px 10px;font-size:11px;display:inline-flex;align-items:center;gap:3px;white-space:nowrap;cursor:pointer;" title="Simkl Modal">Simkl PIN</button>
                        <button onclick="openAnilistModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#60a5fa;padding:6px 10px;font-size:11px;display:inline-flex;align-items:center;gap:3px;white-space:nowrap;cursor:pointer;" title="AniList Auth Modal">AniList ↗</button>
                        <button onclick="openMalModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#818cf8;padding:6px 10px;font-size:11px;display:inline-flex;align-items:center;gap:3px;white-space:nowrap;cursor:pointer;" title="MyAnimeList Auth Modal">MAL ↗</button>
                    </div>
                </div>
            </div>
        </div>
        """

    @staticmethod
    def render_arr_bridge_card(arr_status: dict[str, Any], is_admin: bool, repo_url: str) -> str:
        """Render Content Bridge & *Arr Watchlist Automation card."""
        arr_cfg = arr_status.get("configured", False)
        sonarr_cfg = arr_status.get("sonarr_configured", False)
        sonarr_conn = arr_status.get("sonarr_connected", False)
        radarr_cfg = arr_status.get("radarr_configured", False)
        radarr_conn = arr_status.get("radarr_connected", False)
        overseerr_cfg = arr_status.get("overseerr_configured", False)
        overseerr_conn = arr_status.get("overseerr_connected", False)
        overseerr_app = arr_status.get("overseerr_app_name", "Overseerr")

        if arr_cfg:
            auto_int = arr_status.get("interval_minutes", 0)
            auto_badge = f'<span style="background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">Polling: Every {auto_int}m</span>' if auto_int > 0 else '<span style="background:#0f172a;border:1px solid #334155;color:#64748b;padding:2px 8px;border-radius:4px;font-size:11px;">Polling: Manual</span>'

            sonarr_desc = "Online" if sonarr_conn else "Unreachable"
            radarr_desc = "Online" if radarr_conn else "Unreachable"
            overseerr_desc = "Online" if overseerr_conn else "Unreachable"

            overseerr_pill = (
                f'<span style="background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:3px 9px;border-radius:6px;font-size:12px;display:inline-flex;align-items:center;gap:6px;"><span style="color:#a855f7;">✨ {overseerr_app}</span><span style="color:#10b981;font-weight:600;">{overseerr_desc}</span></span>'
                if overseerr_cfg
                else '<span style="background:#0f172a;border:1px solid #334155;color:#64748b;padding:3px 9px;border-radius:6px;font-size:12px;">✨ Overseerr: Off</span>'
            )

            sonarr_pill = (
                f'<span style="background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:3px 9px;border-radius:6px;font-size:12px;display:inline-flex;align-items:center;gap:6px;"><span style="color:#38bdf8;">📺 Sonarr</span><span style="color:#10b981;font-weight:600;">{sonarr_desc}</span></span>'
                if sonarr_cfg
                else '<span style="background:#0f172a;border:1px solid #334155;color:#64748b;padding:3px 9px;border-radius:6px;font-size:12px;">📺 Sonarr: Off</span>'
            )

            radarr_pill = (
                f'<span style="background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:3px 9px;border-radius:6px;font-size:12px;display:inline-flex;align-items:center;gap:6px;"><span style="color:#f59e0b;">🍿 Radarr</span><span style="color:#10b981;font-weight:600;">{radarr_desc}</span></span>'
                if radarr_cfg
                else '<span style="background:#0f172a;border:1px solid #334155;color:#64748b;padding:3px 9px;border-radius:6px;font-size:12px;">🍿 Radarr: Off</span>'
            )

            sync_btn_html = (
                '<button onclick="triggerArrWatchlistSync(this)" class="btn-sm" style="background:#10b981;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚡ Sync Watchlist Now</button>'
                if is_admin
                else '<button onclick="openUnlockModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#64748b;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔒 Sync Watchlist</button>'
            )

            return f"""
            <div class="card" id="card-arr-bridge">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                    <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                        <span>🎬</span> Content Bridge & *Arr Watchlist Automation
                    </h3>
                    <div style="display:flex;align-items:center;gap:8px;">
                        {auto_badge}
                    </div>
                </div>
                <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
                    Automatically monitors your Trakt Watchlist, checks library duplicates, and acquires new movies and shows into Overseerr, Radarr, and Sonarr with automatic search and notification dispatch.
                </p>
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
                    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                        {overseerr_pill}
                        {sonarr_pill}
                        {radarr_pill}
                        <span style="font-size:12px;color:#94a3b8;">Search on add: <strong>{'Enabled' if arr_status.get('search_on_add') else 'Disabled'}</strong> &bull; Alerts: <strong>{'On' if Config.ARR_NOTIFY_ON_ADD else 'Off'}</strong></span>
                    </div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        {sync_btn_html}
                        <button onclick="openArrModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:8px 14px;display:inline-flex;align-items:center;gap:6px;">📋 View Log</button>
                        <button onclick="openSettingsModal('automation')" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:8px 14px;display:inline-flex;align-items:center;gap:6px;cursor:pointer;">⚙️ Configure</button>
                    </div>
                </div>
            </div>
            """
        else:
            return f"""
            <div class="card" id="card-arr-bridge">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                    <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                        <span>🎬</span> Content Bridge & *Arr Automation
                    </h3>
                    <span style="color:#94a3b8;font-size:12px;">● Not Configured</span>
                </div>
                <p style="color:#94a3b8;font-size:13px;margin-bottom:12px;line-height:1.5;">
                    Connect Trakt Watchlists directly to Sonarr and Radarr. When you add movies or shows to your Trakt Watchlist, Omniscrobble automatically looks them up and queues them for acquisition.
                </p>
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;font-size:13px;color:#cbd5e1;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                    <span>Configure Sonarr and Radarr connections directly in the Settings Hub or via <code>.env</code>.</span>
                    <div style="display:flex;gap:6px;align-items:center;">
                        <button onclick="openSettingsModal('automation')" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;padding:6px 12px;border:none;cursor:pointer;">⚙️ Setup *Arr Bridge</button>
                        <a href="{repo_url}#readme" target="_blank" rel="noopener" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;text-decoration:none;">View Guide &rarr;</a>
                    </div>
                </div>
            </div>
            """

    @staticmethod
    def render_analytics_card(analytics: dict[str, Any], is_admin: bool) -> str:
        """Render the Personal Analytics, Viewing Habits & OmniWrapped card."""
        watch_time = analytics.get("total_watch_formatted", "0h 0m")
        total_scrobbles = analytics.get("total_scrobbles", 0)
        movies = analytics.get("movies_watched", 0)
        episodes = analytics.get("episodes_watched", 0)
        ratings = analytics.get("ratings_submitted", 0)

        solo_hours = analytics.get("solo_hours", 0.0)
        cowatch_hours = analytics.get("cowatch_hours", 0.0)
        cw_pct = analytics.get("cowatch_ratio_percent", 0)
        solo_pct = 100 - cw_pct if (solo_hours + cowatch_hours > 0) else 100

        server_dist = analytics.get("server_distribution", {"Plex": 100})
        server_badges = "".join(
            f'<span style="background:#1e293b;border:1px solid #334155;color:#cbd5e1;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-right:6px;">{s}: <strong style="color:#38bdf8;">{pct}%</strong></span>'
            for s, pct in server_dist.items()
        )

        top_shows = analytics.get("top_shows", [])
        top_show_label = f"📺 {top_shows[0]['show']} ({top_shows[0]['episodes']} eps)" if top_shows else "📺 No series logged yet"

        top_genres = analytics.get("top_genres", [])
        genre_tags = "".join(
            f'<span style="background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:2px 7px;border-radius:4px;font-size:10px;margin-right:4px;">{g["genre"]}</span>'
            for g in top_genres[:4]
        )

        admin_debugger_btn = ""
        if is_admin:
            admin_debugger_btn = """
            <button onclick="openWebhookDebuggerModal()" class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:#e2e8f0;padding:5px 10px;font-size:11px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px;">
                <span>🔍</span><span>Webhook Inspector</span>
            </button>
            """

        return f"""
        <div class="card" style="margin-bottom:20px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <div style="display:flex;align-items:center;gap:8px;">
                    <h3 style="margin:0;font-size:15px;color:#f8fafc;display:flex;align-items:center;gap:8px;">
                        <span>📊</span> Personal Analytics & Viewing Habits
                    </h3>
                </div>
                <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
                    {admin_debugger_btn}
                    <button onclick="openOmniWrappedModal()" class="btn-sm" style="background:linear-gradient(135deg, #8b5cf6, #3b82f6);color:#fff;border:none;padding:5px 12px;font-size:11px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px;box-shadow:0 2px 4px rgba(139,92,246,0.3);">
                        <span>✨</span><span>OmniWrapped</span>
                    </button>
                </div>
            </div>

            <!-- Quick Metrics Grid -->
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));gap:10px;margin-bottom:14px;">
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;">
                    <div style="font-size:11px;color:#64748b;text-transform:uppercase;font-weight:600;margin-bottom:2px;">Watch Time</div>
                    <div style="font-size:18px;font-weight:700;color:#38bdf8;">{watch_time}</div>
                    <div style="font-size:10px;color:#94a3b8;margin-top:2px;">Across all devices</div>
                </div>
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;">
                    <div style="font-size:11px;color:#64748b;text-transform:uppercase;font-weight:600;margin-bottom:2px;">Completed Titles</div>
                    <div style="font-size:18px;font-weight:700;color:#f8fafc;">{total_scrobbles}</div>
                    <div style="font-size:10px;color:#94a3b8;margin-top:2px;">{movies} movies &bull; {episodes} eps</div>
                </div>
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;">
                    <div style="font-size:11px;color:#64748b;text-transform:uppercase;font-weight:600;margin-bottom:2px;">Co-Watch Ratio</div>
                    <div style="font-size:18px;font-weight:700;color:#c084fc;">{cw_pct}%</div>
                    <div style="font-size:10px;color:#94a3b8;margin-top:2px;">{cowatch_hours}h shared / {solo_hours}h solo</div>
                </div>
                <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;">
                    <div style="font-size:11px;color:#64748b;text-transform:uppercase;font-weight:600;margin-bottom:2px;">Star Ratings</div>
                    <div style="font-size:18px;font-weight:700;color:#fbbf24;">{ratings}</div>
                    <div style="font-size:10px;color:#94a3b8;margin-top:2px;">Synced across trackers</div>
                </div>
            </div>

            <!-- Co-Watch Ratio Progress Bar -->
            <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;margin-bottom:12px;">
                <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;margin-bottom:6px;">
                    <span style="color:#94a3b8;"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#38bdf8;margin-right:4px;"></span>Solo Viewing ({solo_pct}%)</span>
                    <span style="color:#94a3b8;"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#c084fc;margin-right:4px;"></span>Shared Co-Watching ({cw_pct}%)</span>
                </div>
                <div style="width:100%;height:6px;background:#1e293b;border-radius:9999px;overflow:hidden;display:flex;">
                    <div style="height:100%;width:{solo_pct}%;background:#38bdf8;"></div>
                    <div style="height:100%;width:{cw_pct}%;background:#c084fc;"></div>
                </div>
            </div>

            <!-- Distribution & Highlights Footer -->
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;font-size:12px;color:#94a3b8;">
                <div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;">
                    <span style="color:#64748b;margin-right:4px;">Servers:</span>
                    {server_badges}
                </div>
                <div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;">
                    <strong style="color:#cbd5e1;font-size:11px;">{top_show_label}</strong>
                    {genre_tags}
                </div>
            </div>
        </div>
        """

    @staticmethod
    def render_template(template: str, replacements: dict[str, str]) -> str:
        """Substitute all replacement tokens into dashboard HTML template."""
        rendered = template
        for k, v in replacements.items():
            rendered = rendered.replace(k, v)
        return rendered


dashboard_renderer = DashboardRenderer()
