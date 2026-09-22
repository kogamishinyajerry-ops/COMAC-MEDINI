"""Exact rational -> decimal text rendering (single canonical implementation).

A Fraction whose denominator is 2^a·5^b has a terminating decimal
representation; this module finds the shortest one, bounded by a digit
budget. Every domain/internal caller uses this helper so that the lexical
form of a probability is defined in exactly one place.

Deliberately NOT used by the independent verification oracle
(`verification/run_rate_cross_check.py::_decimal`), which must stay
code-independent from production.
"""
from __future__ import annotations

from fractions import Fraction

DEFAULT_MAX_DECIMAL_DIGITS = 200


def exact_decimal(value: Fraction, max_digits: int = DEFAULT_MAX_DECIMAL_DIGITS) -> str | None:
    """Shortest exact decimal text for `value`, or None if it needs more than
    `max_digits` fractional digits (i.e. non-terminating within budget)."""
    if value.denominator == 1:
        return str(value.numerator)
    for digits in range(1, max_digits + 1):
        scaled = value * (10**digits)
        if scaled.denominator == 1:
            text = str(scaled.numerator).rjust(digits + 1, "0")
            return f"{text[:-digits]}.{text[-digits:]}"
    return None


def exact_decimal_or_fraction(value: Fraction, max_digits: int = DEFAULT_MAX_DECIMAL_DIGITS) -> str:
    """Exact decimal when it terminates within budget, else `numerator/denominator`."""
    return exact_decimal(value, max_digits) or f"{value.numerator}/{value.denominator}"
