"""Repair negation-induced classifications and one old boot-script bug.

The v4 fallback classifier used to scan the *entire* research record.  It
therefore read caveats such as "no public cash pool was found", "verify the
cash campaign budget", and contributor "USDC payouts" as affirmative evidence
of an active creator/partner cash budget.  Talus, MyShell, Allora and Sapien
were consequently written as L3 even though their source records support, at
most, a formal ecosystem/contributor program whose campaign cash remains
unverified.

Talus and Allora do have public accelerator mechanisms, so L2 is the
conservative repair for those two. MyShell and Sapien have creator/contributor
product mechanics, but their imported budget evidence is only funding and the
cash/program language appears solely inside caveats; they therefore return to
L1. The older version of this script downgraded fifteen rows to L1 based only
on the short ``budget_evidence`` sentence and discarded separate, affirmative
program signals for unrelated companies. A persistent production volume may
already contain that over-correction. Eleven exact company/level/mechanism
signatures are therefore restored to L2 below; the guards prevent a later
human correction from being overwritten.

The full evidence text is preserved.  Both the evidence level and mechanism
are changed together so the row cannot say ``L2`` while retaining the
contradictory ``active_creator_or_partner_budget`` mechanism.

Idempotent (checks current level before downgrading) so it's safe on every
boot -- same pattern as fix_action_item_phrasing.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session  # noqa: E402
from backend.models import Company  # noqa: E402

# (company_id, previous level, previous mechanism, repaired level,
# repaired mechanism).  Checking both old values avoids clobbering a later
# human correction.
NEGATION_REPAIRS = [
    ("company:talusnetwork", "L3", "active_creator_or_partner_budget", "L2", "formal_paid_program"),
    ("company:myshell", "L3", "active_creator_or_partner_budget", "L1", "funding_or_token_value_only"),
    ("company:alloranetwork", "L3", "active_creator_or_partner_budget", "L2", "formal_paid_program"),
    ("company:sapien", "L3", "active_creator_or_partner_budget", "L1", "funding_or_token_value_only"),
]

# The first release of this boot script changed only spend_evidence_level for
# these rows. That left a distinctive inconsistent state: L1 while the
# separately sourced mechanism still records an affirmative formal program or
# affiliate/referral program. Restore only that exact signature.
LEGACY_OVERCORRECTION_RESTORES = [
    ("company:pinai", "L1", "formal_paid_program", "L2", "formal_paid_program"),
    ("company:olas", "L1", "formal_paid_program", "L2", "formal_paid_program"),
    ("company:giza", "L1", "formal_paid_program", "L2", "formal_paid_program"),
    ("company:inferencelabs", "L1", "formal_paid_program", "L2", "formal_paid_program"),
    ("company:kiteai", "L1", "formal_paid_program", "L2", "formal_paid_program"),
    ("company:gaib", "L1", "formal_paid_program", "L2", "formal_paid_program"),
    ("company:hyperbolic", "L1", "formal_paid_program", "L2", "formal_paid_program"),
    ("company:heurist", "L1", "formal_paid_program", "L2", "formal_paid_program"),
    ("company:fractionai", "L1", "affiliate_or_referral_only", "L2", "affiliate_or_referral_only"),
    ("company:375ai", "L1", "affiliate_or_referral_only", "L2", "affiliate_or_referral_only"),
    ("company:openledger", "L1", "affiliate_or_referral_only", "L2", "affiliate_or_referral_only"),
]

SPEND_REPAIRS = NEGATION_REPAIRS + LEGACY_OVERCORRECTION_RESTORES


def repair_spend_classifications(companies: list[Company]) -> tuple[int, int]:
    """Apply guarded repairs; return (negation_repairs, legacy_restores)."""

    by_id = {company.company_id: company for company in companies}
    negation_changed = 0
    restored = 0
    for index, (company_id, expected_level, expected_mechanism, repaired_level, repaired_mechanism) in enumerate(
        SPEND_REPAIRS
    ):
        company = by_id.get(company_id)
        if (
            company is None
            or company.spend_evidence_level != expected_level
            or company.spend_mechanism_level != expected_mechanism
        ):
            continue
        company.spend_evidence_level = repaired_level
        company.spend_mechanism_level = repaired_mechanism
        if index < len(NEGATION_REPAIRS):
            negation_changed += 1
        else:
            restored += 1
    return negation_changed, restored


def main() -> None:
    session = get_session()
    try:
        changed, restored = repair_spend_classifications(session.query(Company).all())
        session.commit()
        print(
            "[fix_fundraise_only_spend_evidence] "
            f"repaired {changed} negation-induced classifications; "
            f"restored {restored} legacy boot-script over-corrections"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
