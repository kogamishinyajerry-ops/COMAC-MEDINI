"""Domain errors.

Error codes are part of the public contract (see reference/03_contracts).
Distinct codes map to distinct CLI exit statuses; nothing silently degrades.
"""
from __future__ import annotations


class ModelError(ValueError):
    """Structural or semantic violation of the static FTA contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ResourceLimitError(RuntimeError):
    """Deterministic resource ceiling exceeded — never truncated-as-success."""

    def __init__(self, message: str, node_count: int | None = None) -> None:
        self.node_count = node_count
        super().__init__(message)


# Stable error codes (contract-level, mirrored in docs/SEMANTICS.md)
VERSION = "VERSION"
ASSUMPTIONS = "ASSUMPTIONS"
ID = "ID"
DUPLICATE_ID = "DUPLICATE_ID"
PROBABILITY = "PROBABILITY"
SOURCE = "SOURCE"
UNSUPPORTED_GATE = "UNSUPPORTED_GATE"
INPUTS = "INPUTS"
K_OF_N = "K_OF_N"
UNKNOWN_REFERENCE = "UNKNOWN_REFERENCE"
CYCLE = "CYCLE"
TOP_EVENT = "TOP_EVENT"
SIZE_LIMIT = "SIZE_LIMIT"
# Failure-rate model (constant_failure_rate → fixed mission probability)
RATE_VALUE = "RATE_VALUE"
RATE_UNITS = "RATE_UNITS"
RATE_UNSUPPORTED = "RATE_UNSUPPORTED"
# FMEA base table + traceability. Distinct codes so a rejected FMEA row is
# never mistaken for a rejected fault tree.
FMEA_INPUTS = "FMEA_INPUTS"        # malformed row structure or empty required text
FMEA_ID = "FMEA_ID"                # bad stable identifier (row/component/function/requirement)
FMEA_SOURCE = "FMEA_SOURCE"        # unknown provenance, or an inference row without its note
FMEA_LINK = "FMEA_LINK"            # a linked event id is unknown / not in the referenced baseline
# Object-level patch language (model-patch-v1). A rejected patch is invalid
# input against the CURRENT baseline's canonical form.
PATCH_OP = "PATCH_OP"
