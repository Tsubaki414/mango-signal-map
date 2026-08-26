import unittest

from scripts import collect_operator_x_identities_v4 as operators


def research_row(name="Ada Lovelace", role="Head of Growth", handle="ada_growth"):
    return {
        "company": "Example AI",
        "official_domain": "example.ai",
        "candidate_operator": {
            "name": name,
            "role": role,
            "x_handle": handle,
            "decision_relevance": "Owns growth programs.",
        },
        "operator_evidence": [
            {
                "source_type": "official_company_team_page",
                "url": "https://example.ai/team",
                "claim": f"{name} is {role} at Example AI and uses @{handle}.",
                "fact_status": "confirmed_current_role",
            }
        ],
    }


def rapid_profile(handle="ada_growth", name="Ada Lovelace", description="Head of Growth at Example AI"):
    return {
        "result": {
            "__typename": "User",
            "rest_id": "42",
            "legacy": {
                "screen_name": handle,
                "name": name,
                "description": description,
            },
        }
    }


class OperatorIdentityContractTests(unittest.TestCase):
    def test_all_deterministic_gates_confirm_operator(self):
        result = operators.resolution_record(
            research_row(), rapid_profile(), "cache.json", "2026-08-25T00:00:00+00:00"
        )
        self.assertTrue(result["confirmed"])
        self.assertEqual(
            result["status"], "confirmed_official_role_and_rapid_x_exact_profile"
        )
        self.assertTrue(all(result["gates"].values()))

    def test_exact_handle_is_required(self):
        result = operators.resolution_record(
            research_row(), rapid_profile(handle="ada_support"), "cache.json", "2026-08-25T00:00:00+00:00"
        )
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["gates"]["rapid_x_exact_handle"])

    def test_single_first_name_cannot_pass_person_identity_gate(self):
        result = operators.resolution_record(
            research_row(name="Naomi"),
            rapid_profile(name="Naomi"),
            "cache.json",
            "2026-08-25T00:00:00+00:00",
        )
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["gates"]["deterministic_person_identity_match"])

    def test_linkedin_only_role_evidence_is_rejected(self):
        row = research_row()
        row["operator_evidence"][0]["source_type"] = "linkedin_profile"
        row["operator_evidence"][0]["url"] = "https://linkedin.com/in/ada"
        result = operators.resolution_record(
            row, rapid_profile(), "cache.json", "2026-08-25T00:00:00+00:00"
        )
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["gates"]["official_current_role_evidence"])
        self.assertFalse(result["gates"]["non_linkedin_evidence_only"])

    def test_company_association_is_not_inferred_from_name_and_handle_alone(self):
        row = research_row()
        row["operator_evidence"][0]["claim"] = "Ada Lovelace is Head of Growth at Example AI."
        result = operators.resolution_record(
            row,
            rapid_profile(description="Growth leader and builder"),
            "cache.json",
            "2026-08-25T00:00:00+00:00",
        )
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["gates"]["company_association"])

    def test_handle_discovery_requires_unique_exact_name_and_company_match(self):
        class Client:
            def search(self, *_args, **_kwargs):
                return rapid_profile()

        handle, _payload, matches = operators.discover_exact_handle(
            Client(), research_row(handle=None), force=False
        )
        self.assertEqual(handle, "ada_growth")
        self.assertEqual(len(matches), 1)

    def test_handle_discovery_rejects_name_only_result_without_company_association(self):
        class Client:
            def search(self, *_args, **_kwargs):
                return rapid_profile(description="Independent growth consultant")

        handle, _payload, matches = operators.discover_exact_handle(
            Client(), research_row(handle=None), force=False
        )
        self.assertEqual(handle, "")
        self.assertEqual(matches, [])

    def test_secondary_url_containing_company_word_is_not_official(self):
        self.assertFalse(operators.is_official_evidence({
            "source_type": "reputable_secondary",
            "publisher": "TechCrunch",
            "url": "https://example.com/company/jane",
        }))

    def test_generic_blog_subdomain_cannot_create_company_association(self):
        row = research_row()
        row["company"] = "Vana"
        row["operator_evidence"][0]["url"] = "https://blog.example.org/post"
        profile = operators.response_users(rapid_profile(description="I write a blog about growth"))[0]
        self.assertFalse(operators.profile_company_matches(row, profile))

    def test_official_role_name_match_respects_token_boundaries(self):
        row = research_row(name="Ann Lee")
        row["operator_evidence"][0]["claim"] = "Joann Lee is Head of Growth at Example AI."
        self.assertEqual(
            operators.official_role_evidence(row, "Ann Lee", "Head of Growth"), []
        )

    def test_activity_words_do_not_substitute_for_an_explicit_title(self):
        cases = (
            (
                "Diego Rodriguez",
                "Enterprise partnership representative; formal title not stated",
                "A note from Diego Rodriguez describes how he developed the partnership.",
                "official_activity_candidate_title_incomplete_pending_rapid_x",
            ),
            (
                "Jordan Dearsley",
                "Founder-level Series B announcement author; formal title not printed",
                "Jordan Dearsley says we started the company and the Series B funds distribution.",
                "official_activity_candidate_title_incomplete_pending_rapid_x",
            ),
        )
        for name, role, claim, status in cases:
            with self.subTest(name=name):
                row = research_row(name=name, role=role)
                row["status"] = status
                row["operator_evidence"][0]["claim"] = claim
                self.assertFalse(operators.candidate_is_writeable(row))
                self.assertEqual(operators.official_role_evidence(row, name, role), [])

    def test_historical_current_role_revalidation_candidate_is_not_writeable(self):
        row = research_row(name="Jenny Na", role="Notion for Startups lead")
        row["status"] = "historical_official_candidate_current_role_revalidation_required"
        row["operator_evidence"][0].update({
            "claim": "Jenny Na was the Notion for Startups lead within marketing.",
            "fact_status": "confirmed_historical_role_requires_current_revalidation",
        })
        self.assertFalse(operators.candidate_is_writeable(row))
        self.assertEqual(operators.official_role_evidence(row, "Jenny Na", row["candidate_operator"]["role"]), [])


if __name__ == "__main__":
    unittest.main()
