import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts import run_v4_qa as qa


class V4QATests(unittest.TestCase):
    @staticmethod
    def operator_artifacts():
        root = Path(__file__).resolve().parents[1]
        out = root / "outputs" / "pilot_v4"
        return (
            json.loads((root / "data" / "pilot_v4" / "top_operator_routes.json").read_text(encoding="utf-8")),
            json.loads((out / "operator_x_identity_resolutions_v4.json").read_text(encoding="utf-8")),
            json.loads((out / "operator_person_paths_v4.json").read_text(encoding="utf-8")),
            json.loads((out / "priority_action_queue_v4.json").read_text(encoding="utf-8")),
            json.loads((out / "company_master_v4.json").read_text(encoding="utf-8")),
        )

    def test_graphml_counts_preserve_direction_independent_shape(self):
        xml = """<?xml version='1.0'?><graphml xmlns='http://graphml.graphdrawing.org/xmlns'><graph edgedefault='directed'><node id='a'/><node id='b'/><edge source='a' target='b'/></graph></graphml>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiny.graphml"
            path.write_text(xml, encoding="utf-8")
            self.assertEqual(qa.graphml_counts(path), (2, 1))

    def test_normalized_company_identity(self):
        self.assertEqual(qa.normalized("v0 / Vercel"), "v0vercel")

    def test_spend_freshness_gate_requires_marker_for_stale_high_confidence(self):
        rows = [{
            "company": "Stale",
            "spend_evidence_level": "L3",
            "last_verified_at": "2026-08-24",
        }]
        violations = qa.revalidation_violations(rows, date(2026, 8, 25), "fixture.json")
        self.assertEqual(len(violations), 1)
        rows[0]["revalidation_needed"] = True
        self.assertEqual(qa.revalidation_violations(rows, date(2026, 8, 25), "fixture.json"), [])

    def test_spend_freshness_gate_localizes_utc_before_comparing_snapshot_date(self):
        # 17:00 UTC on Aug 24 is 01:00 on the Aug 25 Asia/Shanghai snapshot.
        rows = [{
            "company": "Current",
            "budget_confidence": "high_confirmed_spend_mechanism",
            "last_verified_at": "2026-08-24T17:00:00+00:00",
        }]
        self.assertEqual(qa.revalidation_violations(rows, date(2026, 8, 25), "fixture.json"), [])

    def test_spend_freshness_gate_rejects_missing_date_without_marker(self):
        rows = [{"company": "Missing", "budget_confidence": "confirmed"}]
        violations = qa.revalidation_violations(rows, date(2026, 8, 25), "fixture.json")
        self.assertEqual(violations[0]["reason"], "missing_or_invalid_last_verified_at")

    def test_spend_refresh_cannot_be_masked_by_a_newer_unrelated_record_timestamp(self):
        rows = [{
            "company": "Operator Refreshed, Spend Stale",
            "spend_evidence_level": "L3",
            "last_verified_at": "2026-08-25T10:00:00+08:00",
            "spend_last_verified_at": "2026-08-24",
        }]
        violations = qa.revalidation_violations(rows, date(2026, 8, 25), "fixture.json")
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["spend_last_verified_at"], "2026-08-24")

    def test_operator_contract_covers_all_actions_and_strict_resolutions(self):
        details = qa.validate_operator_semantics(*self.operator_artifacts())
        self.assertEqual(details["operator_routes"], 42)
        self.assertEqual(details["action_queue_records"], 42)
        self.assertGreater(details["strict_named_operators"], qa.PRIOR_NAMED_OPERATOR_COUNT)
        self.assertEqual(
            details["strict_named_operators"] + details["unresolved_with_specific_acquisition_path"],
            42,
        )
        self.assertEqual(details["linkedin_evidence_urls"], 0)
        self.assertEqual(details["generic_role_placeholders"], 0)

    def test_unresolved_operator_requires_specific_acquisition_path(self):
        artifacts = list(self.operator_artifacts())
        routes = copy.deepcopy(artifacts[0])
        unresolved = next(row for row in routes if not (row.get("recommended_operator") or {}).get("name"))
        unresolved["recommended_operator"]["status"] = "role_to_find_or_validate"
        unresolved["recommended_operator"]["acquisition_next_step"] = ""
        artifacts[0] = routes
        with self.assertRaisesRegex(ValueError, "role_to_find|specific acquisition"):
            qa.validate_operator_semantics(*artifacts)

    def test_degree_label_domain_rejects_non_linkedin_hop_aliases(self):
        self.assertEqual(qa.validate_degree_labels([None, "direct", "secondary", "third"], "fixture")["rows"], 4)
        with self.assertRaisesRegex(ValueError, "invalid degree_label"):
            qa.validate_degree_labels(["2nd"], "fixture")

    def test_full_report_keeps_loaded_company_details_compact(self):
        report = qa.run_checks()
        details = {row["name"]: row["details"] for row in report["checks"]}
        self.assertEqual(details["company_universe_load"], {"rows": 77})
        self.assertEqual(details["company_master_load"], {"rows": 77})
        self.assertEqual(details["operator_identity_and_route_semantics"]["operator_routes"], 42)


if __name__ == "__main__":
    unittest.main()
