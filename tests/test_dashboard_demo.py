"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


def test_modular_package_structure():
    """Verify clean modular app package structure, template loading, and base directory resolution."""
    import main as root_main
    import app.main as app_main
    import app.config as app_config
    import app.services.cowatch_manager as app_cw
    import app.services.user_manager as app_um
    import app.services.notifier as app_notif
    import app.services.playback_manager as app_pm
    import app.services.queue_manager as app_qm
    import app.clients.trakt_client as app_tc
    import app.clients.sonarr_client as app_sc
    import app.plex_parser as app_pp
    import app.metrics as app_m
    import os

    # 1. Main entrypoint re-exports app
    assert root_main.app is app_main.app

    # 2. Template verification
    templates_dir = app_main.TEMPLATES_DIR
    assert templates_dir.is_dir()
    assert (templates_dir / "dashboard.html").is_file()
    assert (templates_dir / "auth.html").is_file()
    assert (templates_dir / "auth_locked.html").is_file()

    # 3. Base directory is project root
    assert (app_config.Config.BASE_DIR / "requirements.txt").is_file()
    assert (app_config.Config.BASE_DIR / "main.py").is_file()
    assert (app_config.Config.BASE_DIR / "auth.py").is_file()

    # 4. Verify no old root shim files exist in base folder
    root_files = set(os.listdir(app_config.Config.BASE_DIR))
    for deprecated in [
        "config.py",
        "cowatch_manager.py",
        "metrics.py",
        "notifier.py",
        "playback_manager.py",
        "plex_parser.py",
        "queue_manager.py",
        "sonarr_client.py",
        "trakt_client.py",
        "user_manager.py",
    ]:
        assert deprecated not in root_files, f"{deprecated} should no longer exist in root directory"

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_footer_and_repo_link():
    """Verify that the dashboard renders the version badge and GitHub repository links."""
    client = make_async_test_client(app)
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "https://github.com/selits/omniscrobble" in html
    assert "v3.4.0" in html
    assert "https://github.com/selits/omniscrobble/releases" in html
    assert "https://github.com/selits/omniscrobble#readme" in html
    assert "Auto-refresh (30s)" in html
    assert '<input type="checkbox" id="auto-refresh-toggle" onchange="toggleAutoRefresh(this)">' in html

@pytest.mark.asyncio(loop_scope="module")
async def test_demo_dashboard_page():
    client = make_async_test_client(app)
    res = await client.get("/demo")
    assert res.status_code == 200
    html = res.text
    assert "Demo Mode Active" in html
    assert "Severance" in html
    assert "demo_viewer" in html
    assert "demo_partner" in html
    assert "Live Logs" in html
    assert "Exit Demo" in html
    assert "✕ Exit Demo" in html
    assert "1,428" in html or "1428" in html

@pytest.mark.asyncio(loop_scope="module")
async def test_demo_query_param_on_root():
    client = make_async_test_client(app)
    res_demo = await client.get("/?demo=true")
    assert res_demo.status_code == 200
    assert "Demo Mode Active" in res_demo.text
    assert "✕ Exit Demo" in res_demo.text

    # Without ?demo=true, demo banner should not be present, but header button and modal link are
    res_normal = await client.get("/")
    assert res_normal.status_code == 200
    assert "Demo Mode Active" not in res_normal.text
    assert "🎭 Demo" in res_normal.text
    assert "Try Demo Mode" in res_normal.text

@pytest.mark.asyncio(loop_scope="module")
async def test_demo_api_endpoints():
    client = make_async_test_client(app)

    # 1. Playback
    res = await client.get("/api/playback?demo=true")
    assert res.status_code == 200
    data = res.json()
    assert len(data["active_sessions"]) == 1
    assert "Severance" in data["active_sessions"][0]["title"]
    assert data["recently_finished"] is None

    # 2. Events
    res = await client.get("/api/events?demo=true")
    assert res.status_code == 200
    events = res.json()["events"]
    assert len(events) >= 5
    assert any("Severance" in e["title"] for e in events)

    # 3. Clear events
    res = await client.post("/api/events/clear?demo=true")
    assert res.status_code == 200
    assert res.json()["status"] == "cleared"

    # 4. Queue retry & clear
    res = await client.post("/api/queue/retry?demo=true")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    res = await client.post("/api/queue/clear?demo=true")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 5. Search
    res = await client.get("/api/search?demo=true&query=Severance")
    assert res.status_code == 200
    results = res.json()["results"]
    assert any("Severance" in r.get("show", {}).get("title", "") for r in results)

    # 6. Manual scrobble
    res = await client.post("/api/scrobble/manual?demo=true", json={"media_type": "movie", "title": "Inception"})
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # 7. Cowatch shows
    res = await client.post("/api/cowatch/shows?demo=true", json={"show": "Ted Lasso"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    res = await client.delete("/api/cowatch/shows?show=Ted%20Lasso&demo=true")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 8. Sonarr shows
    res = await client.get("/api/sonarr/shows?demo=true")
    assert res.status_code == 200
    shows = res.json()["shows"]
    assert len(shows) >= 5

    res_q = await client.get("/api/sonarr/shows?demo=true&q=Severance")
    assert res_q.status_code == 200
    assert any(s["title"] == "Severance" for s in res_q.json()["shows"])

    # 9. Cowatch sync
    res = await client.post("/api/cowatch/sync?demo=true", json={"media_type": "episode", "title": "Severance"})
    assert res.status_code == 200
    assert res.json()["status"] == "success"

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_mobile_responsiveness():
    """Verify that the dashboard and demo pages contain mobile responsive viewport and CSS structures."""
    client = make_async_test_client(app)

    for path in ["/", "/demo"]:
        resp = await client.get(path)
        assert resp.status_code == 200
        html = resp.text
        html += await dashboard_asset_text(client)
        css_resp = await client.get("/static/dashboard.css")
        assert css_resp.status_code == 200
        css = css_resp.text

        # 1. Viewport meta tag
        assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in html

        # 2. CSS Media query for mobile viewports
        assert "@media (max-width: 640px)" in css
        assert "-webkit-overflow-scrolling: touch" in css
        assert "@media (min-width: 641px) and (max-width: 900px)" in css
        assert "@media (min-width: 1200px)" in css
        assert "scroll-snap-type: x proximity" in css
        assert "#card-activity .activity-row td::before" in css
        assert "display: flex; flex-direction: column" in css
        assert "grid-template-columns: 62px minmax(0, 1fr)" in css
        assert "@media (max-height: 520px) and (max-width: 900px)" in css
        assert "@media (prefers-reduced-motion: reduce)" in css
        assert "@media (prefers-contrast: more)" in css
        assert "@media (forced-colors: active)" in css

        # 3. Mobile layout classes
        assert 'class="title-brand"' in html
        assert 'class="title-sub"' in html
        assert 'class="cowatch-grid"' in html
        assert 'class="cowatch-account-row"' in html
        assert 'class="cowatch-form-row"' in html
        assert 'class="webhook-row"' in html
        assert 'class="activity-header"' in html
        assert 'class="activity-actions"' in html
        assert 'class="table-container"' in html
        assert 'class="modal-dialog' in html
        assert 'class="logs-toolbar"' in html
        assert 'class="footer"' in html
        assert '.cowatch-account-row' in css
        assert '.cowatch-grid > div + div' in css
        assert 'Universal Media Scrobbler &amp; Multi-Tracker Hub' in html
        assert 'Media Server Webhook Endpoints' in html
        assert 'Tracker Only &bull; Mark' in html
        assert 'omniscrobble_auto_refresh' in html
        assert 'grid-template-columns: 1fr !important' in css
        assert 'word-break: break-word' in css
        assert 'settings-trk-cat-btn' in html
        assert 'scrobble-trk-kitsu' in html

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_workspace_and_accessibility_contracts():
    """Guard workspace navigation, activity card labels, and modal keyboard support."""
    client = make_async_test_client(app)
    response = await client.get("/")
    assert response.status_code == 200
    html = response.text + await dashboard_asset_text(client)

    # Workspace tabs expose short visual labels and full accessible names.
    for tab_id, label in [
        ("operations", "Operations"),
        ("watch-lists", "Watch Lists"),
        ("trackers", "Trackers and Hub"),
        ("automation", "Automation"),
        ("analytics", "Analytics"),
        ("diagnostics", "System"),
    ]:
        assert f'id="tab-{tab_id}"' in html
        assert f'aria-label="{label}"' in html
        assert f'id="view-{tab_id}"' in html
    assert 'id="view-operations" role="tabpanel" tabindex="0"' in html
    assert 'class="workspace-intro"' in html

    # Health indicators open the matching connection or recovery flow.
    assert 'class="health-pill health-pill-action" onclick="openTrackerRecovery()"' in html
    assert 'onclick="openListenerRecovery()"' in html
    assert 'onclick="openQueueRecovery()"' in html
    assert "function openTrackerRecovery()" in html
    assert "function openQueueRecovery()" in html

    # Activity rows include labels used by the stacked mobile card layout.
    for label in ["When", "Title", "Type", "User", "Action", "Status"]:
        assert f'data-label="{label}"' in html

    # Workspace arrow-key handling and modal focus containment/restoration stay wired.
    assert "['ArrowLeft', 'ArrowRight', 'Home', 'End']" in html
    assert "event.key === 'ArrowRight'" in html
    assert "function initModalAccessibility()" in html
    assert "dialog.setAttribute('aria-modal', 'true')" in html
    assert "event.shiftKey && (document.activeElement === first" in html
    assert "opener.focus({ preventScroll: true })" in html

def test_static_github_pages_demo_generation(tmp_path):
    """Verify scripts/generate_static_demo.py generates a valid standalone GitHub Pages demo."""
    import re
    from pathlib import Path
    from app.main import APP_VERSION
    from scripts.build_dashboard_bundle import MODULES, OUTPUT, build_bundle
    from scripts.generate_static_demo import generate_static_demo

    # 1. Test generation to temporary directory
    demo_file = generate_static_demo(output_dir=tmp_path)
    assert demo_file.is_file()
    assert (tmp_path / ".nojekyll").is_file()
    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "static" / "icons" / "icon-192.svg").is_file()
    assert (tmp_path / "static" / "icons" / "icon-512.svg").is_file()
    assert (tmp_path / "assets" / "dashboard.css").is_file()
    assert (tmp_path / "assets" / "dashboard-theme.css").is_file()
    assert (tmp_path / "assets" / "dashboard.js").is_file()
    assert "analytics_export.js" in MODULES
    assert OUTPUT.read_text(encoding="utf-8") == build_bundle()

    content = demo_file.read_text(encoding="utf-8")
    assert "Live Interactive Demo" in content
    assert APP_VERSION in content
    assert "👑 Demo Admin" in content
    assert "● Connected as @demo_viewer" in content
    assert "window.fetch = async function" in content
    assert "/api/playback" in content
    assert "/api/events" in content
    assert "/api/cowatch/shows" in content
    assert "/api/cowatch/devices" in content
    assert "/api/sonarr/shows" in content
    assert "/api/logs" in content
    assert "/api/search" in content
    assert "/api/test/webhook" in content
    script_content = (tmp_path / "assets" / "dashboard.js").read_text(encoding="utf-8")
    assert "updateDemoSettingUI" in script_content
    assert "!isDemo && 'serviceWorker'" in script_content

    # Verify asset links use relative paths for GitHub Pages subpath compatibility
    assert 'href="manifest.json"' in content
    assert 'href="/manifest.json"' not in content
    assert 'href="assets/icon-192.png"' in content
    assert 'href="assets/icon.svg"' in content
    assert 'href="/static/icons/icon-192.svg"' not in content
    assert 'href="assets/dashboard.css"' in content
    assert 'href="assets/dashboard-theme.css"' in content
    assert 'src="assets/dashboard.js"' in content

    # Verify no unreplaced template placeholders
    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", content)
    assert not unreplaced, f"Found unreplaced template variables in static demo: {unreplaced}"

    # Verify balanced curly braces in scripts to ensure no syntax errors
    scripts = re.findall(r"<script(?:\s+[^>]*)?>(.*?)</script>", content, re.DOTALL)
    assert len(scripts) >= 2
    for idx, s in enumerate(scripts):
        open_count = s.count("{")
        close_count = s.count("}")
        assert open_count == close_count, f"Script {idx} in static demo has unbalanced braces: open={open_count}, close={close_count}"

    # 2. Verify docs/index.html in repo exists and is valid
    repo_docs_index = Path(__file__).resolve().parent.parent / "docs" / "index.html"
    assert repo_docs_index.is_file(), "docs/index.html does not exist in repository"
    repo_content = repo_docs_index.read_text(encoding="utf-8")
    assert "Live Interactive Demo" in repo_content
    assert APP_VERSION in repo_content
    assert not re.findall(r"\{\{[A-Z_]+\}\}", repo_content)
    assert 'href="/static/icons/icon-192.svg"' not in repo_content

    # Checked-in Pages assets must mirror app assets. CI checks that dashboard.js
    # itself matches the ordered source modules before running this test.
    repo_root = Path(__file__).resolve().parent.parent
    for asset_name in ("dashboard.js", "dashboard.css", "dashboard-theme.css"):
        app_asset = repo_root / "app" / "static" / asset_name
        pages_asset = repo_root / "docs" / "assets" / asset_name
        assert pages_asset.read_bytes() == app_asset.read_bytes(), f"docs/assets/{asset_name} is out of sync"

@pytest.mark.asyncio(loop_scope="module")
async def test_pwa_and_static_assets():
    client = make_async_test_client(app)

    # Manifest
    res_manifest = await client.get("/manifest.json")
    assert res_manifest.status_code == 200
    assert "application/manifest+json" in res_manifest.headers.get("content-type", "")
    data = res_manifest.json()
    assert data["name"] == "Omniscrobble"
    assert data["short_name"] == "Omniscrobble"
    assert data["display"] == "standalone"
    assert len(data["icons"]) >= 2

    # Service Worker
    res_sw = await client.get("/sw.js")
    assert res_sw.status_code == 200
    assert "javascript" in res_sw.headers.get("content-type", "")
    assert "omniscrobble" in res_sw.text

    # Icons
    res_icon192 = await client.get("/static/icons/icon-192.svg")
    assert res_icon192.status_code == 200
    assert "image/svg+xml" in res_icon192.headers.get("content-type", "")
    assert "<svg" in res_icon192.text

    res_icon512 = await client.get("/static/icons/icon-512.svg")
    assert res_icon512.status_code == 200
    assert "image/svg+xml" in res_icon512.headers.get("content-type", "")
    assert "<svg" in res_icon512.text

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_multi_server_tabs_and_oled_theme():
    client = make_async_test_client(app)
    res = await client.get("/")
    assert res.status_code == 200
    html = res.text + await dashboard_asset_text(client)

    # Multi-server tabs
    assert "switchWebhookTab('plex')" in html
    assert "switchWebhookTab('jellyfin')" in html
    assert "switchWebhookTab('emby')" in html
    assert "/webhook/jellyfin" in html
    assert "/webhook/emby" in html

    # OLED theme toggle
    assert "toggleTheme()" in html
    assert "theme-toggle" in html
    assert "theme-oled" in html or "omniscrobble_theme" in html

@pytest.mark.asyncio(loop_scope="module")
async def test_opengraph_and_social_metadata():
    """Verify that the dashboard serves complete OpenGraph and Twitter card social preview meta tags."""
    client = make_async_test_client(app)
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert '<meta property="og:title" content="Omniscrobble' in html
    assert '<meta property="og:description" content="Watch anywhere. Track everywhere.' in html
    assert '<meta property="og:image" content="https://selits.github.io/omniscrobble/assets/social-preview.png">' in html
    assert '<meta property="twitter:card" content="summary_large_image">' in html

def test_brand_assets_integrity():
    """Verify that all generated brand assets exist, are non-empty, and contain valid image headers."""
    assets_dir = Path(__file__).resolve().parent.parent / "docs" / "assets"
    expected_assets = [
        "banner.png",
        "banner.svg",
        "icon-512.png",
        "icon-192.png",
        "icon.svg",
        "favicon.png",
        "favicon.ico",
        "social-preview.png",
        "social-preview.svg",
    ]
    for asset_name in expected_assets:
        asset_path = assets_dir / asset_name
        assert asset_path.exists(), f"Asset {asset_name} is missing from docs/assets/"
        assert asset_path.stat().st_size > 0, f"Asset {asset_name} is empty"

    # SVG validation
    icon_svg = (assets_dir / "icon.svg").read_text(encoding="utf-8")
    assert "<svg" in icon_svg and "</svg>" in icon_svg
    assert "arrowGradTop" in icon_svg

    # PNG magic bytes check (\x89PNG)
    for png_name in ["banner.png", "icon-512.png", "icon-192.png", "favicon.png", "social-preview.png"]:
        header = (assets_dir / png_name).read_bytes()[:8]
        assert header.startswith(b"\x89PNG"), f"{png_name} does not have valid PNG header"

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_renders_cross_sync_modal():
    """Verify that the dashboard renders the Cross-Tracker Reconciliation modal and trigger buttons."""
    client = make_async_test_client(app)
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text + await dashboard_asset_text(client)

    assert "cross-sync-modal" in html
    assert "Trakt &rarr; TMDb ratings" in html
    assert "openCrossSyncModal" in html
    assert "closeCrossSyncModal" in html
    assert "Cross-Tracker Reconciliation" in html

@pytest.mark.asyncio(loop_scope="module")
async def test_all_dashboard_modals_have_overlay_styling_and_hidden_by_default():
    """Every element wrapping a .modal-dialog must be class="modal" with a unique "*-modal" id,
    and the stylesheet must hide it by default as a fixed overlay.

    Prevents modals (e.g. household routing, webhook debugger, omniwrapped) from leaking into the
    bottom of the page in document flow.
    """
    import re
    from html.parser import HTMLParser

    class ModalCollector(HTMLParser):
        VOID = {"br", "hr", "img", "input", "meta", "link", "source", "area", "base", "col", "embed", "wbr"}

        def __init__(self):
            super().__init__()
            self.stack = []
            self.parents = []  # attrs dicts of direct parents of .modal-dialog

        def handle_starttag(self, tag, attrs):
            if tag in self.VOID:
                return
            attrs = dict(attrs)
            classes = (attrs.get("class") or "").split()
            if "modal-dialog" in classes and self.stack:
                self.parents.append(self.stack[-1])
            self.stack.append(attrs)

        def handle_endtag(self, tag):
            if tag not in self.VOID and self.stack:
                self.stack.pop()

    client = make_async_test_client(app)
    for path in ("/", "/demo"):
        res = await client.get(path)
        assert res.status_code == 200
        html_content = res.text
        css_content = (await client.get("/static/dashboard.css")).text

        collector = ModalCollector()
        collector.feed(html_content)
        assert collector.parents, "No modal dialogs found"

        modal_ids = []
        for attrs in collector.parents:
            assert "modal" in (attrs.get("class") or "").split(), f"Modal container {attrs} missing class=\"modal\""
            mid = attrs.get("id")
            assert mid and mid.endswith("-modal"), f"Modal container {attrs} needs an id ending in '-modal'"
            modal_ids.append(mid)
        assert len(modal_ids) == len(set(modal_ids)), "Duplicate modal ids"

        # v3.0.0 modals that originally leaked
        for required in ("household-rule-modal", "webhook-debugger-modal", "omniwrapped-modal"):
            assert required in modal_ids

        # Universal CSS rule hides every modal by default as a fixed overlay
        css_match = re.search(r'(\.modal,\s*\.modal-overlay,[^{]*\{[^}]*\})', css_content)
        assert css_match, "Universal .modal CSS rule missing from stylesheet!"
        modal_css = css_match.group(0)
        assert "display: none;" in modal_css
        assert "position: fixed;" in modal_css
        assert "z-index: 9999;" in modal_css

@pytest.mark.asyncio(loop_scope="module")
async def test_cache_control_headers_and_sw_invalidation():
    """Verify that dashboard, API endpoints, and sw.js have no-cache headers and sw.js is dynamically versioned."""
    from app.main import APP_VERSION
    client = make_async_test_client(app)

    # 1. Root dashboard returns no-cache headers
    dash_res = await client.get("/")
    assert dash_res.status_code == 200
    cache_ctrl = dash_res.headers.get("cache-control", "")
    assert "no-cache" in cache_ctrl
    assert "no-store" in cache_ctrl

    # 2. Service worker script returns no-cache headers and dynamic versioning
    sw_res = await client.get("/sw.js")
    assert sw_res.status_code == 200
    sw_cache = sw_res.headers.get("cache-control", "")
    assert "no-cache" in sw_cache
    assert f"omniscrobble-v{APP_VERSION}" in sw_res.text
    # Verify '/' is not cached in STATIC_ASSETS
    static_assets_block = sw_res.text.split("STATIC_ASSETS = [")[1].split("]")[0]
    assert "'/'" not in static_assets_block and '"/"' not in static_assets_block

    # 3. Dynamic API endpoint returns no-cache headers
    api_res = await client.get("/api/events")
    assert api_res.status_code == 200
    assert "no-cache" in api_res.headers.get("cache-control", "")

@pytest.mark.asyncio(loop_scope="module")
async def test_http_security_headers():
    """Verify security headers are present on dashboard and API responses."""
    client = make_async_test_client(app)
    for path in ["/", "/api/events", "/sw.js"]:
        res = await client.get(path)
        assert res.headers.get("x-frame-options") == "SAMEORIGIN"
        assert res.headers.get("x-content-type-options") == "nosniff"
        assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

@pytest.mark.asyncio(loop_scope="module")
async def test_ssr_html_escaping():
    """Verify stored strings in SSR events table are safely HTML-escaped to prevent Stored XSS."""
    from app.main import log_event, ParsedMedia
    client = make_async_test_client(app)

    malicious_title = "<script>alert('xss_title')</script>"
    malicious_user = "<img src=x onerror=alert('xss_user')>"

    parsed = ParsedMedia(
        event="media.scrobble",
        media_type="movie",
        title=malicious_title,
        year=2024,
        username=malicious_user,
        progress=100.0,
    )
    log_event(
        media=parsed,
        action="scrobble",
        result={"status": "ok"},
    )

    res = await client.get("/")
    assert res.status_code == 200
    assert "<script>alert('xss_title')</script>" not in res.text
    assert "<img src=x onerror=alert('xss_user')>" not in res.text
    assert "&lt;script&gt;alert(&#x27;xss_title&#x27;)&lt;/script&gt;" in res.text or "&lt;script&gt;alert('xss_title')&lt;/script&gt;" in res.text

@pytest.mark.asyncio(loop_scope="module")
async def test_api_events_pagination_and_dashboard_controls():
    client = make_async_test_client(app)
    recent_events.clear()

    # Seed 25 test events
    for i in range(25):
        recent_events.appendleft({
            "timestamp": f"2026-10-02 12:{i:02d}:00",
            "user": "selits",
            "server": "plex",
            "event": "media.scrobble",
            "action": "scrobble",
            "title": f"Show S01E{i+1:02d}",
            "type": "episode",
            "show_title": "Show",
            "media_payload": {"media_type": "episode", "title": f"Episode {i+1}", "season": 1, "episode": i + 1},
            "progress": "100.0%",
            "result_status": "ok",
            "raw_result": {"status": "ok"},
            "cowatch_status": None,
        })

    try:
        # 1. /api/events returns all 25 items and total
        res = await client.get("/api/events")
        assert res.status_code == 200
        data = res.json()
        assert len(data["events"]) == 25
        assert data["total"] == 25

        # 2. /api/events with limit & offset
        res_page1 = await client.get("/api/events?limit=10&offset=0")
        assert res_page1.status_code == 200
        data1 = res_page1.json()
        assert len(data1["events"]) == 10
        assert data1["total"] == 25
        assert data1["events"][0]["title"] == "Show S01E25"

        res_page2 = await client.get("/api/events?limit=10&offset=10")
        assert res_page2.status_code == 200
        data2 = res_page2.json()
        assert len(data2["events"]) == 10
        assert data2["events"][0]["title"] == "Show S01E15"

        res_page3 = await client.get("/api/events?limit=10&offset=20")
        assert res_page3.status_code == 200
        data3 = res_page3.json()
        assert len(data3["events"]) == 5

        # 3. /api/events in demo mode
        res_demo = await client.get("/api/events?demo=true&limit=2&offset=0")
        assert res_demo.status_code == 200
        assert len(res_demo.json()["events"]) == 2
        assert res_demo.json()["total"] > 2

        # 4. Dashboard HTML includes pagination controls and info
        dash_res = await client.get("/")
        assert dash_res.status_code == 200
        assert "events-pagination" in dash_res.text
        assert "events-page-info" in dash_res.text
        assert "events-page-size" in dash_res.text
        assert "events-prev-btn" in dash_res.text
        assert "events-next-btn" in dash_res.text
        assert "Showing 1–10 of 25 events" in dash_res.text
        assert "Page 1 of 3" in dash_res.text

        # 5. Config default verification
        assert Config.MAX_EVENT_HISTORY >= 10
    finally:
        recent_events.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_activity_table_ui_polish():
    """Verify action label formatting, co-watch badge filtering, unhandled webhook skipping, and SSR styling."""
    from app.main import (
        format_action_label,
        should_display_cowatch_badge,
        render_status_badge,
        render_tracker_delivery_badges,
        recent_events,
        log_event,
        process_media_event,
    )
    from app.plex_parser import ParsedMedia
    from app.config import Config

    # 1. Action label formatting
    assert format_action_label("scrobble_start") == "play"
    assert format_action_label("scrobble_pause") == "pause"
    assert format_action_label("scrobble_stop") == "scrobble"
    assert format_action_label("mark_watched") == "scrobble"
    assert format_action_label("playback_stopped") == "stop"
    assert format_action_label("test_webhook") == "test"
    assert format_action_label("none") == "ignored"
    assert format_action_label("custom_action") == "custom_action"

    # 2. Co-watch badge display eligibility
    # False on ignored / error / 0% progress
    assert should_display_cowatch_badge("scrobble_stop", "ignored", "100.0%") is False
    assert should_display_cowatch_badge("mark_watched", "error", "100.0%") is False
    assert should_display_cowatch_badge("scrobble_stop", "ok", "0.0%") is False
    # False on interim playback states
    assert should_display_cowatch_badge("scrobble_start", "ok", "10.0%") is False
    assert should_display_cowatch_badge("scrobble_pause", "ok", "45.0%") is False
    assert should_display_cowatch_badge("playback_stopped", "ok", "50.0%") is False
    # True on valid completed scrobbles
    assert should_display_cowatch_badge("scrobble_stop", "ok", "92.0%") is True
    assert should_display_cowatch_badge("mark_watched", "ok", "100.0%") is True
    assert should_display_cowatch_badge("scrobble", "200", "90.0%") is True

    # 3. Status badge rendering helper
    cw_synced = render_status_badge("mark_watched", "ok", "100.0%", {"synced": True, "target": "partner"})
    assert "👥 Co-Watched" in cw_synced
    assert "Synced to @partner" in cw_synced
    assert "ok" not in cw_synced.lower() or "ok" not in cw_synced  # No awkward 'ok' prepended

    cw_solo = render_status_badge("scrobble_stop", "ok", "95.0%", {"synced": False, "reason": "Not in whitelist"})
    assert "✓ Scrobbled" in cw_solo
    assert "Solo: Not in whitelist" in cw_solo
    assert "👥 Solo" not in cw_solo  # Replaced noisy badge with clean tooltip

    assert "✓ Added" in render_status_badge("collection", "ok")
    assert "✓ Rated" in render_status_badge("rate", "ok")
    assert "✓ OK" in render_status_badge("playback_stopped", "ok", "0.0%")
    assert "Ignored" in render_status_badge("scrobble_start", "ignored")
    assert "⏳ Queued" in render_status_badge("scrobble_stop", "queued")
    assert "✕ Failed" in render_status_badge("scrobble_stop", "error")
    delivery_badges = render_tracker_delivery_badges({"trakt": "success", "simkl": "queued", "anilist": "failed"})
    assert "TRK ✓" in delivery_badges
    assert "SKL ⌛" in delivery_badges
    assert "ANL ×" in delivery_badges

    # 4. Unhandled webhook events in scrobble mode do NOT log "none"
    parsed_unhandled = ParsedMedia(
        event="library.on.deck",
        username="selits",
        media_type="show",
        title="Unknown Deck Item",
        progress=0.0,
    )
    recent_events.clear()
    with patch.object(trakt, "is_authenticated", return_value=True):
        res = await process_media_event(parsed_unhandled, endpoint_name="webhook")
    assert res["status"] == "ignored"
    assert "library.on.deck" in res["reason"]
    # Verify nothing was added to recent_events with action "none"
    assert len([e for e in recent_events if e.get("action") == "none"]) == 0

    # 5. SSR Dashboard Table Verification
    client = make_async_test_client(app)
    client.cookies.set("admin_token", "unlocked")

    # Log a 0% stopped event (should have clean ✓ OK badge, NOT Solo or raw ok)
    parsed_zero = ParsedMedia(
        event="media.stop",
        username="selits",
        media_type="movie",
        title="Quick Sample Test Movie",
        progress=0.0,
    )
    log_event(
        parsed_zero,
        "playback_stopped",
        {"status": "ok"},
        cowatch_status={"synced": False, "reason": "Not in shared co-watch list"},
    )

    # Log a 100% completed scrobble
    parsed_completed = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Finished Blockbuster Movie",
        progress=100.0,
    )
    log_event(
        parsed_completed,
        "mark_watched",
        {"status": "ok"},
        cowatch_status={"synced": True, "target": "partner", "reason": "Shared show whitelist match"},
    )

    # Log an unsynced 100% completed event (should still offer + Sync Partner)
    parsed_unsynced = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Solo Watched Indie Movie",
        progress=100.0,
    )
    log_event(
        parsed_unsynced,
        "mark_watched",
        {"status": "ok"},
        cowatch_status={"synced": False, "reason": "Not in shared co-watch list"},
    )

    dash_res = await client.get("/")
    assert dash_res.status_code == 200
    html_text = dash_res.text + await dashboard_asset_text(client)

    # Header styling: renamed to clean universal "Status"
    assert '<th class="u-white-space-nowrap">Status</th>' in html_text
    assert '<th class="activity-actions-header">Actions</th>' in html_text

    # Verify Co-Watched badge uses the shared status style and has no awkward 'ok' prepended
    assert '👥 Co-Watched</span>' in html_text
    assert 'activity-status-badge activity-status-cowatch" title="Synced to @partner' in html_text

    # Verify 0% stop gets clean ✓ OK badge and does NOT have Solo badge
    assert '✓ OK</span>' in html_text
    assert 'title="Co-watch skipped: Not in shared co-watch list"' not in html_text

    # Action buttons: inert ✓ Co-Watching is omitted completely
    assert '✓ Co-Watching' not in html_text

    # Action buttons: unsynced completed event gets + Sync Partner and Unscrobble
    assert '+ Sync Partner' in html_text
    assert '🗑️ Unscrobble' in html_text

    # Action buttons flex styling
    assert 'activity-row-actions' in html_text

def test_dashboard_search_escapes_metadata_and_inline_arguments():
    """Untrusted tracker metadata cannot become HTML or break inline handlers."""
    modules = Path(__file__).resolve().parents[1] / "app" / "static" / "dashboard_modules"
    api = (modules / "api.js").read_text(encoding="utf-8")
    core = (modules / "core.js").read_text(encoding="utf-8")
    activity = (modules / "activity.js").read_text(encoding="utf-8")
    cowatch = (modules / "cowatch.js").read_text(encoding="utf-8")
    webhook_tests = (modules / "webhook_tests.js").read_text(encoding="utf-8")

    assert "replace(/'/g, '%27')" in api
    assert "const title = escapeHtml(media.title || '')" in core
    assert "${escapeHtml(media.overview)}" in core
    assert "encodeURIComponentForInlineJs(JSON.stringify(payloadData))" in core
    assert "encodeURIComponentForInlineJs(JSON.stringify(ev.media_payload))" in activity
    assert "${escapeHtml(s.status)}" in cowatch
    assert "const titleStr = escapeHtml(item.type === 'episode'" in webhook_tests
    assert "escapeHtml(item.type)" in webhook_tests
    assert 'data-id="${escapeHtml(String(item.id ?? \'\'))}"' in webhook_tests
    assert "escapeHtml(err.detail || 'Error')" in webhook_tests

def test_command_palette_shortcut_toggles_open_state():
    modules = Path(__file__).resolve().parents[1] / "app" / "static" / "dashboard_modules"
    core = (modules / "core.js").read_text(encoding="utf-8")
    palette = (modules / "command_palette.js").read_text(encoding="utf-8")

    assert "e.preventDefault();\n                toggleCommandPalette();" in core
    assert "if (modal.style.display === 'flex') closeCommandPalette();" in palette
    assert "else openCommandPalette();" in palette

@pytest.mark.asyncio(loop_scope="module")
async def test_multi_theme_palette_engine_and_accents():
    """Verify that all 8 theme palettes, 9 accent highlights, modals, and keyboard shortcuts render on the dashboard."""
    from app.main import app

    client = make_async_test_client(app)
    res = await client.get("/")
    assert res.status_code == 200
    html = res.text + await dashboard_asset_text(client)
    css = (await client.get("/static/dashboard-theme.css")).text + (await client.get("/static/dashboard.css")).text

    # 1. Zero-FOUC inline head script
    assert "omniscrobble_theme" in html
    assert "omniscrobble_accent" in html
    assert "document.documentElement.classList.add('theme-' + theme)" in html
    assert "document.documentElement.classList.add('accent-' + accent)" in html

    # 2. Curated Theme Palettes (8 themes)
    for theme in [
        "theme-slate",
        "theme-oled",
        "theme-nord",
        "theme-catppuccin",
        "theme-tokyonight",
        "theme-dracula",
        "theme-emerald",
        "theme-rosepine",
    ]:
        assert f"html.{theme}" in css

    # 3. Accent Highlight Classes (9 accents)
    for accent in [
        "accent-sky",
        "accent-amber",
        "accent-trakt",
        "accent-plex",
        "accent-jellyfin",
        "accent-emerald",
        "accent-cyan",
        "accent-rose",
        "accent-mauve",
    ]:
        assert f"html.{accent}" in css

    # 4. Header theme trigger button
    assert 'id="theme-toggle-btn"' in html
    assert "openThemeModal()" in html
    assert "theme-toggle" in html
    assert 'id="theme-btn-label"' in html

    # 5. Dedicated Modals: #theme-modal and #shortcuts-modal
    assert 'id="theme-modal"' in html
    assert 'id="theme-modal-palette-grid"' in html
    assert 'id="theme-modal-accent-grid"' in html
    assert 'id="shortcuts-modal"' in html
    assert "Cycle Theme Palette" in html
    assert "Refresh Activity &amp; Queue" in html

    # 6. Settings Hub Appearance Tab
    assert 'id="settings-tab-btn-appearance"' in html
    assert "switchSettingsTab('appearance')" in html
    assert 'id="settings-panel-appearance"' in html
    assert 'id="settings-theme-grid"' in html
    assert 'id="settings-accent-grid"' in html

    # 7. JavaScript theme engine functions
    assert "function setTheme(themeId)" in html
    assert "function setAccent(accentId)" in html
    assert "function cycleTheme()" in html
    assert "function toggleTheme()" in html
    assert "function updateThemeUI()" in html
    assert "function renderThemePickers()" in html
    assert "openThemeModal()" in html
    assert "closeThemeModal()" in html
    assert "openShortcutsModal()" in html
    assert "closeShortcutsModal()" in html

    # 8. Accent configuration defaults & reactive switching
    assert "localStorage.getItem('omniscrobble_accent') || 'sky'" in html
    assert "--accent-color: #0284c7;" in css
    assert "root.classList.add('accent-' + accentId);" in html

@pytest.mark.asyncio(loop_scope="module")
async def test_ambient_visuals_compact_mode_and_card_visibility():
    """Verify ambient stream poster & backdrop blur, compact density mode, and card visibility controls."""
    from app.plex_parser import parse_plex_webhook
    from app.jellyfin_parser import parse_jellyfin_webhook
    from app.emby_parser import parse_emby_webhook
    from app.services.dashboard_renderer import DashboardRenderer

    # 1. Artwork extraction in Media Server Parsers
    # Plex with direct thumb/art
    plex_payload = {
        "event": "media.play",
        "Account": {"title": "selits"},
        "Server": {"title": "PlexServer"},
        "Player": {"title": "Living Room TV"},
        "Metadata": {
            "type": "movie",
            "title": "Inception",
            "year": 2010,
            "duration": 8880000,
            "viewOffset": 4440000,
            "Guid": [{"id": "imdb://tt1375666"}],
            "thumb": "https://custom-art.com/poster.jpg",
            "art": "https://custom-art.com/fanart.jpg",
        },
    }
    parsed_plex = parse_plex_webhook(plex_payload)
    assert parsed_plex is not None
    assert parsed_plex.poster_url == "https://custom-art.com/poster.jpg"
    assert parsed_plex.backdrop_url == "https://custom-art.com/fanart.jpg"

    # Plex fallback to Metahub CDN using IMDb ID
    plex_meta = {
        "event": "media.play",
        "Account": {"title": "selits"},
        "Server": {"title": "PlexServer"},
        "Player": {"title": "Living Room TV"},
        "Metadata": {
            "type": "movie",
            "title": "Inception",
            "year": 2010,
            "duration": 8880000,
            "viewOffset": 4440000,
            "Guid": [{"id": "imdb://tt1375666"}],
        },
    }
    parsed_meta = parse_plex_webhook(plex_meta)
    assert parsed_meta is not None
    assert parsed_meta.poster_url == "https://images.metahub.space/poster/medium/tt1375666/img"
    assert parsed_meta.backdrop_url == "https://images.metahub.space/background/medium/tt1375666/img"

    # Jellyfin artwork extraction & Metahub fallback
    jf_payload = {
        "NotificationType": "PlaybackStart",
        "NotificationUsername": "selits",
        "ItemType": "Movie",
        "Name": "Dune",
        "Year": 2021,
        "RunTimeTicks": 1000000000,
        "PlaybackPositionTicks": 500000000,
        "ProviderIds": {"Imdb": "tt1160419"},
    }
    parsed_jf = parse_jellyfin_webhook(jf_payload)
    assert parsed_jf is not None
    assert parsed_jf.poster_url == "https://images.metahub.space/poster/medium/tt1160419/img"
    assert parsed_jf.backdrop_url == "https://images.metahub.space/background/medium/tt1160419/img"

    # Emby artwork extraction & Metahub fallback
    emby_payload = {
        "Event": "playback.start",
        "User": {"Name": "selits"},
        "Item": {
            "Type": "Movie",
            "Name": "Interstellar",
            "ProductionYear": 2014,
            "RunTimeTicks": 1000000000,
            "ProviderIds": {"Imdb": "tt0816692"},
        },
        "PlaybackInfo": {"PositionTicks": 500000000},
    }
    parsed_emby = parse_emby_webhook(emby_payload)
    assert parsed_emby is not None
    assert parsed_emby.poster_url == "https://images.metahub.space/poster/medium/tt0816692/img"
    assert parsed_emby.backdrop_url == "https://images.metahub.space/background/medium/tt0816692/img"

    # 2. Active Playback Banner with Ambient Backdrop & Poster Thumbnail
    active_session = {
        "title": "Severance - S01E01 - Good News About Hell",
        "username": "selits",
        "player": "Shield TV",
        "device": "Android TV",
        "progress": 42.5,
        "state": "playing",
        "trakt_url": "https://trakt.tv/shows/severance",
        "remaining_str": "32m left",
        "poster_url": "https://images.metahub.space/poster/medium/tt11280740/img",
        "backdrop_url": "https://images.metahub.space/background/medium/tt11280740/img",
    }
    card_html = DashboardRenderer.render_active_playback_card([active_session], None, is_admin=True)
    assert 'id="active-playback-card"' in card_html
    assert 'id="stream-ambient-backdrop"' in card_html
    assert "https://images.metahub.space/background/medium/tt11280740/img" in card_html
    assert 'id="stream-poster-container"' in card_html
    assert 'id="stream-poster-img"' in card_html
    assert "https://images.metahub.space/poster/medium/tt11280740/img" in card_html
    assert 'id="stream-poster-fallback"' in card_html

    # Fallback when poster/backdrop is empty
    empty_session = {
        "title": "Unknown Home Video",
        "username": "selits",
        "player": "Web",
        "device": "Chrome",
        "progress": 10.0,
        "state": "playing",
        "trakt_url": "https://trakt.tv",
    }
    fallback_html = DashboardRenderer.render_active_playback_card([empty_session], None, is_admin=True)
    assert 'id="active-playback-card"' in fallback_html
    assert 'id="stream-poster-fallback"' in fallback_html
    assert "radial-gradient" in fallback_html

    # 3. Client Dashboard HTML & UI Components
    client = make_async_test_client(app)
    res = await client.get("/")
    assert res.status_code == 200
    html = res.text + await dashboard_asset_text(client)
    css = (await client.get("/static/dashboard-theme.css")).text + (await client.get("/static/dashboard.css")).text

    # Ambient Backdrop elements in template
    assert 'id="stream-ambient-backdrop"' in html
    assert 'id="stream-poster-img"' in html
    assert 'id="stream-poster-fallback"' in html

    # Compact Density Mode CSS and controls
    assert "html.density-compact" in css
    assert 'id="density-btn-comfortable"' in html
    assert 'id="density-btn-compact"' in html
    assert "setDensity('comfortable')" in html
    assert "setDensity('compact')" in html

    # Unique Dashboard Card IDs
    for card_id in [
        "active-playback-card",
        "card-server-config",
        "card-ecosystem",
        "card-multi-tracker",
        "card-cowatch",
        "card-reconciliation",
        "card-arr-bridge",
        "card-backup",
        "card-activity",
    ]:
        assert f'id="{card_id}"' in html

    # Dashboard Card Visibility controls in Settings Hub
    assert 'id="settings-card-visibility-grid"' in html
    assert "resetCardVisibility()" in html

    # JavaScript Density & Card Visibility Functions
    assert "function getDensity()" in html
    assert "function setDensity(mode)" in html
    assert "function updateDensityUI()" in html
    assert "const DASHBOARD_CARDS =" in html
    assert "function getHiddenCards()" in html
    assert "function setCardVisibility(cardId, isVisible)" in html
    assert "function resetCardVisibility()" in html
    assert "function applyCardVisibility()" in html
    assert "function renderCardVisibilityPickers()" in html
    assert "function initAppearance()" in html

