from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from services import get_vision_extractor


async def main(path: Path) -> None:
    extractor = get_vision_extractor()
    image_bytes = path.read_bytes()
    result = await extractor.extract(image_bytes=image_bytes)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the vision extractor locally without Telegram bot context.",
    )
    parser.add_argument("path", type=Path, help="Path to the image file to parse")
    args = parser.parse_args()

    if not args.path.exists():
        raise SystemExit(f"File not found: {args.path}")

    asyncio.run(main(args.path))
