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
    "M\u00ecnh t\u1eadp trung h\u1ed7 tr\u1ee3 c\u00e1c c\u00e2u h\u1ecfi li\u00ean quan \u0111\u1ebfn ph\u00e2n t\u00edch \u0111\u1ea7u t\u01b0 startup "
    "v\u00e0 market research cho nh\u00e0 \u0111\u1ea7u t\u01b0. N\u1ed9i dung b\u1ea1n h\u1ecfi hi\u1ec7n n\u1eb1m ngo\u00e0i ph\u1ea1m vi \u0111\u00f3."
)
OUT_OF_SCOPE_REFUSAL_EN = (
    "I'm focused on questions related to startup investment analysis "
    "and market research for investors. Your question is outside that scope."
)

OUT_OF_SCOPE_CAVEAT_VI = (
    "C\u00e2u h\u1ecfi n\u1eb1m ngo\u00e0i ph\u1ea1m vi ph\u00e2n t\u00edch \u0111\u1ea7u t\u01b0 "
    "(xu h\u01b0\u1edbng th\u1ecb tr\u01b0\u1eddng, quy \u0111\u1ecbnh, tin t\u1ee9c, ph\u00e2n t\u00edch \u0111\u1ed1i th\u1ee7)."
)
OUT_OF_SCOPE_CAVEAT_EN = (
    "This query is outside investor-research scope "
    "(market trends, regulation, news, competitor context)."
)

# Keep old names as defaults (Vietnamese) for backward compat
OUT_OF_SCOPE_REFUSAL = OUT_OF_SCOPE_REFUSAL_VI
OUT_OF_SCOPE_CAVEAT = OUT_OF_SCOPE_CAVEAT_VI

GREETING_RESPONSE_VI = (
    "Xin ch\u00e0o, t\u00f4i l\u00e0 Fami \u2013 tr\u1ee3 l\u00fd h\u1ed7 tr\u1ee3 thu th\u1eadp v\u00e0 "
    "ph\u00e2n t\u00edch th\u00f4ng tin \u0111\u1ea7u t\u01b0 c\u1ee7a AISEP. T\u00f4i c\u00f3 th\u1ec3 t\u00ecm "
    "ki\u1ebfm, t\u1ed5ng h\u1ee3p v\u00e0 suy lu\u1eadn t\u1eeb c\u00e1c ngu\u1ed3n th\u00f4ng tin li\u00ean "
    "quan \u0111\u1ebfn th\u1ecb tr\u01b0\u1eddng, ng\u00e0nh, \u0111\u1ed1i th\u1ee7 c\u1ea1nh tranh, tin t\u1ee9c "
    "v\u00e0 r\u1ee7i ro, h\u1ed7 tr\u1ee3 b\u1ea1n c\u00f3 th\u00eam g\u00f3c nh\u00ecn tr\u01b0\u1edbc khi "
    "\u0111\u1ea7u t\u01b0. B\u1ea1n mu\u1ed1n t\u00f4i t\u00ecm hi\u1ec3u v\u1ec1 ng\u00e0nh ho\u1eb7c th\u1ecb "
    "tr\u01b0\u1eddng n\u00e0o ?"
)

_VIETNAMESE_MARKERS = re.compile(
    r"[\u00e0\u00e1\u1ea3\u00e3\u1ea1\u0103\u1eaf\u1eb1\u1eb3\u1eb5\u1eb7\u00e2\u1ea5\u1ea7\u1ea9\u1eab\u1ead"
    r"\u00e8\u00e9\u1ebb\u1ebd\u1eb9\u00ea\u1ebf\u1ec1\u1ec3\u1ec5\u1ec7\u00ec\u00ed\u1ec9\u0129\u1ecb"
    r"\u00f2\u00f3\u1ecf\u00f5\u1ecd\u00f4\u1ed1\u1ed3\u1ed5\u1ed7\u1ed9\u01a1\u1edb\u1edd\u1edf\u1ee1\u1ee3"
    r"\u00f9\u00fa\u1ee7\u0169\u1ee5\u01b0\u1ee9\u1eeb\u1eed\u1eef\u1ef1\u1ef3\u00fd\u1ef7\u1ef9\u1ef5\u0111]"
)
_GREETING_PATTERN = re.compile(
    r"^\s*(?:"
    r"hi+|hello+|hey+|alo+|halo+|"
    r"xin\s+ch(?:ao|\u00e0o)|"
    r"ch(?:ao|\u00e0o)"
    r")(?:\s+(?:ban|b\u1ea1n|em|anh|chi|\u1ecb|there|bot|fami))?\s*[!?,.\s~]*$",
    re.IGNORECASE,
)


def _is_vietnamese(text: str) -> bool:
    """Lightweight heuristic: True if text contains Vietnamese diacritics."""
    return bool(_VIETNAMESE_MARKERS.search(text or ""))


def is_greeting(query: str) -> bool:
    return bool(_GREETING_PATTERN.match((query or "").strip()))


def get_refusal(query: str = "") -> str:
    if is_greeting(query):
        return GREETING_RESPONSE_VI
    return OUT_OF_SCOPE_REFUSAL_VI if _is_vietnamese(query) else OUT_OF_SCOPE_REFUSAL_EN


def get_caveat(query: str = "") -> str:
    if is_greeting(query):
        return ""
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
        "xu h\u01b0\u1edbng", "trend", "th\u1ecb tr\u01b0\u1eddng", "market", "growth", "funding", "adoption",
        "fintech", "saas", "\u0111\u1ea7u t\u01b0", "v\u1ed1n", "valuation", "startup", "investment",
    },
    "regulation": {
        "quy \u0111\u1ecbnh", "ngh\u1ecb \u0111\u1ecbnh", "th\u00f4ng t\u01b0", "regulation", "policy", "compliance",
        "license", "effective date", "ph\u00e1p l\u00fd", "gi\u1ea5y ph\u00e9p", "lu\u1eadt",
    },
    "news": {
        "tin t\u1ee9c", "m\u1edbi nh\u1ea5t", "c\u1eadp nh\u1eadt", "latest", "news", "announced", "recent",
        "c\u00f4ng b\u1ed1", "v\u1eeba", "breaking",
    },
    "competitor_context": {
        "\u0111\u1ed1i th\u1ee7", "so s\u00e1nh", "vs", "competitor", "landscape", "player", "positioning",
        "benchmark", "market map", "ai l\u00e0 \u0111\u1ed1i th\u1ee7",
    },
}

_OUT_SCOPE_PATTERNS = {
    "weather": re.compile(r"\b(weather|temperature|rain|forecast|humidity|storm|th\u1eddi ti\u1ebft|m\u01b0a|n\u1eafng)\b", re.IGNORECASE),
    "math": re.compile(r"\b(calculate|solve|equation|integral|derivative|what is \d+\s*[\+\-\*/]|\d+\s*[\+\-\*/]\s*\d+|b\u1eb1ng bao nhi\u00eau|t\u00ednh gi\u00fap)\b", re.IGNORECASE),
    "coding": re.compile(r"\b(code|debug|python|javascript|java|c\+\+|sql|api endpoint|programming|l\u1eadp tr\u00ecnh|vi\u1ebft code)\b", re.IGNORECASE),
    "translation": re.compile(r"\b(translate|translation|d\u1ecbch sang|d\u1ecbch gi\u00fap)\b", re.IGNORECASE),
    "entertainment": re.compile(r"\b(movie|song|celebrity|football score|trivia|game lore|phim|ca s\u0129)\b", re.IGNORECASE),
    "personal_advice": re.compile(r"\b(my relationship|dating advice|medical advice|therapy|personal advice|t\u01b0 v\u1ea5n t\u00ecnh c\u1ea3m|t\u01b0 v\u1ea5n c\u00e1 nh\u00e2n)\b", re.IGNORECASE),
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
    if is_greeting(query):
        return "out_of_scope", "heuristic_greeting_short_circuit"

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
    greeting_query = is_greeting(query)
    caveat = get_caveat(query)
    return {
        "intent": "out_of_scope",
        "final_answer": get_refusal(query),
        "references": [],
        "caveats": [caveat] if caveat else [],
        "suggested_next_questions": [],
        "writer_notes": ["greeting_response"] if greeting_query else ["scope_guard_refusal"],
        "processing_warnings": ["greeting_query"] if greeting_query else ["out_of_scope_query"],
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
