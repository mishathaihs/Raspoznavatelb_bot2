import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.currency_detector import detect_currency


def _show(text: str) -> None:
    code, words = detect_currency(text)
    print(text.strip()[:60], "->", code, "/", words)


if __name__ == "__main__":
    samples = [
        "Сумма: 120 BYN (сто двадцать белорусских рублей)",
        "Итого к оплате: 550 руб. (пятьсот пятьдесят рублей 00 копеек)",
        "Оплатить 99,50 бел. руб.",
        "ИНН 7701234567 КПП 770101001 Сбербанк", 
        "АСБ Беларусбанк УНП 123456789",
    ]
    for sample in samples:
        _show(sample)
