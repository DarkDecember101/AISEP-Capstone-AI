from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
import json
from sqlalchemy.orm import Session
from src.shared.persistence.db import get_session
from src.shared.persistence.models.evaluation_models import EvaluationRun, EvaluationDocument, EvaluationCriteriaResult
from src.modules.evaluation.application.dto.evaluation_schema import (
    SubmitEvaluationRequest, SubmitEvaluationResponse, AggregatedReportSchema, CriterionResultSchema
)
from src.modules.evaluation.application.use_cases.submit_evaluation import submit_evaluation
from src.modules.evaluation.domain.scoring_policy import calculate_overall_score

router = APIRouter()

@router.get("/history")
def get_evaluation_history(startup_id: str, session: Session = Depends(get_session)):
    runs = session.query(EvaluationRun).filter(EvaluationRun.startup_id == startup_id).order_by(EvaluationRun.submitted_at.desc()).all()
    return [{
        "id": r.id,
        "status": r.status,
        "submitted_at": r.submitted_at,
        "overall_score": r.overall_score,
        "failure_reason": r.failure_reason,
    } for r in runs]

@router.post("/", response_model=SubmitEvaluationResponse)
def submit_evaluation_endpoint(request: SubmitEvaluationRequest):
    return submit_evaluation(request)

@router.get("/{id}")
def get_evaluation(id: int, session: Session = Depends(get_session)):
    run = session.query(EvaluationRun).filter(EvaluationRun.id == id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    
    docs = session.query(EvaluationDocument).filter(EvaluationDocument.evaluation_run_id == id).all()
    
    return {
        "id": run.id,
        "startup_id": run.startup_id,
        "status": run.status,
        "submitted_at": run.submitted_at,
        "failure_reason": run.failure_reason,
        "overall_score": run.overall_score,
        "overall_confidence": run.overall_confidence,
        "documents": [
            {
                "id": doc.id,
                "document_type": doc.document_type,
                "status": doc.processing_status,
                "extraction_status": doc.extraction_status,
                "summary": doc.summary,
            } for doc in docs
        ]
    }

@router.get("/{id}/report", response_model=AggregatedReportSchema)
def get_evaluation_report(id: int, session: Session = Depends(get_session)):
    run = session.query(EvaluationRun).filter(EvaluationRun.id == id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")

    if run.status == "failed":
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Evaluation failed. Report is unavailable.",
                "failure_reason": run.failure_reason,
                "next_step": "Check GET /api/v1/evaluations/{id} for document summaries and re-submit with a text-extractable PDF."
            },
        )

    if run.status not in ["completed", "partial_completed"]:
        raise HTTPException(status_code=202, detail="Report is not ready yet. Please retry shortly.")
        
    criteria_results = session.query(EvaluationCriteriaResult)\
        .join(EvaluationDocument)\
        .filter(EvaluationDocument.evaluation_run_id == id).all()
        
    avg_scores = {}
    for cr in criteria_results:
        # Ignore null scores for aggregation
        if cr.score is None:
            continue
        if cr.criterion_code not in avg_scores:
            avg_scores[cr.criterion_code] = []
        avg_scores[cr.criterion_code].append(cr.score)

    avg_scores = {k: sum(v)/len(v) for k, v in avg_scores.items() if len(v) > 0}
    calculated_scores = calculate_overall_score(avg_scores)

    docs = session.query(EvaluationDocument).filter(EvaluationDocument.evaluation_run_id == id).all()
    
    top_strengths = []
    top_risks = []
    missing_information = []
    processing_warnings = []
    
    executive_summary = run.executive_summary or "No summary available."

    for cr in criteria_results:
        text = (cr.reason or "").lower()
        if "fallback" in text:
            missing_information.append("Evaluation used fallback mode for some chunks.")

    for doc in docs:
        if doc.artifact_metadata_json:
            try:
                metadata = json.loads(doc.artifact_metadata_json)
                report_data = metadata.get("report", {}).get("overall_result_narrative", {})
                
                if report_data:
                    top_strengths.extend(report_data.get("top_strengths", []))
                    top_risks.extend(report_data.get("top_risks", []))
                    if "Summary of" in executive_summary and report_data.get("overall_explanation"):
                        executive_summary = report_data.get("overall_explanation")
                
                # You can also fetch gaps or missing info from evidence_mapping if needed
                processing_warnings.extend(metadata.get("processing_warnings", []))
            except Exception:
                pass

    top_strengths = list(dict.fromkeys(top_strengths))
    top_risks = list(dict.fromkeys(top_risks))
    missing_information = list(dict.fromkeys(missing_information))
    processing_warnings = list(dict.fromkeys(processing_warnings))

    return AggregatedReportSchema(
        startup_id=run.startup_id,
        overall_score=run.overall_score or 0.0,
        overall_confidence=run.overall_confidence or 0.0,
        dimension_scores=calculated_scores["dimension_scores"],
        executive_summary=executive_summary,
        top_strengths=top_strengths,
        top_risks=top_risks,
        missing_information=missing_information,
        processing_warnings=processing_warnings,
        criteria_details=[
            CriterionResultSchema(
                criterion_code=cr.criterion_code,
                score=cr.score,
                confidence=cr.confidence,
                reason=cr.reason,
                evidence_refs=json.loads(cr.evidence_refs_json) if cr.evidence_refs_json else []
            ) for cr in criteria_results
        ]
    )
