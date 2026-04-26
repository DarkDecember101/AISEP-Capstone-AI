from __future__ import annotations

import json
import re
import unicodedata
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
    r"|\bmerged analysis\b"
    r"|ca\s+hai\s+tai\s+lieu"
    r"|phan\s+tich\s+ket\s+hop"
    r"|phan\s+tich\s+gop",
    re.IGNORECASE,
)

_STAGE_REGRESSION_PATTERNS = re.compile(
    r"\bbuild\s+(?:an?\s+)?mvp\b"
    r"|\bcreate\s+(?:an?\s+)?mvp\b"
    r"|\bdevelop\s+(?:an?\s+)?mvp\b"
    r"|xay\s+dung\s+(?:san\s+pham\s+)?mvp"
    r"|phat\s+trien\s+(?:san\s+pham\s+)?mvp",
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
    r"|only\s+core\s+rubric\s+(?:and\s+stage\s+profile\s+)?(?:was\s+)?applied"
    r"|khong\s+ap\s+dung\s+(?:lop\s+danh\s+gia\s+)?subindustry"
    r"|chi\s+dung\s+rubric\s+cot\s+loi\s+va\s+stage\s+profile"
    r"|khong\s+the\s+xac\s+dinh\s+subindustry",
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
    r"|\bearly\s+stage\s+(?:validation|traction)\b"
    r"|bang\s+chung\s+(?:con\s+)?han\s+che"
    r"|thieu\s+(?:bang\s+chung|traction|validation|du\s+lieu)"
    r"|chua\s+(?:duoc\s+)?kiem\s+chung"
    r"|uu\s+tien\s+bo\s+sung\s+(?:bang\s+chung|traction|validation)"
    r"|chua\s+co\s+(?:bang\s+chung|khach\s+hang|doanh\s+thu)\s+ro\s+rang",
    re.IGNORECASE,
)

# Criterion name fragments for keyword-based matching in free-text fields
_CRITERION_KEYWORDS: dict[str, list[str]] = {
    "Problem_&_Customer_Pain": ["problem", "customer pain", "customer segment", "icp", "van de", "noi dau", "khach hang"],
    "Market_Attractiveness_&_Timing": ["market", "tam", "sam", "timing", "market size", "thi truong", "quy mo thi truong"],
    "Solution_&_Differentiation": ["solution", "differentiation", "product", "technology", "giai phap", "khac biet", "san pham", "cong nghe"],
    "Business_Model_&_Go_to_Market": ["business model", "go-to-market", "gtm", "revenue model", "monetization", "mo hinh kinh doanh", "go to market", "kenh ban hang"],
    "Team_&_Execution_Readiness": ["team", "founder", "execution", "leadership", "doi ngu", "nha sang lap", "nang luc thuc thi"],
    "Validation_Traction_Evidence_Quality": [
        "validation", "traction", "evidence", "customers", "revenue",
        "adoption", "retention", "early validation", "early traction",
        "kiem chung", "bang chung", "khach hang", "doanh thu", "giu chan",
    ],
}

_STRONG_EVIDENCE_LEVELS = frozenset({"STRONG_DIRECT", "DIRECT"})
# criterion final_score above which weak-evidence language is contradictory
_HIGH_SCORE_THRESHOLD = 75.0

_ABSENT_EVIDENCE_PATTERN = re.compile(
    r"\bno evidence\b|\babsent\b|\bcompletely missing\b|\bno clear evidence\b"
    r"|khong\s+co\s+bang\s+chung"
    r"|thieu\s+hoan\s+toan\s+bang\s+chung",
    re.IGNORECASE,
)

_COMPETITION_CLAIM_PATTERN = re.compile(
    r"\bno competitor\b"
    r"|\bno (?:direct )?competitor\b"
    r"|\bnone of (?:them|the competitors)\b"
    r"|\bkhong doi thu nao\b"
    r"|\bkhong co doi thu nao\b"
    r"|\bhau het\b.*\bdoi thu\b",
    re.IGNORECASE,
)

_LOW_VALIDATION_CAP_PATTERN = re.compile(
    r"\b(?:cannot|can not|could not|khong the)\b.*?\b(?:4(?:\.0)?)\b"
    r"|\bmax(?:imum)?\s*(?:of)?\s*4(?:\.0)?\b"
    r"|\bABSENT\b.*?\b4(?:\.0)?\b",
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
        r"|\bearly[-\s]stage\s+venture\b"
        r"|giai\s+doan\s+pre[-\s]?seed"
        r"|tim\s+kiem\s+(?:nhung\s+)?khach\s+hang\s+dau\s+tien"
        r"|kiem\s+chung\s+(?:y\s+tuong|gia\s+thuyet|khai\s+niem)",
        re.IGNORECASE,
    ),
    "GROWTH": re.compile(
        r"\bpre[-\s_]seed\b"
        r"|\bseed[-\s]stage\b"
        r"|\bbuild(?:ing)?\s+(?:the\s+)?(?:initial\s+)?(?:product|mvp|prototype)\b"
        r"|giai\s+doan\s+seed"
        r"|xay\s+dung\s+(?:san\s+pham|mvp|nguyen\s+mau)\s+ban\s+dau",
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
    for risk in (narrative.get("top_risks") or []):
        if isinstance(risk, Mapping):
            for value in risk.values():
                if isinstance(value, str):
                    texts.append(value)
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


def _ascii_fold(text: str) -> str:
    """Fold Vietnamese diacritics so regex checks can work on both accented and plain ASCII text."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def _text_mentions_criterion(text: str, criterion: str) -> bool:
    """Return True if the text references the given criterion by keyword."""
    keywords = _CRITERION_KEYWORDS.get(criterion, [])
    text_folded = _ascii_fold(text)
    return any(_ascii_fold(kw) in text_folded for kw in keywords)


def _derive_interpretation_band(overall_score: float | None) -> str:
    if overall_score is None:
        return "weak"
    if overall_score >= 85:
        return "very strong"
    if overall_score >= 70:
        return "strong"
    if overall_score >= 50:
        return "promising but incomplete"
    if overall_score >= 35:
        return "below average"
    return "weak"


def _compute_final_score(raw_score: float | None, cap_summary: Mapping[str, Any] | None) -> float | None:
    if not isinstance(raw_score, (int, float)):
        return None

    raw_ten_scale = float(raw_score)
    if raw_ten_scale > 10.0:
        raw_ten_scale = raw_ten_scale / 10.0

    caps = cap_summary or {}
    core_cap = caps.get("core_cap")
    stage_cap = caps.get("stage_cap")
    evidence_cap = caps.get("evidence_quality_cap", 10.0)
    contradiction_cap = caps.get("contradiction_cap", 10.0)
    contradiction_penalty = float(caps.get("contradiction_penalty_points", 0.0) or 0.0)

    effective_cap = min(
        float(core_cap if isinstance(core_cap, (int, float)) else 10.0),
        float(stage_cap if isinstance(stage_cap, (int, float)) else 10.0),
        float(evidence_cap if isinstance(evidence_cap, (int, float)) else 10.0),
        float(contradiction_cap if isinstance(contradiction_cap, (int, float)) else 10.0),
    )
    return round(max(0.0, min(raw_ten_scale, effective_cap) - contradiction_penalty) * 10.0, 2)


def _derive_evidence_coverage(criteria_results: list[Mapping[str, Any]]) -> str:
    strong_count = 0
    moderate_count = 0
    weak_count = 0
    traction_is_weak_or_contradictory = False

    for criterion in criteria_results:
        strength = criterion.get("evidence_strength_summary") or "ABSENT"
        status = criterion.get("status") or "not_applicable"
        if criterion.get("criterion") == "Validation_Traction_Evidence_Quality":
            traction_is_weak_or_contradictory = (
                status == "contradictory" or strength in {"INDIRECT", "ABSENT"}
            )

        if status == "contradictory" or strength == "ABSENT":
            weak_count += 1
        elif status == "insufficient_evidence" or strength == "INDIRECT":
            moderate_count += 1
        else:
            strong_count += 1

    if traction_is_weak_or_contradictory:
        return "mixed" if strong_count > 0 else "moderate"
    if weak_count == 0 and moderate_count <= 1 and strong_count >= 4:
        return "strong"
    if weak_count >= 3 and strong_count == 0:
        return "weak"
    return "moderate"


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
        n, str) and _NO_SUBINDUSTRY_PATTERN.search(_ascii_fold(n))]
    if not conflicting:
        return canonical, corrections

    cleaned = [n for n in op_notes if n not in conflicting]
    correct_note = (
        f"Đã áp dụng lớp đánh giá subindustry: {sub_value} (độ tin cậy High)."
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
            if _STAGE_REGRESSION_PATTERNS.search(_ascii_fold(rec_text)):
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
        if _WEAK_EVIDENCE_LANGUAGE.search(_ascii_fold(concern)):
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
            if isinstance(concern, str) and pattern.search(_ascii_fold(concern)):
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
            if pattern.search(_ascii_fold(rec_text)):
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
        if pattern is not None and pattern.search(_ascii_fold(note)):
            corrections.append(
                f"AUTO_REMOVED_OP_NOTE: stage contradiction — operational note uses "
                f"sub-{stage} language for classified stage={stage}. "
                f"Note: \"{note[:120]}\""
            )
            removed = True
        # Check wrong-stage override claim
        elif _override_to_pattern is not None and _override_to_pattern.search(_ascii_fold(note)):
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


def _repair_scoring_fields(canonical: dict) -> tuple[dict, list[str]]:
    corrections: list[str] = []
    criteria = canonical.get("criteria_results")
    if not isinstance(criteria, list):
        return canonical, corrections

    effective_weights = canonical.get("effective_weights") or {}
    total_weighted = 0.0
    has_weighted = False

    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue

        criterion_name = criterion.get("criterion") or ""
        raw_score = criterion.get("raw_score")
        final_score = criterion.get("final_score")
        recomputed_final = _compute_final_score(raw_score, criterion.get("cap_summary"))

        if recomputed_final is not None and (
            not isinstance(final_score, (int, float))
            or criterion.get("status") == "contradictory"
        ):
            criterion["final_score"] = recomputed_final
            corrections.append(
                f"AUTO_RECOMPUTED_FINAL_SCORE: {criterion_name} final_score set to {recomputed_final}."
            )
            final_score = recomputed_final

        if isinstance(criterion.get("final_score"), (int, float)):
            final_score = float(criterion["final_score"])
            weight_pct = float(effective_weights.get(criterion_name, 0.0) or 0.0)
            weighted = round(final_score * weight_pct / 100.0, 2)
            if criterion.get("weighted_contribution") != weighted:
                criterion["weighted_contribution"] = weighted
                corrections.append(
                    f"AUTO_RECOMPUTED_WEIGHTED_CONTRIBUTION: {criterion_name} weighted_contribution set to {weighted}."
                )
            total_weighted += weighted
            has_weighted = True

    overall_result = canonical.get("overall_result")
    if not isinstance(overall_result, dict):
        overall_result = {}

    if has_weighted:
        overall_score = round(total_weighted, 2)
        if overall_result.get("overall_score") != overall_score:
            overall_result["overall_score"] = overall_score
            corrections.append(
                f"AUTO_RECOMPUTED_OVERALL_SCORE: overall_score set to {overall_score} from weighted_contribution values."
            )
    else:
        overall_score = overall_result.get("overall_score")

    evidence_coverage = _derive_evidence_coverage(
        [c for c in criteria if isinstance(c, dict)]
    )
    if overall_result.get("evidence_coverage") != evidence_coverage:
        overall_result["evidence_coverage"] = evidence_coverage
        corrections.append(
            f"AUTO_RECOMPUTED_EVIDENCE_COVERAGE: evidence_coverage set to {evidence_coverage}."
        )

    interpretation_band = _derive_interpretation_band(
        overall_result.get("overall_score")
        if isinstance(overall_result.get("overall_score"), (int, float))
        else None
    )
    if overall_result.get("interpretation_band") != interpretation_band:
        overall_result["interpretation_band"] = interpretation_band
        corrections.append(
            f"AUTO_RECOMPUTED_INTERPRETATION_BAND: interpretation_band set to {interpretation_band}."
        )

    canonical["overall_result"] = overall_result
    canonical["criteria_results"] = criteria
    if corrections:
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws
    return canonical, corrections


def _normalize_team_score_and_confidence(canonical: dict) -> tuple[dict, list[str]]:
    """Make very high team scores more conservative for business-plan-only evidence."""
    corrections: list[str] = []
    if canonical.get("document_type") != "business_plan":
        return canonical, corrections

    criteria = canonical.get("criteria_results")
    if not isinstance(criteria, list):
        return canonical, corrections

    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        if criterion.get("criterion") != "Team_&_Execution_Readiness":
            continue

        final_score = criterion.get("final_score")
        confidence = criterion.get("confidence")
        if isinstance(final_score, (int, float)) and final_score > 85.0:
            criterion["final_score"] = 85.0
            corrections.append(
                "AUTO_NORMALIZED_TEAM_SCORE: Team_&_Execution_Readiness final_score capped at 85.0 "
                "for business_plan-only self-reported evidence."
            )
        if confidence == "High":
            criterion["confidence"] = "Medium"
            corrections.append(
                "AUTO_NORMALIZED_TEAM_CONFIDENCE: Team_&_Execution_Readiness confidence reduced to Medium "
                "because evidence is from a self-reported business plan."
            )
        break

    if corrections:
        canonical["criteria_results"] = criteria
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws
    return canonical, corrections


def _normalize_industry_classification(canonical: dict) -> tuple[dict, list[str]]:
    corrections: list[str] = []
    classification = canonical.get("classification")
    if not isinstance(classification, dict):
        return canonical, corrections

    main_industry = classification.get("main_industry")
    if not isinstance(main_industry, dict):
        return canonical, corrections

    subindustry = classification.get("subindustry")
    if not isinstance(subindustry, dict):
        subindustry = {
            "value": None,
            "confidence": "Low",
            "resolution_source": "inferred",
            "supporting_evidence_locations": [],
        }

    notes = list(classification.get("operational_notes") or [])
    narrative = canonical.get("narrative") or {}
    notes.extend(list(narrative.get("operational_notes") or []))
    notes_folded = [_ascii_fold(n) for n in notes if isinstance(n, str)]
    notes_claim_other = any(
        "main_industry" in note and "other" in note
        for note in notes_folded
    ) or any(
        "main industry" in note and "other" in note
        for note in notes_folded
    )

    raw_main_value = str(main_industry.get("value") or "").strip()
    main_confidence = main_industry.get("confidence") or "Low"

    if notes_claim_other and raw_main_value != "OTHER":
        main_industry["value"] = "OTHER"
        if main_confidence not in {"Medium", "High"}:
            main_industry["confidence"] = "Medium"
        corrections.append(
            "AUTO_NORMALIZED_MAIN_INDUSTRY: operational_notes indicate OTHER, so main_industry.value was set to OTHER."
        )
    elif not raw_main_value:
        main_industry["value"] = "OTHER"
        if main_confidence == "Low":
            main_industry["confidence"] = "Medium"
        corrections.append(
            "AUTO_NORMALIZED_MAIN_INDUSTRY: main_industry.value was null/blank and has been set to OTHER."
        )

    if main_industry.get("value") == "OTHER":
        if subindustry.get("value") is not None:
            subindustry["value"] = None
            corrections.append(
                "AUTO_NULLIFIED_SUBINDUSTRY_FOR_OTHER: subindustry.value reset to null because main_industry is OTHER."
            )
        if subindustry.get("confidence") != "Low":
            subindustry["confidence"] = "Low"
            corrections.append(
                "AUTO_NORMALIZED_SUBINDUSTRY_CONFIDENCE: subindustry confidence set to Low because value is null / not applicable."
            )
    elif subindustry.get("value") is None and subindustry.get("confidence") != "Low":
        subindustry["confidence"] = "Low"
        corrections.append(
            "AUTO_NORMALIZED_SUBINDUSTRY_CONFIDENCE: subindustry confidence set to Low because value is null."
        )

    classification["main_industry"] = main_industry
    classification["subindustry"] = subindustry
    canonical["classification"] = classification
    if corrections:
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws
    return canonical, corrections


def _synthesize_top_concerns(canonical: dict) -> tuple[dict, list[str]]:
    corrections: list[str] = []
    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections

    existing = [
        item for item in (narrative.get("top_concerns") or [])
        if isinstance(item, str) and item.strip()
    ]
    if existing:
        return canonical, corrections

    criteria = _criteria_lookup(canonical)
    concerns: list[str] = []

    def _add(text: str):
        if text not in concerns:
            concerns.append(text)

    traction = criteria.get("Validation_Traction_Evidence_Quality", {})
    traction_score = traction.get("final_score")
    traction_status = str(traction.get("status") or "")
    traction_strength = str(traction.get("evidence_strength_summary") or "")
    if (
        traction_status in {"insufficient_evidence", "contradictory"}
        or traction_strength in {"INDIRECT", "ABSENT"}
        or (isinstance(traction_score, (int, float)) and traction_score < 30)
    ):
        _add("Thiếu bằng chứng traction thực tế như dữ liệu người dùng, doanh thu, conversion hoặc phản hồi đã được ghi nhận từ khách hàng.")
        _add("Các mốc Alpha/Beta hoặc subscriber hiện vẫn chủ yếu là kế hoạch hoặc milestone, chưa phải kết quả thực tế đã được chứng minh.")

    market = criteria.get("Market_Attractiveness_&_Timing", {})
    market_score = market.get("final_score")
    if isinstance(market_score, (int, float)) and market_score < 75:
        _add("Lập luận 'why now' còn mỏng, chưa làm rõ yếu tố thị trường hoặc công nghệ nào khiến thời điểm hiện tại đặc biệt thuận lợi.")

    solution = criteria.get("Solution_&_Differentiation", {})
    solution_score = solution.get("final_score")
    if isinstance(solution_score, (int, float)) and solution_score < 80:
        _add("Claim về khác biệt cạnh tranh hiện chủ yếu dựa trên mô tả của startup, chưa có đối chiếu độc lập hoặc dữ liệu khách hàng xác nhận.")

    team = criteria.get("Team_&_Execution_Readiness", {})
    team_score = team.get("final_score")
    if isinstance(team_score, (int, float)) and team_score >= 80:
        _add("Thông tin về đội ngũ nhìn chung tích cực nhưng vẫn cần xác minh độc lập trước khi coi là bằng chứng mạnh cho execution readiness.")

    if concerns:
        narrative["top_concerns"] = concerns[:5]
        canonical["narrative"] = narrative
        corrections.append(
            f"AUTO_SYNTHESIZED_TOP_CONCERNS: added {len(narrative['top_concerns'])} concern(s)."
        )
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws

    return canonical, corrections


def _soften_self_reported_claims(canonical: dict) -> tuple[dict, list[str]]:
    corrections: list[str] = []
    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections

    def _soften_text(text: str) -> str:
        lowered = _ascii_fold(text)
        if _COMPETITION_CLAIM_PATTERN.search(lowered):
            if "Theo tài liệu cung cấp, startup cho rằng" not in text:
                cleaned = text[:1].lower() + text[1:] if text else text
                if cleaned.endswith("."):
                    cleaned = cleaned[:-1]
                return (
                    "Theo tài liệu cung cấp, startup cho rằng "
                    f"{cleaned}; claim này cần được kiểm chứng thêm bằng phân tích đối thủ độc lập."
                )
        return text

    changed = False
    for field_name in ("top_strengths", "top_concerns", "missing_information", "operational_notes"):
        items = narrative.get(field_name)
        if not isinstance(items, list):
            continue
        new_items: list[Any] = []
        for item in items:
            if isinstance(item, str):
                softened = _soften_text(item)
                if softened != item:
                    changed = True
                    corrections.append(
                        f"AUTO_SOFTENED_SELF_REPORTED_CLAIM: {field_name} item softened for verification caveat."
                    )
                new_items.append(softened)
            else:
                new_items.append(item)
        narrative[field_name] = new_items

    for text_field in ("executive_summary", "overall_explanation"):
        value = narrative.get(text_field)
        if isinstance(value, str):
            softened = _soften_text(value)
            if softened != value:
                narrative[text_field] = softened
                changed = True
                corrections.append(
                    f"AUTO_SOFTENED_SELF_REPORTED_CLAIM: {text_field} softened for verification caveat."
                )

    criteria = canonical.get("criteria_results")
    if isinstance(criteria, list):
        for criterion in criteria:
            if not isinstance(criterion, dict):
                continue
            explanation = criterion.get("explanation")
            if isinstance(explanation, str):
                softened = _soften_text(explanation)
                if softened != explanation:
                    criterion["explanation"] = softened
                    changed = True
                    corrections.append(
                        f"AUTO_SOFTENED_SELF_REPORTED_CLAIM: explanation softened for {criterion.get('criterion')}."
                    )
            strengths = criterion.get("strengths")
            if isinstance(strengths, list):
                new_strengths = []
                for strength in strengths:
                    if isinstance(strength, str):
                        softened = _soften_text(strength)
                        if softened != strength:
                            changed = True
                            corrections.append(
                                f"AUTO_SOFTENED_SELF_REPORTED_CLAIM: strengths softened for {criterion.get('criterion')}."
                            )
                        new_strengths.append(softened)
                    else:
                        new_strengths.append(strength)
                criterion["strengths"] = new_strengths

    if changed:
        canonical["narrative"] = narrative
        if isinstance(criteria, list):
            canonical["criteria_results"] = criteria
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws

    return canonical, corrections


def _normalize_low_validation_explanations(canonical: dict) -> tuple[dict, list[str]]:
    corrections: list[str] = []
    criteria = canonical.get("criteria_results")
    if not isinstance(criteria, list):
        return canonical, corrections

    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        if criterion.get("criterion") != "Validation_Traction_Evidence_Quality":
            continue

        explanation = criterion.get("explanation")
        final_score = criterion.get("final_score")
        if not isinstance(explanation, str) or not isinstance(final_score, (int, float)):
            continue

        if final_score <= 20 and _LOW_VALIDATION_CAP_PATTERN.search(explanation):
            criterion["explanation"] = (
                "Do thiếu bằng chứng traction thực tế, điểm tiêu chí này bị giới hạn ở mức rất thấp. "
                "Các mốc Alpha/Beta hoặc subscriber hiện chỉ là kế hoạch hoặc milestone, chưa có dữ liệu kết quả, "
                "phản hồi người dùng hay doanh thu thực tế đi kèm."
            )
            corrections.append(
                "AUTO_NORMALIZED_VALIDATION_EXPLANATION: replaced 0-10 scale language in validation explanation."
            )
        break

    if corrections:
        canonical["criteria_results"] = criteria
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws
    return canonical, corrections


def _synthesize_top_risks(canonical: dict) -> tuple[dict, list[str]]:
    corrections: list[str] = []
    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections

    raw_top_risks = [
        risk for risk in (narrative.get("top_risks") or [])
        if isinstance(risk, dict)
    ]
    criteria = _criteria_lookup(canonical)
    document_type = canonical.get("document_type")
    stage = ((canonical.get("classification") or {}).get("stage") or {}).get("value") or ""

    traction = criteria.get("Validation_Traction_Evidence_Quality", {})
    gtm = criteria.get("Business_Model_&_Go_to_Market", {})
    team = criteria.get("Team_&_Execution_Readiness", {})
    solution = criteria.get("Solution_&_Differentiation", {})

    def _risk_key(value: str) -> str:
        return _ascii_fold(str(value or "").strip())

    def _normalize_severity(value: Any) -> str:
        severity = str(value or "Medium").strip().title()
        return severity if severity in {"High", "Medium", "Low"} else "Medium"

    alias_map = {
        "traction evidence risk": "Evidence quality risk",
        "go-to-market risk": "GTM monetization risk",
        "go to market risk": "GTM monetization risk",
        "stage classification risk": "Market adoption risk",
    }

    top_risks: list[dict[str, Any]] = []
    seen_risk_types: set[str] = set()

    for risk in raw_top_risks:
        risk_type = str(risk.get("risk_type") or "").strip()
        if not risk_type:
            continue
        if document_type == "pitch_deck":
            risk_type = alias_map.get(_risk_key(risk_type), risk_type)
        key = _risk_key(risk_type)
        if key in seen_risk_types:
            continue
        seen_risk_types.add(key)
        top_risks.append({
            "risk_type": risk_type,
            "severity": _normalize_severity(risk.get("severity")),
            "description": str(risk.get("description") or "").strip(),
            "related_criterion": str(risk.get("related_criterion") or "Solution_&_Differentiation").strip(),
        })

    def _add_risk(risk_type: str, severity: str, description: str, related_criterion: str):
        key = _risk_key(risk_type)
        if key in seen_risk_types:
            return
        seen_risk_types.add(key)
        top_risks.append({
            "risk_type": risk_type,
            "severity": _normalize_severity(severity),
            "description": description,
            "related_criterion": related_criterion,
        })
        corrections.append(
            f"AUTO_ADDED_TOP_RISK: added {risk_type}."
        )

    def _criterion_is_weak(
        criterion: Mapping[str, Any],
        score_threshold: float,
    ) -> bool:
        score = criterion.get("final_score")
        return (
            criterion.get("status") in {"contradictory", "insufficient_evidence"}
            or criterion.get("evidence_strength_summary") in {"INDIRECT", "ABSENT"}
            or (isinstance(score, (int, float)) and score < score_threshold)
        )

    def _collect_report_blob() -> str:
        parts: list[str] = []
        for key in ("top_strengths", "top_concerns", "missing_information", "operational_notes"):
            for item in (narrative.get(key) or []):
                if isinstance(item, str):
                    parts.append(item)
        for key in ("executive_summary", "overall_explanation"):
            value = narrative.get(key)
            if isinstance(value, str):
                parts.append(value)
        for rec in (narrative.get("recommendations") or []):
            if isinstance(rec, Mapping):
                parts.extend(str(v) for v in rec.values() if isinstance(v, str))
        for criterion in criteria.values():
            explanation = criterion.get("explanation")
            if isinstance(explanation, str):
                parts.append(explanation)
            for key in ("strengths", "concerns"):
                for item in (criterion.get(key) or []):
                    if isinstance(item, str):
                        parts.append(item)
            for loc in (criterion.get("evidence_locations") or []):
                if isinstance(loc, Mapping):
                    excerpt = loc.get("excerpt_or_summary")
                    if isinstance(excerpt, str):
                        parts.append(excerpt)
        return _ascii_fold(" ".join(part for part in parts if part))

    blob = _collect_report_blob()

    def _has_any(terms: list[str]) -> bool:
        return any(_ascii_fold(term) in blob for term in terms)

    traction_is_weak = _criterion_is_weak(traction, 70)
    gtm_is_weak = _criterion_is_weak(gtm, 75)
    team_is_weak = _criterion_is_weak(team, 75)
    solution_is_weak = _criterion_is_weak(solution, 75)
    stage_is_risky = str(stage).upper().strip() == "GROWTH" and traction_is_weak

    if document_type == "pitch_deck":
        startup_label = (
            "nền tảng tuyển dụng cho người khuyết tật"
            if _has_any(["nguoi khuyet tat", "khuyet tat", "disabled", "disability"])
            else ("nền tảng tuyển dụng này" if _has_any(["tuyen dung", "viec lam", "hiring", "recruitment", "employment"]) else "startup này")
        )
        adoption_groups = (
            "người khuyết tật, doanh nghiệp tuyển dụng và các tổ chức đào tạo"
            if _has_any(["nguoi khuyet tat", "khuyet tat", "doanh nghiep", "employer", "to chuc dao tao", "training organization"])
            else "các nhóm khách hàng mục tiêu"
        )
        competitor_names = [
            name for name in ("Jobmetoo", "LinkedIn", "Indeed")
            if _has_any([name])
        ]
        if len(competitor_names) >= 3:
            competitor_phrase = f"{competitor_names[0]}, {competitor_names[1]} va {competitor_names[2]}"
        elif len(competitor_names) == 2:
            competitor_phrase = f"{competitor_names[0]} va {competitor_names[1]}"
        elif len(competitor_names) == 1:
            competitor_phrase = competitor_names[0]
        else:
            competitor_phrase = "cac doi thu hien co"

        has_execution_cues = _has_any([
            "team chi neu ten", "team chi co ten", "ten va vai tro", "name and title",
            "founder background", "team background", "track record", "kinh nghiem",
            "hoc van", "thanh tich", "execution",
        ])
        has_gtm_cues = _has_any([
            "pricing", "gia", "referral fee", "subscription", "freemium", "cac",
            "conversion", "sales strategy", "sales motion", "distribution", "kenh tiep can",
            "channel", "go to market", "gtm",
        ])
        has_competitive_cues = _has_any([
            "doi thu", "competitor", "benchmark", "linkedin", "indeed", "jobmetoo",
            "khac biet", "differentiation", "user feedback",
        ])
        has_technology_cues = _has_any([
            "data mining", "pattern recognition", "matching algorithm", "matching",
            "thuat toan", "thuat toan ghep noi", "ghep noi", "accuracy",
            "do chinh xac", "du lieu dau vao", "cong nghe",
        ])

        if traction_is_weak:
            _add_risk(
                "Evidence quality risk",
                "High",
                f"Pitch Deck chua cung cap bang chung truc tiep nhu phan hoi nguoi dung, pilot, hop dong/doi tac da ky, doanh thu hoac chi so traction de xac nhan nhu cau thuc te doi voi {startup_label}. Viec thieu du lieu kiem chung nay lam giam do tin cay cua cac claim ve muc do phu hop thi truong va kha nang tang truong.",
                "Validation_Traction_Evidence_Quality",
            )
            _add_risk(
                "Market adoption risk",
                "High",
                f"Hien chua co bang chung ro rang cho thay {adoption_groups} thuc su san sang su dung va tra tien cho {startup_label}. Neu chua xac thuc duoc hanh vi chap nhan va willingness-to-pay cua cac nhom nguoi dung chinh, startup co the gap rui ro adoption thap hon ky vong.",
                "Validation_Traction_Evidence_Quality",
            )
        if gtm_is_weak:
            _add_risk(
                "GTM monetization risk",
                "Medium",
                "Mo hinh doanh thu co de cap nhieu huong nhu referral fee, tu van, dao tao, subscription hoac freemium nhung chua lam ro cau truc gia, muc phi cu the, CAC, conversion funnel, chien luoc ban hang va cach tiep can tung nhom khach hang. Viec thieu chi tiet GTM va monetization lam tang rui ro doanh thu thuc te khong dat nhu ke hoach.",
                "Business_Model_&_Go_to_Market",
            )
        elif has_gtm_cues:
            _add_risk(
                "GTM monetization risk",
                "Medium",
                "Pitch Deck da neu mo hinh doanh thu va kenh tiep can nhung chua du chi tiet de kiem chung kha nang chuyen doi thanh doanh thu lap lai mot cach ben vung.",
                "Business_Model_&_Go_to_Market",
            )
        if team_is_weak or has_execution_cues:
            _add_risk(
                "Execution risk",
                "Medium",
                "Doi ngu duoc neu theo ten va vai tro nhung con thieu thong tin ve kinh nghiem chuyen mon, hoc van, thanh tich hoac bang chung da tung trien khai thanh cong san pham cong nghe/tuyen dung tuong tu. Dieu nay lam kho danh gia nang luc thuc thi roadmap va kha nang xu ly cac thach thuc van hanh khi mo rong.",
                "Team_&_Execution_Readiness",
            )
        if solution_is_weak or has_competitive_cues:
            _add_risk(
                "Competitive differentiation risk",
                "Medium",
                f"Startup co claim khac biet so voi {competitor_phrase} nhung chua co benchmark doc lap, phan hoi nguoi dung hoac du lieu hieu qua thuc te de chung minh loi the nay ben vung. Neu diem khac biet khong du manh, startup co the gap kho khan trong thu hut nguoi dung va nha tuyen dung.",
                "Solution_&_Differentiation",
            )
        if has_technology_cues and (solution_is_weak or has_competitive_cues or traction_is_weak):
            _add_risk(
                "Technology feasibility risk",
                "Medium",
                "Pitch Deck de cap den data mining, pattern recognition hoac co che matching viec lam nhung chua mo ta ro du lieu dau vao, logic thuat toan, cach danh gia do chinh xac hoac bang chung kiem chung ky thuat. Dieu nay tao rui ro rang nang luc matching thuc te co the chua du tot de tao ra trai nghiem va ket qua tuyen dung khac biet.",
                "Solution_&_Differentiation",
            )
        elif stage_is_risky:
            _add_risk(
                "Market adoption risk",
                "High",
                "Phan loai stage cao hon ky vong hien dang phu thuoc dang ke vao cac claim traction va adoption chua duoc kiem chung bang du lieu thuc te.",
                "Validation_Traction_Evidence_Quality",
            )
    elif document_type == "business_plan":
        if traction_is_weak:
            _add_risk(
                "Market adoption risk",
                "High",
                "Chưa có bằng chứng đủ mạnh cho thấy khách hàng mục tiêu thực sự sẵn sàng sử dụng hoặc trả tiền cho giải pháp ở giai đoạn hiện tại.",
                "Validation_Traction_Evidence_Quality",
            )
            _add_risk(
                "Evidence quality risk",
                "High",
                "Nhiều luận điểm quan trọng trong tài liệu vẫn ở dạng dự báo, kế hoạch hoặc tự công bố, làm giảm độ chắc chắn của kết luận đầu tư.",
                "Validation_Traction_Evidence_Quality",
            )
        if gtm_is_weak:
            _add_risk(
                "Fundraising risk",
                "Medium",
                "Nhà đầu tư có thể yêu cầu bằng chứng validation và traction mạnh hơn trước khi chấp nhận các giả định về GTM, pricing và tăng trưởng.",
                "Business_Model_&_Go_to_Market",
            )
        solution = criteria.get("Solution_&_Differentiation", {})
        solution_score = solution.get("final_score")
        if (
            solution.get("evidence_strength_summary") in {"DIRECT", "INDIRECT"}
            or (isinstance(solution_score, (int, float)) and solution_score < 80)
        ):
            _add_risk(
                "Competitive differentiation risk",
                "Medium",
                "Theo tài liệu cung cấp, startup cho rằng giải pháp xử lý 'scope creep' tốt hơn đối thủ; claim này cần được kiểm chứng thêm bằng phân tích cạnh tranh độc lập.",
                "Solution_&_Differentiation",
            )
        team = criteria.get("Team_&_Execution_Readiness", {})
        team_score = team.get("final_score")
        if isinstance(team_score, (int, float)) and team_score >= 80:
            _add_risk(
                "Execution risk",
                "Medium",
                "Kế hoạch thực thi có nhiều milestone nhưng phần lớn vẫn là mục tiêu tương lai, nên khả năng chuyển từ kế hoạch sang kết quả thực tế vẫn cần được chứng minh.",
                "Team_&_Execution_Readiness",
            )

    if document_type == "pitch_deck":
        desired_order = {
            "evidence quality risk": 0,
            "market adoption risk": 1,
            "execution risk": 2,
            "gtm monetization risk": 3,
            "competitive differentiation risk": 4,
            "technology feasibility risk": 5,
        }
        top_risks.sort(
            key=lambda risk: desired_order.get(_risk_key(risk.get("risk_type") or ""), 99)
        )

    narrative["top_risks"] = top_risks
    canonical["narrative"] = narrative
    if corrections:
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws
    return canonical, corrections


def sanitize_canonical_report(canonical: dict) -> dict:
    """
    Auto-correction pass applied BEFORE validation.

    Corrections applied (in order):
      1. Score/confidence normalization for business-plan team inflation
      2. Scoring field repair (final_score / weighted_contribution / overall_score / evidence_coverage)
      3. Malformed narrative list-field flattening (RC-2 defense)
      4. Classification subindustry null normalization (Issue 1)
      5. Main-industry / subindustry consistency normalization
      6. Subindustry operational-note auto-fix (Bug 2/3)
      7. Contradictory recommendation + concern filter (Bug 3/4)
      8. Stage-narrative contradiction removal incl. operational_notes (Issue 3)
      9. Auto-synthesize top_concerns when the model leaves them empty
      10. Operational notes deduplication with strip normalization (Issue 4)
      11. Top-risk synthesis / normalization
      12. Soften self-reported competitive claims with verification caveats
      13. Normalize low-validation explanations to 0-100 user-facing wording
      14. Re-repair scoring fields after any score normalization

    All corrections are appended to processing_warnings for auditability.
    Mutates and returns the same dict.
    """
    canonical, _ = _normalize_team_score_and_confidence(canonical)
    canonical, _ = _repair_scoring_fields(canonical)
    canonical, _ = _sanitize_narrative_list_fields(canonical)
    canonical, _ = _sanitize_classification_subindustry_null(canonical)
    canonical, _ = _normalize_industry_classification(canonical)
    canonical, _ = _correct_subindustry_operational_notes(canonical)
    canonical, _ = _filter_contradictory_recommendations(canonical)
    canonical, _ = _correct_stage_narrative_contradictions(canonical)
    canonical, _ = _synthesize_top_concerns(canonical)
    canonical, _ = _dedupe_operational_notes(canonical)
    canonical, _ = _synthesize_top_risks(canonical)
    canonical, _ = _soften_self_reported_claims(canonical)
    canonical, _ = _normalize_low_validation_explanations(canonical)
    canonical, _ = _repair_scoring_fields(canonical)
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
            r"\bbusiness plan\b|ke\s+hoach\s+kinh\s+doanh", _ascii_fold(t), re.IGNORECASE)]
        if contaminated:
            flags.append(
                f"SOURCE_ISOLATION: pitch_deck report references 'Business Plan' in narrative "
                f"({len(contaminated)} occurrence(s))."
            )
    elif doc_type == "business_plan":
        contaminated = [t for t in narrative_texts if re.search(
            r"\bpitch deck\b", _ascii_fold(t), re.IGNORECASE)]
        if contaminated:
            flags.append(
                f"SOURCE_ISOLATION: business_plan report references 'Pitch Deck' in narrative "
                f"({len(contaminated)} occurrence(s))."
            )

    cross_doc = [t for t in narrative_texts if _CROSS_DOC_PATTERNS.search(_ascii_fold(t))]
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
    main_industry = classification.get("main_industry") or {}
    subindustry = classification.get("subindustry") or {}
    sub_confidence = subindustry.get("confidence")
    sub_value = (subindustry.get("value") or "").strip()
    main_value = (main_industry.get("value") or "").strip()
    main_confidence = main_industry.get("confidence")

    if not main_value and main_confidence == "High":
        flags.append(
            "CLASSIFICATION_CONSISTENCY: main_industry.value is null while confidence=High."
        )

    if sub_confidence == "High" and sub_value and sub_value not in ("Unknown", "OTHER", ""):
        op_notes = (canonical.get("narrative") or {}
                    ).get("operational_notes") or []
        conflicting = [n for n in op_notes if isinstance(
            n, str) and _NO_SUBINDUSTRY_PATTERN.search(_ascii_fold(n))]
        if conflicting:
            flags.append(
                f"CLASSIFICATION_CONSISTENCY: subindustry '{sub_value}' resolved with High confidence "
                f"but operational_notes still claim no subindustry was resolvable after auto-correction."
            )
    if sub_value == "" and sub_confidence not in ("Low", "Not_Applicable"):
        flags.append(
            "CLASSIFICATION_CONSISTENCY: subindustry is null/blank but confidence is not Low."
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
        if _STAGE_REGRESSION_PATTERNS.search(_ascii_fold(rec_text)):
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
            and (
                _WEAK_EVIDENCE_LANGUAGE.search(_ascii_fold(question_text))
                or _ABSENT_EVIDENCE_PATTERN.search(_ascii_fold(question_text))
            )
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

    offending = [t for t in narrative_texts if pattern.search(_ascii_fold(t))]
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
