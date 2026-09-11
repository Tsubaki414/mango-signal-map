"""Run read-only Playwright UAT against the deployed Mango BD Cockpit.

This is a visual and interaction audit, not a source-code or unit-test proxy.
It opens the production UI in desktop and mobile Chromium contexts, follows the
core commercial workflow, saves screenshots/download evidence, and records
console, network, copy, overflow, wording, and interaction findings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from playwright.async_api import BrowserContext, Page, async_playwright

from capture_uat_screenshots import DEFAULT_CHROME, _launch_browser


AWKWARD_VISIBLE_TEXT = (
    "Solomon",
    "SOLOMON",
    "Solomon connection opportunity",
    "Prepare three enterprise",
    "Prepare a one-page cohort",
    "Frame Mango as a distribution",
    "Contact routes",
    "Recommended creator mix",
    "Mango owner",
    "askability",
)


class Audit:
    def __init__(self, base_url: str, output_dir: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.origin = urlparse(self.base_url)
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.report: dict = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "base_url": self.base_url,
            "mode": "production_public_read_only",
            "checks": [],
            "failures": [],
            "browser_events": [],
            "screenshots": [],
            "downloads": [],
            "states": [],
            "observations": {},
        }

    def check(self, condition: bool, label: str, detail: object = "") -> None:
        row = {"label": label, "ok": bool(condition), "detail": detail}
        self.report["checks"].append(row)
        if not condition:
            self.report["failures"].append(row)

    def same_origin(self, url: str) -> bool:
        parsed = urlparse(url)
        return (parsed.scheme, parsed.netloc) == (self.origin.scheme, self.origin.netloc)

    def watch(self, page: Page, device: str) -> None:
        def console(message) -> None:
            if message.type != "error":
                return
            location = message.location or {}
            url = location.get("url", "")
            self.report["browser_events"].append(
                {
                    "device": device,
                    "kind": "console_error",
                    "same_origin": not url or self.same_origin(url),
                    "url": url,
                    "detail": message.text,
                }
            )

        def request_failed(request) -> None:
            self.report["browser_events"].append(
                {
                    "device": device,
                    "kind": "request_failed",
                    "same_origin": self.same_origin(request.url),
                    "url": request.url,
                    "detail": request.failure or "request failed",
                    "resource_type": request.resource_type,
                }
            )

        def response(response) -> None:
            if response.status < 400:
                return
            self.report["browser_events"].append(
                {
                    "device": device,
                    "kind": "http_error",
                    "same_origin": self.same_origin(response.url),
                    "url": response.url,
                    "detail": response.status,
                    "resource_type": response.request.resource_type,
                }
            )

        page.on("console", console)
        page.on(
            "pageerror",
            lambda error: self.report["browser_events"].append(
                {
                    "device": device,
                    "kind": "page_error",
                    "same_origin": True,
                    "url": page.url,
                    "detail": str(error),
                }
            ),
        )
        page.on("requestfailed", request_failed)
        page.on("response", response)

    async def shot(
        self,
        page: Page,
        name: str,
        *,
        full_page: bool = False,
        locator: str | None = None,
    ) -> None:
        path = self.output_dir / name
        if locator:
            target = page.locator(locator).first
            await target.scroll_into_view_if_needed()
            await page.wait_for_timeout(250)
            await target.screenshot(path=str(path), timeout=30_000)
        else:
            await page.screenshot(path=str(path), full_page=full_page, timeout=30_000)
        self.report["screenshots"].append(str(path))

    async def state(self, page: Page, device: str, surface: str) -> dict:
        snapshot = await page.evaluate(
            """
            () => {
              const visible = (el) => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const outside = [...document.querySelectorAll('body *')]
                .filter(visible)
                .map((el) => {
                  const r = el.getBoundingClientRect();
                  return {
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    class_name: String(el.className || '').slice(0, 120),
                    left: Math.round(r.left),
                    right: Math.round(r.right),
                    width: Math.round(r.width),
                    text: (el.innerText || '').trim().slice(0, 100),
                  };
                })
                .filter((row) => row.left < -5 || row.right > window.innerWidth + 5)
                .slice(0, 30);
              const brokenImages = [...document.images]
                .filter(visible)
                .filter((img) => img.complete && img.naturalWidth === 0)
                .map((img) => ({src: img.currentSrc || img.src, alt: img.alt || ''}));
              const bodyText = document.body.innerText || '';
              return {
                viewport: {width: window.innerWidth, height: window.innerHeight},
                document_width: document.documentElement.scrollWidth,
                global_overflow_px: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
                outside,
                broken_images: brokenImages,
                visible_text: bodyText,
              };
            }
            """
        )
        visible_text = snapshot.pop("visible_text")
        snapshot["awkward_visible_text"] = [
            phrase for phrase in AWKWARD_VISIBLE_TEXT if phrase in visible_text
        ]
        snapshot.update({"device": device, "surface": surface, "url": page.url})
        self.report["states"].append(snapshot)
        self.check(
            snapshot["global_overflow_px"] <= 4,
            f"{device} {surface}: no document-level horizontal overflow",
            snapshot["global_overflow_px"],
        )
        self.check(
            not snapshot["broken_images"],
            f"{device} {surface}: no visible broken images",
            snapshot["broken_images"],
        )
        self.check(
            not snapshot["awkward_visible_text"],
            f"{device} {surface}: no known awkward/personalized copy",
            snapshot["awkward_visible_text"],
        )
        return snapshot

    async def goto(self, page: Page, path: str = "") -> None:
        response = await page.goto(
            f"{self.base_url}/{path.lstrip('/')}",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        self.check(response is not None and response.status == 200, f"GET {path or '/'} returns 200", response.status if response else None)
        await page.locator("body").wait_for(state="visible")

    async def wait_company(self, page: Page) -> str:
        await page.locator("#drawer:not(.hidden) #canonicalCompanyWorkspace").wait_for(timeout=60_000)
        if await page.locator("#loadSalesPacketBtn").count():
            await page.locator("#drawer:not(.hidden) .sales-packet-result").wait_for(timeout=60_000)
            mode = "sales_packet"
        else:
            preview = page.locator("#previewCampaignBtn")
            await preview.wait_for(timeout=60_000)
            await preview.click()
            await page.locator("#drawer:not(.hidden) .campaign-preview-result").wait_for(timeout=60_000)
            mode = "campaign_preview"
        await page.wait_for_timeout(300)
        return mode

    async def company_packet_observation(self, page: Page, company: str) -> dict:
        sales = page.locator(".sales-packet-result")
        root = sales if await sales.count() else page.locator(".campaign-preview-result")
        creator_selector = ".sales-creator-row > strong:first-of-type" if await sales.count() else ".creator-mix-row > div:first-child strong"
        creator_names = [name for name in await page.locator(creator_selector).all_inner_texts() if name.strip()]
        result = {
            "creator_names": creator_names,
            "pricing_tiers": await page.locator(".pricing-tier").count(),
            "buyer_rows": await page.locator(".buyer-map-row").count(),
            "packet_text_sample": (await root.inner_text())[:1600],
            "mode": "sales_packet" if await sales.count() else "campaign_preview",
        }
        self.report["observations"].setdefault("company_packets", {})[company] = result
        return result

    async def desktop(self, context: BrowserContext) -> None:
        page = await context.new_page()
        page.set_default_timeout(60_000)
        self.watch(page, "desktop")

        await self.goto(page)
        await page.locator(".pursue-section .decision-card").first.wait_for()
        home_cards = await page.locator(".pursue-section .decision-card").count()
        self.check(home_cards == 3, "desktop Home shows exactly three daily priorities", home_cards)
        self.check(await page.locator("body.public-read-only").count() == 1, "desktop production is visibly public read-only")
        await self.state(page, "desktop", "home")
        await self.shot(page, "desktop-01-home.png")

        manager_button = page.locator("#shortlistManagerBtn")
        self.check(
            not await manager_button.is_visible(),
            "desktop public read-only mode hides shortlist management",
        )

        search = page.locator("#globalSearch")
        await search.fill("Gamma")
        result = page.locator('.search-result-row[data-kind="company"][data-id="company:gamma"]')
        await result.wait_for()
        await self.shot(page, "desktop-02-global-search.png")
        await result.click()
        await self.wait_company(page)
        self.check("Gamma" in await page.locator("#canonicalCompanyWorkspace h2").inner_text(), "desktop search opens Gamma company workspace")
        await self.state(page, "desktop", "gamma-company")
        await self.shot(page, "desktop-03-gamma-company.png")
        await self.shot(page, "desktop-04-gamma-sales-packet.png", locator=".sales-packet-workspace")
        gamma = await self.company_packet_observation(page, "Gamma")

        copy_email = page.locator('[data-copy-sales="email"]')
        await copy_email.scroll_into_view_if_needed()
        await copy_email.click()
        await page.locator("#toast:not(.hidden)").wait_for()
        self.check("复制" in await page.locator("#toast").inner_text(), "desktop outreach copy action gives user feedback")
        await page.locator("#drawerClose").click()

        await page.locator('.section-btn[data-section="opportunities"]').click()
        first_company = page.locator("#oppRows tr[data-company-id]").first
        await first_company.wait_for()
        opp_count = await page.locator("#oppRows tr[data-company-id]").count()
        self.check(opp_count > 0, "desktop opportunities render company rows", opp_count)
        await self.state(page, "desktop", "opportunities")
        await self.shot(page, "desktop-05-opportunities.png")
        sort_button = page.locator("#oppSortOrderBtn")
        before = await sort_button.get_attribute("data-order")
        async with page.expect_response(lambda response: "/api/companies?" in response.url and response.request.method == "GET"):
            await sort_button.click()
        after = await sort_button.get_attribute("data-order")
        self.check(before != after, "desktop opportunity sort direction changes", {"before": before, "after": after})

        await self.goto(page, f"?company={quote('company:elevenlabs')}")
        await self.wait_company(page)
        eleven = await self.company_packet_observation(page, "ElevenLabs")
        self.check(eleven["pricing_tiers"] == 3, "ElevenLabs shows three commercial tiers", eleven["pricing_tiers"])
        self.check(eleven["buyer_rows"] == 5, "ElevenLabs shows five buyer-map states", eleven["buyer_rows"])
        self.check(await page.locator("#saveCampaignFromSalesPacketBtn").count() == 0, "public ElevenLabs packet exposes no write/save control")
        await self.shot(page, "desktop-06-elevenlabs-sales-packet.png", locator=".sales-packet-workspace")

        await page.locator("#salesServiceFeePct").fill("20")
        await page.locator("#salesContingencyPct").fill("10")
        await page.locator("#salesLocalizationFee").fill("500")
        async with page.expect_response(lambda response: "/sales-packet?" in response.url and response.request.method == "GET"):
            await page.locator("#repriceSalesPacketBtn").click()
        await page.locator("#salesServiceFeePct").wait_for()
        await page.wait_for_function(
            "document.querySelector('#salesServiceFeePct')?.value === '20' && document.querySelector('#loadSalesPacketBtn')?.textContent === '重新生成销售包'"
        )
        pricing_text = await page.locator(".pricing-tier-grid").inner_text()
        self.check("待输入" not in pricing_text, "desktop local repricing produces complete scenario totals")
        await self.shot(page, "desktop-07-elevenlabs-repriced.png", locator=".sales-packet-workspace")
        async with page.expect_download() as download_info:
            await page.locator("#exportSalesPacketBtn").click()
        download = await download_info.value
        download_path = self.output_dir / download.suggested_filename
        await download.save_as(download_path)
        self.report["downloads"].append(str(download_path))
        self.check(download_path.stat().st_size > 0, "desktop sales packet exports a non-empty Markdown file", download_path.stat().st_size)

        await self.goto(page, f"?company={quote('company:runway')}")
        runway_mode = await self.wait_company(page)
        runway = await self.company_packet_observation(page, "Runway")
        await self.shot(page, "desktop-08-runway-company.png")
        runway_workspace = ".sales-packet-workspace" if runway_mode == "sales_packet" else ".proposal-workspace"
        await self.shot(page, "desktop-09-runway-campaign-preview.png", locator=runway_workspace)
        overlap = sorted(set(runway["creator_names"]) & set(eleven["creator_names"]))
        self.report["observations"]["runway_elevenlabs_creator_overlap"] = overlap
        self.check(
            len(overlap) < max(1, min(len(runway["creator_names"]), len(eleven["creator_names"]))),
            "Runway creator recommendation is not identical to ElevenLabs",
            {"overlap": overlap, "runway": runway["creator_names"], "elevenlabs": eleven["creator_names"]},
        )
        runway_copy = runway["packet_text_sample"].lower()
        self.check(
            any(token in runway_copy for token in ("vfx", "film", "video", "motion", "cinema", "after effects", "视觉", "视频", "导演", "影视")),
            "Runway mix visibly contains film/video/VFX-specific fit evidence",
            runway["packet_text_sample"],
        )
        self.check(gamma["creator_names"] != runway["creator_names"], "Gamma and Runway creator mixes differ", {"gamma": gamma["creator_names"], "runway": runway["creator_names"]})
        await page.locator("#drawerClose").click()

        await page.locator('.section-btn[data-section="network"]').click()
        await page.locator("#networkBody .home-card-title").first.wait_for()
        await self.state(page, "desktop", "network")
        await self.shot(page, "desktop-10-network.png")
        person_paths = await page.get_by_text("符合证据门槛的人对人路径", exact=False).count()
        self.report["observations"]["verified_person_path_sections"] = person_paths
        archive = page.locator("#networkBody details.research-archive").first
        if await archive.count():
            await archive.locator("summary").click()
            self.check(await archive.get_attribute("open") is not None, "desktop Network archive can be expanded")
        network_company = page.locator("#networkBody [data-company-id]").first
        if await network_company.count():
            await network_company.scroll_into_view_if_needed()
            await network_company.click()
            await page.locator("#drawer:not(.hidden) #canonicalCompanyWorkspace").wait_for()
            self.check(True, "desktop Network route opens its company workspace")
            await page.locator("#drawerClose").click()

        await page.locator('.section-btn[data-section="creators"]').click()
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for()
        tab_counts = {}
        for tab in ("strategic", "distribution", "media", "needs_review"):
            button = page.locator(f'.tab[data-tab="{tab}"]')
            async with page.expect_response(lambda response: "/api/creators?" in response.url and response.request.method == "GET"):
                await button.click()
            await page.wait_for_timeout(150)
            count = await page.locator("#creatorRows tr[data-creator-id]").count()
            tab_counts[tab] = count
            self.check(count > 0, f"desktop creator tab {tab} renders records", count)
        self.report["observations"]["creator_tab_page_counts"] = tab_counts
        await page.locator('.tab[data-tab="strategic"]').click()
        await page.locator("#search").fill("Daniel")
        daniel = page.locator("#creatorRows tr[data-creator-id]", has_text="Daniel").first
        await daniel.wait_for()
        await daniel.click()
        await page.locator("#drawer:not(.hidden) .creator-decision-summary").wait_for()
        creator_text = await page.locator("#drawerBody").inner_text()
        self.report["observations"]["daniel_detail_copy_checks"] = {
            "youtube_unavailable_notice": "YouTube 数据源暂不可用" in creator_text,
            "gamma_mentions": creator_text.lower().count("gamma"),
            "claims_no_repeat": "没有重复" in creator_text or "未发现重复" in creator_text,
            "zero_like_rows": creator_text.count("0 赞"),
        }
        self.check(
            not ("YouTube 数据源暂不可用" in creator_text and "最近" in creator_text),
            "Daniel detail does not contradict recent-video data with a source-unavailable notice",
        )
        self.check(
            not (("没有重复" in creator_text or "未发现重复" in creator_text) and creator_text.lower().count("gamma") >= 2),
            "Daniel detail does not deny repeated Gamma evidence while showing multiple observations",
        )
        await self.state(page, "desktop", "creator-detail")
        await self.shot(page, "desktop-11-daniel-detail.png")
        await page.locator("#drawerClose").click()
        await page.locator("#search").fill("")
        await page.locator('.tab[data-tab="media"]').click()
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for()
        await self.state(page, "desktop", "creators-media")
        await self.shot(page, "desktop-12-creators-media.png")

        await page.close()

    async def mobile(self, context: BrowserContext) -> None:
        page = await context.new_page()
        page.set_default_timeout(60_000)
        self.watch(page, "mobile")

        await self.goto(page)
        await page.locator(".pursue-section .decision-card").first.wait_for()
        await self.state(page, "mobile", "home")
        await self.shot(page, "mobile-01-home.png")

        await page.locator('.section-btn[data-section="opportunities"]').click()
        first_company = page.locator("#oppRows tr[data-company-id]").first
        await first_company.wait_for()
        await self.state(page, "mobile", "opportunities")
        await self.shot(page, "mobile-02-opportunities.png")
        await first_company.scroll_into_view_if_needed()
        await self.shot(page, "mobile-03-opportunity-card.png")
        await first_company.click()
        await page.locator("#drawer:not(.hidden) #canonicalCompanyWorkspace").wait_for()
        await self.state(page, "mobile", "company-drawer")
        await self.shot(page, "mobile-04-company-drawer.png")
        sales_packet = page.locator(".sales-packet-result")
        if await sales_packet.count():
            await sales_packet.scroll_into_view_if_needed()
            await self.shot(page, "mobile-05-sales-packet.png")
        await page.locator("#drawerClose").click()

        await page.locator('.section-btn[data-section="network"]').click()
        await page.locator("#networkBody .home-card-title").first.wait_for()
        await self.state(page, "mobile", "network")
        await self.shot(page, "mobile-06-network.png")

        await page.locator('.section-btn[data-section="creators"]').click()
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for()
        await page.locator('.tab[data-tab="media"]').click()
        media_row = page.locator("#creatorRows tr[data-creator-id]").first
        await media_row.wait_for()
        await self.state(page, "mobile", "creators-media")
        await self.shot(page, "mobile-07-creators-media.png")
        await media_row.click()
        await page.locator("#drawer:not(.hidden) .creator-decision-summary").wait_for()
        await self.state(page, "mobile", "creator-detail")
        await self.shot(page, "mobile-08-creator-detail.png")
        await page.locator("#drawerClose").click()

        await page.locator("#globalSearch").fill("Gamma")
        gamma = page.locator('.search-result-row[data-kind="company"][data-id="company:gamma"]')
        await gamma.wait_for()
        await gamma.click()
        await self.wait_company(page)
        await self.state(page, "mobile", "gamma-from-search")
        await self.shot(page, "mobile-09-gamma-from-search.png")
        await page.close()

    def finish(self) -> dict:
        app_errors = [
            event
            for event in self.report["browser_events"]
            if event["same_origin"] and event["kind"] in {"console_error", "page_error", "request_failed", "http_error"}
        ]
        self.check(not app_errors, "no same-origin console/page/network errors across production workflow", app_errors)
        self.report["ok"] = not self.report["failures"]
        json_path = self.output_dir / "production-visual-uat.json"
        json_path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = [
            "# Production visual UAT",
            "",
            f"- Captured: `{self.report['captured_at']}`",
            f"- Production: `{self.base_url}`",
            "- Browser: standalone Playwright + headless Chromium/Chrome",
            "- Viewports: desktop 1440×1000; mobile 390×844",
            "- Mode: public read-only; no production write was attempted",
            f"- Result: **{'PASS' if self.report['ok'] else 'FAIL'}**",
            "",
            "## Checks",
            "",
        ]
        for row in self.report["checks"]:
            detail = f": `{row['detail']}`" if row["detail"] not in ("", None, [], {}) else ""
            lines.append(f"- {'PASS' if row['ok'] else 'FAIL'} — {row['label']}{detail}")
        lines += ["", "## Browser events", ""]
        if self.report["browser_events"]:
            for event in self.report["browser_events"]:
                lines.append(f"- {event['device']} · {event['kind']} · {event['detail']} · `{event['url']}`")
        else:
            lines.append("- None")
        lines += ["", "## Evidence", ""]
        lines.extend(f"- `{Path(path).name}`" for path in self.report["screenshots"])
        (self.output_dir / "production-visual-uat.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.report


async def run(base_url: str, output_dir: Path, chrome: Path | None) -> dict:
    audit = Audit(base_url, output_dir)
    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright, chrome)
        desktop = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            accept_downloads=True,
        )
        await desktop.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url.rstrip("/"))
        try:
            await audit.desktop(desktop)
        finally:
            await desktop.close()

        mobile = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=1,
            is_mobile=True,
            has_touch=True,
            accept_downloads=True,
        )
        await mobile.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url.rstrip("/"))
        try:
            await audit.mobile(mobile)
        finally:
            await mobile.close()
        await browser.close()
    return audit.finish()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chrome", type=Path, default=DEFAULT_CHROME if DEFAULT_CHROME.exists() else None)
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, args.output_dir, args.chrome))
    print(json.dumps({"ok": report["ok"], "checks": len(report["checks"]), "failures": report["failures"], "screenshots": len(report["screenshots"]), "browser_events": len(report["browser_events"])}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
