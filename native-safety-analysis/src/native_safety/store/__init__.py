"""Persistence adapter: one SQLite file holding baselines, runs, importance and reviews.

Layer position: sits beside `adapters/` — it depends on `domain` only, does no
fault-tree validation and no file IO, and is driven by front ends (the CLI
today; an HTTP API or UI later). See `store/schema.py` for the data rules and
`store/repository.py` for the concurrency and staleness rules.
"""
from .errors import StoreError
from .repository import IMPORTANCE_SORT_KEYS, NON_APPROVING_IDENTITIES, RecordOutcome, SqliteRepository
from .schema import STORE_SCHEMA_VERSION

__all__ = [
    "IMPORTANCE_SORT_KEYS",
    "NON_APPROVING_IDENTITIES",
    "RecordOutcome",
    "STORE_SCHEMA_VERSION",
    "SqliteRepository",
    "StoreError",
]
