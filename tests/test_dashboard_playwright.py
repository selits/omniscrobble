"""Real-browser dashboard layout and keyboard interaction checks."""

import asyncio
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from playwright.async_api import Browser, Error as PlaywrightError, Page, Route, async_playwright

from app import main as dashboard_main
from app.main import app
from app.services.watch_list_manager import WatchListManager
from app.services.automation_rules import AutomationRulesManager
from app.services.dashboard_auth_manager import DashboardAuthManager


@pytest_asyncio.fixture(params=("chromium", "firefox", "webkit"), ids=("chromium", "firefox", "webkit"))
async def browser(request: pytest.FixtureRequest) -> Browser:
    """Launch each installed Playwright engine on pytest's asyncio loop."""
    async with async_playwright() as playwright:
        engine_name = request.param
        engine = getattr(playwright, engine_name)
        launch_options = {}
        if not Path(engine.executable_path).is_file():
            pytest.skip(f"Playwright {engine_name} browser is not installed")
        if engine_name == "chromium":
            launch_options["args"] = ["--disable-breakpad", "--disable-crash-reporter"]
        try:
            launched_browser = await engine.launch(**launch_options)
        except PlaywrightError as error:
            if "Host system is missing dependencies to run browsers" in str(error):
                pytest.skip(f"Playwright {engine_name} host dependencies are unavailable")
            if engine_name == "chromium" and "setsockopt: Operation not permitted" in str(error):
                pytest.skip("System Chromium crash reporting is blocked by this host's sandbox")
            raise
        yield launched_browser
        await launched_browser.close()


@pytest_asyncio.fixture
async def dashboard_page(browser: Browser) -> Page:
    """Load the rendered demo and assets while isolating API calls from user data."""
    client = TestClient(app)
    page = await browser.new_page(viewport={"width": 390, "height": 844})

    async def serve_dashboard(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.hostname != "dashboard.test":
            await route.abort()
            return

        path = parsed.path
        if path == "/demo" or path.startswith("/static/") or path == "/manifest.json":
            response = client.get(path + (f"?{parsed.query}" if parsed.query else ""))
            await route.fulfill(
                status=response.status_code,
                content_type=response.headers.get("content-type", "text/plain"),
                body=response.content,
            )
            return

        if path.startswith("/api/"):
            response = client.request(
                route.request.method,
                path + (f"?{parsed.query}" if parsed.query else ""),
                content=route.request.post_data,
                headers={"content-type": route.request.headers.get("content-type", "application/json")},
            )
            await route.fulfill(
                status=response.status_code,
                content_type=response.headers.get("content-type", "application/json"),
                body=response.content,
            )
            return

        await route.fulfill(status=204, body="")

    await page.route("**/*", serve_dashboard)
    await page.goto("http://dashboard.test/demo", wait_until="domcontentloaded")
    await page.locator("#workspace-nav").wait_for(state="visible")
    await page.locator("#events-tbody .activity-row").first.wait_for(state="visible")
    yield page
    await page.close()
    client.close()


@pytest_asyncio.fixture
async def admin_dashboard_page(browser: Browser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Page:
    """Use admin UI with an isolated watch-list file for write-flow coverage."""
    monkeypatch.setattr(dashboard_main, "watch_list_mgr", WatchListManager(tmp_path / "watch-lists.json"))
    client = TestClient(app)
    page = await browser.new_page(viewport={"width": 1280, "height": 900})

    async def serve_dashboard(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.hostname != "dashboard.test":
            await route.abort()
            return
        path = parsed.path
        query = f"?{parsed.query}" if parsed.query else ""
        headers = {
            "content-type": route.request.headers.get("content-type", "application/json"),
            "x-webhook-secret": route.request.headers.get("x-webhook-secret", ""),
        }
        if path == "/" or path.startswith("/static/") or path == "/manifest.json":
            response = client.get(path + query, headers=headers)
        elif path.startswith("/api/"):
            response = client.request(route.request.method, path + query, content=route.request.post_data, headers=headers)
        else:
            await route.fulfill(status=204, body="")
            return
        await route.fulfill(status=response.status_code, content_type=response.headers.get("content-type", "text/plain"), body=response.content)

    await page.route("**/*", serve_dashboard)
    await page.set_extra_http_headers({"x-webhook-secret": dashboard_main.Config.WEBHOOK_SECRET or ""})
    await page.goto("http://dashboard.test/", wait_until="domcontentloaded")
    await page.locator("#workspace-nav").wait_for(state="visible")
    await page.locator("#watch-list-select").wait_for(state="attached")
    yield page
    await page.close()
    client.close()


@pytest.mark.parametrize(
    ("width", "height", "mobile"),
    [
        (320, 740, True),
        (390, 844, True),
        (768, 1024, False),
        (1440, 1000, False),
    ],
)
@pytest.mark.asyncio(loop_scope="function")
async def test_dashboard_layout_at_common_viewports(
    dashboard_page: Page, width: int, height: int, mobile: bool
) -> None:
    """Keep the main workspace and activity content inside the viewport."""
    page = dashboard_page
    await page.set_viewport_size({"width": width, "height": height})
    await page.reload(wait_until="domcontentloaded")
    await page.locator("#events-tbody .activity-row").first.wait_for(state="visible")

    for selector in (".health-strip", "#workspace-nav", "#view-operations", "#card-activity"):
        bounds = await page.locator(selector).bounding_box()
        assert bounds is not None, f"{selector} should be visible at {width}px"
        assert bounds["x"] >= -1, f"{selector} starts outside the viewport at {width}px"
        assert bounds["x"] + bounds["width"] <= width + 1, f"{selector} overflows at {width}px"

    activity_row = page.locator("#events-tbody .activity-row").first
    row_bounds = await activity_row.bounding_box()
    assert row_bounds is not None
    assert row_bounds["x"] + row_bounds["width"] <= width + 1

    if width <= 900:
        assert await activity_row.evaluate("element => getComputedStyle(element).display") == "flex"
        assert await activity_row.evaluate("element => getComputedStyle(element).flexDirection") == "column"
        for cell in await activity_row.locator("td[data-label]").all():
            assert await cell.evaluate("element => getComputedStyle(element, '::before').content") != 'none'
            bounds = await cell.bounding_box()
            assert bounds is not None
            assert bounds["width"] >= row_bounds["width"] - 28
    else:
        assert await activity_row.evaluate("element => getComputedStyle(element).display") == "table-row"

    if mobile:
        nav_position = await page.locator("#workspace-nav").evaluate("element => getComputedStyle(element).position")
        assert nav_position == "fixed"
        nav_button = await page.locator("#tab-operations").bounding_box()
        assert nav_button["height"] >= 44


@pytest.mark.parametrize("width", [320, 390, 768])
@pytest.mark.asyncio(loop_scope="function")
async def test_mobile_activity_fields_do_not_overlap_and_controls_have_consistent_height(
    dashboard_page: Page, width: int
) -> None:
    """Keep long activity values and touch controls readable on narrow phones."""
    page = dashboard_page
    await page.set_viewport_size({"width": width, "height": 844})
    await page.evaluate("""() => {
        const row = document.createElement('tr');
        row.className = 'activity-row';
        row.innerHTML = `
            <td class="activity-time" data-label="When">2026-10-09 04:13:35</td>
            <td class="activity-title" data-label="Title">A long sample title for a phone width</td>
            <td data-label="Type"><span class="activity-type">episode</span></td>
            <td class="activity-user" data-label="User"><div class="activity-user-content"><span class="activity-server-badge">Plex</span><span>selits</span></div></td>
            <td data-label="Action"><span class="activity-action">pause (100.0%) with an intentionally long action label</span></td>
            <td data-label="Status"><div class="activity-status-group"><span class="activity-status-badge activity-status-failed">Failed</span><span class="tracker-delivery-badges"><span class="tracker-delivery-badge tracker-delivery-failed">TRK ×</span><span class="tracker-delivery-badge tracker-delivery-success">SKL ✓</span></span></div></td>
            <td class="activity-actions-cell"><div class="activity-row-actions"><button class="btn-sm activity-row-button">Details</button><button class="btn-sm activity-row-button">Retry</button></div></td>`;
        document.querySelector('#events-tbody').appendChild(row);
    }""")

    measurements = await page.locator("#events-tbody .activity-row").last.evaluate("""row => {
        const rect = element => {
            const box = element.getBoundingClientRect();
            return {x: box.x, y: box.y, right: box.right, bottom: box.bottom, width: box.width, height: box.height};
        };
        const fields = [...row.querySelectorAll('td[data-label]')]
            .filter(cell => ['Type', 'User', 'Action', 'Status'].includes(cell.dataset.label))
            .map(cell => ({cell: rect(cell), content: rect(cell.querySelector('.activity-type, .activity-user-content, .activity-action, .activity-status-group'))}));
        const fieldCells = [...row.querySelectorAll('td[data-label]')].map(rect).sort((a, b) => a.y - b.y);
        const actionButtons = [...document.querySelectorAll('.activity-actions > .btn-sm')].map(button => {
            const box = button.getBoundingClientRect();
            return {width: Math.round(box.width), height: Math.round(box.height)};
        });
        const filterButtonHeights = [...document.querySelectorAll('.activity-filter-chip')]
            .map(button => Math.round(button.getBoundingClientRect().height));
        return {row: rect(row), fields, fieldCells, actionButtons, filterButtonHeights};
    }""")

    assert measurements["row"]["width"] <= width
    for field in measurements["fields"]:
        assert field["cell"]["width"] >= measurements["row"]["width"] - 28
        assert field["content"]["x"] >= field["cell"]["x"] + 60
        assert field["content"]["right"] <= field["cell"]["right"] + 1
    for previous, current in zip(measurements["fieldCells"], measurements["fieldCells"][1:]):
        assert current["y"] >= previous["bottom"] - 1
    if width <= 390:
        assert measurements["actionButtons"] and min(button["height"] for button in measurements["actionButtons"]) >= 40
        assert len({button["width"] for button in measurements["actionButtons"]}) == 1
        assert measurements["filterButtonHeights"] and min(measurements["filterButtonHeights"]) >= 40
        assert len(set(measurements["filterButtonHeights"])) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_mobile_emulation_keeps_activity_content_inside_cells(browser: Browser) -> None:
    """Exercise responsive activity layout with Chromium's mobile viewport behavior."""
    if browser.browser_type.name != "chromium":
        pytest.skip("mobile emulation is covered with Chromium")

    context = await browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
    )
    page = await context.new_page()
    client = TestClient(app)

    async def serve_dashboard(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.hostname != "dashboard.test":
            await route.abort()
            return
        if parsed.path == "/demo" or parsed.path.startswith("/static/") or parsed.path == "/manifest.json":
            response = client.get(parsed.path + (f"?{parsed.query}" if parsed.query else ""))
            await route.fulfill(
                status=response.status_code,
                content_type=response.headers.get("content-type", "text/plain"),
                body=response.content,
            )
            return
        await route.fulfill(status=204, body="")

    await page.route("**/*", serve_dashboard)
    await page.goto("http://dashboard.test/demo", wait_until="domcontentloaded")
    row = page.locator("#events-tbody .activity-row").first
    await row.wait_for(state="visible")
    assert await page.evaluate("window.matchMedia('(max-width: 640px)').matches")
    assert await row.evaluate("element => getComputedStyle(element).flexDirection") == "column"
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

    await context.close()
    client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_workspace_tabs_support_arrow_keys_and_health_shortcuts(dashboard_page: Page) -> None:
    """Arrow keys switch tabs, and health controls open their linked workspace."""
    page = dashboard_page
    operations = page.locator("#tab-operations")
    await operations.focus()
    await page.keyboard.press("ArrowRight")
    assert await page.locator("#tab-watch-lists").get_attribute("aria-selected") == "true"
    assert await page.locator("#view-watch-lists").is_visible()

    await page.locator("#tab-operations").focus()
    await page.keyboard.press("End")
    assert await page.locator("#tab-diagnostics").get_attribute("aria-selected") == "true"

    await page.locator(".health-pill-action").filter(has_text="Queue").click()
    assert await page.locator("#tab-operations").get_attribute("aria-selected") == "true"
    assert await page.locator("#view-operations").is_visible()


@pytest.mark.asyncio(loop_scope="function")
async def test_automation_workspace_saves_rules_and_previews_sample_events(
    admin_dashboard_page: Page, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admins can draft, save, and evaluate ordered event rules in the workspace."""
    monkeypatch.setattr(dashboard_main, "automation_rules_mgr", AutomationRulesManager(tmp_path / "automation-rules.json"))
    page = admin_dashboard_page
    await page.locator("#tab-automation").click()
    await page.locator("#automation-rules-status").wait_for(state="visible")
    await page.locator("#automation-rule-name").fill("Kids movie hold")
    await page.locator("#automation-rule-priority").fill("10")
    await page.locator("#automation-rule-action").select_option("suppress")
    await page.locator("#automation-rule-servers").fill("plex")
    await page.locator("#automation-rule-libraries").fill("Kids")
    await page.locator("#automation-rule-media-types").fill("movie")
    await page.locator("#automation-rules-card details summary").click()
    await page.locator("#automation-sample-server").fill("plex")
    await page.locator("#automation-sample-library").fill("Kids")
    await page.locator("#automation-rules-card button").filter(has_text="Add rule").click()
    assert "Kids movie hold" in await page.locator("#automation-rules-list").inner_text()
    await page.locator("#automation-rules-card button").filter(has_text="Preview unsaved rules").click()
    await page.wait_for_function("document.querySelector('#automation-rules-preview-result').textContent.includes('suppress')")
    await page.locator("#automation-rules-card button").filter(has_text="Save rules").click()
    await page.wait_for_function("document.querySelector('#automation-rules-status').textContent.includes('saved')")
    await page.locator("#automation-rules-card button").filter(has_text="Evaluate saved rules").click()
    await page.wait_for_function("document.querySelector('#automation-rules-preview-result').textContent.includes('suppress')")


@pytest.mark.asyncio(loop_scope="function")
async def test_settings_modal_traps_and_returns_keyboard_focus(dashboard_page: Page) -> None:
    """Keep keyboard focus in settings while open and restore its opener on close."""
    page = dashboard_page
    opener = page.locator('button[onclick="openSettingsModal()"]')
    await opener.click()

    dialog = page.locator("#settings-modal .settings-hub-dialog")
    await dialog.wait_for(state="visible")
    assert await dialog.get_attribute("aria-modal") == "true"
    labelled_by = await dialog.get_attribute("aria-labelledby")
    assert labelled_by and await page.locator(f"#{labelled_by}").count() == 1

    focusable = dialog.locator(
        'a[href], button:not([disabled]), input:not([disabled]), '
        'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
    visible_focusable_indices = await focusable.evaluate_all(
        "elements => elements.flatMap((element, index) => element.getClientRects().length ? [index] : [])"
    )
    assert visible_focusable_indices
    first = focusable.first
    last = focusable.nth(visible_focusable_indices[-1])
    await first.focus()
    await page.keyboard.press("Shift+Tab")
    assert await last.evaluate("element => element === document.activeElement")

    await page.keyboard.press("Escape")
    await page.wait_for_function("getComputedStyle(document.querySelector('#settings-modal')).display === 'none'")
    await page.wait_for_function("document.activeElement === document.querySelector('button[onclick=\"openSettingsModal()\"]')")
    assert await opener.evaluate("element => element === document.activeElement")


@pytest.mark.asyncio(loop_scope="function")
async def test_settings_modal_fits_short_landscape_viewport(dashboard_page: Page) -> None:
    """The settings content remains within a short phone viewport."""
    page = dashboard_page
    await page.set_viewport_size({"width": 568, "height": 360})
    await page.locator('button[onclick="openSettingsModal()"]').click()
    dialog = page.locator("#settings-modal .settings-hub-dialog")
    await dialog.wait_for(state="visible")
    bounds = await dialog.bounding_box()
    assert bounds is not None
    assert bounds["y"] >= 0
    assert bounds["y"] + bounds["height"] <= 360
    assert await page.locator("#settings-modal .settings-hub-content").evaluate(
        "element => element.scrollHeight >= element.clientHeight"
    )


@pytest.mark.parametrize("width", [320, 360, 390])
@pytest.mark.asyncio(loop_scope="function")
async def test_settings_footer_stays_within_narrow_mobile_viewport(dashboard_page: Page, width: int) -> None:
    """Keep settings guidance and save controls visible without horizontal overflow."""
    page = dashboard_page
    await page.set_viewport_size({"width": width, "height": 800})
    await page.locator('button[onclick="openSettingsModal()"]').click()

    dialog = page.locator("#settings-modal .settings-hub-dialog")
    footer = page.locator("#settings-modal .settings-hub-footer")
    await dialog.wait_for(state="visible")
    await footer.wait_for(state="visible")
    assert await footer.evaluate(
        "element => element.parentElement.classList.contains('settings-hub-dialog')"
    ), "the settings footer must remain inside the dialog"
    nav_tabs = page.locator("#settings-nav-tabs")
    nav = await nav_tabs.evaluate("element => ({clientWidth: element.clientWidth, scrollWidth: element.scrollWidth})")
    assert nav["scrollWidth"] > nav["clientWidth"], "mobile settings tabs should scroll horizontally"
    tab_tops = await nav_tabs.locator(".settings-nav-tab").evaluate_all(
        "elements => elements.map(element => element.getBoundingClientRect().top)"
    )
    assert len(set(tab_tops)) == 1, "mobile settings tabs should stay on one row"

    content_height = await page.locator("#settings-modal .settings-hub-content").evaluate(
        "element => element.clientHeight"
    )
    assert content_height >= 180, f"settings content area is too short at {width}px: {content_height}px"

    close_button = footer.locator("button[onclick=\"closeSettingsModal()\"]")
    save_button = footer.locator("#settings-save-all-btn")
    close_bounds = await close_button.bounding_box()
    save_bounds = await save_button.bounding_box()
    assert close_bounds and save_bounds
    assert abs(close_bounds["y"] - save_bounds["y"]) <= 1, "mobile footer actions should share a row"

    for name, locator in (
        ("dialog", dialog),
        ("footer", footer),
        ("hint", footer.locator(".settings-save-hint")),
        ("buttons", footer.locator("button")),
    ):
        for bounds in await locator.evaluate_all(
            "elements => elements.map(element => { const r = element.getBoundingClientRect(); "
            "return {left: r.left, right: r.right, width: r.width}; })"
        ):
            assert bounds["left"] >= -1, f"{name} starts outside viewport at {width}px: {bounds}"
            assert bounds["right"] <= width + 1, f"{name} overflows viewport at {width}px: {bounds}"

    assert await footer.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.asyncio(loop_scope="function")
async def test_trakt_auth_page_sends_csrf_header_for_start_and_poll(
    browser: Browser,
) -> None:
    """The browser auth flow includes the CSRF header required by admin cookies."""
    csrf_token = "playwright-csrf-token"
    page = await browser.new_page(viewport={"width": 390, "height": 844})
    await page.context.add_cookies([
        {"name": "csrf_token", "value": csrf_token, "url": "http://auth.test"},
    ])
    observed_headers: dict[str, str] = {}
    poll_request_seen = asyncio.Event()

    async def serve_auth(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        headers = {
            "x-csrf-token": request.headers.get("x-csrf-token", ""),
        }
        if parsed.path == "/auth":
            auth_html = (
                dashboard_main.AUTH_HTML
                .replace("{{PAGE_TITLE}}", "Link Trakt Account")
                .replace("{{H1_TEXT}}", "Link Trakt Account")
                .replace(
                    "{{P_DESC}}",
                    "Authorize this scrobbler to record playback and sync history with your Trakt profile.",
                )
                .replace("{{ALREADY_CONNECTED_BANNER}}", "")
                .replace("{{TARGET_UNAME}}", "")
            )
            await route.fulfill(status=200, content_type="text/html", body=auth_html)
            return
        elif parsed.path == "/api/auth/start":
            observed_headers[parsed.path] = headers["x-csrf-token"]
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"device_code":"test-device-code","user_code":"ABCD1234",'
                    '"verification_url":"https://trakt.tv/activate","expires_in":600,"interval":1}'
                ),
            )
            return
        elif parsed.path == "/api/auth/poll":
            observed_headers[parsed.path] = headers["x-csrf-token"]
            poll_request_seen.set()
            await route.fulfill(status=200, content_type="application/json", body='{"status":"pending"}')
            return
        else:
            await route.fulfill(status=204, body="")
            return
        await route.fulfill(
            status=response.status_code,
            content_type=response.headers.get("content-type", "text/html"),
            body=response.content,
        )

    await page.route("**/*", serve_auth)
    try:
        await page.goto("http://auth.test/auth", wait_until="domcontentloaded")
        await page.get_by_text("ABCD1234").wait_for(state="visible")
        await asyncio.wait_for(poll_request_seen.wait(), timeout=7)
        assert observed_headers["/api/auth/start"] == csrf_token
        assert observed_headers["/api/auth/poll"] == csrf_token
    finally:
        await page.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_activity_filters_search_and_setup_checklist(dashboard_page: Page) -> None:
    """Activity filters narrow visible results and setup links open the right workspace."""
    page = dashboard_page
    checklist = page.locator("#setup-checklist")
    assert await checklist.locator("li").count() == 3
    assert await page.locator("#setup-checklist-progress").inner_text() == "3 of 3 complete"
    assert await checklist.is_hidden()
    await page.locator("#activity-search").fill("not-a-real-title")
    assert await page.locator("#events-tbody .activity-row").count() == 0
    await page.locator("#activity-search").fill("")
    await page.locator('.activity-filter-chip[onclick*="setActivityFilter(\'status\', \'failed\'"]').click()
    assert await page.locator('.activity-filter-chip[onclick*="setActivityFilter(\'status\', \'failed\'"]').get_attribute("aria-pressed") == "true"

    await page.wait_for_function("Array.isArray(allEvents) && allEvents.length > 0")
    await page.evaluate("""() => {
        document.querySelector('.system-token-health')?.classList.remove('is-healthy');
        const item = Array.from(document.querySelectorAll('#hub-trackers-grid .hub-tracker-item'))
            .find(candidate => !/Trakt/i.test(candidate.firstElementChild?.textContent || ''));
        const badge = item?.querySelector(':scope > div:last-child > span:first-child');
        if (!badge) throw new Error('Expected a non-Trakt tracker status badge');
        badge.textContent = '● Active';
        updateSetupChecklist();
    }""")
    assert "is-complete" in (await page.locator('[data-setup-step="tracker"]').get_attribute("class"))

    await page.evaluate("""() => {
        allEvents = [];
        renderRows(allEvents);
    }""")
    assert await page.locator("#setup-checklist-progress").inner_text() == "2 of 3 complete"
    assert await checklist.is_visible()
    await page.evaluate("""() => {
        allEvents.push({timestamp:'2099-01-01T00:00:00Z', title:'First successful scrobble', action:'mark_watched', result_status:'ok', type:'movie', user:'demo', server:'plex'});
        renderRows(allEvents);
    }""")
    assert await page.locator("#setup-checklist-progress").inner_text() == "3 of 3 complete"
    assert await checklist.is_hidden()


@pytest.mark.asyncio(loop_scope="function")
async def test_sync_detail_shows_destination_reason_and_attempt_history(admin_dashboard_page: Page) -> None:
    """The activity detail panel presents the persisted destination outcome in the browser."""
    page = admin_dashboard_page
    await page.wait_for_function("Array.isArray(allEvents)")
    await page.evaluate("""() => {
        allEvents = [{
            event_id: 'browser-sync-detail', operation_id: 'browser-sync-detail',
            timestamp: '2099-01-01T00:00:00Z', title: 'Sync detail fixture',
            action: 'mark_watched', result_status: 'partial', type: 'movie', user: 'demo', server: 'plex',
            event: 'media.scrobble', tracker_delivery: {trakt: 'failed'},
            tracker_delivery_details: {trakt: {
                state: 'failed', category: 'transient', reason: 'Temporary connection timeout', attempts: 2,
                history: [
                    {state: 'queued', category: 'queued', reason: 'Saved for retry'},
                    {state: 'failed', category: 'transient', reason: 'Temporary connection timeout'},
                ],
            }},
        }];
        renderRows(allEvents);
    }""")
    details_button = page.locator('[data-event-detail-toggle="browser-sync-detail"]')
    await details_button.click()
    detail_panel = page.locator("#event-details-browser-sync-detail")
    assert await detail_panel.is_visible()
    panel_text = await detail_panel.inner_text()
    assert "trakt" in panel_text.lower()
    assert "Temporary failure" in panel_text
    assert "Temporary connection timeout" in panel_text
    assert "2 attempts" in panel_text
    assert "Saved for retry" in panel_text


@pytest.mark.asyncio(loop_scope="function")
async def test_guided_diagnostics_renders_checks_and_connection_test(admin_dashboard_page: Page) -> None:
    """The System workspace renders read-only findings and an explicit, stubbed test action."""
    page = admin_dashboard_page
    recovery_requested = asyncio.Event()
    connection_test_requested = asyncio.Event()

    async def stub_recovery(route: Route) -> None:
        recovery_requested.set()
        await route.fulfill(status=200, content_type="application/json", body='''{
            "read_only": true,
            "checked_at": "2099-01-01T00:00:00+00:00",
            "checks": [{
                "id": "configuration", "label": "Configuration", "status": "warning",
                "summary": "Review configured integrations.", "next_action": "trackers",
                "last_checked": "2099-01-01T00:00:00+00:00", "test_integrations": ["plex"],
                "findings": [{"severity": "warning", "message": "Plex is configured but disabled."}],
                "effective_settings": [{"name": "movie threshold", "value": "90%", "source": "runtime settings"}]
            }]
        }''')

    async def stub_connection_test(route: Route) -> None:
        connection_test_requested.set()
        assert route.request.post_data_json == {"integration": "plex"}
        await route.fulfill(status=200, content_type="application/json", body='''{
            "status": "healthy", "message": "Connection succeeded.",
            "checked_at": "2099-01-01T00:00:01+00:00"
        }''')

    await page.route("**/api/health/recovery", stub_recovery)
    await page.route("**/api/health/recovery/test", stub_connection_test)
    await page.locator("#tab-diagnostics").click()
    await asyncio.wait_for(recovery_requested.wait(), timeout=5)
    check = page.locator("#recovery-checks-list .recovery-check-warning")
    await check.wait_for(state="visible")
    assert "Plex is configured but disabled." in await check.inner_text()
    await check.locator("details summary").click()
    assert "movie threshold: 90% (from runtime settings)" in await check.inner_text()

    await check.get_by_role("button", name="Test Plex").click()
    await asyncio.wait_for(connection_test_requested.wait(), timeout=5)
    result = check.locator(".recovery-test-result")
    await result.get_by_text("plex: Connection succeeded.").wait_for()
    assert await result.get_attribute("class") == "recovery-test-result recovery-test-healthy"


@pytest.mark.asyncio(loop_scope="function")
async def test_reconciliation_preview_confirmation_applies_preview_id(admin_dashboard_page: Page) -> None:
    """The browser confirms a preview summary and submits its preview ID for apply."""
    page = admin_dashboard_page
    preview_requested = asyncio.Event()
    apply_requested = asyncio.Event()
    apply_payload: dict = {}
    dialog_messages: list[str] = []

    async def stub_diff(route: Route) -> None:
        await route.fulfill(status=200, content_type="application/json", body='''{
            "diff": [{
                "id": "movie-123", "title": "Careful Match", "year": 2024, "type": "movie",
                "status": "trakt_only", "trakt_watched": true, "server_watched": false,
                "server": "plex", "action_recommended": "mark_watched_on_server"
            }]
        }''')

    async def stub_preview(route: Route) -> None:
        preview_requested.set()
        assert route.request.post_data_json == {"direction": "all", "server": "plex"}
        await route.fulfill(status=200, content_type="application/json", body='''{
            "preview_id": "browser-preview-42", "total": 1,
            "actions": {"mark_server_watched": 1},
            "warnings": [{"title": "Careful Match"}], "items": []
        }''')

    async def stub_apply(route: Route) -> None:
        apply_requested.set()
        apply_payload.update(route.request.post_data_json)
        await route.fulfill(status=200, content_type="application/json", body='''{
            "reconciled": 1, "failed": 0, "results": []
        }''')

    async def accept_confirmation(dialog) -> None:
        dialog_messages.append(dialog.message)
        await dialog.accept()

    await page.route("**/api/sync/diff**", stub_diff)
    await page.route("**/api/sync/reconcile/preview", stub_preview)
    await page.route("**/api/sync/reconcile", stub_apply)
    page.on("dialog", accept_confirmation)

    await page.locator("#tab-trackers").click()
    await page.evaluate("openReconcileModal(true)")
    await page.locator("#reconcile-modal").wait_for(state="visible")
    await page.get_by_role("button", name="⚡ Reconcile All").click()
    await asyncio.wait_for(preview_requested.wait(), timeout=5)
    await asyncio.wait_for(apply_requested.wait(), timeout=5)

    assert len(dialog_messages) == 2
    assert "Careful Match" in dialog_messages[1]
    assert "title matching" in dialog_messages[1]
    assert apply_payload == {"preview_id": "browser-preview-42", "server": "plex"}
    await page.get_by_text("Reconciliation completed: 1 succeeded, 0 failed.").wait_for()


@pytest.mark.asyncio(loop_scope="function")
async def test_cross_tracker_modal_shows_tmdb_match_and_source(admin_dashboard_page: Page) -> None:
    """Expose exact TMDb matching and the selected source of truth in the diff UI."""
    page = admin_dashboard_page
    diff = [{
        "id": "diff_rating_trakt_to_tmdb_movie_438631",
        "direction": "trakt_to_tmdb",
        "sync_type": "rating",
        "media_type": "movie",
        "title": "Dune",
        "year": 2021,
        "source_status": "9/10",
        "target_status": "7/10",
        "match_reason": "Matched by shared TMDb ID",
    }]

    async def stub_api(route: Route) -> None:
        if "/api/cross-sync/status" in route.request.url:
            await route.fulfill(json={"configured": True, "tmdb_authenticated": True, "trakt_authenticated": True, "simkl_authenticated": False})
        elif "/api/cross-sync/diff" in route.request.url:
            await route.fulfill(json={"diff": diff})
        else:
            await route.continue_()

    await page.route("**/api/cross-sync/**", stub_api)
    await page.evaluate("openCrossSyncModal()")
    await page.locator("#cross-sync-modal").wait_for(state="visible")
    policy = page.locator("#cross-sync-conflict-policy")
    assert await policy.input_value() == "manual"
    assert await policy.locator("option").all_text_contents() == [
        "Manual selection", "Prefer Trakt", "Prefer Simkl", "Prefer TMDb", "Newest rating",
    ]
    await page.locator("button.cross-sync-tab").filter(has_text="TMDb ratings").click()
    row = page.locator("#cross-sync-tbody tr").filter(has_text="Dune")
    await row.wait_for(state="visible")
    assert "Matched by shared TMDb ID; Trakt is the source" in await row.inner_text()


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_recovery_clears_filters_and_focuses_failed_row(dashboard_page: Page) -> None:
    """Queue recovery reveals a failure even when prior filters excluded it."""
    page = dashboard_page
    await page.wait_for_function("Array.isArray(allEvents) && allEvents.length > 0")
    await page.evaluate("""() => {
        const seed = allEvents[0];
        allEvents = [{...seed, timestamp:'2099-01-01T00:00:00Z', title:'Queue recovery failure', action:'scrobble_stop', result_status:'500', type:'movie', tracker_delivery:{trakt:'failed'}}];
        renderRows(allEvents);
    }""")
    await page.locator('.activity-filter-chip[onclick*="setActivityFilter(\'status\', \'success\'"]').click()
    await page.locator('.activity-filter-chip[onclick*="setActivityFilter(\'type\', \'anime\'"]').click()
    await page.locator("#activity-search").fill("no match")
    user_values = await page.locator("#activity-user-filter option").evaluate_all("options => options.map(option => option.value).filter(Boolean)")
    assert user_values
    await page.locator("#activity-user-filter").select_option(user_values[0])
    await page.evaluate("openQueueRecovery()")
    assert await page.locator('.activity-filter-chip[onclick*="setActivityFilter(\'status\', \'failed\'"]').get_attribute("aria-pressed") == "true"
    assert await page.locator('.activity-filter-chip[onclick*="setActivityFilter(\'type\', \'all\'"]').get_attribute("aria-pressed") == "true"
    assert await page.locator("#activity-search").input_value() == ""
    assert await page.locator("#activity-user-filter").input_value() == ""
    assert await page.locator("#events-tbody .activity-row").filter(has_text="Queue recovery failure").count() == 1
    assert await page.evaluate("window.scrollY > 0")


@pytest.mark.asyncio(loop_scope="function")
async def test_dashboard_accessibility_scan_has_no_serious_violations(dashboard_page: Page) -> None:
    """Run axe-core against the rendered dashboard and reject serious WCAG issues."""
    axe_script = Path(__file__).resolve().parents[1] / "node_modules" / "axe-core" / "axe.min.js"
    assert axe_script.is_file(), "Install npm development dependencies before running browser tests"
    await dashboard_page.add_script_tag(path=str(axe_script))
    report = await dashboard_page.evaluate("""async () => {
        const result = await axe.run(document, {runOnly:{type:'tag', values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}});
        return result.violations.filter(item => ['serious','critical'].includes(item.impact))
            .map(item => ({id:item.id, impact:item.impact, help:item.help, nodes:item.nodes.map(node => ({target:node.target, html:node.html}))}));
    }""")
    assert report == [], f"Serious accessibility violations: {report}"


@pytest.mark.asyncio(loop_scope="function")
async def test_watch_list_create_edit_and_import_flow(admin_dashboard_page: Page, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create a list, add a title, rename it, and merge an imported list."""
    page = admin_dashboard_page
    accounts = DashboardAuthManager(tmp_path / "accounts.json")
    accounts.create_account("housemate", "a sufficiently long password", "member")
    monkeypatch.setattr(dashboard_main, "dashboard_auth_mgr", accounts)
    await page.locator("#tab-watch-lists").click()
    await page.get_by_role("button", name="New list").click()
    dialog = page.locator("#watch-list-modal .watch-list-dialog-panel")
    await dialog.wait_for(state="visible")
    assert await dialog.get_attribute("role") == "dialog"
    assert await page.locator("#watch-list-modal").get_attribute("role") is None
    assert await dialog.get_attribute("aria-labelledby") == "watch-list-dialog-title"
    await page.locator("#watch-list-dialog-input").fill("Weekend queue")
    await page.locator("#watch-list-dialog-confirm").click()
    await page.locator("#watch-item-title").fill("Arrival")
    await page.get_by_role("button", name="Add item", exact=True).click()
    await page.get_by_text("Arrival", exact=True).wait_for()

    await page.get_by_role("button", name="Rename").click()
    await page.locator("#watch-list-dialog-input").fill("Favorites")
    await page.locator("#watch-list-dialog-confirm").click()
    await page.locator("#watch-list-select option").filter(has_text="Favorites").wait_for(state="attached")
    await page.get_by_role("button", name="Share").click()
    await page.locator("#watch-list-dialog-input").fill("housemate=viewer")
    await page.locator("#watch-list-dialog-confirm").click()
    shared_list_id = dashboard_main.watch_list_mgr.get_all()["lists"][0]["id"]
    assert dashboard_main.watch_list_mgr.access_role(shared_list_id, "housemate") == "viewer"

    await page.evaluate("() => { importWatchListData({version:1,lists:[{name:'Imported picks',items:[{title:'Moon',media_type:'movie',year:2009}]}]}); }")
    await page.locator("#watch-list-dialog-input").select_option("merge")
    await page.locator("#watch-list-dialog-confirm").click()
    await page.locator("#watch-list-select option").filter(has_text="Imported picks").wait_for(state="attached")
