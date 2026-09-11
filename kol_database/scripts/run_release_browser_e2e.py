"""Full browser release E2E against a self-managed disposable SQLite DB.

This command always copies ``deploy_seed.db`` into a temporary directory,
starts its own loopback Uvicorn process, enables the internal-access gate,
and then performs real UI writes. It cannot target production or the normal
``data/kol.db``. The temporary database is destroyed after evidence is saved.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import openpyxl
from playwright.async_api import async_playwright

from backend.access import ACCESS_COOKIE_NAME, build_access_link
from capture_uat_screenshots import _launch_browser


APP_ROOT = Path(__file__).resolve().parent.parent
SEED_DB = APP_ROOT / "deploy_seed.db"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_health(base_url: str, process, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.returncode is not None:
            raise RuntimeError(f"temporary UAT server exited with {process.returncode}")
        try:
            status = await asyncio.to_thread(
                lambda: urlopen(f"{base_url}/healthz", timeout=1).status
            )
            if status == 200:
                return
        except Exception:
            pass
        await asyncio.sleep(0.1)
    raise TimeoutError("temporary UAT server did not become healthy")


async def _browser_workflow(
    base_url: str,
    token: str,
    chrome: Path | None,
    output_dir: Path,
) -> dict:
    report = {
        "passed": [],
        "failed": [],
        "writes": [],
        "same_origin_http_errors": [],
        "same_origin_request_failures": [],
        "expected_download_aborts": [],
        "external_http_errors": [],
        "external_request_failures": [],
        "page_errors": [],
        "console_errors": [],
        "application_console_errors": [],
        "observations": {},
        "outreach_edit_supported": None,
    }

    def check(condition: bool, label: str) -> None:
        (report["passed"] if condition else report["failed"]).append(label)

    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright, chrome)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1100},
            accept_downloads=True,
        )
        page = await context.new_page()

        def is_same_origin(url: str) -> bool:
            return url.startswith(f"{base_url}/") or url == base_url

        def is_expected_download_abort(url: str, failure: str) -> bool:
            return (
                failure == "net::ERR_ABORTED"
                and is_same_origin(url)
                and ("/export.csv" in url or "/export.xlsx" in url)
            )

        def on_request(request) -> None:
            if is_same_origin(request.url) and request.method not in {
                "GET",
                "HEAD",
                "OPTIONS",
            }:
                report["writes"].append(
                    {"method": request.method, "url": request.url}
                )

        page.on("request", on_request)
        def on_response(response) -> None:
            if response.status < 400:
                return
            item = {
                "status": response.status,
                "url": response.url,
                "resource_type": response.request.resource_type,
            }
            if is_same_origin(response.url):
                report["same_origin_http_errors"].append(item)
            else:
                item["classification"] = (
                    "automation_or_access_block"
                    if response.status in {401, 403, 429}
                    else "broken_external_resource"
                    if response.status == 404 or response.status >= 500
                    else "external_http_error"
                )
                report["external_http_errors"].append(item)

        def on_request_failed(request) -> None:
            failure = request.failure or "failed"
            item = {
                "url": request.url,
                "failure": failure,
                "resource_type": request.resource_type,
            }
            if is_expected_download_abort(request.url, failure):
                report["expected_download_aborts"].append(item)
            elif is_same_origin(request.url):
                report["same_origin_request_failures"].append(item)
            else:
                report["external_request_failures"].append(item)

        def on_console(message) -> None:
            if message.type != "error":
                return
            location = message.location or {}
            item = {
                "text": message.text,
                "url": location.get("url", ""),
                "line": location.get("lineNumber"),
                "column": location.get("columnNumber"),
            }
            report["console_errors"].append(item)
            # Cross-origin assets can legitimately return 401/403/429 to an
            # automated browser. Keep them visible in evidence, but reserve
            # release failure for our own code/origin or an unattributed JS
            # exception. External 404/5xx are asserted separately below.
            if not item["url"] or is_same_origin(item["url"]):
                report["application_console_errors"].append(item)

        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("console", on_console)

        # Exercise the same no-password bootstrap that internal recipients
        # use. The signed values are carried in the URL fragment (never sent
        # in the HTTP request), then exchanged for a scoped HttpOnly cookie.
        access_link = build_access_link(
            base_url,
            token,
            expires=int(time.time()) + 300,
            next_path="/?section=opportunities",
        )
        await page.goto(access_link, wait_until="domcontentloaded")
        await page.wait_for_url(f"{base_url}/?section=opportunities")
        cookies = await context.cookies(base_url)
        access_cookie = next(
            (cookie for cookie in cookies if cookie["name"] == ACCESS_COOKIE_NAME),
            None,
        )
        check(bool(access_cookie), "Signed fragment link establishes an access cookie")
        check(
            bool(access_cookie and access_cookie["httpOnly"]),
            "Internal access cookie is HttpOnly",
        )
        check("#" not in page.url, "Access fragment is cleared before entering the cockpit")
        # The bootstrap redirect reaches DOMContentLoaded before the app's
        # async init applies its requested deep link. Wait for that initial
        # module to settle so a subsequent Home click cannot be overwritten
        # by the still-running init routine.
        await page.locator(
            "#opportunitiesSection:not(.hidden) #oppRows tr[data-company-id]"
        ).first.wait_for(state="visible")

        # A recipient can navigate from Home into every top-level module and
        # back using the visible controls (not just crafted deep links).
        await page.locator('.section-btn[data-section="home"]').click()
        await page.locator("#homeSection:not(.hidden) .today-action-row").first.wait_for(
            state="visible"
        )
        for section, marker in (
            ("opportunities", "#oppRows tr[data-company-id]"),
            ("network", ".interaction-audit"),
            ("creators", "#creatorRows tr[data-creator-id]"),
        ):
            await page.locator(f'.section-btn[data-section="{section}"]').click()
            await page.locator(f"#{section}Section:not(.hidden)").wait_for(
                state="visible"
            )
            await page.locator(marker).first.wait_for(state="visible")
            check(
                await page.locator(
                    f'.section-btn[data-section="{section}"]'
                ).get_attribute("aria-current")
                == "page",
                f"Home navigation opens {section}",
            )
            await page.locator('.section-btn[data-section="home"]').click()
            await page.locator(
                "#homeSection:not(.hidden) .today-action-row"
            ).first.wait_for(state="visible")
        await page.locator('.section-btn[data-section="opportunities"]').click()

        # Search + Opportunities pagination/sort/filter.
        await page.locator("#oppRows tr[data-company-id]").first.wait_for(state="visible")
        await page.locator("#globalSearch").fill("Runway")
        await page.locator('.search-result-row[data-kind="company"]').first.wait_for(
            state="visible"
        )
        search_text = await page.locator(
            '.search-result-row[data-kind="company"]'
        ).first.inner_text()
        check("Runway" in search_text, "Global search finds Runway")
        await page.locator("#globalSearch").fill("")

        first_page_company = await page.locator(
            "#oppRows tr[data-company-id]"
        ).first.get_attribute("data-company-id")
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/companies?" in response.url
            and "page=2" in response.url
        ) as opp_page_two_info:
            await page.locator("#oppNextPage").click()
        check((await opp_page_two_info.value).ok, "Opportunity next-page request succeeds")
        await page.locator("#oppPagination", has_text="第 2 /").wait_for(
            state="visible"
        )
        second_page_company = await page.locator(
            "#oppRows tr[data-company-id]"
        ).first.get_attribute("data-company-id")
        check(
            bool(second_page_company and second_page_company != first_page_company),
            "Opportunity pagination changes the real table",
        )
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/companies?" in response.url
            and "page=1" in response.url
        ) as opp_page_one_info:
            await page.locator("#oppPrevPage").click()
        check((await opp_page_one_info.value).ok, "Opportunity previous-page request succeeds")
        await page.locator("#oppPagination", has_text="第 1 /").wait_for(
            state="visible"
        )

        await page.locator("#oppSortField").select_option("name")
        if await page.locator("#oppSortOrderBtn").get_attribute("data-order") == "desc":
            await page.locator("#oppSortOrderBtn").click()
        await page.wait_for_timeout(500)
        company_names = await page.locator(
            "#oppRows tr[data-company-id] .creator-name"
        ).all_inner_texts()
        check(
            company_names[:10] == sorted(company_names[:10], key=str.casefold),
            "Opportunity name sort ascending changes the real table",
        )
        high_chip = page.locator('.priority-chip[data-priority="High"]')
        await high_chip.click()
        await page.wait_for_timeout(400)
        badges = await page.locator(
            "#oppRows tr td:nth-child(2) .priority-badge"
        ).all_inner_texts()
        check(
            bool(badges) and all(badge.strip() == "商业优先级：高" for badge in badges),
            "Opportunity High filter constrains rows",
        )
        await high_chip.click()

        # Three deterministic decision cases: bounded negative interaction
        # evidence, strongest spend-mechanism signal without reachability, and
        # the closest real relationship/weak-budget counter-case. These are
        # visible UI assertions, not API-only checks.
        await page.goto(
            f"{base_url}/?company=company%3Aheurist",
            wait_until="domcontentloaded",
        )
        await page.locator("#drawer:not(.hidden) .exec-summary").wait_for(state="visible")
        heurist_audit = page.locator(
            "#drawerBody details.alt-paths-toggle",
            has=page.locator("summary", has_text="互动记录审计"),
        ).first
        await heurist_audit.locator("summary").click()
        heurist_text = await page.locator("#drawerBody").inner_text()
        check(
            "本次查询范围内未发现（未发现≠不认识）" in heurist_text
            and "不代表两人不认识" in heurist_text,
            "Heurist UI bounds a no-public-interaction search miss",
        )
        check(
            "公开互关或互动不等于真实认识，更不等于愿意引荐" in heurist_text
            and "E1" in heurist_text,
            "Heurist UI keeps E1 candidates in internal verification",
        )
        await page.goto(
            f"{base_url}/?company=company%3Aakool",
            wait_until="domcontentloaded",
        )
        await page.locator("#drawer:not(.hidden) .exec-summary").wait_for(state="visible")
        akool_text = await page.locator("#drawerBody").inner_text()
        check(
            "暂无可执行渠道，先补证据与负责人" in akool_text
            and "当前负责人候选：尚未识别" in akool_text,
            "AKOOL UI does not turn an L3 spend signal into a credible route",
        )
        await page.goto(
            f"{base_url}/?company=company%3Asapien",
            wait_until="domcontentloaded",
        )
        await page.locator("#drawer:not(.hidden) .exec-summary").wait_for(state="visible")
        sapien_text = await page.locator("#drawerBody").inner_text()
        check(
            "E2" in sapien_text and "仅有能力或资金信号" in sapien_text,
            "Sapien UI shows public-interaction proximity beside weak L1 spend evidence",
        )
        check(
            "不代表真实认识或愿意介绍" in sapien_text,
            "Sapien UI does not promote public interaction to a confirmed intro",
        )

        # Deep-link a deterministic company and inspect evidence/operator/route.
        await page.goto(
            f"{base_url}/?company=company%3Arunway",
            wait_until="domcontentloaded",
        )
        await page.locator("#drawer:not(.hidden) .exec-summary").wait_for(state="visible")
        drawer_text = await page.locator("#drawerBody").inner_text()
        for label in (
            "决策摘要",
            "投放与时机证据",
            "关系事实",
            "目标负责人",
            "Cristóbal Valenzuela",
            "预算影响力未核实",
            "公开 X",
        ):
            check(label in drawer_text, f"Runway detail shows {label}")

        # Opportunity -> campaign -> suggested creator -> rate/budget.
        campaign_objective = f"Release UAT campaign {int(time.time())}"
        await page.locator("#buildCampaignBtn").click()
        await page.locator("#cfObjective").fill(campaign_objective)
        await page.locator("#cfBudget").fill("2000")
        await page.locator("#cfAudience").fill("AI creators and developers")
        await page.locator("#cfRegion").fill("North America")
        await page.locator("#cfLanguage").fill("English")
        await page.locator("#cfPlatforms").fill("X, YouTube")
        await page.locator("#cfTiming").fill("September pilot")
        async with page.expect_response(
            lambda response: response.request.method == "POST"
            and "/api/companies/company%3Arunway/campaigns" in response.url
        ) as campaign_response_info:
            await page.locator("#cfSubmit").click()
        campaign_response = await campaign_response_info.value
        check(campaign_response.ok, "Campaign creation POST succeeds")
        campaign_payload = await campaign_response.json()
        shortlist_id = int(campaign_payload["id"])
        await page.locator("#shortlistModal:not(.hidden)").wait_for(state="visible")
        check(
            campaign_objective
            == await page.locator("#shortlistObjective").input_value(),
            "Campaign objective persists into shortlist modal",
        )

        suggested = page.locator('.suggested-add[data-creator-id="2"]')
        await suggested.wait_for(state="visible")
        async with page.expect_response(
            lambda response: response.request.method == "POST"
            and f"/api/shortlists/{shortlist_id}/items" in response.url
        ) as add_response_info:
            await suggested.click()
        check((await add_response_info.value).ok, "Suggested creator add POST succeeds")
        creator_row = page.locator("#shortlistModal tr[data-item-id]", has_text="Fran_actua")
        await creator_row.wait_for(state="visible")

        detail_response = await context.request.get(
            f"{base_url}/api/shortlists/{shortlist_id}"
        )
        detail_before = await detail_response.json()
        fran_before = next(
            item for item in detail_before["items"] if item["creator_id"] == 2
        )
        check(
            (fran_before["notes"] or "").startswith("推荐理由快照（加入时）："),
            "Suggested creator selection reason persists on the campaign item",
        )
        check(
            "选择理由 / 备注" in await creator_row.inner_text(),
            "Persisted selection reason remains visible after shortlist rerender",
        )
        check(
            fran_before["quote_usd"] == 80,
            "Suggested creator defaults to the lowest available parsed numeric/source rate",
        )
        check(detail_before["remaining_budget_usd"] == 1920, "Budget recalculates after creator add")

        rate_select = creator_row.locator(".item-deliverable")
        async with page.expect_response(
            lambda response: response.request.method == "PATCH"
            and f"/api/shortlists/{shortlist_id}/items/" in response.url
        ) as rate_response_info:
            await rate_select.select_option("3")
        check((await rate_response_info.value).ok, "Deliverable/rate PATCH succeeds")
        await page.wait_for_timeout(400)
        detail_after_rate = await (
            await context.request.get(f"{base_url}/api/shortlists/{shortlist_id}")
        ).json()
        fran_after = next(
            item for item in detail_after_rate["items"] if item["creator_id"] == 2
        )
        check(fran_after["rate_card_id"] == 3, "Changed deliverable persists")
        check(fran_after["quote_usd"] == 200, "Changed rate is reflected in item quote")
        check(detail_after_rate["remaining_budget_usd"] == 1800, "Budget recalculates after rate change")

        # A campaign-only negotiated quote must update every budget bucket
        # without rewriting Fran's source rate card, and clearing it must
        # restore the selected deliverable's source quote.
        creator_row = page.locator("#shortlistModal tr[data-item-id]", has_text="Fran_actua")
        quote_input = creator_row.locator(".item-quote-override")
        await quote_input.fill("2600")
        async with page.expect_response(
            lambda response: response.request.method == "PATCH"
            and f"/api/shortlists/{shortlist_id}/items/" in response.url
        ) as override_response_info:
            await creator_row.locator(".item-quote-save").click()
        check((await override_response_info.value).ok, "Campaign quote override PATCH succeeds")
        await page.wait_for_timeout(300)
        override_detail = await (
            await context.request.get(f"{base_url}/api/shortlists/{shortlist_id}")
        ).json()
        fran_override = next(
            item for item in override_detail["items"] if item["creator_id"] == 2
        )
        check(fran_override["quote_is_override"] and fran_override["quote_usd"] == 2600, "Campaign override becomes the effective quote")
        check(override_detail["total_spend_usd"] == 2600, "Total spend refreshes from campaign override")
        check(override_detail["remaining_budget_usd"] == -600 and override_detail["over_budget"], "Remaining budget and over-budget flag refresh")
        bucket_total = sum(
            override_detail[key]
            for key in (
                "strategic_spend_usd",
                "distribution_spend_usd",
                "media_spend_usd",
                "needs_review_spend_usd",
            )
        )
        check(bucket_total == 2600, "All role-specific spend buckets refresh from override")
        override_provenance = await page.locator(
            "#shortlistModal tr[data-item-id] .quote-provenance"
        ).inner_text()
        check(
            "本次活动手工报价" in override_provenance and "源报价" in override_provenance,
            "UI labels campaign override separately from source quote",
        )
        creator_row = page.locator("#shortlistModal tr[data-item-id]", has_text="Fran_actua")
        await creator_row.locator(".item-quote-override").fill("")
        async with page.expect_response(
            lambda response: response.request.method == "PATCH"
            and f"/api/shortlists/{shortlist_id}/items/" in response.url
        ) as clear_override_response_info:
            await creator_row.locator(".item-quote-save").click()
        check((await clear_override_response_info.value).ok, "Clearing campaign quote override PATCH succeeds")
        await page.wait_for_timeout(300)
        restored_detail = await (
            await context.request.get(f"{base_url}/api/shortlists/{shortlist_id}")
        ).json()
        fran_restored = next(
            item for item in restored_detail["items"] if item["creator_id"] == 2
        )
        check(not fran_restored["quote_is_override"] and fran_restored["quote_usd"] == 200, "Clearing override restores selected source rate")
        check(restored_detail["total_spend_usd"] == 200 and not restored_detail["over_budget"], "Budget summary restores after override clear")

        # Save metadata/context, close, reopen, and export both formats.
        shortlist_name = f"Release UAT Runway {shortlist_id}"
        await page.locator("#shortlistName").fill(shortlist_name)
        await page.locator("#shortlistBudget").fill("2500")
        async with page.expect_response(
            lambda response: response.request.method == "PATCH"
            and response.url.endswith(f"/api/shortlists/{shortlist_id}")
        ) as meta_response_info:
            await page.locator("#saveShortlistMetaBtn").click()
        check((await meta_response_info.value).ok, "Shortlist metadata save succeeds")
        await page.locator("#shortlistTiming").fill("September week 2")
        async with page.expect_response(
            lambda response: response.request.method == "PATCH"
            and response.url.endswith(f"/api/shortlists/{shortlist_id}")
        ) as context_response_info:
            await page.locator("#saveCampaignFieldsBtn").click()
        check((await context_response_info.value).ok, "Campaign context save succeeds")
        await page.locator("#shortlistClose").click()
        await page.locator("#openCurrentShortlistBtn").click()
        await page.locator("#shortlistModal:not(.hidden)").wait_for(state="visible")
        check(
            await page.locator("#shortlistName").input_value() == shortlist_name,
            "Saved shortlist name survives close/reopen",
        )
        check(
            await page.locator("#shortlistBudget").input_value() == "2500",
            "Saved budget survives close/reopen",
        )
        reopened_row = page.locator("#shortlistModal tr[data-item-id]", has_text="Fran_actua")
        check(
            await reopened_row.locator(".item-deliverable").input_value() == "3",
            "Selected deliverable survives close/reopen",
        )

        csv_path = output_dir / "release-uat-shortlist.csv"
        async with page.expect_download() as csv_download_info:
            await page.locator(
                f'a[href="/api/shortlists/{shortlist_id}/export.csv"]'
            ).click()
        csv_download = await csv_download_info.value
        await csv_download.save_as(csv_path)
        csv_text = csv_path.read_text(encoding="utf-8-sig")
        check("Fran_actua" in csv_text and "Creator" in csv_text, "CSV export contains saved creator")

        xlsx_path = output_dir / "release-uat-shortlist.xlsx"
        async with page.expect_download() as xlsx_download_info:
            await page.locator(
                f'a[href="/api/shortlists/{shortlist_id}/export.xlsx"]'
            ).click()
        xlsx_download = await xlsx_download_info.value
        await xlsx_download.save_as(xlsx_path)
        workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        cells = [
            value
            for row in workbook.active.iter_rows(values_only=True)
            for value in row
        ]
        workbook.close()
        check("Fran_actua" in cells, "XLSX export opens and contains saved creator")
        await page.locator("#shortlistClose").click()

        # Manager create -> switch -> delete is exercised only on this
        # disposable DB. Keep the campaign shortlist and delete the throwaway
        # manager row so the rest of the workflow still has a current list.
        await page.locator("#shortlistManagerBtn").click()
        await page.locator("#managerModal:not(.hidden)").wait_for(state="visible")
        manager_name = f"Release UAT manager {shortlist_id}"
        await page.locator("#newShortlistName").fill(manager_name)
        async with page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.endswith("/api/shortlists")
        ) as manager_create_info:
            await page.locator("#createShortlistBtn").click()
        manager_create_response = await manager_create_info.value
        check(manager_create_response.ok, "Shortlist manager create succeeds")
        manager_payload = await manager_create_response.json()
        manager_shortlist_id = int(manager_payload["id"])
        manager_row = page.locator(
            "#managerBody tr",
            has=page.locator(
                f'.switch-shortlist[data-id="{manager_shortlist_id}"]'
            ),
        )
        await manager_row.wait_for(state="visible")
        check("（当前）" in await manager_row.inner_text(), "Created manager list becomes current")
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and response.url.endswith(f"/api/shortlists/{shortlist_id}")
        ) as manager_switch_info:
            await page.locator(
                f'.switch-shortlist[data-id="{shortlist_id}"]'
            ).click()
        check((await manager_switch_info.value).ok, "Shortlist manager switch succeeds")
        await page.wait_for_function(
            "expected => localStorage.getItem('mango_kol_shortlist_id') === String(expected)",
            arg=shortlist_id,
        )
        current_row = page.locator(
            "#managerBody tr",
            has=page.locator(f'.switch-shortlist[data-id="{shortlist_id}"]'),
        )
        await current_row.wait_for(state="visible")
        check("（当前）" in await current_row.inner_text(), "Manager switch updates the current list")
        page.once("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
        async with page.expect_response(
            lambda response: response.request.method == "DELETE"
            and response.url.endswith(f"/api/shortlists/{manager_shortlist_id}")
        ) as manager_delete_info:
            await page.locator(
                f'.delete-shortlist[data-id="{manager_shortlist_id}"]'
            ).click()
        check((await manager_delete_info.value).ok, "Shortlist manager delete succeeds")
        await page.locator(
            f'.switch-shortlist[data-id="{manager_shortlist_id}"]'
        ).wait_for(state="detached")
        check(True, "Deleted throwaway shortlist disappears from manager")
        await page.locator("#managerClose").click()

        # ActionItem operational edit plus append-only OutreachLog result and
        # auditable void semantics.
        await page.goto(
            f"{base_url}/?company=company%3Arunway",
            wait_until="domcontentloaded",
        )
        action_toggle = page.locator(".action-edit-toggle").first
        await action_toggle.wait_for(state="visible")
        action_id = await action_toggle.get_attribute("data-action-id")
        await action_toggle.click()
        action_form = page.locator(f"#action-edit-{action_id}")
        await action_form.locator('[data-field="owner"]').fill("Release UAT Action")
        await action_form.locator('[data-field="status"]').select_option("in_progress")
        await action_form.locator('[data-field="due_date"]').fill("2026-09-22")
        await action_form.locator('[data-field="primary_next_action"]').fill(
            "Release UAT next step"
        )
        await action_form.locator('[data-field="fallback"]').fill(
            "Release UAT fallback"
        )
        await action_form.locator('[data-field="success_condition"]').fill(
            "Release UAT success condition"
        )
        await action_form.locator('[data-field="outcome_notes"]').fill(
            "Disposable ActionItem edit proof"
        )
        async with page.expect_response(
            lambda response: response.request.method == "PATCH"
            and response.url.endswith(f"/api/action-items/{action_id}")
        ) as action_edit_info:
            await action_form.locator(".action-save").click()
        check((await action_edit_info.value).ok, "ActionItem operational edit PATCH succeeds")
        company_after_action = await (
            await context.request.get(
                f"{base_url}/api/companies/company%3Arunway"
            )
        ).json()
        edited_action = next(
            item
            for item in company_after_action["action_items"]
            if str(item["id"]) == str(action_id)
        )
        check(
            edited_action["owner"] == "Release UAT Action"
            and edited_action["status"] == "in_progress"
            and edited_action["due_date"] == "2026-09-22"
            and edited_action["primary_next_action"] == "Release UAT next step"
            and edited_action["fallback"] == "Release UAT fallback"
            and edited_action["success_condition"] == "Release UAT success condition"
            and edited_action["outcome_notes"] == "Disposable ActionItem edit proof",
            "ActionItem UI edit persists required fields and operational notes",
        )
        check(
            company_after_action["current_work_kind"] == "action_item"
            and company_after_action["current_workflow_status"] == "in_progress"
            and company_after_action["next_action_source"] == "stored_action_item"
            and company_after_action["next_action"]
            == edited_action["primary_next_action"]
            and company_after_action["owner"] == edited_action["owner"]
            and company_after_action["due_date"] == edited_action["due_date"]
            and company_after_action["current_fallback"]
            == edited_action["fallback"]
            and company_after_action["has_scheduled_action"],
            "Active current work is one internally consistent persisted ActionItem",
        )

        # Terminal workflow state must remove the company from the Home
        # operational queue.  Exercise both terminal values through the real
        # editor, then restore the disposable row so the outreach continuation
        # workflow below still starts from an active ActionItem.
        async def set_action_status(status: str):
            await page.goto(
                f"{base_url}/?company=company%3Arunway",
                wait_until="domcontentloaded",
            )
            toggle = page.locator(
                f'.action-edit-toggle[data-action-id="{action_id}"]'
            )
            await toggle.wait_for(state="visible")
            await toggle.click()
            form = page.locator(f"#action-edit-{action_id}")
            await form.locator('[data-field="status"]').select_option(status)
            async with page.expect_response(
                lambda response: response.request.method == "PATCH"
                and response.url.endswith(f"/api/action-items/{action_id}")
            ) as terminal_edit_info:
                await form.locator(".action-save").click()
            return await terminal_edit_info.value

        for terminal_status in ("done", "blocked"):
            terminal_response = await set_action_status(terminal_status)
            check(
                terminal_response.ok,
                f"ActionItem UI can persist terminal status {terminal_status}",
            )
            terminal_detail = await (
                await context.request.get(
                    f"{base_url}/api/companies/company%3Arunway"
                )
            ).json()
            check(
                terminal_detail["action_status"] == terminal_status
                and terminal_detail["current_work_kind"]
                == "unscheduled_research_suggestion"
                and terminal_detail["current_workflow_status"] is None
                and terminal_detail["next_action_source"]
                == "unscheduled_research_suggestion"
                and terminal_detail["owner"] is None
                and terminal_detail["due_date"] is None
                and not terminal_detail["has_scheduled_action"],
                f"Terminal {terminal_status} history is not presented as scheduled current work",
            )
            await page.locator(
                "#drawerBody", has_text="历史行动项（不等于当前工作）"
            ).wait_for(state="visible")
            terminal_summary = await page.locator(".exec-summary").inner_text()
            check(
                "未排期研究建议 · 未创建行动项" in terminal_summary
                and "未认领 / 未排期" in terminal_summary,
                f"Company UI labels terminal {terminal_status} as an unscheduled suggestion",
            )
            home_response = await context.request.get(f"{base_url}/api/home/summary")
            home_payload = await home_response.json()
            home_company_ids = {
                row["company_id"]
                for row in home_payload.get("top_priority_companies", [])
            }
            check(
                "company:runway" not in home_company_ids,
                f"Home API excludes {terminal_status} ActionItem",
            )
            await page.goto(base_url, wait_until="domcontentloaded")
            await page.locator(
                "#homeSection:not(.hidden) .today-actions-body"
            ).wait_for(state="visible")
            await page.locator(".today-action-row").first.wait_for(state="visible")
            check(
                await page.locator(
                    '.today-action-row[data-company-id="company:runway"]'
                ).count()
                == 0,
                f"Home UI removes {terminal_status} ActionItem from today's queue",
            )

        restored_response = await set_action_status("in_progress")
        check(
            restored_response.ok,
            "Disposable ActionItem is restored before outreach continuation testing",
        )
        await page.locator("#addOutreachLogToggle").wait_for(state="visible")
        await page.locator("#addOutreachLogToggle").click()
        await page.locator("#newOutreachStage").select_option("contact_attempted")
        await page.locator("#newOutreachOperator").select_option(index=1)
        await page.locator("#newOutreachChannel").select_option("dm")
        await page.locator("#newOutreachOwner").fill("Release UAT")
        await page.locator("#newOutreachContactedWho").fill("Cristóbal Valenzuela")
        await page.locator("#newOutreachFollowUp").fill("2026-09-15")
        await page.locator("#newOutreachNotes").fill("Disposable release workflow proof")
        async with page.expect_response(
            lambda response: response.request.method == "POST"
            and "/api/companies/company%3Arunway/outreach-logs" in response.url
        ) as outreach_response_info:
            await page.locator("#addOutreachLogSubmit").click()
        check((await outreach_response_info.value).ok, "OutreachLog create succeeds")
        await page.locator(".outreach-log-void").last.wait_for(state="visible")
        outreach_text = await page.locator("#drawerBody").inner_text()
        check("Release UAT" in outreach_text and "2026-09-15" in outreach_text, "Outreach owner and follow-up persist")

        edit_toggle = page.locator(".outreach-log-edit-toggle").last
        log_id = await edit_toggle.get_attribute("data-log-id")
        await edit_toggle.click()
        edit_form = page.locator(f"#outreach-edit-{log_id}")
        await edit_form.locator('[data-field="owner"]').fill("Release UAT Edited")
        await edit_form.locator('[data-field="next_follow_up_date"]').fill("2026-09-20")
        await edit_form.locator('[data-field="notes"]').fill("Updated follow-up context")
        async with page.expect_response(
            lambda response: response.request.method == "PATCH"
            and response.url.endswith(f"/api/outreach-logs/{log_id}")
        ) as edit_response_info:
            await edit_form.locator(".outreach-log-save").click()
        check((await edit_response_info.value).ok, "Outreach owner/notes/follow-up PATCH succeeds")
        await page.locator("#drawerBody", has_text="Release UAT Edited").wait_for(state="visible")
        edited_text = await page.locator("#drawerBody").inner_text()
        check(
            "Release UAT Edited" in edited_text
            and "2026-09-20" in edited_text
            and "Updated follow-up context" in edited_text,
            "Editable Outreach operational fields persist",
        )

        # A terminal historical ActionItem does not erase a real, non-void
        # outreach continuation.  The live work must come exclusively from
        # that OutreachLog and the UI must not label it as the old task's
        # terminal state.
        terminal_with_outreach = await set_action_status("done")
        check(
            terminal_with_outreach.ok,
            "ActionItem can close while a real OutreachLog continuation remains",
        )
        outreach_current = await (
            await context.request.get(
                f"{base_url}/api/companies/company%3Arunway"
            )
        ).json()
        check(
            outreach_current["action_status"] == "done"
            and outreach_current["current_work_kind"] == "outreach_follow_up"
            and outreach_current["current_workflow_status"]
            == "follow_up_scheduled"
            and outreach_current["next_action_source"]
            == "live_outreach_continuation"
            and outreach_current["owner"] == "Release UAT Edited"
            and outreach_current["due_date"] == "2026-09-20"
            and outreach_current["has_scheduled_action"],
            "Terminal ActionItem plus active outreach derives current work only from OutreachLog",
        )
        await page.locator(
            "#drawerBody", has_text="当前工作由最新非作废 OutreachLog 推进"
        ).wait_for(state="visible")
        outreach_summary = await page.locator(".exec-summary").inner_text()
        check(
            "真实外联跟进 · 外联跟进中" in outreach_summary
            and "当前工作类型 / 状态：真实外联跟进 · 已完成"
            not in outreach_summary,
            "Company decision summary shows outreach follow-up, not the terminal ActionItem state",
        )

        openapi = await (await context.request.get(f"{base_url}/openapi.json")).json()
        outreach_path = openapi.get("paths", {}).get("/api/outreach-logs/{log_id}", {})
        report["outreach_edit_supported"] = "patch" in outreach_path
        check(
            report["outreach_edit_supported"],
            "OutreachLog exposes bounded operational PATCH",
        )
        check(
            "delete" not in outreach_path,
            "OutreachLog has no destructive delete endpoint",
        )
        page.once(
            "dialog",
            lambda dialog: asyncio.create_task(dialog.accept("Release UAT cleanup")),
        )
        async with page.expect_response(
            lambda response: response.request.method == "PATCH"
            and "/api/outreach-logs/" in response.url
            and response.url.endswith("/void")
        ) as void_response_info:
            await page.locator(".outreach-log-void").last.click()
        check((await void_response_info.value).ok, "OutreachLog void PATCH succeeds")
        await page.locator("#drawerBody", has_text="Release UAT cleanup").wait_for(
            state="visible"
        )
        voided_text = await page.locator("#drawerBody").inner_text()
        check("已作废" in voided_text and "Release UAT cleanup" in voided_text, "Voided OutreachLog remains visible with reason")
        after_void = await (
            await context.request.get(
                f"{base_url}/api/companies/company%3Arunway"
            )
        ).json()
        check(
            after_void["action_status"] == "done"
            and after_void["current_work_kind"]
            == "unscheduled_research_suggestion"
            and not after_void["has_scheduled_action"],
            "Voiding the only active outreach returns terminal history to an unscheduled suggestion",
        )
        final_action_restore = await set_action_status("in_progress")
        check(
            final_action_restore.ok,
            "Disposable ActionItem is restored after terminal/outreach semantics proof",
        )

        # Creator search/filter/sort uses real network requests and rows.
        await page.keyboard.press("Escape")
        await page.locator("#drawer").wait_for(state="hidden")
        await page.locator('.section-btn[data-section="creators"]').click()
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for(state="visible")
        check(
            "active"
            in (await page.locator('.tab[data-tab="strategic"]').get_attribute("class") or ""),
            "Creator Strategic tab is the default",
        )
        strategic_first = await page.locator(
            "#creatorRows tr[data-creator-id]"
        ).first.get_attribute("data-creator-id")
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "tab=strategic" in response.url
            and "page=2" in response.url
        ) as creator_page_two_info:
            await page.locator("#nextPage").click()
        check((await creator_page_two_info.value).ok, "Creator next-page request succeeds")
        await page.locator("#pagination", has_text="第 2 /").wait_for(state="visible")
        strategic_second = await page.locator(
            "#creatorRows tr[data-creator-id]"
        ).first.get_attribute("data-creator-id")
        check(
            bool(strategic_second and strategic_second != strategic_first),
            "Creator pagination changes the real table",
        )
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "tab=strategic" in response.url
            and "page=1" in response.url
        ) as creator_page_one_info:
            await page.locator("#prevPage").click()
        check((await creator_page_one_info.value).ok, "Creator previous-page request succeeds")
        await page.locator("#pagination", has_text="第 1 /").wait_for(state="visible")

        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "tab=needs_review" in response.url
        ) as needs_review_info:
            await page.locator('.tab[data-tab="needs_review"]').click()
        check((await needs_review_info.value).ok, "Creator Needs Review tab request succeeds")
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for(state="visible")
        check(
            "active"
            in (await page.locator('.tab[data-tab="needs_review"]').get_attribute("class") or "")
            and "33" in await page.locator("#resultCount").inner_text(),
            "Needs Review tab visibly switches to its 33-row dataset",
        )
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "tab=media" in response.url
        ) as media_tab_info:
            await page.locator('.tab[data-tab="media"]').click()
        check((await media_tab_info.value).ok, "Creator Media / Community tab request succeeds")
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for(state="visible")
        check(
            "active"
            in (await page.locator('.tab[data-tab="media"]').get_attribute("class") or "")
            and "4" in await page.locator("#resultCount").inner_text(),
            "Media / Community tab visibly isolates its 4-row dataset",
        )
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "tab=strategic" in response.url
        ) as strategic_tab_info:
            await page.locator('.tab[data-tab="strategic"]').click()
        check((await strategic_tab_info.value).ok, "Creator Strategic tab request succeeds")
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for(state="visible")

        await page.locator("#columnsToggleBtn").click()
        platform_column_toggle = page.locator(
            '#columnsMenu input[data-col-toggle="platform"]'
        )
        await platform_column_toggle.uncheck()
        check(
            await page.locator('#creatorTable [data-col="platform"]').first.evaluate(
                "el => getComputedStyle(el).display === 'none'"
            ),
            "Column settings hides the Platform column",
        )
        await platform_column_toggle.check()
        check(
            await page.locator('#creatorTable [data-col="platform"]').first.evaluate(
                "el => getComputedStyle(el).display !== 'none'"
            ),
            "Column settings restores the Platform column",
        )
        await page.locator("#columnsToggleBtn").click()
        await page.locator("#creatorRows .row-select-checkbox").first.check()
        await page.locator("#bulkBar:not(.hidden)").wait_for(state="visible")
        check(
            "已选 1 项" in await page.locator("#bulkBarSummary").inner_text(),
            "Bulk selection reports the selected creator count",
        )
        await page.locator("#bulkClearBtn").click()
        check(
            await page.locator("#bulkBar").evaluate("el => el.classList.contains('hidden')"),
            "Bulk clear resets creator selection",
        )

        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "tab=distribution" in response.url
        ) as distribution_response_info:
            await page.locator('.tab[data-tab="distribution"]').click()
        check((await distribution_response_info.value).ok, "Distribution creator tab loads")
        await page.locator("#creatorRows tr[data-creator-id]").first.wait_for(state="visible")
        # Force this representative page's lazy avatars to settle so a
        # blocked/expired CDN URL is judged by the rendered fallback rather
        # than by an in-flight image. The app's onerror handler swaps a failed
        # <img> for initials; no broken-image glyph may remain.
        await page.evaluate(
            """async () => {
              const images = [...document.querySelectorAll('#creatorRows img.avatar')];
              await Promise.all(images.map((img) => new Promise((resolve) => {
                img.loading = 'eager';
                if (img.complete) return resolve();
                const done = () => resolve();
                img.addEventListener('load', done, {once: true});
                img.addEventListener('error', done, {once: true});
                setTimeout(done, 3000);
              })));
            }"""
        )
        broken_avatars = await page.locator("#creatorRows img.avatar").evaluate_all(
            "images => images.filter((img) => img.complete && img.naturalWidth === 0).length"
        )
        avatar_fallbacks = await page.locator(
            "#creatorRows .avatar-fallback"
        ).count()
        report["observations"]["distribution_broken_avatars"] = broken_avatars
        report["observations"]["distribution_avatar_fallbacks"] = avatar_fallbacks
        check(broken_avatars == 0, "Blocked/expired avatars leave no broken-image glyph")
        avatar_failures = [
            item
            for item in (
                report["external_http_errors"]
                + report["external_request_failures"]
            )
            if item.get("resource_type") == "image"
            and any(
                marker in item["url"]
                for marker in ("cdninstagram.com", "tiktokcdn")
            )
        ]
        check(
            not avatar_failures or avatar_fallbacks > 0,
            "Blocked external avatar is visibly replaced by initials",
        )
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "search=Fran_actua" in response.url
        ) as creator_search_response_info:
            await page.locator("#search").fill("Fran_actua")
        check((await creator_search_response_info.value).ok, "Creator search request succeeds")
        await page.locator(
            '#creatorRows tr[data-creator-id="2"] .creator-name'
        ).wait_for(state="visible")
        creator_names = await page.locator(
            "#creatorRows tr[data-creator-id] .creator-name"
        ).all_inner_texts()
        check(creator_names == ["Fran_actua"], "Creator search constrains the real table")
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "tab=distribution" in response.url
            and "search=" not in response.url
        ) as creator_clear_search_info:
            await page.locator("#search").fill("")
        check((await creator_clear_search_info.value).ok, "Creator search clear succeeds")
        x_filter = page.locator(
            '#filterBody input[data-filter-key="platform"][data-filter-value="X"]'
        )
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "platform=X" in response.url
        ) as creator_filter_info:
            await x_filter.check()
        check((await creator_filter_info.value).ok, "Creator platform filter request succeeds")
        await page.wait_for_function(
            """() => {
              const cells = [...document.querySelectorAll('#creatorRows tr[data-creator-id] [data-col="platform"]')];
              return cells.length > 0 && cells.every((cell) => cell.innerText.includes('X'));
            }"""
        )
        platforms = await page.locator(
            '#creatorRows tr[data-creator-id] [data-col="platform"]'
        ).all_inner_texts()
        check(bool(platforms) and all("X" in value for value in platforms), "Creator platform filter constrains rows")
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "platform=" not in response.url
        ) as creator_clear_filter_info:
            await page.locator("#clearFilters").click()
        check((await creator_clear_filter_info.value).ok, "Creator clear-filters request succeeds")
        async with page.expect_response(
            lambda response: response.request.method == "GET"
            and "/api/creators?" in response.url
            and "sort=name" in response.url
        ) as creator_sort_info:
            await page.locator("#sortField").select_option("name")
        check((await creator_sort_info.value).ok, "Creator name-sort request succeeds")
        if await page.locator("#sortOrderBtn").get_attribute("data-order") == "desc":
            async with page.expect_response(
                lambda response: response.request.method == "GET"
                and "/api/creators?" in response.url
                and "sort=name" in response.url
                and "order=asc" in response.url
            ) as creator_ascending_info:
                await page.locator("#sortOrderBtn").click()
            check((await creator_ascending_info.value).ok, "Creator ascending-sort request succeeds")
        sorted_creators = await page.locator(
            "#creatorRows tr[data-creator-id] .creator-name"
        ).all_inner_texts()
        check(
            sorted_creators[:10]
            == sorted(sorted_creators[:10], key=str.casefold),
            "Creator name sort ascending changes the real table",
        )

        # Expanded Network intelligence, details, and cross-entity buttons.
        await page.locator('.section-btn[data-section="network"]').click()
        await page.locator(".interaction-audit").wait_for(state="visible")
        audit_values = await page.locator(
            ".interaction-audit-stats strong"
        ).all_inner_texts()
        check(audit_values == ["74", "70", "15", "3", "12", "55"], "Network interaction audit shows 74/70/15/3/12/55")
        connector_title = await page.locator("#connectorPeopleTitle").inner_text()
        check("68 人" in connector_title, "Network connector pool reports 68 unique people")
        visible_cards = page.locator(
            ".connector-people-section > .connector-people-grid > .connector-person-card"
        )
        check(await visible_cards.count() == 6, "Network renders exactly six top connector cards")
        visible_handles = await visible_cards.locator(".connector-name").all_inner_texts()
        check(len(visible_handles) == len(set(visible_handles)), "Top connector handles are unique in real DOM")
        connector_details = page.locator(".connector-people-section details")
        await connector_details.locator("summary").click()
        check(
            await page.locator(".connector-people-section .connector-person-card").count() == 68,
            "Expanded connector pool renders 68 unique people once",
        )
        sponsor_section = page.locator(".sponsor-intelligence-section")
        check(
            await sponsor_section.locator(
                ".sponsor-intel-card:not(.media-channel)"
            ).count()
            == 5,
            "Sponsor intelligence keeps five creator-type prospects",
        )
        media_details = sponsor_section.locator(".media-intel-toggle")
        await media_details.locator("summary").click()
        check(
            await sponsor_section.locator(".sponsor-intel-card.media-channel").count()
            == 1,
            "Media channel is separate from creator/connector pool",
        )
        await sponsor_section.locator(".sponsor-creator-link").first.click()
        await page.locator("#drawer:not(.hidden) .creator-decision-summary").wait_for(
            state="visible"
        )
        check(True, "Sponsor creator button opens Creator detail")
        await page.keyboard.press("Escape")
        await page.locator('.section-btn[data-section="network"]').click()
        await page.locator(".sponsor-intelligence-section .company-link").first.click()
        await page.locator("#drawer:not(.hidden) .exec-summary").wait_for(state="visible")
        check(True, "Sponsor company button opens Company detail")
        await page.keyboard.press("Escape")

        # Mobile Network proof, including expandable details and no body overflow.
        mobile = await context.new_page()
        await mobile.set_viewport_size({"width": 500, "height": 844})
        await mobile.goto(f"{base_url}/?section=network", wait_until="domcontentloaded")
        await mobile.locator(".interaction-audit").wait_for(state="visible")
        check(
            await mobile.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth + 1"
            ),
            "Network mobile has no document-level horizontal overflow",
        )
        await mobile.locator(".connector-people-section details summary").click()
        check(
            await mobile.locator(
                ".connector-people-section details[open]"
            ).count()
            == 1,
            "Network mobile connector details expands",
        )
        await mobile.close()

        allowed_write_fragments = {
            ("POST", "/api/companies/company%3Arunway/campaigns"),
            ("POST", f"/api/shortlists/{shortlist_id}/items"),
            ("PATCH", f"/api/shortlists/{shortlist_id}/items/"),
            ("PATCH", f"/api/shortlists/{shortlist_id}"),
            ("POST", "/api/shortlists"),
            ("DELETE", f"/api/shortlists/{manager_shortlist_id}"),
            ("PATCH", f"/api/action-items/{action_id}"),
            ("POST", "/api/companies/company%3Arunway/outreach-logs"),
            ("PATCH", "/api/outreach-logs/"),
            ("POST", "/internal/session"),
        }
        unexpected_writes = []
        for write in report["writes"]:
            if not any(
                write["method"] == method and fragment in write["url"]
                for method, fragment in allowed_write_fragments
            ):
                unexpected_writes.append(write)
        check(not unexpected_writes, "No unexpected write endpoint was called")
        check(not report["same_origin_http_errors"], "No same-origin 4xx/5xx occurred")
        check(not report["same_origin_request_failures"], "No same-origin request failed")
        check(not report["page_errors"], "No uncaught page error occurred")
        check(
            not report["application_console_errors"],
            "No application-origin browser console error occurred",
        )
        broken_external = [
            item
            for item in report["external_http_errors"]
            if item["status"] == 404 or item["status"] >= 500
        ]
        check(
            not broken_external,
            "No rendered external resource returned 404/5xx",
        )

        await page.screenshot(
            path=str(output_dir / "local-release-e2e-final.png"),
            full_page=False,
        )
        await context.close()
        await browser.close()

    return report


def _db_evidence(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as connection:
        return {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "shortlists": connection.execute("SELECT COUNT(*) FROM shortlists").fetchone()[0],
            "shortlist_items": connection.execute("SELECT COUNT(*) FROM shortlist_items").fetchone()[0],
            "outreach_logs": connection.execute("SELECT COUNT(*) FROM outreach_logs").fetchone()[0],
            "voided_outreach_logs": connection.execute(
                "SELECT COUNT(*) FROM outreach_logs WHERE voided = 1"
            ).fetchone()[0],
            "uat_edited_action_items": connection.execute(
                "SELECT COUNT(*) FROM action_items WHERE owner = 'Release UAT Action' "
                "AND status = 'in_progress' AND due_date = '2026-09-22'"
            ).fetchone()[0],
        }


async def run(chrome: Path | None, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    seed_hash_before = _sha256(SEED_DB)
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="mangobd-release-e2e-") as temp_dir:
        temp_db = Path(temp_dir) / "kol.db"
        shutil.copy2(SEED_DB, temp_db)
        initial_temp_hash = _sha256(temp_db)
        port = _free_loopback_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            {
                "DB_PATH": str(temp_db),
                "INTERNAL_ACCESS_TOKEN": token,
                "INTERNAL_ACCESS_COOKIE_SECURE": "0",
                "PYTHONPATH": str(APP_ROOT),
            }
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
            cwd=APP_ROOT,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        fatal = None
        workflow = None
        server_stderr = ""
        try:
            await _wait_for_health(base_url, process)
            workflow = await _browser_workflow(base_url, token, chrome, output_dir)
        except Exception as exc:
            fatal = f"{type(exc).__name__}: {exc}"
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                _, stderr = await process.communicate()
            server_stderr = stderr.decode("utf-8", errors="replace")[-4000:]

        final_temp_hash = _sha256(temp_db)
        db_evidence = _db_evidence(temp_db)

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "database_scope": "self-managed temporary copy of deploy_seed.db",
        "production_contacted": False,
        "normal_local_db_contacted": False,
        "seed_hash_before": seed_hash_before,
        "seed_hash_after": _sha256(SEED_DB),
        "initial_temp_hash": initial_temp_hash,
        "final_temp_hash": final_temp_hash,
        "temporary_db_changed": initial_temp_hash != final_temp_hash,
        "db_evidence": db_evidence,
        "fatal": fatal,
        "workflow": workflow,
        "server_stderr": server_stderr if fatal else "",
    }
    return result


def _write_report(result: dict, output_dir: Path) -> None:
    (output_dir / "local-release-e2e.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    workflow = result.get("workflow") or {"passed": [], "failed": []}
    lines = [
        "# Local mutating release E2E",
        "",
        f"- Checked: `{result['checked_at']}`",
        "- DB scope: self-managed temporary copy of `deploy_seed.db`",
        f"- Production contacted: `{result['production_contacted']}`",
        f"- Normal local DB contacted: `{result['normal_local_db_contacted']}`",
        f"- Temporary DB changed: `{result['temporary_db_changed']}`",
        f"- Seed unchanged: `{result['seed_hash_before'] == result['seed_hash_after']}`",
        f"- Passed: `{len(workflow['passed'])}`",
        f"- Failed: `{len(workflow['failed'])}`",
        "",
        "## DB evidence",
        "",
    ]
    lines.extend(f"- {key}: `{value}`" for key, value in result["db_evidence"].items())
    lines += ["", "## Failures", ""]
    if result["fatal"]:
        lines.append(f"- Fatal: `{result['fatal']}`")
    if workflow["failed"]:
        lines.extend(f"- {failure}" for failure in workflow["failed"])
    if not result["fatal"] and not workflow["failed"]:
        lines.append("- None")
    (output_dir / "local-release-e2e.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=APP_ROOT / "reports" / "release_audit_2026-08-29" / "local_e2e",
    )
    args = parser.parse_args()
    result = asyncio.run(run(args.chrome, args.output_dir))
    _write_report(result, args.output_dir)
    workflow = result.get("workflow") or {"passed": [], "failed": []}
    print(
        f"Local release E2E: {len(workflow['passed'])} passed, "
        f"{len(workflow['failed'])} failed"
    )
    if result["fatal"]:
        print(f"  FATAL  {result['fatal']}")
    for failure in workflow["failed"]:
        print(f"  FAIL  {failure}")
    safe = (
        result["production_contacted"] is False
        and result["normal_local_db_contacted"] is False
        and result["seed_hash_before"] == result["seed_hash_after"]
        and result["temporary_db_changed"]
    )
    return 0 if safe and not result["fatal"] and not workflow["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
