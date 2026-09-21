"""Store-layer error codes.

Storage conflicts are not domain semantics (they do not describe the fault
tree), so they live here rather than in `domain.errors`. They are still part
of the CLI contract: each code maps to a stable exit status and is never
reported as a computation failure.
"""
from __future__ import annotations


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# The database was written by an incompatible engine/schema generation.
SCHEMA_MISMATCH = "STORE_SCHEMA_MISMATCH"
# Same run_id already exists with different content: refuse, never overwrite.
RUN_ID_CONFLICT = "RUN_ID_CONFLICT"
# The model_id exists but its current baseline is a different hash. A model
# change must go through propose -> decide -> apply, not through a re-run.
BASELINE_NOT_CURRENT = "BASELINE_NOT_CURRENT"
# Optimistic concurrency: the caller's expected baseline hash is stale.
BASELINE_CONFLICT = "BASELINE_CONFLICT"
REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"
RUN_NOT_FOUND = "RUN_NOT_FOUND"
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
REVIEW_STATE = "REVIEW_STATE"
# FMEA base table (v2): rows, candidates and their revision ledger.
FMEA_NOT_FOUND = "FMEA_NOT_FOUND"
FMEA_STATE = "FMEA_STATE"
# The row moved on since the proposal: the revision it targets is no longer the
# current version. Mirrors BASELINE_CONFLICT for the FMEA side.
FMEA_REVISION_CONFLICT = "FMEA_REVISION_CONFLICT"
# The draft is still a machine-generated attention placeholder: it names an
# event and the exact importance that flagged it, but its failure mode / cause /
# effect are unfilled. Promoting it would record an inference as a settled fact
# (§110), so the apply is refused until a human writes the real content.
FMEA_PLACEHOLDER = "FMEA_PLACEHOLDER"
# A store subcommand argument is out of range or unknown.
BAD_ARGUMENT = "STORE_BAD_ARGUMENT"
# The declared actor may not approve (agents and the local CLI placeholder
# have no approval authority — B_核心规划 §181).
APPROVAL_AUTHORITY = "APPROVAL_AUTHORITY"
# Integrity verification of the stored database failed.
INTEGRITY = "INTEGRITY"

# Codes the CLI reports as "unsupported semantics" (exit 3) rather than
# "invalid input" (exit 2). An engine/DB generation mismatch is an
# environment capability problem, not a bad request.
UNSUPPORTED_CODES = frozenset({SCHEMA_MISMATCH})
