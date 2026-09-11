"""Budget arithmetic for a set of chosen quotes.

The hard rule from the product spec: **if the quotes cannot be summed
honestly, say 需要 Mango 确认 rather than printing a confident number.** A
total that silently mixes a package price with per-post prices, or converts an
unconfirmed quote at a stale FX rate, is worse than no total -- Mango has to
honour whatever the client is shown.

Three things block a clean total, and they are reported separately because
they need different follow-up:

* **packages** -- a "3 posts + newsletter" bundle price is not comparable with
  a single-post price, and adding them double-counts or under-counts;
* **unconfirmed quotes** -- every price imported from Mango BD carries no
  quote date or source, so it is an estimate until a human clears it;
* **mixed currencies** -- convertible for a planning figure, but the converted
  number is a reference rate, not an invoice.

None of these are errors. They are states the client is entitled to see.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import CLIENT_VISIBLE_QUOTE_STATUSES, Quote
from .quotes import FX_ASOF


@dataclass
class BudgetLine:
    """One creator's chosen deliverable inside a budget."""

    creator_id: int
    creator_name: str
    quote_id: int | None
    deliverable: str | None
    quantity: int = 1
    unit_amount: float | None = None
    unit_amount_usd: float | None = None
    currency: str = "USD"
    is_package: bool = False
    is_confirmed: bool = False
    client_visible_price: float | None = None

    @property
    def line_total_usd(self) -> float | None:
        if self.unit_amount_usd is None:
            return None
        return round(self.unit_amount_usd * max(self.quantity, 1), 2)


@dataclass
class BudgetSummary:
    """A total, plus everything that qualifies it.

    ``total_usd`` is only ever the sum of lines that could be summed. Lines
    that could not are counted in ``unpriced_lines`` -- never folded in at
    zero, which would read as "free" rather than "unknown".
    """

    lines: list[BudgetLine] = field(default_factory=list)
    total_usd: float | None = None
    #: Lines with no usable number at all.
    unpriced_lines: int = 0
    package_lines: int = 0
    unconfirmed_lines: int = 0
    currencies: dict[str, int] = field(default_factory=dict)
    fx_asof: str | None = None
    budget_usd: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def needs_mango_confirmation(self) -> bool:
        """True when the total must be presented as provisional."""
        return bool(
            self.unpriced_lines
            or self.package_lines
            or self.unconfirmed_lines
            or len(self.currencies) > 1
        )

    @property
    def is_exact(self) -> bool:
        return self.total_usd is not None and not self.needs_mango_confirmation

    @property
    def remaining_usd(self) -> float | None:
        if self.budget_usd is None or self.total_usd is None:
            return None
        return round(self.budget_usd - self.total_usd, 2)

    @property
    def over_budget(self) -> bool:
        remaining = self.remaining_usd
        return remaining is not None and remaining < 0

    def client_total_label(self) -> str:
        """The string a client surface shows instead of a bare number.

        Never renders a precise total when one cannot be honoured.
        """
        if self.total_usd is None:
            return "价格待 Mango 确认"
        if self.needs_mango_confirmation:
            return f"约 ${self.total_usd:,.0f}（需要 Mango 确认）"
        return f"${self.total_usd:,.0f}"


def line_from_quote(
    quote: Quote, creator_name: str, quantity: int = 1, *, client_view: bool = False
) -> BudgetLine:
    """Build a budget line from a stored quote.

    ``client_view`` uses only the cleared client price. It never falls back to
    ``amount_usd`` -- that is Mango's internal figure and may be a supplier
    floor.
    """
    confirmed = quote.status in CLIENT_VISIBLE_QUOTE_STATUSES and not quote.needs_review
    unit_usd = quote.client_display_price if client_view else quote.amount_usd
    return BudgetLine(
        creator_id=quote.creator_id,
        creator_name=creator_name,
        quote_id=quote.id,
        deliverable=quote.deliverable_raw,
        quantity=max(quantity, 1),
        unit_amount=quote.amount,
        unit_amount_usd=unit_usd,
        currency=quote.currency or "USD",
        is_package=bool(quote.is_package),
        is_confirmed=confirmed,
        client_visible_price=quote.client_display_price,
    )


def summarize(lines: list[BudgetLine], budget_usd: float | None = None) -> BudgetSummary:
    """Roll lines into a total plus its qualifications."""
    summary = BudgetSummary(lines=list(lines), budget_usd=budget_usd, fx_asof=FX_ASOF)

    currencies: Counter = Counter()
    summable: list[float] = []

    for line in lines:
        currencies[line.currency] += 1
        if line.is_package:
            summary.package_lines += 1
        if not line.is_confirmed:
            summary.unconfirmed_lines += 1
        total = line.line_total_usd
        if total is None:
            summary.unpriced_lines += 1
        else:
            summable.append(total)

    summary.currencies = dict(currencies)
    summary.total_usd = round(sum(summable), 2) if summable else None

    if summary.unpriced_lines:
        summary.notes.append(
            f"{summary.unpriced_lines} 项没有可用报价，未计入总价"
        )
    if summary.package_lines:
        summary.notes.append(
            f"{summary.package_lines} 项为套餐价，不能与单条价格直接相加，需要 Mango 确认"
        )
    if summary.unconfirmed_lines:
        summary.notes.append(
            f"{summary.unconfirmed_lines} 项报价未经复核，金额可能变动"
        )
    if len(currencies) > 1:
        listed = "、".join(sorted(currencies))
        summary.notes.append(f"含多种币种（{listed}），按 {FX_ASOF} 参考汇率折算，非结算价")
    if summary.over_budget:
        summary.notes.append("当前组合超出项目预算")

    return summary
