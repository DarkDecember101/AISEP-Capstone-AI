import json
import logging
from typing import Dict, Any, Optional
from src.shared.persistence.db import get_session
from src.shared.persistence.models.evaluation_models import (
    EvaluationRun, EvaluationDocument, EvaluationCriteriaResult, EvaluationLog
)
from src.modules.evaluation.application.services.report_validity import (
    sanitize_canonical_report,
    validate_canonical_report,
)
from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
from src.modules.evaluation.application.use_cases.merge_evaluation import merge_canonical_results
from src.shared.config.settings import settings

logger = logging.getLogger("aisep.aggregate")

# merge_status values written to EvaluationRun.merge_status
_MS_NOT_APPLICABLE = "not_applicable"      # run is not combined mode
_MS_WAITING = "waiting_for_sources"  # in-flight docs detected; deferred
_MS_FALLBACK = "fallback_source_only"  # combined, only one source available
_MS_MERGED = "merged"               # merge succeeded; merged_artifact_json populated
_MS_MERGE_FAILED = "merge_failed"         # merge attempted but raised exception
# MERGE_EVAL_ENABLED=False; not attempted
_MS_MERGE_DISABLED = "merge_disabled"


def _build_no_success_failure_reason(docs: list[EvaluationDocument]) -> str:
    """Surface the most actionable document failure details at run level."""
    failure_summaries: list[str] = []
    for doc in docs:
        if doc.processing_status != "failed":
            continue
        summary = str(doc.summary or "").strip()
        if not summary:
            continue
        label = f"{doc.document_type or 'document'}"
        failure_summaries.append(f"{label}: {summary}")

    if failure_summaries:
        unique_summaries = list(dict.fromkeys(failure_summaries))
        return "No canonical evaluation data produced. Document failures: " + " | ".join(unique_summaries[:2])

    return (
        "No canonical evaluation data produced. "
        "Check document path/url, parser, and pipeline configuration."
    )


def aggregate_evaluation_run(run_id: int):
    """
    Summarize document-level criteria results into a run-level canonical result.

    merge_status lifecycle written on run:
      not_applicable     — run is pitch_deck_only or business_plan_only
      waiting_for_sources — one or more docs still in flight; aggregate deferred
      fallback_source_only — combined run, but only one source doc succeeded
      merged             — both sources available, merge succeeded
      merge_failed       — both sources available, merge threw exception; PD fallback used
      merge_disabled     — both sources available, MERGE_EVAL_ENABLED=False; PD fallback used
    """
    session = next(get_session())
    run = session.query(EvaluationRun).filter(
        EvaluationRun.id == run_id).first()

    if not run:
        return

    docs = session.query(EvaluationDocument).filter(
        EvaluationDocument.evaluation_run_id == run_id).all()

    eval_mode = run.evaluation_mode or ""
    is_combined = (eval_mode == "combined")

    # ── Log aggregate entry with source availability summary ─────────────────
    pd_all = [d for d in docs if d.document_type == "pitch_deck"]
    bp_all = [d for d in docs if d.document_type == "business_plan"]
    logger.info(
        "[aggregate:start] run_id=%s mode=%s total_docs=%d "
        "pitch_deck_docs=%d business_plan_docs=%d",
        run_id, eval_mode, len(docs), len(pd_all), len(bp_all),
    )

    # ── Early-exit: some docs still in flight ────────────────────────────────
    in_flight = [
        d for d in docs
        if d.processing_status in ("queued", "processing", "pending", "extracting")
    ]
    if in_flight:
        logger.info(
            "[aggregate:waiting] run_id=%s %d doc(s) still in flight — deferring aggregate",
            run_id, len(in_flight),
        )
        if is_combined:
            run.merge_status = _MS_WAITING
            session.commit()
        return

    # ── Successful docs (completed + artifact present) ────────────────────────
    success_docs = [
        d for d in docs
        if d.processing_status == "completed" and d.artifact_metadata_json
    ]
    pd_docs = [d for d in success_docs if d.document_type == "pitch_deck"]
    bp_docs = [d for d in success_docs if d.document_type == "business_plan"]

    logger.info(
        "[aggregate:sources] run_id=%s completed_with_artifact: pitch_deck=%d business_plan=%d",
        run_id, len(pd_docs), len(bp_docs),
    )

    if not success_docs:
        run.status = "failed"
        run.failure_reason = _build_no_success_failure_reason(docs)
        if is_combined:
            run.merge_status = _MS_FALLBACK
        session.add(EvaluationLog(
            evaluation_run_id=run_id,
            step="aggregate",
            status="failed",
            message=run.failure_reason,
        ))
        session.commit()
        return

    # ── Canonical extraction helpers ─────────────────────────────────────────

    def _extract_canonical(doc: EvaluationDocument) -> Optional[Dict]:
        try:
            meta = json.loads(doc.artifact_metadata_json)
            return meta.get("canonical_evaluation")
        except Exception:
            return None

    def _backfill_startup_id(canonical: Dict, doc: EvaluationDocument):
        cid = str(canonical.get("startup_id") or "").strip()
        if not cid and run.startup_id:
            canonical["startup_id"] = run.startup_id
            meta = json.loads(doc.artifact_metadata_json)
            meta["canonical_evaluation"] = canonical
            doc.artifact_metadata_json = json.dumps(meta, ensure_ascii=False)

    try:
        canonical = None
        merge_applied = False

        pd_canonical = None
        bp_canonical = None

        if pd_docs:
            pd_canonical = _extract_canonical(pd_docs[0])
            if pd_canonical:
                _backfill_startup_id(pd_canonical, pd_docs[0])

        if bp_docs:
            bp_canonical = _extract_canonical(bp_docs[0])
            if bp_canonical:
                _backfill_startup_id(bp_canonical, bp_docs[0])

        # ── Merge / fallback decision ─────────────────────────────────────────
        if is_combined:
            if pd_canonical and bp_canonical:
                if settings.MERGE_EVAL_ENABLED:
                    # Both sources present — attempt merge
                    logger.info(
                        "[aggregate:merge_attempt] run_id=%s both PD+BP canonical available; "
                        "attempting merge",
                        run_id,
                    )
                    try:
                        pd_result = CanonicalEvaluationResult(**pd_canonical)
                        bp_result = CanonicalEvaluationResult(**bp_canonical)
                        merged = merge_canonical_results(pd_result, bp_result)
                        canonical = merged.model_dump()
                        merge_applied = True
                        run.merge_status = _MS_MERGED
                        run.merged_artifact_json = json.dumps(
                            {"canonical_evaluation": canonical}, ensure_ascii=False
                        )
                        logger.info(
                            "[aggregate:merged] run_id=%s PD+BP merge succeeded",
                            run_id,
                        )
                    except Exception as me:
                        # Merge attempted but failed — fall back to PD
                        logger.error(
                            "[aggregate:merge_failed] run_id=%s merge exception: %s",
                            run_id, me, exc_info=True,
                        )
                        run.merge_status = _MS_MERGE_FAILED
                        session.add(EvaluationLog(
                            evaluation_run_id=run_id,
                            step="merge",
                            status="failed",
                            message=(
                                f"Merge attempted but failed (falling back to pitch_deck source): "
                                f"{me}"
                            ),
                        ))
                        canonical = pd_canonical
                else:
                    # Feature flag disabled — both sources present but merge skipped
                    logger.info(
                        "[aggregate:merge_disabled] run_id=%s MERGE_EVAL_ENABLED=False; "
                        "skipping merge, falling back to pitch_deck source",
                        run_id,
                    )
                    run.merge_status = _MS_MERGE_DISABLED
                    session.add(EvaluationLog(
                        evaluation_run_id=run_id,
                        step="merge",
                        status="skipped",
                        message=(
                            "Merge skipped: MERGE_EVAL_ENABLED=False. "
                            "Both PD+BP canonical available but merge not attempted; "
                            "falling back to pitch_deck source result."
                        ),
                    ))
                    canonical = pd_canonical
            else:
                # Combined run but only one (or zero) source succeeded
                missing = "business_plan" if not bp_canonical else "pitch_deck"
                present = "pitch_deck" if not bp_canonical else "business_plan"
                present_canonical = pd_canonical if pd_canonical else bp_canonical

                logger.warning(
                    "[aggregate:combined_fallback] run_id=%s %s missing/failed; "
                    "returning %s source result only",
                    run_id, missing, present,
                )
                run.merge_status = _MS_FALLBACK
                session.add(EvaluationLog(
                    evaluation_run_id=run_id,
                    step="aggregate",
                    status="combined_fallback",
                    message=(
                        f"Combined run fallback: {missing} missing/failed; "
                        f"returning {present} source result."
                    ),
                ))
                canonical = present_canonical
        else:
            # Not a combined run — merge not applicable
            run.merge_status = _MS_NOT_APPLICABLE
            if pd_canonical:
                canonical = pd_canonical
            elif bp_canonical:
                canonical = bp_canonical
            else:
                # Fallback: use first success doc (legacy behavior)
                canonical = _extract_canonical(success_docs[0])
                if canonical:
                    _backfill_startup_id(canonical, success_docs[0])

        # ── Validate and persist run-level result ─────────────────────────────
        if canonical:
            canonical = sanitize_canonical_report(canonical)
            validity = validate_canonical_report(canonical)
            if not validity.is_valid:
                run.status = "failed"
                run.overall_score = None
                run.overall_confidence = None
                run.failure_reason = (
                    f"Invalid canonical evaluation report: {validity.reason}"
                )
                session.add(EvaluationLog(
                    evaluation_run_id=run_id,
                    step="aggregate",
                    status="failed",
                    message=run.failure_reason,
                ))
                session.commit()
                return

            run.overall_score = canonical.get(
                "overall_result", {}).get("overall_score")
            cf = canonical.get("overall_result", {}).get(
                "overall_confidence", "Medium")
            run.overall_confidence = {"High": 0.9,
                                      "Medium": 0.5, "Low": 0.2}.get(cf, 0.5)
            run.executive_summary = canonical.get(
                "narrative", {}).get("executive_summary")
            run.status = "completed"
            run.failure_reason = None

            if merge_applied:
                agg_msg = "Merged PD+BP canonical aggregation finished."
            elif is_combined:
                agg_msg = (
                    f"Combined aggregation finished (merge_status={run.merge_status}); "
                    "single-source result used."
                )
            else:
                agg_msg = "Canonical aggregation finished."

            session.add(EvaluationLog(
                evaluation_run_id=run_id,
                step="aggregate",
                status="completed",
                message=agg_msg,
            ))
        else:
            run.status = "failed"
            run.failure_reason = (
                "Failed to parse canonical evaluation object from document."
            )
            session.add(EvaluationLog(
                evaluation_run_id=run_id,
                step="aggregate",
                status="failed",
                message=run.failure_reason,
            ))

    except Exception as e:
        run.status = "failed"
        run.failure_reason = str(e)
        session.add(EvaluationLog(
            evaluation_run_id=run_id,
            step="aggregate",
            status="failed",
            message=str(e),
        ))

    session.commit()
