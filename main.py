"""Vercel entrypoint for the existing FastAPI application."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from f1_pitwall.main import app  # noqa: E402

__all__ = ["app"]
