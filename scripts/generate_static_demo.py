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
from app.main import APP_VERSION, REPO_URL, DASHBOARD_HTML, OMNISCROBBLE_ICON_SVG
from app.services.demo_manager import demo_mgr


def generate_static_demo(output_dir: Path = None) -> Path:
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare demo datasets from DemoManager
    demo_playback = demo_mgr.get_demo_playback()
    demo_shows = demo_mgr.get_demo_cowatch_shows()
    demo_devices = demo_mgr.get_demo_cowatch_devices()
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

    # Allowed Devices Chips
    device_chips_html = "".join([
        f'<span class="cowatch-device-chip" data-title="{html.escape(d.lower())}" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:3px 9px;border-radius:9999px;font-size:12px;display:inline-flex;align-items:center;margin:2px 3px;">'
        f'📺 {html.escape(d)}<button data-device="{html.escape(d)}" onclick="removeCowatchDevice(decodeURIComponent(this.dataset.device))" title="Remove {html.escape(d)}" style="background:none;border:none;color:#f87171;cursor:pointer;margin-left:6px;font-size:13px;font-weight:700;line-height:1;padding:0;">&times;</button></span>'
        for d in sorted(demo_devices, key=lambda x: x.lower())
    ]) if demo_devices else '<span style="color:#64748b;font-size:12px;font-style:italic;">All devices allowed (no device filtering). Playback on any player triggers co-watch.</span>'

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
                    <span> &bull; Devices: <strong id="cowatch-devices-footer-status">{", ".join(demo_devices) if demo_devices else "All Devices"}</strong></span>
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
                <div style="margin-top:16px;border-top:1px solid #334155;padding-top:14px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">
                        <div style="font-size:13px;font-weight:600;color:#f1f5f9;display:flex;align-items:center;gap:6px;">
                            <span>Allowed Devices Whitelist</span>
                            <span id="cowatch-devices-count-badge" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:1px 6px;border-radius:9999px;font-size:11px;font-weight:700;">{len(demo_devices) if demo_devices else "All"}</span>
                        </div>
                    </div>
                    <div id="cowatch-devices-chips-container" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 10px;min-height:44px;max-height:140px;overflow-y:auto;margin-bottom:10px;display:flex;flex-wrap:wrap;align-content:flex-start;align-items:center;">
                        {device_chips_html}
                    </div>
                    <form onsubmit="event.preventDefault();addCowatchDevice();" autocomplete="off" style="margin:0;">
                        <div class="cowatch-form-row">
                            <input type="text" id="cowatch-device-input" name="cowatch_device" placeholder="Add player/device (e.g. Living Room Apple TV, Shield TV)..."
                                   style="flex:1;background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 12px;color:#f8fafc;font-size:13px;outline:none;"
                                   autocomplete="off" />
                            <button type="submit" class="btn-sm" style="background:#2563eb;color:#fff;font-weight:600;padding:8px 14px;white-space:nowrap;">+ Add Device</button>
                        </div>
                    </form>
                    <div style="margin-top:4px;font-size:11px;color:#64748b;">
                        Leave empty to allow all devices. When configured, co-watching only dual-scrobbles on these players.
                    </div>
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

    ecosystem_card_html = """
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>🌐</span> Multi-Server Ecosystem
            </h3>
            <span style="background:#0f172a;border:1px solid #334155;color:#10b981;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px;">
                <span style="width:7px;height:7px;border-radius:50%;background:#10b981;display:inline-block;"></span>
                9/9 Services Healthy
            </span>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;line-height:1.5;">
            Unified operational topology across all media servers, Trakt &amp; anime scrobble trackers, and automated acquisition engines.
        </p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(270px, 1fr));gap:10px;">
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">🔶</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Plex Media Server</div>
                            <div style="font-size:11px;color:#64748b;">Media Server</div>
                        </div>
                    </div>
                    <span style="background:#064e3b;border:1px solid #059669;color:#10b981;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Online</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                    <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="Local Server (Port 32400)">Local Server (Port 32400)</div>
                    <button onclick="toggleSetting('server', 'plex', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Disable Plex"><span>⏸</span><span>Disable</span></button>
                </div>
            </div>
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">🟣</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Jellyfin</div>
                            <div style="font-size:11px;color:#64748b;">Media Server</div>
                        </div>
                    </div>
                    <span style="background:#0c4a6e;border:1px solid #0284c7;color:#38bdf8;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Ready</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                    <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="Webhook Ingestion Active">Webhook Ingestion Active</div>
                    <button onclick="toggleSetting('server', 'jellyfin', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Disable Jellyfin"><span>⏸</span><span>Disable</span></button>
                </div>
            </div>
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">🟢</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Emby Server</div>
                            <div style="font-size:11px;color:#64748b;">Media Server</div>
                        </div>
                    </div>
                    <span style="background:#0c4a6e;border:1px solid #0284c7;color:#38bdf8;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Ready</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                    <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="Webhook Ingestion Active">Webhook Ingestion Active</div>
                    <button onclick="toggleSetting('server', 'emby', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Disable Emby"><span>⏸</span><span>Disable</span></button>
                </div>
            </div>
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">📺</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Sonarr</div>
                            <div style="font-size:11px;color:#64748b;">Acquisition</div>
                        </div>
                    </div>
                    <span style="background:#064e3b;border:1px solid #059669;color:#10b981;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Online</span>
                </div>
                <div style="font-size:11px;color:#94a3b8;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="48 Series Monitored">48 Series Monitored</div>
            </div>
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">🍿</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Radarr</div>
                            <div style="font-size:11px;color:#64748b;">Acquisition</div>
                        </div>
                    </div>
                    <span style="background:#064e3b;border:1px solid #059669;color:#10b981;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Online</span>
                </div>
                <div style="font-size:11px;color:#94a3b8;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="215 Movies Monitored">215 Movies Monitored</div>
            </div>
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">🔴</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Trakt.tv</div>
                            <div style="font-size:11px;color:#64748b;">Tracker</div>
                        </div>
                    </div>
                    <span style="background:#064e3b;border:1px solid #059669;color:#10b981;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Authenticated</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                    <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="Connected as @demo_viewer (84 days left)">Connected as @demo_viewer (84 days left)</div>
                    <button onclick="toggleSetting('tracker', 'trakt', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Pause Trakt"><span>⏸</span><span>Pause</span></button>
                </div>
            </div>
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">✨</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Simkl</div>
                            <div style="font-size:11px;color:#64748b;">Tracker</div>
                        </div>
                    </div>
                    <span style="background:#064e3b;border:1px solid #059669;color:#10b981;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Active</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                    <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="Connected as @demo_viewer (API v2)">Connected as @demo_viewer (API v2)</div>
                    <button onclick="toggleSetting('tracker', 'simkl', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Pause Simkl"><span>⏸</span><span>Pause</span></button>
                </div>
            </div>
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">⚡</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">AniList</div>
                            <div style="font-size:11px;color:#64748b;">Tracker</div>
                        </div>
                    </div>
                    <span style="background:#064e3b;border:1px solid #059669;color:#10b981;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Authenticated</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                    <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="Connected as @demo_viewer (GraphQL API)">Connected as @demo_viewer (GraphQL API)</div>
                    <button onclick="toggleSetting('tracker', 'anilist', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Pause AniList"><span>⏸</span><span>Pause</span></button>
                </div>
            </div>
            <div class="eco-card" style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                        <span style="font-size:18px;flex-shrink:0;">🎌</span>
                        <div style="min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">MyAnimeList</div>
                            <div style="font-size:11px;color:#64748b;">Tracker</div>
                        </div>
                    </div>
                    <span style="background:#064e3b;border:1px solid #059669;color:#10b981;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap;flex-shrink:0;">Authenticated</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:2px;">
                    <div style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="Connected as @demo_viewer (REST API v2)">Connected as @demo_viewer (REST API v2)</div>
                    <button onclick="toggleSetting('tracker', 'mal', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:3px 9px;font-size:11px;font-weight:500;border-radius:6px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;cursor:pointer;white-space:nowrap;line-height:1.2;flex-shrink:0;" title="Pause MAL"><span>⏸</span><span>Pause</span></button>
                </div>
            </div>
        </div>
    </div>
    """

    simkl_card_html = """
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>✨</span> Multi-Tracker Architecture &bull; Simkl Integration
            </h3>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● Active (@demo_viewer)</span>
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;line-height:1.5;">
            Broadcast playback scrobbles and ratings across both Trakt and Simkl simultaneously. Perfect for Anime, TV shows, and movie watch histories with decoupled, zero-latency async dispatch.
        </p>
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
            <div style="font-size:12px;color:#cbd5e1;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span>Simkl Dual-Scrobbler: <strong>Active</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>Cross-Tracker Sync: <strong>Ready</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>Supported: <strong>Movies, Shows, Anime</strong></span>
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <button onclick="toggleSetting('tracker', 'simkl', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:5px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;line-height:1.2;">⏸ Pause Simkl</button>
                <button onclick="openCrossSyncModal(true)" class="btn-sm" style="background:#0284c7;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;">🔄 Reconcile Trakt & Simkl</button>
                <button onclick="openSimklModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">Simkl Settings</button>
            </div>
        </div>
    </div>
    """

    anime_card_html = """
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>⚡</span> Anime Tracking Engine &bull; AniList &amp; MyAnimeList
            </h3>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● AniList Active (@demo_viewer)</span>
                <span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">● MAL Active (@demo_viewer)</span>
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;line-height:1.5;">
            Specialized anime detection with automatic ID resolution across AniList and MyAnimeList. Scrobbles anime episode progress and synchronizes ratings in real-time with zero media playback latency.
        </p>
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
            <div style="font-size:12px;color:#cbd5e1;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span>Detection: <strong>Auto-Detect Active</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>AniList: <strong>Connected</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>MAL: <strong>Connected</strong></span>
                <span style="color:#64748b;">&bull;</span>
                <span>API: <strong>GraphQL &amp; REST v2</strong></span>
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <button onclick="toggleSetting('tracker', 'anilist', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:5px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;line-height:1.2;">⏸ Pause AniList</button>
                <button onclick="toggleSetting('tracker', 'mal', false, this)" class="btn-sm" style="display:inline-flex;align-items:center;gap:5px;background:#1e293b;border:1px solid #475569;color:#cbd5e1;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;line-height:1.2;">⏸ Pause MAL</button>
                <button onclick="openAnilistModal()" class="btn-sm" style="background:#02a9ff;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">⚡ AniList Settings</button>
                <button onclick="openMalModal()" class="btn-sm" style="background:#2e51a2;color:#fff;font-weight:600;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;">🎌 MAL Settings</button>
            </div>
        </div>
    </div>
    """

    arr_bridge_card_html = """
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;display:flex;align-items:center;gap:8px;">
                <span>🎬</span> Content Bridge & *Arr Watchlist Automation
            </h3>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="background:#064e3b;border:1px solid #059669;color:#a7f3d0;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">Auto-Add: Every 30m</span>
            </div>
        </div>
        <p style="color:#94a3b8;font-size:13px;margin-bottom:16px;line-height:1.5;">
            Automatically monitors your Trakt Watchlist, checks library duplicates, and acquires new movies and shows into Radarr and Sonarr with automatic search and notification dispatch.
        </p>
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span style="background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:3px 9px;border-radius:6px;font-size:12px;display:inline-flex;align-items:center;gap:6px;">
                    <span style="color:#38bdf8;">📺 Sonarr</span><span style="color:#10b981;font-weight:600;">48 Series</span>
                </span>
                <span style="background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:3px 9px;border-radius:6px;font-size:12px;display:inline-flex;align-items:center;gap:6px;">
                    <span style="color:#f59e0b;">🍿 Radarr</span><span style="color:#10b981;font-weight:600;">215 Movies</span>
                </span>
                <span style="font-size:12px;color:#94a3b8;">Search on add: <strong>Enabled</strong> &bull; Alerts: <strong>On</strong></span>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <button onclick="triggerArrWatchlistSync(this)" class="btn-sm" style="background:#10b981;color:#fff;font-weight:600;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;">⚡ Sync Watchlist Now</button>
                <button onclick="openArrModal()" class="btn-sm" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;padding:8px 14px;display:inline-flex;align-items:center;gap:6px;">📋 View Log</button>
            </div>
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
        '{{ECOSYSTEM_CARD}}': ecosystem_card_html,
        '{{SIMKL_CARD}}': simkl_card_html,
        '{{ANIME_CARD}}': anime_card_html,
        '{{ARR_BRIDGE_CARD}}': arr_bridge_card_html,
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

    # Rewrite asset links to relative paths for GitHub Pages (handles subpaths and custom domains)
    rendered = rendered.replace(
        '<link rel="manifest" href="/manifest.json">',
        '<link rel="manifest" href="manifest.json">'
    )
    rendered = rendered.replace(
        '<link rel="apple-touch-icon" href="/static/icons/icon-192.svg">',
        '<link rel="apple-touch-icon" href="assets/icon-192.png">'
    )
    rendered = rendered.replace(
        '<link rel="icon" type="image/svg+xml" href="/static/icons/icon-192.svg">',
        '<link rel="icon" type="image/svg+xml" href="assets/icon.svg">\n    <link rel="alternate icon" type="image/png" href="assets/favicon.png">\n    <link rel="shortcut icon" href="favicon.ico">'
    )
    rendered = rendered.replace(
        '<a href="/demo" style="color:#38bdf8;text-decoration:none;font-weight:600;">Try Demo Mode &rarr;</a>',
        '<span style="color:#38bdf8;font-weight:600;">Demo Mode Active</span>'
    )

    manifest_data = {
        "name": "Omniscrobble Demo",
        "short_name": "Omniscrobble",
        "description": "Universal Media Scrobbler & Webhook Bridge Interactive Demo",
        "start_url": "./",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": [
            {
                "src": "assets/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "assets/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "assets/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            }
        ]
    }

    # 4. Inject Client-Side Mock In-Memory Interceptor
    mock_interceptor_js = f"""
    <!-- GitHub Pages Client-Side Simulation Engine -->
    <script>
    (function() {{
        const initialShows = {json.dumps(demo_shows)};
        const initialDevices = {json.dumps(demo_devices)};
        const initialEvents = {json.dumps(demo_events)};
        const initialReconciliation = {json.dumps(demo_reconciliation)};
        const initialCrossDiff = {json.dumps(demo_mgr.get_demo_cross_tracker_diff())};
        const sonarrCatalog = {json.dumps(sonarr_catalog)};
        const demoLogs = {json.dumps(demo_logs)};

        const clientState = {{
            shows: [...initialShows],
            devices: [...initialDevices],
            events: [...initialEvents],
            reconciliation: [...initialReconciliation],
            crossDiff: [...initialCrossDiff],
            movies_enabled: false,
            playback: {json.dumps(demo_playback)},
            settings: {{
                servers: {{ plex: true, jellyfin: true, emby: true }},
                trackers: {{ trakt: true, simkl: true, anilist: true, mal: true }}
            }}
        }};

        const origFetch = window.fetch;
        window.fetch = async function(resource, init = {{}}) {{
            const urlStr = typeof resource === 'string' ? resource : resource.url;
            const url = new URL(urlStr, window.location.href);
            const path = url.pathname;
            const method = (init.method || 'GET').toUpperCase();

            // Intercept manifest.json and static icon requests
            if (path.endsWith('/manifest.json')) {{
                return new Response({json.dumps(json.dumps(manifest_data))}, {{
                    status: 200,
                    headers: {{ 'Content-Type': 'application/manifest+json' }}
                }});
            }}
            if (path.endsWith('/static/icons/icon-192.svg') || path.endsWith('/static/icons/icon-512.svg')) {{
                return new Response({json.dumps(OMNISCROBBLE_ICON_SVG)}, {{
                    status: 200,
                    headers: {{ 'Content-Type': 'image/svg+xml' }}
                }});
            }}

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

            // 4b. Co-Watch Devices
            if (path.endsWith('/api/cowatch/devices')) {{
                if (method === 'POST') {{
                    const body = init.body ? JSON.parse(init.body) : {{}};
                    const dev = (body.device || '').trim();
                    if (dev && !clientState.devices.some(d => d.toLowerCase() === dev.toLowerCase())) {{
                        clientState.devices.push(dev);
                        clientState.devices.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
                    }}
                    return jsonResp({{ status: 'ok', devices: clientState.devices }});
                }}
                if (method === 'DELETE') {{
                    const dev = url.searchParams.get('device') || '';
                    clientState.devices = clientState.devices.filter(d => d.toLowerCase() !== dev.toLowerCase());
                    return jsonResp({{ status: 'ok', devices: clientState.devices }});
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
                    {{ type: 'show', show: {{ title: 'Lanterns', year: 2026, overview: 'Intergalactic cops John Stewart and Hal Jordan investigate a dark mystery on Earth.', ids: {{ tmdb: 208852 }} }} }},
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

            // 9. Manual Scrobble
            if (path.endsWith('/api/scrobble/manual')) {{
                const body = init.body ? JSON.parse(init.body) : {{}};
                const media = body.media || {{}};
                const isEpisode = media.media_type === 'episode';
                const showTitle = media.show_title || (isEpisode ? media.title : null);
                const titleStr = isEpisode ? `${{showTitle}} S${{String(media.season || 1).padStart(2, '0')}}E${{String(media.episode || 1).padStart(2, '0')}}` : (media.title || 'Movie');
                const isCowatch = Boolean(body.cowatch);
                const newEvent = {{
                    timestamp: new Date().toLocaleTimeString(),
                    user: "demo_viewer",
                    event: "media.scrobble",
                    action: "manual_scrobble (100.0%)",
                    title: titleStr,
                    type: media.media_type || 'episode',
                    show_title: showTitle,
                    progress: "100.0%",
                    result_status: "ok",
                    media_payload: media,
                    cowatch_status: {{
                        synced: isCowatch,
                        reason: isCowatch ? "Manual dual-sync" : "Solo",
                        target: "demo_partner"
                    }}
                }};
                clientState.events.unshift(newEvent);
                return jsonResp({{ status: 'success', result: {{ added: {{ movies: 1, episodes: 1 }} }} }});
            }}

            // 9b. Unscrobble / Remove from History
            if (path.endsWith('/api/history/remove')) {{
                const body = init.body ? JSON.parse(init.body) : {{}};
                const media = body.media || {{}};
                const mTitle = (media.show_title || media.title || '').toLowerCase();
                clientState.events = clientState.events.filter(ev => !(ev.title || '').toLowerCase().includes(mTitle));
                return jsonResp({{
                    status: 'success',
                    message: 'Removed from history across selected trackers',
                    results: {{ trakt: {{ status: 'removed' }}, simkl: {{ status: 'removed' }} }}
                }});
            }}

            // 9c. Dynamic Service & Tracker Toggles
            if (path.endsWith('/api/settings/toggle')) {{
                const body = init.body ? JSON.parse(init.body) : {{}};
                const cat = body.category;
                const k = body.key;
                const en = Boolean(body.enabled);
                if (clientState.settings && clientState.settings[cat]) {{
                    clientState.settings[cat][k] = en;
                }}
                return jsonResp({{ status: 'ok', category: cat, key: k, enabled: en }});
            }}

            if (path.endsWith('/api/settings')) {{
                return jsonResp({{ status: 'ok', settings: clientState.settings }});
            }}

            if (path.endsWith('/api/watchlist') || path.endsWith('/api/cowatch/sync')) {{
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

            // 13. Content Bridge & *Arr Automation Endpoints
            if (path.endsWith('/api/arr/status')) {{
                return jsonResp({{
                    configured: true,
                    sonarr_configured: true,
                    sonarr_connected: true,
                    sonarr_version: "4.0.9",
                    sonarr_series_count: 48,
                    radarr_configured: true,
                    radarr_connected: true,
                    radarr_version: "5.9.1",
                    radarr_movies_count: 215,
                    auto_add_enabled: true,
                    search_on_add: true,
                    interval_seconds: 1800,
                    last_sync_time: Math.floor(Date.now() / 1000) - 900,
                    is_syncing: false,
                    last_result: {{
                        added: {{ movies: 1, shows: 1 }},
                        skipped: {{ movies: 2, shows: 2 }},
                        items: [
                            {{ title: "Gladiator II", year: 2024, type: "movie", status: "added", app: "Radarr" }},
                            {{ title: "Dune: Part Two", year: 2024, type: "movie", status: "skipped", reason: "Already in Radarr", app: "Radarr" }},
                            {{ title: "Alien: Earth", year: 2025, type: "show", status: "added", app: "Sonarr" }},
                            {{ title: "Severance", year: 2022, type: "show", status: "skipped", reason: "Already in Sonarr", app: "Sonarr" }}
                        ]
                    }}
                }});
            }}
            if (path.endsWith('/api/arr/sync')) {{
                return jsonResp({{
                    status: 'success',
                    added: {{ movies: 1, shows: 1 }},
                    skipped: {{ movies: 2, shows: 2 }},
                    items: [
                        {{ title: "Gladiator II", year: 2024, type: "movie", status: "added", app: "Radarr" }},
                        {{ title: "Dune: Part Two", year: 2024, type: "movie", status: "skipped", reason: "Already in Radarr", app: "Radarr" }},
                        {{ title: "Alien: Earth", year: 2025, type: "show", status: "added", app: "Sonarr" }},
                        {{ title: "Severance", year: 2022, type: "show", status: "skipped", reason: "Already in Sonarr", app: "Sonarr" }}
                    ]
                }});
            }}
            if (path.endsWith('/api/simkl/status')) {{
                return jsonResp({{
                    enabled: true,
                    configured: true,
                    authenticated: true,
                    user: "demo_viewer",
                    account_id: 987654,
                    timezone: "America/New_York"
                }});
            }}
            if (path.endsWith('/api/simkl/pin')) {{
                return jsonResp({{
                    user_code: "DEMO-8492",
                    verification_url: "https://simkl.com/pin?code=DEMO-8492",
                    expires_in: 900,
                    interval: 4
                }});
            }}
            if (path.endsWith('/api/simkl/poll')) {{
                return jsonResp({{
                    result: "OK",
                    access_token: "demo_simkl_access_token_xyz"
                }});
            }}
            if (path.endsWith('/api/simkl/disconnect')) {{
                return jsonResp({{
                    status: "ok",
                    message: "Simkl disconnected"
                }});
            }}
            if (path.endsWith('/api/anilist/status')) {{
                return jsonResp({{
                    status: "connected",
                    authenticated: true,
                    user: "demo_viewer",
                    avatar: "https://s4.anilist.co/file/anilistcdn/user/avatar/medium/default.png",
                    id: 1234567,
                    enabled: true,
                    configured: true
                }});
            }}
            if (path.endsWith('/api/anilist/token')) {{
                return jsonResp({{
                    status: "success",
                    user: "demo_viewer",
                    id: 1234567
                }});
            }}
            if (path.endsWith('/api/anilist/disconnect')) {{
                return jsonResp({{
                    status: "ok",
                    message: "AniList disconnected"
                }});
            }}
            if (path.endsWith('/api/mal/status')) {{
                return jsonResp({{
                    status: "connected",
                    authenticated: true,
                    user: "demo_viewer",
                    avatar: "https://myanimelist.net/images/userimages/default.jpg",
                    id: 7654321,
                    enabled: true,
                    configured: true
                }});
            }}
            if (path.endsWith('/api/mal/token')) {{
                return jsonResp({{
                    status: "success",
                    user: "demo_viewer",
                    id: 7654321
                }});
            }}
            if (path.endsWith('/api/mal/disconnect')) {{
                return jsonResp({{
                    status: "ok",
                    message: "MyAnimeList disconnected"
                }});
            }}
            if (path.endsWith('/api/anime/resolve')) {{
                const title = url.searchParams.get('title') || 'Attack on Titan';
                return jsonResp({{
                    title: title,
                    cleaned_title: title,
                    is_anime: true,
                    detection_method: "title_heuristic",
                    confidence: 0.95,
                    anilist_id: 16498,
                    mal_id: 16498,
                    romaji: "Shingeki no Kyojin",
                    english: "Attack on Titan",
                    cached: true
                }});
            }}
            if (path.endsWith('/api/ecosystem')) {{
                return jsonResp({{
                    healthy_count: 9,
                    total_count: 9,
                    servers: [
                        {{ id: "plex", name: "Plex Media Server", category: "Media Server", status: "connected", badge: "Online", version: "1.40.5", details: "Local Server (Port 32400)", icon: "plex" }},
                        {{ id: "jellyfin", name: "Jellyfin", category: "Media Server", status: "available", badge: "Ready", version: "10.9.11", details: "Webhook Ingestion Active", icon: "jellyfin" }},
                        {{ id: "emby", name: "Emby Server", category: "Media Server", status: "available", badge: "Ready", version: "4.8.8", details: "Webhook Ingestion Active", icon: "emby" }},
                        {{ id: "sonarr", name: "Sonarr", category: "Acquisition", status: "connected", badge: "Online", version: "4.0.9", details: "48 Series Monitored", icon: "sonarr" }},
                        {{ id: "radarr", name: "Radarr", category: "Acquisition", status: "connected", badge: "Online", version: "5.9.1", details: "215 Movies Monitored", icon: "radarr" }},
                        {{ id: "trakt", name: "Trakt.tv", category: "Tracker", status: "connected", badge: "Authenticated", version: "API v2", details: "Connected as @demo_viewer (84 days left)", icon: "trakt" }},
                        {{ id: "simkl", name: "Simkl", category: "Tracker", status: "connected", badge: "Active", version: "API v2", details: "Connected as @demo_viewer (Movies, Shows, Anime)", icon: "simkl" }},
                        {{ id: "anilist", name: "AniList", category: "Tracker", status: "connected", badge: "Authenticated", version: "GraphQL API", details: "Connected as @demo_viewer (Anime)", icon: "anilist" }},
                        {{ id: "myanimelist", name: "MyAnimeList", category: "Tracker", status: "connected", badge: "Authenticated", version: "REST API v2", details: "Connected as @demo_viewer (Anime)", icon: "myanimelist" }}
                    ]
                }});
            }}
            if (path.endsWith('/api/cross-sync/status')) {{
                const t2s = clientState.crossDiff.filter(d => d.direction === 'trakt_to_simkl').length;
                const s2t = clientState.crossDiff.filter(d => d.direction === 'simkl_to_trakt').length;
                return jsonResp({{
                    configured: true,
                    trakt_authenticated: true,
                    simkl_authenticated: true,
                    is_scanning: false,
                    is_syncing: false,
                    last_scan_time: Date.now() / 1000 - 300,
                    last_sync_time: Date.now() / 1000 - 1800,
                    diff_count: clientState.crossDiff.length,
                    diff_by_direction: {{
                        trakt_to_simkl: t2s,
                        simkl_to_trakt: s2t
                    }},
                    sync_progress: {{ total: clientState.crossDiff.length, current: clientState.crossDiff.length, success: clientState.crossDiff.length, failed: 0, in_progress: false, status: "idle", message: "" }}
                }});
            }}
            if (path.includes('/api/cross-sync/diff') || path.includes('/api/cross-sync/scan')) {{
                return jsonResp({{
                    status: "ok",
                    diff: clientState.crossDiff,
                    count: clientState.crossDiff.length
                }});
            }}
            if (path.endsWith('/api/cross-sync/execute')) {{
                const count = clientState.crossDiff.length;
                clientState.crossDiff = [];
                return jsonResp({{
                    status: "completed",
                    message: "Successfully synchronized " + count + " cross-tracker items (Demo Mode).",
                    progress: {{ total: count, current: count, success: count, failed: 0, in_progress: false, status: "completed", message: "Done" }}
                }});
            }}
            if (path.endsWith('/api/cross-sync/progress')) {{
                return jsonResp({{
                    total: 6,
                    current: 6,
                    success: 6,
                    failed: 0,
                    in_progress: false,
                    status: "completed",
                    message: "Sync complete!"
                }});
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

    # Write manifest.json with relative asset paths for GitHub Pages
    manifest_file = output_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    # Ensure root favicon.ico exists in output_dir
    assets_favicon = output_dir / "assets" / "favicon.ico"
    if not assets_favicon.is_file():
        assets_favicon = PROJECT_ROOT / "docs" / "assets" / "favicon.ico"
    if assets_favicon.is_file():
        import shutil
        shutil.copyfile(assets_favicon, output_dir / "favicon.ico")

    # Create static icon fallbacks for root deployment requests
    static_icons_dir = output_dir / "static" / "icons"
    static_icons_dir.mkdir(parents=True, exist_ok=True)
    (static_icons_dir / "icon-192.svg").write_text(OMNISCROBBLE_ICON_SVG, encoding="utf-8")
    (static_icons_dir / "icon-512.svg").write_text(OMNISCROBBLE_ICON_SVG, encoding="utf-8")

    print(f"Generated standalone GitHub Pages demo at: {output_file} ({output_file.stat().st_size:,} bytes)")
    return output_file


if __name__ == "__main__":
    generate_static_demo()
