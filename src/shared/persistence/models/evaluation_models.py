from typing import Optional, List
from datetime import datetime
import json
from sqlmodel import SQLModel, Field, Column, String


class EvaluationRun(SQLModel, table=True):
    __tablename__ = "evaluation_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    startup_id: str
    # queued, processing, completed, partial_completed, failed
    status: str = Field(default="queued")
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    prompt_version: str = Field(default="1.0")
    model_name: str = Field(default="gpt-4o-mini")

    overall_score: Optional[float] = None
    overall_confidence: Optional[float] = None
    executive_summary: Optional[str] = None
    failure_reason: Optional[str] = None


class EvaluationDocument(SQLModel, table=True):
    __tablename__ = "evaluation_documents"

    id: Optional[int] = Field(default=None, primary_key=True)
    evaluation_run_id: int = Field(foreign_key="evaluation_runs.id")
    document_id: str
    document_type: str  # pitch_deck, business_plan
    # queued, processing, completed, failed
    processing_status: str = Field(default="queued")
    # pending, extracting, done, failed
    extraction_status: str = Field(default="pending")
    source_file_url_or_path: str
    extracted_text_path_or_blob: Optional[str] = None
    artifact_metadata_json: Optional[str] = None
    summary: Optional[str] = None
    document_score: Optional[float] = None
    document_confidence: Optional[float] = None


class EvaluationCriteriaResult(SQLModel, table=True):
    __tablename__ = "evaluation_criteria_results"

    id: Optional[int] = Field(default=None, primary_key=True)
    evaluation_document_id: int = Field(foreign_key="evaluation_documents.id")
    criterion_code: str
    criterion_name: str
    status: str = Field(default="scored")
    score: Optional[float] = None
    confidence: float
    reason: str
    evidence_refs_json: Optional[str] = None


class EvaluationLog(SQLModel, table=True):
    __tablename__ = "evaluation_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    evaluation_run_id: int = Field(foreign_key="evaluation_runs.id")
    step: str
    status: str
    message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
