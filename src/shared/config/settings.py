import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    # ── CORS ───────────────────────────────────────────────────────────────
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

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()

# Ensure directories exist
os.makedirs(settings.STORAGE_DIR, exist_ok=True)
os.makedirs(settings.ARTIFACTS_DIR, exist_ok=True)
