"""Content-consistency regression tests (2026-08-28 UAT-prep spec,
section VI) -- guards against the exact class of wording bugs this
session found and fixed: overclaiming a relationship stage, showing an
orphan action step, hiding a real contact behind an identity-verification
gate, etc. See docs/product-language.md for the rules these enforce.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException

from backend.bd_api import IntroPathCreate, IntroPathEdit, create_intro_path, edit_intro_path
from backend.bd_compute import E_STAGE_LABELS, relationship_stage, suggested_bridge_action
from backend.bd_serializers import _operator_json, company_summary
from backend.app import app
from backend.models import Company, IntroBridge, Operator


def _company(**overrides):
    defaults = dict(company_id="company:test", name="Test Co", spend_evidence_level="L1", priority_tier="C")
    defaults.update(overrides)
    c = Company(**defaults)
    c.intro_paths = []
    c.operators = []
    c.action_items = []
    c.sponsorships = []
    c.outreach_logs = []
    return c


FORBIDDEN_FOR_UNVERIFIED = ["真实关系", "已验证引荐", "已确认可触达", "已确认认识"]


class TestE1toE3NeverClaimVerifiedRelationship(unittest.TestCase):
    """E1-E3 come entirely from public data -- none of them may use
    language implying a human confirmed the relationship (that's
    reserved for E4+, and requires a real OutreachLog row)."""

    def test_e1_bridge_label_has_no_verified_claim(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        operator.bridges = [IntroBridge(bridge_handle="x", mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        stage = relationship_stage(c)
        self.assertEqual(stage["level"], 1)
        for phrase in FORBIDDEN_FOR_UNVERIFIED:
            self.assertNotIn(phrase, stage["label"])

    def test_one_way_follow_reference_signal_never_says_warm_intro(self):
        from backend.bd_compute import relationship_strength
        from backend.models import IntroPath

        path = IntroPath(target_type="company_account", root="mango", degree_label="secondary", hop_count=2, graph_reachable=True)
        result = relationship_strength(path)
        self.assertNotIn("warm intro", result["label"].lower())
        self.assertNotIn("已核实", result["label"])


class TestDMOpenNeverShownAsConfirmedRelationship(unittest.TestCase):
    def test_open_dm_alone_is_direct_channel_not_relationship_confirmed(self):
        c = _company(spend_evidence_level="L1")
        c.operators = [Operator(name="Tala", identity_confirmed=True, can_dm=True, x_handle="tala")]
        plan = suggested_bridge_action(c)
        self.assertIsNotNone(plan)
        self.assertIsNotNone(plan["direct"])
        self.assertNotIn("关系已确认", plan["text"])
        self.assertNotIn("已确认联系人", plan["text"])


class TestNoOperatorMeansNoContactClaim(unittest.TestCase):
    def test_no_operator_no_bridge_high_value_does_not_claim_a_contact(self):
        c = _company(spend_evidence_level="L2")
        plan = suggested_bridge_action(c)
        self.assertEqual(plan["kind"], "research_only")
        self.assertIn("没有任何已知关系路径或负责人", plan["text"])
        self.assertNotIn("联系该负责人", plan["text"])


class TestDirectContactNeverOrphanedToFallbackOnly(unittest.TestCase):
    """A real direct channel (open DM) must always produce a
    direct_contact step -- it must never collapse to just a fallback
    step, which would silently drop a real, usable channel."""

    def test_direct_channel_always_produces_a_direct_contact_step(self):
        c = _company(spend_evidence_level="L1")
        c.operators = [Operator(name="Lee", identity_confirmed=False, can_dm=True, x_handle="lee")]
        plan = suggested_bridge_action(c)
        kinds = [s["kind"] for s in plan["steps"]]
        self.assertIn("direct_contact", kinds)
        self.assertNotEqual(kinds, ["fallback"])


class TestNoOrphanStepNumbering(unittest.TestCase):
    """A step must never appear without the steps before it that its
    label implies -- e.g. never a lone 'fallback' step floating with no
    verify_warm/direct_contact step preceding it, and 'also'/'in
    parallel' language must never appear when only one track exists."""

    def test_only_direct_channel_has_exactly_two_steps_no_verify_warm(self):
        c = _company(spend_evidence_level="L1")
        c.operators = [Operator(name="Lee", identity_confirmed=False, can_dm=True, x_handle="lee")]
        plan = suggested_bridge_action(c)
        kinds = [s["kind"] for s in plan["steps"]]
        self.assertEqual(kinds, ["direct_contact", "fallback"])
        self.assertNotIn("同时", plan["text"])

    def test_only_warm_candidate_has_no_direct_contact_step(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        operator.bridges = [IntroBridge(bridge_handle="x", mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        plan = suggested_bridge_action(c)
        kinds = [s["kind"] for s in plan["steps"]]
        self.assertNotIn("direct_contact", kinds)
        self.assertNotIn("同时", plan["text"])

    def test_mutual_follow_plus_direct_keeps_candidate_in_verification_queue(self):
        c = _company()
        operator = Operator(name="Tala", identity_confirmed=True, can_dm=True, x_handle="tala")
        operator.bridges = [IntroBridge(bridge_handle="x", mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        plan = suggested_bridge_action(c)
        kinds = [s["kind"] for s in plan["steps"]]
        self.assertEqual(kinds, ["direct_contact", "verify_candidate", "fallback"])
        self.assertIsNone(plan["warm"])
        self.assertEqual([row["bridge_handle"] for row in plan["verification_queue"]], ["x"])


class TestIdentityUnverifiedStillShowsRealFacts(unittest.TestCase):
    """The Cursor bug: identity_confirmed=False must never hide a real,
    independently-checked fact (an open DM, a known name) -- it's a
    separate displayed status, not a gate."""

    def test_unconfirmed_identity_operator_dm_still_surfaces(self):
        c = _company(spend_evidence_level="L1")
        c.operators = [Operator(name="Lee Robinson", identity_confirmed=False, can_dm=True, x_handle="leerob")]
        plan = suggested_bridge_action(c)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["direct"]["handle"], "leerob")
        self.assertFalse(plan["direct"]["identity_confirmed"])
        self.assertIn("身份/职位待人工复核", plan["text"])


class TestVerificationAndInternalCirculation(unittest.TestCase):
    def test_source_match_is_not_rendered_as_human_verification(self):
        operator = Operator(
            name="Cristóbal",
            identity_confirmed=True,
            identity_status="confirmed_official_role_and_rapid_x_exact_profile",
            last_verified_at=None,
        )
        row = _operator_json(operator)
        self.assertTrue(row["identity_source_matched"])
        self.assertTrue(row["x_account_verified"])
        self.assertFalse(row["identity_human_verified"])
        self.assertFalse(row["human_identity_verified"])
        self.assertFalse(row["role_human_verified"])
        self.assertTrue(row["identity_confirmed_metadata"]["deprecated"])
        self.assertEqual(
            row["identity_confirmed_metadata"]["does_not_mean"],
            "mango_human_identity_verification",
        )

        company = _company()
        company.operators = [operator]
        summary = company_summary(company)
        self.assertIsNotNone(summary["confirmed_operator"])
        self.assertFalse(summary["confirmed_operator"]["human_identity_verified"])
        self.assertTrue(summary["confirmed_operator_metadata"]["deprecated"])
        self.assertEqual(
            summary["confirmed_operator_metadata"]["does_not_mean"],
            "mango_human_identity_verification",
        )

    def test_e4_does_not_claim_connector_willingness(self):
        self.assertIn("确认真实认识", E_STAGE_LABELS[4])
        self.assertNotIn("愿意", E_STAGE_LABELS[4])

    def test_app_has_no_product_level_basic_auth_middleware(self):
        middleware_names = {middleware.cls.__name__ for middleware in app.user_middleware}
        self.assertNotIn("BasicAuthGateMiddleware", middleware_names)

    def test_company_blocker_copy_understands_real_outreach_continuations(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
        block = source.split("function actionBlockerText", 1)[1].split("function companyDrawerHtml", 1)[0]
        for step_kind in ("wait_reply", "follow_up_no_response", "prepare_meeting", "send_proposal", "close_outreach"):
            self.assertIn(f"{step_kind}:", block)
        self.assertLess(block.index("continuationBlockers"), block.index('if (!bestOperator)'))

    def test_cold_high_value_route_label_does_not_claim_operator_is_missing(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn('cold_high_value: "暂无可执行联系渠道，先补路径/联系方式"', source)
        self.assertNotIn('cold_high_value: "尚无联系人，先定位负责人"', source)

    def test_empty_shortlist_state_explains_campaign_start_and_requires_a_name(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn("完整合作活动请从公司详情填写合作目标、受众、平台与预算后创建", source)
        self.assertIn("请先填写候选名单名称", source)
        ensure_block = source.split("async function ensureShortlist", 1)[1].split("async function refreshShortlistState", 1)[0]
        self.assertNotIn('method: "POST"', ensure_block)
        self.assertNotIn("未命名候选名单", ensure_block)
        self.assertIn("await openManager()", ensure_block)

    def test_intro_path_graph_facts_are_pipeline_owned_and_api_read_only(self):
        with self.assertRaises(HTTPException) as create_error:
            create_intro_path(
                "company:test",
                IntroPathCreate(
                    target_type="company_account",
                    path_labels="@mango -> @target",
                    graph_reachable=True,
                ),
            )
        self.assertEqual(create_error.exception.status_code, 409)

        with self.assertRaises(HTTPException) as edit_error:
            edit_intro_path(1, IntroPathEdit(graph_reachable=True))
        self.assertEqual(edit_error.exception.status_code, 409)

    def test_unreviewed_paid_copy_is_consistently_an_observation(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn('return "付费观察";', source)
        # One helper definition plus its Creator-detail and Company-detail callers.
        self.assertEqual(source.count("sponsorshipDisclosureLabel("), 3)

    def test_outreach_channel_copy_matches_required_submission_contract(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn("联系渠道（必选）", source)
        self.assertNotIn("联系渠道（可留空）", source)
        self.assertIn("请选择本次真实使用的联系渠道。", source)


if __name__ == "__main__":
    unittest.main()
