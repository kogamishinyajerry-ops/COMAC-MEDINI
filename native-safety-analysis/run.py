#!/usr/bin/env python3
"""Convenience wrapper: run the CLI without installation.

Usage: python run.py <command> [args...]
(e.g. python run.py analyze model.json --evidence-dir evidence)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from native_safety.cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
