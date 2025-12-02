Binary sample images were intentionally removed from version control so pull requests stay light and reviewable.

Use your own local sample instead:

1) Drop a document photo/scan into this folder. The helper script defaults to `samples/sample.png`, but any filename works (png, jpg/jpeg, heic, webp, etc.).
2) Run the script:
       python scripts/test_vision_extractor.py            # uses samples/sample.png if present
       python scripts/test_vision_extractor.py /path/to/your/image.jpg

Tips:
* Keep your file untracked—`git status` should stay clean—so no binary diffs end up in PRs.
* Use a realistic doc (акт, счёт-фактура, ТН/ТТН) to mirror production outputs.
* If the default file is missing, the script will tell you and show the exact command with your own path.
