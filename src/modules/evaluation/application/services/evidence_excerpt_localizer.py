from __future__ import annotations

import ast
import json
import re
from typing import Any, Callable

from src.modules.evaluation.application.dto.pipeline_schema import (
    ClassificationResult,
    EvidenceMappingResult,
)

_ENGLISH_CUE_WORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "allows",
    "allow", "users", "user", "customers", "customer", "company", "purpose",
    "solution", "market", "revenue", "growth", "competitive", "content",
    "video", "upload", "share", "browse", "business", "team", "product",
    "problem", "consumers", "viewers", "internet", "platform", "domain",
    "expertise", "traction", "financial", "monthly", "annual", "founded",
    "distribution", "technology", "pain",
})
_VIETNAMESE_MARKERS = set(
    "\u0103\u00e2\u0111\u00ea\u00f4\u01a1\u01b0"
    "\u00e1\u00e0\u1ea3\u00e3\u1ea1\u1ea5\u1ea7\u1ea9\u1eab\u1ead\u1eaf\u1eb1\u1eb3\u1eb5\u1eb7"
    "\u00e9\u00e8\u1ebb\u1ebd\u1eb9\u1ebf\u1ec1\u1ec3\u1ec5\u1ec7"
    "\u00ed\u00ec\u1ec9\u0129\u1ecb"
    "\u00f3\u00f2\u1ecf\u00f5\u1ecd\u1ed1\u1ed3\u1ed5\u1ed7\u1ed9\u1edb\u1edd\u1edf\u1ee1\u1ee3"
    "\u00fa\u00f9\u1ee7\u0169\u1ee5\u1ee9\u1eeb\u1eed\u1eef\u1ef1"
    "\u00fd\u1ef3\u1ef7\u1ef9\u1ef5"
)


def parse_supporting_evidence_location(raw_loc: Any) -> tuple[str, int, str | None]:
    if isinstance(raw_loc, str):
        stripped = raw_loc.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            parsed = None
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                pass
            if parsed is None:
                try:
                    parsed = ast.literal_eval(stripped)
                except (ValueError, SyntaxError):
                    pass
            if isinstance(parsed, dict):
                excerpt = parsed.get("excerpt_or_summary") or stripped
                page = parsed.get("slide_number_or_page_number") or 1
                section_name = parsed.get("section_name")
                return str(excerpt), int(page) if page else 1, section_name

        match = re.search(r"\d+", raw_loc)
        return raw_loc, int(match.group(0)) if match else 1, None

    if hasattr(raw_loc, "model_dump"):
        data = raw_loc.model_dump()
        return (
            str(data.get("excerpt_or_summary", str(raw_loc))),
            int(data.get("slide_number_or_page_number", 1) or 1),
            data.get("section_name"),
        )

    if isinstance(raw_loc, dict):
        return (
            str(raw_loc.get("excerpt_or_summary", str(raw_loc))),
            int(raw_loc.get("slide_number_or_page_number", 1) or 1),
            raw_loc.get("section_name"),
        )

    return str(raw_loc), 1, None


def should_localize_excerpt(text: str) -> bool:
    cleaned = " ".join(str(text).split()).strip()
    if len(cleaned) < 20:
        return False

    lowered = cleaned.lower()
    if any(ch in lowered for ch in _VIETNAMESE_MARKERS):
        return False

    tokens = re.findall(r"[a-zA-Z]+", lowered)
    if len(tokens) < 4:
        return False

    cue_hits = sum(1 for token in tokens if token in _ENGLISH_CUE_WORDS)
    ascii_ratio = sum(1 for ch in cleaned if ord(ch) < 128) / max(len(cleaned), 1)
    return ascii_ratio >= 0.85 and (
        cue_hits >= 2 or (cue_hits >= 1 and (":" in cleaned or len(tokens) >= 6))
    )


def _normalize_excerpt_text(text: str) -> str:
    return " ".join(str(text).split()).strip()


def _collect_candidate_excerpts(
    classification_res: ClassificationResult,
    evidence_res: EvidenceMappingResult,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(text: str):
        normalized = _normalize_excerpt_text(text)
        if not normalized or normalized in seen or not should_localize_excerpt(normalized):
            return
        seen.add(normalized)
        candidates.append(normalized)

    for field_name in ("stage", "main_industry", "subindustry"):
        field = getattr(classification_res, field_name, None)
        if not field:
            continue
        for raw_loc in getattr(field, "supporting_evidence_locations", None) or []:
            excerpt, _, _ = parse_supporting_evidence_location(raw_loc)
            _add(excerpt)

    for criterion in evidence_res.criteria_evidence:
        for unit in list(criterion.evidence_units) + list(criterion.weakening_evidence_units):
            _add(unit.excerpt_or_summary)

    return candidates


def _update_classification_location(raw_loc: Any, new_excerpt: str) -> str:
    if isinstance(raw_loc, str):
        stripped = raw_loc.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            parsed = None
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                pass
            if parsed is None:
                try:
                    parsed = ast.literal_eval(stripped)
                except (ValueError, SyntaxError):
                    pass
            if isinstance(parsed, dict):
                parsed["excerpt_or_summary"] = new_excerpt
                return json.dumps(parsed, ensure_ascii=False)
        return new_excerpt

    if hasattr(raw_loc, "model_dump"):
        parsed = raw_loc.model_dump()
        parsed["excerpt_or_summary"] = new_excerpt
        return json.dumps(parsed, ensure_ascii=False)

    if isinstance(raw_loc, dict):
        parsed = dict(raw_loc)
        parsed["excerpt_or_summary"] = new_excerpt
        return json.dumps(parsed, ensure_ascii=False)

    return new_excerpt


def localize_excerpts_in_results(
    classification_res: ClassificationResult,
    evidence_res: EvidenceMappingResult,
    translate_batch: Callable[[list[str]], list[str]],
) -> tuple[ClassificationResult, EvidenceMappingResult, int]:
    candidates = _collect_candidate_excerpts(classification_res, evidence_res)
    if not candidates:
        return classification_res, evidence_res, 0

    localized = translate_batch(candidates)
    if len(localized) != len(candidates):
        raise ValueError(
            f"Expected {len(candidates)} localized excerpt(s) but got {len(localized)}."
        )

    localized_map = {
        original: _normalize_excerpt_text(rewritten) or original
        for original, rewritten in zip(candidates, localized)
    }

    changed_count = 0

    def _rewrite_field(field):
        nonlocal changed_count
        if not field:
            return field
        changed = False
        new_locs = []
        for raw_loc in getattr(field, "supporting_evidence_locations", None) or []:
            excerpt, _, _ = parse_supporting_evidence_location(raw_loc)
            new_excerpt = localized_map.get(_normalize_excerpt_text(excerpt))
            if new_excerpt and new_excerpt != excerpt:
                new_locs.append(_update_classification_location(raw_loc, new_excerpt))
                changed = True
                changed_count += 1
            else:
                new_locs.append(raw_loc)
        return field.model_copy(update={"supporting_evidence_locations": new_locs}) if changed else field

    updated_classification = classification_res.model_copy(update={
        "stage": _rewrite_field(getattr(classification_res, "stage", None)),
        "main_industry": _rewrite_field(getattr(classification_res, "main_industry", None)),
        "subindustry": _rewrite_field(getattr(classification_res, "subindustry", None)),
    })

    updated_criteria = []
    for criterion in evidence_res.criteria_evidence:
        new_units = []
        for unit in criterion.evidence_units:
            new_excerpt = localized_map.get(_normalize_excerpt_text(unit.excerpt_or_summary))
            if new_excerpt and new_excerpt != unit.excerpt_or_summary:
                new_units.append(unit.model_copy(update={"excerpt_or_summary": new_excerpt}))
                changed_count += 1
            else:
                new_units.append(unit)

        new_weakening_units = []
        for unit in criterion.weakening_evidence_units:
            new_excerpt = localized_map.get(_normalize_excerpt_text(unit.excerpt_or_summary))
            if new_excerpt and new_excerpt != unit.excerpt_or_summary:
                new_weakening_units.append(unit.model_copy(update={"excerpt_or_summary": new_excerpt}))
                changed_count += 1
            else:
                new_weakening_units.append(unit)

        updated_criteria.append(
            criterion.model_copy(update={
                "evidence_units": new_units,
                "weakening_evidence_units": new_weakening_units,
            })
        )

    updated_evidence = evidence_res.model_copy(
        update={"criteria_evidence": updated_criteria}
    )
    return updated_classification, updated_evidence, changed_count
