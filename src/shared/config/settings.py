import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="allow")
    PROJECT_NAME: str = "AISEP - AI Evaluation"
    API_V1_STR: str = "/api/v1"

    # DB
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./aisep_ai.db")

    # LLM
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    # Vertex AI (replaces GEMINI_API_KEY). Auth via service-account JSON pointed
    # to by GOOGLE_APPLICATION_CREDENTIALS (handled automatically by the SDK).
    GOOGLE_CLOUD_PROJECT: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    GOOGLE_CLOUD_LOCATION: str = os.getenv(
        "GOOGLE_CLOUD_LOCATION", "us-central1")
    DEFAULT_LLM_PROVIDER: str = "openai"  # or gemini
    DEFAULT_MODEL_NAME: str = "gpt-4o-mini"
    ENABLE_PSEUDO_OCR_FALLBACK: bool = os.getenv(
        "ENABLE_PSEUDO_OCR_FALLBACK", "true").lower() == "true"

    # Paths
    STORAGE_DIR: str = os.path.join(os.getcwd(), "storage")
    ARTIFACTS_DIR: str = os.path.join(STORAGE_DIR, "artifacts")
    TAVILY_API_KEY: str = os.getenv(
        "TAVILY_API_KEY", os.getenv("TAILY_API_KEY", ""))
    AISEP_INTERNAL_TOKEN: str = os.getenv("AISEP_INTERNAL_TOKEN", "")
    REQUIRE_INTERNAL_AUTH: bool = os.getenv(
        "REQUIRE_INTERNAL_AUTH", "false").lower() == "true"

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    # Defaults to <STORAGE_DIR>/logs to preserve legacy behavior; override
    # with LOG_DIR env when logs live on a dedicated volume (production).
    LOG_DIR: str = os.getenv(
        "LOG_DIR", os.path.join(os.getenv("STORAGE_DIR", os.path.join(os.getcwd(), "storage")), "logs"))

    # Celery / Redis
    CELERY_BROKER_URL: str = os.getenv(
        "CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND: str = os.getenv(
        "CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    # Investor-Agent Checkpoint
    # "redis" for durable shared checkpoint; "memory" for in-process (local dev)
    CHECKPOINT_BACKEND: str = os.getenv("CHECKPOINT_BACKEND", "memory")
    CHECKPOINT_REDIS_URL: str = os.getenv(
        "CHECKPOINT_REDIS_URL", "redis://localhost:6379/2")
    # Minutes before idle conversation checkpoint expires (0 = no expiry)
    CHECKPOINT_TTL_MINUTES: int = int(
        os.getenv("CHECKPOINT_TTL_MINUTES", "1440"))

    # Recommendation Storage Backend
    # "db" for durable DB-backed storage (default / production)
    # "filesystem" for legacy JSON files under storage/recommendations
    RECOMMENDATION_BACKEND: str = os.getenv("RECOMMENDATION_BACKEND", "db")

    # ── Webhook / Callback ─────────────────────────────────────────
    # URL to POST terminal evaluation events to (empty = disabled)
    WEBHOOK_CALLBACK_URL: str = os.getenv("WEBHOOK_CALLBACK_URL", "")
    # Shared secret for HMAC-SHA256 signing (empty = unsigned)
    WEBHOOK_SIGNING_SECRET: str = os.getenv("WEBHOOK_SIGNING_SECRET", "")
    # Max delivery attempts per callback (exponential back-off between)
    WEBHOOK_MAX_RETRIES: int = int(os.getenv("WEBHOOK_MAX_RETRIES", "3"))
    # Verify TLS certificate when posting webhook (set false for dev self-signed certs)
    WEBHOOK_VERIFY_SSL: bool = os.getenv(
        "WEBHOOK_VERIFY_SSL", "true").lower() == "true"

    # ── Feature Flags ──────────────────────────────────────────────
    BUSINESS_PLAN_EVAL_ENABLED: bool = os.getenv(
        "BUSINESS_PLAN_EVAL_ENABLED", "true").lower() == "true"
    MERGE_EVAL_ENABLED: bool = os.getenv(
        "MERGE_EVAL_ENABLED", "true").lower() == "true"

    # ── OpenTelemetry Tracing ──────────────────────────────────────
    OTEL_ENABLED: str = os.getenv("OTEL_ENABLED", "false")
    OTEL_SERVICE_NAME: str = os.getenv("OTEL_SERVICE_NAME", "aisep-ai")
    OTEL_EXPORTER_OTLP_ENDPOINT: str = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    # ── Rate Limiting ──────────────────────────────────────────────
    RATE_LIMIT_ENABLED: str = os.getenv("RATE_LIMIT_ENABLED", "true")
    RATE_LIMIT_EVAL_RPM: int = int(os.getenv("RATE_LIMIT_EVAL_RPM", "20"))
    RATE_LIMIT_CHAT_RPM: int = int(os.getenv("RATE_LIMIT_CHAT_RPM", "30"))
    RATE_LIMIT_STREAM_RPM: int = int(os.getenv("RATE_LIMIT_STREAM_RPM", "30"))
    RATE_LIMIT_RECO_RPM: int = int(os.getenv("RATE_LIMIT_RECO_RPM", "60"))

    # ── HTTPS / TLS ────────────────────────────────────────────────────────────
    # Path to TLS private key PEM file (empty = HTTP only)
    SSL_KEYFILE: str = os.getenv("SSL_KEYFILE", "")
    # Path to TLS certificate PEM file
    SSL_CERTFILE: str = os.getenv("SSL_CERTFILE", "")
    # Optional CA bundle for mutual TLS client verification (empty = disabled)
    SSL_CA_CERTS: str = os.getenv("SSL_CA_CERTS", "")
    # Port to listen on (default 8443 for HTTPS, 8000 for HTTP)
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")

    # ── Investor Agent Performance ─────────────────────────────────────────
    # "basic" is ~3-5x faster than "advanced"; use "advanced" only when answer
    # quality matters more than latency (e.g. premium tier).
    INVESTOR_AGENT_SEARCH_DEPTH: str = os.getenv(
        "INVESTOR_AGENT_SEARCH_DEPTH", "basic")
    # Max Tavily results per sub-query (floor 2, cap 5). Lower = faster.
    INVESTOR_AGENT_MAX_RESULTS_PER_QUERY: int = int(
        os.getenv("INVESTOR_AGENT_MAX_RESULTS_PER_QUERY", "3"))
    # Set to "true" to use the slower LLM-based source selection.
    # Default is heuristic-only (much faster, similar quality for most queries).
    INVESTOR_AGENT_LLM_SOURCE_SELECTION: bool = os.getenv(
        "INVESTOR_AGENT_LLM_SOURCE_SELECTION", "false").lower() == "true"
    # Max repair loop iterations (0 = no repair, 1 = one repair pass).
    # Each iteration re-runs search→extract→fact_builder→claim_verifier.
    INVESTOR_AGENT_MAX_REPAIR_LOOPS: int = int(
        os.getenv("INVESTOR_AGENT_MAX_REPAIR_LOOPS", "0"))

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins, e.g.:
    #   http://localhost:5294,https://yourapp.example.com
    # Leave empty when CORS_ALLOW_ALL=true (wildcard overrides this list)
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "")
    # Set to true ONLY for local development — allows all origins (*)
    CORS_ALLOW_ALL: bool = os.getenv(
        "CORS_ALLOW_ALL", "false").lower() == "true"

    # ── Document Download ─────────────────────────────────────────────────────
    # Bearer token sent as "Authorization: Bearer <token>" when downloading
    # remote documents (Cloudinary, S3 pre-signed, etc.).
    # Leave empty if the URL is public / already signed.
    DOCUMENT_DOWNLOAD_BEARER_TOKEN: str = os.getenv(
        "DOCUMENT_DOWNLOAD_BEARER_TOKEN", "")
    # Optional extra headers as a JSON object, e.g.: {"X-Api-Key": "abc"}
    DOCUMENT_DOWNLOAD_EXTRA_HEADERS: str = os.getenv(
        "DOCUMENT_DOWNLOAD_EXTRA_HEADERS", "{}")

    # Evaluation performance
    EVALUATION_PARALLEL_CLASSIFY_EVIDENCE: bool = os.getenv(
        "EVALUATION_PARALLEL_CLASSIFY_EVIDENCE", "true").lower() == "true"
    EVALUATION_PITCH_DECK_IMAGE_TEXT_THRESHOLD: int = int(
        os.getenv("EVALUATION_PITCH_DECK_IMAGE_TEXT_THRESHOLD", "160"))
    EVALUATION_PITCH_DECK_MAX_IMAGES: int = int(
        os.getenv("EVALUATION_PITCH_DECK_MAX_IMAGES", "8"))


    INVESTOR_AGENT_REQUIRE_THREAD_ID: bool = os.getenv(
        "INVESTOR_AGENT_REQUIRE_THREAD_ID", "false").lower() == "true"


settings = Settings()

# Ensure directories exist
os.makedirs(settings.STORAGE_DIR, exist_ok=True)
os.makedirs(settings.ARTIFACTS_DIR, exist_ok=True)
