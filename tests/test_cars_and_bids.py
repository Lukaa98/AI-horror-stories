import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from cars_and_bids import choose_reviewed_images  # noqa: E402


def test_generation_match_does_not_require_exact_trim(tmp_path):
    image = tmp_path / "exterior.jpg"
    image.write_bytes(b"image")
    payload = {
        "reviews": [{
            "path": image.name,
            "is_target_vehicle": True,
            "generation_match_confidence": 8,
            "variant_match_confidence": 3,
            "target_match_confidence": 8,
            "reject": False,
            "shot_type": "exterior",
            "scene_fit_tags": ["exterior"],
            "quality_score": 8,
            "composition_score": 8,
        }]
    }

    selected = choose_reviewed_images(tmp_path, {"name": "Boxster 987 S"}, payload)

    assert selected == ["images/boxster-987-s/exterior.jpg"]
