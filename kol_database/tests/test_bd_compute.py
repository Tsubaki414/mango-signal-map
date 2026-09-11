import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_compute import (
    best_intro_path,
    canonical_sponsorship_type,
    channel_access,
    creator_business_relationships,
    creator_campaign_fit,
    creator_connector_candidacy,
    deduplicate_bridge_stages,
    direct_channel_status,
    execution_priority,
    intro_readiness,
    operator_relevance,
    opportunity_priority,
    rank_suggested_creators,
    reachability_summary,
    relationship_evidence,
    relationship_stage,
    relationship_strength,
    sponsor_side_intelligence_prospects,
    sponsorship_evidence_semantics,
    suggested_bridge_action,
)
from backend.models import (
    ActionItem,
    Company,
    ContactMethod,
    Creator,
    InteractionCheck,
    InteractionEvidence,
    IntroBridge,
    IntroPath,
    Operator,
    OutreachLog,
    RateCard,
    SocialAccount,
    SponsorshipEvidence,
)


def _path(target_type="operator_person", degree_label="direct", reachable=True, primary=True, hop_count=1):
    return IntroPath(
        target_type=target_type,
        root="mango",
        is_primary=primary,
        degree_label=degree_label,
        hop_count=hop_count,
        graph_reachable=reachable,
        human_intro_status="human_intro_unvalidated",
    )


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


class TestBestIntroPath(unittest.TestCase):
    def test_prefers_reachable_over_unreachable(self):
        unreachable = _path(reachable=False)
        reachable = _path(reachable=True)
        self.assertIs(best_intro_path([unreachable, reachable]), reachable)

    def test_prefers_direct_over_secondary(self):
        secondary = _path(degree_label="secondary")
        direct = _path(degree_label="direct")
        self.assertIs(best_intro_path([secondary, direct]), direct)

    def test_filters_by_target_type(self):
        person = _path(target_type="operator_person")
        company_acct = _path(target_type="company_account")
        self.assertIs(best_intro_path([person, company_acct], target_type="company_account"), company_acct)

    def test_empty_list_returns_none(self):
        self.assertIsNone(best_intro_path([]))

    def test_empty_not_evaluated_placeholder_is_not_a_path(self):
        placeholder = IntroPath(
            target_type="operator_person",
            root="mango",
            graph_reachable=False,
            degree_label=None,
            hop_count=None,
            connector_handle=None,
            path_labels=None,
            human_intro_status="not_evaluated",
        )
        self.assertIsNone(best_intro_path([placeholder]))

    def test_missing_degree_label_falls_back_to_hop_count_not_worst_rank(self):
        """Regression test for a real bug found auditing the source data:
        87% of operator_person_paths (73/84, per the v4 QA report) have no
        degree_label at all. Ranking those as automatically worse than
        every labeled "third" (weakest) path silently buried good paths
        for a missing-metadata reason, not an actual weakness."""
        unlabeled_1hop = _path(degree_label=None, hop_count=1)
        labeled_third = _path(degree_label="third", hop_count=5)
        self.assertIs(best_intro_path([labeled_third, unlabeled_1hop]), unlabeled_1hop)

    def test_missing_both_degree_label_and_hop_count_ranks_last(self):
        labeled_third = _path(degree_label="third", hop_count=5)
        unlabeled_unknown = _path(degree_label=None, hop_count=None)
        self.assertIs(best_intro_path([unlabeled_unknown, labeled_third]), labeled_third)


class TestReachability(unittest.TestCase):
    """Auditing this sprint's own intro-graph found that "graph reachable"
    is almost always a ONE-WAY follow (74 connector-target edges checked
    by hand, only 1 was genuinely mutual). These tests lock in the
    correction: bare IntroPath/identity data is a research lead, never
    "reachable" on its own -- only a verified IntroBridge or a confirmed
    operator with open DMs earns that."""

    def test_no_paths_is_level_zero(self):
        c = _company()
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 0)

    def test_identified_operator_alone_is_level_two_not_reachable(self):
        c = _company()
        c.intro_paths = [_path(target_type="operator_person", reachable=True)]
        c.operators = [Operator(name="Jane", identity_confirmed=True)]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 2)
        self.assertIsNotNone(summary["confirmed_operator"])

    def test_confirmed_operator_with_open_dm_is_level_three(self):
        """Open DM is real but cold -- it's a fallback channel, not the
        target outcome, so it ranks below a bridge candidate (level 4)."""
        c = _company()
        c.operators = [Operator(name="Jane", identity_confirmed=True, can_dm=True, x_handle="jane")]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 3)
        self.assertIsNotNone(summary["dm_open_operator"])

    def test_verified_bridge_alone_is_level_four(self):
        """A bridge only proves a third person is mutual with both a
        Mango-side account and the target -- not that they actually know
        each other or are willing to introduce. Still ranks ABOVE a cold
        open-DM channel: it's the closest thing to a warm path this data
        can show, and warm intros (not cold outreach) are the whole point
        of this feature -- see reachability_summary's 2026-08-28 fix."""
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True, can_dm=False)
        operator.bridges = [IntroBridge(bridge_handle="friend_of_jane", mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 4)
        self.assertTrue(summary["verified_bridge"])

    def test_bridge_outranks_open_dm_when_both_present(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True, can_dm=True)
        operator.bridges = [IntroBridge(bridge_handle="friend_of_jane", mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 4)

    def test_one_way_company_path_without_confirmed_operator_is_level_one(self):
        c = _company()
        c.intro_paths = [_path(target_type="company_account", reachable=True)]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 1)

    def test_unreachable_path_is_still_level_one_reference_signal(self):
        c = _company()
        c.intro_paths = [_path(target_type="company_account", reachable=False)]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 1)


class TestSuggestedBridgeAction(unittest.TestCase):
    """suggested_bridge_action is computed fresh every request from live
    Operator/IntroBridge/InteractionCheck/OutreachLog data, and (2026-08-28,
    section III correction) never auto-picks a single "best" connector --
    it always returns up to 3 candidates for human comparison, plus the
    direct-DM fallback and a catch-all fallback, in parallel."""

    def test_no_leads_gets_explicit_research_plan(self):
        c = _company()
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "research_only")
        self.assertTrue(result["text"])
        self.assertIn("补充", result["text"])

    def test_never_auto_picks_a_single_bridge_even_with_one_candidate(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        operator.bridges = [
            IntroBridge(bridge_handle="small_fry", bridge_followers_count=100, mango_side_handle="Solomon_Nahhh"),
            IntroBridge(bridge_handle="big_fish", bridge_name="Big Fish", bridge_followers_count=9000, mango_side_handle="Solomon_Nahhh"),
        ]
        c.operators = [operator]
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "verification_queue_only")
        self.assertIsNone(result["warm"])
        handles = {cand["bridge_handle"] for cand in result["verification_queue"]}
        self.assertEqual(handles, {"small_fry", "big_fish"})
        self.assertIn("不是暖路径", result["text"])

    def test_sapien_case_shows_both_candidates_neither_auto_picked(self):
        """The real bug this fix targets: @roxinft (zero public
        interaction found) was being suggested while @calchulus (a real,
        if 5-year-old, personal exchange) sat unmentioned. Both must show,
        neither auto-picked, and their evidence must differ visibly."""
        c = _company()
        operator = Operator(name="Rowan", identity_confirmed=True)
        roxinft = IntroBridge(bridge_handle="roxinft", bridge_followers_count=2000, mango_side_handle="Solomon_Nahhh")
        calchulus = IntroBridge(bridge_handle="calchulus", bridge_followers_count=1500, mango_side_handle="Solomon_Nahhh")
        calchulus.interaction_checks = [
            InteractionCheck(
                from_handle="calchulus",
                to_handle="RowanRK6",
                query_string="from:calchulus to:RowanRK6",
                evidence=[InteractionEvidence(tweet_id="1", posted_at=dt.datetime(2021, 6, 11), author_handle="calchulus", interaction_type="reply")],
            )
        ]
        roxinft.interaction_checks = [
            InteractionCheck(from_handle="roxinft", to_handle="RowanRK6", query_string="from:roxinft to:RowanRK6", evidence=[])
        ]
        operator.bridges = [roxinft, calchulus]
        c.operators = [operator]
        result = suggested_bridge_action(c)
        handles = {cand["bridge_handle"]: cand for cand in result["warm"]}
        queued = {cand["bridge_handle"]: cand for cand in result["verification_queue"]}
        self.assertEqual(set(handles), {"calchulus"})
        self.assertEqual(set(queued), {"roxinft"})
        self.assertEqual(queued["roxinft"]["level"], 1)
        self.assertEqual(handles["calchulus"]["level"], 2)

    def test_warm_and_direct_shown_together_when_both_exist(self):
        """Section III: when a bridge candidate AND an open DM both exist
        on the same company, both tracks must show -- not one silently
        replacing the other."""
        c = _company()
        operator = Operator(name="Tala", identity_confirmed=True, can_dm=True, x_handle="_talaawwad")
        operator.bridges = [IntroBridge(bridge_handle="someone", bridge_followers_count=500, mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        result = suggested_bridge_action(c)
        self.assertIsNone(result["warm"])
        self.assertEqual([row["bridge_handle"] for row in result["verification_queue"]], ["someone"])
        self.assertIsNotNone(result["direct"])
        self.assertEqual(result["direct"]["handle"], "_talaawwad")
        self.assertIn("someone", result["text"])
        self.assertIn("_talaawwad", result["text"])
        self.assertEqual(result["kind"], "direct_only")
        self.assertEqual(len(result["steps"]), 3)
        self.assertEqual(result["steps"][0]["kind"], "direct_contact")
        self.assertEqual(result["steps"][1]["kind"], "verify_candidate")
        self.assertEqual(result["steps"][2]["kind"], "fallback")

    def test_direct_only_never_says_also_or_in_parallel(self):
        """Section IV bug: '同时可直接私信' (also/in parallel) implies a
        warm path exists before it -- must never appear when there is no
        warm candidate at all, and there must be no orphan step B/C
        without a step A."""
        c = _company()
        c.operators = [Operator(name="Tala", identity_confirmed=True, can_dm=True, x_handle="_talaawwad")]
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "direct_only")
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["kind"], "direct_contact")
        self.assertNotIn("同时", result["text"])

    def test_dm_open_but_identity_not_confirmed_still_shows_direct_channel(self):
        """The real Cursor bug (2026-08-28): Lee Robinson has can_dm=True
        but identity_confirmed=False -- this used to hide the direct
        channel entirely and claim "no known operator at all" even though
        a real name is on file. Identity verification is a separate
        displayed fact now, not a gate on whether the channel/name shows."""
        c = _company(spend_evidence_level="L2")
        c.operators = [Operator(name="Lee Robinson", identity_confirmed=False, can_dm=True, x_handle="leerob")]
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "direct_only")
        self.assertIsNotNone(result["direct"])
        self.assertEqual(result["direct"]["handle"], "leerob")
        self.assertFalse(result["direct"]["identity_confirmed"])
        self.assertIn("身份/职位待人工复核", result["text"])

    def test_disproven_bridge_excluded_from_warm_candidates(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(id=1, bridge_handle="deadend", bridge_followers_count=100, mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        c.operators = [operator]
        c.outreach_logs = [OutreachLog(bridge_id=1, stage="relationship_rejected", occurred_at=dt.datetime(2026, 1, 1))]
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "research_only")
        self.assertNotIn("deadend", [candidate["bridge_handle"] for candidate in result["warm"] or []])
        self.assertIn("不再重复询问", result["text"])
        self.assertEqual(intro_readiness(c)["label"], "Unverified")
        self.assertEqual(relationship_evidence(c)["state"], "none_found")
        self.assertEqual(relationship_stage(c)["level"], 0)
        self.assertFalse(reachability_summary(c)["verified_bridge"])
        self.assertEqual(opportunity_priority(c)["route_type"], "none")

    def test_e1_mutual_follow_is_verification_reference_not_warm_route(self):
        """A bare mutual follow must not satisfy the opportunity route gate.

        This keeps the top-level opportunity classification aligned with the
        action planner: E1 belongs in ``verification_queue`` until public
        interaction (E2+) or human relationship evidence is recorded.  An
        independently usable channel can still make the route cold, but the
        E1 row must never turn that route warm.
        """
        c = _company(
            spend_evidence_level="L3",
            spend_mechanism_level="active_creator_or_partner_budget",
            category="AI video",
            why_now="Recent creator marketing campaign",
            last_verified_at=dt.datetime.now(),
        )
        operator = Operator(
            name="Growth Owner",
            role="Head of Growth",
            identity_confirmed=True,
        )
        operator.bridges = [
            IntroBridge(
                id=1,
                bridge_handle="mutual_only",
                mango_side_handle="Solomon_Nahhh",
            )
        ]
        c.operators = [operator]
        c.action_items = [
            ActionItem(
                primary_next_action="Verify the relationship internally",
                fallback="Use the official contact form",
                owner="Solomon",
                due_date="2026-08-29",
            )
        ]

        priority = opportunity_priority(c)
        plan = suggested_bridge_action(c)

        self.assertEqual(priority["route_type"], "none")
        self.assertFalse(priority["gates"]["clear_route_type"])
        self.assertNotEqual(priority["label"], "High")
        self.assertEqual(plan["kind"], "verification_queue_only")
        self.assertEqual(plan["relationship_candidates"], [])
        self.assertEqual(
            [row["bridge_handle"] for row in plan["verification_queue"]],
            ["mutual_only"],
        )

        operator.can_dm = True
        operator.x_handle = "growth_owner"
        priority = opportunity_priority(c)
        plan = suggested_bridge_action(c)

        self.assertEqual(priority["route_type"], "cold")
        self.assertEqual(plan["kind"], "direct_only")
        self.assertEqual(plan["relationship_candidates"], [])
        self.assertEqual(
            [row["bridge_handle"] for row in plan["verification_queue"]],
            ["mutual_only"],
        )

    def test_latest_relationship_rejection_retracts_earlier_human_confirmation(self):
        c = _company()
        operator = Operator(id=9, name="Jane", identity_confirmed=True)
        bridge = IntroBridge(id=7, bridge_handle="deadend", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        c.operators = [operator]
        c.outreach_logs = [
            OutreachLog(
                bridge_id=7,
                operator_id=9,
                stage="relationship_confirmed",
                occurred_at=dt.datetime(2026, 1, 1),
            ),
            OutreachLog(
                bridge_id=7,
                operator_id=9,
                stage="relationship_rejected",
                occurred_at=dt.datetime(2026, 1, 2),
            ),
        ]
        self.assertEqual(intro_readiness(c)["level"], 0)
        self.assertFalse(relationship_evidence(c)["relationship_verified"])
        self.assertEqual(opportunity_priority(c)["route_type"], "none")

    def test_no_response_does_not_permanently_remove_bridge_candidate(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(id=1, bridge_handle="quiet", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        c.operators = [operator]
        c.outreach_logs = [OutreachLog(bridge_id=1, stage="no_response", occurred_at=dt.datetime(2026, 1, 1))]
        result = suggested_bridge_action(c)
        self.assertIsNotNone(result)
        self.assertEqual([candidate["bridge_handle"] for candidate in result["verification_queue"]], ["quiet"])

    def test_open_dm_used_only_when_no_bridge_exists(self):
        c = _company()
        operator = Operator(name="Tala", identity_confirmed=True, can_dm=True, x_handle="_talaawwad")
        c.operators = [operator]
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "direct_only")
        self.assertIsNone(result["warm"])
        self.assertIn("_talaawwad", result["text"])

    def test_high_opportunity_with_no_relationship_signal_still_gets_a_suggestion(self):
        """Gamma's real case: real spend evidence, zero known path (no
        bridge, no open DM, not even a confirmed operator) -- must never
        silently return None just because there's nothing to verify yet."""
        c = _company(spend_evidence_level="L2")
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "research_only")
        self.assertEqual(opportunity_priority(c)["label"], "Medium")
        self.assertIn("没有任何已知关系路径或负责人", result["text"])

    def test_high_opportunity_with_identified_operator_but_no_contact_method(self):
        """Meshy's real case: a confirmed operator is known by name, but
        with no bridge and no open DM there's still no way to reach them
        -- the suggestion must say who's known, not claim nothing is
        known at all (that overstates the gap the same way the bare
        "no known path" text would understate it for a truly blank
        company)."""
        c = _company(spend_evidence_level="L2")
        c.operators = [Operator(name="Ethan", identity_confirmed=True, can_dm=False, x_handle="YuanmingH")]
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "research_only")
        self.assertIn("YuanmingH", result["text"])
        self.assertIn("没有已知的联系方式", result["text"])

    def test_low_opportunity_with_no_relationship_signal_gets_defer_research_plan(self):
        c = _company(spend_evidence_level="legacy_unmapped", priority_tier="C")
        result = suggested_bridge_action(c)
        self.assertEqual(result["kind"], "research_only")
        self.assertEqual(result["recommended_execution_status"], "暂缓")
        self.assertIn("在证据出现前不投入主动外联", result["text"])


class TestDynamicActionContinuation(unittest.TestCase):
    def _company_at_stage(self, stage: str, **log_overrides):
        company = _company(spend_evidence_level="L2")
        operator = Operator(id=9, name="Target", x_handle="target", can_dm=True, identity_confirmed=True)
        bridge = IntroBridge(id=7, bridge_handle="connector", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        company.operators = [operator]
        log_fields = {
            "bridge_id": 7,
            "operator_id": 9,
            "contacted_who": "@connector",
            "stage": stage,
            "occurred_at": dt.datetime(2026, 8, 27),
        }
        log_fields.update(log_overrides)
        company.outreach_logs = [OutreachLog(**log_fields)]
        return company

    def test_every_funnel_stage_produces_specific_next_action_and_matching_status(self):
        cases = {
            "contact_attempted": ("等待回复", "不要重复发送首触达"),
            "connector_replied": ("需要跟进", "已回复"),
            "relationship_confirmed": ("需要跟进", "确认是否愿意引荐"),
            "relationship_rejected": ("需要跟进", "改走已核实开放的 X 私信"),
            "intro_accepted": ("需要跟进", "不要再重复询问意愿"),
            "intro_made": ("等待回复", "不另起新线程"),
            "target_replied": ("需要跟进", "确认预算、时间线"),
            "meeting_booked": ("本周准备", "准备一页会议简报"),
            "proposal_requested": ("需要跟进", "制作 campaign 方案"),
            "rejected": ("放弃", "停止当前主动跟进"),
            "no_response": ("需要跟进", "新增价值"),
        }
        for stage, (expected_status, phrase) in cases.items():
            with self.subTest(stage=stage):
                # A bridge declining an intro is a route-level outcome,
                # not a company rejection. This table's `rejected` case
                # deliberately represents the target operator declining.
                company = self._company_at_stage(stage, bridge_id=None) if stage == "rejected" else self._company_at_stage(stage)
                plan = suggested_bridge_action(company)
                execution = execution_priority(company)
                self.assertEqual(plan["latest_outreach_stage"], stage)
                self.assertEqual(plan["recommended_execution_status"], expected_status)
                self.assertEqual(execution["label"], expected_status)
                self.assertIn(phrase, plan["text"])

    def test_bridge_attributed_rejection_switches_candidate_not_company_status(self):
        company = _company(spend_evidence_level="L2")
        operator = Operator(id=9, name="Target", x_handle="target", can_dm=True, identity_confirmed=True)
        rejected_bridge = IntroBridge(id=7, bridge_handle="declined", mango_side_handle="Solomon_Nahhh")
        alternate_bridge = IntroBridge(id=8, bridge_handle="alternate", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [rejected_bridge, alternate_bridge]
        company.operators = [operator]
        company.outreach_logs = [
            OutreachLog(bridge_id=7, operator_id=9, stage="rejected", occurred_at=dt.datetime(2026, 8, 27))
        ]
        plan = suggested_bridge_action(company)
        self.assertEqual(plan["recommended_execution_status"], "等待内部核实")
        self.assertEqual(execution_priority(company)["label"], "等待内部核实")
        self.assertIn("@alternate", plan["text"])
        self.assertIn("不再向同一人重复请求", plan["text"])
        self.assertNotIn("停止当前主动跟进", plan["text"])

    def test_bridge_attributed_rejection_can_switch_to_independent_direct(self):
        company = self._company_at_stage("rejected")
        plan = suggested_bridge_action(company)
        self.assertEqual(plan["recommended_execution_status"], "需要跟进")
        self.assertIn("这不代表目标方已拒绝", plan["text"])
        self.assertIn("独立 X 私信 @target", plan["text"])

    def test_due_contact_and_intro_are_followups_not_new_first_touches(self):
        for stage, phrase in (("contact_attempted", "已到跟进日"), ("intro_made", "不要重新发起引荐")):
            with self.subTest(stage=stage):
                company = self._company_at_stage(stage, next_follow_up_date="2020-01-01")
                plan = suggested_bridge_action(company)
                self.assertEqual(plan["recommended_execution_status"], "需要跟进")
                self.assertIn(phrase, plan["text"])
                self.assertNotIn("可直接私信 @target", plan["text"])

    def test_future_no_response_follow_up_waits_until_planned_date(self):
        company = self._company_at_stage("no_response", next_follow_up_date="2099-01-01")
        plan = suggested_bridge_action(company)
        self.assertEqual(plan["recommended_execution_status"], "等待回复")
        self.assertIn("等待至 2099-01-01", plan["text"])
        self.assertIn("当前不重复首触达", plan["text"])

    def test_direct_dm_no_response_does_not_fallback_to_same_dm(self):
        company = _company(spend_evidence_level="L2")
        company.operators = [
            Operator(id=9, name="Target", x_handle="target", can_dm=True, identity_confirmed=True)
        ]
        company.outreach_logs = [
            OutreachLog(
                operator_id=9,
                bridge_id=None,
                contact_channel="dm",
                stage="no_response",
                occurred_at=dt.datetime(2026, 8, 27),
            )
        ]
        plan = suggested_bridge_action(company)
        self.assertEqual(plan["recommended_execution_status"], "需要跟进")
        self.assertIn("不要再次建议同一 X 私信", plan["text"])
        self.assertIn("官方 email/contact form 或新的 connector", plan["text"])
        self.assertNotIn("改走独立 X 私信 @target", plan["text"])

    def test_bridge_no_response_may_fallback_to_independent_direct_dm(self):
        company = self._company_at_stage("no_response", contact_channel="connector_ask")
        plan = suggested_bridge_action(company)
        self.assertIn("改走独立 X 私信 @target", plan["text"])
        self.assertNotIn("不要再次建议同一 X 私信", plan["text"])

    def test_latest_voided_event_is_ignored(self):
        company = self._company_at_stage("contact_attempted")
        company.outreach_logs.append(
            OutreachLog(stage="rejected", occurred_at=dt.datetime(2026, 8, 28), voided=True)
        )
        plan = suggested_bridge_action(company)
        self.assertEqual(plan["latest_outreach_stage"], "contact_attempted")
        self.assertEqual(execution_priority(company)["label"], "等待回复")

    def test_name_only_operator_never_renders_at_none(self):
        company = _company(spend_evidence_level="L2")
        company.operators = [Operator(name="Named Person", can_dm=True, x_handle=None)]
        plan = suggested_bridge_action(company)
        self.assertEqual(plan["kind"], "research_only")
        self.assertIsNone(plan["direct"])
        self.assertIn("Named Person", plan["text"])
        self.assertNotIn("@None", plan["text"])

    def test_freshness_timestamp_is_not_human_identity_or_role_review(self):
        company = _company()
        operator = Operator(
            name="Named Person",
            x_handle="named",
            can_dm=True,
            identity_confirmed=True,
            last_verified_at=dt.datetime(2026, 8, 28),
        )
        company.operators = [operator]
        channel = direct_channel_status(company)
        self.assertFalse(channel["identity_human_verified"])
        self.assertFalse(channel["role_human_verified"])
        self.assertFalse(channel["identity_and_role_human_verified"])
        self.assertIn("待人工复核", suggested_bridge_action(company)["text"])

    def test_identity_and_role_human_review_are_separate(self):
        company = _company()
        operator = Operator(
            name="Named Person",
            x_handle="named",
            can_dm=True,
            identity_confirmed=True,
            identity_human_verified_at=dt.datetime(2026, 8, 28),
            role_human_verified_at=None,
        )
        company.operators = [operator]
        channel = direct_channel_status(company)
        self.assertTrue(channel["identity_human_verified"])
        self.assertFalse(channel["role_human_verified"])
        self.assertFalse(channel["identity_and_role_human_verified"])
        self.assertIn("身份已人工复核，职位待人工复核", suggested_bridge_action(company)["text"])


class TestRelationshipStage(unittest.TestCase):
    """E0-E6 (section II): public evidence can only earn E1-E3; E4-E6
    require a real, human-entered OutreachLog row -- never inferred."""

    def test_no_bridge_no_path_is_e0(self):
        c = _company()
        self.assertEqual(relationship_stage(c)["level"], 0)

    def test_one_way_path_only_is_e1(self):
        c = _company()
        c.intro_paths = [_path(target_type="company_account", reachable=True)]
        self.assertEqual(relationship_stage(c)["level"], 1)

    def test_empty_not_evaluated_path_placeholder_is_e0(self):
        c = _company()
        c.intro_paths = [
            IntroPath(
                target_type="operator_person",
                root="mango",
                graph_reachable=False,
                degree_label=None,
                hop_count=None,
                connector_handle=None,
                path_labels=None,
                human_intro_status="not_evaluated",
            )
        ]
        self.assertEqual(relationship_stage(c)["level"], 0)

    def test_bridge_with_no_interaction_check_is_e1(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        operator.bridges = [IntroBridge(bridge_handle="x", mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        self.assertEqual(relationship_stage(c)["level"], 1)

    def test_bridge_checked_zero_interaction_is_still_e1_not_downgraded(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        bridge.interaction_checks = [InteractionCheck(from_handle="x", to_handle="y", query_string="q", evidence=[])]
        operator.bridges = [bridge]
        c.operators = [operator]
        self.assertEqual(relationship_stage(c)["level"], 1)

    def test_single_old_interaction_is_e2(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        bridge.interaction_checks = [
            InteractionCheck(
                from_handle="x", to_handle="y", query_string="q",
                evidence=[InteractionEvidence(tweet_id="1", posted_at=dt.datetime(2020, 1, 1), author_handle="x")],
            )
        ]
        operator.bridges = [bridge]
        c.operators = [operator]
        self.assertEqual(relationship_stage(c)["level"], 2)

    def test_recent_repeated_bidirectional_interaction_is_e3(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        recent = dt.datetime.utcnow() - dt.timedelta(days=30)
        bridge.interaction_checks = [
            InteractionCheck(
                from_handle="x", to_handle="y", query_string="q",
                evidence=[
                    InteractionEvidence(tweet_id="1", posted_at=recent, author_handle="x"),
                    InteractionEvidence(tweet_id="2", posted_at=recent, author_handle="y"),
                ],
            )
        ]
        operator.bridges = [bridge]
        c.operators = [operator]
        self.assertEqual(relationship_stage(c)["level"], 3)

    def test_old_reverse_interaction_cannot_make_recent_one_way_activity_e3(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        recent = dt.datetime.utcnow() - dt.timedelta(days=30)
        old = dt.datetime.utcnow() - dt.timedelta(days=600)
        bridge.interaction_checks = [
            InteractionCheck(
                from_handle="x",
                to_handle="y",
                query_string="q",
                evidence=[
                    InteractionEvidence(tweet_id="1", posted_at=recent, author_handle="x"),
                    InteractionEvidence(tweet_id="2", posted_at=recent, author_handle="x"),
                    InteractionEvidence(tweet_id="3", posted_at=old, author_handle="y"),
                ],
            )
        ]
        operator.bridges = [bridge]
        c.operators = [operator]
        self.assertEqual(relationship_stage(c)["level"], 2)

    def test_relationship_confirmed_log_advances_to_e4(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(id=7, bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        c.operators = [operator]
        c.outreach_logs = [OutreachLog(bridge_id=7, stage="relationship_confirmed", occurred_at=dt.datetime(2026, 1, 1))]
        self.assertEqual(relationship_stage(c)["level"], 4)

    def test_target_replied_after_intro_made_advances_to_e6(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(id=7, bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        c.operators = [operator]
        c.outreach_logs = [
            OutreachLog(bridge_id=7, stage="intro_made", occurred_at=dt.datetime(2026, 1, 1)),
            OutreachLog(bridge_id=7, stage="target_replied", occurred_at=dt.datetime(2026, 1, 2)),
        ]
        self.assertEqual(relationship_stage(c)["level"], 6)

    def test_direct_target_reply_does_not_claim_intro_happened(self):
        c = _company()
        operator = Operator(id=9, name="Jane", identity_confirmed=True)
        c.operators = [operator]
        c.outreach_logs = [
            OutreachLog(operator_id=9, contact_channel="dm", stage="target_replied", occurred_at=dt.datetime(2026, 1, 1))
        ]
        self.assertEqual(relationship_stage(c)["level"], 0)

    def test_bridge_target_reply_without_intro_made_stays_e1(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(id=7, bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        c.operators = [operator]
        c.outreach_logs = [
            OutreachLog(bridge_id=7, stage="target_replied", occurred_at=dt.datetime(2026, 1, 1))
        ]
        self.assertEqual(relationship_stage(c)["level"], 1)

    def test_voided_log_does_not_advance_stage(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(id=7, bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        c.operators = [operator]
        c.outreach_logs = [OutreachLog(bridge_id=7, stage="relationship_confirmed", occurred_at=dt.datetime(2026, 1, 1), voided=True)]
        self.assertEqual(relationship_stage(c)["level"], 1)

    def test_disproven_bridge_flagged_in_bridges_detail(self):
        c = _company()
        operator = Operator(name="Jane", identity_confirmed=True)
        bridge = IntroBridge(id=7, bridge_handle="x", mango_side_handle="Solomon_Nahhh")
        operator.bridges = [bridge]
        c.operators = [operator]
        c.outreach_logs = [OutreachLog(bridge_id=7, stage="relationship_rejected", occurred_at=dt.datetime(2026, 1, 1))]
        stage = relationship_stage(c)
        self.assertTrue(stage["bridges"][0]["disproven"])

    def test_no_response_or_declined_request_does_not_disprove_relationship(self):
        for outcome in ("no_response", "rejected"):
            with self.subTest(outcome=outcome):
                c = _company()
                operator = Operator(name="Jane", identity_confirmed=True)
                bridge = IntroBridge(id=7, bridge_handle="x", mango_side_handle="Solomon_Nahhh")
                operator.bridges = [bridge]
                c.operators = [operator]
                c.outreach_logs = [OutreachLog(bridge_id=7, stage=outcome, occurred_at=dt.datetime(2026, 1, 1))]
                stage = relationship_stage(c)
                self.assertFalse(stage["bridges"][0]["disproven"])


class TestExecutionPriority(unittest.TestCase):
    def test_high_opportunity_no_path_no_operator_is_not_contact_now(self):
        """Gamma's real case must not rank as "现在联系" -- there's
        genuinely nothing to execute today, however valuable the company
        would be if that changed."""
        c = _company(spend_evidence_level="L2")
        result = execution_priority(c)
        self.assertNotEqual(result["label"], "现在联系")

    def test_high_opportunity_with_open_dm_is_contact_now(self):
        c = _company(
            spend_evidence_level="L3",
            spend_mechanism_level="active_creator_or_partner_budget",
            category="AI video",
            buyer_or_route="Creator campaign proposal",
            last_verified_at=dt.datetime(2026, 8, 1),
        )
        c.sponsorships = [
            SponsorshipEvidence(
                company_id=c.company_id,
                platform="YouTube",
                content_url="https://example.com/ad",
                published_at="2026-07-01",
                disclosure_type="paid_sponsorship",
                review_status="confirmed",
            )
        ]
        c.operators = [
            Operator(name="Tala", role="Head of Creator Partnerships", identity_confirmed=True, can_dm=True, x_handle="_talaawwad")
        ]
        c.action_items = [ActionItem(primary_next_action="Send campaign proposal", fallback="Use partner form")]
        result = execution_priority(c)
        self.assertEqual(result["label"], "现在联系")

    def test_no_opportunity_no_path_is_defer(self):
        c = _company(spend_evidence_level="legacy_unmapped", priority_tier="D")
        result = execution_priority(c)
        self.assertEqual(result["label"], "暂缓")

    def test_unverified_bridge_without_direct_channel_waits_for_internal_check(self):
        c = _company(spend_evidence_level="L2")
        operator = Operator(name="Target", identity_confirmed=True, can_dm=False)
        operator.bridges = [IntroBridge(id=8, bridge_handle="candidate", mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        self.assertEqual(execution_priority(c)["label"], "等待内部核实")

    def test_contact_attempted_waits_for_reply(self):
        c = _company(spend_evidence_level="L2")
        c.operators = [Operator(name="Target", can_dm=True, x_handle="target")]
        c.outreach_logs = [OutreachLog(stage="contact_attempted", occurred_at=dt.datetime(2026, 8, 27))]
        self.assertEqual(execution_priority(c)["label"], "等待回复")

    def test_due_follow_up_takes_precedence(self):
        c = _company(spend_evidence_level="L2")
        c.operators = [Operator(name="Target", can_dm=True, x_handle="target")]
        c.outreach_logs = [
            OutreachLog(
                stage="contact_attempted",
                occurred_at=dt.datetime(2026, 8, 20),
                next_follow_up_date="2020-01-01",
            )
        ]
        self.assertEqual(execution_priority(c)["label"], "需要跟进")

    def test_explicit_rejection_is_abandoned(self):
        c = _company(spend_evidence_level="L2")
        c.outreach_logs = [OutreachLog(stage="rejected", occurred_at=dt.datetime(2026, 8, 27))]
        self.assertEqual(execution_priority(c)["label"], "放弃")


class TestRejectedEvidenceExcluded(unittest.TestCase):
    def test_rejected_sponsorship_does_not_count_toward_priority_reasons(self):
        c = _company(spend_evidence_level="L1")
        c.sponsorships = [
            SponsorshipEvidence(company_id=c.company_id, platform="YouTube", disclosure_type="paid_sponsorship", review_status="rejected"),
        ]
        result = opportunity_priority(c)
        self.assertFalse(any("赞助观察记录" in r for r in result["reasons"]))

    def test_confirmed_sponsorship_still_counts(self):
        c = _company(spend_evidence_level="L1")
        c.sponsorships = [
            SponsorshipEvidence(company_id=c.company_id, platform="YouTube", disclosure_type="paid_sponsorship", review_status="confirmed"),
        ]
        result = opportunity_priority(c)
        self.assertTrue(any("付费赞助证据" in r for r in result["reasons"]))

    def test_unreviewed_paid_observation_is_labeled_as_unreviewed(self):
        c = _company(spend_evidence_level="L1")
        c.sponsorships = [
            SponsorshipEvidence(
                company_id=c.company_id,
                platform="YouTube",
                disclosure_type="paid_sponsorship",
                review_status="unreviewed",
            ),
        ]
        reasons = opportunity_priority(c)["reasons"]
        self.assertTrue(any("待人工复核" in reason for reason in reasons))
        self.assertFalse(any("人工复核的付费赞助证据" in reason for reason in reasons))


class TestOpportunityPriority(unittest.TestCase):
    """High is a transparent five-gate execution recommendation. Weak
    relationship or capacity signals never satisfy missing business gates."""

    def test_paid_sponsorship_alone_is_not_high(self):
        c = _company(spend_evidence_level="L1", priority_tier="D")
        c.sponsorships = [
            SponsorshipEvidence(company_id=c.company_id, platform="YouTube", disclosure_type="paid_sponsorship", review_status="confirmed")
        ]
        # No operators, no intro_paths at all -- reachability is level 0.
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "Medium")
        self.assertEqual(result["reachability"]["level"], 0)

    def test_l3_label_without_freshness_or_route_is_not_high(self):
        c = _company(spend_evidence_level="L3", spend_mechanism_level="active_creator_or_partner_budget", priority_tier="A")
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "Medium")
        self.assertEqual(result["reachability"]["level"], 0)

    def test_all_five_gates_allow_high_even_when_route_is_cold(self):
        c = _company(
            spend_evidence_level="L3",
            spend_mechanism_level="active_creator_or_partner_budget",
            priority_tier="A",
            category="AI video",
            buyer_or_route="Send a creator campaign proposal",
            last_verified_at=dt.datetime(2026, 8, 1),
        )
        c.operators = [Operator(name="Buyer", role="Head of Creator Partnerships", can_dm=True, x_handle="buyer")]
        c.action_items = [ActionItem(primary_next_action="Send scoped offer", fallback="Use official form")]
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "High")
        self.assertTrue(all(result["gates"].values()))
        self.assertEqual(result["route_type"], "cold")

    def test_bridge_candidate_does_not_by_itself_make_opportunity_high(self):
        """A verified bridge is a relationship-stage fact, not a business
        value fact -- reachability must never leak into this score, in
        either direction."""
        c = _company(spend_evidence_level="legacy_unmapped", priority_tier="D")
        operator = Operator(name="Jane", identity_confirmed=True)
        operator.bridges = [IntroBridge(bridge_handle="friend", mango_side_handle="Solomon_Nahhh")]
        c.operators = [operator]
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "Low")
        self.assertEqual(result["reachability"]["level"], 4)

    def test_weak_spend_no_sponsorship_is_low(self):
        c = _company(spend_evidence_level="legacy_unmapped", priority_tier="D")
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "Low")

    def test_tier_b_alone_is_medium(self):
        c = _company(spend_evidence_level="legacy_unmapped", priority_tier="B")
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "Medium")

    def test_organic_mention_alone_does_not_create_business_priority(self):
        c = _company(spend_evidence_level="legacy_unmapped", priority_tier="D")
        c.sponsorships = [
            SponsorshipEvidence(
                company_id=c.company_id,
                platform="YouTube",
                disclosure_type="mention",
                review_status="unreviewed",
            )
        ]
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "Low")

    def test_reasons_always_present(self):
        c = _company()
        result = opportunity_priority(c)
        self.assertTrue(len(result["reasons"]) > 0)

    def test_reasons_name_route_and_missing_gates(self):
        c = _company(spend_evidence_level="L3", priority_tier="A")
        result = opportunity_priority(c)
        self.assertTrue(any("路径类型" in r for r in result["reasons"]))
        self.assertTrue(any("High 尚缺条件" in r for r in result["reasons"]))


class TestCreatorCampaignFit(unittest.TestCase):
    def test_flags_missing_quote(self):
        creator = Creator(display_name="Test", categories=None)
        creator.rate_cards = []
        creator.sponsorships = []
        fit = creator_campaign_fit(creator)
        self.assertFalse(fit["has_quote"])
        self.assertIn("暂无可用数值报价——需要先联系", fit["reasons"])

    def test_flags_confirmed_quote(self):
        creator = Creator(display_name="Test", categories=None)
        creator.rate_cards = [RateCard(deliverable="Post", quote_amount_usd=500, is_confident=True)]
        creator.sponsorships = []
        fit = creator_campaign_fit(creator)
        self.assertTrue(fit["has_quote"])

    def test_flags_prior_paid_sponsorship_of_same_company(self):
        company = _company()
        creator = Creator(display_name="Test", categories=None)
        creator.rate_cards = []
        creator.sponsorships = [
            SponsorshipEvidence(
                company_id=company.company_id,
                platform="YouTube",
                disclosure_type="paid_sponsorship",
                confidence=0.9,
                review_status="confirmed",
            )
        ]
        fit = creator_campaign_fit(creator, company)
        self.assertTrue(any("人工复核" in r and "付费赞助" in r for r in fit["reasons"]))

    def test_does_not_promote_mention_to_paid(self):
        company = _company()
        creator = Creator(display_name="Test", categories=None)
        creator.rate_cards = []
        creator.sponsorships = [
            SponsorshipEvidence(company_id=company.company_id, platform="YouTube", disclosure_type="mention", confidence=0.5)
        ]
        fit = creator_campaign_fit(creator, company)
        self.assertFalse(any("经人工复核" in r and "付费赞助" in r for r in fit["reasons"]))
        self.assertTrue(any("不代表已确认合作" in r for r in fit["reasons"]))


class TestRelationshipStrength(unittest.TestCase):
    def test_none_path_is_cold_contact(self):
        result = relationship_strength(None)
        self.assertEqual(result["label"], "仅为冷启动联系")
        self.assertEqual(result["level"], 0)

    def test_unreachable_path_is_weak(self):
        result = relationship_strength(_path(reachable=False))
        self.assertEqual(result["label"], "较弱 / 未确认")

    def test_direct_reachable_path_is_strongest(self):
        result = relationship_strength(_path(degree_label="direct", reachable=True))
        self.assertEqual(result["label"], "直接公开关联")


def _creator(id, display_name, creator_class, quoted=False, categories=None):
    c = Creator(id=id, display_name=display_name, creator_class=creator_class, categories=categories)
    c.rate_cards = [RateCard(deliverable="Post", quote_amount_usd=500, is_confident=True)] if quoted else []
    c.sponsorships = []
    c.social_accounts = [SocialAccount(platform="YouTube", handle=display_name.lower())]
    return c


class TestRankSuggestedCreators(unittest.TestCase):
    def setUp(self):
        self.company = _company()

    def test_non_creator_never_appears_in_either_list(self):
        creators = [_creator(1, "Cybernews", "Non-creator / Irrelevant", quoted=True)]
        results, media = rank_suggested_creators(creators, self.company, prior_creator_ids={1}, excluded_creator_ids=set())
        self.assertEqual(results, [])
        self.assertEqual(media, [])

    def test_media_account_goes_to_media_channels_not_results(self):
        creators = [_creator(2, "AI News Daily", "Media / Community Account", quoted=True)]
        results, media = rank_suggested_creators(creators, self.company, prior_creator_ids=set(), excluded_creator_ids=set())
        self.assertEqual(results, [])
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["display_name"], "AI News Daily")

    def test_needs_review_sorts_after_confident_results(self):
        creators = [
            _creator(3, "Unclassified Person", "Unknown", quoted=False),
            _creator(4, "Confident KOL", "KOL", quoted=True),
        ]
        # Both have a prior relationship so that alone doesn't explain ordering.
        results, _ = rank_suggested_creators(
            creators, self.company, prior_creator_ids={3, 4}, excluded_creator_ids=set()
        )
        self.assertEqual([r["display_name"] for r in results], ["Confident KOL", "Unclassified Person"])
        self.assertTrue(results[1]["needs_review"])
        self.assertFalse(results[0]["needs_review"])

    def test_excluded_creator_is_filtered_out(self):
        creators = [_creator(5, "Bad Fit Creator", "KOL", quoted=True)]
        results, _ = rank_suggested_creators(creators, self.company, prior_creator_ids=set(), excluded_creator_ids={5})
        self.assertEqual(results, [])

    def test_no_quote_and_no_relationship_is_excluded(self):
        creators = [_creator(6, "Unrelated Creator", "KOL", quoted=False)]
        results, _ = rank_suggested_creators(creators, self.company, prior_creator_ids=set(), excluded_creator_ids=set())
        self.assertEqual(results, [])

    def test_prior_relationship_without_quote_is_included(self):
        creators = [_creator(7, "Prior Sponsor No Quote", "KOC", quoted=False)]
        results, _ = rank_suggested_creators(creators, self.company, prior_creator_ids={7}, excluded_creator_ids=set())
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["prior_relationship"])


class TestCreatorConnectorCandidacy(unittest.TestCase):
    """Section VII (2026-08-28): a sponsorship is a commercial signal
    only, never proof of introduction ability -- candidacy requires
    REPEAT paid precedent AND Mango already having a usable quote/contact,
    not just any sponsorship history."""

    def _creator_with_sponsorships(self, sponsorships, quoted=False):
        creator = Creator(id=1, display_name="Test Creator", categories=None)
        creator.rate_cards = [RateCard(deliverable="Post", quote_amount_usd=500, is_confident=True)] if quoted else []
        creator.sponsorships = sponsorships
        return creator

    def test_single_paid_sponsorship_is_not_a_candidate(self):
        company = _company(company_id="company:x", name="X")
        s = SponsorshipEvidence(company_id="company:x", platform="YouTube", disclosure_type="paid_sponsorship", review_status="confirmed")
        s.company = company
        creator = self._creator_with_sponsorships([s], quoted=True)
        result = creator_connector_candidacy(creator)
        self.assertFalse(result["is_candidate"])

    def test_repeat_paid_with_quote_is_a_candidate(self):
        company = _company(company_id="company:x", name="X")
        sponsorships = []
        for _ in range(2):
            s = SponsorshipEvidence(company_id="company:x", platform="YouTube", disclosure_type="paid_sponsorship", review_status="confirmed")
            s.company = company
            sponsorships.append(s)
        creator = self._creator_with_sponsorships(sponsorships, quoted=True)
        result = creator_connector_candidacy(creator)
        self.assertTrue(result["is_candidate"])
        self.assertEqual(len(result["repeat_paid_relationships"]), 1)
        self.assertEqual(result["repeat_paid_relationships"][0]["confirmed_paid_count"], 2)

    def test_repeat_but_no_quote_is_not_a_candidate(self):
        """Mango can't act on the candidacy without a way to reach the
        creator -- repeat history alone isn't enough."""
        company = _company(company_id="company:x", name="X")
        sponsorships = []
        for _ in range(2):
            s = SponsorshipEvidence(company_id="company:x", platform="YouTube", disclosure_type="paid_sponsorship", review_status="confirmed")
            s.company = company
            sponsorships.append(s)
        creator = self._creator_with_sponsorships(sponsorships, quoted=False)
        result = creator_connector_candidacy(creator)
        self.assertFalse(result["is_candidate"])

    def test_rejected_sponsorship_excluded_from_repeat_count(self):
        company = _company(company_id="company:x", name="X")
        s1 = SponsorshipEvidence(company_id="company:x", platform="YouTube", disclosure_type="paid_sponsorship", review_status="confirmed")
        s1.company = company
        s2 = SponsorshipEvidence(company_id="company:x", platform="YouTube", disclosure_type="paid_sponsorship", review_status="rejected")
        s2.company = company
        creator = self._creator_with_sponsorships([s1, s2], quoted=True)
        result = creator_business_relationships(creator)
        self.assertEqual(result[0]["observation_count"], 1)

    def test_affiliate_only_repeat_is_not_treated_as_paid(self):
        """disclosure_type must be checked, not just repeat count -- an
        affiliate/mention repeated twice is still not paid_sponsorship
        evidence."""
        company = _company(company_id="company:x", name="X")
        sponsorships = []
        for _ in range(2):
            s = SponsorshipEvidence(company_id="company:x", platform="YouTube", disclosure_type="affiliate", review_status="confirmed")
            s.company = company
            sponsorships.append(s)
        creator = self._creator_with_sponsorships(sponsorships, quoted=True)
        result = creator_connector_candidacy(creator)
        self.assertFalse(result["is_candidate"])

    def test_unreviewed_paid_repeat_is_not_a_connector_candidate(self):
        company = _company(company_id="company:x", name="X")
        sponsorships = []
        for _ in range(2):
            evidence = SponsorshipEvidence(
                company_id="company:x",
                platform="YouTube",
                disclosure_type="paid_sponsorship",
                review_status="unreviewed",
            )
            evidence.company = company
            sponsorships.append(evidence)
        creator = self._creator_with_sponsorships(sponsorships, quoted=True)
        result = creator_connector_candidacy(creator)
        self.assertFalse(result["is_candidate"])
        relationship = creator_business_relationships(creator)[0]
        self.assertTrue(relationship["has_paid_observation"])
        self.assertFalse(relationship["has_confirmed_paid_evidence"])
        self.assertEqual(relationship["confirmed_paid_count"], 0)

    def test_one_confirmed_plus_one_unreviewed_is_not_confirmed_repeat(self):
        company = _company(company_id="company:x", name="X")
        sponsorships = []
        for status in ("confirmed", "unreviewed"):
            evidence = SponsorshipEvidence(
                company_id="company:x",
                platform="YouTube",
                disclosure_type="paid_sponsorship",
                review_status=status,
            )
            evidence.company = company
            sponsorships.append(evidence)
        creator = self._creator_with_sponsorships(sponsorships, quoted=True)
        result = creator_connector_candidacy(creator)
        self.assertFalse(result["is_candidate"])


if __name__ == "__main__":
    unittest.main()
