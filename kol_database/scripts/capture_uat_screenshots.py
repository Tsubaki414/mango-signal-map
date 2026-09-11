"""Capture browser UAT evidence and a read-only HTTP/console/network report.

The capture is version-tolerant: an older deployed UI still produces a
baseline screenshot and a machine-readable missing-selector report instead
of aborting on the first renamed element. Use ``--strict`` for the final
release candidate, where any missing current-release surface or browser error
must fail the command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from backend.access import build_access_link

DEFAULT_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "uat_screenshots"
    / date.today().isoformat()
)

API_PROBES = [
    "/",
    "/healthz",
    "/api/meta/status",
    "/api/home/summary",
    "/api/companies?page_size=1",
    "/api/network/interview-queue",
    "/api/creators?page_size=1",
    "/api/shortlists",
]

SURFACES = [
    {
        "name": "home-desktop.png",
        "query": "",
        "selectors": [".today-action-row", ".home-list-row", "#creatorRows tr"],
        "current_selector": ".today-action-row",
    },
    {
        "name": "opportunities-desktop.png",
        "query": "?section=opportunities",
        "selectors": ["#oppRows tr", "#creatorRows tr"],
        "current_selector": "#oppRows tr",
    },
    {
        "name": "runway-desktop.png",
        "query": "?company=company%3Arunway",
        "selectors": ["#drawer:not(.hidden) .detail-name", "#creatorRows tr", ".home-list-row"],
        "current_selector": "#drawer:not(.hidden) .detail-name",
    },
    {
        "name": "cursor-desktop.png",
        "query": "?company=company%3Acursor",
        "selectors": ["#drawer:not(.hidden) .detail-name", "#creatorRows tr", ".home-list-row"],
        "current_selector": "#drawer:not(.hidden) .detail-name",
    },
    {
        "name": "gamma-e0-desktop.png",
        "query": "?company=company%3Agamma",
        "selectors": ["#drawer:not(.hidden) .detail-name", "#creatorRows tr", ".home-list-row"],
        "current_selector": "#drawer:not(.hidden) .detail-name",
    },
    {
        "name": "heurist-bounded-negative-desktop.png",
        "query": "?company=company%3Aheurist",
        "selectors": ["#drawer:not(.hidden) .detail-name", "#creatorRows tr", ".home-list-row"],
        "current_selector": "#drawer:not(.hidden) .detail-name",
    },
    {
        "name": "network-desktop.png",
        "query": "?section=network",
        "selectors": ["#networkBody .home-card-title", "#creatorRows tr"],
        "current_selector": "#networkBody .home-card-title",
    },
    {
        "name": "creators-desktop.png",
        "query": "?section=creators",
        "selectors": ["#creatorRows tr"],
        "current_selector": "#creatorRows tr",
    },
    {
        "name": "creator-sponsored-detail-desktop.png",
        "query": "?creator=216",
        "selectors": ["#drawer:not(.hidden) .creator-decision-summary", "#creatorRows tr"],
        "current_selector": "#drawer:not(.hidden) .creator-decision-summary",
    },
    {
        "name": "campaign-builder-desktop.png",
        "query": "?company=company%3Arunway",
        "selectors": ["#drawer:not(.hidden) #buildCampaignBtn", "#creatorRows tr"],
        "current_selector": "#buildCampaignForm .campaign-form",
        "action": "open_campaign_builder",
    },
]


async def _launch_browser(playwright, chrome: Path | None) -> Browser:
    if chrome is not None:
        if not chrome.is_file():
            raise FileNotFoundError(f"Chrome executable not found: {chrome}")
        return await playwright.chromium.launch(headless=True, executable_path=str(chrome))
    try:
        return await playwright.chromium.launch(headless=True)
    except Exception:
        # Developer machines often have system Chrome but not Playwright's
        # separately-downloaded Chromium. This fallback is optional and is
        # never hardcoded as the only supported runtime.
        if DEFAULT_CHROME.is_file():
            return await playwright.chromium.launch(
                headless=True,
                executable_path=str(DEFAULT_CHROME),
            )
        raise


async def _wait_for_any(page: Page, selectors: list[str], timeout_ms: int = 5_000) -> str | None:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        for selector in selectors:
            if await page.locator(selector).count():
                try:
                    await page.locator(selector).first.wait_for(state="visible", timeout=250)
                    return selector
                except PlaywrightTimeoutError:
                    pass
        await page.wait_for_timeout(100)
    return None


async def _safe_screenshot(page: Page, path: Path) -> str | None:
    """Capture one artifact without aborting the remainder of the audit.

    Remote browser screenshots can time out after the page itself has already
    loaded.  Keep that timeout as an explicit release failure, but return it to
    the report so later surfaces and the machine-readable report are still
    produced.
    """

    try:
        await page.screenshot(path=str(path), full_page=False, timeout=15_000)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


async def _safe_close_page(page: Page) -> str | None:
    """Best-effort page cleanup whose failure remains visible in the report."""

    try:
        await page.close()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


async def _probe_api(context, base_url: str) -> list[dict]:
    async def probe(endpoint: str) -> dict:
        try:
            response = await context.request.get(
                urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/")),
                timeout=15_000,
            )
            return {
                "endpoint": endpoint,
                "status": response.status,
                "ok": response.ok,
                "content_type": response.headers.get("content-type", ""),
            }
        except Exception as exc:
            return {
                "endpoint": endpoint,
                "status": None,
                "ok": False,
                "error": type(exc).__name__,
            }

    return list(await asyncio.gather(*(probe(endpoint) for endpoint in API_PROBES)))


async def _same_origin_links(page: Page, base_url: str) -> list[str]:
    origin = urlparse(base_url)
    links: set[str] = set()
    for href in await page.locator("a[href]").evaluate_all("els => els.map(el => el.href)"):
        parsed = urlparse(href)
        if parsed.scheme in {"http", "https"} and (parsed.scheme, parsed.netloc) == (
            origin.scheme,
            origin.netloc,
        ):
            links.add(href.split("#", 1)[0])
    return sorted(links)


async def _external_link_records(page: Page, base_url: str) -> list[dict]:
    origin = urlparse(base_url)
    records = []
    raw_links = await page.locator("a[href]").evaluate_all(
        "els => els.map(el => ({href: el.href, target: el.target, rel: el.rel}))"
    )
    for row in raw_links:
        parsed = urlparse(row["href"])
        if parsed.scheme in {"http", "https"} and (parsed.scheme, parsed.netloc) == (
            origin.scheme,
            origin.netloc,
        ):
            continue
        rel_tokens = set((row.get("rel") or "").lower().split())
        valid_http = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        records.append(
            {
                "url": row["href"],
                "scheme_valid": valid_http,
                "target_blank": row.get("target") == "_blank",
                "noopener": "noopener" in rel_tokens,
                "markup_ok": valid_http
                and row.get("target") == "_blank"
                and "noopener" in rel_tokens,
            }
        )
    return records


async def _page_visual_state(page: Page) -> dict:
    await page.evaluate(
        """
        () => {
          const inViewport = (el) => {
            const s = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none'
              && s.visibility !== 'hidden'
              && r.width > 0
              && r.height > 0
              && r.bottom > 0
              && r.right > 0
              && r.top < window.innerHeight
              && r.left < window.innerWidth;
          };
          return Promise.all([...document.images].filter(inViewport).map(img => {
          if (img.complete) return Promise.resolve();
          return new Promise(resolve => {
            const done = () => resolve();
            img.addEventListener('load', done, {once: true});
            img.addEventListener('error', done, {once: true});
            setTimeout(done, 3000);
          });
          }));
        }
        """
    )
    return await page.evaluate(
        """
        () => {
          const visible = (el) => {
            const s = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
          };
          const inViewport = (el) => {
            const r = el.getBoundingClientRect();
            return visible(el)
              && r.bottom > 0
              && r.right > 0
              && r.top < window.innerHeight
              && r.left < window.innerWidth;
          };
          const rows = (selector) => [...document.querySelectorAll(selector)]
            .filter(visible)
            .map(el => ({selector, text: (el.textContent || '').trim().slice(0, 160)}));
          const images = [...document.images].filter(inViewport).map(img => ({
            src: img.currentSrc || img.src,
            alt: img.alt || '',
            complete: img.complete,
            natural_width: img.naturalWidth,
          }));
          return {
            broken_images: images.filter(img => !img.complete || img.natural_width === 0),
            errors: rows('.error, .error-state, #errorState, #oppErrorState'),
            empty_states: rows('.empty-state'),
            loading_states: rows('.loading, #loadingState, [aria-busy="true"]'),
          };
        }
        """
    )


async def _fallback_ui_navigation(page: Page, surface_name: str) -> str | None:
    """Open old deployments through their visible controls when deep links
    are unsupported. This prevents a failed `?section=` query from producing
    several misleading copies of the Home screenshot.
    """

    section_by_name = {
        "opportunities-desktop.png": "opportunities",
        "network-desktop.png": "network",
        "creators-desktop.png": "creators",
    }
    if surface_name in section_by_name:
        section = section_by_name[surface_name]
        button = page.locator(f'.section-btn[data-section="{section}"]')
        if await button.count():
            await button.click()
            await page.wait_for_timeout(500)
            return f"clicked section nav: {section}"
        return None

    company_by_name = {
        "runway-desktop.png": "company:runway",
        "cursor-desktop.png": "company:cursor",
        "gamma-e0-desktop.png": "company:gamma",
    }
    company_id = company_by_name.get(surface_name)
    if company_id:
        opportunities = page.locator(
            '.section-btn[data-section="opportunities"]'
        )
        if await opportunities.count():
            await opportunities.click()
            await page.wait_for_timeout(400)
        row = page.locator(f'[data-company-id="{company_id}"]').first
        try:
            await row.wait_for(state="visible", timeout=5_000)
            await row.click()
            await page.wait_for_timeout(500)
            return f"clicked company row: {company_id}"
        except PlaywrightTimeoutError:
            search = page.locator("#globalSearch")
            if await search.count():
                await search.fill(company_id.split(":", 1)[1])
                result = page.locator(
                    f'.search-result-row[data-kind="company"][data-id="{company_id}"]'
                )
                try:
                    await result.wait_for(state="visible", timeout=5_000)
                    await result.click()
                    await page.wait_for_timeout(500)
                    return f"searched and clicked company: {company_id}"
                except PlaywrightTimeoutError:
                    pass
    return None


async def capture(
    base_url: str,
    output_dir: Path,
    chrome: Path | None,
    *,
    access_token: str | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    base_url = base_url.rstrip("/")
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "mode": "read_only",
        "api_probes": [],
        "surfaces": [],
        "broken_internal_links": [],
        "external_links": [],
    }

    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright, chrome)
        context = await browser.new_context()
        if access_token:
            bootstrap = await context.new_page()
            link = build_access_link(
                base_url,
                access_token,
                expires=int(time.time()) + 300,
                next_path="/",
            )
            await bootstrap.goto(link, wait_until="domcontentloaded")
            await bootstrap.wait_for_url(f"{base_url}/")
            await bootstrap.close()
        report["api_probes"] = await _probe_api(context, base_url)
        all_internal_links: set[str] = set()
        all_external_links: dict[str, dict] = {}

        async def shot(surface: dict, *, viewport: dict[str, int] | None = None) -> None:
            page = await context.new_page()
            if viewport:
                await page.set_viewport_size(viewport)
            console_errors: list[dict] = []
            application_console_errors: list[dict] = []
            page_errors: list[str] = []
            request_failures: list[dict] = []
            external_request_failures: list[dict] = []
            http_errors: list[dict] = []
            external_http_errors: list[dict] = []

            def same_origin(url: str) -> bool:
                expected = urlparse(base_url)
                actual = urlparse(url)
                return (actual.scheme, actual.netloc) == (expected.scheme, expected.netloc)

            def on_console(message) -> None:
                if message.type != "error":
                    return
                location = message.location or {}
                item = {"text": message.text, "url": location.get("url", "")}
                console_errors.append(item)
                if not item["url"] or same_origin(item["url"]):
                    application_console_errors.append(item)

            def on_request_failed(request) -> None:
                item = {
                    "url": request.url,
                    "failure": request.failure or "request failed",
                    "resource_type": request.resource_type,
                }
                if same_origin(request.url):
                    request_failures.append(item)
                else:
                    external_request_failures.append(item)

            def on_response(response) -> None:
                if response.status < 400:
                    return
                item = {
                    "url": response.url,
                    "status": response.status,
                    "resource_type": response.request.resource_type,
                }
                if same_origin(response.url):
                    http_errors.append(item)
                else:
                    item["classification"] = (
                        "automation_or_access_block"
                        if response.status in {401, 403, 429}
                        else "broken_external_resource"
                        if response.status == 404 or response.status >= 500
                        else "external_http_error"
                    )
                    external_http_errors.append(item)

            page.on("console", on_console)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("requestfailed", on_request_failed)
            page.on("response", on_response)
            url = f"{base_url}/{surface['query']}"
            navigation_status = None
            navigation_error = None
            screenshot_error = None
            page_close_error = None
            matched_selector = None
            fallback_navigation = None
            visual_state = {
                "broken_images": [],
                "errors": [],
                "empty_states": [],
                "loading_states": [],
            }
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                navigation_status = response.status if response else None
                await page.wait_for_selector("body", state="visible", timeout=10_000)
                matched_selector = await _wait_for_any(page, surface["selectors"])
                # List rows often render before a requested creator/company
                # deep-link drawer finishes its own API request.  Prefer the
                # current-release selector for a bounded extra window instead
                # of prematurely accepting the already-visible list fallback.
                if (
                    matched_selector != surface["current_selector"]
                    and not surface.get("action")
                ):
                    current = await _wait_for_any(
                        page,
                        [surface["current_selector"]],
                        timeout_ms=12_000,
                    )
                    if current:
                        matched_selector = current
                if matched_selector != surface["current_selector"]:
                    fallback_navigation = await _fallback_ui_navigation(
                        page,
                        surface["name"],
                    )
                    if fallback_navigation:
                        current = await _wait_for_any(
                            page,
                            [surface["current_selector"]],
                        )
                        if current:
                            matched_selector = current
                if surface.get("action") == "open_campaign_builder":
                    await page.locator("#buildCampaignBtn").click()
                    current = await _wait_for_any(page, [surface["current_selector"]])
                    if current:
                        matched_selector = current
                await page.wait_for_timeout(500)
                all_internal_links.update(await _same_origin_links(page, base_url))
                for record in await _external_link_records(page, base_url):
                    all_external_links[record["url"]] = record
                visual_state = await _page_visual_state(page)
            except Exception as exc:
                navigation_error = f"{type(exc).__name__}: {exc}"
            finally:
                screenshot_error = await _safe_screenshot(
                    page,
                    output_dir / surface["name"],
                )
                page_close_error = await _safe_close_page(page)

            report["surfaces"].append(
                {
                    "name": surface["name"],
                    "url": url,
                    "navigation_status": navigation_status,
                    "navigation_error": navigation_error,
                    "screenshot_error": screenshot_error,
                    "page_close_error": page_close_error,
                    "matched_selector": matched_selector,
                    "fallback_navigation": fallback_navigation,
                    "current_release_selector_found": matched_selector
                    == surface["current_selector"],
                    "console_errors": console_errors,
                    "application_console_errors": application_console_errors,
                    "page_errors": page_errors,
                    "request_failures": request_failures,
                    "external_request_failures": external_request_failures,
                    "http_errors": http_errors,
                    "external_http_errors": external_http_errors,
                    **visual_state,
                }
            )
            print(
                f"captured {surface['name']} "
                f"({matched_selector or 'no known release selector'})"
            )

        for surface in SURFACES:
            await shot(surface)

        # Manager is a real GET-only interaction. If the old UI does not
        # have it, retain a screenshot and mark the current selector missing.
        manager = {
            "name": "campaign-manager-desktop.png",
            "query": "",
            "selectors": ["#shortlistManagerBtn", "#creatorRows tr"],
        }
        page = await context.new_page()
        manager_error = None
        manager_screenshot_errors: list[str] = []
        manager_close_error = None
        manager_open = False
        manager_current_selector = (
            "#managerModal:not(.hidden) #managerBody .shortlist-toolbar"
        )
        manager_visual_state = {
            "broken_images": [],
            "errors": [],
            "empty_states": [],
            "loading_states": [],
        }
        try:
            await page.goto(
                f"{base_url}/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            selector = await _wait_for_any(page, manager["selectors"])
            if selector == "#shortlistManagerBtn":
                await page.locator(selector).click()
                loaded_selector = await _wait_for_any(
                    page,
                    [manager_current_selector],
                    timeout_ms=15_000,
                )
                manager_open = loaded_selector == manager_current_selector
                manager_visual_state = await _page_visual_state(page)
        except Exception as exc:
            manager_error = f"{type(exc).__name__}: {exc}"
        finally:
            for screenshot_name in (
                manager["name"],
                "shortlists-empty-desktop.png",
            ):
                screenshot_error = await _safe_screenshot(
                    page,
                    output_dir / screenshot_name,
                )
                if screenshot_error:
                    manager_screenshot_errors.append(
                        f"{screenshot_name}: {screenshot_error}"
                    )
            manager_close_error = await _safe_close_page(page)
        report["surfaces"].append(
            {
                "name": manager["name"],
                "url": f"{base_url}/",
                "navigation_status": 200 if manager_error is None else None,
                "navigation_error": manager_error,
                "screenshot_error": "; ".join(manager_screenshot_errors) or None,
                "page_close_error": manager_close_error,
                "matched_selector": manager_current_selector if manager_open else None,
                "current_release_selector_found": manager_open,
                "console_errors": [],
                "application_console_errors": [],
                "page_errors": [],
                "request_failures": [],
                "external_request_failures": [],
                "http_errors": [],
                "external_http_errors": [],
                **manager_visual_state,
            }
        )

        mobile = dict(SURFACES[0], name="home-small-screen.png")
        await shot(mobile, viewport={"width": 500, "height": 844})
        network_mobile = dict(
            next(surface for surface in SURFACES if surface["name"] == "network-desktop.png"),
            name="network-small-screen.png",
        )
        await shot(network_mobile, viewport={"width": 500, "height": 844})

        async def check_internal_link(link: str) -> dict | None:
            try:
                response = await context.request.get(link, timeout=15_000)
                if response.status >= 400:
                    return {"url": link, "status": response.status}
            except Exception as exc:
                return {"url": link, "error": type(exc).__name__}
            return None

        internal_results = await asyncio.gather(
            *(check_internal_link(link) for link in sorted(all_internal_links))
        )
        report["broken_internal_links"] = [row for row in internal_results if row]

        # Validate every rendered external link's markup. Probe up to two
        # representatives per host with HEAD; never parse response content.
        # X links are status-checked only, preserving the Rapid-X-only data
        # contract for profiles/posts/relationships.
        host_probe_counts: dict[str, int] = {}
        scheduled_external: list[tuple[str, dict, bool]] = []
        for link, record in sorted(all_external_links.items()):
            parsed = urlparse(link)
            host = parsed.netloc.lower()
            probe = host_probe_counts.get(host, 0) < 2 and record["scheme_valid"]
            if probe:
                host_probe_counts[host] = host_probe_counts.get(host, 0) + 1
            scheduled_external.append((link, record, probe))

        async def check_external_link(link: str, record: dict, probe: bool) -> dict:
            host = urlparse(link).netloc.lower()
            status = None
            status_class = "not_probed_representative_limit"
            method = None
            if probe:
                method = "HEAD"
                try:
                    response = await context.request.head(link, timeout=8_000)
                    status = response.status
                    if status == 405 and host not in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
                        method = "GET"
                        response = await context.request.get(link, timeout=8_000)
                        status = response.status
                    if 200 <= status < 400:
                        status_class = "reachable"
                    elif status in {401, 403, 429, 999}:
                        status_class = "automation_blocked"
                    elif status == 404:
                        status_class = "broken_404"
                    elif status >= 500:
                        status_class = "server_error"
                    else:
                        status_class = "unexpected_status"
                except Exception as exc:
                    status_class = f"probe_error:{type(exc).__name__}"
            return {
                **record,
                "probe_method": method,
                "status": status,
                "status_class": status_class,
                "content_read": False,
            }

        report["external_links"] = list(
            await asyncio.gather(
                *(
                    check_external_link(link, record, probe)
                    for link, record, probe in scheduled_external
                )
            )
        )

        await context.close()
        await browser.close()

    return report


def _write_report(report: dict, output_dir: Path) -> None:
    (output_dir / "browser-smoke.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Browser smoke report",
        "",
        f"- Captured: `{report['captured_at']}`",
        f"- Base URL: `{report['base_url']}`",
        "- Mode: read-only (GET/navigation only; no production writes)",
        "",
        "## API probes",
        "",
    ]
    lines.extend(
        f"- `{row['endpoint']}`: {row.get('status') or row.get('error')}"
        for row in report["api_probes"]
    )
    lines += ["", "## Browser surfaces", ""]
    for row in report["surfaces"]:
        lines.append(
            f"- `{row['name']}`: current selector="
            f"{'yes' if row['current_release_selector_found'] else 'no'}; "
            f"app-console={len(row.get('application_console_errors', row['console_errors']))}; "
            f"page={len(row['page_errors'])}; "
            f"same-origin-network={len(row['request_failures']) + len(row['http_errors'])}; "
            f"screenshot={'error' if row.get('screenshot_error') else 'ok'}; "
            f"external-diagnostics={len(row.get('external_request_failures', [])) + len(row.get('external_http_errors', []))}"
        )
    lines += ["", "## Broken same-origin links", ""]
    if report["broken_internal_links"]:
        lines.extend(
            f"- `{row['url']}`: {row.get('status') or row.get('error')}"
            for row in report["broken_internal_links"]
        )
    else:
        lines.append("- None found")
    lines += ["", "## External links", ""]
    if report["external_links"]:
        lines.extend(
            f"- `{row['url']}`: markup={'ok' if row['markup_ok'] else 'invalid'}; "
            f"probe={row['status'] or row['status_class']} ({row['status_class']})"
            for row in report["external_links"]
        )
    else:
        lines.append("- None rendered")
    (output_dir / "browser-smoke.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _strict_failures(report: dict) -> list[str]:
    failures = []
    for probe in report["api_probes"]:
        if not probe["ok"]:
            failures.append(
                f"API {probe['endpoint']} -> {probe.get('status') or probe.get('error')}"
            )
    for surface in report["surfaces"]:
        if not surface["current_release_selector_found"]:
            failures.append(f"missing current selector: {surface['name']}")
        if surface["navigation_error"]:
            failures.append(f"navigation: {surface['name']}")
        if surface.get("screenshot_error"):
            failures.append(f"screenshot: {surface['name']}")
        if surface.get("page_close_error"):
            failures.append(f"page close: {surface['name']}")
        if (
            surface.get("application_console_errors", surface["console_errors"])
            or surface["page_errors"]
            or surface["request_failures"]
            or surface["http_errors"]
        ):
            failures.append(f"browser errors: {surface['name']}")
        for external in surface.get("external_http_errors", []):
            if external["status"] == 404 or external["status"] >= 500:
                failures.append(
                    f"broken external resource: {surface['name']} -> {external['url']}"
                )
        if surface.get("broken_images"):
            failures.append(f"broken images: {surface['name']}")
        if surface.get("errors") or surface.get("loading_states"):
            failures.append(f"visible error/loading state: {surface['name']}")
    if report["broken_internal_links"]:
        failures.append("broken same-origin links")
    for link in report["external_links"]:
        if not link["markup_ok"]:
            failures.append(f"external link markup: {link['url']}")
        if link["status_class"] in {"broken_404", "server_error"}:
            failures.append(f"external link status: {link['url']}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8811")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--chrome",
        type=Path,
        help="Optional system Chrome/Chromium executable.",
    )
    parser.add_argument(
        "--access-token",
        default=os.environ.get("INTERNAL_ACCESS_TOKEN"),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail unless every current-release surface is clean.",
    )
    args = parser.parse_args()
    report = asyncio.run(
        capture(
            args.base_url,
            args.output_dir,
            args.chrome,
            access_token=args.access_token,
        )
    )
    _write_report(report, args.output_dir)
    failures = _strict_failures(report)
    if failures:
        print(f"Browser smoke recorded {len(failures)} release issue(s).")
        for failure in failures:
            print(f"  - {failure}")
    if args.strict and failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
