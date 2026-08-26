"""Static, manually-maintained FX reference table.

There is no live FX API in scope for this tool. Rates below are approximate
reference rates set at build time (2026-08) and are for internal budgeting
only -- not for invoicing. Update FX_TO_USD by hand if a rate drifts a lot.
"""

from __future__ import annotations

FX_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "USDT": 1.0,
    "USDC": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CNY": 0.14,
    "RMB": 0.14,
    "INR": 0.012,
    "JPY": 0.0067,
    "KRW": 0.00072,
    "SGD": 0.74,
    "HKD": 0.128,
    "AUD": 0.65,
    "CAD": 0.72,
}

CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "CNY",
    "₹": "INR",
}


def to_usd(amount: float | None, currency: str) -> tuple[float | None, float | None]:
    """Return (amount_usd, fx_rate_used). None in, None out."""
    if amount is None:
        return None, None
    currency = (currency or "USD").upper()
    rate = FX_TO_USD.get(currency)
    if rate is None:
        return None, None
    return round(amount * rate, 2), rate
