import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from single_car_short import (  # noqa: E402
    ACCEPTABLE_WORDS,
    ALLOWED_MEDIA_TYPES,
    AUDITION_PRESETS,
    FAST_TTS_SPEED,
    HARD_WORD_RANGE,
    TARGET_WORD_CENTER,
    TARGET_WORD_FLEX,
    TARGET_WORDS,
    _hard_word_range,
    _select_side_profile_media,
    _strip_citations,
    _visual_highlight_for_scenes,
    _word_count,
    apply_rival_photos,
    generate_voice_auditions,
    order_media_for_scenes,
    research_script,
)
from audition_voices import VOICE_PRESETS  # noqa: E402


def test_word_count_handles_contractions_and_hyphenated_terms():
    assert _word_count("It's a four-wheel-drive Golf R.") == 5


def test_target_words_is_a_fixed_center_not_tied_to_tts_speed():
    # A target that floated up with FAST_TTS_SPEED (it drifted to ~220 at
    # 1.35x) produced scripts that needed real atempo speed-up on top of
    # the already-fast TTS to hit ~58s, which read as rushed -- so this is
    # a fixed ~180-word center regardless of speed, with a flexible +-10
    # band around it.
    assert TARGET_WORDS == (TARGET_WORD_CENTER - TARGET_WORD_FLEX, TARGET_WORD_CENTER + TARGET_WORD_FLEX)
    assert TARGET_WORD_CENTER == 175
    assert ACCEPTABLE_WORDS[0] < TARGET_WORDS[0] < TARGET_WORDS[1] < ACCEPTABLE_WORDS[1]


def test_interior_media_is_available_for_cabin_script_scenes():
    assert "interior" in ALLOWED_MEDIA_TYPES


def test_hard_word_range_is_derived_from_the_atempo_clamp_not_a_guess():
    # normalize_audio_duration clamps atempo to 0.5-2.0 -- a script whose
    # raw audio needs a gentler correction than that still reaches ~target
    # runtime with acceptable audio quality, so the hard gate should track
    # that clamp directly rather than an arbitrary +-25% guess.
    assert HARD_WORD_RANGE == _hard_word_range()
    assert HARD_WORD_RANGE[0] < ACCEPTABLE_WORDS[0] < TARGET_WORDS[0]
    assert TARGET_WORDS[1] < ACCEPTABLE_WORDS[1] < HARD_WORD_RANGE[1]
    # This is the actual regression this whole range exists to fix: a
    # 146-word script (real build failure -- see the commit this test was
    # added in) is well outside ACCEPTABLE_WORDS but must NOT be outside
    # the real, atempo-derived hard gate.
    assert HARD_WORD_RANGE[0] <= 146 <= HARD_WORD_RANGE[1]


def test_research_script_retries_with_feedback_when_outside_acceptable_words(monkeypatch):
    import single_car_short

    prompts = []
    packages = [
        {"scenes": [], "script": "", "word_count": 100},
        {"scenes": [{"headline": "", "narration": "ok", "rival_make": None, "rival_model": None}],
         "script": "ok", "word_count": TARGET_WORDS[0] + 5},
    ]

    def fake_request(prompt):
        prompts.append(prompt)
        return packages[len(prompts) - 1]

    monkeypatch.setattr(single_car_short, "_request_script_package", fake_request)

    package = research_script("Ford", "Mustang")

    assert package["word_count"] == TARGET_WORDS[0] + 5
    assert len(prompts) == 2
    # The retry prompt must actually reference what went wrong so the
    # model has something concrete to correct.
    assert "100 words" in prompts[1]


def test_research_script_does_not_retry_when_first_attempt_is_already_acceptable(monkeypatch):
    import single_car_short

    calls = []

    def fake_request(prompt):
        calls.append(prompt)
        return {"scenes": [], "script": "", "word_count": TARGET_WORDS[0]}

    monkeypatch.setattr(single_car_short, "_request_script_package", fake_request)

    research_script("Ford", "Mustang")

    assert len(calls) == 1


def test_research_script_never_fails_the_build_over_word_count(monkeypatch):
    """A build dying over a word count was the actual complaint (a script
    the retries still couldn't pull into range used to raise RuntimeError
    and throw the whole build away) -- research_script must always return
    its best attempt, however far outside any of the word ranges, and just
    let normalize_audio_duration's atempo correction do what it can."""
    import single_car_short

    monkeypatch.setattr(
        single_car_short, "_request_script_package",
        lambda prompt: {"scenes": [], "script": "", "word_count": HARD_WORD_RANGE[0] - 20},
    )

    package = research_script("Ford", "Mustang", max_attempts=2)

    assert package["word_count"] == HARD_WORD_RANGE[0] - 20


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


def test_gather_media_requests_a_wider_pool_than_the_default_limit(tmp_path, monkeypatch):
    """The default limit=6 (tuned for ranking/battle, which mostly just
    need one hero shot per car) was capping the pool before engine/wheel/
    detail photos ever got a chance to survive the second review pass --
    single-car needs real variety across five media_types, so it must ask
    for more than the default."""
    import single_car_short

    images_dir = tmp_path / "images"
    calls = []

    def fake_scrape_entry_images(scraper_dir, dest, entry, limit=6):
        calls.append(limit)
        return [], {"selected_auction": {}}

    monkeypatch.setattr(single_car_short, "scrape_entry_images", fake_scrape_entry_images)
    monkeypatch.setattr(single_car_short, "enrich_entry_from_manifest", lambda entry, manifest: entry)

    try:
        single_car_short.gather_media("Audi", "TT", "", 1998, 1998, images_dir, scenes=[{"media_type": "exterior"}])
    except RuntimeError:
        pass  # no images approved -- irrelevant to this test, which only checks the requested limit

    assert calls == [10]


def test_gather_media_uses_scrape_auction_images_when_an_auction_url_is_given(tmp_path, monkeypatch):
    """A pasted Cars & Bids listing URL is the escape hatch for a car whose
    make/model search comes back empty (or lands on the wrong listing) --
    it must skip scrape_entry_images' search entirely and fetch that exact
    auction instead."""
    import single_car_short

    images_dir = tmp_path / "images"
    search_calls = []
    auction_calls = []

    def fake_scrape_entry_images(scraper_dir, dest, entry, limit=6):
        search_calls.append(entry)
        return [], {"selected_auction": {}}

    def fake_scrape_auction_images(scraper_dir, dest, entry, auction_url, limit=6):
        auction_calls.append((auction_url, limit))
        return [], {"selected_auction": {"url": auction_url}}

    monkeypatch.setattr(single_car_short, "scrape_entry_images", fake_scrape_entry_images)
    monkeypatch.setattr(single_car_short, "scrape_auction_images", fake_scrape_auction_images)
    monkeypatch.setattr(single_car_short, "enrich_entry_from_manifest", lambda entry, manifest: entry)

    try:
        single_car_short.gather_media(
            "Volkswagen", "Golf GTI", "", 2020, 2020, images_dir,
            scenes=[{"media_type": "exterior"}],
            auction_url="https://carsandbids.com/auctions/abc123/2021-volkswagen-golf-gti",
        )
    except RuntimeError:
        pass  # no images approved -- irrelevant to this test

    assert search_calls == []
    assert auction_calls == [("https://carsandbids.com/auctions/abc123/2021-volkswagen-golf-gti", 10)]


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

    def fake_scrape_entry_images(scraper_dir, dest, entry, limit=6):
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
        lambda scraper_dir, dest, entry, limit=6: (
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


def test_select_side_profile_media_prefers_a_true_side_shot():
    media = [
        {"path": "front.jpg", "type": "exterior", "category": "exterior_front", "facing_direction": "left"},
        {"path": "side.jpg", "type": "exterior", "category": "exterior_side", "facing_direction": "right"},
        {"path": "full.jpg", "type": "exterior", "category": "exterior_full", "facing_direction": "left"},
    ]
    assert _select_side_profile_media(media) == {"path": "side.jpg", "facing_direction": "right"}


def test_select_side_profile_media_falls_back_to_full_car_angle_then_any_exterior():
    assert _select_side_profile_media([
        {"path": "front.jpg", "type": "exterior", "category": "exterior_front", "facing_direction": "left"},
        {"path": "full.jpg", "type": "exterior", "category": "exterior_full", "facing_direction": "right"},
    ]) == {"path": "full.jpg", "facing_direction": "right"}
    assert _select_side_profile_media([
        {"path": "interior.jpg", "type": "interior", "category": "interior"},
        {"path": "front.jpg", "type": "exterior", "category": "exterior_front", "facing_direction": "unclear"},
    ]) == {"path": "front.jpg", "facing_direction": "unclear"}


def test_select_side_profile_media_returns_none_without_any_exterior_photo():
    assert _select_side_profile_media([{"path": "interior.jpg", "type": "interior", "category": "interior"}]) is None


def test_gather_rival_photo_prefers_side_profile_over_front_rear(monkeypatch, tmp_path):
    import single_car_short

    images_dir = tmp_path / "images"
    car_dir = images_dir / "camaro"
    car_dir.mkdir(parents=True)
    for name in ["front-01.jpg", "side-02.jpg"]:
        (car_dir / name).write_bytes(b"fake-image-bytes")

    monkeypatch.setattr(
        single_car_short, "scrape_entry_images",
        lambda scraper_dir, dest, entry: (
            ["images/camaro/front-01.jpg", "images/camaro/side-02.jpg"],
            {"selected_auction": {}},
        ),
    )

    def fake_review_and_rename(entry, images_dir_arg, require_ai=False, seen_images=None, trusted_variant_provenance=False):
        entry["image_reviews"] = [
            {"path": "images/camaro/front-01.jpg", "category": "exterior_front", "facing_direction": "left"},
            {"path": "images/camaro/side-02.jpg", "category": "exterior_side", "facing_direction": "right"},
        ]
        return entry

    monkeypatch.setattr(single_car_short, "enrich_entry_from_manifest", lambda entry, manifest: entry)
    monkeypatch.setattr(single_car_short, "review_and_rename_entry_images", fake_review_and_rename)
    monkeypatch.setattr(single_car_short, "_auction_provenance_matches_entry", lambda entry: True)
    monkeypatch.setattr(single_car_short, "blur_license_plates", lambda path: None)
    monkeypatch.setattr(single_car_short, "remove_background", lambda path: path)

    result = single_car_short.gather_rival_photo("Chevrolet", "Camaro", 2015, 2015, images_dir)

    assert result == ("images/camaro/side-02.jpg", "right")


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
        lambda make, model, start, end, images_dir: ("images/camaro/exterior-01.jpg", "left"),
    )

    result = apply_rival_photos(scenes, media, 2015, 2015, tmp_path)

    assert result[0]["path"] == "images/mustang/exterior-01.jpg"
    assert result[1] == {"path": "images/camaro/exterior-01.jpg", "type": "exterior", "facing_direction": "left"}
    assert result[2]["path"] == "images/mustang/detail-01.jpg"


def test_apply_rival_photos_leaves_media_untouched_when_no_rival_named(monkeypatch, tmp_path):
    import single_car_short

    scenes = [{"media_type": "exterior", "rival_make": None, "rival_model": None}]
    media = [{"path": "images/mustang/exterior-01.jpg", "type": "exterior"}]
    calls = []
    monkeypatch.setattr(
        single_car_short, "gather_rival_photo",
        lambda *a, **k: calls.append(a) or ("images/camaro/exterior-01.jpg", "left"),
    )

    result = apply_rival_photos(scenes, media, 2015, 2015, tmp_path)

    assert result == media
    assert calls == []


def test_apply_rival_photos_keeps_original_media_when_rival_lookup_fails(monkeypatch, tmp_path):
    import single_car_short

    scenes = [{"media_type": "exterior", "rival_make": "Chevrolet", "rival_model": "Camaro"}]
    media = [{"path": "images/mustang/exterior-01.jpg", "type": "exterior"}]
    monkeypatch.setattr(single_car_short, "gather_rival_photo", lambda *a, **k: (None, "unclear"))

    result = apply_rival_photos(scenes, media, 2015, 2015, tmp_path)

    assert result == media


def test_apply_rival_photos_uses_the_manual_rival_url_for_every_comparison_scene(monkeypatch, tmp_path):
    """A pasted rival photo link can't be matched to a make/model ahead of
    the AI script naming one -- so it's applied to whichever scene(s) turn
    out to need a rival photo, and the scrape path is never touched."""
    import single_car_short

    scenes = [
        {"media_type": "exterior", "rival_make": "Chevrolet", "rival_model": "Camaro"},
        {"media_type": "exterior", "rival_make": None, "rival_model": None},
    ]
    media = [
        {"path": "images/mustang/exterior-01.jpg", "type": "exterior"},
        {"path": "images/mustang/exterior-02.jpg", "type": "exterior"},
    ]
    calls = []

    def fake_gather_manual_rival_photo(url, images_dir, rival_make, rival_model):
        calls.append((url, rival_make, rival_model))
        return ("images/manual-rival/rival.png", "right")

    def fail_gather_rival_photo(*a, **k):
        raise AssertionError("should not scrape when a manual rival URL is given")

    monkeypatch.setattr(single_car_short, "gather_manual_rival_photo", fake_gather_manual_rival_photo)
    monkeypatch.setattr(single_car_short, "gather_rival_photo", fail_gather_rival_photo)

    result = apply_rival_photos(
        scenes, media, 2015, 2015, tmp_path, manual_rival_url="https://carsandbids.com/rival.jpg",
    )

    assert result[0] == {"path": "images/manual-rival/rival.png", "type": "exterior", "facing_direction": "right"}
    assert result[1]["path"] == "images/mustang/exterior-02.jpg"
    # Only downloaded once even though it's applied to a scene -- caching
    # the same manual photo instead of re-fetching it per scene.
    assert len(calls) == 1


def test_download_car_photo_rejects_a_pasted_page_link(tmp_path, monkeypatch):
    """The actual bug this guards against: a user pasting a Cars & Bids
    *listing page* URL instead of a direct image link. That request
    succeeds and returns real bytes (an HTML page), which used to get
    silently saved as a fake ".jpg" -- the override then quietly never
    took effect, with no indication why. A non-image content-type must
    fail the download outright instead of saving garbage as a photo."""
    import single_car_short

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<html><body>a listing page, not a photo</body></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(single_car_short.requests, "get", lambda *a, **k: FakeResponse())

    path = single_car_short._download_car_photo(
        "https://carsandbids.com/auctions/3vEJlbNB/1993-toyota-supra-turbo", tmp_path, "front",
    )

    assert path is None
    assert list(tmp_path.iterdir()) == []


def test_download_car_photo_rejects_bytes_that_are_not_a_real_image(tmp_path, monkeypatch):
    """Even a response that claims to be an image but isn't a real,
    decodable one (a mislabeled content-type, a truncated download)
    should fail cleanly rather than save unusable bytes."""
    import single_car_short

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}
        content = b"not actually jpeg bytes"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(single_car_short.requests, "get", lambda *a, **k: FakeResponse())

    path = single_car_short._download_car_photo("https://example.com/fake.jpg", tmp_path, "front")

    assert path is None


def test_download_car_photo_accepts_a_real_image(tmp_path, monkeypatch):
    import single_car_short
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (255, 0, 0)).save(buf, format="JPEG")

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}
        content = buf.getvalue()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(single_car_short.requests, "get", lambda *a, **k: FakeResponse())

    path = single_car_short._download_car_photo("https://example.com/real.jpg", tmp_path, "front")

    assert path is not None
    assert path.exists()
    assert path.suffix == ".jpg"


class _FakeOpenAIResponse:
    def __init__(self, text):
        self.output_text = text


class _FakeOpenAIClient:
    def __init__(self, text):
        self._text = text
        self.responses = self

    def create(self, **kwargs):
        return _FakeOpenAIResponse(self._text)


def test_identify_car_in_photo_returns_the_identified_car(tmp_path, monkeypatch):
    import single_car_short

    monkeypatch.setattr(single_car_short, "OpenAI", lambda: _FakeOpenAIClient("Acura NSX"))
    path = tmp_path / "rival.jpg"
    path.write_bytes(b"fake-image-bytes")

    assert single_car_short._identify_car_in_photo(path) == "Acura NSX"


def test_identify_car_in_photo_returns_none_when_unidentifiable(tmp_path, monkeypatch):
    import single_car_short

    monkeypatch.setattr(single_car_short, "OpenAI", lambda: _FakeOpenAIClient("unknown"))
    path = tmp_path / "rival.jpg"
    path.write_bytes(b"fake-image-bytes")

    assert single_car_short._identify_car_in_photo(path) is None


def test_identify_car_in_photo_fails_open_on_error(tmp_path, monkeypatch):
    import single_car_short

    def broken_client():
        raise RuntimeError("network down")

    monkeypatch.setattr(single_car_short, "OpenAI", broken_client)
    path = tmp_path / "rival.jpg"
    path.write_bytes(b"fake-image-bytes")

    assert single_car_short._identify_car_in_photo(path) is None


def test_gather_manual_media_trusts_the_field_category_over_any_ai_guess(tmp_path, monkeypatch):
    """The category comes from which field the user pasted the link into,
    not from re-classifying the photo -- a user who says "this is the
    front" should get exterior_front even if a vision model would have
    called it exterior_full."""
    import single_car_short

    images_dir = tmp_path / "images"

    def fake_download(url, dest_dir, filename_stem):
        path = dest_dir / f"{filename_stem}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-image-bytes")
        return path

    monkeypatch.setattr(single_car_short, "_download_car_photo", fake_download)
    monkeypatch.setattr(single_car_short, "_facing_direction_for_photo", lambda path, entry: "right")
    monkeypatch.setattr(single_car_short, "blur_license_plates", lambda path: None)
    monkeypatch.setattr(single_car_short, "remove_background", lambda path: path)

    media = single_car_short.gather_manual_media(
        {"front": "https://example.com/front.jpg", "interior": "https://example.com/int.jpg", "rear": ""},
        images_dir, {"name": "Volkswagen Golf GTI"},
    )

    by_category = {item["category"]: item for item in media}
    assert set(by_category) == {"exterior_front", "interior"}
    assert by_category["exterior_front"]["type"] == "exterior"
    assert by_category["exterior_front"]["facing_direction"] == "right"
    # Interior isn't an exterior shot, so no facing-direction lookup is
    # meaningful for it -- it should stay "unclear" rather than reusing
    # whatever the (unrelated) exterior mock returned.
    assert by_category["interior"]["type"] == "interior"
    assert by_category["interior"]["facing_direction"] == "unclear"


def test_gather_media_uses_manual_photo_urls_and_skips_scraping_entirely(tmp_path, monkeypatch):
    """Pasted photo links are the whole point of avoiding the slow
    Puppeteer search -- when any are given, neither scrape function should
    run at all."""
    import single_car_short

    images_dir = tmp_path / "images"

    def fail_scrape(*a, **k):
        raise AssertionError("scraping should be skipped when manual photo URLs are given")

    def fake_gather_manual_media(photo_urls, images_dir_arg, entry):
        assert photo_urls == {"front": "https://example.com/front.jpg", "side": "https://example.com/side.jpg"}
        return [
            {"path": "images/manual/front.jpg", "type": "exterior", "category": "exterior_front", "facing_direction": "right"},
            {"path": "images/manual/side.jpg", "type": "exterior", "category": "exterior_side", "facing_direction": "left"},
        ]

    monkeypatch.setattr(single_car_short, "scrape_entry_images", fail_scrape)
    monkeypatch.setattr(single_car_short, "scrape_auction_images", fail_scrape)
    monkeypatch.setattr(single_car_short, "gather_manual_media", fake_gather_manual_media)

    media, selected_auction = single_car_short.gather_media(
        "Volkswagen", "Golf GTI", "", 2020, 2020, images_dir,
        scenes=[{"media_type": "exterior"}],
        manual_photo_urls={"front": "https://example.com/front.jpg", "side": "https://example.com/side.jpg", "rear": ""},
    )

    assert len(media) == 2
    assert selected_auction == {}


def test_gather_media_merges_manual_photo_overrides_into_an_auction_scrape(tmp_path, monkeypatch):
    """Pasting a listing URL *and* a manual photo isn't an either/or --
    the listing identifies the car and fills in whatever isn't manually
    overridden, while the manual photo replaces just its own category."""
    import single_car_short

    images_dir = tmp_path / "images"
    car_dir = images_dir / "porsche-911"
    car_dir.mkdir(parents=True)
    for name in ["front-01.jpg", "interior-02.jpg", "engine-03.jpg"]:
        (car_dir / name).write_bytes(b"fake-image-bytes")

    def fake_scrape_auction_images(scraper_dir, dest, entry, auction_url, limit=6):
        assert auction_url == "https://carsandbids.com/auctions/abc123/2024-porsche-911"
        return (
            [
                "images/porsche-911/front-01.jpg",
                "images/porsche-911/interior-02.jpg",
                "images/porsche-911/engine-03.jpg",
            ],
            {"selected_auction": {"url": auction_url}},
        )

    def fake_review_and_rename(entry, images_dir_arg, require_ai=False, seen_images=None, trusted_variant_provenance=False):
        entry["image_reviews"] = [
            {"path": "images/porsche-911/front-01.jpg", "category": "exterior_front", "facing_direction": "right"},
            {"path": "images/porsche-911/interior-02.jpg", "category": "interior"},
            {"path": "images/porsche-911/engine-03.jpg", "category": "engine_bay"},
        ]
        return entry

    def fake_gather_manual_media(photo_urls, images_dir_arg, entry):
        assert photo_urls == {"side": "https://example.com/side.jpg"}
        return [{"path": "images/manual/side.jpg", "type": "exterior", "category": "exterior_side", "facing_direction": "left"}]

    monkeypatch.setattr(single_car_short, "scrape_auction_images", fake_scrape_auction_images)
    monkeypatch.setattr(single_car_short, "enrich_entry_from_manifest", lambda entry, manifest: entry)
    monkeypatch.setattr(single_car_short, "review_and_rename_entry_images", fake_review_and_rename)
    monkeypatch.setattr(single_car_short, "_auction_provenance_matches_entry", lambda entry: True)
    monkeypatch.setattr(single_car_short, "blur_license_plates", lambda path: None)
    monkeypatch.setattr(single_car_short, "remove_background", lambda path: path)
    monkeypatch.setattr(single_car_short, "gather_manual_media", fake_gather_manual_media)

    media, selected_auction = single_car_short.gather_media(
        "Porsche", "911", "", 2024, 2024, images_dir, scenes=[{"media_type": "exterior"}],
        auction_url="https://carsandbids.com/auctions/abc123/2024-porsche-911",
        manual_photo_urls={"side": "https://example.com/side.jpg"},
    )

    by_category = {item["category"]: item for item in media}
    assert set(by_category) == {"exterior_front", "interior", "engine_bay", "exterior_side"}
    assert by_category["exterior_side"] == {
        "path": "images/manual/side.jpg", "type": "exterior", "category": "exterior_side", "facing_direction": "left",
    }
    assert selected_auction == {"url": "https://carsandbids.com/auctions/abc123/2024-porsche-911"}


def test_apply_manual_photo_overrides_replaces_only_matching_categories():
    import single_car_short

    media = [
        {"path": "a", "category": "exterior_front"},
        {"path": "b", "category": "exterior_side"},
        {"path": "c", "category": "interior"},
    ]
    manual_media = [{"path": "new-side", "category": "exterior_side"}]

    result = single_car_short._apply_manual_photo_overrides(media, manual_media)

    assert {item["path"] for item in result} == {"a", "new-side", "c"}


def test_gather_extra_media_downloads_arbitrarily_named_photos(tmp_path, monkeypatch):
    """Extra photos are free-typed by the user (e.g. "Gauge Cluster") --
    always filed as detail shots, and always additions, never replacing
    anything else in the pool."""
    import single_car_short

    images_dir = tmp_path / "images"

    def fake_download(url, dest_dir, filename_stem):
        assert filename_stem == "0-gauge-cluster"
        path = dest_dir / f"{filename_stem}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-image-bytes")
        return path

    monkeypatch.setattr(single_car_short, "_download_car_photo", fake_download)
    monkeypatch.setattr(single_car_short, "blur_license_plates", lambda path: None)

    media = single_car_short.gather_extra_media(
        [{"label": "Gauge Cluster", "url": "https://example.com/gauges.jpg"}],
        images_dir, {"name": "Toyota Supra"},
    )

    assert len(media) == 1
    assert media[0]["type"] == "detail"
    assert media[0]["category"] == "other_detail"
    assert media[0]["label"] == "Gauge Cluster"


def test_gather_extra_media_skips_malformed_or_url_less_entries(tmp_path, monkeypatch):
    import single_car_short

    calls = []
    monkeypatch.setattr(single_car_short, "_download_car_photo", lambda *a, **k: calls.append(a) or None)

    media = single_car_short.gather_extra_media(
        [{"label": "No URL"}, "not-a-dict", None, {"label": "Blank", "url": ""}],
        tmp_path / "images", {"name": "Toyota Supra"},
    )

    assert media == []
    assert calls == []


def test_gather_media_appends_extra_photos_alongside_scraped_media(tmp_path, monkeypatch):
    """Extra photos add to the pool from either photo path -- the normal
    scrape and the manual-only skip-scrape path -- rather than requiring
    their own separate mode."""
    import single_car_short

    images_dir = tmp_path / "images"

    def fake_scrape_entry_images(scraper_dir, dest, entry, limit=6):
        return [], {"selected_auction": {}}

    monkeypatch.setattr(single_car_short, "scrape_entry_images", fake_scrape_entry_images)
    monkeypatch.setattr(single_car_short, "enrich_entry_from_manifest", lambda entry, manifest: entry)
    monkeypatch.setattr(single_car_short, "review_and_rename_entry_images", lambda *a, **k: None)
    monkeypatch.setattr(
        single_car_short, "gather_extra_media",
        lambda extra_photos, images_dir_arg, entry: [
            {"path": "images/manual-extra/0-gauge-cluster.jpg", "type": "detail", "category": "other_detail", "facing_direction": "unclear", "label": "Gauge Cluster"},
        ],
    )

    media, _ = single_car_short.gather_media(
        "Toyota", "Supra", "", 1993, 1993, images_dir, scenes=[{"media_type": "exterior"}],
        extra_photos=[{"label": "Gauge Cluster", "url": "https://example.com/gauges.jpg"}],
    )

    assert media == [
        {"path": "images/manual-extra/0-gauge-cluster.jpg", "type": "detail", "category": "other_detail", "facing_direction": "unclear", "label": "Gauge Cluster"},
    ]


def test_gather_photo_script_hints_describes_fixed_and_extra_photos(tmp_path, monkeypatch):
    """The whole point of pasting a photo (especially a labeled extra like
    "Gauge Cluster") is that the script ends up talking about what's
    actually in it -- so each hint pairs a human-readable label with a
    concrete AI-described detail."""
    import single_car_short

    images_dir = tmp_path / "images"

    def fake_download(url, dest_dir, filename_stem):
        path = dest_dir / f"{filename_stem}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-image-bytes")
        return path

    def fake_describe(path, label_hint, car_label):
        return f"a described detail for {label_hint} on the {car_label}"

    monkeypatch.setattr(single_car_short, "_download_car_photo", fake_download)
    monkeypatch.setattr(single_car_short, "_describe_photo_for_script", fake_describe)

    hints = single_car_short.gather_photo_script_hints(
        {"interior": "https://example.com/int.jpg", "front": ""},
        [{"label": "Gauge Cluster", "url": "https://example.com/gauges.jpg"}],
        images_dir, "1993 Toyota Supra Turbo",
    )

    assert hints == [
        "interior photo: a described detail for interior on the 1993 Toyota Supra Turbo",
        "Gauge Cluster photo: a described detail for Gauge Cluster on the 1993 Toyota Supra Turbo",
    ]


def test_gather_photo_script_hints_drops_photos_with_no_description(tmp_path, monkeypatch):
    import single_car_short

    def fake_download(url, dest_dir, filename_stem):
        path = dest_dir / f"{filename_stem}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-image-bytes")
        return path

    monkeypatch.setattr(single_car_short, "_download_car_photo", fake_download)
    monkeypatch.setattr(single_car_short, "_describe_photo_for_script", lambda *a, **k: None)

    hints = single_car_short.gather_photo_script_hints(
        {"front": "https://example.com/front.jpg"}, [], tmp_path / "images", "Toyota Supra",
    )

    assert hints == []


def test_research_script_prompt_folds_in_photo_hints():
    import single_car_short

    prompt = single_car_short._research_script_prompt(
        "1993 Toyota Supra Turbo", "model year 1993", photo_hints=["Gauge Cluster photo: a distinctive analog cluster."],
    )
    assert "Gauge Cluster photo: a distinctive analog cluster." in prompt
    assert "You MUST write one scene's narration specifically about each one" in prompt


def test_research_script_prompt_omits_the_photo_hints_block_when_there_are_none():
    import single_car_short

    prompt = single_car_short._research_script_prompt("1993 Toyota Supra Turbo", "model year 1993")
    assert "specifically pasted these photos" not in prompt


def test_research_script_prompt_forces_the_pasted_comparison_car_as_rival():
    """A pasted comparison-car photo must not just be used *if* the script
    happens to name a rival on its own -- the script has to be told to use
    that exact car, or the photo (and the drag-race animation) silently
    never happens."""
    import single_car_short

    prompt = single_car_short._research_script_prompt(
        "1993 Toyota Supra Turbo", "model year 1993", forced_rival="Acura NSX",
    )
    assert "Acura NSX" in prompt
    assert "HARD REQUIREMENT: the user has already chosen Acura NSX" in prompt


def test_research_script_prompt_omits_the_forced_rival_block_when_there_is_none():
    import single_car_short

    prompt = single_car_short._research_script_prompt("1993 Toyota Supra Turbo", "model year 1993")
    assert "already chosen" not in prompt


def test_research_script_prompt_forbids_a_rival_scene_when_comparison_is_disabled():
    import single_car_short

    prompt = single_car_short._research_script_prompt(
        "1993 Toyota Supra Turbo", "model year 1993", disable_comparison=True,
    )
    assert "do NOT name" in prompt
    assert "explicitly turned off the rival-comparison scene" in prompt


def test_research_script_prompt_omits_the_no_comparison_block_by_default():
    import single_car_short

    prompt = single_car_short._research_script_prompt("1993 Toyota Supra Turbo", "model year 1993")
    assert "turned off the rival-comparison scene" not in prompt


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
