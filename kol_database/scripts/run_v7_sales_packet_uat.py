"""Focused browser UAT for the Top-5 sales packet workflow."""

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
TOP_FIVE = ["elevenlabs", "gamma", "replit", "pixverse", "perplexity"]


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
        public_context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await public_context.new_page()
        page.set_default_timeout(60_000)
        page.on("pageerror", lambda error: report["browser_errors"].append(f"pageerror: {error}"))
        page.on(
            "console",
            lambda message: report["browser_errors"].append(f"console: {message.text}")
            if message.type == "error" and (message.location or {}).get("url", "").startswith(base_url)
            else None,
        )

        async def screenshot(name: str, *, full_page: bool = False) -> None:
            path = output_dir / name
            await page.screenshot(path=path, full_page=full_page)
            report["screenshots"].append(str(path))

        await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
        await page.locator(".pursue-section .decision-card").first.wait_for()
        check(await page.locator("body.public-read-only").count() == 1, "public site remains read-only")
        check(await page.locator(".pursue-section .decision-card").count() == 3, "Home shows exactly three daily priorities")
        top_three = await page.locator(".pursue-section .decision-card strong").all_inner_texts()
        check(top_three == ["ElevenLabs", "Gamma", "Replit"], "Home Top 3 order is execution-ready", str(top_three))
        check(await page.locator(".sales-packet-tag").count() >= 3, "Top 3 cards show sales-packet readiness")
        report["observations"]["home_top_three"] = top_three
        await screenshot("01-home-top-three-1440x1000.png")

        company_observations = {}
        for index, company_id in enumerate(TOP_FIVE, start=1):
            await page.goto(
                f"{base_url}/?company={quote('company:' + company_id)}",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.locator(".sales-packet-result").wait_for()
            packet_text = await page.locator(".sales-packet-result").inner_text()
            buyer_count = await page.locator(".buyer-map-row").count()
            pricing_count = await page.locator(".pricing-tier").count()
            overflow = await page.locator("#drawerBody").evaluate("el => el.scrollWidth - el.clientWidth")
            check(buyer_count == 5, f"{company_id} has five buyer-map truth states", str(buyer_count))
            check(pricing_count == 3, f"{company_id} has three pricing tiers", str(pricing_count))
            check("Creator media" in packet_text and "Mango 服务费" in packet_text, f"{company_id} separates media and Mango fee")
            check("经济买方" in packet_text, f"{company_id} shows economic-buyer status")
            check("复制 Email" in packet_text and "复制 X DM" in packet_text, f"{company_id} has copy-ready outreach")
            check(await page.locator("#saveCampaignFromSalesPacketBtn").count() == 0, f"{company_id} public packet has no save control")
            check(overflow <= 4, f"{company_id} packet has no horizontal overflow", str(overflow))
            company_observations[company_id] = {"buyers": buyer_count, "tiers": pricing_count, "overflow_px": overflow}
            if index in (1, 4):
                await screenshot(f"0{index + 1}-{company_id}-sales-packet-1440x1000.png")
        report["observations"]["company_packets"] = company_observations

        await page.goto(
            f"{base_url}/?company={quote('company:elevenlabs')}",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await page.locator(".sales-packet-result").wait_for()
        check("待输入" in await page.locator(".pricing-tier-grid").inner_text(), "unconfigured Mango fee is visibly incomplete")
        await page.locator("#salesServiceFeePct").fill("20")
        await page.locator("#salesContingencyPct").fill("10")
        await page.locator("#salesLocalizationFee").fill("500")
        await page.locator("#repriceSalesPacketBtn").click()
        await page.locator(".pricing-tier-grid").get_by_text("客户情景总价").first.wait_for()
        repriced = await page.locator(".pricing-tier-grid").inner_text()
        check("待输入" not in repriced, "user-entered fee assumptions produce complete scenario totals")
        await screenshot("06-elevenlabs-repriced-public-1440x1000.png")

        if public_only:
            report["observations"]["production_writes_attempted"] = 0
        else:
            internal_context = await browser.new_context(
                viewport={"width": 1440, "height": 1000},
                extra_http_headers={"x-internal-access-token": token},
            )
            internal_page = await internal_context.new_page()
            internal_page.set_default_timeout(60_000)
            await internal_page.goto(
                f"{base_url}/?company={quote('company:elevenlabs')}",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await internal_page.locator(".sales-packet-result").wait_for()
            await internal_page.locator("#salesServiceFeePct").fill("20")
            await internal_page.locator("#salesContingencyPct").fill("10")
            await internal_page.locator("#salesLocalizationFee").fill("500")
            await internal_page.locator("#repriceSalesPacketBtn").click()
            await internal_page.locator(".pricing-tier-grid").get_by_text("客户情景总价").first.wait_for()
            await internal_page.locator("#saveCampaignFromSalesPacketBtn").click()
            await internal_page.locator("#shortlistModal:not(.hidden)").wait_for()
            await internal_page.locator(".shortlist-table tbody tr[data-item-id]").first.wait_for()
            await internal_page.locator(".shortlist-commercial-breakdown").wait_for()
            creator_rows = await internal_page.locator(".shortlist-table tbody tr[data-item-id]").count()
            check(creator_rows == 8, "internal Recommended save persists all eight creator rows", str(creator_rows))
            check(await internal_page.locator(".shortlist-commercial-breakdown").count() == 1, "saved shortlist preserves commercial assumptions")
            shortlist_text = await internal_page.locator("#shortlistBody").inner_text()
            check("Mango 服务费" in shortlist_text and "当前客户总价" in shortlist_text, "saved shortlist shows client-price breakdown")
            internal_shot = output_dir / "07-internal-saved-recommended-1440x1000.png"
            await internal_page.screenshot(path=internal_shot, full_page=False)
            report["screenshots"].append(str(internal_shot))
            report["observations"]["saved_recommended_creator_count"] = creator_rows
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
    parser.add_argument("--public-only", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url.rstrip("/"), args.output_dir, args.token, public_only=args.public_only))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
