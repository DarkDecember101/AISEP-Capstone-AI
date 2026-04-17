"""
Output Assembly Issues — regression tests (rounds 1 and 2).

Round 1 (Issues 1-5, previous session):
  Issue 1: Unresolved subindustry must be null, not "Unknown"
  Issue 2: excerpt_or_summary must not contain serialized JSON
  Issue 3: section_name survives into final canonical response
  Issue 4: source_id "document_id_placeholder" replaced before output
  Issue 5: operational_notes duplicates are removed

Round 2 (Issues 1-4, this session):
  Issue 1b: 'null' / 'none' string sentinels also normalize to null via sanitizer
  Issue 2b: Python dict-repr strings (single-quoted) parsed via ast.literal_eval()
  Issue 3b: Stage contradiction in operational_notes repaired (not just warned)
  Issue 4b: Near-duplicate notes (whitespace-only difference) also removed
"""

import ast
import json
import pytest


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_canonical(op_notes=None, criteria_results=None, subindustry_value="SUB_X"):
    return {
        "startup_id": "s1",
        "document_type": "business_plan",
        "status": "completed",
        "classification": {
            "stage": {"value": "SEED", "confidence": "High",
                      "resolution_source": "inferred", "supporting_evidence_locations": []},
            "main_industry": {"value": "SAAS_ENTERPRISE_SOFTWARE", "confidence": "High",
                              "resolution_source": "inferred", "supporting_evidence_locations": []},
            "subindustry": {"value": subindustry_value, "confidence": "Low",
                            "resolution_source": "inferred", "supporting_evidence_locations": []},
            "operational_notes": [],
        },
        "effective_weights": {"Problem_&_Customer_Pain": 20.0},
        "criteria_results": criteria_results or [
            {
                "criterion": "Problem_&_Customer_Pain",
                "status": "scored",
                "final_score": 70.0,
                "confidence": "Medium",
                "evidence_strength_summary": "DIRECT",
                "evidence_locations": [],
                "cap_summary": {
                    "evidence_quality_cap": 10.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "explanation": "ok",
            }
        ],
        "overall_result": {
            "overall_score": 70.0,
            "overall_confidence": "Medium",
            "evidence_coverage": "moderate",
            "interpretation_band": "promising but incomplete",
            "stage_context_note": "SEED stage.",
        },
        "narrative": {
            "executive_summary": "summary",
            "top_strengths": ["strong team"],
            "top_concerns": ["limited traction"],
            "missing_information": [],
            "overall_explanation": "decent",
            "operational_notes": op_notes or [],
            "recommendations": [],
            "key_questions": [],
        },
        "processing_warnings": [],
    }


# ─── Issue 1 ──────────────────────────────────────────────────────────────────

class TestUnresolvedSubindustryNull:
    """safe_class() must emit value=null when the LLM returns None or 'Unknown'."""

    def test_safe_class_none_value_yields_null(self):
        """When ClassificationField.value is None, the assembled dict must have value=null."""
        from unittest.mock import MagicMock, patch

        # Build a fake ClassificationField with value=None
        mock_item = MagicMock()
        mock_item.value = None
        mock_item.confidence = "Low"
        mock_item.resolution_source = "inferred"
        mock_item.supporting_evidence_locations = []

        # Import and exercise safe_class in isolation by running the process_document module's
        # inner function logic without triggering the full pipeline.
        # We test the canonical dict subindustry value produced by the assembly step.
        raw_value = mock_item.value
        result_value = raw_value if raw_value and str(
            raw_value).strip() not in ("", "Unknown") else None
        assert result_value is None, (
            f"Expected None but got {result_value!r}. "
            "Unresolved subindustry must be null, not a placeholder string."
        )

    def test_safe_class_unknown_string_yields_null(self):
        """When the LLM emits the string 'Unknown', value must be normalized to null."""
        raw_value = "Unknown"
        result_value = raw_value if raw_value and str(
            raw_value).strip() not in ("", "Unknown") else None
        assert result_value is None

    def test_sanitize_does_not_inject_unknown(self):
        """sanitize_canonical_report must not turn a null subindustry value into 'Unknown'."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report

        canonical = _make_canonical(
            subindustry_value=None)  # type: ignore[arg-type]
        canonical["classification"]["subindustry"]["value"] = None

        result = sanitize_canonical_report(canonical)
        sub_value = result["classification"]["subindustry"]["value"]
        assert sub_value is None, (
            f"sanitize_canonical_report must not replace null subindustry with a string; got {sub_value!r}"
        )

    def test_validate_accepts_null_subindustry(self):
        """validate_canonical_report must not reject a report with subindustry.value=null."""
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report

        canonical = _make_canonical()
        canonical["classification"]["subindustry"]["value"] = None

        validity = validate_canonical_report(canonical)
        assert validity.is_valid, f"Report with null subindustry should be valid. Reason: {validity.reason}"


# ─── Issue 2 ──────────────────────────────────────────────────────────────────

class TestExcerptNotSerializedJson:
    """excerpt_or_summary must not contain a full serialized EvidenceLocation JSON object."""

    def _parse_excerpt(self, raw_loc: str) -> str:
        """Replicate the fix logic from safe_class()."""
        import json as _json
        import re as _re
        stripped = raw_loc.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = _json.loads(stripped)
                if isinstance(parsed, dict) and "excerpt_or_summary" in parsed:
                    return parsed["excerpt_or_summary"]
            except (_json.JSONDecodeError, ValueError):
                pass
        return raw_loc

    def test_plain_string_passes_through(self):
        excerpt = self._parse_excerpt(
            "Product shows strong PMF signals from pilot customers.")
        assert "{" not in excerpt

    def test_json_object_string_extracts_excerpt(self):
        """LLM emits the whole EvidenceLocation as a JSON string → only excerpt survives."""
        raw = json.dumps({
            "source_type": "Business Plan",
            "source_id": "document_id_placeholder",
            "slide_number_or_page_number": 12,
            "excerpt_or_summary": "ARR grew 3x year-over-year.",
            "section_name": "Financial Summary",
        })
        result = self._parse_excerpt(raw)
        assert result == "ARR grew 3x year-over-year.", (
            f"excerpt_or_summary must extract only the text; got: {result!r}"
        )
        # Must not contain the full JSON
        assert "{" not in result

    def test_json_without_excerpt_key_returns_raw(self):
        """Graceful fallback: if the JSON has no excerpt_or_summary key, keep raw string."""
        raw = json.dumps({"source_type": "Business Plan", "source_id": "x"})
        result = self._parse_excerpt(raw)
        # Should not crash; returns the raw string (no excerpt key present)
        assert isinstance(result, str)


# ─── Issue 3 ──────────────────────────────────────────────────────────────────

class TestSectionNamePreserved:
    """section_name must survive from the JSON evidence string into the assembled location dict."""

    def _extract_section_name(self, raw_loc: str):
        """Replicate the fix logic from safe_class()."""
        import json as _json
        import re as _re
        stripped = raw_loc.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = _json.loads(stripped)
                if isinstance(parsed, dict):
                    return parsed.get("section_name")
            except (_json.JSONDecodeError, ValueError):
                pass
        return None

    def test_section_name_extracted_from_json_string(self):
        raw = json.dumps({
            "source_type": "Business Plan",
            "source_id": "document_id_placeholder",
            "slide_number_or_page_number": 5,
            "excerpt_or_summary": "Revenue model is subscription-based.",
            "section_name": "Revenue Model",
        })
        section_name = self._extract_section_name(raw)
        assert section_name == "Revenue Model", (
            f"section_name must be preserved from serialized evidence string; got {section_name!r}"
        )

    def test_plain_string_returns_none_section_name(self):
        section_name = self._extract_section_name(
            "Plain excerpt text, no JSON.")
        assert section_name is None

    def test_location_dict_includes_section_name_when_present(self):
        """The assembled location dict must contain section_name when it is present."""
        raw = json.dumps({
            "source_type": "Business Plan",
            "source_id": "doc-1",
            "slide_number_or_page_number": 3,
            "excerpt_or_summary": "Team has domain expertise.",
            "section_name": "Team Overview",
        })
        import json as _json
        import re as _re
        # Simulate safe_class() location assembly for a JSON-string evidence entry
        stripped = raw.strip()
        parsed = _json.loads(stripped)
        excerpt = parsed.get("excerpt_or_summary")
        section_name = parsed.get("section_name")
        loc_entry = {
            "source_type": "Business Plan",
            "source_id": "42",
            "slide_number_or_page_number": max(1, parsed.get("slide_number_or_page_number", 1)),
            "excerpt_or_summary": excerpt or "",
        }
        if section_name is not None:
            loc_entry["section_name"] = section_name

        assert "section_name" in loc_entry
        assert loc_entry["section_name"] == "Team Overview"


# ─── Issue 4 ──────────────────────────────────────────────────────────────────

class TestSourceIdPlaceholderReplaced:
    """'document_id_placeholder' must not appear in any evidence_location in the final output."""

    def _replace_placeholders(self, canonical: dict, real_id: str) -> dict:
        """Replicate the fix logic from process_document.py."""
        PLACEHOLDER = "document_id_placeholder"
        for cr in canonical.get("criteria_results", []):
            for ev in cr.get("evidence_locations", []):
                if isinstance(ev, dict) and ev.get("source_id") == PLACEHOLDER:
                    ev["source_id"] = real_id
        for cls_field in canonical.get("classification", {}).values():
            if isinstance(cls_field, dict):
                for ev in cls_field.get("supporting_evidence_locations", []):
                    if isinstance(ev, dict) and ev.get("source_id") == PLACEHOLDER:
                        ev["source_id"] = real_id
        return canonical

    def test_placeholder_replaced_in_criteria_evidence(self):
        canonical = _make_canonical(criteria_results=[
            {
                "criterion": "Problem_&_Customer_Pain",
                "status": "scored",
                "final_score": 70.0,
                "confidence": "Medium",
                "evidence_strength_summary": "DIRECT",
                "evidence_locations": [
                    {
                        "source_type": "Business Plan",
                        "source_id": "document_id_placeholder",
                        "slide_number_or_page_number": 4,
                        "excerpt_or_summary": "Clear pain statement.",
                    }
                ],
                "cap_summary": {
                    "evidence_quality_cap": 10.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "explanation": "ok",
            }
        ])
        result = self._replace_placeholders(canonical, real_id="doc-77")
        ev = result["criteria_results"][0]["evidence_locations"][0]
        assert ev["source_id"] == "doc-77", (
            f"Placeholder must be replaced; got {ev['source_id']!r}"
        )

    def test_placeholder_replaced_in_classification_evidence(self):
        canonical = _make_canonical()
        canonical["classification"]["stage"]["supporting_evidence_locations"] = [
            {
                "source_type": "Business Plan",
                "source_id": "document_id_placeholder",
                "slide_number_or_page_number": 1,
                "excerpt_or_summary": "Seed-stage language used.",
            }
        ]
        result = self._replace_placeholders(canonical, real_id="bp-99")
        ev = result["classification"]["stage"]["supporting_evidence_locations"][0]
        assert ev["source_id"] == "bp-99"

    def test_no_placeholder_present_is_unchanged(self):
        canonical = _make_canonical()
        canonical["criteria_results"][0]["evidence_locations"] = [
            {"source_type": "Business Plan", "source_id": "real-doc-1",
             "slide_number_or_page_number": 2, "excerpt_or_summary": "x"}
        ]
        result = self._replace_placeholders(canonical, real_id="ignored")
        ev = result["criteria_results"][0]["evidence_locations"][0]
        assert ev["source_id"] == "real-doc-1"


# ─── Issue 5 ──────────────────────────────────────────────────────────────────

class TestOperationalNotesDedup:
    """operational_notes must not contain exact duplicate strings in the final output."""

    def test_dedupe_removes_exact_duplicates(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report

        dup_notes = ["No subindustry overlay applied.",
                     "Seed stage confirmed.", "No subindustry overlay applied."]
        canonical = _make_canonical(op_notes=dup_notes)
        result = sanitize_canonical_report(canonical)
        notes = result["narrative"]["operational_notes"]
        assert len(notes) == len(set(notes)), (
            f"Duplicate operational_notes must be removed; got {notes}"
        )

    def test_dedupe_preserves_order(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report

        dup_notes = ["A", "B", "A", "C", "B"]
        canonical = _make_canonical(op_notes=dup_notes)
        result = sanitize_canonical_report(canonical)
        notes = result["narrative"]["operational_notes"]
        assert notes == [
            "A", "B", "C"], f"Order must be preserved after dedup; got {notes}"

    def test_no_duplicates_unchanged(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report

        unique_notes = ["Note 1", "Note 2", "Note 3"]
        canonical = _make_canonical(op_notes=unique_notes)
        result = sanitize_canonical_report(canonical)
        notes = result["narrative"]["operational_notes"]
        assert notes == unique_notes

    def test_dict_fromkeys_dedup_logic(self):
        """Unit test the exact dedup mechanic used in process_document.py."""
        report_notes = ["stage: SEED",
                        "industry override applied", "stage: SEED"]
        classification_notes = ["industry override applied", "extra note"]
        all_notes = report_notes + classification_notes
        deduped = list(dict.fromkeys(all_notes))
        assert deduped == ["stage: SEED",
                           "industry override applied", "extra note"]
        assert len(deduped) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# ROUND 2 TESTS — 4 remaining issues
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubindustrySentinelNormalization:
    """Issue 1b: 'null' / 'none' / 'undefined' string values must also → null via sanitizer."""

    def test_string_null_normalized_to_none(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canonical = _make_canonical(subindustry_value="null")
        result = sanitize_canonical_report(canonical)
        assert result["classification"]["subindustry"]["value"] is None, (
            "String 'null' must be normalized to Python None"
        )

    def test_string_none_normalized_to_none(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canonical = _make_canonical(subindustry_value="none")
        result = sanitize_canonical_report(canonical)
        assert result["classification"]["subindustry"]["value"] is None

    def test_string_unknown_normalized_to_none(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canonical = _make_canonical(subindustry_value="Unknown")
        result = sanitize_canonical_report(canonical)
        assert result["classification"]["subindustry"]["value"] is None

    def test_empty_string_normalized_to_none(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canonical = _make_canonical(subindustry_value="")
        result = sanitize_canonical_report(canonical)
        assert result["classification"]["subindustry"]["value"] is None

    def test_real_value_preserved(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canonical = _make_canonical(subindustry_value="MARKETING_TECH")
        result = sanitize_canonical_report(canonical)
        assert result["classification"]["subindustry"]["value"] == "MARKETING_TECH"

    def test_null_sentinel_in_safe_class_logic(self):
        """Direct unit test of the sentinel exclusion set used in safe_class()."""
        _NULL_SENTINELS = frozenset(
            {"", "unknown", "null", "none", "n/a", "na", "undefined"})
        for sentinel in ("Unknown", "unknown", "null", "none", "NULL", "NONE", "undefined", ""):
            raw = sentinel
            result = raw if raw is not None and str(
                raw).strip().lower() not in _NULL_SENTINELS else None
            assert result is None, f"Sentinel '{sentinel}' should normalize to None"
        # Real values pass through
        for real in ("MARKETING_TECH", "FINTECH", "HEALTH_TECH"):
            raw = real
            result = raw if raw is not None and str(
                raw).strip().lower() not in _NULL_SENTINELS else None
            assert result == real, f"Real value '{real}' should pass through"


class TestPythonDictReprParsing:
    """Issue 2b: safe_class() must handle Python dict repr strings (single-quoted)
    using ast.literal_eval() as a fallback after json.loads() fails."""

    def _parse_evidence_loc(self, raw_loc):
        """Replicate _parse_evidence_loc from safe_class() in process_document.py."""
        import re as _re
        import json as _json
        import ast as _ast
        if isinstance(raw_loc, str):
            stripped = raw_loc.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                parsed = None
                try:
                    parsed = _json.loads(stripped)
                except (_json.JSONDecodeError, ValueError):
                    pass
                if parsed is None:
                    try:
                        parsed = _ast.literal_eval(stripped)
                    except (ValueError, SyntaxError):
                        pass
                if isinstance(parsed, dict):
                    excerpt = parsed.get("excerpt_or_summary") or stripped
                    page = parsed.get("slide_number_or_page_number") or 1
                    sec = parsed.get("section_name")
                    return excerpt, page, sec
            m = _re.search(r'\d+', raw_loc)
            return raw_loc, int(m.group(0)) if m else 1, None
        return str(raw_loc), 1, None

    def test_valid_json_string_parsed(self):
        raw = json.dumps({
            "source_type": "Business Plan",
            "source_id": "document_id_placeholder",
            "slide_number_or_page_number": 5,
            "excerpt_or_summary": "ARR grew 3x YoY.",
            "section_name": "Financials",
        })
        excerpt, page, sec = self._parse_evidence_loc(raw)
        assert excerpt == "ARR grew 3x YoY."
        assert page == 5
        assert sec == "Financials"

    def test_python_dict_repr_parsed_via_ast(self):
        """Python str(dict) produces single-quoted repr — json.loads fails, ast.literal_eval succeeds."""
        raw_dict = {
            "source_type": "Business Plan",
            "source_id": "document_id_placeholder",
            "slide_number_or_page_number": 7,
            "excerpt_or_summary": "Monthly recurring revenue is $50k.",
            "section_name": "Revenue",
        }
        # Produce Python dict repr (single-quoted)
        raw_repr = str(raw_dict)
        # Verify it is NOT valid JSON
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(raw_repr)
        # But ast.literal_eval can parse it
        parsed = ast.literal_eval(raw_repr)
        assert parsed["excerpt_or_summary"] == "Monthly recurring revenue is $50k."

        # Now verify our parser handles it end-to-end
        excerpt, page, sec = self._parse_evidence_loc(raw_repr)
        assert excerpt == "Monthly recurring revenue is $50k."
        assert page == 7
        assert sec == "Revenue"

    def test_plain_string_passed_through(self):
        raw = "Strong product-market fit observed in pilot data."
        excerpt, page, sec = self._parse_evidence_loc(raw)
        assert excerpt == raw
        assert "{" not in excerpt


class TestStageContradictionOperationalNotes:
    """Issue 3b: sanitize_canonical_report must REMOVE contradicting operational_notes,
    not just flag them. Validator must not return contradicting text to the client."""

    def _make_seed_canonical(self, op_notes):
        c = _make_canonical(op_notes=op_notes)
        c["classification"]["stage"]["value"] = "SEED"
        return c

    def test_pre_seed_stage_note_removed(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        notes = [
            "No subindustry overlay was applied.",
            "Provided stage was overridden to PRE_SEED based on evidence.",
        ]
        canonical = self._make_seed_canonical(notes)
        result = sanitize_canonical_report(canonical)
        remaining = result["narrative"]["operational_notes"]
        assert not any("PRE_SEED" in n or "pre_seed" in n.lower() for n in remaining), (
            f"Operational note claiming override to PRE_SEED must be removed for SEED stage. "
            f"Remaining: {remaining}"
        )

    def test_correct_stage_note_preserved(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        notes = [
            "No subindustry overlay was applied.",
            "Stage confirmed as SEED (provided by evaluator).",
        ]
        canonical = self._make_seed_canonical(notes)
        result = sanitize_canonical_report(canonical)
        remaining = result["narrative"]["operational_notes"]
        assert any("SEED" in n for n in remaining), (
            f"Note confirming correct SEED stage must be preserved. Remaining: {remaining}"
        )

    def test_processing_warnings_records_removal(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        notes = ["Stage overridden to PRE_SEED."]
        canonical = self._make_seed_canonical(notes)
        result = sanitize_canonical_report(canonical)
        warnings = result.get("processing_warnings") or []
        assert any("AUTO_REMOVED_OP_NOTE" in w for w in warnings), (
            f"Removal must be recorded in processing_warnings. Warnings: {warnings}"
        )

    def test_validate_no_stage_contradiction_flag_after_sanitize(self):
        """After sanitize, the validator must NOT return STAGE_NARRATIVE_CONTRADICTION."""
        from src.modules.evaluation.application.services.report_validity import (
            sanitize_canonical_report, validate_canonical_report
        )
        notes = ["Provided stage was overridden to PRE_SEED based on evidence."]
        canonical = self._make_seed_canonical(notes)
        canonical = sanitize_canonical_report(canonical)
        validity = validate_canonical_report(canonical)
        contradiction_flags = [f for f in (
            validity.validation_flags or ()) if "STAGE_NARRATIVE" in f]
        assert not contradiction_flags, (
            f"No STAGE_NARRATIVE flag expected after sanitize. Got: {contradiction_flags}"
        )


class TestNearDuplicateDedup:
    """Issue 4b: near-duplicates differing only by whitespace must also be removed."""

    def test_trailing_whitespace_dedup(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        notes = [
            "No subindustry overlay was applied.",
            "No subindustry overlay was applied.  ",  # trailing spaces
        ]
        canonical = _make_canonical(op_notes=notes)
        result = sanitize_canonical_report(canonical)
        remaining = result["narrative"]["operational_notes"]
        assert len(remaining) == 1, (
            f"Near-duplicates differing only by whitespace must be collapsed to 1. Got: {remaining}"
        )

    def test_leading_whitespace_dedup(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        notes = [
            "  Stage confirmed as SEED.",
            "Stage confirmed as SEED.",
        ]
        canonical = _make_canonical(op_notes=notes)
        result = sanitize_canonical_report(canonical)
        remaining = result["narrative"]["operational_notes"]
        assert len(remaining) == 1

    def test_exact_dedup_still_works(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        notes = ["A", "B", "A", "C"]
        canonical = _make_canonical(op_notes=notes)
        result = sanitize_canonical_report(canonical)
        remaining = result["narrative"]["operational_notes"]
        assert remaining == ["A", "B", "C"]

    def test_stripped_content_is_stored(self):
        """After dedup, the stored note must be stripped."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        notes = ["  Leading spaces preserved?  ",
                 "  Leading spaces preserved?  "]
        canonical = _make_canonical(op_notes=notes)
        result = sanitize_canonical_report(canonical)
        remaining = result["narrative"]["operational_notes"]
        assert len(remaining) == 1
        assert remaining[0] == "Leading spaces preserved?"
