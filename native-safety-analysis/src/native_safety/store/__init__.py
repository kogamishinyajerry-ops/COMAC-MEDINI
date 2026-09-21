"""Persistence adapter: one SQLite file holding baselines, runs, importance,
reviews and the FMEA base table with its traceability links.

Layer position: sits beside `adapters/` — it depends on `domain` only, does no
fault-tree validation and no file IO, and is driven by front ends (the CLI
today; an HTTP API or UI later). See `store/schema.py` for the data rules and
`store/repository.py` for the concurrency, staleness and FMEA-approval rules.
"""
from .errors import StoreError
from .repository import (
    FMEA_CANDIDATE_STATES,
    IMPORTANCE_SORT_KEYS,
    NON_APPROVING_IDENTITIES,
    RecordOutcome,
    SqliteRepository,
)
from .schema import STORE_SCHEMA_VERSION

__all__ = [
    "FMEA_CANDIDATE_STATES",
    "IMPORTANCE_SORT_KEYS",
    "NON_APPROVING_IDENTITIES",
    "RecordOutcome",
    "STORE_SCHEMA_VERSION",
    "SqliteRepository",
    "StoreError",
]
