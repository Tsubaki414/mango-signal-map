"""Playwright interaction smoke for a local, disposable Mango cockpit DB.

The script refuses non-loopback URLs. It exercises validation and navigation
without creating, editing, or deleting records, and verifies that the empty
shortlist/campaign paths do not accidentally emit write requests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from backend.access import build_access_link
from capture_uat_screenshots import DEFAULT_CHROME, _launch_browser


def _is_loopback(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "").lower() in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


async def run(base_url: str, chrome: Path | None, access_token: str | None) -> dict:
    if not _is_loopback(base_url):
        raise ValueError("Interaction smoke is local-only; remote/production writes are forbidden")

    base_url = base_url.rstrip("/")
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "mode": "local_read_and_validation_interactions",
        "passed": [],
        "failed": [],
        "same_origin_write_requests": [],
        "same_origin_browser_errors": [],
    }

    def check(condition: bool, label: str) -> None:
        if condition:
            report["passed"].append(label)
        else:
            report["failed"].append(label)

    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright, chrome)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1100},
        )
        await context.add_init_script(
            "localStorage.removeItem('mango_kol_shortlist_id');"
        )
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

        async def page_for(query: str = "", *, page_context=None):
            page = await (page_context or context).new_page()
            page.on(
                "request",
                lambda request: report["same_origin_write_requests"].append(
                    {"method": request.method, "url": request.url}
                )
                if request.method not in {"GET", "HEAD", "OPTIONS"}
                and request.url.startswith(base_url)
                else None,
            )
            page.on(
                "response",
                lambda response: report["same_origin_browser_errors"].append(
                    {"status": response.status, "url": response.url}
                )
                if response.status >= 400 and response.url.startswith(base_url)
                else None,
            )
            page.on(
                "pageerror",
                lambda error: report["same_origin_browser_errors"].append(
                    {"page_error": str(error)}
                ),
            )
            await page.goto(f"{base_url}/{query}", wait_until="domcontentloaded")
            return page

        # Home: decision-ready rows and no nested action-card scrolling.
        page = await page_for()
        await page.locator(".today-action-row").first.wait_for(state="visible")
        home_text = await page.locator(".today-action-row").first.inner_text()
        for label in (
            "为什么现在",
            "具体下一步",
            "目标负责人",
            "关系事实",
            "执行",
            "主要阻碍",
            "失败备选",
        ):
            check(label in home_text, f"Home first action shows {label}")
        if "任务类型：内部关系核实" in home_text:
            check(
                "独立渠道（非本任务）" in home_text,
                "Home internal-check card keeps the independent channel separate",
            )
        else:
            check(
                "路径类型" in home_text,
                "Home non-internal action shows path type",
            )
        no_nested_scroll = await page.locator(".today-actions-body").evaluate(
            "el => el.scrollHeight <= el.clientHeight + 1 && !['auto','scroll'].includes(getComputedStyle(el).overflowY)"
        )
        check(no_nested_scroll, "Home today-actions has no nested vertical scroll")
        await page.close()

        # Opportunities: first row carries the decision summary, not only a name.
        page = await page_for("?section=opportunities")
        # The table body is initially populated by a visible loading placeholder.
        # Wait for the deep-linked section and a real decision row so a fast local
        # response cannot turn this check into a race (or a lucky pass).
        await page.locator(
            '.section-btn[data-section="opportunities"].active'
        ).wait_for(state="visible")
        await page.locator("#opportunitiesSection:not(.hidden)").wait_for(
            state="visible"
        )
        await page.locator("#oppRows tr .opp-decision-line").first.wait_for(
            state="visible"
        )
        opportunity_text = await page.locator("#oppRows tr").first.inner_text()
        check("为什么现在" in opportunity_text, "Opportunity first row explains why now")
        check("目标负责人" in opportunity_text, "Opportunity first row names operator status")
        check("最后核实" in opportunity_text, "Opportunity first row shows verification freshness")
        await page.close()

        # Company drawer: ten-field execution summary and campaign validation.
        page = await page_for("?company=company%3Arunway")
        await page.locator("#drawer:not(.hidden) .exec-summary").wait_for(state="visible")
        company_summary = await page.locator("#drawer .exec-summary").first.inner_text()
        for label in (
            "建议",
            "为什么现在",
            "最强预算/投放证据",
            "当前负责人候选",
            "最佳当前路径",
            "关系事实",
            "具体下一步",
            "负责人 / 截止",
            "主要阻碍",
            "失败备选",
        ):
            check(label in company_summary, f"Company summary shows {label}")
        await page.locator("#buildCampaignBtn").click()
        labels = await page.locator("#buildCampaignForm label").all_inner_texts()
        for label in ("合作目标", "预算", "目标受众", "目标地区", "目标语言", "目标平台", "时间安排"):
            check(any(label in row for row in labels), f"Campaign field has persistent label: {label}")
        writes_before = len(report["same_origin_write_requests"])
        await page.locator("#cfSubmit").click()
        check(
            await page.locator("#cfObjective").evaluate("el => document.activeElement === el"),
            "Empty campaign objective is rejected and focused",
        )
        check(
            len(report["same_origin_write_requests"]) == writes_before,
            "Empty campaign objective emits no write request",
        )
        await page.close()

        # Creator decision summary and drawer focus trap.
        page = await page_for("?section=creators")
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for(state="visible")
        # A data row can render before the async app bootstrap has applied
        # the requested deep link.  Wait for the active nav state as the
        # completion marker; otherwise that late bootstrap can overwrite a
        # subsequent real Network click.
        await page.locator(
            '.section-btn[data-section="creators"].active'
        ).wait_for(state="visible")
        await page.locator("#creatorsSection:not(.hidden)").wait_for(state="visible")
        await page.locator("#creatorRows tr[data-creator-id] .creator-name").first.click()
        await page.locator("#drawer:not(.hidden) .creator-decision-summary").wait_for(state="visible")
        creator_summary = await page.locator(".creator-decision-summary").inner_text()
        for label in ("当前角色", "Mango 建联", "真实报价", "历史合作证据", "可能相关公司", "用于具体合作活动前"):
            check(label in creator_summary, f"Creator summary shows {label}")
        check(
            await page.locator("#drawerClose").evaluate("el => document.activeElement === el"),
            "Creator drawer takes initial focus",
        )
        await page.keyboard.press("Shift+Tab")
        check(
            await page.locator("#drawer").evaluate("el => el.contains(document.activeElement)"),
            "Drawer Shift+Tab remains trapped",
        )
        await page.keyboard.press("Tab")
        check(
            await page.locator("#drawerClose").evaluate("el => document.activeElement === el"),
            "Drawer Tab wraps to first control",
        )
        await page.keyboard.press("Escape")

        # No current shortlist: row/bulk add open manager and never auto-create.
        writes_before = len(report["same_origin_write_requests"])
        await page.locator("#creatorRows .add-btn").first.click()
        await page.locator("#managerModal:not(.hidden)").wait_for(state="visible")
        check(
            len(report["same_origin_write_requests"]) == writes_before,
            "Creator Add with no current shortlist emits no write request",
        )
        check(
            await page.locator("#managerClose").evaluate("el => document.activeElement === el"),
            "Shortlist manager takes initial focus",
        )
        await page.keyboard.press("Shift+Tab")
        check(
            await page.locator("#managerModal").evaluate("el => el.contains(document.activeElement)"),
            "Manager Shift+Tab remains trapped",
        )
        await page.keyboard.press("Tab")
        check(
            await page.locator("#managerClose").evaluate("el => document.activeElement === el"),
            "Manager Tab wraps to first control",
        )
        writes_before = len(report["same_origin_write_requests"])
        await page.locator("#createShortlistBtn").click()
        check(
            await page.locator("#newShortlistName").evaluate("el => document.activeElement === el"),
            "Blank shortlist name is rejected and focused",
        )
        check(
            len(report["same_origin_write_requests"]) == writes_before,
            "Blank shortlist name emits no write request",
        )
        await page.keyboard.press("Escape")
        await page.locator("#creatorRows .row-select-checkbox").first.check()
        writes_before = len(report["same_origin_write_requests"])
        await page.locator("#bulkAddBtn").click()
        await page.locator("#managerModal:not(.hidden)").wait_for(state="visible")
        check(
            len(report["same_origin_write_requests"]) == writes_before,
            "Bulk Add with no current shortlist emits no write request",
        )
        await page.locator("#managerClose").click()
        await page.locator("#managerModal").wait_for(state="hidden")

        # Network aggregation makes company coverage distinct from candidates.
        # Isolate this aggregation read in a fresh browser context.  The
        # earlier half intentionally leaves localStorage empty and exercises
        # two modal focus traps; neither should become an accidental
        # prerequisite for verifying Network.  Real cross-module top-nav is
        # already exercised by the full disposable E2E.
        await page.close()
        network_context = await browser.new_context(
            viewport={"width": 1440, "height": 1100},
        )
        if access_token:
            network_bootstrap = await network_context.new_page()
            network_link = build_access_link(
                base_url,
                access_token,
                expires=int(time.time()) + 300,
                next_path="/",
            )
            await network_bootstrap.goto(
                network_link,
                wait_until="domcontentloaded",
            )
            await network_bootstrap.wait_for_url(
                f"{base_url}/"
            )
            await network_bootstrap.close()
        page = await page_for(
            "?section=network",
            page_context=network_context,
        )
        await page.locator(".interaction-audit").wait_for(state="visible")
        await page.locator(
            ".grouped-verification-queue > summary"
        ).click()
        await page.locator(
            ".grouped-verification-queue[open]"
        ).wait_for(state="visible")
        await page.locator(
            "#networkSection:not(.hidden) .network-count-line"
        ).first.wait_for(state="visible")
        network_lines = await page.locator(
            "#networkSection .network-count-line"
        ).all_inner_texts()
        check(
            any("4 家公司 / 70 位候选人" in row for row in network_lines),
            "Network shows Solomon company/candidate counts",
        )
        check(
            any("1 家公司 / 4 位候选人" in row for row in network_lines),
            "Network shows Mango company/candidate counts",
        )
        await network_context.close()

        # Small-screen document itself must not overflow; tables can scroll in
        # their own wrappers. Also catch visible broken images/error/loading.
        mobile = await context.new_page()
        await mobile.set_viewport_size({"width": 500, "height": 844})
        await mobile.goto(f"{base_url}/", wait_until="domcontentloaded")
        await mobile.locator(".today-action-row").first.wait_for(state="visible")
        check(
            await mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"),
            "Home small-screen has no document-level horizontal overflow",
        )
        await mobile.close()

        check(
            not report["same_origin_write_requests"],
            "Entire interaction smoke made zero same-origin write requests",
        )
        check(
            not report["same_origin_browser_errors"],
            "No same-origin HTTP/page errors during interaction smoke",
        )
        await context.close()
        await browser.close()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8811")
    parser.add_argument("--chrome", type=Path)
    parser.add_argument("--access-token", default=os.environ.get("INTERNAL_ACCESS_TOKEN"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not _is_loopback(args.base_url):
        parser.error("--base-url must be loopback; production interaction smoke is forbidden")
    report = asyncio.run(run(args.base_url, args.chrome, args.access_token))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(
        f"Browser interaction smoke: {len(report['passed'])} passed, "
        f"{len(report['failed'])} failed"
    )
    for failure in report["failed"]:
        print(f"  FAIL  {failure}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
