import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from single_car_short import (  # noqa: E402
    ACCEPTABLE_WORDS,
    ALLOWED_MEDIA_TYPES,
    AUDITION_PRESETS,
    FAST_TTS_SPEED,
    TARGET_WORDS,
    _strip_citations,
    _target_word_range,
    _visual_highlight_for_scenes,
    _word_count,
    apply_rival_photos,
    generate_voice_auditions,
    order_media_for_scenes,
)
from audition_voices import VOICE_PRESETS  # noqa: E402


def test_word_count_handles_contractions_and_hyphenated_terms():
    assert _word_count("It's a four-wheel-drive Golf R.") == 5


def test_target_words_scales_with_tts_speed_so_the_two_cant_drift_apart():
    # This is the regression the whole formula exists to catch: a script
    # sized for a slower speed produces less raw audio once spoken faster,
    # so the duration-normalization step has to slow it back down and
    # mostly cancels out the speed increase -- TARGET_WORDS must scale
    # with FAST_TTS_SPEED instead of being a hardcoded tuple that can fall
    # out of sync with it.
    slower = _target_word_range(speed=1.0)
    faster = _target_word_range(speed=1.5)
    assert faster[0] > slower[0] and faster[1] > slower[1]
    assert TARGET_WORDS == _target_word_range(FAST_TTS_SPEED)
    assert ACCEPTABLE_WORDS[0] < TARGET_WORDS[0] < TARGET_WORDS[1] < ACCEPTABLE_WORDS[1]


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
    monkeypatch.setattr(single_car_short, "remove_background", lambda path: path)

    media, selected_auction = single_car_short.gather_media(
        "Audi", "TT", "", 1998, 1998, images_dir, scenes=[{"media_type": "exterior"}]
    )

    assert {(item["path"], item["type"]) for item in media} == {
        ("images/audi-tt/front-01.jpg", "exterior"),
        ("images/audi-tt/interior-02.jpg", "interior"),
        ("images/audi-tt/engine-03.jpg", "engine"),
    }
    assert selected_auction == {"url": "https://example.com/auctions/abc"}


def test_gather_media_only_removes_background_from_exterior_photos(tmp_path, monkeypatch):
    import single_car_short

    images_dir = tmp_path / "images"
    car_dir = images_dir / "audi-tt"
    car_dir.mkdir(parents=True)
    for name in ["front-01.jpg", "interior-02.jpg", "engine-03.jpg"]:
        (car_dir / name).write_bytes(b"fake-image-bytes")

    monkeypatch.setattr(
        single_car_short, "scrape_entry_images",
        lambda scraper_dir, dest, entry: (
            [
                "images/audi-tt/front-01.jpg",
                "images/audi-tt/interior-02.jpg",
                "images/audi-tt/engine-03.jpg",
            ],
            {"selected_auction": {}},
        ),
    )

    def fake_review_and_rename(entry, images_dir_arg, require_ai=False, seen_images=None, trusted_variant_provenance=False):
        entry["image_reviews"] = [
            {"path": "images/audi-tt/front-01.jpg", "category": "exterior_front"},
            {"path": "images/audi-tt/interior-02.jpg", "category": "interior"},
            {"path": "images/audi-tt/engine-03.jpg", "category": "engine_bay"},
        ]
        return entry

    bg_removed_paths = []

    def fake_remove_background(path):
        bg_removed_paths.append(str(path))
        return path

    monkeypatch.setattr(single_car_short, "enrich_entry_from_manifest", lambda entry, manifest: entry)
    monkeypatch.setattr(single_car_short, "review_and_rename_entry_images", fake_review_and_rename)
    monkeypatch.setattr(single_car_short, "_auction_provenance_matches_entry", lambda entry: True)
    monkeypatch.setattr(single_car_short, "blur_license_plates", lambda path: None)
    monkeypatch.setattr(single_car_short, "remove_background", fake_remove_background)

    single_car_short.gather_media("Audi", "TT", "", 1998, 1998, images_dir, scenes=[{"media_type": "exterior"}])

    assert len(bg_removed_paths) == 1
    assert bg_removed_paths[0].endswith("front-01.jpg")


def test_apply_rival_photos_swaps_in_a_rival_photo_for_the_naming_scene(monkeypatch, tmp_path):
    import single_car_short

    scenes = [
        {"media_type": "exterior", "rival_make": None, "rival_model": None},
        {"media_type": "exterior", "rival_make": "Chevrolet", "rival_model": "Camaro"},
        {"media_type": "detail", "rival_make": None, "rival_model": None},
    ]
    media = [
        {"path": "images/mustang/exterior-01.jpg", "type": "exterior"},
        {"path": "images/mustang/exterior-02.jpg", "type": "exterior"},
        {"path": "images/mustang/detail-01.jpg", "type": "detail"},
    ]
    monkeypatch.setattr(
        single_car_short, "gather_rival_photo",
        lambda make, model, start, end, images_dir: "images/camaro/exterior-01.jpg",
    )

    result = apply_rival_photos(scenes, media, 2015, 2015, tmp_path)

    assert result[0]["path"] == "images/mustang/exterior-01.jpg"
    assert result[1] == {"path": "images/camaro/exterior-01.jpg", "type": "exterior"}
    assert result[2]["path"] == "images/mustang/detail-01.jpg"


def test_apply_rival_photos_leaves_media_untouched_when_no_rival_named(monkeypatch, tmp_path):
    import single_car_short

    scenes = [{"media_type": "exterior", "rival_make": None, "rival_model": None}]
    media = [{"path": "images/mustang/exterior-01.jpg", "type": "exterior"}]
    calls = []
    monkeypatch.setattr(
        single_car_short, "gather_rival_photo",
        lambda *a, **k: calls.append(a) or "images/camaro/exterior-01.jpg",
    )

    result = apply_rival_photos(scenes, media, 2015, 2015, tmp_path)

    assert result == media
    assert calls == []


def test_apply_rival_photos_keeps_original_media_when_rival_lookup_fails(monkeypatch, tmp_path):
    import single_car_short

    scenes = [{"media_type": "exterior", "rival_make": "Chevrolet", "rival_model": "Camaro"}]
    media = [{"path": "images/mustang/exterior-01.jpg", "type": "exterior"}]
    monkeypatch.setattr(single_car_short, "gather_rival_photo", lambda *a, **k: None)

    result = apply_rival_photos(scenes, media, 2015, 2015, tmp_path)

    assert result == media


def test_british_voice_presets_are_registered():
    for preset in AUDITION_PRESETS:
        assert preset in VOICE_PRESETS
        assert "british" in preset


def test_generate_voice_auditions_covers_chosen_preset_and_british_options(tmp_path, monkeypatch):
    import single_car_short

    synthesized = []
    monkeypatch.setattr(
        single_car_short, "synthesize_narration",
        lambda script, path, preset=None, speed=None: (synthesized.append(preset), Path(path).write_bytes(b"x")),
    )

    files = generate_voice_auditions("Some script text.", tmp_path, "onyx")

    assert set(synthesized) == {"onyx", *AUDITION_PRESETS}
    assert set(files) == {"onyx", *AUDITION_PRESETS}
    for relative in files.values():
        assert (tmp_path.parent / relative).exists()


def test_generate_voice_auditions_does_not_duplicate_a_british_preset_already_chosen(tmp_path, monkeypatch):
    import single_car_short

    synthesized = []
    monkeypatch.setattr(
        single_car_short, "synthesize_narration",
        lambda script, path, preset=None, speed=None: (synthesized.append(preset), Path(path).write_bytes(b"x")),
    )

    generate_voice_auditions("Some script text.", tmp_path, "british_narrator")

    assert synthesized.count("british_narrator") == 1


def test_generate_voice_auditions_skips_a_failing_preset_without_raising(tmp_path, monkeypatch):
    import single_car_short

    def fake_synthesize(script, path, preset=None, speed=None):
        if preset == "british_dry_wit":
            raise RuntimeError("tts failed")
        Path(path).write_bytes(b"x")

    monkeypatch.setattr(single_car_short, "synthesize_narration", fake_synthesize)

    files = generate_voice_auditions("Some script text.", tmp_path, "onyx")

    assert "british_dry_wit" not in files
    assert "onyx" in files


def test_visual_highlight_only_names_shot_types_the_scenes_actually_need():
    exterior_only_scenes = [
        {"media_type": "exterior"}, {"media_type": "engine"}, {"media_type": "exterior"},
    ]
    highlight = _visual_highlight_for_scenes(exterior_only_scenes)
    assert "interior" not in highlight
    assert "engine" in highlight

    with_interior = [{"media_type": "exterior"}, {"media_type": "interior"}]
    assert "interior" in _visual_highlight_for_scenes(with_interior)
