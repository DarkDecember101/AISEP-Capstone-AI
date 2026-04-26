import asyncio
import json
import re
import time
from typing import Type, TypeVar

import httpx
from google import genai
from google.genai import types, errors
from pydantic import BaseModel, ValidationError

from src.shared.config.settings import settings
from src.shared.logging.logger import setup_logger

logger = setup_logger("gemini_client")

T = TypeVar("T", bound=BaseModel)


class GeminiQuotaExceededError(Exception):
    pass


class GeminiTransientError(Exception):
    pass


class GeminiResponseParseError(Exception):
    pass


def is_transient_gemini_error(exc: Exception) -> bool:
    return isinstance(exc, GeminiTransientError)


class GeminiClient:
    def __init__(self):
        if not settings.GOOGLE_CLOUD_PROJECT:
            logger.warning(
                "GOOGLE_CLOUD_PROJECT is not set. Vertex AI calls will fail.")

        self.client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        self.default_model = "gemini-2.5-flash"
        self.max_retries = 3

    def _build_contents(self, prompt: str, image_paths: list[str] | None):
        contents = [prompt]

        if image_paths:
            for ip in image_paths:
                try:
                    with open(ip, "rb") as f:
                        image_bytes = f.read()
                    contents.append(
                        types.Part.from_bytes(
                            data=image_bytes,
                            mime_type="image/png",
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to load image part {ip}: {e}")
                    raise

        return contents

    def _extract_retry_seconds(self, message: str, default: int = 30) -> int:
        if not message:
            return default

        # match "Please retry in 30.524102128s."
        match = re.search(r"retry in\s+(\d+(?:\.\d+)?)s",
                          message, re.IGNORECASE)
        if match:
            return max(1, int(float(match.group(1))) + 1)

        # match retryDelay style if appears in stringified payload
        match = re.search(r"'retryDelay':\s*'(\d+)s'", message, re.IGNORECASE)
        if match:
            return max(1, int(match.group(1)))

        return default

    def _is_daily_or_hard_quota_exhausted(self, message: str) -> bool:
        if not message:
            return False

        lowered = message.lower()

        hard_quota_markers = [
            "generate_content_free_tier_requests",
            "perdayperprojectpermodel-freetier",
            "quota exceeded for metric",
            "you exceeded your current quota",
        ]

        return any(marker in lowered for marker in hard_quota_markers)

    def _classify_api_error(self, e: errors.APIError) -> Exception:
        code = getattr(e, "code", None)
        message = getattr(e, "message", str(e))

        logger.error(f"Gemini APIError code={code}, message={message}")

        if code == 429:
            if self._is_daily_or_hard_quota_exhausted(message):
                return GeminiQuotaExceededError(message)
            return GeminiTransientError(message)

        if code in (500, 503, 504):
            return GeminiTransientError(message)

        return e

    def _classify_transport_error(self, e: Exception) -> Exception:
        if isinstance(e, (httpx.TimeoutException, httpx.TransportError)):
            logger.warning("Gemini transport error: %s", e)
            return GeminiTransientError(str(e))
        return e

    def _extract_json_substring(self, raw_text: str) -> str | None:
        """Extract the outermost JSON object/array from noisy model output."""
        if not raw_text:
            return None

        stripped = raw_text.strip()
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
        if fenced:
            stripped = fenced.group(1).strip()

        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped
        if stripped.startswith("[") and stripped.endswith("]"):
            return stripped

        obj_start = stripped.find("{")
        obj_end = stripped.rfind("}")
        if obj_start != -1 and obj_end > obj_start:
            return stripped[obj_start:obj_end + 1]

        arr_start = stripped.find("[")
        arr_end = stripped.rfind("]")
        if arr_start != -1 and arr_end > arr_start:
            return stripped[arr_start:arr_end + 1]

        return None

    def _parse_structured_response(
        self,
        response,
        response_schema: Type[T],
    ) -> T:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, response_schema):
            return parsed
        if isinstance(parsed, BaseModel):
            return response_schema(**parsed.model_dump())
        if isinstance(parsed, dict):
            return response_schema(**parsed)

        response_text = getattr(response, "text", None)
        if not response_text:
            raise GeminiResponseParseError("Gemini returned empty structured response.")

        candidate_payloads = [response_text]
        extracted = self._extract_json_substring(response_text)
        if extracted and extracted != response_text:
            candidate_payloads.append(extracted)

        last_error: Exception | None = None
        for payload in candidate_payloads:
            try:
                result_dict = json.loads(payload)
                return response_schema(**result_dict)
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e

        preview = re.sub(r"\s+", " ", response_text).strip()[:240]
        if isinstance(last_error, ValidationError):
            raise GeminiResponseParseError(
                f"Gemini returned schema-invalid structured output: {last_error}"
            ) from last_error

        raise GeminiResponseParseError(
            f"Invalid JSON returned by Gemini: {last_error}. Response preview: {preview}"
        ) from last_error

    def generate_structured(
        self,
        prompt: str,
        response_schema: Type[T],
        model_name: str | None = None,
        image_paths: list[str] | None = None,
    ) -> T:
        model = model_name or self.default_model
        contents = self._build_contents(prompt, image_paths)

        attempt = 0
        last_error = None

        while attempt <= self.max_retries:
            attempt += 1

            logger.info(
                f"Calling Gemini ({model}) for structured output "
                f"(multimodal items: {len(contents)}, attempt: {attempt}/{self.max_retries + 1})."
            )

            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=response_schema,
                        temperature=0.2,
                        max_output_tokens=16384,
                    ),
                )

                if not response.text:
                    raise GeminiResponseParseError(
                        "Gemini returned empty response.text")

                return self._parse_structured_response(response, response_schema)

            except errors.APIError as e:
                classified = self._classify_api_error(e)

                if isinstance(classified, GeminiQuotaExceededError):
                    # daily/free-tier quota style -> fail fast
                    raise classified from e

                if isinstance(classified, GeminiTransientError):
                    if attempt > self.max_retries:
                        raise classified from e

                    wait_seconds = self._extract_retry_seconds(
                        getattr(e, "message", str(e)),
                        default=min(10 * attempt, 60),
                    )
                    logger.warning(
                        f"Transient Gemini error. Sleeping {wait_seconds}s before retry..."
                    )
                    time.sleep(wait_seconds)
                    last_error = classified
                    continue

                raise

            except GeminiResponseParseError as e:
                logger.error("Failed to parse Gemini structured response: %s", e)
                last_error = e
                if attempt > self.max_retries:
                    raise last_error from e
                wait_seconds = min(5 * attempt, 30)
                logger.warning(
                    f"Structured parse error from Gemini (attempt {attempt}/{self.max_retries + 1}). "
                    f"Retrying in {wait_seconds}s..."
                )
                time.sleep(wait_seconds)
                continue

            except (httpx.TimeoutException, httpx.TransportError) as e:
                classified = self._classify_transport_error(e)
                if attempt > self.max_retries:
                    raise classified from e

                wait_seconds = self._extract_retry_seconds(
                    str(e),
                    default=min(5 * attempt, 30),
                )
                logger.warning(
                    "Transient Gemini transport error (attempt %s/%s). Retrying in %ss...",
                    attempt,
                    self.max_retries + 1,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                last_error = classified
                continue

            except Exception as e:
                logger.error(f"Unexpected Gemini error: {e}")
                raise

        if last_error:
            raise last_error

        raise RuntimeError("Gemini call failed unexpectedly.")

    async def generate_structured_async(
        self,
        prompt: str,
        response_schema: Type[T],
        model_name: str | None = None,
        image_paths: list[str] | None = None,
        timeout: float = 30.0,
    ) -> T:
        """
        Async wrapper for generate_structured.

        Runs the blocking call in a thread executor so that `time.sleep()`
        inside the retry logic never freezes the asyncio event loop.
        A hard `timeout` (default 60 s) is enforced via asyncio.wait_for;
        raise asyncio.TimeoutError if the call takes too long.
        """
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.generate_structured(
                    prompt=prompt,
                    response_schema=response_schema,
                    model_name=model_name,
                    image_paths=image_paths,
                ),
            ),
            timeout=timeout,
        )
