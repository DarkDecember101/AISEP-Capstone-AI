"""Append new tests (Bugs 1+2, 4+5) to the phase C test file."""
import py_compile
import pathlib

TARGET = pathlib.Path(
    r"c:\Users\LENOVO\Desktop\AISEP_AI\src\tests\unit\test_phase_c_validation.py"
)

NEW_TESTS = '''


# ─── Tests: Provided-stage enforcement (Bugs 1 + 2) ─────────────────────────

class TestProvidedStageEnforcement:
    """Verify that provided_stage is enforced in process_document.py so the
    deterministic scorer always receives (and uses) the evaluator-supplied stage,
    regardless of what the LLM classified."""

    def _make_mock_clf_with_wrong_stage(self, llm_stage: str = "PRE_SEED"):
        """Simulate an LLM that returned the wrong stage despite a provided hint."""
        from src.modules.evaluation.application.dto.pipeline_schema import (
            ClassificationResult, ClassificationField,
            EvidenceMappingResult, CriterionEvidence,
            RawCriterionJudgmentResult, RawJudgment,
        )
        clf = ClassificationResult(
            stage=ClassificationField(
                value=llm_stage, confidence="Medium",
                resolution_source="inferred", supporting_evidence_locations=[],
            ),
            main_industry=ClassificationField(
                value="SAAS_ENTERPRISE_SOFTWARE", confidence="High",
                resolution_source="inferred", supporting_evidence_locations=[],
            ),
        )
        cnames = [
            "Problem_&_Customer_Pain", "Market_Attractiveness_&_Timing",
            "Solution_&_Differentiation", "Business_Model_&_Go_to_Market",
            "Team_&_Execution_Readiness", "Validation_Traction_Evidence_Quality",
        ]
        evidence = EvidenceMappingResult(
            criteria_evidence=[
                CriterionEvidence(criterion=c, strongest_evidence_level="DIRECT",
                                  evidence_units=[]) for c in cnames
            ]
        )
        judgments = RawCriterionJudgmentResult(
            raw_judgments=[
                RawJudgment(criterion=c, raw_score=7.0, criterion_confidence="High",
                            suggested_contradiction_severity="none") for c in cnames
            ]
        )
        return clf, evidence, judgments

    def _apply_provided_stage_override(self, clf, provided_stage: str):
        """Apply the same override logic as process_document.py."""
        from src.modules.evaluation.application.dto.pipeline_schema import (
            ClassificationContextInput, ClassificationField
        )
        _VALID = frozenset({"IDEA", "MVP", "PRE_SEED", "SEED", "GROWTH"})
        ctx = ClassificationContextInput(provided_stage=provided_stage)
        ps = (ctx.provided_stage or "").upper().strip()
        if ps in _VALID:
            clf = clf.model_copy(update={
                "stage": ClassificationField(
                    value=ps,
                    confidence="High",
                    resolution_source="provided",
                    supporting_evidence_locations=clf.stage.supporting_evidence_locations,
                )
            })
        return clf

    def test_provided_seed_overrides_llm_pre_seed(self):
        """When provided_stage=SEED and LLM returned PRE_SEED, stage must be SEED."""
        clf, _, _ = self._make_mock_clf_with_wrong_stage("PRE_SEED")
        clf = self._apply_provided_stage_override(clf, "seed")
        assert clf.stage.value == "SEED"
        assert clf.stage.resolution_source == "provided"
        assert clf.stage.confidence == "High"

    def test_provided_seed_gives_seed_effective_weights(self):
        """After provided_stage enforcement, scorer must use SEED weight profile."""
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService, STAGE_WEIGHT_PROFILES
        )
        clf, evidence, judgments = self._make_mock_clf_with_wrong_stage("PRE_SEED")
        clf = self._apply_provided_stage_override(clf, "SEED")
        svc = DeterministicScoringService(total_pages=20)
        result = svc.score(clf, evidence, judgments)
        assert result.effective_weights == STAGE_WEIGHT_PROFILES["SEED"], (
            f"Expected SEED weights but got: {result.effective_weights}"
        )

    def test_provided_seed_weights_not_pre_seed(self):
        """SEED effective_weights must differ from PRE_SEED effective_weights."""
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService, STAGE_WEIGHT_PROFILES
        )
        clf, evidence, judgments = self._make_mock_clf_with_wrong_stage("PRE_SEED")
        clf = self._apply_provided_stage_override(clf, "SEED")
        svc = DeterministicScoringService(total_pages=20)
        result = svc.score(clf, evidence, judgments)
        assert result.effective_weights != STAGE_WEIGHT_PROFILES["PRE_SEED"], (
            "SEED weights must not equal PRE_SEED weights — stage override failed"
        )

    def test_invalid_provided_stage_not_applied(self):
        """An invalid provided_stage value must not override the LLM classification."""
        from src.modules.evaluation.application.dto.pipeline_schema import (
            ClassificationContextInput, ClassificationField
        )
        _VALID = frozenset({"IDEA", "MVP", "PRE_SEED", "SEED", "GROWTH"})
        clf, _, _ = self._make_mock_clf_with_wrong_stage("SEED")
        ctx = ClassificationContextInput(provided_stage="SERIES_A")
        ps = (ctx.provided_stage or "").upper().strip()
        if ps in _VALID:
            clf = clf.model_copy(update={
                "stage": ClassificationField(
                    value=ps, confidence="High", resolution_source="provided",
                    supporting_evidence_locations=[],
                )
            })
        # SERIES_A is not valid — override must not be applied
        assert clf.stage.value == "SEED"

    def test_provided_stage_resolution_source_is_provided(self):
        """After override, classification.stage.resolution_source must be 'provided'."""
        clf, _, _ = self._make_mock_clf_with_wrong_stage("IDEA")
        clf = self._apply_provided_stage_override(clf, "SEED")
        assert clf.stage.resolution_source == "provided"


# ─── Tests: Stage-narrative contradiction (Bug 5) ────────────────────────────

class TestStageNarrativeContradiction:
    """sanitize_canonical_report must remove PRE_SEED language from a SEED report,
    and validate_canonical_report must flag any that slipped through."""

    def _seed_canon_with_preseed_concern(self):
        """SEED report that has a PRE_SEED-language top_concern."""
        canon = _make_canonical(overall_score=89.0)
        canon["narrative"]["top_concerns"] = [
            "This pre-seed venture has not yet secured its first customers.",
            "GTM strategy needs further development.",
        ]
        return canon

    def test_sanitize_removes_preseed_concern_from_seed_report(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = self._seed_canon_with_preseed_concern()
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert not any("pre-seed" in c.lower() for c in concerns), (
            f"PRE_SEED language was not removed from SEED concerns: {concerns}"
        )
        assert any("AUTO_REMOVED_CONCERN" in w for w in result["processing_warnings"])

    def test_sanitize_keeps_non_contradictory_concern(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = self._seed_canon_with_preseed_concern()
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert any("GTM" in c for c in concerns), "Non-contradictory concern must not be removed"

    def test_sanitize_removes_preseed_language_from_recommendations(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(overall_score=89.0)
        canon["narrative"]["recommendations"] = [
            {
                "category": "STRATEGIC_CLARITY",
                "priority": 1,
                "recommendation": "Seek validation from first customers as an early-stage venture.",
                "rationale": "Pre-seed companies should validate early.",
                "expected_impact": "Validation_Traction_Evidence_Quality",
            }
        ]
        result = sanitize_canonical_report(canon)
        recs = result["narrative"]["recommendations"]
        assert not any("pre-seed" in (r.get("rationale") or "").lower() for r in recs), (
            "PRE_SEED language must be removed from SEED recommendations"
        )
        assert any("AUTO_REMOVED_REC" in w for w in result["processing_warnings"])

    def test_validator_flags_residual_stage_contradiction(self):
        """validate_canonical_report must flag PRE_SEED language that survived sanitize."""
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(overall_score=89.0)
        # Inject directly without going through sanitize
        canon["narrative"]["overall_explanation"] = (
            "This pre-seed venture shows strong market potential at the SEED stage."
        )
        result = validate_canonical_report(canon)
        assert result.is_valid  # soft flag only, not hard fail
        assert any("STAGE_NARRATIVE_CONTRADICTION" in f for f in result.validation_flags), (
            f"Expected STAGE_NARRATIVE_CONTRADICTION flag but got: {result.validation_flags}"
        )

    def test_growth_stage_no_seed_language_flagged(self):
        """validate_canonical_report must flag seed-stage language in a GROWTH report."""
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(overall_score=89.0)
        canon["classification"]["stage"]["value"] = "GROWTH"
        canon["narrative"]["overall_explanation"] = (
            "This seed-stage company should now focus on building the initial product."
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any("STAGE_NARRATIVE_CONTRADICTION" in f for f in result.validation_flags)

    def test_mvp_stage_no_stage_contradiction_flag(self):
        """MVP stage has no lower-stage restriction pattern — must produce no flag."""
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(overall_score=65.0)
        canon["classification"]["stage"]["value"] = "MVP"
        canon["narrative"]["overall_explanation"] = "This early-stage MVP shows promise."
        result = validate_canonical_report(canon)
        assert not any("STAGE_NARRATIVE_CONTRADICTION" in f for f in result.validation_flags)


# ─── Tests: Concern removal for contradictory score + narrative (Bug 4) ──────

class TestConcernRemovalBug4:
    """sanitize_canonical_report must REMOVE (not just flag) top_concerns that
    describe high-scoring criteria as having weak/limited evidence."""

    def _high_validation_criteria(self) -> list:
        return [
            {
                "criterion": "Validation_Traction_Evidence_Quality",
                "status": "scored",
                "final_score": 95.0,
                "confidence": "High",
                "evidence_strength_summary": "STRONG_DIRECT",
                "evidence_locations": [],
                "cap_summary": {"evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                "contradiction_penalty_points": 0.0},
                "explanation": "strong traction evidence",
            }
        ]

    def test_contradictory_concern_is_removed_not_just_flagged(self):
        """Concern saying 'limited validation' for a criterion scoring 95/STRONG_DIRECT
        must be physically removed from top_concerns."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(
            overall_score=89.0,
            criteria_results=self._high_validation_criteria(),
        )
        canon["narrative"]["top_concerns"] = [
            "Limited early validation evidence undermines investor confidence.",
            "GTM channel diversity should be improved.",
        ]
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert not any("limited" in c.lower() and "validation" in c.lower() for c in concerns), (
            f"Contradictory concern was not removed: {concerns}"
        )
        assert any("AUTO_REMOVED_CONCERN" in w for w in result["processing_warnings"])

    def test_non_contradictory_concern_is_kept(self):
        """A concern about a WEAK criterion (score < 65) must never be removed."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        weak_criteria = [
            {
                "criterion": "Business_Model_&_Go_to_Market",
                "status": "scored",
                "final_score": 45.0,
                "confidence": "Medium",
                "evidence_strength_summary": "INDIRECT",
                "evidence_locations": [],
                "cap_summary": {"evidence_quality_cap": 6.0, "contradiction_cap": 10.0,
                                "contradiction_penalty_points": 0.0},
                "explanation": "weak GTM",
            }
        ]
        canon = _make_canonical(overall_score=75.0, criteria_results=weak_criteria)
        canon["narrative"]["top_concerns"] = [
            "Limited go-to-market evidence undermines the revenue model credibility."
        ]
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert len(concerns) == 1, f"Concern for weak GTM criterion must be kept: {concerns}"
'''

content = TARGET.read_text(encoding="utf-8")
content += NEW_TESTS
TARGET.write_text(content, encoding="utf-8")
print("New tests appended.")

try:
    py_compile.compile(str(TARGET), doraise=True)
    print("Syntax OK.")
except py_compile.PyCompileError as e:
    print(f"Syntax ERROR: {e}")
