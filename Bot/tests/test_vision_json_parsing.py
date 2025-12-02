import pytest

from services.vision_extractor import VisionExtractionError, _extract_parsed_payload

def test_parses_plain_json_without_raw_ocr():
    response = '{"document_type": "act", "fields": {}, "positions": []}'
    parsed = _extract_parsed_payload(response)
    assert parsed["document_type"] == "act"


def test_parses_json_with_markdown_fence():
    response = """```json\n{\n  \"document_type\": \"waybill\",\n  \"fields\": {},\n  \"positions\": []\n}\n```"""
    parsed = _extract_parsed_payload(response)
    assert parsed["document_type"] == "waybill"


def test_parses_json_and_strips_raw_ocr_text():
    response = """
    ```json
    {
      "document_type": "invoice",
      "fields": {},
      "positions": [],
      "raw_ocr_text": "строка 1\nстрока 2"
    }
    ```
    """
    parsed = _extract_parsed_payload(response)
    assert parsed["document_type"] == "invoice"
    assert "raw_ocr_text" not in parsed


def test_parses_json_with_noise_around_and_trailing_raw_text():
    response = {
        "output": [
            {
                "content": [
                    {
                        "text": "before {\"document_type\": \"act\", \"fields\": {}, \"positions\": []} after",
                    }
                ]
            }
        ]
    }
    parsed = _extract_parsed_payload(response)
    assert parsed["document_type"] == "act"


def test_invalid_json_raises():
    with pytest.raises(VisionExtractionError):
        _extract_parsed_payload("not-a-json-response")

