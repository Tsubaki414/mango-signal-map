import unittest

from scripts import collect_x_identity_corrections as collector


class XIdentityCorrectionTests(unittest.TestCase):
    def adjudication(self, handle="@Official_AI"):
        return {
            "company": "Official AI",
            "status": "corrected_handle_requires_rapid_lookup",
            "official_domain": "official.ai",
            "official_social_url": "https://x.com/Official_AI",
            "proposed_handle": handle,
        }

    def test_exact_returned_screen_name_confirms(self):
        payload = {
            "result": {"data": {"user": {"result": {
                "__typename": "User",
                "rest_id": "42",
                "core": {"screen_name": "official_ai", "name": "Official AI"},
                "legacy": {"description": "Official"},
            }}}}
        }
        result = collector.resolve_profile_response(
            self.adjudication(), payload, "cache.json", "2026-08-25T00:00:00+08:00"
        )
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["rest_id"], "42")
        self.assertEqual(result["status"], "confirmed_exact_official_handle_from_rapid_profile")

    def test_returned_handle_mismatch_stays_unresolved(self):
        payload = {
            "result": {
                "__typename": "User",
                "rest_id": "99",
                "core": {"screen_name": "official_support", "name": "Support"},
            }
        }
        result = collector.resolve_profile_response(
            self.adjudication(), payload, "cache.json", "2026-08-25T00:00:00+08:00"
        )
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["rest_id"], "")
        self.assertEqual(result["status"], "unresolved_returned_handle_mismatch")


if __name__ == "__main__":
    unittest.main()
