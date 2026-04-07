"""Launch Chromium; no site-specific selectors."""

from __future__ import annotations

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

# Approx. Sydney CBD — satisfies portal/map geolocation prompts without a system dialog.
_DEFAULT_GEO = {"latitude": -33.8688, "longitude": 151.2093}
_CPA_ORIGIN = "https://apps.cpaaustralia.com.au"


def new_browser_context(
    playwright: Playwright,
    *,
    headless: bool,
) -> tuple[Browser, BrowserContext, Page]:
    browser = playwright.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
        ],
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="en-AU",
        timezone_id="Australia/Sydney",
        geolocation=_DEFAULT_GEO,
        permissions=["geolocation"],
    )
    context.grant_permissions(["geolocation"], origin=_CPA_ORIGIN)
    page = context.new_page()
    page.set_default_timeout(60_000)
    return browser, context, page
