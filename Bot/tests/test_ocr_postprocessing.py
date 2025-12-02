from services.vision_extractor import reconcile_company_field


def test_reconcile_prefers_ocr_variant_when_similar():
    raw_ocr_text = 'Исполнитель: ООО "ЛойкоБелРус"\nЗаказчик: ООО "Покупатель"'
    model_value = 'ООО "ЛюксоБелРус"'

    reconciled, replaced = reconcile_company_field(model_value, raw_ocr_text)

    assert replaced is True
    assert reconciled == 'ООО "ЛойкоБелРус"'

