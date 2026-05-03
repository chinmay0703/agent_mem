"""First-run setup wizard endpoints.

Goal: an operator can deploy the container with NO env vars and reach the
UI; the wizard collects OpenAI / Postgres / Neo4j credentials, validates
each connection live, optionally creates the Postgres database if it
doesn't exist, then persists everything to runtime-config.json. The
backend's cached singletons are torn down so the next request sees the
new credentials without a process restart.

Security: these endpoints are intentionally NOT behind the API_KEY guard
because the wizard runs before the operator has chosen an API key. The
expectation is that you only expose the deployed app to trusted users
during the bootstrap window — once configured, you can enable API_KEY
and the rest of the app respects it.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, HTTPException
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.config import reload_settings
from app.services.graph.neo4j_client import reset_graph_client
from app.services.llm import reset_client as reset_openai_client
from app.services.runtime_config import (
    clear_runtime_config,
    configured_sections,
    is_configured,
    load_runtime_config,
    save_runtime_config,
)
from app.services.storage import postgres as pg


router = APIRouter(prefix="/setup", tags=["setup"])


# Postgres identifier guard — used when we create a database from a
# user-supplied name. asyncpg doesn't parameterize DDL so we have to
# string-interpolate, which means a strict allow-list is mandatory.
_PG_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class PostgresCreds(BaseModel):
    host: str = Field(..., min_length=1)
    port: int = Field(..., ge=1, le=65535)
    database: str = Field(..., min_length=1, max_length=63)
    user: str = Field(..., min_length=1)
    password: str = ""
    create_if_missing: bool = True


class Neo4jCreds(BaseModel):
    uri: str = Field(..., min_length=1)
    user: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    database: str = "neo4j"


class OpenAICreds(BaseModel):
    api_key: str = Field(..., min_length=10)
    model_name: Optional[str] = None
    embedding_model: Optional[str] = None


class SaveBody(BaseModel):
    openai: OpenAICreds
    postgres: PostgresCreds
    neo4j: Neo4jCreds


@router.get("/status")
async def status() -> dict[str, Any]:
    """Whether the app is configured + which sections have values yet.
    The frontend polls this on mount to decide whether to show the wizard."""
    cfg = load_runtime_config()
    return {
        "configured": is_configured(),
        "sections": configured_sections(),
        # Echo back what's already saved so a re-visit shows the existing
        # values (passwords masked) instead of empty fields.
        "values": {
            "openai": {
                "api_key": _mask(cfg.get("OPENAI_API_KEY")),
                "model_name": cfg.get("MODEL_NAME") or "",
            },
            "postgres": {
                "host": cfg.get("PG_HOST") or "",
                "port": cfg.get("PG_PORT") or 5432,
                "database": cfg.get("PG_DATABASE") or "",
                "user": cfg.get("PG_USER") or "",
                "password": _mask(cfg.get("PG_PASSWORD")),
            },
            "neo4j": {
                "uri": cfg.get("NEO4J_URI") or "",
                "user": cfg.get("NEO4J_USER") or "",
                "password": _mask(cfg.get("NEO4J_PASSWORD")),
                "database": cfg.get("NEO4J_DATABASE") or "neo4j",
            },
        },
    }


def _mask(value: Optional[str]) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "•" * len(value)
    return value[:3] + "•" * (len(value) - 6) + value[-3:]


@router.post("/test/openai")
async def test_openai(body: OpenAICreds) -> dict[str, Any]:
    """Validate the OpenAI key by listing models — the cheapest read call."""
    client = AsyncOpenAI(api_key=body.api_key)
    try:
        await asyncio.wait_for(client.models.list(), timeout=15.0)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Timed out reaching api.openai.com (15s)."}
    except Exception as e:
        return {"ok": False, "error": _short_err(e)}
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return {"ok": True, "message": "Key accepted."}


@router.post("/test/postgres")
async def test_postgres(body: PostgresCreds) -> dict[str, Any]:
    """Try to connect to the named Postgres database. If it doesn't exist
    and create_if_missing=True, connect to the maintenance `postgres` DB
    with the same creds and CREATE DATABASE."""
    # 1. Try the target database directly.
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=body.host,
                port=body.port,
                user=body.user,
                password=body.password,
                database=body.database,
                timeout=10.0,
            ),
            timeout=12.0,
        )
        await conn.close()
        return {"ok": True, "db_existed": True, "message": f"Connected to {body.database}."}
    except asyncpg.InvalidCatalogNameError:
        # The database doesn't exist — handled below.
        pass
    except asyncpg.InvalidPasswordError:
        return {"ok": False, "error": "Password rejected."}
    except (asyncio.TimeoutError, OSError) as e:
        return {"ok": False, "error": f"Cannot reach {body.host}:{body.port} — {_short_err(e)}"}
    except Exception as e:
        return {"ok": False, "error": _short_err(e)}

    # 2. Database doesn't exist. Optionally create it.
    if not body.create_if_missing:
        return {
            "ok": False,
            "error": f"Database '{body.database}' does not exist on this server.",
        }
    if not _PG_IDENT_RE.match(body.database):
        return {
            "ok": False,
            "error": "Database name must match /^[A-Za-z_][A-Za-z0-9_]{0,62}$/ to be safely created.",
        }
    try:
        admin = await asyncio.wait_for(
            asyncpg.connect(
                host=body.host,
                port=body.port,
                user=body.user,
                password=body.password,
                database="postgres",
                timeout=10.0,
            ),
            timeout=12.0,
        )
    except Exception as e:
        return {
            "ok": False,
            "error": (
                f"Database '{body.database}' missing and could not connect to "
                f"the maintenance 'postgres' database to create it: {_short_err(e)}"
            ),
        }
    try:
        await admin.execute(f'CREATE DATABASE "{body.database}"')
    except asyncpg.InsufficientPrivilegeError:
        await admin.close()
        return {
            "ok": False,
            "error": f"User '{body.user}' lacks CREATEDB privilege.",
        }
    except Exception as e:
        await admin.close()
        return {"ok": False, "error": f"CREATE DATABASE failed: {_short_err(e)}"}
    await admin.close()
    return {
        "ok": True,
        "db_existed": False,
        "created": True,
        "message": f"Created database '{body.database}'.",
    }


@router.post("/test/neo4j")
async def test_neo4j(body: Neo4jCreds) -> dict[str, Any]:
    """Open a Neo4j driver with the supplied creds and run RETURN 1."""
    driver = None
    try:
        driver = AsyncGraphDatabase.driver(body.uri, auth=(body.user, body.password))
        async with driver.session(database=body.database) as session:
            result = await asyncio.wait_for(session.run("RETURN 1 AS ok"), timeout=10.0)
            row = await result.single()
            if not row or row.get("ok") != 1:
                return {"ok": False, "error": "Unexpected response from Neo4j."}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Timed out reaching Neo4j (10s)."}
    except Exception as e:
        return {"ok": False, "error": _short_err(e)}
    finally:
        if driver is not None:
            try:
                await driver.close()
            except Exception:
                pass
    return {"ok": True, "message": "Connected and authenticated."}


@router.post("/save")
async def save(body: SaveBody) -> dict[str, Any]:
    """Persist the wizard values, then re-init the cached settings + every
    downstream singleton so the next request sees the new credentials."""
    overrides: dict[str, Any] = {
        "OPENAI_API_KEY": body.openai.api_key,
        "PG_HOST": body.postgres.host,
        "PG_PORT": int(body.postgres.port),
        "PG_DATABASE": body.postgres.database,
        "PG_USER": body.postgres.user,
        "PG_PASSWORD": body.postgres.password,
        "NEO4J_URI": body.neo4j.uri,
        "NEO4J_USER": body.neo4j.user,
        "NEO4J_PASSWORD": body.neo4j.password,
        "NEO4J_DATABASE": body.neo4j.database,
    }
    if body.openai.model_name:
        overrides["MODEL_NAME"] = body.openai.model_name
    if body.openai.embedding_model:
        overrides["EMBEDDING_MODEL"] = body.openai.embedding_model

    # Don't overwrite a saved password with the masked echo from /status.
    existing = load_runtime_config()
    for k, v in list(overrides.items()):
        if isinstance(v, str) and "•" in v and existing.get(k):
            overrides[k] = existing[k]

    save_runtime_config(overrides)
    reload_settings()

    # Tear down cached connections so the next request rebuilds them with
    # the freshly written config.
    try:
        await pg.close_pool()
    except Exception:
        pass
    try:
        await reset_graph_client()
    except Exception:
        pass
    reset_openai_client()

    # Eagerly initialize Neo4j schema so the user's first chat doesn't pay
    # the cost (and we surface any post-save connection issue here).
    schema_ok = True
    schema_err: Optional[str] = None
    try:
        from app.services.graph.neo4j_client import get_graph_client

        await get_graph_client().init_schema()
    except Exception as e:
        schema_ok = False
        schema_err = _short_err(e)

    return {
        "ok": True,
        "configured": is_configured(),
        "schema_initialized": schema_ok,
        "schema_error": schema_err,
    }


@router.delete("/config")
async def reset_config() -> dict[str, Any]:
    """Wipe the saved runtime-config so the wizard reappears on next load.
    Tears down every cached connection so the next request rebuilds them
    against env defaults (or, more commonly, errors out cleanly until the
    wizard saves new credentials).

    NOTE: this does NOT drop your Postgres data or Neo4j graph — only the
    locally-stored credentials. Re-enter the same creds in the wizard to
    pick up exactly where you left off.
    """
    deleted = clear_runtime_config()
    reload_settings()
    try:
        await pg.close_pool()
    except Exception:
        pass
    try:
        await reset_graph_client()
    except Exception:
        pass
    reset_openai_client()
    return {
        "ok": True,
        "deleted": deleted,
        "configured": is_configured(),
    }


def _short_err(e: BaseException) -> str:
    """Single-line, prompt-safe rendering of an exception for the wizard."""
    msg = str(e) or e.__class__.__name__
    return " ".join(msg.split())[:240]
