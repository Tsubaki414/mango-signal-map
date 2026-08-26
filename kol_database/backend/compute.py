"""Small, honest math helpers shared by the API: CPM and quote summaries.

Estimated CPM = quote_usd / avg_views * 1000, per deliverable. We never
invent a views number to make a CPM appear -- if avg_views is missing the
CPM for that rate card is None and stays blank in the UI.
"""

from __future__ import annotations

from .models import Creator, RateCard, SocialAccount


def primary_x_account(creator: Creator) -> SocialAccount | None:
    for acct in creator.social_accounts:
        if acct.platform.upper() in {"X", "TWITTER", "X(TWITTER)"}:
            return acct
    return None


def primary_account(creator: Creator) -> SocialAccount | None:
    x = primary_x_account(creator)
    if x:
        return x
    return creator.social_accounts[0] if creator.social_accounts else None


def rate_card_cpm(rate_card: RateCard, avg_views: float | None) -> float | None:
    if not avg_views or avg_views <= 0 or rate_card.quote_amount_usd is None:
        return None
    return round(rate_card.quote_amount_usd / avg_views * 1000, 2)


def confident_rate_cards(creator: Creator) -> list[RateCard]:
    return [rc for rc in creator.rate_cards if rc.is_confident and rc.quote_amount_usd is not None]


def quote_summary(creator: Creator) -> dict:
    """min/max USD quote across confident rate cards, and CPM min/max using
    the primary account's avg_views if available."""
    confident = confident_rate_cards(creator)
    if not confident:
        return {
            "quote_min_usd": None,
            "quote_max_usd": None,
            "deliverable_count": len(creator.rate_cards),
            "confident_count": 0,
            "cpm_min": None,
            "cpm_max": None,
        }

    amounts = [rc.quote_amount_usd for rc in confident]
    account = primary_account(creator)
    avg_views = account.avg_views if account else None
    cpms = [c for c in (rate_card_cpm(rc, avg_views) for rc in confident) if c is not None]

    return {
        "quote_min_usd": min(amounts),
        "quote_max_usd": max(amounts),
        "deliverable_count": len(creator.rate_cards),
        "confident_count": len(confident),
        "cpm_min": min(cpms) if cpms else None,
        "cpm_max": max(cpms) if cpms else None,
    }
