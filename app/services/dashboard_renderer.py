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
        return f'<span class="activity-status-badge activity-status-cowatch" title="Synced to {target_txt}: {reason_txt}">👥 Co-Watched</span>'

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
        return f'<span class="activity-status-badge activity-status-success" title="{tooltip}">{label}</span>'

    if stat == "ignored":
        return '<span class="activity-status-badge activity-status-ignored" title="Playback or event skipped">Ignored</span>'

    if stat == "queued":
        return '<span class="activity-status-badge activity-status-queued" title="Saved to offline retry queue">⏳ Queued</span>'

    if stat in ("error", "500", "502", "503", "504"):
        return '<span class="activity-status-badge activity-status-failed" title="Action failed">✕ Failed</span>'

    return f'<span class="activity-status-badge activity-status-unknown">{html.escape(str(result_status))}</span>'


def render_tracker_delivery_badges(delivery: dict[str, Any] | None) -> str:
    """Render compact, data-backed upstream tracker delivery states."""
    labels = {
        "trakt": "TRK", "simkl": "SKL", "anilist": "ANL", "mal": "MAL",
        "myanimelist": "MAL", "kitsu": "KTS", "tmdb": "TMDB",
        "letterboxd": "LBD", "serializd": "SER", "mdblist": "MDB",
    }
    states = {
        "success": ("✓", "Delivered", "success"),
        "queued": ("⌛", "Queued for retry", "queued"),
        "failed": ("×", "Delivery failed", "failed"),
        "skipped": ("—", "Not applicable to this event", "skipped"),
    }
    badges = []
    for tracker, status in (delivery or {}).items():
        key = str(tracker).lower()
        state = states.get(str(status).lower())
        if key not in labels or not state:
            continue
        icon, description, state_class = state
        title = html.escape(f"{labels[key]}: {description}", quote=True)
        badges.append(f'<span class="tracker-delivery-badge tracker-delivery-{state_class}" title="{title}" aria-label="{title}">{labels[key]} {icon}</span>')
    if not badges:
        return ""
    return f'<span class="tracker-delivery-badges" aria-label="Tracker delivery">{"".join(badges)}</span>'


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
            rows = f'<tr><td colspan="{col_span}" class="activity-empty">No scrobble events received yet. Start playing media on Plex, Jellyfin, or Emby to test!</td></tr>'
        else:
            for ev in ssr_events:
                u = ev["user"] if is_admin else mask_username_fn(ev["user"])
                server_raw = ev.get("server", "plex").lower()
                server_name = server_raw.title() if server_raw in {"jellyfin", "emby"} else "Plex"
                server_badge = f'<span class="activity-server-badge activity-server-{server_name.lower()}">{server_name}</span>'

                action_col = ""
                if is_admin:
                    show_title = ev.get("show_title") or (ev.get("title") if ev.get("type") == "show" else None)
                    if not show_title and ev.get("media_payload") and ev.get("media_payload", {}).get("media_type") == "show":
                        show_title = ev["media_payload"].get("title")
                    action_buttons = []
                    media_kind = "series" if str(ev.get("type", "")).lower() in {"show", "series", "episode"} else ("movie" if str(ev.get("type", "")).lower() == "movie" else "")
                    media_title = ev.get("show_title") or ev.get("title")
                    if media_kind and media_title:
                        kind_esc = urllib.parse.quote(media_kind)
                        title_esc = urllib.parse.quote(str(media_title))
                        action_buttons.append(f'<button data-kind="{kind_esc}" data-term="{title_esc}" onclick="openAddArrModal(decodeURIComponent(this.dataset.kind), decodeURIComponent(this.dataset.term))" class="btn-sm activity-row-button activity-row-button-arr" title="Search and add this title in Sonarr or Radarr">+ Add *Arr</button>')
                    if show_title:
                        show_esc = urllib.parse.quote(show_title)
                        if not is_cowatch_show_fn(show_title):
                            action_buttons.append(f'<button data-show="{show_esc}" onclick="quickAddShow(decodeURIComponent(this.dataset.show), this)" class="btn-sm activity-row-button activity-row-button-cowatch" title="Add show to co-watch whitelist">+ Co-Watch</button>')
                    if (cowatch_user or is_demo) and ev.get("media_payload"):
                        media_enc = urllib.parse.quote(json.dumps(ev["media_payload"]))
                        raw_act = str(ev.get("action", "")).lower().strip()
                        res_stat = str(ev.get("result_status", "")).lower().strip()
                        prog_val = str(ev.get("progress", "")).strip()
                        is_completion = raw_act.startswith(("mark_watched", "scrobble_stop", "collection", "rate")) or raw_act in ("scrobble", "watched")
                        if is_completion and res_stat != "ignored" and prog_val != "0.0%":
                            cw = ev.get("cowatch_status") or {}
                            if not cw.get("synced"):
                                action_buttons.append(f'<button onclick="quickSyncPartner(\'{media_enc}\', this)" class="btn-sm activity-row-button activity-row-button-partner" title="Manually push this watch event to partner account">+ Sync Partner</button>')
                            if raw_act.startswith(("mark_watched", "scrobble_stop")) or raw_act in ("scrobble", "watched"):
                                action_buttons.append(f'<button onclick="quickUnscrobble(\'{media_enc}\', this)" class="btn-sm activity-row-button activity-row-button-danger" title="Unscrobble / Remove from connected trackers">🗑️ Unscrobble</button>')
                    action_col = f'<td class="activity-actions-cell"><div class="activity-row-actions">{"".join(action_buttons)}</div></td>'

                status_badge_html = render_status_badge(
                    ev.get("action"),
                    ev.get("result_status"),
                    ev.get("progress", ""),
                    ev.get("cowatch_status"),
                )
                tracker_badges_html = render_tracker_delivery_badges(ev.get("tracker_delivery"))

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
                <tr class="activity-row">
                    <td class="activity-time">{time_disp}</td>
                    <td class="activity-title">{title_disp}</td>
                    <td><span class="activity-type">{type_disp}</span></td>
                    <td class="activity-user"><div class="activity-user-content">{server_badge}<span>{user_disp}</span></div></td>
                    <td><span class="activity-action">{action_text}</span></td>
                    <td><div class="activity-status-group">{status_badge_html}{tracker_badges_html}</div></td>
                    {action_col}
                </tr>
                """.strip() + "\n"

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
            <div class="u-margin-top-18px">
                <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-bottom-8px u-flex-wrap-wrap u-gap-8px">
                    <div class="info-label">Media Server Webhook Endpoints</div>
                    <div class="u-display-flex u-gap-6px">
                        <button type="button" onclick="switchWebhookTab('plex')" id="btn-tab-plex" class="btn-sm webhook-tab" aria-pressed="true">Plex</button>
                        <button type="button" onclick="switchWebhookTab('jellyfin')" id="btn-tab-jellyfin" class="btn-sm webhook-tab" aria-pressed="false">Jellyfin</button>
                        <button type="button" onclick="switchWebhookTab('emby')" id="btn-tab-emby" class="btn-sm webhook-tab" aria-pressed="false">Emby</button>
                    </div>
                </div>
                <div class="webhook-row">
                    <input type="text" readonly id="webhook-url-input" value="{full_webhook_url}"
                           data-plex="{full_webhook_url}" data-jellyfin="{full_jellyfin_url}" data-emby="{full_emby_url}"
                           class="webhook-url-input" />
                    <button onclick="copyWebhookUrl()" id="copy-btn" class="btn-copy">
                        📋 Copy URL
                    </button>
                </div>
                <div id="webhook-instructions" class="webhook-help">
                    Add in Plex: <strong>Settings &rarr; Webhooks &rarr; Add Webhook</strong> &bull; Jellyfin (<code>/webhook/jellyfin</code>) &bull; Emby (<code>/webhook/emby</code>) &bull; Sonarr (<code>/sonarr</code>) &bull; Radarr (<code>/radarr</code>).
                </div>
            </div>
            """
        else:
            return f"""
            <div class="u-margin-top-18px">
                <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-bottom-6px">
                    <div class="info-label">Media Server Webhook Endpoints</div>
                    <span class="webhook-masked-label">🔒 Secret Masked</span>
                </div>
                <div class="webhook-row">
                    <input type="text" readonly value="{masked_webhook_url}"
                           class="webhook-url-input webhook-url-masked" />
                    <button onclick="openUnlockModal()" class="btn-copy webhook-unlock-button">
                        🔓 Unlock
                    </button>
                </div>
                <div class="webhook-help">Admin authorization required to reveal webhook URLs. Supports Plex, Jellyfin, Emby, Sonarr, and Radarr.</div>
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
            card_hidden = ""
            playback_state = "playing" if s["state"] == "playing" else "paused"
            badge_text = "Currently Streaming" if s["state"] == "playing" else "Paused"
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
            card_hidden = ""
            playback_state = "finished"
            badge_text = "Recently Finished"
            user_dev = f"• {f['username']}" + (f" on {f['player']}" if (is_admin and f['player']) else "")
            stream_title = f['title']
            stream_url = f['trakt_url']
            stream_prog_text = "100.0% • Finished"
            stream_prog_width = "100%"
            poster_url = f.get("poster_url")
            backdrop_url = f.get("backdrop_url") or poster_url
        else:
            card_hidden = "hidden"
            playback_state = "playing"
            badge_text = "Currently Streaming"
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

        poster_img_src = html.escape(str(poster_url)) if poster_url else ""
        if backdrop_url:
            backdrop_style = f"background-image: url('{html.escape(str(backdrop_url))}');"
        else:
            backdrop_style = "background: radial-gradient(circle at top right, var(--accent-glow, rgba(56, 189, 248, 0.3)), transparent 60%);"

        return f"""
            <div id="active-playback-card" class="card playback-card playback-state-{playback_state}" {card_hidden}>
            <!-- Frosted Ambient Backdrop -->
            <div id="stream-ambient-backdrop" class="playback-ambient-backdrop" style="{backdrop_style}"></div>

            <!-- Content Area -->
            <div class="playback-content">
                <!-- Leading Poster Thumbnail -->
                <div id="stream-poster-container" class="playback-poster">
                    <img id="stream-poster-img" src="{poster_img_src}" alt="Poster" class="playback-poster-image" {'hidden' if not poster_url else ''} onerror="this.hidden=true; document.getElementById('stream-poster-fallback').hidden=false;" />
                    <div id="stream-poster-fallback" class="playback-poster-fallback" {'hidden' if poster_url else ''}>
                        🎬
                    </div>
                </div>

                <!-- Playback Details & Progress -->
                <div class="playback-details">
                    <div class="playback-heading-row">
                        <div>
                            <div class="playback-state-line">
                                <span id="stream-pulse-indicator" class="pulse-indicator"></span>
                                <span class="playback-state-label" id="stream-state-badge">{badge_text}</span>
                                <span class="playback-user-device" id="stream-user-device">{user_dev_esc}</span>
                            </div>
                            <h2 class="playback-title" id="stream-title">{stream_title_esc}</h2>
                        </div>
                        <div id="stream-actions">
                            <a id="stream-trakt-link" href="{clean_stream_url_esc}" target="_blank" rel="noopener noreferrer" class="btn-sm playback-link">View on Trakt ↗</a>
                        </div>
                    </div>
                    <div class="playback-progress">
                        <div class="playback-progress-labels">
                            <span>Playback Progress</span>
                            <span id="stream-progress-text" class="playback-progress-value">{stream_prog_text_esc}</span>
                        </div>
                        <div class="playback-progress-track">
                            <div id="stream-progress-bar" class="playback-progress-bar" style="width:{stream_prog_width};"></div>
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
            chips_html = f'<div class="cowatch-privacy-hint"><span>🔒</span><span><strong>{count} shared show{"s" if count != 1 else ""} configured</strong> &bull; Unlock admin access to view titles and manage whitelist.</span></div>'
        else:
            chips_html = ""
            for s in cw_shows:
                s_enc = urllib.parse.quote(s)
                del_btn = f'<button data-show="{s_enc}" onclick="removeCowatchShow(decodeURIComponent(this.dataset.show))" title="Remove {html.escape(s)}" class="cowatch-chip-del">&times;</button>'
                chips_html += f'<span class="cowatch-chip" data-title="{html.escape(s.lower())}">{html.escape(s)}{del_btn}</span>'
            if not chips_html:
                chips_html = '<span class="cowatch-empty">No shows added yet. Add shows below or directly from recent activity.</span>'

        # Allowed devices chips
        if not is_admin:
            device_chips_html = '<div class="cowatch-privacy-hint"><span>🔒</span><span>Unlock admin access to manage allowed devices.</span></div>'
            devices_count_badge = "🔒"
        else:
            devices_count_badge = str(len(cw_devices)) if cw_devices else "All"
            if not cw_devices:
                device_chips_html = '<span class="cowatch-empty">All devices allowed (no device filtering). Playback on any player triggers co-watch.</span>'
            else:
                device_chips_html = ""
                for d in cw_devices:
                    d_enc = urllib.parse.quote(d)
                    del_btn = f'<button data-device="{d_enc}" onclick="removeCowatchDevice(decodeURIComponent(this.dataset.device))" title="Remove {html.escape(d)}" class="cowatch-chip-del">&times;</button>'
                    device_chips_html += f'<span class="cowatch-device-chip" data-title="{html.escape(d.lower())}">📺 {html.escape(d)}{del_btn}</span>'

        device_form_html = f'''
        <form onsubmit="event.preventDefault();addCowatchDevice();" autocomplete="off" class="cowatch-device-form">
            <div class="cowatch-form-row">
                <input type="text" id="cowatch-device-input" name="cowatch_device" placeholder="Add device (e.g. Apple TV, Shield TV)..."
                       class="cowatch-form-input"
                       autocomplete="off" />
                <button type="submit" class="btn-sm cowatch-add-button">+ Add Device</button>
            </div>
        </form>
        <div class="cowatch-form-help">
            Leave empty to allow all devices. When configured, co-watching only dual-scrobbles on these players.
        </div>
        ''' if is_admin else '<div class="cowatch-admin-hint">Admin access required to configure allowed devices.</div>'

        # Multi-user accounts list
        users_badges_html = ""
        for u in configured_users:
            u_name = u["username"]
            is_def = u.get("is_default", False)
            is_cw = u.get("is_cowatch_target", False)
            auth = u.get("authenticated", False)
            status_class = "connected" if auth else "disconnected"
            status_text = "Connected" if auth else "Not Linked"
            link_url = f"/auth?user={u_name}" if not is_def else "/auth"

            link_btn = ""
            if is_admin:
                if not auth:
                    link_btn = f'<a href="{link_url}" class="btn-sm cowatch-link-button">Link &rarr;</a>'
                else:
                    link_btn = f'<a href="{link_url}" class="btn-sm cowatch-reconnect-button">Reconnect</a>'

            role_label = ""
            if is_def:
                role_label = '<span class="cowatch-role-badge cowatch-role-default">Default</span>'
            elif is_cw:
                role_label = '<span class="cowatch-role-badge cowatch-role-partner">Partner</span>'

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
                    <span class="cowatch-account-connection is-{status_class}">● {status_text}</span>
                    {link_btn}
                </div>
            </div>
            """

        rule_movies_str = "Enabled" if cowatch_movies else "Disabled"
        sonarr_status_note = (
            '<span class="cowatch-sonarr-status is-connected">'
            '✓ Connected to Sonarr (type to search library)</span>'
            if sonarr_configured
            else '<span class="cowatch-sonarr-status">Configure SONARR_URL & SONARR_API_KEY in .env for library search</span>'
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
                badge_class = "connected" if auth else "disconnected"
                status_txt = "Connected" if auth else "Not Linked"

                act_btn = ""
                if is_admin:
                    if trk_key == "trakt":
                        link_href = f"/auth?user={cw_user}"
                        act_btn = f'<a href="{link_href}" class="btn-sm cowatch-tracker-action">{"Reconnect" if auth else "Link &rarr;"}</a>'
                    elif trk_key == "simkl":
                        act_btn = f'<button onclick="openSimklModal(\'{cw_user}\')" class="btn-sm cowatch-tracker-action tracker-simkl">{"PIN Reconnect" if auth else "Link PIN"}</button>'
                    elif trk_key == "anilist":
                        act_btn = f'<button onclick="openAnilistModal(\'{cw_user}\')" class="btn-sm cowatch-tracker-action tracker-anilist">{"Token" if auth else "Link &rarr;"}</button>'
                    elif trk_key == "mal":
                        act_btn = f'<button onclick="openMalModal(\'{cw_user}\')" class="btn-sm cowatch-tracker-action tracker-mal">{"Token" if auth else "Link &rarr;"}</button>'

                tracker_badges.append(f"""
                <div class="cowatch-tracker-row">
                    <div class="cowatch-tracker-info">
                        <span class="cowatch-tracker-dot is-{badge_class}"></span>
                        <strong>{trk_name}</strong>
                        <span>({status_txt})</span>
                    </div>
                    {act_btn}
                </div>
                """)

            partner_trackers_html = f"""
            <div class="cowatch-partner-trackers">
                <div class="cowatch-section-title">
                    <span>Partner Cloud Tracker Credentials</span>
                    <span class="cowatch-partner-name">(@{cw_user_display})</span>
                </div>
                <div class="cowatch-tracker-grid">
                    {''.join(tracker_badges)}
                </div>
            </div>
            """

        # Section 3: Household Multi-Tenant Routing Rules (3+ Profiles)
        rules = household_rules or []
        rules_badge = str(len(rules))
        if not rules:
            rules_html = '<div class="household-rules-empty">No custom household routing rules configured. Secondary scrobbles follow the default partner settings above.</div>'
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

                status_txt = "Active" if renabled else "Paused"
                status_class = "active" if renabled else "paused"

                actions_html = ""
                if is_admin:
                    rid_esc = urllib.parse.quote(rid)
                    actions_html = f'''
                    <div class="household-rule-actions">
                        <button onclick="toggleHouseholdRule(decodeURIComponent('{rid_esc}'))" class="btn-sm household-rule-toggle">
                            {"Pause" if renabled else "Activate"}
                        </button>
                        <button onclick="deleteHouseholdRule(decodeURIComponent('{rid_esc}'))" class="btn-sm household-rule-delete">
                            &times; Delete
                        </button>
                    </div>
                    '''

                rule_items.append(f'''
                <div class="household-rule-card">
                    <div class="household-rule-heading">
                        <div class="household-rule-title-group">
                            <strong>{html.escape(rname)}</strong>
                            <span class="household-rule-status is-{status_class}">{status_txt}</span>
                        </div>
                        {actions_html}
                    </div>
                    <div class="household-rule-details">
                        <div><span>Targets:</span> <strong>{html.escape(targets_str)}</strong></div>
                        <div><span>Players:</span> <strong>📺 {html.escape(devices_str)}</strong></div>
                        <div><span>Media:</span> <strong>🎬 {html.escape(media_str)}</strong></div>
                        <div><span>Shows:</span> <strong>📺 {html.escape(shows_str)}</strong></div>
                    </div>
                </div>
                ''')
            rules_html = "".join(rule_items)

        household_section_html = f'''
        <!-- Household Multi-Tenant Routing Rules (3+ Profiles) -->
        <div class="household-section">
            <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-bottom-10px u-flex-wrap-wrap u-gap-8px">
                <div class="cowatch-section-title">
                    <span>🏡 Household Multi-Tenant Routing Rules</span>
                    <span id="household-rules-count-badge" class="cowatch-count-badge">{rules_badge}</span>
                </div>
                {f'<button onclick="openHouseholdRuleModal()" class="btn-sm cowatch-add-button">+ New Routing Rule</button>' if is_admin else ''}
            </div>
            <p class="household-section-description">
                Route scrobbles to specific family members or kids profiles based on player devices (e.g. Living Room TV vs Bedroom TV) and media types.
            </p>
            <div id="household-rules-container">
                {rules_html}
            </div>
        </div>
        '''

        return f"""
        <div class="card" id="card-cowatch">
            <div class="cowatch-card-heading">
                <h3 class="cowatch-card-title">
                    <span>👥</span> Watch Together & Household Multi-Tenancy
                </h3>
                <span class="cowatch-card-mode">
                    {f"Partner: @{cw_user_display}" if cw_user else "Multi-Profile Routing"}
                </span>
            </div>
            <p class="cowatch-card-description">
                Dual-scrobble watched shows to your partner's Trakt account and route household playback across arbitrary user profiles.
            </p>
            <!-- Top Section: Targeting & Destinations (Accounts & Devices side-by-side) -->
            <div class="cowatch-grid">
                <div>
                    <div class="cowatch-subsection-heading">
                        <div class="cowatch-section-title">Linked Trakt Accounts</div>
                        {f'<button onclick="promptLinkAccount()" class="btn-sm cowatch-link-account">+ Link Account</button>' if is_admin else ''}
                    </div>
                    <div>
                        {users_badges_html}
                    </div>
                </div>
                <div>
                    <div class="cowatch-subsection-heading">
                        <div class="cowatch-section-title">
                            <span>Allowed Devices Whitelist</span>
                            <span id="cowatch-devices-count-badge" class="cowatch-count-badge">{devices_count_badge}</span>
                        </div>
                    </div>
                    <div id="cowatch-devices-chips-container" class="custom-scroll cowatch-chip-container cowatch-devices-container">
                        {device_chips_html}
                    </div>
                    {device_form_html}
                </div>
            </div>
            {partner_trackers_html}

            <!-- Bottom Section: Shared Media & Shows Whitelist (Full Width) -->
            <div class="cowatch-shows-section">
                <div class="cowatch-subsection-heading">
                    <div class="cowatch-section-title">
                        <span>Shared Shows Whitelist</span>
                        <span id="cowatch-count-badge" class="cowatch-count-badge">{len(cw_shows)}</span>
                    </div>
                    {f'<input type="text" id="cowatch-filter-input" class="cowatch-filter-input" placeholder="Filter list..." oninput="filterCowatchChips(this.value)" />' if is_admin else ''}
                </div>
                <div id="cowatch-chips-container" class="custom-scroll cowatch-chip-container cowatch-shows-container">
                    {chips_html}
                </div>
                {f'''
                <form onsubmit="event.preventDefault();addCowatchShow();" autocomplete="off" class="cowatch-show-form">
                    <div class="cowatch-form-row">
                        <div class="cowatch-suggestion-wrap">
                            <input type="search" id="cowatch-show-input" name="cowatch_show_search" placeholder="Add show (e.g. Severance, Lanterns)..."
                                    class="cowatch-form-input cowatch-show-input"
                                    oninput="onCowatchShowInput(this.value)"
                                    onfocus="onCowatchShowInput(this.value)"
                                    autocomplete="off"
                                    data-lpignore="true"
                                    data-1p-ignore="true"
                                    onkeydown="if(event.key==='Enter')addCowatchShow()" />
                            <div id="sonarr-suggestions" class="cowatch-suggestions"></div>
                        </div>
                        <button type="submit" class="btn-sm cowatch-add-button">+ Add Show</button>
                    </div>
                </form>
                <div class="u-margin-top-4px">{sonarr_status_note}</div>
                ''' if is_admin else '<div class="u-font-size-12px u-color-text-muted">Admin access required to add or remove shared shows.</div>'}
                <div class="cowatch-movies-controls">
                    <span>Movies: <strong id="cowatch-movies-status">{rule_movies_str}</strong></span>
                    {f'<button id="cowatch-movies-btn" onclick="toggleCowatchMovies()" class="btn-sm cowatch-movies-button">Toggle Movies ({ "Disable" if cowatch_movies else "Enable" })</button>' if is_admin else ''}
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
            st = "Online" if plex_conn else "Unreachable"
            server_status_badges.append(f'<span class="reconcile-server-status is-{"online" if plex_conn else "offline"}">● Plex {st}</span>')
        if jf_cfg:
            st = "Online" if jf_conn else "Unreachable"
            server_status_badges.append(f'<span class="reconcile-server-status is-{"online" if jf_conn else "offline"}">● Jellyfin {st}</span>')
        if emby_cfg:
            st = "Online" if emby_conn else "Unreachable"
            server_status_badges.append(f'<span class="reconcile-server-status is-{"online" if emby_conn else "offline"}">● Emby {st}</span>')

        if not server_status_badges:
            server_status_badges.append('<span class="reconcile-server-status is-unconfigured">● Direct API Not Configured</span>')
        server_badges_html = " ".join(server_status_badges)

        diff_count = sync_status.get("diff_count", 0)
        int_mins = sync_status.get("interval_minutes", 0)
        auto_sync_badge = f'<span class="reconcile-schedule-badge">Periodic: Every {int_mins}m</span>' if int_mins > 0 else '<span class="reconcile-schedule-badge is-manual">Periodic: Manual</span>'

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

        bg_status_class = "success" if bg_status_txt == "success" else ("partial" if bg_status_txt == "partial_error" else "idle")

        cloud_sync_panel_html = f"""
        <div class="cloud-sync-panel">
            <div class="cloud-sync-details">
                <span>Automated Cloud Sync: <strong>Every {bg_interval_hours}h</strong></span>
                <span>Last Run: <strong>{bg_run_display}</strong> (<span class="cloud-sync-status is-{bg_status_class}">{bg_status_txt}</span>)</span>
                <span>Next: <strong class="cloud-sync-next">{bg_next_display}</strong></span>
                <span>Export: <strong class="cloud-sync-export">Letterboxd CSV</strong></span>
            </div>
            <div>
                {f'<button onclick="triggerBackgroundCloudSync(this)" class="btn-sm cloud-sync-action">⚡ Run Cloud Sync Now</button>' if is_admin else ''}
            </div>
        </div>
        """

        any_server_configured = plex_cfg or jf_cfg or emby_cfg
        if any_server_configured:
            return f"""
            <div class="card" id="card-reconciliation">
                <div class="reconcile-heading">
                    <h3 class="reconcile-title">
                        <span>🔄</span> Two-Way Library Reconciliation & Reverse Sync
                    </h3>
                    <div class="reconcile-status-group">
                        {server_badges_html}
                        {auto_sync_badge}
                    </div>
                </div>
                <p class="reconcile-description">
                    Bi-directional sync matches watched history and ratings between your media servers (Plex, Jellyfin, Emby) and Trakt with automatic echo-loop suppression.
                </p>
                <div class="reconcile-summary-panel">
                    <div>
                        <div class="reconcile-summary-title">
                            <span>Pending Discrepancies</span>
                            <span id="reconcile-diff-badge" class="cowatch-count-badge">{diff_count}</span>
                        </div>
                        <div class="reconcile-summary-note">
                            Ratings sync: {'Enabled' if sync_status.get('sync_ratings') else 'Disabled'} &bull; Startup sync: {'Active' if sync_status.get('sync_on_startup') else 'Off'}
                        </div>
                    </div>
                    <div class="reconcile-actions">
                        {f'<button onclick="openReconcileSettingsModal()" class="btn-sm reconcile-button reconcile-button-secondary">⚙️ Configure</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm reconcile-button" >🔒 Configure</button>'}
                        {f'<button onclick="openReconcileModal(true)" class="btn-sm reconcile-button reconcile-button-primary">🔍 Review Discrepancies</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm reconcile-button">🔒 Review Discrepancies</button>'}
                        {f'<button onclick="quickReconcileTraktToPlex(this)" class="btn-sm reconcile-button reconcile-button-sync">⚡ Quick Sync (Trakt &rarr; {active_srv.capitalize()})</button>' if is_admin else ''}
                    </div>
                </div>
                {cloud_sync_panel_html}
            </div>
            """
        else:
            return f"""
            <div class="card" id="card-reconciliation">
                <div class="reconcile-heading">
                    <h3 class="reconcile-title">
                        <span>🔄</span> Two-Way Library Reconciliation
                    </h3>
                    <span class="reconcile-server-status is-unconfigured">● Direct API Not Configured</span>
                </div>
                <p class="reconcile-description reconcile-description-compact">
                    Enable direct media server reconciliation (Plex, Jellyfin, Emby) to pull watched history and user ratings from Trakt back to your media server with loop prevention.
                </p>
                <div class="reconcile-summary-panel reconcile-summary-panel-compact">
                    <span>Configure your media server direct connection to activate two-way reconciliation and rating synchronization.</span>
                    <div class="reconcile-actions">
                        {f'<button onclick="openReconcileSettingsModal()" class="btn-sm reconcile-button reconcile-button-primary">⚙️ Set Up Connection</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm reconcile-button">🔒 Set Up Connection</button>'}
                        <a href="{repo_url}#readme" target="_blank" rel="noopener" class="btn-sm reconcile-button reconcile-button-link">View Guide &rarr;</a>
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
            <div class="reconcile-heading">
                <h3 class="reconcile-title">
                    <span>💾</span> System Operations & Observability
                </h3>
                <div class="reconcile-actions">
                    {f'<button onclick="openLogsModal()" class="btn-sm diagnostics-action-button is-admin">📜 View Logs</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm diagnostics-action-button" title="Admin unlock required to view logs">🔒 View Logs</button>'}
                    <a href="/metrics" target="_blank" rel="noopener" class="btn-sm diagnostics-action-link">📊 Prometheus /metrics ↗</a>
                </div>
            </div>
            <p class="reconcile-description">
                Export or restore your configuration, multi-user Trakt tokens, co-watch whitelist, and inspect live service logs.
            </p>
            <div class="backup-actions">
                {f'''
                <a href="/api/backup" download class="btn-sm backup-action-button backup-download-button">
                    💾 Download Backup (.zip)
                </a>
                <label class="btn-sm backup-action-button backup-restore-button">
                    📤 Restore Backup (.zip)
                    <input type="file" id="backup-file-input" accept=".zip" onchange="uploadBackup(this)" hidden />
                </label>
                <button onclick="openTestWebhookModal()" class="btn-sm backup-action-button backup-test-button">
                    🧪 Test Webhook
                </button>
                ''' if is_admin else '<div class="backup-admin-hint">Admin authorization required to download or restore server backups.</div>'}
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
            status_class = "disabled" if is_disabled else st if st in {"connected", "available", "error"} else "unknown"

            toggle_btn = ""
            if is_admin and sid in ("plex", "jellyfin", "emby"):
                cat = "server"
                key = sid
                is_en = srv.get("enabled", True)
                config_gear = f'<button onclick="openReconcileSettingsModal(\'{sid}\')" class="btn-sm eco-config-button" title="Configure {srv_name} Direct API">⚙️</button>'
                if is_en:
                    toggle_btn = f'{config_gear} <button onclick="toggleSetting(\'{cat}\', \'{key}\', false, this)" class="btn-sm eco-control-button" title="Disable {srv_name}"><span>⏸</span><span>Disable</span></button>'
                else:
                    toggle_btn = f'{config_gear} <button onclick="toggleSetting(\'{cat}\', \'{key}\', true, this)" class="btn-sm eco-control-button eco-control-enable" title="Enable {srv_name}"><span>▶</span><span>Enable</span></button>'

            eco_cards_html += f"""
            <div class="{card_class}">
                <div class="eco-card-heading">
                    <div class="eco-card-title-group">
                        <span class="eco-card-icon">{srv_icon}</span>
                        <div class="eco-card-name-group">
                            <div class="eco-card-name">{srv_name}</div>
                            <div class="eco-card-category">{html.escape(srv.get('category', ''))}</div>
                        </div>
                    </div>
                    <span class="eco-status-badge is-{status_class}">
                        {html.escape(srv.get('badge', st.capitalize()))}
                    </span>
                </div>
                <div class="eco-card-footer">
                    <div class="eco-card-details" title="{html.escape(srv.get('details', ''))}">
                        {html.escape(srv.get('details', ''))}
                    </div>
                    {toggle_btn}
                </div>
            </div>
            """

        return f"""
        <div class="card" id="card-ecosystem">
            <div class="reconcile-heading">
                <h3 class="reconcile-title">
                    <span>🌐</span> Multi-Server Ecosystem
                </h3>
                <div class="eco-header-actions">
                    <span class="eco-healthy-badge">
                        <span class="eco-healthy-indicator"></span>
                        {eco_healthy}/{eco_total} Services Healthy
                    </span>
                    <button onclick="openSettingsModal('servers')" class="btn-sm eco-manage-button">⚙️ Manage Servers</button>
                </div>
            </div>
            <p class="reconcile-description eco-description">
                Unified operational topology across all media servers and automated acquisition engines.
            </p>
            <div class="eco-card-grid">
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

        quick_scrobble_btn = '<button onclick="openManualScrobbleModal()" class="btn-sm u-background-accent-color u-color-fff u-font-weight-600 u-padding-6px-12px u-font-size-12px u-cursor-pointer u-display-inline-flex u-align-items-center u-gap-4px u-white-space-nowrap">🍿 Quick Scrobble</button>' if is_admin else '<button onclick="openUnlockModal()" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-text-muted u-padding-6px-12px u-font-size-12px u-cursor-pointer u-white-space-nowrap">🔒 Quick Scrobble</button>'

        cross_sync_btn = ""
        if simkl_auth and (is_demo or trakt_authenticated):
            if is_admin:
                cross_sync_btn = '<button onclick="openCrossSyncModal(true)" class="btn-sm u-background-accent-color u-color-fff u-font-weight-600 u-padding-6px-12px u-font-size-12px u-cursor-pointer u-display-inline-flex u-align-items-center u-gap-4px u-white-space-nowrap">🔄 Reconcile Trakt & Simkl</button>'
            else:
                cross_sync_btn = '<button onclick="openUnlockModal()" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-text-muted u-padding-6px-12px u-font-size-12px u-cursor-pointer u-white-space-nowrap">🔒 Reconcile</button>'

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
                t_badge = '<span class="u-background-bg-surface u-border-1px-solid-475569 u-color-text-muted u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">⏸ Paused</span>'
            elif t_auth:
                active_trackers_count += 1
                u_suffix = f" (@{t_disp_user})" if t_disp_user else ""
                t_badge = f'<span class="u-background-064e3b u-border-1px-solid-059669 u-color-a7f3d0 u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">● Active{u_suffix}</span>'
            elif t_cfg:
                active_trackers_count += 1
                t_badge = '<span class="u-background-bg-surface u-border-1px-solid-eab308 u-color-fde047 u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">● Ready</span>'
            else:
                t_badge = '<span class="u-background-bg-surface u-border-1px-solid-334155 u-color-text-muted u-padding-2px-8px u-border-radius-4px u-font-size-11px">● Optional</span>'

            tm_id = tm["id"]
            tm_name = tm["name"]
            cfg_btn = f'<button onclick="openSettingsModal(\'trackers\', \'{tm_id}\')" class="btn-sm tracker-config-button tracker-config-{tm_id} u-background-bg-surface u-border-1px-solid-475569 u-padding-2px-7px u-font-size-11px u-border-radius-4px u-cursor-pointer" title="{tm_name} Settings">⚙️</button>'

            hub_items_html += f"""
            <div class="hub-tracker-item u-display-flex u-align-items-center u-justify-content-space-between u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-10px-14px u-gap-8px" data-cat="{tm['cat']}">
                <div class="u-display-flex u-align-items-center u-gap-10px u-min-width-0">
                    <span class="u-font-size-18px u-flex-shrink-0">{tm['icon']}</span>
                    <div class="u-min-width-0">
                        <div class="u-font-size-13px u-font-weight-600 u-color-text-main u-white-space-nowrap u-overflow-hidden u-text-overflow-ellipsis">{tm['name']}</div>
                        <div class="u-font-size-11px u-color-text-muted">{tm['desc']}</div>
                    </div>
                </div>
                <div class="u-display-flex u-align-items-center u-gap-6px u-flex-shrink-0">
                    {t_badge}
                    {cfg_btn}
                </div>
            </div>
            """

        return f"""
        <div class="card" id="card-multi-tracker">
            <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-bottom-12px u-flex-wrap-wrap u-gap-8px">
                <h3 class="u-margin-0 u-display-flex u-align-items-center u-gap-8px">
                    <span>🌐</span> Multi-Tracker Hub &bull; Cloud Synchronization
                </h3>
                <div class="u-display-flex u-align-items-center u-gap-8px">
                    <span class="u-background-bg-page u-border-1px-solid-334155 u-color-10b981 u-padding-4px-10px u-border-radius-6px u-font-size-12px u-font-weight-600 u-display-inline-flex u-align-items-center u-gap-6px">
                        <span class="u-width-7px u-height-7px u-border-radius-50 u-background-10b981 u-display-inline-block"></span>
                        {active_trackers_count}/9 Trackers Active
                    </span>
                </div>
            </div>
            <p class="u-color-text-muted u-font-size-13px u-margin-bottom-14px u-line-height-1-5">
                Broadcast playback scrobbles, ratings, and diary entries across universal trackers, dedicated anime services, social diaries, and curated lists in real time.
            </p>
            <div class="u-display-flex u-gap-6px u-margin-bottom-14px u-flex-wrap-wrap">
                <button class="btn-sm hub-cat-tab u-background-accent-color u-border-1px-solid-0284c7 u-color-fff u-font-weight-600 u-padding-5px-12px u-font-size-12px u-border-radius-6px u-cursor-pointer" onclick="filterHubTrackers('all', this)">All Trackers (9)</button>
                <button class="btn-sm hub-cat-tab u-background-bg-surface u-border-1px-solid-334155 u-color-text-heading u-padding-5px-12px u-font-size-12px u-border-radius-6px u-cursor-pointer" onclick="filterHubTrackers('universal', this)">Universal (3)</button>
                <button class="btn-sm hub-cat-tab u-background-bg-surface u-border-1px-solid-334155 u-color-text-heading u-padding-5px-12px u-font-size-12px u-border-radius-6px u-cursor-pointer" onclick="filterHubTrackers('anime', this)">Anime (3)</button>
                <button class="btn-sm hub-cat-tab u-background-bg-surface u-border-1px-solid-334155 u-color-text-heading u-padding-5px-12px u-font-size-12px u-border-radius-6px u-cursor-pointer" onclick="filterHubTrackers('social_diary', this)">Social Diaries (2)</button>
                <button class="btn-sm hub-cat-tab u-background-bg-surface u-border-1px-solid-334155 u-color-text-heading u-padding-5px-12px u-font-size-12px u-border-radius-6px u-cursor-pointer" onclick="filterHubTrackers('lists_ratings', this)">Lists &amp; Ratings (1)</button>
            </div>
            <div id="hub-trackers-grid" class="u-display-grid u-grid-template-columns-repeat-auto-fit-minmax-260px-1fr u-gap-10px u-margin-bottom-14px">
                {hub_items_html}
            </div>
            <div class="u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-12px-14px u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-12px">
                <div class="u-font-size-12px u-color-text-heading u-display-flex u-align-items-center u-gap-10px u-flex-wrap-wrap">
                    <span>Active Trackers: <strong>{active_trackers_count}/9 Connected</strong></span>
                    <span class="u-color-text-muted">&bull;</span>
                    <span>Anime Tracking Engine: <strong>{"Auto-Detect Active" if Config.ANIME_AUTO_DETECT else "Explicit Only"}</strong></span>
                    <span class="u-color-text-muted">&bull;</span>
                    <span>Cross-Tracker Sync: <strong>{"Ready" if simkl_auth and (is_demo or trakt_authenticated) else "Requires Trakt + Simkl Auth"}</strong></span>
                </div>
                <div class="u-display-flex u-gap-8px u-align-items-center u-flex-wrap-wrap">
                    {quick_scrobble_btn}
                    {cross_sync_btn}
                    <button onclick="openSettingsModal('trackers')" class="btn-sm u-background-accent-color u-color-fff u-font-weight-600 u-padding-6px-14px u-font-size-12px u-display-inline-flex u-align-items-center u-gap-5px u-white-space-nowrap u-cursor-pointer">⚙️ Configure Trackers</button>
                    <a href="/api/letterboxd/export" download="letterboxd_diary.csv" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-34d399 u-text-decoration-none u-padding-6px-12px u-font-size-12px u-display-inline-flex u-align-items-center u-gap-4px u-white-space-nowrap" title="Export Letterboxd Watch Diary as CSV">📥 Letterboxd CSV</a>
                    <div class="u-display-inline-flex u-gap-4px u-align-items-center u-flex-wrap-wrap">
                        <a href="/auth" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-f87171 u-text-decoration-none u-padding-6px-10px u-font-size-11px u-display-inline-flex u-align-items-center u-gap-3px u-white-space-nowrap" title="Trakt Auth Portal">Trakt ↗</a>
                        <button onclick="openSettingsModal('trackers','simkl')" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-accent-color u-padding-6px-10px u-font-size-11px u-display-inline-flex u-align-items-center u-gap-3px u-white-space-nowrap u-cursor-pointer" title="Configure Simkl">Simkl PIN</button>
                        <button onclick="openSettingsModal('trackers','anilist')" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-60a5fa u-padding-6px-10px u-font-size-11px u-display-inline-flex u-align-items-center u-gap-3px u-white-space-nowrap u-cursor-pointer" title="Configure AniList">AniList ↗</button>
                        <button onclick="openSettingsModal('trackers','mal')" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-818cf8 u-padding-6px-10px u-font-size-11px u-display-inline-flex u-align-items-center u-gap-3px u-white-space-nowrap u-cursor-pointer" title="Configure MyAnimeList">MAL ↗</button>
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
            auto_badge = f'<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-muted u-padding-2px-8px u-border-radius-4px u-font-size-11px">Polling: Every {auto_int}m</span>' if auto_int > 0 else '<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-muted u-padding-2px-8px u-border-radius-4px u-font-size-11px">Polling: Manual</span>'

            sonarr_desc = "Online" if sonarr_conn else "Unreachable"
            radarr_desc = "Online" if radarr_conn else "Unreachable"
            overseerr_desc = "Online" if overseerr_conn else "Unreachable"

            overseerr_pill = (
                f'<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-main u-padding-3px-9px u-border-radius-6px u-font-size-12px u-display-inline-flex u-align-items-center u-gap-6px"><span class="u-color-a855f7">✨ {overseerr_app}</span><span class="u-color-10b981 u-font-weight-600">{overseerr_desc}</span></span>'
                if overseerr_cfg
                else '<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-muted u-padding-3px-9px u-border-radius-6px u-font-size-12px">✨ Overseerr: Off</span>'
            )

            sonarr_pill = (
                f'<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-main u-padding-3px-9px u-border-radius-6px u-font-size-12px u-display-inline-flex u-align-items-center u-gap-6px"><span class="u-color-accent-color">📺 Sonarr</span><span class="u-color-10b981 u-font-weight-600">{sonarr_desc}</span></span>'
                if sonarr_cfg
                else '<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-muted u-padding-3px-9px u-border-radius-6px u-font-size-12px">📺 Sonarr: Off</span>'
            )

            radarr_pill = (
                f'<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-main u-padding-3px-9px u-border-radius-6px u-font-size-12px u-display-inline-flex u-align-items-center u-gap-6px"><span class="u-color-f59e0b">🍿 Radarr</span><span class="u-color-10b981 u-font-weight-600">{radarr_desc}</span></span>'
                if radarr_cfg
                else '<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-muted u-padding-3px-9px u-border-radius-6px u-font-size-12px">🍿 Radarr: Off</span>'
            )

            sync_btn_html = (
                '<button onclick="triggerArrWatchlistSync(this)" class="btn-sm u-background-10b981 u-color-fff u-font-weight-600 u-display-inline-flex u-align-items-center u-gap-6px u-padding-8px-14px">⚡ Sync Watchlist Now</button>'
                if is_admin
                else '<button onclick="openUnlockModal()" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-text-muted u-display-inline-flex u-align-items-center u-gap-6px u-padding-8px-14px">🔒 Sync Watchlist</button>'
            )
            add_media_btn_html = (
                '<button onclick="openAddArrModal()" class="btn-sm u-background-0369a1 u-color-fff u-font-weight-600 u-display-inline-flex u-align-items-center u-gap-6px u-padding-8px-14px">➕ Add Media</button>'
                if is_admin else '<button onclick="openUnlockModal()" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-text-muted u-padding-8px-14px">🔒 Add Media</button>'
            )

            return f"""
            <div class="card" id="card-arr-bridge">
                <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-bottom-12px u-flex-wrap-wrap u-gap-8px">
                    <h3 class="u-margin-0 u-display-flex u-align-items-center u-gap-8px">
                        <span>🎬</span> Content Bridge & *Arr Watchlist Automation
                    </h3>
                    <div class="u-display-flex u-align-items-center u-gap-8px">
                        {auto_badge}
                    </div>
                </div>
                <p class="u-color-text-muted u-font-size-13px u-margin-bottom-16px u-line-height-1-5">
                    Automatically monitors your Trakt Watchlist, checks library duplicates, and acquires new movies and shows into Overseerr, Radarr, and Sonarr with automatic search and notification dispatch.
                </p>
                <div class="u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-14px u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-12px">
                    <div class="u-display-flex u-align-items-center u-gap-10px u-flex-wrap-wrap">
                        {overseerr_pill}
                        {sonarr_pill}
                        {radarr_pill}
                        <span class="u-font-size-12px u-color-text-muted">Search on add: <strong>{'Enabled' if arr_status.get('search_on_add') else 'Disabled'}</strong> &bull; Alerts: <strong>{'On' if Config.ARR_NOTIFY_ON_ADD else 'Off'}</strong></span>
                    </div>
                    <div class="u-display-flex u-gap-8px u-flex-wrap-wrap">
                        {add_media_btn_html}
                        {sync_btn_html}
                        <button onclick="openArrModal()" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-accent-color u-padding-8px-14px u-display-inline-flex u-align-items-center u-gap-6px">📋 View Log</button>
                        <button onclick="openSettingsModal('automation')" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-accent-color u-padding-8px-14px u-display-inline-flex u-align-items-center u-gap-6px u-cursor-pointer">⚙️ Configure</button>
                    </div>
                </div>
            </div>
            """
        else:
            return f"""
            <div class="card" id="card-arr-bridge">
                <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-bottom-12px u-flex-wrap-wrap u-gap-8px">
                    <h3 class="u-margin-0 u-display-flex u-align-items-center u-gap-8px">
                        <span>🎬</span> Content Bridge & *Arr Automation
                    </h3>
                    <span class="u-color-text-muted u-font-size-12px">● Not Configured</span>
                </div>
                <p class="u-color-text-muted u-font-size-13px u-margin-bottom-12px u-line-height-1-5">
                    Connect Trakt Watchlists directly to Sonarr and Radarr. When you add movies or shows to your Trakt Watchlist, Omniscrobble automatically looks them up and queues them for acquisition.
                </p>
                <div class="u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-12px-14px u-font-size-13px u-color-text-heading u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-8px">
                    <span>Configure Sonarr and Radarr connections directly in the Settings Hub or via <code>.env</code>.</span>
                    <div class="u-display-flex u-gap-6px u-align-items-center">
                        <button onclick="openSettingsModal('automation')" class="btn-sm u-background-accent-color u-color-fff u-font-weight-600 u-padding-6px-12px u-border-none u-cursor-pointer">⚙️ Setup *Arr Bridge</button>
                        <a href="{repo_url}#readme" target="_blank" rel="noopener" class="btn-sm u-background-bg-surface u-border-1px-solid-334155 u-color-accent-color u-text-decoration-none">View Guide &rarr;</a>
                    </div>
                </div>
            </div>
            """

    @staticmethod
    def render_analytics_card(analytics: dict[str, Any], is_admin: bool) -> str:
        """Render the Personal Analytics, Viewing Habits & OmniWrapped card."""
        watch_time = analytics.get("total_watch_formatted", "0h 0m")
        total_scrobbles = analytics.get("total_scrobbles", 0)
        unique_titles = analytics.get("unique_titles", 0)
        movies = analytics.get("movies_watched", 0)
        episodes = analytics.get("episodes_watched", 0)
        ratings = analytics.get("ratings_submitted", 0)

        solo_hours = analytics.get("solo_hours", 0.0)
        cowatch_hours = analytics.get("cowatch_hours", 0.0)
        cw_pct = analytics.get("cowatch_ratio_percent", 0)
        solo_pct = 100 - cw_pct if (solo_hours + cowatch_hours > 0) else 100

        server_dist = analytics.get("server_distribution", {"Plex": 100})
        server_badges = "".join(
            f'<span class="u-background-bg-surface u-border-1px-solid-334155 u-color-text-heading u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600 u-margin-right-6px">{s}: <strong class="u-color-accent-color">{pct}%</strong></span>'
            for s, pct in server_dist.items()
        )

        top_shows = analytics.get("top_shows", [])
        top_show_label = f"📺 {top_shows[0]['show']} ({top_shows[0]['episodes']} eps)" if top_shows else "📺 No series logged yet"

        top_genres = analytics.get("top_genres", [])
        genre_tags = "".join(
            f'<span class="u-background-bg-page u-border-1px-solid-334155 u-color-text-muted u-padding-2px-7px u-border-radius-4px u-font-size-10px u-margin-right-4px">{g["genre"]}</span>'
            for g in top_genres[:4]
        )

        admin_debugger_btn = ""
        if is_admin:
            admin_debugger_btn = """
            <button onclick="openWebhookDebuggerModal()" class="btn-sm u-background-bg-surface u-border-1px-solid-475569 u-color-text-heading u-padding-5px-10px u-font-size-11px u-font-weight-600 u-cursor-pointer u-display-inline-flex u-align-items-center u-gap-5px">
                <span>🔍</span><span>Webhook Inspector</span>
            </button>
            """

        if unique_titles > 0 and unique_titles != total_scrobbles:
            scrobbles_sub = f"{movies} movies &bull; {episodes} eps ({unique_titles} unique)"
        else:
            scrobbles_sub = f"{movies} movies &bull; {episodes} eps"

        return f"""
        <div class="card u-margin-bottom-20px" id="card-analytics">
            <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-bottom-12px u-flex-wrap-wrap u-gap-8px">
                <div class="u-display-flex u-align-items-center u-gap-8px">
                    <h3 class="u-margin-0 u-font-size-15px u-color-text-main u-display-flex u-align-items-center u-gap-8px">
                        <span>📊</span> Personal Analytics & Viewing Habits
                    </h3>
                </div>
                <div class="u-display-flex u-gap-6px u-align-items-center u-flex-wrap-wrap">
                    {admin_debugger_btn}
                    <button onclick="openOmniWrappedModal()" class="btn-sm u-background-linear-gradient-135deg-8b5cf6-3b82f6 u-color-fff u-border-none u-padding-5px-12px u-font-size-11px u-font-weight-600 u-cursor-pointer u-display-inline-flex u-align-items-center u-gap-5px u-box-shadow-0-2px-4px-rgba-139-92-246-0-3">
                        <span>✨</span><span>OmniWrapped</span>
                    </button>
                </div>
            </div>

            <!-- Quick Metrics Grid -->
            <div class="u-display-grid u-grid-template-columns-repeat-auto-fit-minmax-130px-1fr u-gap-10px u-margin-bottom-14px">
                <div class="u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-10px-12px">
                    <div class="u-font-size-11px u-color-text-muted u-text-transform-uppercase u-font-weight-600 u-margin-bottom-2px">Watch Time</div>
                    <div class="u-font-size-18px u-font-weight-700 u-color-accent-color">{watch_time}</div>
                    <div class="u-font-size-10px u-color-text-muted u-margin-top-2px">Across all devices</div>
                </div>
                <div class="u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-10px-12px">
                    <div class="u-font-size-11px u-color-text-muted u-text-transform-uppercase u-font-weight-600 u-margin-bottom-2px">Completed Scrobbles</div>
                    <div class="u-font-size-18px u-font-weight-700 u-color-text-main">{total_scrobbles}</div>
                    <div class="u-font-size-10px u-color-text-muted u-margin-top-2px">{scrobbles_sub}</div>
                </div>
                <div class="u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-10px-12px">
                    <div class="u-font-size-11px u-color-text-muted u-text-transform-uppercase u-font-weight-600 u-margin-bottom-2px">Co-Watch Ratio</div>
                    <div class="u-font-size-18px u-font-weight-700 u-color-c084fc">{cw_pct}%</div>
                    <div class="u-font-size-10px u-color-text-muted u-margin-top-2px">{cowatch_hours}h shared / {solo_hours}h solo</div>
                </div>
                <div class="u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-10px-12px">
                    <div class="u-font-size-11px u-color-text-muted u-text-transform-uppercase u-font-weight-600 u-margin-bottom-2px">Star Ratings</div>
                    <div class="u-font-size-18px u-font-weight-700 u-color-fbbf24">{ratings}</div>
                    <div class="u-font-size-10px u-color-text-muted u-margin-top-2px">Synced across trackers</div>
                </div>
            </div>

            <!-- Co-Watch Ratio Progress Bar -->
            <div class="u-background-bg-page u-border-1px-solid-334155 u-border-radius-8px u-padding-10px-12px u-margin-bottom-12px">
                <div class="u-display-flex u-justify-content-space-between u-align-items-center u-font-size-11px u-margin-bottom-6px">
                    <span class="u-color-text-muted"><span class="u-display-inline-block u-width-8px u-height-8px u-border-radius-50 u-background-38bdf8 u-margin-right-4px"></span>Solo Viewing ({solo_pct}%)</span>
                    <span class="u-color-text-muted"><span class="u-display-inline-block u-width-8px u-height-8px u-border-radius-50 u-background-c084fc u-margin-right-4px"></span>Shared Co-Watching ({cw_pct}%)</span>
                </div>
                <div class="u-width-100 u-height-6px u-background-bg-surface u-border-radius-9999px u-overflow-hidden u-display-flex">
                    <div class="tracker-share-segment tracker-share-solo" style="width:{solo_pct}%;"></div>
                    <div class="tracker-share-segment tracker-share-cowatch" style="width:{cw_pct}%;"></div>
                </div>
            </div>

            <!-- Distribution & Highlights Footer -->
            <div class="u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-10px u-font-size-12px u-color-text-muted">
                <div class="u-display-flex u-align-items-center u-flex-wrap-wrap u-gap-4px">
                    <span class="u-color-text-muted u-margin-right-4px">Servers:</span>
                    {server_badges}
                </div>
                <div class="u-display-flex u-align-items-center u-flex-wrap-wrap u-gap-6px">
                    <strong class="u-color-text-heading u-font-size-11px">{top_show_label}</strong>
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
