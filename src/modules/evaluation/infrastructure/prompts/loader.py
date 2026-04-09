import os
from pathlib import Path
from src.shared.logging.logger import setup_logger

logger = setup_logger("prompt_loader")


class PromptLoader:
    """
    Loads prompt templates from the file system.
    Supports composition (e.g., injecting shared_rule.txt into step prompts).
    """

    def __init__(self, base_dir: Path = None):
        if base_dir is None:
            # Resolve relative to this file: src/modules/evaluation/infrastructure/prompts/loader.py
            self.base_dir = Path(__file__).resolve().parents[2] / "prompts"
        else:
            self.base_dir = base_dir

    def load_prompt(self, pack_name: str, step_name: str, include_shared_rule: bool = True) -> str:
        """
        Loads a prompt for a specific pack (e.g., 'pitch_deck') and step (e.g., 'classification').
        If include_shared_rule is True, it prepends the content of shared_rule.txt from the same pack.
        """
        pack_dir = self.base_dir / pack_name
        step_path = pack_dir / f"{step_name}.txt"
        shared_rule_path = pack_dir / "shared_rule.txt"

        prompt_content = ""

        # Load shared rules if requested and exists
        if include_shared_rule:
            if shared_rule_path.exists():
                prompt_content += shared_rule_path.read_text(
                    encoding="utf-8").strip() + "\n\n"
            else:
                logger.warning(f"Shared rule not found at {shared_rule_path}")

        # Load step specific prompt
        if step_path.exists():
            prompt_content += step_path.read_text(encoding="utf-8").strip()
        else:
            logger.error(f"Prompt file not found at {step_path}")
            raise FileNotFoundError(f"Prompt file not found: {step_path}")

        return prompt_content
