"""FastAPI entrypoint for the memory-augmented chatbot."""
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.routes.audit import router as audit_router
from app.routes.chat import router as chat_router
from app.routes.files import router as files_router
from app.routes.memory import router as memory_router
from app.routes.setup import router as setup_router
from app.routes.threads import router as threads_router
from app.routes.users import router as users_router
from app.services.graph.neo4j_client import get_graph_client
from app.services.storage import postgres as pg


# Structured (single-line JSON) logger so prod deployments can ship to a
# log aggregator without a parser.
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in ("request_id", "user_id", "path", "method", "status", "latency_ms"):
            v = getattr(record, k, None)
            if v is not None:
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _setup_logging() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


_setup_logging()
log = logging.getLogger("chatmem")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot soft: never crash on missing credentials. The frontend's first
    request hits /setup/status, sees `configured: false`, and renders the
    wizard. The wizard's /setup/save call wires everything up at runtime."""
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        log.info("OPENAI_API_KEY not set — booting into setup-wizard mode.")
    else:
        try:
            await get_graph_client().init_schema()
        except Exception as e:
            log.warning("Neo4j init deferred: %s", e)
        try:
            await pg.get_pool()
        except Exception as e:
            log.warning("Postgres init deferred: %s", e)
    yield
    # Best-effort teardown — singletons may not have been built if the
    # wizard never ran.
    try:
        from app.services.graph.neo4j_client import _singleton as _g

        if _g is not None:
            await _g.close()
    except Exception:
        pass
    try:
        await pg.close_pool()
    except Exception:
        pass


app = FastAPI(title="Agent Mem", version="1.0.0", lifespan=lifespan)


# ── CORS (env-driven) ────────────────────────────────────────────────────
_origins = [o.strip() for o in get_settings().CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# ── Request ID + access log ──────────────────────────────────────────────
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = rid
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        latency_ms = int((time.perf_counter() - start) * 1000)
        log.exception(
            "request failed",
            extra={
                "request_id": rid,
                "path": request.url.path,
                "method": request.method,
                "latency_ms": latency_ms,
            },
        )
        raise
    latency_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Request-ID"] = rid
    log.info(
        "%s %s -> %s",
        request.method,
        request.url.path,
        response.status_code,
        extra={
            "request_id": rid,
            "path": request.url.path,
            "method": request.method,
            "status": response.status_code,
            "latency_ms": latency_ms,
        },
    )
    return response


# ── Request size limit (defense in depth — covers any route) ─────────────
@app.middleware("http")
async def cap_request_size(request: Request, call_next):
    # File uploads have their own 25 MB cap inside the route — skip the
    # global 1 MB cap for the /files namespace so multipart bodies pass.
    if request.url.path.startswith("/files"):
        return await call_next(request)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > 1_000_000:  # 1 MB
        return JSONResponse({"detail": "payload too large"}, status_code=413)
    return await call_next(request)


# ── Standard error envelopes ─────────────────────────────────────────────
@app.exception_handler(StarletteHTTPException)
async def http_exc_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        {"error": {"status": exc.status_code, "detail": exc.detail}},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        {"error": {"status": 422, "detail": "validation_error", "errors": exc.errors()}},
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    log.exception("unhandled error: %s", exc)
    return JSONResponse(
        {"error": {"status": 500, "detail": "internal server error"}},
        status_code=500,
    )


# ── Routers ──────────────────────────────────────────────────────────────
# Every API route lives under /api so the frontend can be served from the
# same origin (FastAPI handles /api/*, the SPA owns everything else). This
# means a single deploy step — uvicorn — serves both the API and the
# built frontend, no separate web tier needed.
API_PREFIX = "/api"
app.include_router(setup_router, prefix=API_PREFIX)
app.include_router(chat_router, prefix=API_PREFIX)
app.include_router(memory_router, prefix=API_PREFIX)
app.include_router(threads_router, prefix=API_PREFIX)
app.include_router(users_router, prefix=API_PREFIX)
app.include_router(files_router, prefix=API_PREFIX)
app.include_router(audit_router, prefix=API_PREFIX)


# ── Health ───────────────────────────────────────────────────────────────
# Health probes stay at the root so ops tooling (k8s, load balancers,
# uptime monitors) can hit `/health` without an `/api` prefix.
@app.get("/health")
@app.get("/api/health")
async def health() -> dict:
    """Liveness — process is up. Always cheap; safe for kube liveness."""
    return {"status": "ok"}


@app.get("/health/ready")
@app.get("/api/health/ready")
async def ready() -> dict:
    """Readiness — checks downstream dependencies. Kube readiness probe."""
    deps: dict[str, str] = {}
    overall_ok = True

    try:
        pool = await pg.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        deps["postgres"] = "ok"
    except Exception as e:
        deps["postgres"] = f"down: {e}"
        overall_ok = False

    try:
        async with get_graph_client()._driver.session() as s:
            await s.run("RETURN 1")
        deps["neo4j"] = "ok"
    except Exception as e:
        deps["neo4j"] = f"down: {e}"
        overall_ok = False

    status_code = 200 if overall_ok else 503
    return JSONResponse({"status": "ok" if overall_ok else "degraded", "deps": deps}, status_code=status_code)


# ── Frontend (single-deploy) ─────────────────────────────────────────────
# When the React app has been built with `npm run build`, the static
# bundle is served from this same process. /api/* routes (registered
# above) take priority; everything else falls through to index.html so
# the SPA owns its own routing.
from pathlib import Path as _Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_FRONTEND_DIST = _Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    _ASSETS_DIR = _FRONTEND_DIST / "assets"
    if _ASSETS_DIR.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(_ASSETS_DIR)),
            name="assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Hard 404 for unknown /api/* so the SPA never accidentally
        # masks a typo'd backend route.
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse(
                {"error": {"status": 404, "detail": "not found"}},
                status_code=404,
            )
        target = _FRONTEND_DIST / full_path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(_FRONTEND_DIST / "index.html")
else:
    log.info(
        "frontend/dist not found — running API-only. Build the frontend "
        "with `cd frontend && npm run build` to enable single-deploy."
    )
