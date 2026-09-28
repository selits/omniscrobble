#!/usr/bin/env python3
"""
Generate a self-contained, interactive static Demo dashboard for GitHub Pages.
Outputs to docs/index.html with an embedded in-memory API simulator.
"""

import html
import json
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Config
from app.main import APP_VERSION, REPO_URL, DASHBOARD_HTML
from app.services.demo_manager import demo_mgr


def generate_static_demo(output_dir: Path = None) -> Path:
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare demo datasets from DemoManager
    demo_playback = demo_mgr.get_demo_playback()
    demo_shows = demo_mgr.get_demo_cowatch_shows()
    demo_events = demo_mgr.get_demo_events()
    demo_users = demo_mgr.get_demo_users()
    demo_logs = demo_mgr.get_demo_logs(lines=60)
    demo_stats = demo_mgr.get_demo_stats()
    demo_reconciliation = demo_mgr.get_demo_reconciliation()
    sonarr_catalog = demo_mgr.SONARR_CATALOG

    # 2. Render initial static HTML template variables
    demo_banner = (
        '<div style="background:linear-gradient(90deg, #1e3a8a, #0284c7);color:#ffffff;padding:12px 18px;'
        'border-radius:10px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;'
        'box-shadow:0 4px 6px -1px rgba(0,0,0,0.3);flex-wrap:wrap;gap:10px;">'
        '<div style="display:flex;align-items:center;gap:10px;">'
        '<span style="font-size:20px;">🎭</span>'
        '<div>'
        '<strong style="color:#ffffff;font-size:14px;">Live Interactive Demo:</strong>'
        '<span style="color:#e0f2fe;font-size:13px;margin-left:6px;">'
        'Experience the Omniscrobble dashboard live in your browser. All interactions are simulated client-side with zero server dependencies.'
        '</span>'
        '</div>'
        '</div>'
        f'<a href="{REPO_URL}" style="background:rgba(255,255,255,0.2);color:#ffffff;text-decoration:none;'
        'padding:6px 14px;border-radius:6px;font-weight:600;font-size:12px;transition:background 0.15s;'
        'display:inline-flex;align-items:center;gap:6px;" '
        'onmouseover="this.style.background=\'rgba(255,255,255,0.3)\'" '
        'onmouseout="this.style.background=\'rgba(255,255,255,0.2)\'">'
        '⭐ View on GitHub &rarr;</a>'
        '</div>'
    )

    demo_header_btn = (
        f'<a href="{REPO_URL}" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;'
        'text-decoration:none;font-weight:600;display:inline-flex;align-items:center;gap:5px;">'
        '⭐ Star on GitHub</a>'
    )

    demo_footer_link = (
        f'<a href="{REPO_URL}" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;text-decoration:none;font-weight:600;">'
        'GitHub Repository &rarr;</a>'
    )

    status_badge = (
        '<span style="background:#10b981;color:#fff;padding:6px 14px;border-radius:9999px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">'
        '● Connected as @demo_viewer &bull; Demo</span>'
    )

    admin_btn = (
        '<span style="font-size:12px;color:#38bdf8;background:#1e293b;border:1px solid #334155;padding:4px 10px;border-radius:6px;font-weight:600;">'
        '👑 Demo Admin</span>'
    )

    # Active Playback Card
    badge_color = "#10b981"
    stream_prog_text = f"{demo_playback['progress']:.1f}% • 18m left"
    user_dev = f"• {demo_playback['username']} on {demo_playback['player']} ({demo_playback['device']})"

    active_playback_card_html = f"""
        <div id="active-playback-card" class="card" style="border-left: 4px solid {badge_color}; margin-bottom: 24px; display: block;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px;">
                <div>
                    <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                        <span id="stream-pulse-indicator" class="pulse-indicator" style="background:{badge_color};"></span>
                        <span style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; color:{badge_color};" id="stream-state-badge">Currently Streaming</span>
                        <span style="font-size:12px; color:#94a3b8;" id="stream-user-device">{user_dev}</span>
                    </div>
                    <h2 style="margin:4px 0 8px 0; font-size:18px; color:#f8fafc;" id="stream-title">{demo_playback['title']}</h2>
                </div>
                <div id="stream-actions">
                    <a id="stream-trakt-link" href="{demo_playback['trakt_url']}" target="_blank" rel="noopener" class="btn-sm" style="background:#334155; color:#38bdf8; text-decoration:none; display:inline-flex; align-items:center; gap:4px;">View on Trakt ↗</a>
                </div>
            </div>
            <div style="margin-top:12px;">
                <div style="display:flex; justify-content:space-between; font-size:12px; color:#94a3b8; margin-bottom:6px;">
                    <span>Playback Progress</span>
                    <span id="stream-progress-text" style="font-weight:600; color:#f8fafc;">{stream_prog_text}</span>
                </div>
                <div style="background:#0f172a; border-radius:9999px; height:8px; overflow:hidden; border:1px solid #334155;">
                    <div id="stream-progress-bar" style="background:{badge_color}; height:100%; width:{demo_playback['progress']}%; border-radius:9999px; transition: width 0.4s ease;"></div>
                </div>
            </div>
        </div>
    """

    # Shared Shows Chips
    chips_html = "".join([
        f'<span class="cowatch-chip" data-title="{html.escape(s.lower())}" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:3px 9px;border-radius:9999px;font-size:12px;display:inline-flex;align-items:center;margin:2px 3px;">'
        f'{html.escape(s)}<button data-show="{html.escape(s)}" onclick="removeCowatchShow(decodeURIComponent(this.dataset.show))" title="Remove {html.escape(s)}" style="background:none;border:none;color:#f87171;cursor:pointer;margin-left:6px;font-size:13px;font-weight:700;line-height:1;padding:0;">&times;</button></span>'
        for s in sorted(demo_shows, key=lambda x: x.lower())
    ])

    users_badges_html = """
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:6px;">
            <div style="display:flex;align-items:center;gap:6px;">
                <span style="font-weight:600;color:#f8fafc;font-size:13px;">@demo_viewer</span>
                <span style="background:#1e3a8a;color:#93c5fd;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:4px;">Default</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
                <span style="color:#10b981;font-size:12px;font-weight:500;">● Connected</span>
            </div>
        </div>
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:6px;">
            <div style="display:flex;align-items:center;gap:6px;">
                <span style="font-weight:600;color:#f8fafc;font-size:13px;">@demo_partner</span>
                <span style="background:#701a75;color:#f5d0fe;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:4px;">Partner</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
                <span style="color:#10b981;font-size:12px;font-weight:500;">● Connected</span>
            </div>
        </div>
    """

    cowatch_card_html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>👥</span> Watch Together & Multi-User Accounts
            </h3>
            <span style="background:#0f172a;border:1px solid #334155;color:#38bdf8;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;">
                Partner: @demo_partner
            </span>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
            Dual-scrobble watched shows to your partner's Trakt account automatically, without syncing your solo shows.
        </p>
        <div class="cowatch-grid">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">
                    <div style="font-size:13px;font-weight:600;color:#f1f5f9;display:flex;align-items:center;gap:6px;">
                        <span>Shared Shows Whitelist</span>
                        <span id="cowatch-count-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 6px;border-radius:9999px;font-size:11px;font-weight:700;">{len(demo_shows)}</span>
                    </div>
                    <input type="text" id="cowatch-filter-input" placeholder="Filter list..." oninput="filterCowatchChips(this.value)" style="background:#0f172a;border:1px solid #334155;border-radius:4px;padding:3px 8px;color:#f8fafc;font-size:11px;outline:none;width:110px;" />
                </div>
                <div id="cowatch-chips-container" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 10px;min-height:54px;max-height:180px;overflow-y:auto;margin-bottom:10px;display:flex;flex-wrap:wrap;align-content:flex-start;align-items:center;">
                    {chips_html}
                </div>
                <form onsubmit="event.preventDefault();addCowatchShow();" autocomplete="off" style="margin:0;">
                    <div class="cowatch-form-row">
                        <div style="flex:1;position:relative;">
                            <input type="search" id="cowatch-show-input" name="cowatch_show_search" placeholder="Add show (e.g. Yellowstone, Severance)..."
                                   style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 12px;color:#f8fafc;font-size:13px;outline:none;"
                                   oninput="onCowatchShowInput(this.value)"
                                   onfocus="onCowatchShowInput(this.value)"
                                   autocomplete="off"
                                   data-lpignore="true"
                                   data-1p-ignore="true"
                                   onkeydown="if(event.key==='Enter')addCowatchShow()" />
                            <div id="sonarr-suggestions" style="display:none;position:absolute;top:100%;left:0;right:0;background:#1e293b;border:1px solid #3b82f6;border-radius:6px;margin-top:4px;max-height:220px;overflow-y:auto;z-index:100;box-shadow:0 10px 15px -3px rgba(0,0,0,0.7);"></div>
                        </div>
                        <button type="submit" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:8px 14px;">+ Add Show</button>
                    </div>
                </form>
                <div style="margin-top:4px;">
                    <span style="color:#10b981;font-size:11px;font-weight:500;display:inline-flex;align-items:center;gap:4px;">✓ Connected to Sonarr (type to search library)</span>
                </div>
                <div style="margin-top:10px;font-size:12px;color:#94a3b8;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                    <span>Movies: <strong id="cowatch-movies-status">Disabled</strong></span>
                    <button id="cowatch-movies-btn" onclick="toggleCowatchMovies()" class="btn-sm" style="padding:2px 8px;font-size:11px;background:#334155;border:1px solid #475569;">Toggle Movies (Enable)</button>
                    <span> &bull; Devices: <strong>Living Room Apple TV</strong></span>
                </div>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <div style="font-size:13px;font-weight:600;color:#f1f5f9;">Linked Trakt Accounts</div>
                    <button onclick="alert('In real mode, this connects secondary Trakt accounts via OAuth.')" class="btn-sm" style="background:#334155;color:#38bdf8;">+ Link Account</button>
                </div>
                <div>
                    {users_badges_html}
                </div>
            </div>
        </div>
    </div>
    """

    reconcile_card_html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>🔄</span> Two-Way Library Reconciliation & Reverse Sync
            </h3>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="color:#10b981;font-size:12px;font-weight:600;">● Plex API Connected</span>
                <span style="background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px;">Periodic: Every 30m</span>
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
            Bi-directional sync matches watched history and ratings between your media server and Trakt with automatic echo-loop suppression.
        </p>
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
            <div>
                <div style="font-size:14px;font-weight:600;color:#f8fafc;display:flex;align-items:center;gap:6px;">
                    <span>Pending Discrepancies</span>
                    <span id="reconcile-diff-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 8px;border-radius:9999px;font-size:12px;font-weight:700;">{len(demo_reconciliation)}</span>
                </div>
                <div style="font-size:12px;color:#94a3b8;margin-top:4px;">
                    Ratings sync: Enabled &bull; Startup sync: Active
                </div>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <button onclick="openReconcileModal(true)" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">🔍 Review Discrepancies</button>
                <button onclick="quickReconcileTraktToPlex(this)" class="btn-sm" style="background:#10b981;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚡ Quick Sync (Trakt &rarr; Plex)</button>
            </div>
        </div>
    </div>
    """

    backup_card_html = f"""
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>💾</span> System Operations & Observability
            </h3>
            <div style="display:flex;gap:8px;align-items:center;">
                <button onclick="openLogsModal()" class="btn-sm" style="background:#1e293b;border:1px solid #3b82f6;color:#60a5fa;display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-weight:600;">📜 View Logs</button>
                <a href="{REPO_URL}#readme" target="_blank" rel="noopener" class="btn-sm" style="background:#0f172a;border:1px solid #334155;color:#38bdf8;text-decoration:none;">📊 Prometheus /metrics ↗</a>
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
            Export or restore your configuration, multi-user Trakt tokens, co-watch whitelist, and inspect live service logs.
        </p>
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;">
            <button onclick="alert('In real mode, this downloads a timestamped backup zip with your tokens and settings.')" class="btn-sm" style="background:#0284c7;color:#fff;padding:8px 16px;font-weight:600;display:inline-flex;align-items:center;gap:6px;cursor:pointer;border:none;">
                💾 Download Backup (.zip)
            </button>
            <button onclick="alert('In real mode, this restores your configuration and SQLite retry database from a zip backup.')" class="btn-sm" style="background:#1e293b;border:1px solid #475569;color:#e2e8f0;padding:8px 16px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px;">
                📤 Restore Backup (.zip)
            </button>
            <button onclick="openTestWebhookModal()" class="btn-sm" style="background:#4338ca;color:#fff;border:1px solid #6366f1;padding:8px 16px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px;">
                🧪 Test Webhook
            </button>
        </div>
    </div>
    """

    webhook_html_section = """
    <div style="margin-top: 18px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:8px;">
            <div class="info-label">Universal Webhook URLs</div>
            <div style="display:flex;gap:6px;">
                <button type="button" onclick="switchWebhookTab('plex')" id="btn-tab-plex" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">Plex</button>
                <button type="button" onclick="switchWebhookTab('jellyfin')" id="btn-tab-jellyfin" class="btn-sm" style="background:#1e293b;color:#94a3b8;">Jellyfin</button>
                <button type="button" onclick="switchWebhookTab('emby')" id="btn-tab-emby" class="btn-sm" style="background:#1e293b;color:#94a3b8;">Emby</button>
            </div>
        </div>
        <div class="webhook-row">
            <input type="text" readonly id="webhook-url-input" value="https://plex.example.com/webhook?token=demo_webhook_secret_xyz"
                   data-plex="https://plex.example.com/webhook?token=demo_webhook_secret_xyz"
                   data-jellyfin="https://jellyfin.example.com/webhook/jellyfin?token=demo_webhook_secret_xyz"
                   data-emby="https://emby.example.com/webhook/emby?token=demo_webhook_secret_xyz"
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

    # Events rows
    rows = ""
    for ev in demo_events:
        color = "#10b981" if ev["result_status"] in ("ok", 200, 201) else "#f59e0b"
        show_title = ev.get("show_title")
        action_buttons = []
        if show_title:
            show_esc = html.escape(show_title)
            action_buttons.append(
                f'<span class="btn-sm" style="padding:2px 6px;font-size:11px;background:#064e3b;color:#a7f3d0;border:1px solid #059669;cursor:default;" title="This show is in your shared co-watch whitelist">✓ Co-Watching</span>'
            )
        if ev.get("media_payload"):
            media_enc = html.escape(json.dumps(ev["media_payload"]))
            action_buttons.append(
                f'<button onclick="quickSyncPartner(\'{media_enc}\', this)" class="btn-sm" style="padding:2px 6px;font-size:11px;background:#701a75;color:#f5d0fe;margin-left:4px;" title="Manually push this watch event to partner account">+ Sync Partner</button>'
            )
        action_col = f'<td style="padding:12px 16px;white-space:nowrap;display:flex;gap:4px;align-items:center;">{"".join(action_buttons)}</td>'

        status_badge_html = f'<span style="color:{color};font-weight:600;font-size:13px;">{ev["result_status"]}</span>'
        cw = ev.get("cowatch_status")
        if cw and cw.get("synced"):
            status_badge_html += ' <span style="background:#701a75;color:#f5d0fe;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;margin-left:4px;" title="Synced to partner: Shared show whitelist match">👥 Co-Watched</span>'

        server_raw = ev.get("server", "plex").lower()
        if server_raw == "jellyfin":
            server_badge = '<span style="background:#3b0764;color:#d8b4fe;border:1px solid #7e22ce;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Jellyfin</span>'
        elif server_raw == "emby":
            server_badge = '<span style="background:#064e3b;color:#a7f3d0;border:1px solid #059669;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Emby</span>'
        else:
            server_badge = '<span style="background:#1e293b;color:#94a3b8;border:1px solid #334155;font-size:10px;font-weight:600;padding:1px 5px;border-radius:3px;margin-right:5px;">Plex</span>'

        rows += f"""
        <tr style="border-bottom: 1px solid #334155;">
            <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;">{ev['timestamp']}</td>
            <td style="padding:12px 16px;color:#f8fafc;font-weight:500;">{html.escape(ev['title'])}</td>
            <td style="padding:12px 16px;"><span style="background:#0f172a;color:#93c5fd;padding:2px 8px;border-radius:4px;font-size:12px;">{ev['type']}</span></td>
            <td style="padding:12px 16px;color:#cbd5e1;font-size:13px;"><div style="display:inline-flex;align-items:center;">{server_badge}<span>{ev['user']}</span></div></td>
            <td style="padding:12px 16px;"><span style="background:#0f172a;color:#e2e8f0;padding:2px 8px;border-radius:4px;font-size:12px;">{ev['action']} ({ev['progress']})</span></td>
            <td style="padding:12px 16px;">{status_badge_html}</td>
            {action_col}
        </tr>
        """

    # 3. Base Template Replacements
    rendered = DASHBOARD_HTML
    replacements = {
        '{{DEMO_BANNER}}': demo_banner,
        '{{DEMO_HEADER_BTN}}': demo_header_btn,
        '{{DEMO_FOOTER_LINK}}': demo_footer_link,
        '{{STATUS_BADGE}}': status_badge,
        '{{ADMIN_BTN}}': admin_btn,
        '{{ACTIVE_PLAYBACK_CARD}}': active_playback_card_html,
        '{{ACCOUNT_DISPLAY}}': '@demo_viewer',
        '{{TOKEN_HEALTH_COLOR}}': '#10b981',
        '{{TOKEN_HEALTH_STR}}': 'Healthy • Auto-renews in 84d',
        '{{ALLOWED_USERS_DISPLAY}}': 'demo_viewer, demo_partner',
        '{{ALLOWED_LIBS_DISPLAY}}': 'Movies, TV Shows, Anime',
        '{{SYNC_COLLECTION_DISPLAY}}': 'On',
        '{{UPTIME_STR}}': '14d 8h 22m',
        '{{QUEUE_COLOR}}': '#94a3b8',
        '{{PENDING_QUEUE}}': '0',
        '{{NOTIF_SUMMARY}}': 'Discord, Telegram',
        '{{STAT_TOTAL}}': str(demo_stats['total']),
        '{{STAT_MOVIES}}': str(demo_stats['movies']),
        '{{STAT_EPISODES}}': str(demo_stats['episodes']),
        '{{STAT_RATINGS}}': str(demo_stats['ratings']),
        '{{STAT_COLLECTIONS}}': str(demo_stats['collections']),
        '{{WEBHOOK_CARD}}': webhook_html_section,
        '{{COWATCH_CARD}}': cowatch_card_html,
        '{{RECONCILIATION_CARD}}': reconcile_card_html,
        '{{BACKUP_CARD}}': backup_card_html,
        '{{MANUAL_SCROBBLE_BTN}}': '<button onclick="openManualScrobbleModal()" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;">🔍 Manual Scrobble</button>',
        '{{RETRY_QUEUE_BTN}}': '',
        '{{CLEAR_BUTTON}}': '<button onclick="clearHistory()" class="btn-sm" style="color:#f87171;">Clear</button>',
        '{{ACTIONS_HEADER}}': '<th>Actions</th>',
        '{{EVENT_ROWS}}': rows,
        '{{IS_ADMIN_JS}}': 'true',
        '{{IS_DEMO_JS}}': 'true',
        '{{APP_VERSION}}': APP_VERSION,
        '{{REPO_URL}}': REPO_URL,
    }

    for k, v in replacements.items():
        rendered = rendered.replace(k, v)

    # 4. Inject Client-Side Mock In-Memory Interceptor
    mock_interceptor_js = f"""
    <!-- GitHub Pages Client-Side Simulation Engine -->
    <script>
    (function() {{
        const initialShows = {json.dumps(demo_shows)};
        const initialEvents = {json.dumps(demo_events)};
        const initialReconciliation = {json.dumps(demo_reconciliation)};
        const sonarrCatalog = {json.dumps(sonarr_catalog)};
        const demoLogs = {json.dumps(demo_logs)};

        const clientState = {{
            shows: [...initialShows],
            events: [...initialEvents],
            reconciliation: [...initialReconciliation],
            movies_enabled: false,
            playback: {json.dumps(demo_playback)}
        }};

        const origFetch = window.fetch;
        window.fetch = async function(resource, init = {{}}) {{
            const urlStr = typeof resource === 'string' ? resource : resource.url;
            const url = new URL(urlStr, window.location.href);
            const path = url.pathname;
            const method = (init.method || 'GET').toUpperCase();

            // If not /api/, let it pass through
            if (!path.includes('/api/')) {{
                return origFetch(resource, init);
            }}

            // Simulated network latency
            await new Promise(r => setTimeout(r, 60));

            const jsonResp = (data, status = 200) => {{
                return new Response(JSON.stringify(data), {{
                    status: status,
                    headers: {{ 'Content-Type': 'application/json' }}
                }});
            }};

            // 1. Playback status
            if (path.endsWith('/api/playback')) {{
                return jsonResp({{
                    active_sessions: [clientState.playback],
                    recently_finished: null
                }});
            }}

            // 2. Events list
            if (path.endsWith('/api/events')) {{
                return jsonResp({{ events: clientState.events }});
            }}

            // 3. Clear events
            if (path.endsWith('/api/events/clear')) {{
                clientState.events = [];
                return jsonResp({{ status: 'cleared' }});
            }}

            // 4. Co-Watch Shows
            if (path.endsWith('/api/cowatch/shows')) {{
                if (method === 'POST') {{
                    const body = init.body ? JSON.parse(init.body) : {{}};
                    const show = (body.show || '').trim();
                    if (show && !clientState.shows.some(s => s.toLowerCase() === show.toLowerCase())) {{
                        clientState.shows.push(show);
                        clientState.shows.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
                    }}
                    return jsonResp({{ status: 'ok', shows: clientState.shows }});
                }}
                if (method === 'DELETE') {{
                    const show = url.searchParams.get('show') || '';
                    clientState.shows = clientState.shows.filter(s => s.toLowerCase() !== show.toLowerCase());
                    return jsonResp({{ status: 'ok', shows: clientState.shows }});
                }}
            }}

            // 5. Co-Watch Settings (Toggle Movies)
            if (path.endsWith('/api/cowatch/settings')) {{
                if (method === 'POST') {{
                    const body = init.body ? JSON.parse(init.body) : {{}};
                    if (body.co_watch_movies !== undefined) {{
                        clientState.movies_enabled = body.co_watch_movies;
                    }}
                    return jsonResp({{ status: 'ok', co_watch_movies: clientState.movies_enabled }});
                }}
            }}

            // 6. Sonarr Series Search / Autocomplete
            if (path.endsWith('/api/sonarr/shows')) {{
                const q = (url.searchParams.get('q') || '').toLowerCase().trim();
                const exclude = clientState.shows.map(s => s.toLowerCase());
                let matches = sonarrCatalog.filter(s => !exclude.includes(s.title.toLowerCase()));
                if (q) {{
                    matches = matches.filter(s => s.title.toLowerCase().includes(q));
                }}
                return jsonResp({{ configured: true, shows: matches.slice(0, 8) }});
            }}

            // 7. System Logs Viewer
            if (path.endsWith('/api/logs')) {{
                const lines = parseInt(url.searchParams.get('lines')) || 50;
                return jsonResp({{
                    source: 'systemd journalctl (simulated)',
                    logs: demoLogs.slice(-lines)
                }});
            }}

            // 8. Trakt Catalog Search
            if (path.endsWith('/api/search')) {{
                const q = (url.searchParams.get('query') || '').toLowerCase().trim();
                const catalog = [
                    {{ type: 'show', show: {{ title: 'Severance', year: 2022, overview: 'Mark leads a team of office workers whose memories have been surgically divided between their work and personal lives.', ids: {{ tmdb: 1128074 }} }} }},
                    {{ type: 'show', show: {{ title: 'The Bear', year: 2022, overview: 'A young chef from the fine dining world comes home to Chicago to run his family Italian beef sandwich shop.', ids: {{ tmdb: 1445277 }} }} }},
                    {{ type: 'movie', movie: {{ title: 'Dune: Part Two', year: 2024, overview: 'Paul Atreides unites with Chani and the Fremen while seeking revenge against the conspirators who destroyed his family.', ids: {{ tmdb: 693134 }} }} }},
                    {{ type: 'show', show: {{ title: 'Fallout', year: 2024, overview: 'In a future post-apocalyptic Los Angeles, citizens must live in underground bunkers to protect themselves from radiation and mutants.', ids: {{ tmdb: 106379 }} }} }},
                    {{ type: 'show', show: {{ title: 'Yellowstone', year: 2018, overview: 'A ranching family in Montana faces off against others encroaching on their land.', ids: {{ tmdb: 73586 }} }} }}
                ];
                let results = catalog.filter(c => !q || (c.show ? c.show.title : c.movie.title).toLowerCase().includes(q));
                if (!results.length && q) {{
                    const titleCased = q.charAt(0).toUpperCase() + q.slice(1);
                    results = [{{ type: 'show', show: {{ title: titleCased, year: 2025, overview: 'Simulated Trakt search result.', ids: {{ tmdb: 100000 }} }} }}];
                }}
                return jsonResp({{ results: results }});
            }}

            // 9. Manual Scrobble & Watchlist
            if (path.endsWith('/api/scrobble/manual') || path.endsWith('/api/watchlist') || path.endsWith('/api/cowatch/sync')) {{
                return jsonResp({{ status: 'success', result: {{ added: {{ movies: 1, episodes: 1 }} }} }});
            }}

            // 10. Queue Retry & Clear
            if (path.endsWith('/api/queue/retry') || path.endsWith('/api/queue/clear')) {{
                return jsonResp({{ status: 'ok', pending_count: 0 }});
            }}

            // 11. Synthetic Test Webhook
            if (path.endsWith('/api/test/webhook')) {{
                const body = init.body ? JSON.parse(init.body) : {{}};
                const isEpisode = body.media_type === 'episode';
                const showTitle = body.show_title || (isEpisode ? 'Synthetic Test Show' : null);
                const isCowatch = isEpisode
                    ? (showTitle && clientState.shows.some(s => s.toLowerCase() === showTitle.toLowerCase()))
                    : clientState.movies_enabled;

                const newEvent = {{
                    timestamp: new Date().toLocaleTimeString(),
                    user: "demo_viewer",
                    event: body.event || "media.scrobble",
                    action: `${{(body.event || 'media.scrobble').replace('media.', '')}} (100.0%)`,
                    title: isEpisode ? `${{showTitle}} S${{String(body.season || 1).padStart(2, '0')}}E${{String(body.episode || 1).padStart(2, '0')}}` : (body.title || 'Synthetic Movie'),
                    type: body.media_type || 'episode',
                    show_title: showTitle,
                    progress: "100.0%",
                    result_status: "ok",
                    cowatch_status: {{
                        synced: isCowatch,
                        reason: isCowatch ? "Shared show whitelist match" : "Solo show",
                        target: "demo_partner"
                    }}
                }};
                clientState.events.unshift(newEvent);

                return jsonResp({{
                    status: 'success',
                    parsed: {{
                        title: newEvent.title,
                        media_type: newEvent.type,
                        show_title: showTitle
                    }},
                    cowatch: {{
                        eligible: isCowatch,
                        reason: isCowatch ? 'Shared show whitelist match' : 'Solo show',
                        partner: 'demo_partner'
                    }},
                    result: {{ status: 'ok', mode: 'simulated' }}
                }});
            }}

            // 12. Two-Way Library Reconciliation Endpoints
            if (path.endsWith('/api/sync/status')) {{
                return jsonResp({{
                    configured: true,
                    plex_configured: true,
                    plex_connected: true,
                    trakt_authenticated: true,
                    diff_count: clientState.reconciliation.length,
                    interval_minutes: 30,
                    sync_on_startup: true,
                    sync_ratings: true
                }});
            }}
            if (path.endsWith('/api/sync/diff')) {{
                return jsonResp({{ status: 'ok', diff: clientState.reconciliation, count: clientState.reconciliation.length }});
            }}
            if (path.endsWith('/api/sync/reconcile')) {{
                const body = init.body ? JSON.parse(init.body) : {{}};
                let count = clientState.reconciliation.length;
                if (body.item_ids && Array.isArray(body.item_ids)) {{
                    clientState.reconciliation = clientState.reconciliation.filter(i => !body.item_ids.includes(i.id));
                    count = body.item_ids.length;
                }} else {{
                    clientState.reconciliation = [];
                }}
                const badge = document.getElementById('reconcile-diff-badge');
                if (badge) badge.innerText = clientState.reconciliation.length;
                return jsonResp({{ status: 'success', total: count, reconciled: count, failed: 0, items: [] }});
            }}
            if (path.endsWith('/api/sync/progress')) {{
                return jsonResp({{ in_progress: false, status: 'idle', total: 0, current: 0, success: 0, failed: 0 }});
            }}

            return jsonResp({{ status: 'ok', mode: 'simulated' }});
        }};
    }})();
    </script>
    """

    # Inject mock interceptor before the closing </head> or right before </script>
    if "</head>" in rendered:
        rendered = rendered.replace("</head>", f"{mock_interceptor_js}\n</head>")
    else:
        rendered = rendered.replace("</body>", f"{mock_interceptor_js}\n</body>")

    output_file = output_dir / "index.html"
    output_file.write_text(rendered, encoding="utf-8")

    # Write .nojekyll so GitHub Pages does not run Jekyll processing
    nojekyll_file = output_dir / ".nojekyll"
    nojekyll_file.write_text("", encoding="utf-8")

    print(f"Generated standalone GitHub Pages demo at: {output_file} ({output_file.stat().st_size:,} bytes)")
    return output_file


if __name__ == "__main__":
    generate_static_demo()
