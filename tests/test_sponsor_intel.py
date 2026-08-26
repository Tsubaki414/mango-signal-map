import unittest

from mangobd.sponsor_intel import (
    BrandAliasResolver,
    ExtractionProvenance,
    SponsorMention,
    build_brand_signals,
    build_sponsorship_edges,
    creator_gap,
    exact_category_competitors,
    mention_from_legacy,
    sponsor_gap,
)


def make_mention(
    observation_id: str,
    creator_id: str,
    brand_id: str,
    observed_date: str,
    *,
    disclosure_class: str = "confirmed_paid_sponsorship",
    fact_status: str = "confirmed",
    confidence: float = 0.95,
) -> SponsorMention:
    mention_type = {
        "confirmed_paid_sponsorship": "paid_sponsorship",
        "confirmed_program_or_official_campaign": "official_program",
        "affiliate_or_referral": "affiliate",
    }.get(disclosure_class, "unverified_mention")
    evidence_id = f"ev_{observation_id}"
    return SponsorMention(
        observation_id=observation_id,
        creator_id=creator_id,
        canonical_brand_id=brand_id,
        raw_brand=brand_id,
        brand_resolution_status="explicit_canonical_id",
        content_id=f"record:{observation_id}",
        content_title=observation_id,
        content_url=f"https://example.com/{observation_id}",
        observed_date=observed_date,
        platform="YouTube",
        cooperation_type="paid sponsorship",
        disclosure_class=disclosure_class,
        mention_type=mention_type,
        confidence=confidence,
        fact_status=fact_status,
        start_ts=None,
        provenance=ExtractionProvenance(
            source_record_id=observation_id,
            source_type="unit_test",
            extractor="fixture",
            extractor_version="1",
            matched_excerpt=f"Sponsored by {brand_id}",
            evidence_ids=(evidence_id,),
            source_urls=(f"https://example.com/{observation_id}",),
            evidence_excerpts=(f"Sponsored by {brand_id}",),
            last_verified_at="2026-02-01",
        ),
    )


class SponsorIntelTests(unittest.TestCase):
    def test_alias_resolution_surfaces_ambiguity_and_explicit_id_wins(self):
        resolver = BrandAliasResolver.from_projects(
            [
                {"id": "proj_gamma", "company": "Gamma.app", "aliases": ["Gamma"]},
                {"id": "proj_gamma_other", "company": "Gamma Other", "aliases": ["Gamma"]},
            ]
        )
        ambiguous = resolver.resolve(" gamma ")
        self.assertEqual(ambiguous.status, "ambiguous_alias")
        self.assertIsNone(ambiguous.canonical_brand_id)
        explicit = resolver.resolve("Gamma", explicit_id="proj_gamma")
        self.assertEqual(explicit.status, "explicit_canonical_id")
        self.assertEqual(explicit.canonical_brand_id, "proj_gamma")
        unknown = resolver.resolve("Gamma.app", explicit_id="proj_missing")
        self.assertEqual(unknown.status, "unknown_explicit_id")
        self.assertIsNone(unknown.canonical_brand_id)

    def test_legacy_adapter_retains_provenance_and_does_not_promote_affiliate(self):
        projects = [{"id": "proj_gamma", "company": "Gamma"}]
        resolver = BrandAliasResolver.from_projects(projects)
        evidence = {
            "ev_1": {
                "verified_date": "2026-08-24",
                "source_url": "https://source.example/proof",
                "excerpt": "The description contains an affiliate link.",
            }
        }
        record = {
            "id": "sp_1",
            "creator_id": "creator_1",
            "project_id": "proj_gamma",
            "platform": "YouTube",
            "content_title": "Gamma workflow",
            "content_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "content_date": "2026-08-20",
            "cooperation_type": "tracked recommendation / probable affiliate",
            "disclosure": "This description contains an affiliate link",
            "evidence_ids": ["ev_1"],
            "confidence": "high_probability",
        }
        mention = mention_from_legacy(record, resolver, "Gamma", evidence)
        self.assertEqual(mention.canonical_brand_id, "proj_gamma")
        self.assertEqual(mention.content_id, "youtube:abcdefghijk")
        self.assertEqual(mention.disclosure_class, "affiliate_or_referral")
        self.assertEqual(mention.fact_status, "probable")
        self.assertEqual(mention.confidence, 0.75)
        self.assertEqual(mention.provenance.last_verified_at, "2026-08-24")
        self.assertIn("https://source.example/proof", mention.provenance.source_urls)
        self.assertEqual(mention.provenance.extractor, "legacy_sponsorship_adapter")

    def test_edges_retain_evidence_and_expose_repeat_and_multi_creator_signals(self):
        mentions = [
            make_mention("a1", "creator_a", "brand_x", "2026-01-01"),
            make_mention("a2", "creator_a", "brand_x", "2026-01-20"),
            make_mention(
                "b1",
                "creator_b",
                "brand_x",
                "2026-01-25",
                disclosure_class="confirmed_program_or_official_campaign",
            ),
        ]
        edges = build_sponsorship_edges(mentions)
        edge_a = next(edge for edge in edges if edge.creator_id == "creator_a")
        self.assertTrue(edge_a.directional)
        self.assertEqual(edge_a.source_id, "brand_x")
        self.assertEqual(edge_a.target_id, "creator_a")
        self.assertTrue(edge_a.repeat_confirmed_relationship)
        self.assertTrue(edge_a.multi_creator_signal)
        self.assertEqual(edge_a.campaign_signal, "repeat_and_multi_creator_confirmed")
        self.assertEqual(edge_a.dates, ("2026-01-20", "2026-01-01"))
        self.assertEqual(len(edge_a.evidence), 2)
        self.assertEqual(edge_a.evidence[0].extractor, "fixture")

        signal = build_brand_signals(edges)[0]
        self.assertEqual(signal["confirmed_creator_count"], 2)
        self.assertTrue(signal["repeat_signal"])
        self.assertTrue(signal["multi_creator_signal"])

    def test_affiliate_edge_is_not_promoted_by_brand_level_multi_creator_signal(self):
        mentions = [
            make_mention("paid_1", "creator_a", "brand_x", "2026-01-01"),
            make_mention("paid_2", "creator_b", "brand_x", "2026-01-02"),
            make_mention(
                "affiliate",
                "creator_c",
                "brand_x",
                "2026-01-03",
                disclosure_class="affiliate_or_referral",
            ),
        ]
        affiliate_edge = next(
            edge
            for edge in build_sponsorship_edges(mentions)
            if edge.creator_id == "creator_c"
        )
        self.assertTrue(affiliate_edge.multi_creator_signal)
        self.assertEqual(affiliate_edge.confirmed_paid_or_program_count, 0)
        self.assertEqual(affiliate_edge.campaign_signal, "affiliate_or_unverified_only")

    def test_sponsor_gap_uses_shared_confirmed_brands_and_recency(self):
        pairs = [
            ("target", "brand_a"),
            ("target", "brand_b"),
            ("peer_1", "brand_a"),
            ("peer_1", "brand_b"),
            ("peer_1", "brand_c"),
            ("peer_2", "brand_a"),
            ("peer_2", "brand_b"),
            ("peer_2", "brand_c"),
            ("peer_2", "brand_d"),
        ]
        mentions = [
            make_mention(f"m{index}", creator, brand, "2026-01-15")
            for index, (creator, brand) in enumerate(pairs)
        ]
        result = sponsor_gap(
            build_sponsorship_edges(mentions),
            "target",
            min_shared_brands=2,
            min_peers=2,
            as_of="2026-02-01",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["peer_ids"], ["peer_1", "peer_2"])
        self.assertEqual(result["results"][0]["candidate_brand_id"], "brand_c")
        self.assertEqual(result["results"][0]["supporting_peer_count"], 2)
        self.assertEqual(result["results"][0]["score"], 2.0)

    def test_creator_gap_requires_explicit_competitors_and_ranks_coverage(self):
        pairs = [
            ("target_creator", "brand_target"),
            ("creator_1", "brand_b"),
            ("creator_2", "brand_b"),
            ("creator_1", "brand_c"),
            ("creator_3", "brand_c"),
        ]
        mentions = [
            make_mention(f"g{index}", creator, brand, "2026-01-15")
            for index, (creator, brand) in enumerate(pairs)
        ]
        result = creator_gap(
            build_sponsorship_edges(mentions),
            "brand_target",
            ["brand_b", "brand_c"],
            as_of="2026-02-01",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["results"][0]["candidate_creator_id"], "creator_1")
        self.assertEqual(result["results"][0]["supporting_competitor_count"], 2)
        self.assertEqual(result["results"][0]["score"], 2.0)

    def test_sparse_inputs_return_insufficient_history_instead_of_a_score(self):
        edges = build_sponsorship_edges(
            [
                make_mention("a", "creator_a", "brand_a", "2026-01-01"),
                make_mention("b", "creator_b", "brand_a", "2026-01-02"),
                make_mention("c", "creator_c", "brand_b", "2026-01-03"),
            ]
        )
        sponsor_result = sponsor_gap(edges, "creator_a")
        self.assertEqual(sponsor_result["status"], "insufficient_history")
        self.assertEqual(sponsor_result["results"], [])

        creator_result = creator_gap(edges, "brand_a", ["brand_b"])
        self.assertEqual(creator_result["status"], "insufficient_history")
        self.assertEqual(creator_result["results"], [])

    def test_exact_category_competitors_do_not_invent_semantic_matches(self):
        competitors = exact_category_competitors(
            [
                {"id": "a", "category": "AI video"},
                {"id": "b", "category": "ai-video"},
                {"id": "c", "category": "Generative video"},
            ]
        )
        self.assertEqual(competitors["a"], ["b"])
        self.assertEqual(competitors["b"], ["a"])
        self.assertEqual(competitors["c"], [])


if __name__ == "__main__":
    unittest.main()
