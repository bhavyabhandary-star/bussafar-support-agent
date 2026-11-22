"""Vercel entry point: the BusSafar website and Sahil's chat API (see web/server.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.server import app  # noqa: E402,F401
