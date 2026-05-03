from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Optional at process start so the app can boot into the setup wizard
    # before the operator has supplied a key. Code paths that actually need
    # the key (LLM client init) raise their own clear error if it's empty.
    OPENAI_API_KEY: str = ""
    # Main response model (reasoning). Override via env if needed.
    MODEL_NAME: str = "gpt-5.2"
    # Maximum tokens the model may emit per response. Reasoning models bill
    # both reasoning + output here; 16K leaves plenty of headroom for long
    # summaries and detailed file analyses without truncation.
    MAX_OUTPUT_TOKENS: int = 16000
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536

    NEO4J_URI: str = "neo4j://127.0.0.1:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"
    NEO4J_DATABASE: str = "neo4j"

    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    PG_DATABASE: str = "chatbot_kg"
    PG_USER: str = "postgres"
    PG_PASSWORD: str = "postgres"

    DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"

    SUMMARIZE_EVERY_N: int = 6
    SHORT_TERM_TURNS: int = 8
    TOP_K_VECTOR: int = 5
    # Cap on graph facts injected into the response prompt. Must be high
    # enough to include 2-hop entity-to-entity facts (e.g. Arjun IS_A doctor)
    # alongside the 1-hop User-anchored facts; otherwise the bot can answer
    # "where does my brother work?" but not "what is his profession?".
    GRAPH_RETRIEVAL_LIMIT: int = 80

    DECAY_HALF_LIFE_DAYS: float = 30.0
    MIN_CONFIDENCE: float = 0.3

    # ── Production hardening ─────────────────────────────────────────────
    # Comma-separated list of allowed origins for CORS. Use "*" for dev only.
    CORS_ORIGINS: str = "*"
    # Optional bearer token. If unset, API is open (dev). If set, all
    # /chat, /threads, /memory routes require Authorization: Bearer <token>.
    API_KEY: str = ""
    # Per-user rate limit (chat requests per minute). 0 disables.
    RATE_LIMIT_PER_MIN: int = 60
    # Hard caps on user inputs.
    MAX_MESSAGE_CHARS: int = 4000
    MAX_USER_ID_LEN: int = 64
    MAX_ENTITY_NAME_CHARS: int = 200
    # Hang-budget for any single LLM call.
    # Reasoning models can take longer than chat completions. 90s gives
    # gpt-5.x room to reason on complex prompts without timing out the
    # request, while still preventing a hung connection from sitting on a
    # per-thread lock indefinitely.
    LLM_TIMEOUT_S: float = 90.0
    # Tool calling
    MAX_TOOL_ITERATIONS: int = 6
    # Compaction — total prompt token budget (input only). Tuned for gpt-4o
    # with file-content tool returns of up to 30 KB.
    MAX_PROMPT_TOKENS: int = 24000


@lru_cache
def get_settings() -> Settings:
    # Runtime-config (written by the setup wizard) takes precedence over
    # .env defaults — Settings(**overrides) lets pydantic-settings resolve
    # env first, then the explicit kwargs override on top.
    from app.services.runtime_config import load_runtime_config

    overrides = load_runtime_config()
    s = Settings(**overrides)
    s.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return s


def reload_settings() -> Settings:
    """Drop the cached Settings so the next get_settings() picks up freshly
    written runtime-config values. Called by the /setup/save endpoint."""
    get_settings.cache_clear()
    return get_settings()
