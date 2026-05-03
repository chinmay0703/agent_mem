"""Runtime configuration overlay.

The project ships with sane defaults from `.env`, but in practice every
deploy needs different OpenAI / Postgres / Neo4j credentials. Rather than
forcing the operator to edit `.env` and restart the container, the setup
wizard writes the user-supplied values into `data/runtime-config.json`.

Precedence: runtime-config.json > .env > Settings defaults.

The file is gitignored — it contains secrets (API key, DB passwords).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# Anchor to backend/data so the file lives next to other persisted state.
# Honor a DATA_DIR env override (used on read-only-fs hosts like Vercel,
# which set DATA_DIR=/tmp/chatmem-data) so the writer points somewhere
# writable. Same env var consumed by Settings.DATA_DIR.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else _BACKEND_ROOT / "data"
_CONFIG_PATH = _DATA_DIR / "runtime-config.json"

# Only these keys are accepted from the wizard — anything else in the JSON
# is ignored, so a tampered file can't smuggle in unrelated settings.
_ALLOWED_KEYS = {
    "OPENAI_API_KEY",
    "MODEL_NAME",
    "EMBEDDING_MODEL",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
    "PG_HOST",
    "PG_PORT",
    "PG_DATABASE",
    "PG_USER",
    "PG_PASSWORD",
}

# Subset that determines whether the app is "configured enough to run".
_REQUIRED_KEYS = {
    "OPENAI_API_KEY",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "PG_HOST",
    "PG_PORT",
    "PG_DATABASE",
    "PG_USER",
    "PG_PASSWORD",
}

_lock = threading.Lock()


def config_path() -> Path:
    return _CONFIG_PATH


def load_runtime_config() -> dict[str, Any]:
    """Return the saved overrides, or {} if no file yet."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in _ALLOWED_KEYS and v is not None and v != ""}


def save_runtime_config(values: dict[str, Any]) -> None:
    """Atomically write the overrides to disk. Ignores keys outside the
    allow-list so callers can't sneak in extra settings."""
    cleaned = {k: v for k, v in values.items() if k in _ALLOWED_KEYS}
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _CONFIG_PATH.with_suffix(".json.tmp")
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2)
        os.replace(tmp, _CONFIG_PATH)
        # Tighten perms — best effort; ignored on filesystems that don't
        # support unix modes (e.g. some bind-mounts on Docker for Windows).
        try:
            os.chmod(_CONFIG_PATH, 0o600)
        except OSError:
            pass


def clear_runtime_config() -> bool:
    """Delete the saved overrides file so the next get_settings() falls back
    to env defaults (typically empty in a wizard-driven deploy → wizard
    re-appears). Returns True if a file was deleted."""
    with _lock:
        try:
            _CONFIG_PATH.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False


def is_configured() -> bool:
    """True if every required key has a non-empty value, sourced from
    runtime config OR env. Used by the frontend to decide whether to show
    the wizard."""
    cfg = load_runtime_config()
    for k in _REQUIRED_KEYS:
        if cfg.get(k):
            continue
        if os.environ.get(k):
            continue
        return False
    return True


def configured_sections() -> dict[str, bool]:
    """Per-section status flag for the wizard's progress indicator."""
    cfg = load_runtime_config()

    def _has(k: str) -> bool:
        return bool(cfg.get(k) or os.environ.get(k))

    return {
        "openai": _has("OPENAI_API_KEY"),
        "postgres": all(_has(k) for k in ("PG_HOST", "PG_PORT", "PG_DATABASE", "PG_USER", "PG_PASSWORD")),
        "neo4j": all(_has(k) for k in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")),
    }
