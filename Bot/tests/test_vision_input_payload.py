import pytest

import pytest

pytest.importorskip("openai")

from types import SimpleNamespace

from services.vision_extractor import VisionExtractor


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def test_build_input_payload_uses_required_types():
    extractor = VisionExtractor(client=SimpleNamespace(), model="test-model")
    payload = extractor._build_input_payload("instructions", b"\xff\xd8\xff", extra_text="extra")

    assert isinstance(payload, list)
    assert payload[0]["role"] == "user"
    content = payload[0]["content"]

    assert content[0]["type"] == "input_text"
    assert "text" in content[0]
    assert content[1]["type"] == "input_text"
    assert content[1]["text"] == "extra"
    assert content[2]["type"] == "input_image"
    assert isinstance(content[2]["image_url"], str)
    assert content[2]["image_url"].startswith("data:image/jpeg;base64,")


def test_should_fallback_on_unsupported_value():
    extractor = VisionExtractor(client=SimpleNamespace(), model="gpt-4.1-mini")

    error = SimpleNamespace(response={"error": {"code": "unsupported_value"}})

    assert extractor._should_fallback(error) is True


def test_should_fallback_on_verify_message():
    extractor = VisionExtractor(client=SimpleNamespace(), model="gpt-4.1")

    error = SimpleNamespace(
        response={
            "error": {
                "message": "Your organization must be verified to use the model 'gpt-4.1'.",
                "code": "unsupported_value",
            }
        }
    )

    assert extractor._should_fallback(error) is True


@pytest.mark.anyio("asyncio")
async def test_retry_with_fallback_switches_model(monkeypatch):
    extractor = VisionExtractor(client=SimpleNamespace(), model="gpt-4.1-mini")
    calls: list[str] = []

    async def fake_call(input_payload, json_schema):  # type: ignore[override]
        calls.append(extractor._model)
        return "ok"

    monkeypatch.setattr(extractor, "_call_model", fake_call)

    result = await extractor._retry_with_fallback([{}], {})

    assert result == "ok"
    assert extractor._fallback_used is True
    assert extractor._model in {"gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"}
    assert calls == [extractor._model]


def test_fallback_chain_includes_accessible_model():
    extractor = VisionExtractor(client=SimpleNamespace(), model="gpt-4.1")
    assert "gpt-4o-mini" in extractor._fallback_candidates
