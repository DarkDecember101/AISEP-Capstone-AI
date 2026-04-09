"""
Recommendation repository factory (Phase 2B).

Returns the appropriate repository implementation based on
``settings.RECOMMENDATION_BACKEND``:

* ``"db"``         → :class:`DBRecommendationRepository` (durable, production)
* ``"filesystem"`` → :class:`RecommendationRepository`   (legacy JSON files)

The engine and router import this instead of hard-coding a specific repo.
"""

from __future__ import annotations

import logging

from src.shared.config.settings import settings

logger = logging.getLogger("aisep.recommendation.repo_factory")


def get_recommendation_repository():
    """
    Build and return the recommendation repository for the configured backend.

    Returns the same interface regardless of backend — both classes expose
    ``upsert_investor``, ``upsert_startup``, ``get_investor``, ``get_startup``,
    ``list_startups``, ``store_run``, ``list_runs_for_investor``, and
    ``latest_run_for_investor``.
    """
    backend = (settings.RECOMMENDATION_BACKEND or "db").lower().strip()

    if backend == "db":
        from src.modules.recommendation.infrastructure.repositories.db_recommendation_repository import (
            DBRecommendationRepository,
        )

        logger.info("Using DB-backed recommendation repository.")
        return DBRecommendationRepository()

    if backend == "filesystem":
        from src.modules.recommendation.infrastructure.repositories.recommendation_repository import (
            RecommendationRepository,
        )

        logger.info(
            "Using filesystem-backed recommendation repository (legacy).")
        return RecommendationRepository()

    logger.warning(
        "Unknown RECOMMENDATION_BACKEND=%r — defaulting to DB.", backend
    )
    from src.modules.recommendation.infrastructure.repositories.db_recommendation_repository import (
        DBRecommendationRepository,
    )

    return DBRecommendationRepository()
