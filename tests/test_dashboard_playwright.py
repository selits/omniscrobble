"""Real-browser dashboard layout and keyboard interaction checks."""

from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from playwright.async_api import Browser, Error as PlaywrightError, Page, Route, async_playwright

from app import main as dashboard_main
from app.main import app
from app.services.watch_list_manager import WatchListManager


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
        assert await activity_row.evaluate("element => getComputedStyle(element).display") == "grid"
        for cell in await activity_row.locator("td[data-label]").all():
            assert await cell.evaluate("element => getComputedStyle(element, '::before').content") != 'none'
    else:
        assert await activity_row.evaluate("element => getComputedStyle(element).display") == "table-row"

    if mobile:
        nav_position = await page.locator("#workspace-nav").evaluate("element => getComputedStyle(element).position")
        assert nav_position == "fixed"
        nav_button = await page.locator("#tab-operations").bounding_box()
        assert nav_button["height"] >= 44


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
async def test_watch_list_create_edit_and_import_flow(admin_dashboard_page: Page) -> None:
    """Create a list, add a title, rename it, and merge an imported list."""
    page = admin_dashboard_page
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

    await page.evaluate("() => { importWatchListData({version:1,lists:[{name:'Imported picks',items:[{title:'Moon',media_type:'movie',year:2009}]}]}); }")
    await page.locator("#watch-list-dialog-input").select_option("merge")
    await page.locator("#watch-list-dialog-confirm").click()
    await page.locator("#watch-list-select option").filter(has_text="Imported picks").wait_for(state="attached")
