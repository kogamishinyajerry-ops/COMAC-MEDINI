"""Constant-failure-rate model → fixed mission probability.

Gate condition (contract, B_核心规划 §05): the conversion
    q = 1 - exp(-lambda * t) = -expm1(-lambda * t)
is applied ONLY when the event is declared non-repairable, the units are
explicit, and the mission time is explicit. Missing units, missing time,
repairable/dormant/inspection semantics are REJECTED — never silently
converted and never treated as a per-flight-hour metric.

Numerics: lambda and t are exact rationals (decimal strings). q is
transcendental, so it is evaluated with a configurable number of
significant decimal digits and then stored as an exact rational. The
kernel downstream is exact over that rational; the rounding happens once,
here, and the digit count is recorded in every run.

Stability:
  * x = lambda*t == 0            -> q = 0 exactly
  * x <= 0.5                     -> alternating Taylor series of 1-exp(-x),
                                    no cancellation for small x
  * 0.5 < x and 10^x > 10^prec   -> q rounds to exactly 1
  * otherwise                    -> 1 - Decimal.exp(-x)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction

from . import errors
from .errors import ModelError
from .ratnum import exact_decimal_or_fraction

DEFAULT_PRECISION_DIGITS = 40

RATE_MODEL_CONSTANT = "constant_failure_rate"

LEVEL_UNITS = frozenset({"1/h", "/h", "per_hour", "h^-1", "1/hour"})
TIME_UNITS = frozenset({"h", "hour", "hours"})

FORMULA = "q = -expm1(-lambda*t)"

# Emitted on every rate-derived evidence record: q is a mission-time
# failure probability, NOT a per-flight-hour rate metric.
INTERPRETATION = "mission_time_failure_probability_not_per_flight_hour_metric"

_LOG10_E = Decimal(
    "0.4342944819032518276511289189166050822943970058036665661144537831658646492088"
)


@dataclass(frozen=True)
class RateSpec:
    """Declared constant failure rate and mission duration for one event."""

    lambda_per_hour: Fraction
    mission_time_hours: Fraction
    lambda_unit: str
    mission_time_unit: str
    repairable: bool
    source_lambda: str  # original lexical lambda, kept for evidence
    source_time: str
    model: str = RATE_MODEL_CONSTANT

    @property
    def mean_failures(self) -> Fraction:
        """Expected number of failures over the mission, lambda*t (exact)."""
        return self.lambda_per_hour * self.mission_time_hours


def parse_rate_spec(raw: dict, event_id: str) -> RateSpec:
    """Validate a failure_rate block; reject anything outside the gate."""
    if not isinstance(raw, dict):
        raise ModelError(errors.RATE_VALUE, f"event {event_id}: failure_rate must be an object")

    model = raw.get("model", RATE_MODEL_CONSTANT)
    if model != RATE_MODEL_CONSTANT:
        raise ModelError(
            errors.RATE_UNSUPPORTED,
            f"event {event_id}: rate model {model!r} is not supported (only {RATE_MODEL_CONSTANT})",
        )

    if raw.get("repairable") is not False:
        raise ModelError(
            errors.RATE_UNSUPPORTED,
            f"event {event_id}: repairable must be explicitly false; repair semantics are out of scope",
        )
    for forbidden in ("dormant", "latent", "inspection_interval", "repair_rate", "restoration"):
        if forbidden in raw:
            raise ModelError(
                errors.RATE_UNSUPPORTED,
                f"event {event_id}: dormant/inspection/repair semantics ({forbidden}) are out of scope",
            )

    lam = _parse_decimal(raw.get("lambda"), event_id, "lambda")
    t = _parse_decimal(raw.get("mission_time"), event_id, "mission_time")
    if lam < 0:
        raise ModelError(errors.RATE_VALUE, f"event {event_id}: lambda must be >= 0")
    if t < 0:
        raise ModelError(errors.RATE_VALUE, f"event {event_id}: mission_time must be >= 0")

    lambda_unit = raw.get("lambda_unit")
    time_unit = raw.get("mission_time_unit")
    if not isinstance(lambda_unit, str) or lambda_unit not in LEVEL_UNITS:
        raise ModelError(
            errors.RATE_UNITS,
            f"event {event_id}: lambda_unit must be one of {sorted(LEVEL_UNITS)}",
        )
    if not isinstance(time_unit, str) or time_unit not in TIME_UNITS:
        raise ModelError(
            errors.RATE_UNITS,
            f"event {event_id}: mission_time_unit must be one of {sorted(TIME_UNITS)}",
        )
    if not raw.get("source"):
        raise ModelError(errors.SOURCE, f"event {event_id}: failure_rate requires a source")

    return RateSpec(
        lambda_per_hour=lam,
        mission_time_hours=t,
        lambda_unit=lambda_unit,
        mission_time_unit=time_unit,
        repairable=False,
        source_lambda=raw["lambda"],
        source_time=raw["mission_time"],
    )


def _parse_decimal(text, event_id: str, field: str) -> Fraction:
    """Parse a non-negative decimal string (plain or scientific notation)."""
    if not isinstance(text, str) or not text.strip():
        raise ModelError(errors.RATE_VALUE, f"event {event_id}: {field} must be a decimal string")
    try:
        value = Fraction(text.strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise ModelError(errors.RATE_VALUE, f"event {event_id}: {field} is not a valid decimal: {text!r}") from exc
    return value


def mission_probability(spec: RateSpec, precision_digits: int = DEFAULT_PRECISION_DIGITS) -> Fraction:
    """Evaluate q = 1 - exp(-lambda*t) with `precision_digits` significant digits.

    Returns an exact Fraction: the decimal rounding of the transcendental
    value. The rounding is deliberate and recorded; the kernel is exact
    from here on.
    """
    if precision_digits < 15:
        raise ValueError("precision_digits must be >= 15")
    x = spec.mean_failures
    if x == 0:
        return Fraction(0)
    with localcontext() as ctx:
        extra = 15
        ctx.prec = precision_digits + extra
        ctx.rounding = ROUND_HALF_EVEN
        xd = Decimal(x.numerator) / Decimal(x.denominator)

        # exp(-x) below the working precision -> q rounds to exactly 1
        if xd * _LOG10_E > Decimal(precision_digits + extra):
            return Fraction(1)

        if xd <= Decimal("0.5"):
            qd = _q_taylor(xd, precision_digits + extra)
        else:
            qd = Decimal(1) - (-xd).exp()

        # round to the requested significant digits
        ctx.prec = precision_digits
        rounded = +qd
    return Fraction(rounded)


def _q_taylor(x: Decimal, working_prec: int) -> Decimal:
    """q = x - x^2/2! + x^3/3! - ... — stable for small x, no cancellation."""
    tiny = Decimal(1).scaleb(-(working_prec + 5))
    term = x
    total = x
    k = 1
    while True:
        k += 1
        term = -term * x / k
        total += term
        if term == 0 or abs(term) < tiny:
            return total


def rate_provenance(spec: RateSpec, q: Fraction, precision_digits: int) -> dict:
    """Canonical provenance record for one rate-derived event.

    Single source of truth for the shape stored in `StaticFtaModel.rates`
    (and therefore covered by the semantic hash). The caller adds
    `event_id` / `interpretation` when emitting evidence, so those
    constant keys stay out of the hash.
    """
    return {
        "lambda": spec.source_lambda,
        "lambda_unit": spec.lambda_unit,
        "mission_time": spec.source_time,
        "mission_time_unit": spec.mission_time_unit,
        "repairable": spec.repairable,
        "lambda_t": exact_decimal_or_fraction(spec.mean_failures),
        "q": exact_decimal_or_fraction(q),
        "precision_digits": precision_digits,
        "formula": FORMULA,
    }

