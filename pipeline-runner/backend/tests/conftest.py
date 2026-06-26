"""Make `app` importable and force SQLite for tests (no DATABASE_URL)."""
import os
import sys
import tempfile

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("PIPELINE_DB", tempfile.mktemp(suffix=".db"))
