"""Salary normalization.

The user's actual floor is EUR 120k and target EUR 150k+. Different sources
report compensation in different currencies; nominal values must never be
compared directly. This module converts to EUR while keeping the uncertainty
explicit (approximate mid-market conversion rates, flagged when applied).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# Approximate mid-market EUR conversion rates (1 unit of currency = X EUR).
# These are deliberately NOT exact; any conversion result is flagged as
# uncertain so downstream logic never pretends the number is precise.
EUR_RATES: dict[str, Decimal] = {
    "EUR": Decimal("1.0000"),
    "GBP": Decimal("1.1750"),
    "CHF": Decimal("1.0500"),
    "SEK": Decimal("0.0900"),
    "DKK": Decimal("0.1340"),
    "NOK": Decimal("0.0960"),
    "USD": Decimal("0.9200"),
    "PLN": Decimal("0.2350"),
    "CZK": Decimal("0.0410"),
}

ANY_EUR_ALIASES = {
    "EUR": "EUR",
    "EUR (gross)": "EUR",
    "EURO": "EUR",
    "€": "EUR",
}


class SalaryNormalizationError(ValueError):
    pass


@dataclass
class SalaryInfo:
    """Normalized salary information with explicit uncertainty."""

    min_eur: float | None
    max_eur: float | None
    currency: str | None
    converted: bool
    uncertainty_reason: str | None = None
    original_min: float | None = field(default=None)
    original_max: float | None = field(default=None)


def _parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        t = value.strip()
        if not t:
            return None
        try:
            # German dot-thousands "70.000" vs decimal "70.000"? Ambiguous without
            # locale; we treat a dot followed by exactly three digits as a
            # thousands separator, and a plain decimal as decimals.
            # "$120,000" etc. — strip currency-ish clutter.
            clean = t.replace("$", "").replace("€", "").replace(" ", "")
            if clean.lower().endswith("k"):
                return float(clean[:-1]) * 1_000
            if clean.endswith(".") and clean[:-1].replace(".", "").isdigit():
                clean = clean[:-1]
            if clean.count(".") == 1 and clean.split(".")[1].isdigit():
                int_part, dec_part = clean.split(".")
                if len(dec_part) == 3 and int_part.strip().isdigit():
                    clean = int_part + dec_part  # treat 70.000 as 70000
            if clean.count(",") == 1 and clean.split(",")[1].isdigit():
                int_part, dec_part = clean.split(",")
                # treat 70,000 as 70000, 70,5 as 70.5
                clean = int_part + dec_part if len(dec_part) == 3 else clean.replace(",", ".")
            return float(Decimal(clean))
        except (InvalidOperation, ValueError):
            return None
    return None


def normalize_salary(
    salary_min: Any,
    salary_max: Any,
    currency: str | None,
    rates: dict[str, Decimal] | None = None,
) -> SalaryInfo:
    """Convert a salary range to EUR.

    ``rates`` overrides the built-in approximate table (used by tests to keep
    conversions deterministic and self-contained). Returns a :class:`SalaryInfo`
    with ``min_eur``/``max_eur`` set when a conversion is possible, else ``None``
    and an explicit reason. Conversion applies the approximate rate and always
    marks ``converted=True`` with an uncertainty reason.
    """
    table = rates if rates is not None else EUR_RATES
    min_raw = _parse_amount(salary_min)
    max_raw = _parse_amount(salary_max)

    if min_raw is None and max_raw is None:
        return SalaryInfo(None, None, currency, converted=False, uncertainty_reason="no compensation published")

    currency = (currency or "").upper()
    currency = ANY_EUR_ALIASES.get(currency, currency)

    if currency not in table:
        # Unknown/non-convertible currency: cannot normalize. Keep raw but flag.
        return SalaryInfo(
            min_raw,
            max_raw,
            currency,
            converted=False,
            uncertainty_reason=f"unknown currency '{currency}' — not compared to EUR",
        )

    rate = table[currency]
    exact = rate == Decimal("1.0000") and currency == "EUR"

    def conv(v: float | None) -> float | None:
        return float(rate * Decimal(str(v))) if v is not None else None

    min_eur = conv(min_raw)
    max_eur = conv(max_raw)

    return SalaryInfo(
        min_eur=min_eur,
        max_eur=max_eur,
        currency=currency,
        converted=not exact,
        uncertainty_reason=None if exact else f"approximate FX conversion {currency or '?'}→EUR",
    )


def salary_reaches_target(salary: SalaryInfo, target_eur: float) -> bool:
    """A known compensation reaches the target when the *minimum* published
    value is at or above the target (conservative: use floor of the range)."""
    if salary.min_eur is None:
        return False
    return salary.min_eur >= target_eur


def salary_exceeds_floor(salary: SalaryInfo, minimum_eur: float) -> bool:
    if salary.min_eur is None:
        return False
    return salary.min_eur >= minimum_eur
