"""Vercel Python serverless entrypoint.

Vercel detects files under `api/` as serverless functions. This module
re-exports the existing FastAPI app so a single deploy of the repo can
serve both the React frontend (static, from `frontend/dist`) and the
Python API.

Routing (configured in vercel.json):
  /api/*          -> this function -> FastAPI under /api
  /health, etc.   -> this function (so ops probes work without /api)
  everything else -> static frontend (handled by Vercel directly)

The path layout fixup below adds backend/ to sys.path so we can import
`app.main` without any wheel/package gymnastics.
"""
import os
import sys
from pathlib import Path

# Vercel's working directory is the repo root. Add backend/ to sys.path
# so we can `from app.main import app`.
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Vercel serverless filesystem is read-only EXCEPT for /tmp. Point the
# runtime-config writer at /tmp so the wizard's save still succeeds —
# but note that anything in /tmp is ephemeral per cold-start. For real
# Vercel deploys, prefer setting credentials as project env vars in the
# Vercel dashboard so the app boots configured (no wizard needed).
os.environ.setdefault("CHATMEM_DATA_DIR", "/tmp/chatmem-data")

from app.main import app  # noqa: E402  (must come after sys.path setup)

# Vercel's Python runtime expects an ASGI / WSGI app named `app`.
__all__ = ["app"]
