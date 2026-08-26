from __future__ import annotations

from collections import Counter

from .compute import primary_account, quote_summary, rate_card_cpm
from .models import Creator, DISTRIBUTION_CLASSES, NEEDS_REVIEW_CLASSES, STRATEGIC_CLASSES


def tab_for_creator(creator: Creator) -> str:
    if creator.creator_class in DISTRIBUTION_CLASSES:
        return "distribution"
    if creator.creator_class in NEEDS_REVIEW_CLASSES:
        return "needs_review"
    return "strategic"


def creator_summary(creator: Creator) -> dict:
    account = primary_account(creator)
    summary = quote_summary(creator)
    return {
        "id": creator.id,
        "display_name": creator.display_name,
        "handle": account.handle if account else None,
        "platform": account.platform if account else None,
        "platform_raw": account.platform_raw if account and account.platform_raw != (account.platform if account else None) else None,
        "avatar_url": account.avatar_url if account else None,
        "creator_class": creator.creator_class,
        "classification_confidence": creator.classification_confidence,
        "promotion_level": creator.promotion_level,
        "region": creator.region,
        "language": creator.language,
        "categories": creator.categories.split(",") if creator.categories else [],
        "followers": account.followers if account else None,
        "avg_views": account.avg_views if account else None,
        "engagement_rate": account.engagement_rate if account else None,
        "quote_min_usd": summary["quote_min_usd"],
        "quote_max_usd": summary["quote_max_usd"],
        "deliverable_count": summary["deliverable_count"],
        "cpm_min": summary["cpm_min"],
        "cpm_max": summary["cpm_max"],
        "has_email": any(c.method_type == "email" for c in creator.contacts),
        "has_telegram": any(c.method_type == "telegram" for c in creator.contacts),
        "tab": tab_for_creator(creator),
        "last_enriched_at": account.last_enriched_at.isoformat() if account and account.last_enriched_at else None,
        "updated_at": creator.updated_at.isoformat() if creator.updated_at else None,
    }


def creator_detail(creator: Creator) -> dict:
    base = creator_summary(creator)
    base.update(
        {
            "creator_class_reason": creator.creator_class_reason,
            "creator_class_source": creator.creator_class_source,
            "creator_class_locked": creator.creator_class_locked,
            "promotion_level_source": creator.promotion_level_source,
            "internal_notes": creator.internal_notes,
            "source_files": creator.source_files,
            "created_at": creator.created_at.isoformat() if creator.created_at else None,
            "social_accounts": [
                {
                    "id": a.id,
                    "platform": a.platform,
                    "platform_raw": a.platform_raw if a.platform_raw != a.platform else None,
                    "handle": a.handle,
                    "profile_url": a.profile_url,
                    "avatar_url": a.avatar_url,
                    "display_name_x": a.display_name_x,
                    "bio": a.bio,
                    "verified": a.verified,
                    "account_created_at": a.account_created_at,
                    "followers": a.followers,
                    "following": a.following,
                    "followers_source": a.followers_source,
                    "avg_views": a.avg_views,
                    "median_views": a.median_views,
                    "engagement_rate": a.engagement_rate,
                    "posting_frequency": a.posting_frequency,
                    "original_repost_ratio": a.original_repost_ratio,
                    "promotional_content_ratio": a.promotional_content_ratio,
                    "content_summary": a.content_summary,
                    "last_enriched_at": a.last_enriched_at.isoformat() if a.last_enriched_at else None,
                    "enrichment_error": a.enrichment_error,
                    "recent_content": [
                        {
                            "post_id": c.post_id,
                            "text": c.text,
                            "posted_at": c.posted_at,
                            "views": c.views,
                            "likes": c.likes,
                            "replies": c.replies,
                            "reposts": c.reposts,
                            "is_repost": c.is_repost,
                            "is_pinned": c.is_pinned,
                        }
                        for c in sorted(a.recent_content, key=lambda c: c.is_pinned, reverse=True)
                    ],
                    "sponsors": [
                        {"project_name": p.project_name, "mention_count": p.mention_count} for p in a.sponsors
                    ],
                }
                for a in creator.social_accounts
            ],
            "contacts": [{"id": c.id, "method_type": c.method_type, "value": c.value} for c in creator.contacts],
            "rate_cards": [
                {
                    "id": rc.id,
                    "platform": rc.platform,
                    "deliverable": rc.deliverable,
                    "quote_amount": rc.quote_amount,
                    "quote_amount_min": rc.quote_amount_min,
                    "quote_amount_max": rc.quote_amount_max,
                    "quote_currency": rc.quote_currency,
                    "quote_amount_usd": rc.quote_amount_usd,
                    "fx_rate_used": rc.fx_rate_used,
                    "is_confident": rc.is_confident,
                    "raw_quote_text": rc.raw_quote_text,
                    "cpm": rate_card_cpm(rc, base["avg_views"]),
                }
                for rc in creator.rate_cards
            ],
            "campaigns": [
                {
                    "id": cm.id,
                    "campaign_name": cm.campaign_name,
                    "campaign_date": cm.campaign_date,
                    "deliverable": cm.deliverable,
                    "performance_notes": cm.performance_notes,
                }
                for cm in creator.campaigns
            ],
            "sponsorship_history": [
                {
                    "id": s.id,
                    "company_id": s.company_id,
                    "company_name": s.company.name if s.company else None,
                    "platform": s.platform,
                    "content_url": s.content_url,
                    "content_title": s.content_title,
                    "published_at": s.published_at,
                    "disclosure_type": s.disclosure_type,
                    "evidence_text": s.evidence_text,
                    "confidence": s.confidence,
                }
                for s in sorted(creator.sponsorships, key=lambda s: s.published_at or "", reverse=True)
            ],
            "repeat_sponsor_companies": sorted(
                name for name, count in Counter(s.company.name for s in creator.sponsorships if s.company).items() if count > 1
            ),
        }
    )
    return base
