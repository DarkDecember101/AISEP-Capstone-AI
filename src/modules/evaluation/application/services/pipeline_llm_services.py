from typing import List, Optional
import json

from src.shared.providers.llm.gemini_client import GeminiClient
from src.modules.evaluation.infrastructure.prompts.loader import PromptLoader
from src.modules.evaluation.application.dto.pipeline_schema import (
    ClassificationResult,
    ClassificationContextInput,
    EvidenceMappingResult,
    RawCriterionJudgmentResult,
    ReportWriterResult
)
from src.shared.logging.logger import setup_logger

logger = setup_logger("pipeline_services")


class PipelineLLMServices:
    def __init__(self, pack_name: str = "pitch_deck"):
        self.pack_name = pack_name
        self.llm = GeminiClient()
        self.prompt_loader = PromptLoader()

    def classify_startup(self, full_text: str, images: Optional[List[str]] = None,
                         classification_context: Optional[ClassificationContextInput] = None) -> ClassificationResult:
        logger.info("[Step 1] Running Classification...")
        prompt_tmpl = self.prompt_loader.load_prompt(
            self.pack_name, "classification")
        prompt = prompt_tmpl.replace(
            "{content}", full_text if full_text.strip() else "Visual document attached")
        ctx_block = classification_context.to_prompt_block() if classification_context else \
            "No classification context was provided. Infer all fields from the document."
        prompt = prompt.replace("{classification_context}", ctx_block)
        return self.llm.generate_structured(prompt, ClassificationResult, image_paths=images)

    def map_evidence(self, full_text: str, images: Optional[List[str]] = None) -> EvidenceMappingResult:
        logger.info("[Step 2] Running Evidence Mapping...")
        prompt_tmpl = self.prompt_loader.load_prompt(
            self.pack_name, "evidence")
        prompt = prompt_tmpl.replace(
            "{content}", full_text if full_text.strip() else "Visual document attached")
        return self.llm.generate_structured(prompt, EvidenceMappingResult, image_paths=images)

    def judge_raw_criteria(self, evidence_result_json: str, full_text: str, images: Optional[List[str]] = None) -> RawCriterionJudgmentResult:
        logger.info("[Step 3] Running Raw Criterion Judgment...")
        prompt_tmpl = self.prompt_loader.load_prompt(
            self.pack_name, "raw_criterion")
        # Inject the evidence mapping result as context
        context = f"=== EVIDENCE MAP ===\n{evidence_result_json}\n\n=== SOURCE CONTENT ===\n{full_text}"
        prompt = prompt_tmpl.replace("{content}", context)
        return self.llm.generate_structured(prompt, RawCriterionJudgmentResult, image_paths=images)

    def write_report(
        self,
        scoring_result_json: str,
        document_type: str = "pitch_deck",
        classification_json: str = "{}",
    ) -> ReportWriterResult:
        logger.info("[Step 5] Writing Final Report...")
        prompt_tmpl = self.prompt_loader.load_prompt(
            self.pack_name, "report_writer")
        prompt = prompt_tmpl.replace("{document_type}", document_type)
        prompt = prompt.replace(
            "{classification}", f"=== CLASSIFICATION ===\n{classification_json}")
        prompt = prompt.replace(
            "{content}", f"=== SCORING RESULT ===\n{scoring_result_json}")
        return self.llm.generate_structured(prompt, ReportWriterResult)
