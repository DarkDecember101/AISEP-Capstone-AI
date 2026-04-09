"""
Migration utility: import existing filesystem JSON → DB (Phase 2B).

Usage::

    python -m src.modules.recommendation.scripts.migrate_json_to_db

Or from code::

    from src.modules.recommendation.scripts.migrate_json_to_db import migrate
    stats = migrate()          # default storage/recommendations
    stats = migrate("/path")   # custom source directory

Behaviour
---------
* Walks ``investors/*.json``, ``startups/*.json``, ``runs/*.json``
  inside the given base directory.
* For each file, parses the JSON into the corresponding Pydantic DTO
  and upserts/stores it via :class:`DBRecommendationRepository`.
* Existing DB rows with the same entity ID are **updated** (upsert),
  so the script is idempotent and safe to re-run.
* Prints a summary of imported / skipped / errored counts.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from src.shared.config.settings import settings
from src.shared.persistence.db import init_db
from src.modules.recommendation.infrastructure.repositories.db_recommendation_repository import (
    DBRecommendationRepository,
)
from src.modules.recommendation.application.dto.recommendation_schema import (
    InvestorRecommendationDocument,
    RecommendationRunRecord,
    StartupRecommendationDocument,
)

logger = logging.getLogger("aisep.recommendation.migrate")


@dataclass
class MigrationStats:
    investors_imported: int = 0
    investors_skipped: int = 0
    startups_imported: int = 0
    startups_skipped: int = 0
    runs_imported: int = 0
    runs_skipped: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def total_imported(self) -> int:
        return self.investors_imported + self.startups_imported + self.runs_imported

    @property
    def total_errors(self) -> int:
        return len(self.errors)


def migrate(base_dir: str | Path | None = None) -> MigrationStats:
    """
    Import all recommendation JSON files from *base_dir* into the DB.

    Parameters
    ----------
    base_dir : str or Path, optional
        Root of the recommendation JSON tree (contains ``investors/``,
        ``startups/``, ``runs/`` subdirectories).  Defaults to
        ``<STORAGE_DIR>/recommendations``.

    Returns
    -------
    MigrationStats
        Counts of imported / skipped / errored documents.
    """
    base = Path(base_dir or Path(settings.STORAGE_DIR) / "recommendations")
    stats = MigrationStats()
    repo = DBRecommendationRepository()

    # Ensure DB tables exist
    init_db()

    # ── Investors ───────────────────────────────────────────────────
    investors_dir = base / "investors"
    if investors_dir.is_dir():
        for path in sorted(investors_dir.glob("*.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                doc = InvestorRecommendationDocument.model_validate_json(raw)
                repo.upsert_investor(doc)
                stats.investors_imported += 1
                logger.info("Imported investor %s from %s",
                            doc.investor_id, path.name)
            except Exception as exc:
                stats.investors_skipped += 1
                msg = f"Skipped investor file {path.name}: {exc}"
                stats.errors.append(msg)
                logger.warning(msg)
    else:
        logger.info("No investors/ directory found at %s — skipping.", base)

    # ── Startups ────────────────────────────────────────────────────
    startups_dir = base / "startups"
    if startups_dir.is_dir():
        for path in sorted(startups_dir.glob("*.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                doc = StartupRecommendationDocument.model_validate_json(raw)
                repo.upsert_startup(doc)
                stats.startups_imported += 1
                logger.info("Imported startup %s from %s",
                            doc.startup_id, path.name)
            except Exception as exc:
                stats.startups_skipped += 1
                msg = f"Skipped startup file {path.name}: {exc}"
                stats.errors.append(msg)
                logger.warning(msg)
    else:
        logger.info("No startups/ directory found at %s — skipping.", base)

    # ── Runs ────────────────────────────────────────────────────────
    runs_dir = base / "runs"
    if runs_dir.is_dir():
        for path in sorted(runs_dir.glob("*.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                rec = RecommendationRunRecord.model_validate_json(raw)
                repo.store_run(rec)
                stats.runs_imported += 1
                logger.info("Imported run %s from %s", rec.run_id, path.name)
            except Exception as exc:
                stats.runs_skipped += 1
                msg = f"Skipped run file {path.name}: {exc}"
                stats.errors.append(msg)
                logger.warning(msg)
    else:
        logger.info("No runs/ directory found at %s — skipping.", base)

    return stats


def main() -> None:
    """CLI entry-point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    base = sys.argv[1] if len(sys.argv) > 1 else None
    print(
        f"Migrating recommendation JSON files from: {base or '<default storage/recommendations>'}")
    print()

    stats = migrate(base)

    print()
    print("═" * 52)
    print(
        f"  Investors  imported={stats.investors_imported}  skipped={stats.investors_skipped}")
    print(
        f"  Startups   imported={stats.startups_imported}  skipped={stats.startups_skipped}")
    print(
        f"  Runs       imported={stats.runs_imported}  skipped={stats.runs_skipped}")
    print(
        f"  Total      imported={stats.total_imported}  errors={stats.total_errors}")
    print("═" * 52)

    if stats.errors:
        print("\nErrors:")
        for err in stats.errors:
            print(f"  ⚠ {err}")

    sys.exit(1 if stats.total_errors > 0 else 0)


if __name__ == "__main__":
    main()
