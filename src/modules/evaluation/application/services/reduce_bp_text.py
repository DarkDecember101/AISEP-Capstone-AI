"""
Structure-preserving text reduction for long Business Plan documents.

When a Business Plan exceeds the word threshold, this module reduces text
per-band while preserving the most important content for evaluation.
"""
import re
from typing import List, Dict, Any, Tuple
from src.shared.logging.logger import setup_logger

logger = setup_logger("reduce_bp_text")

# 25 000 words threshold
DEFAULT_WORD_THRESHOLD = 25_000

# 10 semantic bands in priority order (higher priority = more budget)
BANDS = [
    ("executive_summary",  0.12),
    ("problem_customer",   0.12),
    ("market",             0.10),
    ("product_solution",   0.12),
    ("gtm_business_model", 0.10),
    ("team_execution",     0.10),
    ("financials",         0.10),
    ("risk_validation",    0.08),
    ("appendix",           0.04),
    ("uncategorized",      0.12),
]

# Keywords used to classify pages into bands
_BAND_KEYWORDS: Dict[str, List[str]] = {
    "executive_summary":  ["executive summary", "overview", "about us", "introduction", "tl;dr"],
    "problem_customer":   ["problem", "pain point", "customer", "target user", "persona", "icp"],
    "market":             ["market", "tam", "sam", "som", "market size", "addressable", "competitive landscape", "competitor"],
    "product_solution":   ["product", "solution", "platform", "technology", "feature", "differentiation", "usp", "value proposition"],
    "gtm_business_model": ["go-to-market", "gtm", "business model", "revenue model", "pricing", "monetization", "distribution", "channel"],
    "team_execution":     ["team", "founder", "co-founder", "advisor", "board", "experience", "execution", "milestone", "roadmap"],
    "financials":         ["financial", "revenue", "cost", "projection", "forecast", "burn rate", "unit economics", "p&l", "cash flow"],
    "risk_validation":    ["risk", "validation", "traction", "pilot", "case study", "testimonial", "user feedback"],
    "appendix":           ["appendix", "annex", "reference", "supplement", "additional"],
}


def _classify_page(text: str) -> str:
    """Classify a page into a semantic band based on keyword density."""
    lower = text.lower()
    scores: Dict[str, int] = {}
    for band, keywords in _BAND_KEYWORDS.items():
        scores[band] = sum(lower.count(kw) for kw in keywords)
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    return best if scores[best] > 0 else "uncategorized"


def _word_count(text: str) -> int:
    return len(text.split())


def reduce_business_plan_text(
    pages: List[Dict[str, Any]],
    warnings: List[str],
    word_threshold: int = DEFAULT_WORD_THRESHOLD,
) -> Tuple[str, Dict[str, Any]]:
    """
    Reduce Business Plan text while preserving structure.

    Args:
        pages: List of page dicts from PDFParser (must have "text" key, may have "page_number").
        warnings: Mutable list; a structured warning is appended if reduction occurs.
        word_threshold: Maximum word count before reduction triggers.

    Returns:
        (reduced_full_text, metadata_dict)
        metadata_dict contains band_word_counts, original_word_count, reduced_word_count, etc.
    """
    full_text = "\n\n".join(p.get("text", "") for p in pages)
    original_wc = _word_count(full_text)

    if original_wc <= word_threshold:
        return full_text, {
            "reduction_applied": False,
            "original_word_count": original_wc,
            "reduced_word_count": original_wc,
        }

    logger.info(
        "BP text reduction triggered: %d words > %d threshold",
        original_wc, word_threshold,
    )

    # Classify each page into a band
    band_pages: Dict[str, List[Dict[str, Any]]] = {b: [] for b, _ in BANDS}
    for i, page in enumerate(pages):
        text = page.get("text", "")
        band = _classify_page(text)
        band_pages[band].append(
            {"index": i, "text": text, "page_number": page.get("page_number", i + 1)})

    # Compute per-band budgets
    band_budgets: Dict[str, int] = {}
    for band, ratio in BANDS:
        band_budgets[band] = int(word_threshold * ratio)

    # First pass: identify bands that are under-budget → redistribute surplus
    surplus = 0
    over_budget_bands = []
    for band, _ in BANDS:
        band_wc = sum(_word_count(p["text"]) for p in band_pages[band])
        if band_wc <= band_budgets[band]:
            surplus += band_budgets[band] - band_wc
        else:
            over_budget_bands.append(band)

    # Redistribute surplus proportionally to over-budget bands
    if over_budget_bands and surplus > 0:
        total_over = sum(
            sum(_word_count(p["text"])
                for p in band_pages[b]) - band_budgets[b]
            for b in over_budget_bands
        )
        for band in over_budget_bands:
            band_over = sum(_word_count(p["text"])
                            for p in band_pages[band]) - band_budgets[band]
            extra = int(surplus * (band_over / total_over)
                        ) if total_over > 0 else 0
            band_budgets[band] += extra

    # Second pass: truncate each band to its budget
    reduced_parts: List[str] = []
    band_stats: Dict[str, Dict[str, int]] = {}

    # Process bands in page order for coherence
    all_reduced: List[Tuple[int, str]] = []
    for band, _ in BANDS:
        budget = band_budgets[band]
        band_text_parts = []
        used = 0
        for p in band_pages[band]:
            pw = _word_count(p["text"])
            if used + pw <= budget:
                band_text_parts.append(p["text"])
                all_reduced.append((p["index"], p["text"]))
                used += pw
            else:
                # Partial inclusion: take first N words
                remaining = budget - used
                if remaining > 50:  # Only include if meaningful
                    words = p["text"].split()
                    partial = " ".join(words[:remaining])
                    partial += f"\n[... page {p['page_number']} truncated for length ...]"
                    band_text_parts.append(partial)
                    all_reduced.append((p["index"], partial))
                    used += remaining
                break
        band_stats[band] = {"original": sum(_word_count(p["text"]) for p in band_pages[band]),
                            "reduced": used, "pages": len(band_pages[band])}

    # Reconstruct in original page order
    all_reduced.sort(key=lambda x: x[0])
    reduced_text = "\n\n".join(t for _, t in all_reduced)
    reduced_wc = _word_count(reduced_text)

    metadata = {
        "reduction_applied": True,
        "original_word_count": original_wc,
        "reduced_word_count": reduced_wc,
        "word_threshold": word_threshold,
        "band_stats": band_stats,
    }

    warnings.append(
        f"BP_TEXT_REDUCED: Original {original_wc} words reduced to {reduced_wc} words "
        f"(threshold: {word_threshold}). Band-level truncation applied."
    )

    logger.info("BP text reduced: %d → %d words", original_wc, reduced_wc)
    return reduced_text, metadata
