"""Explainable scoring for Opportunities, Creator campaign fit, and
Relationship strength. Per product spec: three independent judgments, never
one black-box number. Every function here returns a label plus the plain
factors that produced it, not just a score.
"""

from __future__ import annotations

from .models import Company, IntroPath, Operator


def best_intro_path(paths: list[IntroPath], target_type: str | None = None) -> IntroPath | None:
    """Pick the strongest path: reachable beats unreachable, direct beats
    secondary beats third, is_primary beats alternates."""
    candidates = [p for p in paths if target_type is None or p.target_type == target_type]
    if not candidates:
        return None
    degree_rank = {"direct": 0, "secondary": 1, "third": 2}

    def sort_key(p: IntroPath):
        return (
            0 if p.graph_reachable else 1,
            degree_rank.get(p.degree_label, 3),
            0 if p.is_primary else 1,
            p.hop_count or 99,
        )

    return sorted(candidates, key=sort_key)[0]


def reachability_summary(company: Company) -> dict:
    company_path = best_intro_path(company.intro_paths, "company_account")
    person_path = best_intro_path(company.intro_paths, "operator_person")
    confirmed_operator = next((o for o in company.operators if o.identity_confirmed), None)

    if person_path and person_path.graph_reachable and confirmed_operator:
        level, label = 3, "Reachable to a named operator"
    elif company_path and company_path.graph_reachable:
        level, label = 2, "Reachable to the company account"
    elif company_path or person_path:
        level, label = 1, "Path exists, not confirmed reachable"
    else:
        level, label = 0, "No known path"

    return {
        "level": level,
        "label": label,
        "best_company_path": company_path,
        "best_person_path": person_path,
        "confirmed_operator": confirmed_operator,
    }


SPEND_WEIGHT = {"L3": 3, "L2": 2, "L1": 1, "legacy_unmapped": 0}


def opportunity_priority(company: Company) -> dict:
    """Bucketed, explainable priority -- reachability is weighted highest,
    per spec. A company can have great spend evidence and still land in
    "Watchlist" if there's no path to anyone there; it never auto-ranks top
    on budget size alone."""
    reach = reachability_summary(company)
    spend_weight = SPEND_WEIGHT.get(company.spend_evidence_level, 0)
    has_action = len(company.action_items) > 0
    sponsor_count = len(company.sponsorships)

    reasons = []
    if reach["level"] >= 2:
        reasons.append(reach["label"])
    elif reach["level"] == 1:
        reasons.append("only an unconfirmed/weak path so far")
    else:
        reasons.append("no known path to anyone at this company yet")

    if spend_weight >= 2:
        reasons.append(f"spend evidence {company.spend_evidence_level}")
    elif company.priority_tier in ("A", "B"):
        reasons.append(f"legacy priority tier {company.priority_tier}")

    if sponsor_count:
        reasons.append(f"{sponsor_count} creator-sponsorship observation(s) on record")

    if reach["level"] == 0:
        if spend_weight >= 2 or company.priority_tier in ("A", "B"):
            label = "Watchlist"
            reasons.insert(0, "high signal but unreachable -- watch, do not lead with this one")
        else:
            label = "Low"
    elif reach["level"] >= 2 and (spend_weight >= 1 or has_action):
        label = "High"
    elif reach["level"] >= 1:
        label = "Medium"
    else:
        label = "Low"

    return {"label": label, "reachability": reach, "reasons": reasons}


def creator_campaign_fit(creator, company: Company | None = None) -> dict:
    """Why a creator is a plausible pick for a given company's campaign --
    category/region match, class, historical sponsorship of this exact
    company or similar ones, quote availability. Explainable, not scored."""
    reasons = []
    has_quote = any(rc.is_confident for rc in creator.rate_cards)
    reasons.append("has a confirmed Mango quote on file" if has_quote else "no Mango quote yet -- would need outreach")

    if company:
        prior = [s for s in creator.sponsorships if s.company_id == company.company_id] if hasattr(creator, "sponsorships") else []
        if prior:
            paid = [s for s in prior if s.disclosure_type == "paid_sponsorship"]
            if paid:
                reasons.append(f"previously paid-sponsored by {company.name} ({len(paid)}x)")
            else:
                reasons.append(f"previously mentioned/affiliated with {company.name}")

        if creator.categories and company.category:
            creator_cats = {c.strip().lower() for c in creator.categories.split(",")}
            company_cat_words = set(company.category.lower().split())
            if creator_cats & company_cat_words or any(w in " ".join(creator_cats) for w in company_cat_words):
                reasons.append("category overlaps with company's space")

    return {"has_quote": has_quote, "reasons": reasons}


RELATIONSHIP_RANK = {
    "direct": ("Direct relationship", 4),
    "secondary": ("Introduction via one connector", 3),
    "third": ("Weak, multi-hop connection", 2),
}


def relationship_strength(path: IntroPath | None) -> dict:
    if path is None:
        return {"label": "Cold contact only", "level": 0, "reason": "No observed path"}
    if not path.graph_reachable:
        return {"label": "Weak / unconfirmed", "level": 1, "reason": path.human_intro_status or "path not confirmed reachable"}
    label, level = RELATIONSHIP_RANK.get(path.degree_label, ("Weak public connection", 1))
    reason = path.path_labels or path.human_intro_status or ""
    return {"label": label, "level": level, "reason": reason}
