"""Frontend decision/content contract tests.

These are precise source-level regressions for the no-silent-shortlist and
decision-hierarchy bugs.  They deliberately do not claim to replace
Playwright interaction coverage.
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")


def function_block(name: str, next_name: str) -> str:
    return APP.split(f"function {name}", 1)[1].split(f"function {next_name}", 1)[0]


class TestExplicitNamedShortlistFlow(unittest.TestCase):
    def test_creator_add_never_silently_creates_a_formal_shortlist(self):
        block = function_block("ensureShortlist", "refreshShortlistState")
        self.assertNotIn('method: "POST"', block)
        self.assertNotIn("未命名候选名单", block)
        self.assertIn("await openManager()", block)
        self.assertIn("return null", block)
        self.assertIn('localStorage.removeItem("mango_kol_shortlist_id")', block)

    def test_single_and_bulk_add_abort_when_no_named_list_is_selected(self):
        toggle = function_block("toggleShortlist", "closeShortlistModal")
        bulk = APP.split('document.getElementById("bulkAddBtn")', 1)[1].split("// ---------------------------------------------------------------------------\n// Column visibility", 1)[0]
        self.assertIn("if (!shortlist) return false", toggle)
        self.assertIn("if (!shortlist) return", bulk)

    def test_existing_list_cannot_be_saved_with_a_blank_name(self):
        block = function_block("wireShortlistModal", "openManager")
        save_block = block.split('document.getElementById("saveShortlistMetaBtn")', 1)[1].split(
            'document.getElementById("copyTsvBtn")', 1
        )[0]
        self.assertIn("nameInput.value.trim()", save_block)
        self.assertIn("候选名单名称不能为空", save_block)
        self.assertLess(save_block.index("if (!name)"), save_block.index("/api/shortlists/${detail.id}"))


class TestDecisionHierarchy(unittest.TestCase):
    def test_product_ui_has_no_personal_name_or_placeholder_english_headings(self):
        for visible_copy in (
            "Solomon BD 决策工作台",
            "Solomon commercial priority",
            "Solomon 优先看这 15 家",
            "Solomon 可执行销售包",
            "均待 Solomon 真实复盘",
            "Campaign concept available",
            "Pricing incomplete",
            "Connector Intelligence",
            "Intent → Operator → Route → Connector → Action",
        ):
            self.assertNotIn(visible_copy, APP)

    def test_home_action_exposes_all_decision_fields_without_nested_scroll(self):
        block = function_block("homeHtml", "wireHome")
        for label in ("为什么现在", "目标负责人", "路径类型", "关系事实", "具体下一步", "执行", "主要阻碍", "失败备选"):
            self.assertIn(label, block)
        self.assertIn("today-actions-body", block)
        self.assertIn(".home-card-body.today-actions-body { max-height: none; overflow: visible; }", STYLES)

    def test_home_distinguishes_internal_checks_and_action_workflow_status(self):
        block = function_block("homeHtml", "wireHome")
        self.assertIn('c.planned_work_type === "internal_relationship_check"', block)
        self.assertIn("内部关系核实（非 warm path / 非引荐请求）", block)
        self.assertIn("公司账号单向关注只作参考", block)
        self.assertIn("currentWorkStatusLabel(c)", block)
        self.assertIn("currentWorkKindLabel(c)", block)
        self.assertIn("c.current_fallback", block)

    def test_opportunity_row_is_a_seven_column_action_view_without_hidden_duplicate_prose(self):
        block = function_block("renderOppTable", "renderOppPagination")
        for label in ("Mango 可提供", "最后核实", "买方待补", "正式渠道待补", "下一步待补"):
            self.assertIn(label, block)
        self.assertNotIn('class="sr-only"', block)
        self.assertNotIn("relationshipTruthSummary(r)", block)
        self.assertNotIn("Mango 可卖</th>", INDEX)
        self.assertEqual(INDEX.split('<table id="oppTable">', 1)[1].split("</thead>", 1)[0].count("<th "), 7)

    def test_company_drawer_leads_with_a_complete_decision_summary(self):
        block = function_block("companyDrawerHtml", "solomonReviewSectionHtml")
        for label in ("决策摘要", "建议：", "为什么现在：", "最强预算/投放证据", "当前负责人候选", "最佳当前路径", "关系事实", "具体下一步", "负责人 / 截止", "主要阻碍", "失败备选"):
            self.assertIn(label, block)
        ordered_sections = (
            "投放与时机证据",
            "目标负责人",
            "关系证据与候选路径",
            "创作者 / 赞助历史与复核",
            "建议合作活动",
            "当前行动",
            "执行结果记录",
            "公司资料与历史 / 参考证据",
        )
        positions = [block.index(label) for label in ordered_sections]
        self.assertEqual(positions, sorted(positions))

    def test_company_drawer_never_splices_live_guidance_with_historical_action_state(self):
        block = function_block("companyDrawerHtml", "solomonReviewSectionHtml")
        self.assertIn("c.next_action", block)
        self.assertIn("c.current_fallback", block)
        self.assertIn("currentWorkStatusLabel(c)", block)
        self.assertIn("行动项记录（当前已排期项置顶；历史状态原样保留）", block)
        self.assertIn("历史行动项（不等于当前工作）", block)
        self.assertIn("实时研究建议（未自动改写行动项）", block)
        self.assertIn("没有沿用行动项的负责人、截止日期或状态", block)
        self.assertNotIn("已被上方最新建议替代", block)

    def test_terminal_outreach_provenance_is_visible_across_decision_surfaces(self):
        status_block = function_block("currentWorkStatusLabel", "currentWorkKindLabel")
        kind_block = function_block("currentWorkKindLabel", "productCopy")
        home_block = function_block("homeHtml", "wireHome")
        opportunity_block = function_block("renderOppTable", "renderOppPagination")
        drawer_block = function_block("companyDrawerHtml", "solomonReviewSectionHtml")

        self.assertIn('current_work_kind === "outreach_terminal_outcome"', status_block)
        self.assertIn("最新外联终态", kind_block)
        for block in (home_block, drawer_block):
            self.assertIn('current_work_kind === "outreach_terminal_outcome"', block)
            self.assertIn("current_source_occurred_at", block)
        self.assertNotIn('current_work_kind === "outreach_terminal_outcome"', opportunity_block)
        self.assertIn("记录人：", home_block)
        self.assertIn("结果时间：", home_block)
        self.assertNotIn("记录人：", opportunity_block)
        self.assertNotIn("结果状态：", opportunity_block)
        self.assertIn("基于最新执行终态的停止 / 改路方案", drawer_block)
        self.assertIn("旧行动项仍保留作审计历史，但不再作为当前工作", drawer_block)

    def test_company_e1_truth_can_name_one_way_and_mutual_edges(self):
        block = function_block("pathDirectionTruth", "relationshipTruthSummary")
        self.assertIn("（互关）", block)
        self.assertIn("（单向关注）", block)
        relationship = function_block("relationshipTruthSummary", "whyNowSummary")
        self.assertIn("不证明认识或愿意引荐", relationship)

    def test_company_connector_summary_exposes_coverage_and_never_hides_remainder(self):
        block = function_block("_reachabilitySummaryBridgesHtml", "_bridgesHtml")
        for label in ("公开互关的中间联系人候选", "公开互动已核查", "找到公开互动", "本次未找到", "尚未核查"):
            self.assertIn(label, block)
        self.assertIn("公开互关或互动不等于真实认识，更不等于愿意引荐", block)
        self.assertIn("还有 ${rest.length} 位中间联系人候选", block)

    def test_creator_detail_states_capability_and_uncertainty(self):
        block = function_block("drawerHtml", "sponsorshipHistoryHtml")
        for label in ("合作判断摘要", "当前角色", "Mango 建联", "真实报价", "历史合作证据", "可能相关公司", "不证明认识预算负责人或愿意引荐"):
            self.assertIn(label, block)
        for label in ("点赞未知", "缓存快照", "推广关键词命中率（启发式）", "重复付费观察待复核"):
            self.assertIn(label, block)

    def test_network_person_paths_are_conditional_and_creator_disclaimer_is_section_level(self):
        block = function_block("routePortfolioHtml", "solomonPilotHtml")
        self.assertIn("verified_person_to_person_paths", block)
        self.assertIn("personPaths.length", block)
        self.assertIn("符合证据门槛的人对人路径", block)
        self.assertIn("历史合作只用于了解采购流程", block)
        self.assertIn("hideWhy: true", block)

    def test_network_uses_delivery_grade_chinese_for_routes_and_actions(self):
        cards = function_block("routePortfolioCards", "routePortfolioHtml")
        self.assertIn('productCopy(row.route || "未命名路线")', cards)
        self.assertIn("productCopy(row.why)", cards)
        self.assertIn("productCopy(row.first_step)", cards)
        for source, translation in (
            (
                "Prepare three enterprise workflow concepts and a credible educator shortlist.",
                "准备 3 个企业工作流方案，并附可信的技术教育创作者名单。",
            ),
            (
                "Prepare a one-page cohort with creator types, formats, market, budget bands, and attribution plan.",
                "准备一页式名单，写清创作者类型、内容形式、目标市场、预算区间和归因方案。",
            ),
            (
                "Frame Mango as a distribution operator, not a software buyer.",
                "将 Mango 定位为企业内容分发合作方，不以软件采购方身份询价。",
            ),
        ):
            self.assertIn(f'"{source}": "{translation}"', APP)

        block = function_block("routePortfolioHtml", "solomonPilotHtml")
        for visible_copy in (
            "优先用于发送提案和冷启动接触",
            "创作者侧采购情报",
            "创作者赞助情报详情",
            "去重后的引荐候选人",
        ):
            self.assertIn(visible_copy, block)
        for stale_copy in (
            "proposal 与冷启动接触",
            "Creator 侧商业情报",
            "已验证的 person-to-person 研究路径",
            "Creator sponsor 情报详情",
            "去重 connector 候选",
        ):
            self.assertNotIn(stale_copy, block)


class TestNetworkAndCampaignTruth(unittest.TestCase):
    def test_network_reports_unique_companies_separately_from_candidates(self):
        block = function_block("_interviewQueueHtml", "networkHtml")
        self.assertIn("const companyIds = new Set", block)
        self.assertIn("家公司 / ${candidateCount} 位候选人", block)
        self.assertIn("firstPerCompany", block)
        self.assertNotIn("候选库共 ${group.count} 条", block)

    def test_network_leads_with_people_dedupe_and_interaction_coverage(self):
        load_block = function_block("loadNetwork", "solomonPilotHtml")
        self.assertIn("queue.people", load_block)
        self.assertIn("queue.interaction_audit", load_block)
        people_block = function_block("connectorPeopleHtml", "networkHtml")
        self.assertIn("按人去重的中间联系人核实队列", people_block)
        self.assertIn("按可询问性与证据强度排序，不按粉丝量", people_block)
        self.assertEqual(people_block.count('<div class="connector-people-grid">${visible.map'), 1)
        audit_block = function_block("interactionAuditHtml", "_connectorPersonCardHtml")
        for label in ("去重公司-人组合", "已核查", "找到公开互动", "本次未找到", "尚未核查", "查询范围与限制"):
            self.assertIn(label, audit_block)
        person_card = function_block("_connectorPersonCardHtml", "connectorPeopleHtml")
        self.assertIn("仍未验证：Mango 是否真实认识此人", person_card)
        self.assertIn("第一问：", person_card)

    def test_connector_first_ask_does_not_prematurely_request_an_intro(self):
        block = function_block("connectorFirstAsk", "routeFallbackText")
        self.assertIn("当前只核实关系，不请求引荐", block)
        queue = function_block("_interviewQueueHtml", "networkHtml")
        self.assertNotIn("item.suggested_question", queue)

    def test_sponsor_intelligence_is_separate_from_media_and_never_claims_intro(self):
        load_block = function_block("loadNetwork", "solomonPilotHtml")
        self.assertIn('/api/network/sponsor-intelligence-prospects', load_block)
        sponsor_block = function_block("sponsorIntelligenceHtml", "networkHtml")
        self.assertIn("data.results || data.prospects", sponsor_block)
        self.assertIn("data.media_channels", sponsor_block)
        self.assertIn("不进入创作者或中间联系人候选池", sponsor_block)
        card_block = function_block("_sponsorProspectCardHtml", "sponsorIntelligenceHtml")
        self.assertIn("引荐准备度：未验证", card_block)
        self.assertIn("第一问：", card_block)
        self.assertIn("不请求引荐", APP)
        self.assertIn("是否认识现任预算负责人", card_block)

    def test_campaign_form_has_persistent_labels_and_honest_scope(self):
        self.assertIn('class="field-label"', APP)
        self.assertIn("当前是可保存、可报价和可导出的合作候选名单", APP)
        self.assertIn("加入后尚不保存逐人选择理由", APP)

    def test_campaign_quote_override_is_editable_and_preserves_source_provenance(self):
        html_block = function_block("shortlistModalHtml", "_suggestedRowHtml")
        self.assertIn('class="item-quote-override"', html_block)
        self.assertIn("本次活动手工报价", html_block)
        self.assertIn("手工报价只作用于本名单", html_block)
        self.assertIn("清空后应用可恢复源报价", html_block)
        wire_block = function_block("wireShortlistModal", "closeManager")
        self.assertIn("quote_usd_override", wire_block)
        self.assertIn("JSON.stringify({ quote_usd_override })", wire_block)
        self.assertIn("await renderShortlistModal()", wire_block)
        self.assertIn("源报价未被修改", wire_block)

    def test_campaign_budget_does_not_call_media_or_unknown_strategic_kol(self):
        block = function_block("shortlistModalHtml", "_suggestedRowHtml")
        for key in (
            "strategic_spend_usd",
            "distribution_spend_usd",
            "media_spend_usd",
            "needs_review_spend_usd",
        ):
            self.assertIn(key, block)
        self.assertIn("Media / Community 花费", block)
        self.assertIn("Needs Review 花费", block)

    def test_suggested_creator_reason_is_snapshotted_and_visible_after_reopen(self):
        load_block = function_block("loadSuggestedCreators", "wireShortlistModal")
        self.assertIn("recommendationById", load_block)
        self.assertIn("推荐理由快照（加入时）", load_block)
        self.assertIn("notes: reasonSnapshot", load_block)
        modal_block = function_block("shortlistModalHtml", "_suggestedRowHtml")
        self.assertIn("选择理由 / 备注", modal_block)

    def test_needs_review_copy_matches_low_priority_recommendation_behavior(self):
        block = function_block("drawerHtml", "sponsorshipHistoryHtml")
        self.assertIn("仅以低优先级、明确标注的方式进入候选区", block)
        self.assertNotIn("待复核；暂不进入推荐池", block)

    def test_parsed_quote_is_not_presented_as_human_confirmed(self):
        self.assertNotIn("已有确认报价", APP)
        self.assertNotIn("mango.confirmed_quote_available", APP)
        self.assertIn("已有可用数值报价记录", APP)
        self.assertIn("不表示人工或当前报价确认", APP)

    def test_outreach_log_can_edit_operational_fields_without_rewriting_history(self):
        html_block = function_block("companyDrawerHtml", "solomonReviewSectionHtml")
        self.assertIn("编辑执行备注 / 跟进", html_block)
        self.assertIn("下一次跟进日期".replace("一次", "次"), html_block)
        self.assertIn("阶段、对象、渠道、证据或归属有误时，请作废原记录并重新录入", html_block)
        wire_block = function_block("wireCompanyDrawer", "loadNetwork")
        self.assertIn(".outreach-log-save", wire_block)
        self.assertIn("notes:", wire_block)
        self.assertIn("next_follow_up_date:", wire_block)
        self.assertIn("历史事件归属未改写", wire_block)
        self.assertIn("作废（记录错误时使用，不会删除）", APP)

    def test_action_create_and_edit_preserve_execution_invariants(self):
        html_block = function_block("companyDrawerHtml", "solomonReviewSectionHtml")
        for field in (
            'id="newActionOwner"',
            'id="newActionStatus"',
            'id="newActionDueDate"',
            'id="newActionText"',
            'id="newActionFallback"',
        ):
            self.assertIn(field, html_block)
        wire_block = function_block("wireCompanyDrawer", "loadNetwork")
        self.assertIn("负责人、截止日期、具体下一步和失败备选都必须填写", wire_block)
        self.assertIn("primary_next_action: primaryNextAction", wire_block)
        self.assertIn("fallback,", wire_block)

    def test_unknown_sponsorship_publish_date_keeps_collection_date_visible(self):
        self.assertIn("function sponsorshipDateLabel", APP)
        self.assertIn("发布日期未知 · 收集于", APP)
        self.assertIn("function evidenceConfidenceLabel", APP)
        self.assertIn("未人工复核", APP)

    def test_media_and_community_has_its_own_directory_tab(self):
        self.assertIn('data-tab="media"', INDEX)
        self.assertIn('id="countMedia"', INDEX)
        self.assertIn('media: "媒体 / 社区"', APP)
        self.assertIn("不进入创作者推荐池", APP)

    def test_computed_guidance_is_not_mislabeled_as_an_owned_action(self):
        self.assertIn("未排期研究建议（非行动项）", APP)
        self.assertIn("这份建议根据当前证据即时计算", APP)

    def test_company_detail_renders_freshness_and_atomic_evidence_contract(self):
        block = function_block("companyDrawerHtml", "solomonReviewSectionHtml")
        self.assertIn("c.evidence_contract", block)
        for label in ("记录鲜度", "人工复核", "重新核实", "记录置信度 / 事实状态", "原子证据记录"):
            self.assertIn(label, block)
        self.assertIn("缺失值保持未知，不做推断", block)


class TestBasicFrontendAccessibilityContract(unittest.TestCase):
    def test_navigation_and_data_tables_have_accessible_names(self):
        self.assertIn('aria-label="主要功能"', INDEX)
        self.assertIn('aria-current="page"', INDEX)
        self.assertIn('aria-label="全局搜索"', INDEX)
        self.assertGreaterEqual(INDEX.count('<caption class="sr-only">'), 2)

    def test_dialog_keyboard_focus_is_contained(self):
        self.assertIn("function trapDialogFocus", APP)
        self.assertIn('e.key === "Tab"', APP)


if __name__ == "__main__":
    unittest.main()
