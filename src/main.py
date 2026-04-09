from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.shared.config.settings import settings
from src.shared.persistence.db import init_db
from src.shared.correlation import CorrelationIdMiddleware
from src.shared.error_response import register_error_handlers
from src.shared.health import router as health_router
from src.shared.tracing.setup import init_tracing
from src.modules.evaluation.api.router import router as evaluation_router
from src.modules.investor_agent.api.router import router as investor_router
from src.modules.recommendation.api.router import router as recommendation_router
import uvicorn
import logging

logger = logging.getLogger("aisep.main")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="AISEP AI Evaluation Service - Phase 1",
    version="1.0.0",
)

# ── CORS ─────────────────────────────────────────────────────────────
# CORS_ORIGINS env var: comma-separated list of allowed origins.
# Set to "*" (with CORS_ALLOW_ALL=true) only for local dev.
_cors_origins: list[str] = [
    o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()
] if settings.CORS_ORIGINS else []

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.CORS_ALLOW_ALL else _cors_origins,
    # credentials incompatible with wildcard
    allow_credentials=not settings.CORS_ALLOW_ALL,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Middleware ───────────────────────────────────────────────────────
app.add_middleware(CorrelationIdMiddleware)

# ── Global error handlers ───────────────────────────────────────────
register_error_handlers(app)


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Database initialized.")
    init_tracing()
    logger.info("Tracing bootstrap complete.")


# ── Routers ─────────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(evaluation_router,
                   prefix=f"{settings.API_V1_STR}/evaluations", tags=["Evaluations"])
app.include_router(investor_router,
                   prefix=f"{settings.API_V1_STR}/investor-agent", tags=["Investor Agent"])
app.include_router(recommendation_router, tags=["Recommendations"])


if __name__ == "__main__":
    ssl_kwargs = {}
    if settings.SSL_KEYFILE and settings.SSL_CERTFILE:
        ssl_kwargs["ssl_keyfile"] = settings.SSL_KEYFILE
        ssl_kwargs["ssl_certfile"] = settings.SSL_CERTFILE
        if settings.SSL_CA_CERTS:
            ssl_kwargs["ssl_ca_certs"] = settings.SSL_CA_CERTS
        logger.info("Starting with HTTPS (TLS) on port %s",
                    settings.SERVER_PORT)
    else:
        logger.info("Starting with HTTP (no TLS) on port %s",
                    settings.SERVER_PORT)

    uvicorn.run(
        "src.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=True,
        **ssl_kwargs,
    )
