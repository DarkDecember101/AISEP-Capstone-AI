from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Literal, Optional


SUPPORTED_INTENTS = {
    "market_trend",
    "regulation",
    "news",
    "competitor_context",
    "mixed",
}

OUT_OF_SCOPE_REFUSAL_VI = (
    "Mình tập trung hỗ trợ các câu hỏi liên quan đến phân tích đầu tư startup "
    "và market research cho nhà đầu tư. Nội dung bạn hỏi hiện nằm ngoài phạm vi đó."
)
OUT_OF_SCOPE_REFUSAL_EN = (
    "I'm focused on questions related to startup investment analysis "
    "and market research for investors. Your question is outside that scope."
)

OUT_OF_SCOPE_CAVEAT_VI = (
    "Câu hỏi nằm ngoài phạm vi phân tích đầu tư "
    "(xu hướng thị trường, quy định, tin tức, phân tích đối thủ)."
)
OUT_OF_SCOPE_CAVEAT_EN = (
    "This query is outside investor-research scope "
    "(market trends, regulation, news, competitor context)."
)

# Keep old names as defaults (Vietnamese) for backward compat
OUT_OF_SCOPE_REFUSAL = OUT_OF_SCOPE_REFUSAL_VI
OUT_OF_SCOPE_CAVEAT = OUT_OF_SCOPE_CAVEAT_VI

_VIETNAMESE_MARKERS = re.compile(
    r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]"
)


def _is_vietnamese(text: str) -> bool:
    """Lightweight heuristic: True if text contains Vietnamese diacritics."""
    return bool(_VIETNAMESE_MARKERS.search(text or ""))


def get_refusal(query: str = "") -> str:
    return OUT_OF_SCOPE_REFUSAL_VI if _is_vietnamese(query) else OUT_OF_SCOPE_REFUSAL_EN


def get_caveat(query: str = "") -> str:
    return OUT_OF_SCOPE_CAVEAT_VI if _is_vietnamese(query) else OUT_OF_SCOPE_CAVEAT_EN


@dataclass
class ScopeDecision:
    is_out_of_scope: bool
    reason: str
    final_intent: Literal[
        "market_trend", "regulation", "news", "competitor_context", "mixed", "out_of_scope"
    ]
    heuristic_used: bool = False
    heuristic_intent: Optional[str] = None
    refusal_reason: Optional[str] = None


RouterIntent = Literal[
    "market_trend", "regulation", "news", "competitor_context", "mixed", "out_of_scope"
]
RouterConfidence = Literal["high", "medium", "low"]


_IN_SCOPE_KEYWORDS: Dict[str, set[str]] = {
    "market_trend": {
        "xu hướng", "trend", "thị trường", "market", "growth", "funding", "adoption",
        "fintech", "saas", "đầu tư", "vốn", "valuation", "startup", "investment",
    },
    "regulation": {
        "quy định", "nghị định", "thông tư", "regulation", "policy", "compliance",
        "license", "effective date", "pháp lý", "giấy phép", "luật",
    },
    "news": {
        "tin tức", "mới nhất", "cập nhật", "latest", "news", "announced", "recent",
        "công bố", "vừa", "breaking",
    },
    "competitor_context": {
        "đối thủ", "so sánh", "vs", "competitor", "landscape", "player", "positioning",
        "benchmark", "market map", "ai là đối thủ",
    },
}

_OUT_SCOPE_PATTERNS = {
    "weather": re.compile(r"\b(weather|temperature|rain|forecast|humidity|storm|thời tiết|mưa|nắng)\b", re.IGNORECASE),
    "math": re.compile(r"\b(calculate|solve|equation|integral|derivative|what is \d+\s*[\+\-\*/]|\d+\s*[\+\-\*/]\s*\d+|bằng bao nhiêu|tính giúp)\b", re.IGNORECASE),
    "coding": re.compile(r"\b(code|debug|python|javascript|java|c\+\+|sql|api endpoint|programming|lập trình|viết code)\b", re.IGNORECASE),
    "translation": re.compile(r"\b(translate|translation|dịch sang|dịch giúp)\b", re.IGNORECASE),
    "entertainment": re.compile(r"\b(movie|song|celebrity|football score|trivia|game lore|phim|ca sĩ)\b", re.IGNORECASE),
    "personal_advice": re.compile(r"\b(my relationship|dating advice|medical advice|therapy|personal advice|tư vấn tình cảm|tư vấn cá nhân)\b", re.IGNORECASE),
}


def _normalize_query(query: str) -> str:
    return (query or "").strip().lower()


def normalize_intent(intent: str | None) -> Optional[RouterIntent]:
    if intent in SUPPORTED_INTENTS or intent == "out_of_scope":
        return intent  # type: ignore[return-value]
    return None


def normalize_confidence(confidence: str | None) -> RouterConfidence:
    if confidence in {"high", "medium", "low"}:
        return confidence  # type: ignore[return-value]
    return "low"


def heuristic_classify_intent(query: str) -> tuple[RouterIntent, str]:
    normalized = _normalize_query(query)
    if not normalized:
        return "mixed", "empty_query_default_in_scope"

    matches: list[str] = []
    for intent, keywords in _IN_SCOPE_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            matches.append(intent)

    if matches:
        unique = list(dict.fromkeys(matches))
        if len(unique) > 1:
            return "mixed", "heuristic_multi_intent_signal"
        return unique[0], "heuristic_in_scope_signal"

    for reason, pattern in _OUT_SCOPE_PATTERNS.items():
        if pattern.search(normalized):
            return "out_of_scope", f"heuristic_out_scope_{reason}"

    return "mixed", "heuristic_ambiguous_default_in_scope"


def decide_scope(
    query: str,
    router_intent: str | None,
    router_confidence: str | None,
    router_reasoning: str | None = None,
) -> ScopeDecision:
    normalized_intent = normalize_intent(router_intent)
    normalized_confidence = normalize_confidence(router_confidence)

    if normalized_intent in SUPPORTED_INTENTS:
        return ScopeDecision(
            is_out_of_scope=False,
            reason="router_in_scope",
            final_intent=normalized_intent,
            heuristic_used=False,
        )

    if normalized_intent == "out_of_scope" and normalized_confidence == "high":
        return ScopeDecision(
            is_out_of_scope=True,
            reason="router_out_of_scope_high_confidence",
            final_intent="out_of_scope",
            heuristic_used=False,
            refusal_reason="router_high_confidence_out_of_scope",
        )

    heuristic_intent, heuristic_reason = heuristic_classify_intent(query)
    if heuristic_intent in SUPPORTED_INTENTS:
        return ScopeDecision(
            is_out_of_scope=False,
            reason=f"{heuristic_reason}_router_fallback",
            final_intent=heuristic_intent,
            heuristic_used=True,
            heuristic_intent=heuristic_intent,
        )

    if normalized_intent == "out_of_scope":
        return ScopeDecision(
            is_out_of_scope=True,
            reason=f"router_out_of_scope_{normalized_confidence}_and_{heuristic_reason}",
            final_intent="out_of_scope",
            heuristic_used=True,
            heuristic_intent=heuristic_intent,
            refusal_reason="router_out_of_scope_with_heuristic_confirmation",
        )

    return ScopeDecision(
        is_out_of_scope=True,
        reason=f"router_parse_or_low_confidence_and_{heuristic_reason}",
        final_intent="out_of_scope",
        heuristic_used=True,
        heuristic_intent=heuristic_intent,
        refusal_reason="router_failed_and_heuristic_out_of_scope",
    )


def detect_out_of_scope(query: str) -> ScopeDecision:
    heuristic_intent, reason = heuristic_classify_intent(query)
    return ScopeDecision(
        is_out_of_scope=heuristic_intent == "out_of_scope",
        reason=reason,
        final_intent=heuristic_intent,
        heuristic_used=True,
        heuristic_intent=heuristic_intent,
        refusal_reason=reason if heuristic_intent == "out_of_scope" else None,
    )


def build_out_of_scope_payload(query: str = "") -> dict:
    return {
        "intent": "out_of_scope",
        "final_answer": get_refusal(query),
        "references": [],
        "caveats": [get_caveat(query)],
        "writer_notes": ["scope_guard_refusal"],
        "processing_warnings": ["out_of_scope_query"],
        "grounding_summary": {
            "verified_claim_count": 0,
            "weakly_supported_claim_count": 0,
            "conflicting_claim_count": 0,
            "unsupported_claim_count": 0,
            "reference_count": 0,
            "coverage_status": "insufficient",
        },
        "fallback_triggered": False,
    }
