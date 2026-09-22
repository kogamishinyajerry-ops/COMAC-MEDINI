"""Evidence bundle writer: run_<id>/ directory per contract §7.

manifest.json records task, input/config hashes, engine versions, artifact
hashes, status, timings, identity placeholders and open limitations.
Original inputs are copied unmodified; reports never overwrite them.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import CONTRACT_VERSION, ENGINE_NAME, __version__


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidenceBundle:
    def __init__(self, root: str | Path, run_id: str) -> None:
        self.run_id = run_id
        self.base = Path(root) / f"run_{run_id}"
        self.inputs = self.base / "inputs"
        self.results = self.base / "results"
        self.reports = self.base / "reports"
        self.execution = self.base / "execution"
        for d in (self.inputs, self.results, self.reports, self.execution):
            d.mkdir(parents=True, exist_ok=True)

    def copy_input(self, src: str | Path, name: str) -> str:
        dst = self.inputs / name
        shutil.copyfile(src, dst)
        return _sha256_file(dst)

    def write_json(self, subdir: Path, name: str, payload: dict) -> str:
        target = subdir / name
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return _sha256_file(target)

    def write_text(self, subdir: Path, name: str, text: str) -> str:
        target = subdir / name
        target.write_text(text, encoding="utf-8")
        return _sha256_file(target)

    def finalize(self, *, model_file: str, semantic_hash: str, status: str, artifacts: dict, notes: list[str], started: float) -> dict:
        manifest = {
            "manifest_version": "0.1.0",
            "run_id": self.run_id,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "engine": ENGINE_NAME,
            "engine_version": __version__,
            "contract_version": CONTRACT_VERSION,
            "status": status,  # succeeded | failed | unsupported | resource_limited
            "model_file": model_file,
            "semantic_model_hash": semantic_hash,
            "artifacts": artifacts,  # name -> sha256
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "identity": "local-cli (no approval authority)",
            "notes": notes,
        }
        self.write_json(self.base, "manifest.json", manifest)
        return manifest
