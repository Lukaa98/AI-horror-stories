import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars" / "automation"))

from research_request import valid_images  # noqa: E402


def test_valid_images_keeps_supported_decodable_files_and_removes_corrupt_ones(tmp_path):
    for filename in ("front.jpg", "rear.png", "interior.webp"):
        Image.new("RGB", (40, 30), (30, 60, 90)).save(tmp_path / filename)
    (tmp_path / "broken.jpg").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("not an image")

    paths = valid_images(tmp_path)

    assert [path.name for path in paths] == ["front.jpg", "interior.webp", "rear.png"]
    assert not (tmp_path / "broken.jpg").exists()
