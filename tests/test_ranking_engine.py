import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation"), str(ROOT / "horror_stories" / "src")]

from ranking_engine import _ranking_rail_layout  # noqa: E402


def test_ranking_rail_is_fixed_for_every_source_photo_shape():
    """Photo dimensions are deliberately absent from the rail layout API."""
    canvas = (1080, 1920)
    expected = _ranking_rail_layout(canvas)

    for _source_photo_size in [(2000, 600), (600, 2000), (1200, 1200), (3840, 2160)]:
        assert _ranking_rail_layout(canvas) == expected

    assert len(expected) == 4
    assert len({top for _, top, _, _ in expected}) == 4
    assert len({right - left for left, _, right, _ in expected}) == 1
