import json
import os
import uuid
import httpx
from typing import Optional, List, Dict, Any
from src.shared.logging.logger import setup_logger
from src.shared.persistence.db import get_session
from src.shared.persistence.models.evaluation_models import (
    EvaluationRun, EvaluationDocument, EvaluationCriteriaResult, EvaluationLog
)
from src.modules.evaluation.infrastructure.parsers.pdf_parser import PDFParser
from src.shared.config.settings import settings
from src.modules.evaluation.domain.scoring_policy import normalize_to_canonical_criterion_name
from src.modules.evaluation.application.services.report_validity import validate_canonical_report

# New Pipeline Imports
from src.modules.evaluation.application.services.pipeline_llm_services import PipelineLLMServices
from src.modules.evaluation.application.services.deterministic_scorer import DeterministicScoringService

logger = setup_logger("process_document")


def resolve_document_source_to_local_path(source: str) -> str:
    """ Resolves a URL or local path to a readable file natively in artifacts """
    if source.startswith("http://") or source.startswith("https://"):
        os.makedirs(os.path.join(settings.ARTIFACTS_DIR,
                    "downloads"), exist_ok=True)
        # Download...
        target_path = os.path.join(
            settings.ARTIFACTS_DIR, "downloads", f"{uuid.uuid4()}.pdf")

        # Build optional auth / extra headers
        import json as _json
        dl_headers: dict = {}
        bearer = getattr(settings, "DOCUMENT_DOWNLOAD_BEARER_TOKEN", "")
        if bearer:
            dl_headers["Authorization"] = f"Bearer {bearer}"
        extra_raw = getattr(settings, "DOCUMENT_DOWNLOAD_EXTRA_HEADERS", "{}")
        try:
            extra = _json.loads(extra_raw) if isinstance(
                extra_raw, str) else (extra_raw or {})
        except Exception:
            extra = {}
        dl_headers.update(extra)

        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(source, headers=dl_headers)
            if response.status_code == 401:
                raise PermissionError(
                    f"401 Unauthorized fetching document from {source}. "
                    "Set DOCUMENT_DOWNLOAD_BEARER_TOKEN (or DOCUMENT_DOWNLOAD_EXTRA_HEADERS) "
                    "in .env with the correct credential."
                )
            response.raise_for_status()
            with open(target_path, "wb") as file_obj:
                file_obj.write(response.content)
        return target_path
    return source


def process_document(document_id: int):
    """
    Background job to process a single evaluation document using the multi-step pipeline.
    """
    session = next(get_session())
    doc = session.query(EvaluationDocument).filter(
        EvaluationDocument.id == document_id).first()
    if not doc:
        logger.error(f"Failed to find document id {document_id}")
        return

    doc.processing_status = "processing"
    session.commit()

    run = session.query(EvaluationRun).filter(
        EvaluationRun.id == doc.evaluation_run_id).first()

    try:
        local_file_path = resolve_document_source_to_local_path(
            doc.source_file_url_or_path)
        if not os.path.exists(local_file_path):
            raise FileNotFoundError(
                f"Document not found at path: {local_file_path}")

        logger.info(f"Extracting Multi-Modal pages for Document {doc.id}")
        pages = PDFParser.extract_text_and_images(
            local_file_path, extract_images=True)
        total_pages = len(pages)
        all_images = [p.get("image_path")
                      for p in pages if p.get("image_path")]
        full_text = "\n\n".join([p.get("text", "") for p in pages])

        if not all_images and not full_text.strip():
            raise ValueError(
                "Could not extract any content or images from the document.")

        pack_name = "pitch_deck" if doc.document_type == "pitch_deck" else "business_plan"
        pipeline_services = PipelineLLMServices(pack_name=pack_name)
        scorer = DeterministicScoringService(total_pages=total_pages)

        logger.info(f"Step 1: Classify Startup Context")
        classification_res = pipeline_services.classify_startup(
            full_text=full_text, images=all_images)

        logger.info(f"Step 2: Evidence Mapping")
        evidence_res = pipeline_services.map_evidence(
            full_text=full_text, images=all_images)
        evidence_json = evidence_res.model_dump_json(indent=2)

        logger.info(f"Step 3: Raw Judgments")
        raw_res = pipeline_services.judge_raw_criteria(
            evidence_result_json=evidence_json, full_text=full_text, images=all_images)

        logger.info(f"Step 4: Deterministic Scoring (Python)")
        scoring_res = scorer.score(classification_res, evidence_res, raw_res)

        logger.info(f"Step 5: Report Writer")
        scoring_json = scoring_res.model_dump_json(indent=2)
        report_res = pipeline_services.write_report(
            scoring_result_json=scoring_json)

        from src.modules.evaluation.application.dto.canonical_schema import (
            CanonicalEvaluationResult, CanonicalNarrative
        )

        # Missing Information derived dynamically
        missing_info = list(set(
            [gap for cr in evidence_res.criteria_evidence for gap in cr.gaps] + scoring_res.processing_warnings))

        # Normalize inputs for Pydantic cast
        def safe_recom(r):
            d = r.model_dump()
            normalized = normalize_to_canonical_criterion_name(
                d.get("expected_impact", "")
            )
            d["expected_impact"] = normalized if normalized else "Solution_&_Differentiation"
            return d

        def safe_question(q):
            d = q.model_dump()
            normalized = normalize_to_canonical_criterion_name(
                d.get("criterion", "")
            )
            d["criterion"] = normalized if normalized else "Solution_&_Differentiation"
            return d

        def safe_class(item):
            import re
            locs = []
            if getattr(item, "supporting_evidence_locations", None):
                for getattr_loc in getattr(item, "supporting_evidence_locations", []):
                    loc = str(getattr_loc)
                    m = re.search(r'\d+', loc)
                    num = int(m.group(0)) if m else 1
                    locs.append({
                        "source_type": "Pitch Deck",
                        "source_id": str(doc.id),
                        "slide_number_or_page_number": max(1, num),
                        "excerpt_or_summary": loc
                    })
            return {
                "value": getattr(item, "value", "Unknown") or "Unknown",
                "confidence": getattr(item, "confidence", "Low"),
                "resolution_source": getattr(item, "resolution_source", "inferred"),
                "supporting_evidence_locations": locs
            }

        narrative = CanonicalNarrative(
            executive_summary=report_res.overall_result_narrative.overall_explanation,
            top_strengths=report_res.overall_result_narrative.top_strengths,
            top_concerns=report_res.overall_result_narrative.top_concerns,
            missing_information=missing_info,
            overall_explanation=report_res.overall_result_narrative.overall_explanation,
            recommendations=[safe_recom(r)
                             for r in report_res.recommendations],
            key_questions=[safe_question(q) for q in report_res.key_questions],
            operational_notes=report_res.operational_notes +
            classification_res.operational_notes
        )

        classification_dict = {
            "stage": safe_class(classification_res.stage),
            "main_industry": safe_class(classification_res.main_industry),
            "subindustry": safe_class(classification_res.subindustry) if getattr(classification_res, "subindustry", None) else {
                "value": "Unknown", "confidence": "Low", "resolution_source": "inferred", "supporting_evidence_locations": []
            },
            "operational_notes": getattr(classification_res, "operational_notes", [])
        }

        canonical_dict = {
            "startup_id": str(run.startup_id).strip() if run and run.startup_id else "",
            "status": "completed",
            "classification": classification_dict,
            "effective_weights": scoring_res.effective_weights,
            "criteria_results": [c.model_dump() for c in scoring_res.criteria_results],
            "overall_result": scoring_res.overall_result.model_dump(),
            "narrative": narrative.model_dump(),
            "processing_warnings": list(scoring_res.processing_warnings),
        }

        validity = validate_canonical_report(canonical_dict)
        if not validity.is_valid:
            canonical_dict["status"] = "failed"
            canonical_dict["processing_warnings"] = list(
                canonical_dict["processing_warnings"]
            ) + [f"Canonical validation failed: {validity.reason}"]
            logger.warning(
                "Document %s canonical report invalid: %s",
                doc.id,
                validity.reason,
            )

        canonical_result = CanonicalEvaluationResult(**canonical_dict)

        # Persistence
        artifact_data = {
            "canonical_evaluation": canonical_result.model_dump()
        }

        doc.artifact_metadata_json = json.dumps(
            artifact_data, ensure_ascii=False)
        doc.summary = narrative.executive_summary
        doc.extraction_status = "done"

        # Save explicit mappings (Legacy backwards compatibility for simple dashboards if needed)
        for final_c in scoring_res.criteria_results:
            cf_val = {"High": 0.9, "Medium": 0.5, "Low": 0.2}.get(
                final_c.confidence, 0.5)

            crc = EvaluationCriteriaResult(
                evaluation_document_id=doc.id,
                criterion_code=final_c.criterion,
                criterion_name=final_c.criterion,
                status=final_c.status,
                score=final_c.final_score,
                confidence=cf_val,
                reason=final_c.explanation,
                evidence_refs_json=json.dumps(
                    [str(l.slide_number_or_page_number) for l in final_c.evidence_locations])
            )
            session.add(crc)

        doc.processing_status = "completed"
        logger.info(
            f"Successfully processed Document {doc.id} via Multi-Step Pipeline.")

    except Exception as e:
        logger.error(f"Failed doc process {doc.id}: {str(e)}", exc_info=True)
        doc.processing_status = "failed"
        doc.extraction_status = "failed"
        doc.summary = str(e)
        session.add(EvaluationLog(evaluation_run_id=doc.evaluation_run_id,
                    step="evaluate_document", status="failed", message=str(e)))

    finally:
        session.commit()
