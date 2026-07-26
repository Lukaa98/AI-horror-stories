import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from ranking_engine import (  # noqa: E402
    _image_category,
    _narration_visual_cues,
    _order_images_for_narration,
    _automatic_performance_beats,
    _performance_instructions,
    _select_image_for_cue,
    _ranking_rail_layout,
)


def test_ranking_rail_is_fixed_for_every_source_photo_shape():
    """Photo dimensions are deliberately absent from the rail layout API."""
    canvas = (1080, 1920)
    expected = _ranking_rail_layout(canvas)

    for _source_photo_size in [(2000, 600), (600, 2000), (1200, 1200), (3840, 2160)]:
        assert _ranking_rail_layout(canvas) == expected

    assert len(expected) == 4
    assert len({top for _, top, _, _ in expected}) == 4
    assert len({right - left for left, _, right, _ in expected}) == 1


def test_visual_cues_follow_narration_order():
    narration = "Inside, the gated manual steals the show. Then notice those unique wheels."
    assert _narration_visual_cues(narration) == ["interior", "wheel"]


def test_matching_detail_images_are_prioritized_without_dropping_fallbacks():
    images = [
        Path("front-left-exterior.jpg"),
        Path("wheel-detail.jpg"),
        Path("interior-dashboard.jpg"),
    ]
    ordered = _order_images_for_narration(
        images,
        "The gated shifter feels special. Its wheel design is unique.",
    )
    assert ordered == [images[2], images[1], images[0]]
    assert [_image_category(path) for path in ordered] == ["interior", "wheel", "front"]


def test_old_drafts_receive_automatic_performance_beats():
    beats = _automatic_performance_beats(
        "At number four, meet the R8. But the gated manual changes everything."
    )
    assert [beat["style"] for beat in beats] == ["energetic_reveal", "intrigued"]
    assert beats[1]["visual_cue"] == "interior"


def test_emphasis_words_become_acting_direction_without_text_misspelling():
    beat = {
        "style": "energetic_reveal",
        "emphasis_words": ["legendary R8"],
    }
    instructions = _performance_instructions(beat)
    assert "Slightly sustain" in instructions
    assert "legendary R8" in instructions


def test_plural_visual_terms_are_detected_in_spoken_order():
    assert _narration_visual_cues(
        "Its blackened headlights lead into five-spoke wheels and revised taillights."
    ) == ["front", "wheel", "rear"]


def test_missing_engine_shot_falls_back_to_exterior_not_interior():
    images = [
        Path("interior-dashboard-01.jpg"),
        Path("side-exterior-01.jpg"),
        Path("rear-exterior-01.jpg"),
    ]

    selected = _select_image_for_cue(images, "engine", set())

    assert selected == images[1]
