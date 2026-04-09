import json
from pathlib import Path
from src.modules.evaluation.application.dto.evaluation_schema import LLMDutchEvaluationResult
from src.modules.evaluation.application.dto.evaluation_schema import CriterionResultSchema
from src.shared.providers.llm.gemini_client import GeminiClient
from src.shared.config.settings import settings


def _load_prompt(file_name: str, default_text: str) -> str:
    prompts_dir = Path(__file__).resolve().parents[2] / "prompts"
    prompt_path = prompts_dir / file_name
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return default_text


PITCH_DECK_EVAL_PROMPT = _load_prompt(
    "pitch_deck_eval_prompt.txt",
    """
You are an expert venture capital and startup evaluator.
Evaluate the following pitch deck section/slides for a startup.

=== PITCH DECK CONTENT (Text and/or Images) ===
{content}
==========================
""".strip(),
)

BUSINESS_PLAN_EVAL_PROMPT = _load_prompt(
    "business_plan_eval_prompt.txt",
    """
You are an expert venture capital and startup evaluator.
Evaluate the following business plan abstract/chunk.

=== BUSINESS PLAN CONTENT ===
{content}
==========================
""".strip(),
)


class BaseEvaluator:
    def __init__(self):
        self.llm_client = GeminiClient()

    def _fallback_evaluation(self, content: str) -> LLMDutchEvaluationResult:
        lower_content = content.lower()
        criteria = [
            "problem_clarity",
            "solution_strength",
            "market_opportunity",
            "business_model",
            "traction_evidence",
            "team_quality",
            "financial_feasibility",
            "execution_readiness",
            "risk_awareness",
        ]
        results = []
        base_score = 65.0 if len(content.strip()) > 300 else 50.0
        confidence = 0.45 if len(content.strip()) > 300 else 0.30
        for criterion in criteria:
            score = base_score
            if criterion in ["traction_evidence", "financial_feasibility"] and ("revenue" in lower_content or "mrr" in lower_content):
                score += 10.0
            if criterion == "team_quality" and ("founder" in lower_content or "team" in lower_content):
                score += 8.0
            if criterion == "market_opportunity" and ("tam" in lower_content or "sam" in lower_content or "market" in lower_content):
                score += 8.0
            results.append(
                CriterionResultSchema(
                    criterion_code=criterion,
                    score=max(0.0, min(100.0, score)),
                    confidence=confidence,
                    reason="Fallback heuristic evaluation was used because LLM response was unavailable.",
                    evidence_refs=[]
                )
            )

        return LLMDutchEvaluationResult(
            criteria_results=results,
            strengths=["Document contains baseline evaluable information."],
            weaknesses=[
                "LLM unavailable; used heuristic fallback for Phase 1 continuity."],
            red_flags=[],
            missing_information=[
                "Detailed evidence extraction limited in fallback mode."],
            summary="Fallback evaluation completed with reduced confidence."
        )

    def evaluate_text_chunk(self, content: str, is_pitch_deck: bool) -> LLMDutchEvaluationResult:
        if is_pitch_deck:
            prompt = PITCH_DECK_EVAL_PROMPT.format(content=content)
        else:
            prompt = BUSINESS_PLAN_EVAL_PROMPT.format(content=content)

        if not settings.GEMINI_API_KEY:
            return self._fallback_evaluation(content)

        try:
            return self.llm_client.generate_structured(
                prompt=prompt,
                response_schema=LLMDutchEvaluationResult
            )
        except Exception:
            return self._fallback_evaluation(content)

    def evaluate_multimodal_chunk(self, textual_content: str, image_paths: list[str]) -> LLMDutchEvaluationResult:
        prompt = PITCH_DECK_EVAL_PROMPT.format(
            content=textual_content if textual_content.strip() else "Visual Pitch Deck Slides attached.")

        if not settings.GEMINI_API_KEY:
            return self._fallback_evaluation(textual_content)

        try:
            return self.llm_client.generate_structured(
                prompt=prompt,
                response_schema=LLMDutchEvaluationResult,
                image_paths=image_paths
            )
        except Exception:
            return self._fallback_evaluation("Failed image multimodal fallback.")
