import argparse
from pathlib import Path

from PIL import Image

from services.ocr_party_extractor import (
    CUSTOMER_BOX_RATIO,
    SUPPLIER_BOX_RATIO,
    extract_parties,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug OCR party extraction for waybills")
    parser.add_argument("image_path", type=Path, help="Path to the waybill image")
    args = parser.parse_args()

    image = Image.open(args.image_path)
    width, height = image.size
    print(f"Image size: {width}x{height}")

    supplier_box = (
        int(SUPPLIER_BOX_RATIO[0] * width),
        int(SUPPLIER_BOX_RATIO[1] * height),
        int(SUPPLIER_BOX_RATIO[2] * width),
        int(SUPPLIER_BOX_RATIO[3] * height),
    )
    customer_box = (
        int(CUSTOMER_BOX_RATIO[0] * width),
        int(CUSTOMER_BOX_RATIO[1] * height),
        int(CUSTOMER_BOX_RATIO[2] * width),
        int(CUSTOMER_BOX_RATIO[3] * height),
    )
    print(f"Supplier crop: {supplier_box}")
    print(f"Customer crop: {customer_box}")

    info = extract_parties(image, doc_type="товарная_накладная")
    print(f"Supplier line OCR: {info.supplier_line_ocr}")
    print(f"Customer line OCR: {info.customer_line_ocr}")
    print(f"Supplier name raw: {info.supplier_name_raw}")
    print(f"Customer name raw: {info.customer_name_raw}")


if __name__ == "__main__":
    main()
