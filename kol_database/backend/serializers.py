from __future__ import annotations

from collections import Counter

from .bd_compute import sponsorship_evidence_semantics
from .compute import primary_account, quote_summary, rate_card_cpm
from .models import Creator, DISTRIBUTION_CLASSES, MEDIA_CHANNEL_CLASSES, NEEDS_REVIEW_CLASSES, STRATEGIC_CLASSES


def tab_for_creator(creator: Creator) -> str:
    if creator.creator_class in MEDIA_CHANNEL_CLASSES:
        return "media"
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


def _social_account_detail(account) -> dict:
    recent = sorted(account.recent_content, key=lambda row: row.is_pinned, reverse=True)
    sample_size = len(recent)
    metric_non_null = {
        field: sum(getattr(row, field) is not None for row in recent)
        for field in ("views", "likes", "replies", "reposts")
    }
    return {
        "id": account.id,
        "platform": account.platform,
        "platform_raw": account.platform_raw if account.platform_raw != account.platform else None,
        "handle": account.handle,
        "profile_url": account.profile_url,
        "avatar_url": account.avatar_url,
        "display_name_x": account.display_name_x,
        "bio": account.bio,
        "verified": account.verified,
        "account_created_at": account.account_created_at,
        "followers": account.followers,
        "following": account.following,
        "followers_source": account.followers_source,
        "avg_views": account.avg_views,
        "median_views": account.median_views,
        "engagement_rate": account.engagement_rate,
        "posting_frequency": account.posting_frequency,
        "original_repost_ratio": account.original_repost_ratio,
        "promotional_content_ratio": account.promotional_content_ratio,
        "promotional_ratio_method": "keyword_heuristic",
        "promotional_ratio_note": (
            f"基于 {sample_size} 条缓存内容的推广关键词命中率；不等同于真实付费内容占比。"
            if sample_size
            else "没有内容样本，不能估计推广关键词命中率。"
        ),
        "content_summary": account.content_summary,
        "last_enriched_at": account.last_enriched_at.isoformat() if account.last_enriched_at else None,
        "enrichment_error": account.enrichment_error,
        "content_snapshot_available": bool(account.last_enriched_at and sample_size),
        "metric_coverage": {
            "sample_size": sample_size,
            "non_null": metric_non_null,
            "is_partial": any(count < sample_size for count in metric_non_null.values()) if sample_size else False,
        },
        "recent_content": [
            {
                "post_id": row.post_id,
                "text": row.text,
                "posted_at": row.posted_at,
                "views": row.views,
                "likes": row.likes,
                "replies": row.replies,
                "reposts": row.reposts,
                "is_repost": row.is_repost,
                "is_pinned": row.is_pinned,
            }
            for row in recent
        ],
        "sponsors": [
            {"project_name": row.project_name, "mention_count": row.mention_count}
            for row in account.sponsors
        ],
    }


def creator_detail(creator: Creator, *, include_internal: bool = False) -> dict:
    base = creator_summary(creator)
    confirmed_paid = [
        sponsorship
        for sponsorship in creator.sponsorships
        if sponsorship.disclosure_type == "paid_sponsorship" and sponsorship.review_status == "confirmed"
    ]
    repeat_confirmed_paid = sorted(
        name
        for name, count in Counter(s.company.name for s in confirmed_paid if s.company).items()
        if count > 1
    )
    paid_observations = [
        sponsorship
        for sponsorship in creator.sponsorships
        if sponsorship.disclosure_type == "paid_sponsorship"
        and sponsorship.review_status != "rejected"
        and sponsorship.company
    ]
    repeat_paid_observations = []
    for company_name, rows in sorted(
        ((name, [row for row in paid_observations if row.company.name == name])
         for name in {row.company.name for row in paid_observations}),
        key=lambda item: item[0].casefold(),
    ):
        if len(rows) < 2:
            continue
        repeat_paid_observations.append({
            "company": company_name,
            "observation_count": len(rows),
            "confirmed_count": sum(row.review_status == "confirmed" for row in rows),
            "unreviewed_count": sum(row.review_status == "unreviewed" for row in rows),
            "status": "confirmed_repeat" if all(row.review_status == "confirmed" for row in rows) else "review_required",
        })
    base.update(
        {
            "creator_class_reason": creator.creator_class_reason,
            "creator_class_source": creator.creator_class_source,
            "creator_class_locked": creator.creator_class_locked,
            "promotion_level_source": creator.promotion_level_source,
            "internal_notes": creator.internal_notes if include_internal else None,
            "internal_notes_available": bool(creator.internal_notes) if include_internal else False,
            "source_files": creator.source_files,
            "created_at": creator.created_at.isoformat() if creator.created_at else None,
            "social_accounts": [_social_account_detail(a) for a in creator.social_accounts],
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
                if not (
                    rc.quote_amount_usd is None
                    and (rc.deliverable or "").strip().casefold() in {"同上", "same as above"}
                )
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
                    **sponsorship_evidence_semantics(s),
                    "evidence_text": s.evidence_text,
                    "confidence": s.confidence,
                    "review_status": s.review_status,
                    "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
                    "reviewed_note": s.reviewed_note,
                }
                for s in sorted(creator.sponsorships, key=lambda s: s.published_at or "", reverse=True)
            ],
            # A repeated affiliate/mention, rejected row, or unreviewed
            # extraction must never become the stronger "repeat sponsor"
            # claim on the Creator page.
            "repeat_sponsor_companies": repeat_confirmed_paid,
            "repeat_confirmed_paid_sponsor_companies": repeat_confirmed_paid,
            "repeat_paid_observations": repeat_paid_observations,
            "commercial_profile": {
                "quote_freshness": (
                    creator.updated_at.date().isoformat() if creator.updated_at else None
                ),
                "quote_freshness_note": "报价记录最后更新日；原始报价未单独记录日期时，不推断仍然有效。",
                "audience_fit": creator.categories.split(",") if creator.categories else [],
                "relevant_categories": creator.categories.split(",") if creator.categories else [],
                "competitive_conflicts": sorted({
                    s.company.name
                    for s in creator.sponsorships
                    if s.company and s.review_status != "rejected"
                }),
                "mango_relationship": (
                    "已有联系方式和报价"
                    if creator.contacts and creator.rate_cards
                    else "已有报价，联系渠道待确认"
                    if creator.rate_cards
                    else "已有联系方式，报价待确认"
                    if creator.contacts
                    else "未记录直接商业关系"
                ),
                "campaign_role": (
                    "战略 KOL / 类目背书"
                    if creator.creator_class in STRATEGIC_CLASSES
                    else "区域与 KOC 分发"
                    if creator.creator_class in DISTRIBUTION_CLASSES
                    else "媒体/社区渠道"
                    if creator.creator_class in MEDIA_CHANNEL_CLASSES
                    else "需人工审核后再进入方案"
                ),
                "evidence_review_status": (
                    "含人工确认的付费合作证据"
                    if confirmed_paid
                    else "历史商业证据尚未人工确认"
                    if creator.sponsorships
                    else "暂无历史 sponsor 证据"
                ),
            },
        }
    )
    return base
