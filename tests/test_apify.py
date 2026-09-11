from __future__ import annotations

import json
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

from mangobd.apify import ApifyClient, ApifyError, actor_api_id
from mangobd.apify_ingestion import (
    contact_actor_input,
    instagram_actor_input,
    linkedin_actor_input,
    merge_review_queue,
    normalize_apify_items,
    website_actor_input,
    youtube_actor_input,
)
from scripts.build_apify_candidate_exports import (
    build_gamma_campaign_plan,
    build_incremental_company_digest,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


class ApifyClientTests(unittest.TestCase):
    def test_actor_store_id_is_normalized(self):
        self.assertEqual(actor_api_id("apify/website-content-crawler"), "apify~website-content-crawler")

    def test_token_uses_authorization_header_not_query_string(self):
        captured = {}

        def opener(request, **_kwargs):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            return FakeResponse({"data": {"id": "run-1"}})

        with patch("mangobd.apify.ssl.create_default_context"):
            client = ApifyClient(token="secret-token", opener=opener)
            client.start_actor("owner/actor", {"startUrls": []}, max_total_charge_usd=1.25)
        self.assertNotIn("secret-token", captured["url"])
        self.assertEqual(captured["authorization"], "Bearer secret-token")
        self.assertIn("maxTotalChargeUsd=1.25", captured["url"])

    def test_list_runs_returns_items(self):
        def opener(_request, **_kwargs):
            return FakeResponse({"data": {"items": [{"id": "run-1", "status": "SUCCEEDED"}]}})

        with patch("mangobd.apify.ssl.create_default_context"):
            client = ApifyClient(token="secret-token", opener=opener)
            self.assertEqual(client.list_runs()[0]["id"], "run-1")

    def test_idempotent_get_retries_incomplete_reads(self):
        calls = 0

        def opener(_request, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise IncompleteRead(b"partial")
            return FakeResponse({"data": {"id": "user-1"}})

        with patch("mangobd.apify.ssl.create_default_context"), patch("mangobd.apify.time.sleep"):
            client = ApifyClient(token="secret-token", opener=opener)
            self.assertEqual(client.get_current_user()["id"], "user-1")
        self.assertEqual(calls, 2)

    def test_missing_token_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ApifyError):
                ApifyClient(env_file=Path(directory) / ".env")


class ApifyIngestionTests(unittest.TestCase):
    def test_official_web_normalization_stays_unreviewed(self):
        rows = normalize_apify_items(
            [
                {
                    "url": "https://example.com/partners",
                    "text": "Partner with us at business@example.com https://linkedin.com/company/example",
                    "metadata": {"title": "Partners"},
                }
            ],
            company_id="company:example",
            company_name="Example",
            source_kind="official_web",
            actor_id="apify/website-content-crawler",
            actor_build="version-0",
            run_id="run-1",
            dataset_id="dataset-1",
            raw_cache_path="data/cache/apify/official_web/run-1/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fact_status"], "observed_unreviewed")
        self.assertEqual(rows[0]["review_status"], "unreviewed")
        self.assertEqual(rows[0]["evidence_type"], "first_party_web_snapshot")
        self.assertEqual(rows[0]["candidate_contacts"]["emails"], ["business@example.com"])
        self.assertTrue(rows[0]["eligible_for_evidence_review"])

    def test_youtube_metrics_and_social_links_are_kept_for_creator_expansion(self):
        rows = normalize_apify_items(
            [
                {
                    "url": "https://youtube.com/watch?v=metric",
                    "text": "Gamma tutorial https://instagram.com/example",
                    "channelName": "Example Creator",
                    "viewCount": 12345,
                    "numberOfSubscribers": 50000,
                }
            ],
            company_id="company:gamma",
            company_name="Gamma",
            source_kind="youtube_sponsorship",
            actor_id="streamers/youtube-scraper",
            actor_build="0.0.287",
            run_id="run-metric",
            dataset_id="dataset-metric",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(rows[0]["candidate_metrics"]["views"], 12345)
        self.assertEqual(rows[0]["candidate_metrics"]["subscribers"], 50000)
        self.assertEqual(rows[0]["candidate_contacts"]["instagram_urls"], ["https://instagram.com/example"])

    def test_japanese_youtube_content_is_language_observed_not_region_inferred(self):
        rows = normalize_apify_items(
            [{"url": "https://youtube.com/watch?v=jp", "title": "Gamma AI の使い方", "text": "プレゼンを作成"}],
            company_id="company:gamma",
            company_name="Gamma",
            source_kind="youtube_sponsorship",
            actor_id="streamers/youtube-scraper",
            actor_build="0.0.287",
            run_id="run-jp",
            dataset_id="dataset-jp",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(rows[0]["candidate_entity"]["content_language_observed"], "Japanese")
        self.assertNotIn("region", rows[0]["candidate_entity"])

    def test_404_item_is_retained_for_audit_but_not_evidence_review(self):
        rows = normalize_apify_items(
            [{"url": "https://example.com/missing", "text": "Page not found", "crawl": {"httpStatusCode": 404}}],
            company_id="company:example",
            company_name="Example",
            source_kind="official_web",
            actor_id="apify/website-content-crawler",
            actor_build="0.3.95",
            run_id="run-404",
            dataset_id="dataset-404",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(rows[0]["source_validity"], "invalid_http_status")
        self.assertFalse(rows[0]["eligible_for_evidence_review"])
        self.assertEqual(rows[0]["candidate_signals"], [])

    def test_linkedin_operator_gate_requires_current_company_position(self):
        rows = normalize_apify_items(
            [
                {
                    "linkedinUrl": "https://www.linkedin.com/in/current",
                    "firstName": "Current",
                    "lastName": "Buyer",
                    "headline": "Creator Partnerships Lead",
                    "currentPosition": [
                        {"position": "Creator Partnerships Lead", "companyName": "Example", "endDate": {"text": "Present"}}
                    ],
                    "experience": [],
                },
                {
                    "linkedinUrl": "https://www.linkedin.com/in/stale",
                    "firstName": "Stale",
                    "lastName": "Claim",
                    "currentPosition": [],
                    "experience": [
                        {"position": "Growth", "companyName": "Example", "endDate": {"text": "Present"}}
                    ],
                },
            ],
            company_id="company:example",
            company_name="Example",
            source_kind="linkedin",
            actor_id="harvestapi/linkedin-company-employees",
            actor_build="0.0.158",
            run_id="run-linkedin",
            dataset_id="dataset-linkedin",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(rows[0]["candidate_entity"]["operator_candidate_status"], "current_direct_buyer")
        self.assertTrue(rows[0]["candidate_entity"]["eligible_for_operator_review"])
        self.assertEqual(
            rows[1]["candidate_entity"]["operator_candidate_status"],
            "profile_claim_needs_current_role_validation",
        )
        self.assertFalse(rows[1]["candidate_entity"]["eligible_for_operator_review"])

    def test_linkedin_operator_gate_accepts_only_explicit_company_aliases(self):
        rows = normalize_apify_items(
            [
                {
                    "linkedinUrl": "https://www.linkedin.com/in/cursor-growth",
                    "firstName": "Cursor",
                    "lastName": "Growth",
                    "currentPosition": [
                        {"position": "Growth Marketing", "companyName": "Anysphere, Inc."}
                    ],
                },
                {
                    "linkedinUrl": "https://www.linkedin.com/in/lookalike",
                    "firstName": "Lookalike",
                    "lastName": "Person",
                    "currentPosition": [
                        {"position": "Growth Marketing", "companyName": "Cursor LLC"}
                    ],
                },
            ],
            company_id="company:cursor",
            company_name="Cursor",
            source_kind="linkedin",
            actor_id="harvestapi/linkedin-company-employees",
            actor_build="0.0.158",
            run_id="run-linkedin-alias",
            dataset_id="dataset-linkedin-alias",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        self.assertTrue(rows[0]["candidate_entity"]["eligible_for_operator_review"])
        self.assertFalse(rows[1]["candidate_entity"]["eligible_for_operator_review"])

    def test_linkedin_program_participant_is_not_promoted_to_operator(self):
        rows = normalize_apify_items(
            [{
                "linkedinUrl": "https://www.linkedin.com/in/program-participant",
                "firstName": "Creator",
                "lastName": "Participant",
                "currentPosition": [
                    {"position": "Affiliate Partner", "companyName": "PixVerse"}
                ],
            }],
            company_id="company:pixverse",
            company_name="PixVerse",
            source_kind="linkedin",
            actor_id="harvestapi/linkedin-company-employees",
            actor_build="0.0.158",
            run_id="run-linkedin-participant",
            dataset_id="dataset-linkedin-participant",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        entity = rows[0]["candidate_entity"]
        self.assertEqual(entity["role_relevance"], "external_program_participant")
        self.assertEqual(entity["operator_candidate_status"], "current_non_buyer_role")
        self.assertFalse(entity["eligible_for_operator_review"])

    def test_youtube_paid_and_affiliate_are_candidates_not_confirmed(self):
        rows = normalize_apify_items(
            [
                {
                    "url": "https://youtube.com/watch?v=one",
                    "title": "Gamma tutorial",
                    "channelName": "Creator One",
                    "text": "This video is sponsored by Gamma. Some links are affiliate links and I may earn a commission.",
                    "date": "2026-08-01",
                }
            ],
            company_id="company:gamma",
            company_name="Gamma",
            source_kind="youtube_sponsorship",
            actor_id="streamers/youtube-scraper",
            actor_build="0.0.287",
            run_id="run-youtube",
            dataset_id="dataset-youtube",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        candidate = rows[0]["candidate_commercial_relationship"]
        self.assertEqual(candidate["classification_candidate"], "paid_sponsorship_and_affiliate_candidate")
        self.assertTrue(candidate["eligible_for_sponsorship_review"])
        self.assertEqual(rows[0]["review_status"], "unreviewed")

    def test_youtube_sponsorship_denial_is_not_paid_candidate(self):
        rows = normalize_apify_items(
            [{"url": "https://youtube.com/watch?v=two", "text": "This Gamma review was not sponsored."}],
            company_id="company:gamma",
            company_name="Gamma",
            source_kind="youtube_sponsorship",
            actor_id="streamers/youtube-scraper",
            actor_build="0.0.287",
            run_id="run-youtube",
            dataset_id="dataset-youtube",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        candidate = rows[0]["candidate_commercial_relationship"]
        self.assertEqual(candidate["classification_candidate"], "explicit_organic_or_denial")
        self.assertFalse(candidate["eligible_for_sponsorship_review"])

    def test_generic_affiliate_disclosure_is_flagged_for_attribution_resolution(self):
        rows = normalize_apify_items(
            [
                {
                    "url": "https://youtube.com/watch?v=three",
                    "text": "Gamma tutorial. Amazon affiliate link: https://amzn.to/example. I may earn a commission.",
                    "descriptionLinks": [{"url": "https://amzn.to/example", "text": "Amazon"}],
                }
            ],
            company_id="company:gamma",
            company_name="Gamma",
            source_kind="youtube_sponsorship",
            actor_id="streamers/youtube-scraper",
            actor_build="0.0.287",
            run_id="run-youtube",
            dataset_id="dataset-youtube",
            raw_cache_path="data/cache/items.json",
            collected_at="2026-09-01T00:00:00Z",
        )
        candidate = rows[0]["candidate_commercial_relationship"]
        self.assertFalse(candidate["affiliate_brand_attributed"])
        self.assertEqual(candidate["attribution_status"], "brand_link_or_disclosure_needs_resolution")

    def test_review_queue_merge_preserves_human_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            base = {
                "observation_id": "apify:one",
                "company_name": "Example",
                "source_url": "https://example.com",
                "review_status": "confirmed",
                "reviewed_at": "2026-09-01T00:01:00Z",
                "reviewed_note": "checked",
            }
            path.write_text(json.dumps({"schema_version": 1, "observations": [base]}))
            refreshed = dict(base, review_status="unreviewed", reviewed_at=None, reviewed_note=None)
            result = merge_review_queue(path, [refreshed])
            row = result["observations"][0]
            self.assertEqual(row["review_status"], "confirmed")
            self.assertEqual(row["reviewed_note"], "checked")

    def test_website_input_is_bounded_and_single_domain(self):
        payload = website_actor_input(["https://example.com/about"], max_pages=7, max_depth=1)
        self.assertEqual(payload["maxCrawlPages"], 7)
        self.assertEqual(payload["maxCrawlDepth"], 1)
        self.assertTrue(payload["respectRobotsTxtFile"])
        with self.assertRaises(ValueError):
            website_actor_input(["https://one.example/a", "https://two.example/b"])

    def test_empty_or_cross_domain_inputs_are_rejected_before_spend(self):
        with self.assertRaises(ValueError):
            website_actor_input([])
        with self.assertRaises(ValueError):
            website_actor_input(["not-a-url"])

    def test_platform_inputs_are_bounded_and_disable_ai_addons(self):
        linkedin = linkedin_actor_input(["https://www.linkedin.com/company/example"], max_items=12)
        self.assertEqual(linkedin["maxItems"], 12)
        self.assertNotIn("email search", linkedin["profileScraperMode"].casefold())
        youtube = youtube_actor_input(["Example sponsored"], max_results=18)
        self.assertEqual(youtube["maxResults"], 18)
        self.assertFalse(youtube["aiVideoDescription"])
        self.assertFalse(youtube["aiVideoSummary"])
        contact = contact_actor_input(["https://example.com"], max_requests=9)
        self.assertEqual(contact["maxRequestsPerCrawl"], 9)
        instagram = instagram_actor_input(["https://www.instagram.com/example/"], max_results=7)
        self.assertEqual(instagram["resultsLimit"], 7)


class ApifyExportTests(unittest.TestCase):
    def test_gamma_campaign_plan_survives_multi_company_export_refactors(self):
        plan = build_gamma_campaign_plan(
            [{
                "creator": "Kevin Stratvert",
                "attribution_status": "brand_attributed",
                "channel_url": "https://youtube.com/@KevinStratvert",
                "content_url": "https://youtube.com/watch?v=gamma",
                "published_at": "2026-07-13",
                "disclosure_type_candidate": "paid_sponsorship",
                "metrics": {"subscribers": 4_000_000, "views": 20_000},
                "existing_creator_id": 1,
                "rate_on_file": False,
            }],
            [{"creator": "Kevin Stratvert", "rates": []}],
            [],
        )
        self.assertEqual(len(plan["cohorts"]), 4)
        self.assertEqual(plan["cohorts"][0]["creator_candidates"][0]["creator"], "Kevin Stratvert")

    def test_attribution_holds_do_not_create_a_company_campaign_cohort(self):
        digest = build_incremental_company_digest(
            company_id="company:example",
            company_name="Example",
            rows=[{
                "source_kind": "official_web",
                "eligible_for_evidence_review": True,
                "source_title": "Partner with Example",
                "source_url": "https://example.com/partners",
                "source_validity": "valid_http_response",
                "candidate_signals": [{"signal_type": "partnership_ecosystem"}],
            }],
            operators=[],
            sponsorships=[{
                "company_id": "company:example",
                "creator": "Unresolved Creator",
                "attribution_status": "brand_link_or_disclosure_needs_resolution",
            }],
        )
        self.assertEqual(digest["decision_ready_counts"]["brand_attribution_holds"], 1)
        self.assertEqual(digest["decision_ready_counts"]["brand_attributed_commercial_candidates"], 0)
        self.assertEqual(digest["campaign"]["cohorts"], [])
        self.assertEqual(len(digest["public_contact_routes"]), 1)


if __name__ == "__main__":
    unittest.main()
