from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Iterable, List

TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
EMBEDDING_DIMENSION = 64


class EmbeddingService:
    @staticmethod
    def tokenize(text: str) -> List[str]:
        if not text:
            return []
        return TOKEN_PATTERN.findall(text.lower())

    @staticmethod
    def build_embedding(text: str, dimension: int = EMBEDDING_DIMENSION) -> List[float]:
        tokens = EmbeddingService.tokenize(text)
        if not tokens:
            return [0.0] * dimension

        counts = Counter(tokens)
        vector = [0.0] * dimension
        for token, count in counts.items():
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % dimension
            weight = 1.0 + math.log1p(count)
            vector[index] += weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return [0.0] * dimension

        return [value / norm for value in vector]

    @staticmethod
    def cosine_similarity(left: Iterable[float] | None, right: Iterable[float] | None) -> float:
        if not left or not right:
            return 0.0

        left_list = list(left)
        right_list = list(right)
        length = min(len(left_list), len(right_list))
        if length == 0:
            return 0.0

        dot_product = sum(left_list[index] * right_list[index]
                          for index in range(length))
        left_norm = math.sqrt(
            sum(value * value for value in left_list[:length]))
        right_norm = math.sqrt(
            sum(value * value for value in right_list[:length]))
        if left_norm == 0 or right_norm == 0:
            return 0.0

        score = dot_product / (left_norm * right_norm)
        return max(0.0, min(1.0, score))

    @staticmethod
    def normalize_similarity(score: float) -> float:
        return max(0.0, min(1.0, score)) * 100.0
