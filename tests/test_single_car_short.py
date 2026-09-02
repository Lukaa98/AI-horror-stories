import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from single_car_short import (  # noqa: E402
    ACCEPTABLE_WORDS,
    ALLOWED_MEDIA_TYPES,
    TARGET_WORDS,
    _strip_citations,
    _visual_highlight_for_scenes,
    _word_count,
    order_media_for_scenes,
)


def test_word_count_handles_contractions_and_hyphenated_terms():
    assert _word_count("It's a four-wheel-drive Golf R.") == 5


def test_one_minute_script_range_is_fast_but_bounded():
    assert TARGET_WORDS == (175, 190)
    assert ACCEPTABLE_WORDS[0] <= 199 <= ACCEPTABLE_WORDS[1]


def test_interior_media_is_available_for_cabin_script_scenes():
    assert "interior" in ALLOWED_MEDIA_TYPES


def test_strip_citations_removes_inline_markdown_links_and_urls():
    text = (
        "It makes 420 horsepower ([ru.wikipedia.org](https://ru.wikipedia.org/wiki/Audi_R8?utm_source=openai)) "
        "and tops out near 200 mph (https://automonitor.io/audi-r8-specs)."
    )
    cleaned = _strip_citations(text)
    assert "wikipedia" not in cleaned
    assert "automonitor" not in cleaned
    assert "http" not in cleaned
    assert cleaned.startswith("It makes 420 horsepower and tops out near 200 mph")


def test_order_media_for_scenes_does_not_repeat_a_photo_while_others_are_unused():
    scenes = [
        {"media_type": "interior"},
        {"media_type": "interior"},
        {"media_type": "interior"},
    ]
    media = [
        {"path": "interior-06.jpg", "type": "interior"},
        {"path": "interior-07.jpg", "type": "interior"},
        {"path": "exterior-01.jpg", "type": "exterior"},
    ]
    ordered = order_media_for_scenes(scenes, media)
    assert [item["path"] for item in ordered] == ["interior-06.jpg", "interior-07.jpg", "exterior-01.jpg"]


def test_order_media_for_scenes_repeats_only_once_every_photo_is_used():
    scenes = [{"media_type": "exterior"}, {"media_type": "exterior"}, {"media_type": "exterior"}]
    media = [{"path": "exterior-01.jpg", "type": "exterior"}, {"path": "exterior-02.jpg", "type": "exterior"}]
    ordered = order_media_for_scenes(scenes, media)
    assert [item["path"] for item in ordered] == ["exterior-01.jpg", "exterior-02.jpg", "exterior-01.jpg"]


def test_gather_media_uses_real_ai_review_categories_for_media_types(tmp_path, monkeypatch):
    """gather_media must route through research_request's real per-image AI
    review (review_and_rename_entry_images) -- the same one the ranking/
    battle pipeline uses to actually check "is this the front/side/interior
    of this generation" -- rather than trusting scrape_entry_images' own
    coarser first-pass shot_type guess."""
    import single_car_short

    images_dir = tmp_path / "images"
    car_dir = images_dir / "audi-tt"
    car_dir.mkdir(parents=True)
    for name in ["front-01.jpg", "interior-02.jpg", "engine-03.jpg"]:
        (car_dir / name).write_bytes(b"fake-image-bytes")

    def fake_scrape_entry_images(scraper_dir, dest, entry):
        return (
            [
                "images/audi-tt/front-01.jpg",
                "images/audi-tt/interior-02.jpg",
                "images/audi-tt/engine-03.jpg",
            ],
            {"selected_auction": {"url": "https://example.com/auctions/abc"}},
        )

    def fake_review_and_rename(entry, images_dir_arg, require_ai=False, seen_images=None, trusted_variant_provenance=False):
        entry["image_reviews"] = [
            {"path": "images/audi-tt/front-01.jpg", "category": "exterior_front"},
            {"path": "images/audi-tt/interior-02.jpg", "category": "interior"},
            {"path": "images/audi-tt/engine-03.jpg", "category": "engine_bay"},
        ]
        return entry

    monkeypatch.setattr(single_car_short, "scrape_entry_images", fake_scrape_entry_images)
    monkeypatch.setattr(single_car_short, "enrich_entry_from_manifest", lambda entry, manifest: entry)
    monkeypatch.setattr(single_car_short, "review_and_rename_entry_images", fake_review_and_rename)
    monkeypatch.setattr(single_car_short, "_auction_provenance_matches_entry", lambda entry: True)
    monkeypatch.setattr(single_car_short, "blur_license_plates", lambda path: None)

    media, selected_auction = single_car_short.gather_media(
        "Audi", "TT", "", 1998, 1998, images_dir, scenes=[{"media_type": "exterior"}]
    )

    assert {(item["path"], item["type"]) for item in media} == {
        ("images/audi-tt/front-01.jpg", "exterior"),
        ("images/audi-tt/interior-02.jpg", "interior"),
        ("images/audi-tt/engine-03.jpg", "engine"),
    }
    assert selected_auction == {"url": "https://example.com/auctions/abc"}


def test_visual_highlight_only_names_shot_types_the_scenes_actually_need():
    exterior_only_scenes = [
        {"media_type": "exterior"}, {"media_type": "engine"}, {"media_type": "exterior"},
    ]
    highlight = _visual_highlight_for_scenes(exterior_only_scenes)
    assert "interior" not in highlight
    assert "engine" in highlight

    with_interior = [{"media_type": "exterior"}, {"media_type": "interior"}]
    assert "interior" in _visual_highlight_for_scenes(with_interior)
