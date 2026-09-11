"""Focused Solomon v6 browser UAT against an already-running disposable app."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capture_uat_screenshots import _launch_browser


DEFAULT_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


async def run(base_url: str, output_dir: Path, token: str, *, public_only: bool = False) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {"passed": [], "failures": [], "browser_errors": [], "screenshots": [], "observations": {}}

    def check(condition: bool, label: str, detail: str = "") -> None:
        if condition:
            report["passed"].append(label)
        else:
            report["failures"].append({"check": label, "detail": detail})

    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright, DEFAULT_CHROME if DEFAULT_CHROME.exists() else None)
        public_context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await public_context.new_page()
        page.on("pageerror", lambda error: report["browser_errors"].append(f"pageerror: {error}"))
        def capture_app_console(message) -> None:
            location_url = (message.location or {}).get("url", "")
            if message.type == "error" and location_url.startswith(base_url):
                report["browser_errors"].append(f"console: {message.text}")

        page.on("console", capture_app_console)

        async def screenshot(name: str, *, full_page: bool = True) -> None:
            path = output_dir / name
            await page.screenshot(path=path, full_page=full_page)
            report["screenshots"].append(str(path))

        await page.goto(base_url, wait_until="networkidle")
        await page.locator(".pursue-section .decision-card").first.wait_for()
        check(await page.locator("body.public-read-only").count() == 1, "public mode is read-only")
        check(await page.locator(".pursue-section .decision-card").count() == 5, "Home Pursue now is capped at five")
        home_text = await page.locator("#homeBody").inner_text()
        check("95/100" not in home_text and "94/100" not in home_text, "Home does not expose conflicting numeric scores")
        check(all(raw not in home_text for raw in ("ecosystem_partner", "keep_longlist")), "Home hides internal enums")
        visible_writes = await page.locator(
            "#shortlistManagerBtn:visible, #currentShortlistBtn:visible, #batchEnrichBtn:visible, #stickyBar:visible"
        ).count()
        check(visible_writes == 0, "public Home hides write-only controls", str(visible_writes))
        report["observations"]["home_top_five"] = await page.locator(".pursue-section .decision-card strong").all_inner_texts()
        await screenshot("01-home-1440x900.png")

        await page.goto(f"{base_url}/?section=opportunities", wait_until="networkidle")
        await page.locator("#oppRows tr").first.wait_for()
        opp_text = await page.locator("#opportunitiesSection").inner_text()
        for raw in ("ecosystem_partner", "keep_longlist"):
            check(raw not in opp_text, f"Opportunities hides {raw}")
        check("商机价值" in opp_text and "执行准备度" in opp_text, "Opportunities uses canonical decision columns")
        check(await page.locator("#oppRows tr").count() > 0, "Opportunities renders company rows")
        await screenshot("02-opportunities-1440x900.png")

        company_ids = ["elevenlabs", "runway", "perplexity", "pixverse", "gamma"]
        company_checks = {}
        for index, company_id in enumerate(company_ids, start=1):
            await page.goto(f"{base_url}/?company={quote('company:' + company_id)}", wait_until="networkidle")
            await page.locator("#canonicalCompanyWorkspace").wait_for()
            workspace_text = await page.locator("#canonicalCompanyWorkspace").inner_text()
            overflow = await page.locator("#drawerBody").evaluate("el => el.scrollWidth - el.clientWidth")
            company_checks[company_id] = {
                "overflow_px": overflow,
                "has_json_blob": "{\"" in workspace_text or "[{" in workspace_text,
                "decision": await page.locator(".decision-strip .decision-badge").all_inner_texts(),
            }
            check(overflow <= 4, f"{company_id} detail has no horizontal overflow", str(overflow))
            check("{\"" not in workspace_text and "[{" not in workspace_text, f"{company_id} detail has no serialized JSON")
            check("mango 卖什么" in workspace_text.casefold() and "第一动作" in workspace_text, f"{company_id} has one-screen action brief")
            if company_id == "elevenlabs":
                check("已确认亚洲扩张" in workspace_text, "ElevenLabs APAC status is displayed")
                check("已确认事实" in workspace_text, "confirmed evidence is labeled confirmed")
                check(
                    "official affiliate page offers" not in workspace_text,
                    "English research prose is not primary UI copy",
                )
                hrefs = await page.locator('a[href*="elevenlabs.io/careers"]').count()
                check(hrefs >= 1, "ElevenLabs official APAC source is linked")
            if company_id == "runway":
                check("官方 Partnerships" in workspace_text, "Runway primary route uses official Partnerships")
                await page.locator("#canonicalCompanyWorkspace details.research-archive > summary").click()
                archive_text = await page.locator("#canonicalCompanyWorkspace details.research-archive").inner_text()
                check("Weak secondary research path" in archive_text, "Runway weak secondary path is preserved in archive")
            if index <= 3:
                await screenshot(f"03-company-{index}-{company_id}-1440x900.png", full_page=False)
        report["observations"]["company_checks"] = company_checks

        await page.goto(f"{base_url}/?company={quote('company:elevenlabs')}", wait_until="networkidle")
        await page.locator("#previewCampaignBtn").click()
        await page.locator(".campaign-preview-result").wait_for()
        check(await page.locator(".creator-mix-row").count() > 0, "Campaign preview uses real creator rows")
        preview_text = await page.locator(".campaign-preview-result").inner_text()
        check("建议组合" in preview_text and "$" in preview_text, "Campaign preview shows quote-grounded budget")
        check(await page.locator("#saveCampaignFromPreviewBtn").count() == 0, "public Campaign preview has no save control")
        await screenshot("06-campaign-preview-public-1440x900.png", full_page=False)

        await page.goto(f"{base_url}/?section=network", wait_until="networkidle")
        await page.locator(".route-portfolio-card").first.wait_for()
        network_text = await page.locator("#networkBody").inner_text()
        check("正式商业渠道" in network_text, "Network leads with official commercial routes")
        check("按人去重的 connector 核实优先队列" not in network_text, "connector verification queue does not lead Network")
        await screenshot("07-route-portfolio-1440x900.png")

        await page.goto(f"{base_url}/?section=creators", wait_until="networkidle")
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for()
        strategic_creator_id = await page.locator("#creatorRows tr[data-creator-id]").first.get_attribute("data-creator-id")
        tab_counts = {}
        for tab in ("strategic", "distribution", "media", "needs_review"):
            await page.locator(f'button.tab[data-tab="{tab}"]').click()
            await page.locator("#creatorRows tr.skeleton-row").first.wait_for(state="detached")
            await page.locator("#creatorRows tr[data-creator-id]").first.wait_for()
            tab_counts[tab] = await page.locator("#creatorRows tr[data-creator-id]").count()
            check(tab_counts[tab] > 0, f"Creator tab {tab} renders rows")
        await page.goto(f"{base_url}/?creator={strategic_creator_id}", wait_until="networkidle")
        await page.locator(".creator-commercial-profile").wait_for()
        creator_text = await page.locator("#drawerBody").inner_text()
        check("报价鲜度" in creator_text and "竞争/排他风险" in creator_text, "Creator detail has commercial readiness fields")
        check(await page.locator("#saveClassBtn:visible, #saveNotesBtn:visible, #drawerAddBtn:visible").count() == 0, "public Creator detail hides write controls")
        await screenshot("08-creator-detail-1440x900.png", full_page=False)
        report["observations"]["creator_tab_rows"] = tab_counts

        await page.locator("#drawerClose").click()
        await page.locator("#globalSearch").fill("Runway")
        await page.locator("#globalSearchResults .search-result-row").first.wait_for()
        check("Runway" in await page.locator("#globalSearchResults").inner_text(), "global search returns company result")

        if public_only:
            # Production smoke is intentionally read-only.  Never send an
            # internal token or exercise a mutating route against live data.
            report["observations"]["production_writes_attempted"] = 0
        else:
            # Internal write workflow on the same disposable database only.
            internal_context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                extra_http_headers={"x-internal-access-token": token},
            )
            internal_page = await internal_context.new_page()
            await internal_page.goto(f"{base_url}/?company={quote('company:elevenlabs')}", wait_until="networkidle")
            await internal_page.locator("#previewCampaignBtn").click()
            await internal_page.locator("#saveCampaignFromPreviewBtn").wait_for()
            await internal_page.locator("#saveCampaignFromPreviewBtn").click()
            await internal_page.locator("#shortlistModal:not(.hidden)").wait_for()
            await internal_page.locator(".suggested-add:not([disabled])").first.wait_for()
            await internal_page.locator(".suggested-add:not([disabled])").first.click()
            await internal_page.locator(".shortlist-table tbody tr[data-item-id]").first.wait_for()
            item_count = await internal_page.locator(".shortlist-table tbody tr[data-item-id]").count()
            check(item_count >= 1, "disposable workflow saves creator selection")
            deliverable = internal_page.locator(".item-deliverable").first
            check(await deliverable.locator("option").count() >= 1, "saved creator has a deliverable choice")
            budget_value = await internal_page.locator("#shortlistBudget").input_value()
            check(bool(budget_value), "saved shortlist carries preview budget")
            export_response = await internal_page.request.get(
                f"{base_url}/api/shortlists/{await internal_page.locator('#shortlistModal').get_attribute('data-shortlist-id') or ''}/export.csv"
            ) if await internal_page.locator('#shortlistModal').get_attribute('data-shortlist-id') else None
            # UI export links are authoritative when the modal does not expose ID as a data attribute.
            export_href = await internal_page.locator('#shortlistModal a[href$="/export.csv"]').get_attribute("href")
            if export_href:
                export_response = await internal_page.request.get(f"{base_url}{export_href}")
            check(bool(export_response and export_response.ok), "saved shortlist exports CSV")
            shortlist_shot = output_dir / "09-shortlist-disposable-internal-1440x900.png"
            await internal_page.screenshot(path=shortlist_shot, full_page=False)
            report["screenshots"].append(str(shortlist_shot))
            report["observations"]["disposable_shortlist_items"] = item_count
            await internal_context.close()
        await public_context.close()
        await browser.close()

    report["ok"] = not report["failures"] and not report["browser_errors"]
    (output_dir / "uat-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--token", default="uat-write-token")
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Run only read-only browser checks; required for production smoke tests.",
    )
    args = parser.parse_args()
    report = asyncio.run(
        run(args.base_url.rstrip("/"), args.output_dir, args.token, public_only=args.public_only)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
