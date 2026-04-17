from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ReportValidity:
    is_valid: bool
    reason: str
    validation_flags: tuple = ()  # non-fatal auditable warnings


# â”€â”€ Regex patterns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_CROSS_DOC_PATTERNS = re.compile(
    r"\bacross both documents?\b"
    r"|\bboth documents?\b"
    r"|\bcombined analysis\b"
    r"|\bmerged analysis\b",
    re.IGNORECASE,
)

_STAGE_REGRESSION_PATTERNS = re.compile(
    r"\bbuild\s+(?:an?\s+)?mvp\b"
    r"|\bcreate\s+(?:an?\s+)?mvp\b"
    r"|\bdevelop\s+(?:an?\s+)?mvp\b",
    re.IGNORECASE,
)

# Extended â€” catches all documented LLM phrasings for "no subindustry overlay"
_NO_SUBINDUSTRY_PATTERN = re.compile(
    r"no\s+(?:listed\s+)?subindustry\s+(?:was\s+)?confidently\s+resolvable"
    r"|no\s+specific\s+subindustry"
    r"|no\s+overlay\s+was\s+applied"
    r"|no\s+subindustry\s+overlay"  # catches 'No subindustry overlay was applied'
    r"|subindustry\s+(?:could\s+not|cannot|was\s+unable\s+to)\s+be\s+(?:resolved|identified|applied)"
    r"|subindustry\s+overlay\s+was\s+not\s+applied"
    r"|could\s+not\s+confidently\s+resolve\s+(?:a\s+)?subindustry"
    r"|only\s+core\s+rubric\s+(?:and\s+stage\s+profile\s+)?(?:was\s+)?applied",
    re.IGNORECASE,
)

# Patterns indicating weak evidence â€” used to cross-check against high-scoring criteria
_WEAK_EVIDENCE_LANGUAGE = re.compile(
    r"\blimited\s+(?:early\s+)?(?:validation|traction|evidence|data|proof)\b"
    r"|\bno\s+(?:clear\s+|meaningful\s+|strong\s+)?(?:validation|traction|evidence|customers?|revenue)\b"
    r"|\bprioritize\s+(?:obtaining\s+|building\s+|seeking\s+)?(?:early\s+)?(?:validation|traction|evidence)\b"
    r"|\black\s+of\s+(?:validation|traction|evidence|customers?|traction)\b"
    r"|\binsufficient\s+(?:validation|traction|evidence)\b"
    r"|\bunvalidated\b|\bunproven\b"
    r"|\bearly\s+stage\s+(?:validation|traction)\b",
    re.IGNORECASE,
)

# Criterion name fragments for keyword-based matching in free-text fields
_CRITERION_KEYWORDS: dict[str, list[str]] = {
    "Problem_&_Customer_Pain": ["problem", "customer pain", "customer segment", "icp"],
    "Market_Attractiveness_&_Timing": ["market", "tam", "sam", "timing", "market size"],
    "Solution_&_Differentiation": ["solution", "differentiation", "product", "technology"],
    "Business_Model_&_Go_to_Market": ["business model", "go-to-market", "gtm", "revenue model", "monetization"],
    "Team_&_Execution_Readiness": ["team", "founder", "execution", "leadership"],
    "Validation_Traction_Evidence_Quality": [
        "validation", "traction", "evidence", "customers", "revenue",
        "adoption", "retention", "early validation", "early traction",
    ],
}

_STRONG_EVIDENCE_LEVELS = frozenset({"STRONG_DIRECT", "DIRECT"})
# criterion final_score above which weak-evidence language is contradictory
_HIGH_SCORE_THRESHOLD = 75.0

_ABSENT_EVIDENCE_PATTERN = re.compile(
    r"\bno evidence\b|\babsent\b|\bcompletely missing\b|\bno clear evidence\b",
    re.IGNORECASE,
)

# Stage-descending language that should not appear when a higher stage is classified.
# Keys are the CLASSIFIED stage; value is a pattern of language only appropriate
# for a LOWER stage.
_LOWER_STAGE_LANGUAGE: dict[str, re.Pattern] = {
    "SEED": re.compile(
        r"\bpre[-\s_]seed\b"
        r"|\bpre[-\s]product[-\s]market\b"
        r"|\bseeking\s+(?:its\s+)?(?:first\s+)?(?:customers?|users?|pilots?)\b"
        r"|\bvalidat(?:e|ing)\s+(?:the\s+)?(?:core\s+)?(?:concept|hypothesis|idea)\b"
        r"|\bpre[-\s]revenue\s+venture\b"
        r"|\bearly[-\s]stage\s+venture\b",
        re.IGNORECASE,
    ),
    "GROWTH": re.compile(
        r"\bpre[-\s_]seed\b"
        r"|\bseed[-\s]stage\b"
        r"|\bbuild(?:ing)?\s+(?:the\s+)?(?:initial\s+)?(?:product|mvp|prototype)\b",
        re.IGNORECASE,
    ),
}


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _collect_narrative_texts(canonical: Mapping[str, Any]) -> list[str]:
    narrative = canonical.get("narrative") or {}
    texts: list[str] = []
    for key in ("top_strengths", "top_concerns", "missing_information", "operational_notes"):
        for item in (narrative.get(key) or []):
            if isinstance(item, str):
                texts.append(item)
    for key in ("executive_summary", "overall_explanation"):
        v = narrative.get(key)
        if isinstance(v, str):
            texts.append(v)
    for rec in (narrative.get("recommendations") or []):
        if isinstance(rec, dict):
            texts.extend(v for v in rec.values() if isinstance(v, str))
    for kq in (narrative.get("key_questions") or []):
        if isinstance(kq, dict):
            texts.extend(v for v in kq.values() if isinstance(v, str))
    return texts


def _criteria_lookup(canonical: Mapping[str, Any]) -> dict[str, Mapping]:
    return {
        c["criterion"]: c
        for c in (canonical.get("criteria_results") or [])
        if isinstance(c, Mapping) and c.get("criterion")
    }


def _text_mentions_criterion(text: str, criterion: str) -> bool:
    """Return True if the text references the given criterion by keyword."""
    keywords = _CRITERION_KEYWORDS.get(criterion, [])
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# AUTO-CORRECTION PASS  (mutates the canonical dict before validation)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _correct_subindustry_operational_notes(canonical: dict) -> tuple[dict, list[str]]:
    """
    Bug 2 fix: when classification.subindustry has confidence=High with a real
    resolved value, remove any operational note that claims no overlay was applied
    and inject the correct note.

    Returns (modified_canonical, list_of_correction_messages).
    """
    corrections: list[str] = []
    classification = canonical.get("classification") or {}
    subindustry = classification.get("subindustry") or {}
    sub_confidence = subindustry.get("confidence")
    sub_value = (subindustry.get("value") or "").strip()

    if sub_confidence != "High" or not sub_value or sub_value in ("Unknown", "OTHER", ""):
        return canonical, corrections

    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections

    op_notes: list = list(narrative.get("operational_notes") or [])
    conflicting = [n for n in op_notes if isinstance(
        n, str) and _NO_SUBINDUSTRY_PATTERN.search(n)]
    if not conflicting:
        return canonical, corrections

    cleaned = [n for n in op_notes if n not in conflicting]
    correct_note = (
        f"Subindustry overlay confirmed: '{sub_value}' (confidence: High). "
        f"Industry-specific rubric applied."
    )
    cleaned.append(correct_note)

    narrative["operational_notes"] = cleaned
    canonical["narrative"] = narrative
    corrections.append(
        f"AUTO_CORRECTED_SUBINDUSTRY_NOTE: removed {len(conflicting)} conflicting note(s) "
        f"claiming no overlay; injected correct note for '{sub_value}'."
    )
    # Append to processing_warnings so the correction is auditable in the stored artifact
    pws = list(canonical.get("processing_warnings") or [])
    pws.extend(corrections)
    canonical["processing_warnings"] = pws
    return canonical, corrections


def _filter_contradictory_recommendations(canonical: dict) -> tuple[dict, list[str]]:
    """
    Bug 3 fix: remove recommendations whose expected_impact criterion scored
    >= HIGH_SCORE_THRESHOLD with STRONG_DIRECT/DIRECT evidence and High confidence,
    OR that use stage-regressive language when overall_score >= 70.

    Also removes top_concerns that describe a high-scoring/high-confidence criterion
    as having limited/no evidence.

    Returns (modified_canonical, list_of_correction_messages).
    """
    corrections: list[str] = []
    overall_score = (canonical.get("overall_result")
                     or {}).get("overall_score")
    if not isinstance(overall_score, (int, float)):
        return canonical, corrections

    criteria_by_name = _criteria_lookup(canonical)
    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections

    # â”€â”€ Filter recommendations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    recs: list = list(narrative.get("recommendations") or [])
    filtered_recs: list = []
    for rec in recs:
        if not isinstance(rec, dict):
            filtered_recs.append(rec)
            continue

        removed = False

        # Check 1: expected_impact criterion has strong score
        impact = rec.get("expected_impact") or ""
        crit_data = criteria_by_name.get(impact)
        if crit_data:
            score = crit_data.get("final_score")
            ev_strength = crit_data.get("evidence_strength_summary") or ""
            confidence = crit_data.get("confidence")
            if (
                isinstance(score, (int, float))
                and score >= _HIGH_SCORE_THRESHOLD
                and ev_strength in _STRONG_EVIDENCE_LEVELS
                and confidence == "High"
            ):
                corrections.append(
                    f"AUTO_REMOVED_REC: Recommendation targeting '{impact}' removed â€” "
                    f"criterion scored {score:.0f} with {ev_strength}/High confidence. "
                    f"Text: \"{rec.get('recommendation', '')[:80]}...\""
                )
                removed = True

        # Check 2: stage-regressive language when overall is strong
        if not removed and overall_score >= 70:
            rec_text = " ".join(str(v)
                                for v in rec.values() if isinstance(v, str))
            if _STAGE_REGRESSION_PATTERNS.search(rec_text):
                corrections.append(
                    f"AUTO_REMOVED_REC: Stage-regressive recommendation removed "
                    f"(overall_score={overall_score:.1f}). "
                    f"Text: \"{rec.get('recommendation', '')[:80]}...\""
                )
                removed = True

        if not removed:
            filtered_recs.append(rec)

    if len(filtered_recs) < len(recs):
        narrative["recommendations"] = filtered_recs

    # ── Remove contradictory top_concerns (Bug 4 fix) ──────────────────────────
    concerns: list = list(narrative.get("top_concerns") or [])
    filtered_concerns: list = []
    for concern in concerns:
        if not isinstance(concern, str):
            filtered_concerns.append(concern)
            continue
        removed_concern = False
        if _WEAK_EVIDENCE_LANGUAGE.search(concern):
            for crit_name, crit_data in criteria_by_name.items():
                if not _text_mentions_criterion(concern, crit_name):
                    continue
                score = crit_data.get("final_score")
                ev_strength = crit_data.get("evidence_strength_summary") or ""
                if (
                    isinstance(score, (int, float))
                    and score >= _HIGH_SCORE_THRESHOLD
                    and ev_strength in _STRONG_EVIDENCE_LEVELS
                ):
                    corrections.append(
                        "AUTO_REMOVED_CONCERN: top_concern removed — references "
                        f"'{crit_name}' as weak/limited but criterion scored {score:.0f} "
                        f"with {ev_strength}. Concern: \"{concern[:100]}\""
                    )
                    removed_concern = True
                    break
        if not removed_concern:
            filtered_concerns.append(concern)

    if len(filtered_concerns) < len(concerns):
        narrative["top_concerns"] = filtered_concerns

    if corrections:
        canonical["narrative"] = narrative
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws

    return canonical, corrections


def _correct_stage_narrative_contradictions(canonical: dict) -> "tuple[dict, list[str]]":
    """
    Bug 5 / Issue 3 fix: remove top_concerns, recommendations, AND operational_notes
    that use language appropriate for a LOWER stage than what is classified.

    Also removes operational_notes that explicitly claim the stage was set/overridden
    to a stage other than the actual classified stage (e.g. LLM noted
    \"provided stage overridden to PRE_SEED\" when classified stage is SEED).
    """
    corrections: list[str] = []
    stage = ((canonical.get("classification") or {}).get(
        "stage") or {}).get("value") or ""
    stage = stage.upper().strip()
    pattern = _LOWER_STAGE_LANGUAGE.get(stage)

    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections

    filtered_concerns = list(narrative.get("top_concerns") or [])
    filtered_recs = list(narrative.get("recommendations") or [])

    # ── 1. top_concerns ─────────────────────────────────────────────────────
    if pattern is not None:
        new_concerns: list = []
        for concern in filtered_concerns:
            if isinstance(concern, str) and pattern.search(concern):
                corrections.append(
                    f"AUTO_REMOVED_CONCERN: stage contradiction — uses sub-{stage} "
                    f"language for classified stage={stage}. Concern: \"{concern[:120]}\""
                )
            else:
                new_concerns.append(concern)
        filtered_concerns = new_concerns

        # ── 2. recommendations ──────────────────────────────────────────────
        new_recs: list = []
        for rec in filtered_recs:
            if not isinstance(rec, dict):
                new_recs.append(rec)
                continue
            rec_text = " ".join(str(v)
                                for v in rec.values() if isinstance(v, str))
            if pattern.search(rec_text):
                corrections.append(
                    f"AUTO_REMOVED_REC: stage contradiction — uses sub-{stage} "
                    f"language for classified stage={stage}. "
                    f"Text: \"{rec.get('recommendation', '')[:100]}\""
                )
            else:
                new_recs.append(rec)
        filtered_recs = new_recs

    # ── 3. operational_notes: lower-stage language + wrong-stage override ──
    _all_valid_stages = frozenset(
        {"IDEA", "MVP", "PRE_SEED", "SEED", "GROWTH"})
    _other_stages = _all_valid_stages - {stage}
    # Matches "overridden to X" / "set to X" / "classified as X" where X is NOT
    # the actual stage.  Also catches "X stage applied" patterns.
    if _other_stages:
        _other_pat_str = "|".join(re.escape(s) for s in _other_stages)
        _override_to_pattern = re.compile(
            r"\b(?:overrid(?:e|ing|en|den)|set|changed|adjusted|classified)\b"
            r".*?\b(" + _other_pat_str + r")\b"
            r"|\b(" + _other_pat_str + r")\b"
            r".*?\b(?:overrid(?:e|ing|en|den)|stage|classification)\b",
            re.IGNORECASE | re.DOTALL,
        )
    else:
        _override_to_pattern = None

    op_notes: list = list(narrative.get("operational_notes") or [])
    filtered_op: list = []
    for note in op_notes:
        if not isinstance(note, str):
            filtered_op.append(note)
            continue
        removed = False
        # Check lower-stage language pattern
        if pattern is not None and pattern.search(note):
            corrections.append(
                f"AUTO_REMOVED_OP_NOTE: stage contradiction — operational note uses "
                f"sub-{stage} language for classified stage={stage}. "
                f"Note: \"{note[:120]}\""
            )
            removed = True
        # Check wrong-stage override claim
        elif _override_to_pattern is not None and _override_to_pattern.search(note):
            corrections.append(
                f"AUTO_REMOVED_OP_NOTE: operational note claims stage was set to a stage "
                f"other than classified {stage}. Note: \"{note[:120]}\""
            )
            removed = True
        if not removed:
            filtered_op.append(note)

    if corrections or len(filtered_op) < len(op_notes):
        narrative["top_concerns"] = filtered_concerns
        narrative["recommendations"] = filtered_recs
        narrative["operational_notes"] = filtered_op
        canonical["narrative"] = narrative
        if corrections:
            pws = list(canonical.get("processing_warnings") or [])
            pws.extend(corrections)
            canonical["processing_warnings"] = pws

    return canonical, corrections


def _sanitize_narrative_list_fields(canonical: dict) -> tuple[dict, list[str]]:
    """
    RC-2 / Priority-3 defense: when Gemini is constrained to List[str] but prompted
    with a dict example it may emit JSON-serialized dicts as individual strings, e.g.:
      '{"title": "Strong validation", "reason": "...", "evidence_reference": "..."}'
    or plain dict objects that Pydantic coerced to str.

    This pass flattens any such item in top_strengths, top_concerns, and
    operational_notes into human-readable plain strings.

    Returns (modified_canonical, list_of_correction_messages).
    """
    corrections: list[str] = []
    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections

    def _flatten_item(item: Any) -> str:
        """Attempt to extract a readable string from a dict-embedded or JSON-string item."""
        if isinstance(item, str):
            stripped = item.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    obj = json.loads(stripped)
                    if isinstance(obj, dict):
                        # Prefer known meaningful keys; fall back to joining all values.
                        parts = []
                        for key in ("title", "reason", "evidence_reference", "overlay_applied"):
                            if key in obj and obj[key]:
                                parts.append(str(obj[key]))
                        return ". ".join(parts) if parts else stripped
                except (json.JSONDecodeError, ValueError):
                    pass
            return item
        if isinstance(item, dict):
            parts = []
            for key in ("title", "reason", "evidence_reference", "overlay_applied"):
                if key in item and item[key]:
                    parts.append(str(item[key]))
            return ". ".join(parts) if parts else str(item)
        return str(item)

    changed = False
    for field_name in ("top_strengths", "top_concerns", "operational_notes"):
        items = narrative.get(field_name)
        if not isinstance(items, list):
            continue
        new_items = []
        for item in items:
            flattened = _flatten_item(item)
            if flattened != item:
                corrections.append(
                    f"AUTO_FLATTENED_{field_name.upper()}: converted structured item to plain string. "
                    f"Original type: {type(item).__name__}. Result: \"{flattened[:80]}\""
                )
                changed = True
            new_items.append(flattened)
        if changed:
            narrative[field_name] = new_items

    if corrections:
        canonical["narrative"] = narrative
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws

    return canonical, corrections


def _check_malformed_fields(
    canonical: Mapping[str, Any], narrative_texts: list[str]
) -> list[str]:
    """
    Validator: flag any narrative list field that still contains a structured
    object after the sanitize pass (should be empty after _sanitize_narrative_list_fields).
    Also flags operational_notes that look like JSON-serialized dicts.
    """
    flags: list[str] = []
    narrative = canonical.get("narrative") or {}
    for field_name in ("top_strengths", "top_concerns", "operational_notes"):
        for item in (narrative.get(field_name) or []):
            if not isinstance(item, str):
                flags.append(
                    f"MALFORMED_FIELD: {field_name} contains a non-string item "
                    f"(type={type(item).__name__}). "
                    "Sanitize pass may not have run or item was inserted post-sanitize."
                )
                break
            stripped = item.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                flags.append(
                    f"MALFORMED_FIELD: {field_name} item appears to be a JSON-embedded "
                    f"dict string: \"{stripped[:80]}\""
                )
                break
    return flags


def _dedupe_operational_notes(canonical: dict) -> tuple[dict, list[str]]:
    """Issue 5 / Issue 4 fix: remove duplicate strings from narrative.operational_notes
    while preserving order.

    Uses strip-normalised keys so near-duplicates that differ only in leading/trailing
    whitespace are also collapsed. Non-string items are skipped (preserved as-is).
    """
    corrections: list[str] = []
    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections
    notes = narrative.get("operational_notes")
    if not isinstance(notes, list):
        return canonical, corrections

    seen: set = set()
    deduped: list = []
    for n in notes:
        if not isinstance(n, str):
            deduped.append(n)
            continue
        key = n.strip()
        if key not in seen:
            seen.add(key)
            # store stripped form to eliminate whitespace noise
            deduped.append(key)

    if len(deduped) < len(notes):
        removed = len(notes) - len(deduped)
        corrections.append(
            f"AUTO_DEDUPED_OPERATIONAL_NOTES: removed {removed} duplicate/near-duplicate item(s)."
        )
        narrative["operational_notes"] = deduped
        canonical["narrative"] = narrative
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws
    return canonical, corrections


def _sanitize_classification_subindustry_null(canonical: dict) -> tuple[dict, list[str]]:
    """Issue 1 belt-and-suspenders: normalize any placeholder/sentinel string in
    classification.subindustry.value to Python None before Pydantic round-trip.

    Sentinels normalized: 'Unknown', 'unknown', 'null', 'none', 'n/a', 'na',
    '' (empty string), 'undefined', 'other' (when used as a placeholder, not a
    real taxonomy value).
    """
    _NULL_SENTINELS = frozenset(
        {"", "unknown", "null", "none", "n/a", "na", "undefined"})
    corrections: list[str] = []
    classification = canonical.get("classification")
    if not isinstance(classification, dict):
        return canonical, corrections
    subindustry = classification.get("subindustry")
    if not isinstance(subindustry, dict):
        return canonical, corrections
    raw_val = subindustry.get("value")
    if raw_val is not None and str(raw_val).strip().lower() in _NULL_SENTINELS:
        subindustry["value"] = None
        classification["subindustry"] = subindustry
        canonical["classification"] = classification
        corrections.append(
            f"AUTO_NULLIFIED_SUBINDUSTRY: value '{raw_val}' normalized to null."
        )
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws
    return canonical, corrections


def sanitize_canonical_report(canonical: dict) -> dict:
    """
    Auto-correction pass applied BEFORE validation.

    Corrections applied (in order):
      1. Malformed narrative list-field flattening (RC-2 defense)
      2. Classification subindustry null normalization (Issue 1)
      3. Subindustry operational-note auto-fix (Bug 2/3)
      4. Contradictory recommendation + concern filter (Bug 3/4)
      5. Stage-narrative contradiction removal incl. operational_notes (Issue 3)
      6. Operational notes deduplication with strip normalization (Issue 4)

    All corrections are appended to processing_warnings for auditability.
    Mutates and returns the same dict.
    """
    canonical, _ = _sanitize_narrative_list_fields(canonical)
    canonical, _ = _sanitize_classification_subindustry_null(canonical)
    canonical, _ = _correct_subindustry_operational_notes(canonical)
    canonical, _ = _filter_contradictory_recommendations(canonical)
    canonical, _ = _correct_stage_narrative_contradictions(canonical)
    canonical, _ = _dedupe_operational_notes(canonical)
    return canonical


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# VALIDATION PASS  (read-only checks, produces flags)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _check_source_isolation(
    canonical: Mapping[str, Any], narrative_texts: list[str]
) -> list[str]:
    flags: list[str] = []
    doc_type = canonical.get("document_type") or ""
    if doc_type not in ("pitch_deck", "business_plan"):
        return flags

    if doc_type == "pitch_deck":
        contaminated = [t for t in narrative_texts if re.search(
            r"\bbusiness plan\b", t, re.IGNORECASE)]
        if contaminated:
            flags.append(
                f"SOURCE_ISOLATION: pitch_deck report references 'Business Plan' in narrative "
                f"({len(contaminated)} occurrence(s))."
            )
    elif doc_type == "business_plan":
        contaminated = [t for t in narrative_texts if re.search(
            r"\bpitch deck\b", t, re.IGNORECASE)]
        if contaminated:
            flags.append(
                f"SOURCE_ISOLATION: business_plan report references 'Pitch Deck' in narrative "
                f"({len(contaminated)} occurrence(s))."
            )

    cross_doc = [t for t in narrative_texts if _CROSS_DOC_PATTERNS.search(t)]
    if cross_doc:
        flags.append(
            f"SOURCE_ISOLATION: single-source report ({doc_type}) uses cross-document language "
            f"({len(cross_doc)} occurrence(s))."
        )
    return flags


def _check_classification_consistency(canonical: Mapping[str, Any]) -> list[str]:
    """Flag any remaining High-confidence subindustry vs no-overlay note conflict
    (after sanitize_canonical_report should have resolved it)."""
    flags: list[str] = []
    classification = canonical.get("classification") or {}
    subindustry = classification.get("subindustry") or {}
    sub_confidence = subindustry.get("confidence")
    sub_value = (subindustry.get("value") or "").strip()

    if sub_confidence == "High" and sub_value and sub_value not in ("Unknown", "OTHER", ""):
        op_notes = (canonical.get("narrative") or {}
                    ).get("operational_notes") or []
        conflicting = [n for n in op_notes if isinstance(
            n, str) and _NO_SUBINDUSTRY_PATTERN.search(n)]
        if conflicting:
            flags.append(
                f"CLASSIFICATION_CONSISTENCY: subindustry '{sub_value}' resolved with High confidence "
                f"but operational_notes still claim no subindustry was resolvable after auto-correction."
            )
    return flags


def _check_score_narrative_consistency(
    canonical: Mapping[str, Any], narrative_texts: list[str]
) -> list[str]:
    """Residual score/narrative check after auto-correction (should be rare)."""
    flags: list[str] = []
    overall_score = (canonical.get("overall_result")
                     or {}).get("overall_score")
    if not isinstance(overall_score, (int, float)) or overall_score < 70:
        return flags

    narrative = canonical.get("narrative") or {}
    for rec in (narrative.get("recommendations") or []):
        if not isinstance(rec, dict):
            continue
        rec_text = " ".join(str(v) for v in rec.values() if isinstance(v, str))
        if _STAGE_REGRESSION_PATTERNS.search(rec_text):
            flags.append(
                f"SCORE_NARRATIVE_CONSISTENCY: stage-regressive recommendation survived filter "
                f"(overall_score={overall_score:.1f})."
            )
            break

    return flags


def _check_criterion_kq_consistency(canonical: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    criteria_by_name = _criteria_lookup(canonical)
    narrative = canonical.get("narrative") or {}
    for kq in (narrative.get("key_questions") or []):
        if not isinstance(kq, dict):
            continue
        crit_name = kq.get("criterion") or ""
        question_text = kq.get("question") or ""
        crit_data = criteria_by_name.get(crit_name)
        if not crit_data:
            continue
        crit_confidence = crit_data.get("confidence")
        ev_strength = crit_data.get("evidence_strength_summary") or ""
        if (
            crit_confidence == "High"
            and ev_strength in _STRONG_EVIDENCE_LEVELS
            and (_WEAK_EVIDENCE_LANGUAGE.search(question_text) or _ABSENT_EVIDENCE_PATTERN.search(question_text))
        ):
            flags.append(
                f"CRITERION_KQ_CONSISTENCY: key_question for '{crit_name}' implies weak/absent "
                f"evidence but criterion has confidence=High, evidence_strength={ev_strength}."
            )
    return flags


# â”€â”€ Public entry points â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _check_stage_consistency(
    canonical: Mapping[str, Any], narrative_texts: list[str]
) -> list[str]:
    """Bug 5 validator: flag any narrative text that uses lower-stage language
    after sanitize_canonical_report should have removed it."""
    flags: list[str] = []
    stage = ((canonical.get("classification") or {}).get(
        "stage") or {}).get("value") or ""
    stage = stage.upper().strip()
    pattern = _LOWER_STAGE_LANGUAGE.get(stage)
    if pattern is None:
        return flags

    offending = [t for t in narrative_texts if pattern.search(t)]
    if offending:
        flags.append(
            f"STAGE_NARRATIVE_CONTRADICTION: classified stage={stage} but "
            f"{len(offending)} narrative field(s) use lower-stage language. "
            f"Example: \"{offending[0][:120]}\""
        )
    return flags


def validate_canonical_report(canonical: Mapping[str, Any] | None) -> ReportValidity:
    """
    Read-only validation. Call AFTER sanitize_canonical_report().

    Hard failures (is_valid=False):
      - missing startup_id
      - no usable scoring data

    Soft flags (is_valid=True, validation_flags non-empty):
      - source isolation violations
      - residual classification/notes conflicts
      - residual score/narrative inconsistencies
      - criterion/key-question contradictions
    """
    if not canonical or not isinstance(canonical, Mapping):
        return ReportValidity(False, "Canonical report payload is missing.")

    startup_id = str(canonical.get("startup_id") or "").strip()
    if not startup_id:
        return ReportValidity(False, "startup_id is missing in canonical report.")

    overall_score = (canonical.get("overall_result")
                     or {}).get("overall_score")
    has_score = isinstance(overall_score, (int, float))
    if not has_score:
        for criterion in (canonical.get("criteria_results") or []):
            if isinstance(criterion, Mapping) and isinstance(criterion.get("final_score"), (int, float)):
                has_score = True
                break

    if not has_score:
        return ReportValidity(
            False,
            "No usable scoring data: overall_score is null and all criterion final scores are null.",
        )

    narrative_texts = _collect_narrative_texts(canonical)
    flags: list[str] = []
    flags.extend(_check_malformed_fields(canonical, narrative_texts))
    flags.extend(_check_source_isolation(canonical, narrative_texts))
    flags.extend(_check_classification_consistency(canonical))
    flags.extend(_check_score_narrative_consistency(
        canonical, narrative_texts))
    flags.extend(_check_criterion_kq_consistency(canonical))
    flags.extend(_check_stage_consistency(canonical, narrative_texts))

    return ReportValidity(is_valid=True, reason="ok", validation_flags=tuple(flags))
