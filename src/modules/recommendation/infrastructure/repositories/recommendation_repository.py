from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.shared.config.settings import settings
from src.shared.sanitize import require_safe_id
from src.modules.recommendation.application.dto.recommendation_schema import (
    InvestorRecommendationDocument,
    RecommendationRunRecord,
    StartupRecommendationDocument,
)

logger = logging.getLogger("aisep.recommendation.repository")


def _safe_filename(entity_id: str, label: str = "id") -> str:
    """Validate *entity_id* is safe for use as a filename component."""
    return require_safe_id(entity_id, label)


class RecommendationRepository:
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or Path(
            settings.STORAGE_DIR) / "recommendations")
        self.investors_dir = self.base_dir / "investors"
        self.startups_dir = self.base_dir / "startups"
        self.runs_dir = self.base_dir / "runs"
        self.investors_dir.mkdir(parents=True, exist_ok=True)
        self.startups_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def upsert_investor(self, document: InvestorRecommendationDocument) -> InvestorRecommendationDocument:
        safe_id = _safe_filename(document.investor_id, "investor_id")
        self._write_json(
            self.investors_dir / f"{safe_id}.json", document.model_dump(mode="json"))
        return document

    def upsert_startup(self, document: StartupRecommendationDocument) -> StartupRecommendationDocument:
        safe_id = _safe_filename(document.startup_id, "startup_id")
        self._write_json(
            self.startups_dir / f"{safe_id}.json", document.model_dump(mode="json"))
        return document

    def get_investor(self, investor_id: str) -> Optional[InvestorRecommendationDocument]:
        safe_id = _safe_filename(investor_id, "investor_id")
        path = self.investors_dir / f"{safe_id}.json"
        return self._read_json(path, InvestorRecommendationDocument)

    def get_startup(self, startup_id: str) -> Optional[StartupRecommendationDocument]:
        safe_id = _safe_filename(startup_id, "startup_id")
        path = self.startups_dir / f"{safe_id}.json"
        return self._read_json(path, StartupRecommendationDocument)

    def list_startups(self) -> List[StartupRecommendationDocument]:
        documents: List[StartupRecommendationDocument] = []
        for path in sorted(self.startups_dir.glob("*.json")):
            doc = self._read_json(path, StartupRecommendationDocument)
            if doc is not None:
                documents.append(doc)
        return documents

    def store_run(self, record: RecommendationRunRecord) -> RecommendationRunRecord:
        safe_id = _safe_filename(record.run_id, "run_id")
        self._write_json(
            self.runs_dir / f"{safe_id}.json", record.model_dump(mode="json"))
        return record

    def list_runs_for_investor(self, investor_id: str) -> List[RecommendationRunRecord]:
        records: List[RecommendationRunRecord] = []
        for path in sorted(self.runs_dir.glob("*.json")):
            rec = self._read_json(path, RecommendationRunRecord)
            if rec is not None and rec.investor_id == investor_id:
                records.append(rec)
        return records

    def latest_run_for_investor(self, investor_id: str) -> Optional[RecommendationRunRecord]:
        runs = self.list_runs_for_investor(investor_id)
        if not runs:
            return None
        return sorted(runs, key=lambda item: item.generated_at, reverse=True)[0]

    # ── Safe I/O helpers ────────────────────────────────────────────

    def _write_json(self, path: Path, payload: dict) -> None:
        """Atomic write: write to temp file then rename (safe on Windows too)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            content = json.dumps(
                payload, ensure_ascii=False, indent=2, default=str)
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(path)
        except Exception:
            # Clean up temp file on failure
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def _read_json(self, path: Path, model_cls):
        """Read + validate JSON file; returns None if missing or malformed."""
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            return model_cls.model_validate_json(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Malformed JSON in %s: %s — skipping", path, exc)
            return None
        except Exception as exc:
            logger.warning("Failed to parse %s: %s — skipping", path, exc)
            return None
