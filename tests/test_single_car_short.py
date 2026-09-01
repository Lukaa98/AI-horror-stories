import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from single_car_short import ALLOWED_MEDIA_TYPES, TARGET_WORDS, _word_count  # noqa: E402


def test_word_count_handles_contractions_and_hyphenated_terms():
    assert _word_count("It's a four-wheel-drive Golf R.") == 5


def test_one_minute_script_range_is_fast_but_bounded():
    assert TARGET_WORDS == (175, 190)


def test_interior_media_is_available_for_cabin_script_scenes():
    assert "interior" in ALLOWED_MEDIA_TYPES
