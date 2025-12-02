from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from services import get_vision_extractor

SAMPLE_PATH = Path("samples/sample.png")


async def main(path: Path) -> None:
    extractor = get_vision_extractor()
    image_bytes = path.read_bytes()
    parsed = await extractor.extract(image_bytes=image_bytes)
    print(json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the vision extractor against a sample image.")
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=f"Path to an image file. Defaults to {SAMPLE_PATH} if it exists.",
    )
    args = parser.parse_args()

    target = Path(args.path) if args.path else SAMPLE_PATH
    if not target.exists():
        raise SystemExit(
            f"Sample file not found: {target}. Provide a path to an image, e.g. 'python scripts/test_vision_extractor.py /path/to/image.jpg'."
        )

    asyncio.run(main(target))
