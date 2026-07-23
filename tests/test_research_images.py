import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars" / "automation"))

from research_request import valid_images  # noqa: E402
from cars_and_bids import (  # noqa: E402
    augment_narration_with_current_value,
    format_current_value,
    infer_search_params,
    parse_year_range,
    round_current_value,
)


def test_valid_images_keeps_supported_decodable_files_and_removes_corrupt_ones(tmp_path):
    for filename in ("front.jpg", "rear.png", "interior.webp"):
        Image.new("RGB", (40, 30), (30, 60, 90)).save(tmp_path / filename)
    (tmp_path / "broken.jpg").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("not an image")

    paths = valid_images(tmp_path)

    assert [path.name for path in paths] == ["front.jpg", "interior.webp", "rear.png"]
    assert not (tmp_path / "broken.jpg").exists()


def test_infer_search_params_uses_make_and_model_from_search_hint():
    assert infer_search_params("Audi R8 V10 Plus") == {"make": "audi", "model": "r8"}
    assert infer_search_params("Chevrolet Corvette C5 Z06") == {"make": "chevrolet", "model": "corvette"}


def test_parse_year_range_handles_ranges_and_single_years():
    assert parse_year_range("2007-2012") == (2007, 2012)
    assert parse_year_range("2014") == (2014, 2014)


def test_round_current_value_formats_examples_like_user_requested():
    assert round_current_value(109000) == 110000
    assert format_current_value(109000) == "$110K"
    assert round_current_value(7800) == 8000
    assert format_current_value(7800) == "$8K"


def test_augment_narration_with_current_value_adds_cars_and_bids_context():
    entry = {
        "one_line_fact": "Debuting in 2007 at $109,000 with 414 horsepower, the R8 V8 4.2 Coupe introduced Audi's supercar prowess.",
        "current_value_display": "$110K",
    }
    text = augment_narration_with_current_value(entry)
    assert "Today, clean examples trade around" in text
    assert "$110K" in text
