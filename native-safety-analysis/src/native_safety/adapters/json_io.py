"""JSON adapter: read static FTA models; write structured results.

Parsing is defensive: malformed JSON, wrong types and non-contract fields
produce ModelError with explicit codes, never silent defaults.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..domain.errors import ModelError
from ..domain.model import StaticFtaModel
from ..domain.ratnum import exact_decimal
from ..domain.validation import validate_model


def load_model(path: str | Path) -> StaticFtaModel:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelError("IO", f"cannot read model file: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelError("PARSE", f"invalid JSON in {p.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelError("PARSE", "model root must be a JSON object")
    return validate_model(data)


def probability_to_text(fraction, max_digits: int | None = None) -> str:
    """Render result probability in contract lexical form.

    - max_digits None  -> exact decimal when it terminates within 200 digits.
    - max_digits N     -> exact decimal if it fits in N significant digits;
                          otherwise rounded HALF_EVEN to N significant digits
                          (caller must flag display rounding in warnings).
    """
    from fractions import Fraction

    assert isinstance(fraction, Fraction)
    if fraction == 0:
        return "0"
    if fraction == 1:
        return "1"

    exact = exact_decimal(fraction, max_digits=200)
    if exact is not None and (max_digits is None or _significant_digits(exact) <= max_digits):
        return exact
    if max_digits is None:
        return f"{fraction.numerator}/{fraction.denominator}"
    return _round_significant(fraction, max_digits)


def _significant_digits(text: str) -> int:
    stripped = text.lstrip("0").replace(".", "").rstrip("0")
    return len(stripped) if stripped else 1


def _round_significant(fraction, max_digits: int) -> str:
    """Round a Fraction in (0,1) to max_digits significant digits, HALF_EVEN."""
    from decimal import Decimal, localcontext

    with localcontext() as ctx:
        ctx.prec = 60
        adjusted = (Decimal(fraction.numerator) / Decimal(fraction.denominator)).adjusted()
    places = max_digits - 1 - adjusted  # digits after the decimal point
    if places < 0:
        places = 0
    scaled = fraction * (10**places)
    q, r = divmod(scaled.numerator, scaled.denominator)
    twice = 2 * r
    if twice > scaled.denominator or (twice == scaled.denominator and q % 2 == 1):
        q += 1
    text = str(q).rjust(places + 1, "0")
    if places == 0:
        return text
    return f"{text[:-places]}.{text[-places:]}"
