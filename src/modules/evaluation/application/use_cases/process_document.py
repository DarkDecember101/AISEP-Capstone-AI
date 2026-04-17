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
from src.modules.evaluation.application.services.report_validity import (
    validate_canonical_report,
    sanitize_canonical_report,
)

# New Pipeline Imports
from src.modules.evaluation.application.services.pipeline_llm_services import PipelineLLMServices
from src.modules.evaluation.application.services.deterministic_scorer import DeterministicScoringService
from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
from src.modules.evaluation.application.services.reduce_bp_text import reduce_business_plan_text

logger = setup_logger("process_document")

_NULL_STRINGS: frozenset = frozenset({"null", "none", "n/a", "na", ""})


def _normalize_null_str(v: str | None) -> str | None:
    """Coerce string sentinels like 'null', 'None', '' to actual None."""
    if v is None:
        return None
    if str(v).strip().lower() in _NULL_STRINGS:
        return None
    return v


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

        # Apply BP text reduction if needed
        bp_reduction_metadata = None
        reduction_warnings: list = []
        if pack_name == "business_plan":
            full_text, bp_reduction_metadata = reduce_business_plan_text(
                pages, reduction_warnings)

        pipeline_services = PipelineLLMServices(pack_name=pack_name)
        scorer = DeterministicScoringService(total_pages=total_pages)

        # Build classification context from user-provided hints on the run.
        # Normalize string sentinels ("null", "None", "") to actual None first.
        classification_context = None
        if run:
            _ps = _normalize_null_str(run.provided_stage)
            _pi = _normalize_null_str(run.provided_main_industry)
            _psi = _normalize_null_str(run.provided_subindustry)
            if any([_ps, _pi, _psi]):
                classification_context = ClassificationContextInput(
                    provided_stage=_ps,
                    provided_main_industry=_pi,
                    provided_subindustry=_psi,
                )

        logger.info(f"Step 1: Classify Startup Context")
        classification_res = pipeline_services.classify_startup(
            full_text=full_text, images=all_images,
            classification_context=classification_context)

        # ── Bug 1+2 fix: enforce provided_stage on the classification result. ──
        # The LLM is instructed to verify the hint against evidence and may return
        # a different stage.  Per product spec, an evaluator-supplied stage is
        # authoritative; it must not be silently overridden by the LLM.
        # This also guarantees the deterministic scorer uses the correct weight
        # profile regardless of what the LLM chose.
        _VALID_STAGES = frozenset(
            {"IDEA", "MVP", "PRE_SEED", "SEED", "GROWTH"})
        if classification_context and classification_context.provided_stage:
            _ps = classification_context.provided_stage.upper().strip()
            if _ps in _VALID_STAGES:
                from src.modules.evaluation.application.dto.pipeline_schema import ClassificationField as _CF
                _llm_stage = (classification_res.stage.value or "").upper()
                if _llm_stage != _ps:
                    logger.info(
                        "Stage enforced to provided value '%s' (LLM classified as '%s').",
                        _ps, _llm_stage,
                    )
                classification_res = classification_res.model_copy(update={
                    "stage": _CF(
                        value=_ps,
                        confidence="High",
                        resolution_source="provided",
                        supporting_evidence_locations=(
                            classification_res.stage.supporting_evidence_locations
                        ),
                    )
                })

                # Purge LLM-generated operational notes that reference the now-wrong
                # stage override.  The LLM may have written e.g.
                # "Provided stage SEED overridden to PRE_SEED based on evidence."
                # After enforcement that claim is factually incorrect.
                import re as _re_stage
                _stage_override_pat = _re_stage.compile(
                    r"\boverrid(?:e|ing|en|den)\b"
                    r"|\bstage\s+(?:was\s+)?(?:set|changed|adjusted)\b"
                    r"|\bclassified\s+as\b",
                    _re_stage.IGNORECASE,
                )
                _other_stages = _VALID_STAGES - {_ps}
                _other_pat = _re_stage.compile(
                    r"\b(" + "|".join(_other_stages) + r")\b",
                    _re_stage.IGNORECASE,
                )
                _clean_op_notes = [
                    n for n in getattr(classification_res, "operational_notes", [])
                    if not (
                        isinstance(n, str)
                        and _stage_override_pat.search(n)
                        and _other_pat.search(n)
                    )
                ]
                if len(_clean_op_notes) != len(getattr(classification_res, "operational_notes", [])):
                    classification_res = classification_res.model_copy(
                        update={"operational_notes": _clean_op_notes}
                    )
                    logger.info(
                        "Removed %d contradictory stage-override note(s) from classification_res.",
                        len(getattr(classification_res,
                            "operational_notes", [])) - len(_clean_op_notes)
                        + (len(getattr(classification_res,
                           "operational_notes", [])) - len(_clean_op_notes)),
                    )

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
        classification_json = classification_res.model_dump_json(indent=2)
        report_res = pipeline_services.write_report(
            scoring_result_json=scoring_json,
            document_type=doc.document_type or pack_name,
            classification_json=classification_json,
        )

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
            import re as _re
            import json as _json
            import ast as _ast
            source_type_label = "Business Plan" if doc.document_type == "business_plan" else "Pitch Deck"
            # String values that represent "no value" and must be normalized to null.
            _NULL_SENTINELS = frozenset(
                {"", "unknown", "null", "none", "n/a", "na", "other", "undefined"})

            def _parse_evidence_loc(raw_loc):
                """Parse a raw evidence location (str / dict / model) into
                (excerpt: str, page_num: int, section_name: str|None).
                Handles three formats emitted by the LLM:
                  1. Valid JSON string (double-quoted):   '{"excerpt_or_summary": "..."}'
                  2. Python dict repr (single-quoted):   "{'excerpt_or_summary': '...'}"
                  3. Plain string excerpt with optional page number embedded.
                """
                if isinstance(raw_loc, str):
                    stripped = raw_loc.strip()
                    if stripped.startswith("{") and stripped.endswith("}"):
                        # Try valid JSON first
                        parsed = None
                        try:
                            parsed = _json.loads(stripped)
                        except (_json.JSONDecodeError, ValueError):
                            pass
                        # Fallback: Python dict repr (single-quoted keys/values)
                        if parsed is None:
                            try:
                                parsed = _ast.literal_eval(stripped)
                            except (ValueError, SyntaxError):
                                pass
                        if isinstance(parsed, dict):
                            excerpt = parsed.get(
                                "excerpt_or_summary") or stripped
                            page = parsed.get(
                                "slide_number_or_page_number") or 1
                            sec = parsed.get("section_name")
                            return excerpt, page, sec
                    # Plain string — extract the first number as page reference
                    m = _re.search(r'\d+', raw_loc)
                    return raw_loc, int(m.group(0)) if m else 1, None
                if hasattr(raw_loc, "model_dump"):
                    d = raw_loc.model_dump()
                    return d.get("excerpt_or_summary", str(raw_loc)), d.get("slide_number_or_page_number", 1), d.get("section_name")
                if isinstance(raw_loc, dict):
                    return raw_loc.get("excerpt_or_summary", str(raw_loc)), raw_loc.get("slide_number_or_page_number", 1), raw_loc.get("section_name")
                return str(raw_loc), 1, None

            locs = []
            for raw_loc in (getattr(item, "supporting_evidence_locations", None) or []):
                excerpt, page_num, section_name = _parse_evidence_loc(raw_loc)
                loc_entry = {
                    "source_type": source_type_label,
                    "source_id": str(doc.id),
                    "slide_number_or_page_number": max(1, int(page_num) if page_num else 1),
                    "excerpt_or_summary": excerpt or "",
                }
                if section_name is not None:
                    loc_entry["section_name"] = section_name
                locs.append(loc_entry)

            # Normalize value: any placeholder / unknown sentinel → None
            raw_value = getattr(item, "value", None)
            normalized_value = (
                raw_value
                if raw_value is not None and str(raw_value).strip().lower() not in _NULL_SENTINELS
                else None
            )
            return {
                "value": normalized_value,
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
            # Issue 5: deduplicate while preserving order
            operational_notes=list(dict.fromkeys(
                report_res.operational_notes + classification_res.operational_notes
            ))
        )

        classification_dict = {
            "stage": safe_class(classification_res.stage),
            "main_industry": safe_class(classification_res.main_industry),
            "subindustry": safe_class(classification_res.subindustry) if getattr(classification_res, "subindustry", None) else {
                "value": None, "confidence": "Low", "resolution_source": "inferred", "supporting_evidence_locations": []
            },
            "operational_notes": getattr(classification_res, "operational_notes", [])
        }

        _real_doc_id = str(doc.document_id) if getattr(
            doc, "document_id", None) else str(doc.id)
        _PLACEHOLDER = "document_id_placeholder"

        def _fix_evidence_locs(locs: list) -> list:
            """Replace source_id placeholders in a list of evidence location dicts."""
            for ev in locs:
                if isinstance(ev, dict) and ev.get("source_id") == _PLACEHOLDER:
                    ev["source_id"] = _real_doc_id
            return locs

        raw_criteria = [c.model_dump() for c in scoring_res.criteria_results]
        for _cr in raw_criteria:
            _fix_evidence_locs(_cr.get("evidence_locations") or [])

        canonical_dict = {
            "startup_id": str(run.startup_id).strip() if run and run.startup_id else "",
            "document_type": doc.document_type,
            "status": "completed",
            "classification": classification_dict,
            "effective_weights": scoring_res.effective_weights,
            "criteria_results": raw_criteria,
            "overall_result": scoring_res.overall_result.model_dump(),
            "narrative": narrative.model_dump(),
            "processing_warnings": list(scoring_res.processing_warnings) + reduction_warnings,
        }

        # Auto-correction pass: fix subindustry notes, filter contradictory recommendations
        canonical_dict = sanitize_canonical_report(canonical_dict)

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
        if validity.validation_flags:
            canonical_dict["processing_warnings"] = list(
                canonical_dict["processing_warnings"]
            ) + list(validity.validation_flags)
            for flag in validity.validation_flags:
                logger.warning(
                    "Document %s post-assembly flag: %s", doc.id, flag)

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
