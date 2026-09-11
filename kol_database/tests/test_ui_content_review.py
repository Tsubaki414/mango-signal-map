from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.review_ui_content import (  # noqa: E402
    ISSUE_TYPES,
    _extract_output_text,
    collect_packet,
    response_schema,
)


class TestUIContentReviewPacket(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = collect_packet("2026-08-28")

    def test_packet_covers_all_required_representative_states(self):
        pairs = {(page["page"], page["state"]) for page in self.packet["pages"]}
        required = {
            ("首页", "默认首屏"),
            ("商机列表", "按执行状态排序"),
            ("公司详情", "E0 公司详情"),
            ("公司详情", "E1 单向关注公司详情"),
            ("公司详情", "E1 互关候选公司详情"),
            ("公司详情", "E2 单次互动公司详情"),
            ("公司详情", "Direct Contact、无 warm path"),
            ("公司详情", "Runway"),
            ("公司详情", "Replit"),
            ("公司详情", "Cursor"),
            ("公司详情", "Olas"),
            ("公司详情", "Sapien"),
            ("公司详情", "GAIB"),
            ("公司详情", "有 warm candidate、无直接渠道（模板夹具）"),
            ("人脉网络", "Connector Interview Queue"),
            ("Creator 列表", "核心 KOL、按互动率排序"),
            ("Creator 详情", "代表性核心 KOL"),
            ("Campaign Builder", "空 Campaign / 当前生产数据"),
            ("Campaign Builder", "有候选人的非持久化模板夹具"),
            ("全局状态", "Loading / API Error / Empty State"),
        }
        self.assertEqual(pairs, required)
        self.assertEqual(self.packet["metadata"]["page_state_count"], 20)

    def test_synthetic_states_are_explicit_and_never_claim_persistence(self):
        fixtures = [page for page in self.packet["pages"] if "fixture" in page["state_origin"]]
        self.assertEqual(len(fixtures), 2)
        self.assertTrue(all(page["state_origin"] == "non_persistent_template_fixture" for page in fixtures))
        self.assertTrue(self.packet["metadata"]["advisory_only"])
        self.assertFalse(self.packet["metadata"]["database_writes"])
        self.assertFalse(self.packet["metadata"]["production_copy_auto_apply"])

    def test_packet_contains_no_api_key_value(self):
        serialized = json.dumps(self.packet, ensure_ascii=False)
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                if raw.startswith("OPENAI_API_KEY="):
                    value = raw.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        self.assertNotIn(value, serialized)

    def test_warm_only_fixture_has_no_stale_direct_channel_copy(self):
        fixture = next(page for page in self.packet["pages"] if page["state"] == "有 warm candidate、无直接渠道（模板夹具）")
        action = fixture["structured_data"]["recommended_action"]
        self.assertEqual(action["kind"], "connector_only")
        self.assertNotIn("direct_contact", {step["kind"] for step in action["steps"]})
        self.assertNotIn("可直接私信", action["text"])
        self.assertEqual(fixture["structured_data"]["execution_status"], "等待内部核实")

    def test_creator_review_state_contains_real_sponsorship_review_status(self):
        creator = next(page for page in self.packet["pages"] if page["page"] == "Creator 详情")
        data = creator["structured_data"]
        self.assertGreater(data["sponsorship_observation_count"], 0)
        self.assertGreater(data["sponsorship_review_counts"].get("unreviewed", 0), 0)
        self.assertTrue(all("review_status" in row for row in data["sponsorship_observation_samples"]))

    def test_e0_state_uses_its_own_gamma_screenshot(self):
        e0 = next(page for page in self.packet["pages"] if page["state"] == "E0 公司详情")
        self.assertIsNotNone(e0["screenshot"])
        self.assertTrue(e0["screenshot"].endswith("gamma-e0-desktop.png"))

    def test_current_company_actions_expose_route_kind(self):
        company_pages = [
            page for page in self.packet["pages"]
            if page["page"] == "公司详情" and page["state_origin"] == "current_application_data"
        ]
        self.assertTrue(company_pages)
        self.assertTrue(all(page["structured_data"]["recommended_action"]["kind"] for page in company_pages))


class TestUIContentReviewSchema(unittest.TestCase):
    def test_schema_is_strict_and_supports_every_required_issue_type(self):
        schema = response_schema()
        self.assertFalse(schema["additionalProperties"])
        review = schema["properties"]["reviews"]["items"]
        self.assertFalse(review["additionalProperties"])
        issue = review["properties"]["issues"]["items"]
        self.assertFalse(issue["additionalProperties"])
        self.assertEqual(set(issue["properties"]["issue_type"]["enum"]), set(ISSUE_TYPES))

    def test_output_text_parser_reads_responses_message(self):
        raw = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"reviews":[]}'}],
                }
            ]
        }
        self.assertEqual(_extract_output_text(raw), '{"reviews":[]}')


if __name__ == "__main__":
    unittest.main()
