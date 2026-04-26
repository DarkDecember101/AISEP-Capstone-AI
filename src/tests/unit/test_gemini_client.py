from pydantic import BaseModel

from src.shared.providers.llm.gemini_client import GeminiClient


class _SampleSchema(BaseModel):
    value: str


class _FakeResponse:
    def __init__(self, text=None, parsed=None):
        self.text = text
        self.parsed = parsed


class _FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)

    def generate_content(self, **kwargs):
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.models = _FakeModels(responses)


def test_generate_structured_prefers_sdk_parsed_model(monkeypatch):
    parsed_model = _SampleSchema(value="ok")
    fake_client = _FakeClient([
        _FakeResponse(text='{"value":"unterminated}', parsed=parsed_model),
    ])

    monkeypatch.setattr(
        "src.shared.providers.llm.gemini_client.genai.Client",
        lambda **kwargs: fake_client,
    )

    client = GeminiClient()
    result = client.generate_structured("prompt", _SampleSchema)

    assert result.value == "ok"


def test_generate_structured_retries_after_parse_error(monkeypatch):
    fake_client = _FakeClient([
        _FakeResponse(text='{"value":"unterminated}'),
        _FakeResponse(text='{"value":"recovered"}'),
    ])

    monkeypatch.setattr(
        "src.shared.providers.llm.gemini_client.genai.Client",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(
        "src.shared.providers.llm.gemini_client.time.sleep",
        lambda seconds: None,
    )

    client = GeminiClient()
    client.max_retries = 1
    result = client.generate_structured("prompt", _SampleSchema)

    assert result.value == "recovered"
