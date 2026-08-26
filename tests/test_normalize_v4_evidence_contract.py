import unittest

from scripts import normalize_v4_evidence_contract as contract


class EvidenceContractMigrationTests(unittest.TestCase):
    def test_longtail_atomic_claim_gets_contract_fields(self):
        rows = [{
            "company": "Demo",
            "overall_confidence": "high",
            "sources": [{"url": "https://demo.ai/program", "claim": "Cash pool", "date": "2026-01-01", "confidence": "high"}],
        }]
        row = contract.normalize_longtail(rows, "2026-08-25")[0]
        self.assertEqual(row["fact_status"], "confirmed")
        self.assertEqual(row["sources"][0]["evidence_type"], "official")
        self.assertEqual(row["sources"][0]["last_verified_at"], "2026-08-25")

    def test_crypto_url_is_not_promoted_to_atomic_confirmed_claim(self):
        rows = [{"company": "Demo", "metric_as_of": "2026", "evidence_urls": ["https://demo.ai/"]}]
        row = contract.normalize_crypto(rows, "2026-08-25")[0]
        self.assertEqual(row["evidence"][0]["fact_status"], "unverified")
        self.assertIn("not_atomic", row["evidence_contract_status"])

    def test_operator_evidence_inherits_verified_date(self):
        rows = [{
            "company": "Demo", "last_verified_at": "2026-08-24",
            "operator_evidence": [{"url": "https://demo.ai/team", "confidence": "medium"}],
        }]
        source = contract.normalize_operators(rows, "2026-08-25")[0]["operator_evidence"][0]
        self.assertEqual(source["fact_status"], "probable")
        self.assertEqual(source["last_verified_at"], "2026-08-24")


if __name__ == "__main__":
    unittest.main()
