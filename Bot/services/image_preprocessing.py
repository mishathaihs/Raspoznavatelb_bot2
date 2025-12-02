from __future__ import annotations

import io
from typing import Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps


def preprocess_image_bytes(image_bytes: bytes) -> tuple[bytes, Image.Image]:
    """Prepare image bytes for OCR and vision models.

    Steps:
    - rotate based on EXIF
    - convert to grayscale
    - upscale small images to ~1500px width
    - deskew via moments
    - adaptive threshold to improve contrast
    """

    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)
    rgb = image.convert("RGB")
    gray = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.2, beta=5)

    if gray.shape[1] < 1500:
        scale = 1500 / gray.shape[1]
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gray = _deskew(gray)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10
    )
    processed_image = Image.fromarray(thresh)
    buffer = io.BytesIO()
    processed_image.save(buffer, format="JPEG")
    return buffer.getvalue(), processed_image


def _deskew(gray: np.ndarray) -> np.ndarray:
    coords = np.column_stack(np.where(gray < 255))
    if coords.size == 0:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    (h, w) = gray.shape[:2]
    center = (w // 2, h // 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


__all__ = ["preprocess_image_bytes"]
