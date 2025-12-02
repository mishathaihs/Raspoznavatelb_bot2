from bot.handlers import _vision_error_message
from services.vision_extractor import VisionExtractionError


def test_vision_json_error_message_is_technical():
    exc = VisionExtractionError("Vision response is not valid JSON")
    message = _vision_error_message(exc)
    lowered = message.lower()
    assert "технической" in lowered
    assert "не связано с качеством фото" in lowered
    assert "json" in lowered or "некорректный" not in lowered
