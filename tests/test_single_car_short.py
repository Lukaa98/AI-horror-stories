import pytest
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
    # 175 is a ceiling, not a midpoint. A target that floated up with
    # FAST_TTS_SPEED (it drifted to ~220 at 1.35x) produced scripts that
    # needed real atempo speed-up on top of the already-fast TTS to hit
    # ~58s, which read as rushed.
    from single_car_short import WORD_CAP
    assert WORD_CAP == 175
    assert TARGET_WORDS[1] == WORD_CAP and ACCEPTABLE_WORDS[1] == WORD_CAP
    assert ACCEPTABLE_WORDS[0] < TARGET_WORDS[0] < TARGET_WORDS[1]


def test_interior_media_is_available_for_cabin_script_scenes():
    assert "interior" in ALLOWED_MEDIA_TYPES


def test_hard_word_range_is_derived_from_the_atempo_clamp_not_a_guess():
    # normalize_audio_duration clamps atempo to 0.5-2.0 -- a script whose
    # raw audio needs a gentler correction than that still reaches ~target
    # runtime with acceptable audio quality, so the hard gate should track
    # that clamp directly rather than an arbitrary +-25% guess.
    assert HARD_WORD_RANGE == _hard_word_range()
    assert HARD_WORD_RANGE[0] < ACCEPTABLE_WORDS[0] < TARGET_WORDS[0]
    # The upper end of the atempo range is no longer a ceiling anyone can
    # ship through -- _enforce_word_cap trims to WORD_CAP well below it.
    assert TARGET_WORDS[1] < HARD_WORD_RANGE[1]
    # This is the actual regression this whole range exists to fix: a
    # 146-word script (real build failure -- see the commit this test was
    # added in) is well outside ACCEPTABLE_WORDS but must NOT be outside
    # the real, atempo-derived hard gate.
    assert HARD_WORD_RANGE[0] <= 146 <= HARD_WORD_RANGE[1]


def test_research_script_retries_with_feedback_when_outside_acceptable_words(monkeypatch):
    import single_car_short

    prompts = []
    # The second attempt has to satisfy the house rules as well as the word
    # count, or the loop correctly keeps retrying: opens on a number without
    # naming the car, says which car it is in the beat after, closes on a
    # question, no banned shapes.
    good = [
        {"headline": "", "narration": "A 707-horsepower coupe with 650 lb-ft hides behind that badge. "
                                      "That's the Mustang.",
         "rival_make": None, "rival_model": None},
        {"headline": "", "narration": "So would you daily it, or is that too much?",
         "rival_make": None, "rival_model": None},
    ]
    packages = [
        {"scenes": [], "script": "", "word_count": 100},
        {"scenes": good, "script": " ".join(s["narration"] for s in good),
         "word_count": TARGET_WORDS[0] + 5},
    ]

    def fake_request(prompt, max_scenes=8):
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

    def fake_request(prompt, max_scenes=8):
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
        lambda prompt, max_scenes=8: {"scenes": [], "script": "", "word_count": HARD_WORD_RANGE[0] - 20},
    )

    package = research_script("Ford", "Mustang", max_attempts=2)

    assert package["word_count"] == HARD_WORD_RANGE[0] - 20


def test_word_cap_trims_whole_sentences_and_keeps_every_scene_narrating():
    """Run #172 shipped 257 words in 58 seconds -- 4.4 words/sec -- because
    the retry loop only warned and the atempo gate allowed up to 440. The
    cap is now enforced by trimming rather than by hoping."""
    import single_car_short

    scenes = [{"narration": f"Sentence {i} alpha beta gamma delta epsilon zeta. "
                            f"Sentence {i} tail eta theta iota kappa lambda."}
              for i in range(16)]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes)}
    package["word_count"] = single_car_short._word_count(package["script"])
    assert package["word_count"] > single_car_short.WORD_CAP

    single_car_short._enforce_word_cap(package)

    assert package["word_count"] <= single_car_short.WORD_CAP
    # Every scene keeps narrating, so the imagery still lines up with what
    # is being said -- trimming must never empty a scene.
    assert all(scene["narration"].strip() for scene in package["scenes"])
    # Whole sentences only; no truncated fragments.
    assert all(scene["narration"].strip().endswith(".") for scene in package["scenes"])


def test_word_cap_drops_whole_scenes_when_no_scene_has_a_spare_sentence():
    """Run #175 came back as eleven scenes of exactly one sentence each. The
    sentence-level trim cannot touch those without emptying a scene, so it
    found nothing trimmable and shipped 199 words. Whole scenes go instead --
    longest first, never the hook or the closing question."""
    import single_car_short

    scenes = [{"narration": f"Scene {i} alpha beta gamma delta epsilon zeta eta theta iota kappa."}
              for i in range(16)]
    hook, closer = scenes[0]["narration"], scenes[-1]["narration"]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes)}
    package["word_count"] = single_car_short._word_count(package["script"])
    assert package["word_count"] > single_car_short.WORD_CAP

    single_car_short._enforce_word_cap(package)

    assert package["word_count"] <= single_car_short.WORD_CAP
    assert len(package["scenes"]) < 16
    assert package["scenes"][0]["narration"] == hook
    assert package["scenes"][-1]["narration"] == closer
    assert all(scene["narration"].strip() for scene in package["scenes"])


def test_script_violations_catch_what_the_prompt_alone_did_not():
    """Audited across runs #180-#182 the hook named the car every time, and
    #182 also opened without a number, closed on a statement instead of a
    question, and used a banned shape. These are mechanical properties of the
    text, so the retry loop verifies them rather than hoping."""
    import single_car_short

    bad = {"scenes": [
        {"narration": "Performance is at the core of the Challenger SRT Super Stock."},
        {"narration": "The wide-body fenders aren't just for looks."},
        {"narration": "The Challenger is not just a muscle car; it's a statement."},
    ]}
    found = " | ".join(single_car_short._script_violations(bad, "Dodge", "Challenger SRT Super Stock"))
    # Run #184 never said the car's own horsepower and never mentioned torque.
    assert "never states the car's torque" in found
    assert "no number" in found
    assert "names the car" in found
    assert "does not end on a question" in found
    # A plural walked straight past a ban on the singular in run #182.
    assert "just for looks" in found

    good = {"scenes": [
        {"narration": "A 807-horsepower supercharged V8 making 707 lb-ft hides behind that badge. "
                      "That's the Challenger SRT Super Stock."},
        {"narration": "So would you daily it, or is that a step too far?"},
    ]}
    assert single_car_short._script_violations(good, "Dodge", "Challenger SRT Super Stock") == []


def test_horsepower_has_to_arrive_in_the_opening_scenes():
    """Stating horsepower anywhere was not enough. Run #184 buried its only
    technical beat past the halfway mark, and horsepower is the number that
    keeps a viewer watching, so the check is positional."""
    import single_car_short

    late = {"scenes": [
        {"narration": "Only 300 were ever built."},
        {"narration": "That's the Challenger SRT Super Stock."},
        {"narration": "The stance alone tells you it means it."},
        {"narration": "It makes 807 horsepower and 707 lb-ft."},
        {"narration": "So would you daily it?"},
    ]}
    found = " | ".join(single_car_short._script_violations(late, "Dodge", "Challenger SRT Super Stock"))
    assert "not until after scene" in found
    # It is late, not missing -- the feedback has to say which of the two.
    assert "never states the car's horsepower" not in found

    early = dict(late, scenes=[
        {"narration": "Only 300 were ever built."},
        {"narration": "That's the Challenger SRT Super Stock, all 807 horsepower of it."},
        {"narration": "707 lb-ft goes with it."},
        {"narration": "So would you daily it?"},
    ])
    assert single_car_short._script_violations(early, "Dodge", "Challenger SRT Super Stock") == []


def test_word_cap_leaves_a_script_already_under_it_untouched():
    import single_car_short

    package = {"scenes": [{"narration": "Short and sweet."}], "script": "Short and sweet.", "word_count": 3}
    assert single_car_short._enforce_word_cap(package) is package
    assert package["word_count"] == 3


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
    ]
    media = [
        {"path": "interior-06.jpg", "type": "interior"},
        {"path": "interior-07.jpg", "type": "interior"},
        {"path": "exterior-01.jpg", "type": "exterior"},
    ]
    ordered = order_media_for_scenes(scenes, media)
    assert [item["path"] for item in ordered] == ["interior-06.jpg", "interior-07.jpg"]


def test_order_media_for_scenes_reuses_same_type_before_stealing_a_different_type():
    """The actual regression this fixes: a third interior-topic scene, with
    both interior photos already used, must repeat one of them rather than
    grab the still-unused exterior photo -- reusing a shot is fine, but a
    wrong-content-type photo under an unrelated topic (e.g. an interior
    photo shown during a scene about the rear wing) is a real bug."""
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
    assert [item["path"] for item in ordered] == ["interior-06.jpg", "interior-07.jpg", "interior-06.jpg"]


def test_order_media_for_scenes_matches_a_labeled_photo_to_its_own_scene():
    """The actual Boxster-build regression: several "detail"-type extra
    photos in the pool (tail lights, spoiler, gauge cluster) with several
    "detail"-type scenes about them -- without label matching, pool order
    alone pairs them up wrong (the spoiler scene gets the tail-lights
    photo, the gauge scene gets the spoiler photo). photo_label lets each
    scene claim its own specific photo regardless of pool order."""
    scenes = [
        {"media_type": "detail", "photo_label": "Stability Boost"},
        {"media_type": "detail", "photo_label": "Driver-Focused Gauges"},
        {"media_type": "detail", "photo_label": None},
    ]
    media = [
        {"path": "tail-lights.jpg", "type": "detail", "category": "other_detail", "label": "Retro Tail Lights"},
        {"path": "spoiler.jpg", "type": "detail", "category": "other_detail", "label": "Stability Boost"},
        {"path": "gauges.jpg", "type": "detail", "category": "other_detail", "label": "Driver-Focused Gauges"},
    ]
    ordered = order_media_for_scenes(scenes, media)
    assert [item["path"] for item in ordered] == ["spoiler.jpg", "gauges.jpg", "tail-lights.jpg"]


def test_order_media_for_scenes_matches_a_labeled_photo_even_with_a_trailing_photo_word():
    """The actual Maybach-build regression: despite the prompt telling it to
    copy the label verbatim, the model reliably echoes "<label> photo"
    (picked up from the "<label> photo: <description>" hint format it was
    shown) instead of the bare label -- a strict equality check against the
    bare label/category then never matches anything at all, silently
    falling back to the old pool-order behavior for the entire build."""
    scenes = [
        {"media_type": "engine", "photo_label": "V12 engine photo"},
        {"media_type": "detail", "photo_label": "Rear seats with pillows photo."},
    ]
    media = [
        {"path": "v12.jpg", "type": "detail", "category": "other_detail", "label": "V12 engine"},
        {"path": "rear-seats.jpg", "type": "detail", "category": "other_detail", "label": "Rear seats with pillows"},
    ]
    ordered = order_media_for_scenes(scenes, media)
    assert [item["path"] for item in ordered] == ["v12.jpg", "rear-seats.jpg"]


def test_order_media_for_scenes_reserves_a_labeled_photo_against_an_earlier_unlabeled_scene():
    """The actual second-Maybach-build regression: an earlier scene with no
    photo_label at all (an ordinary "4MATIC" beat) grabbed the "Its all
    about rear seat luxury" extra via plain same-type pool order before the
    later "Rear Seat Luxury" scene -- which specifically names that same
    photo via photo_label -- got its turn, leaving the labeled scene to
    fall back to a generic reused interior shot instead of its own photo.
    Labeled matches must be reserved in a pass over the whole scene list
    before any unlabeled scene's type fallback can claim them."""
    scenes = [
        {"media_type": "detail", "photo_label": None},
        {"media_type": "detail", "photo_label": "Its all about rear seat luxury photo"},
    ]
    media = [
        {"path": "rear-seat-luxury.jpg", "type": "detail", "category": "other_detail", "label": "Its all about rear seat luxury"},
        {"path": "front-seat-luxury.jpg", "type": "detail", "category": "other_detail", "label": "Front seat luxuary"},
    ]
    ordered = order_media_for_scenes(scenes, media)
    assert ordered[1]["path"] == "rear-seat-luxury.jpg"
    assert ordered[0]["path"] == "front-seat-luxury.jpg"


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

    def fake_gather_manual_rival_photo(url, images_dir, rival_make, rival_model, mirror=False):
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

    # Main-slot photos and ungrouped extras are tagged so the prompt can hold
    # them to different standards -- a main photo must carry a scene, an extra
    # need not.
    # The exact photo_label the model has to echo is quoted inline, so
    # copying it is a literal string copy rather than a parse of the line.
    assert hints == [
        'MAIN PHOTO -- photo_label: "interior" -- a described detail for interior on the 1993 Toyota Supra Turbo',
        'EXTRA PHOTO -- photo_label: "Gauge Cluster" -- a described detail for Gauge Cluster on the 1993 Toyota Supra Turbo',
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
    assert "Lines marked MAIN PHOTO are the subjects of this video" in prompt
    assert "Lines marked CLOSE-UP are different" in prompt
    assert "copy the\ntext INSIDE THE QUOTES" in prompt


def test_scene_cap_for_photo_hints_grows_with_more_pasted_photos():
    """The actual root cause behind a pasted extra (e.g. cup holders) never
    showing up at all: with the old fixed 8-scene cap, several pasted
    photos each requiring their own dedicated scene didn't fit alongside
    the canonical hook/history/engine/comparison beats, so the model had
    to silently drop some -- they were described in the photo hints but
    never actually assigned a scene. The cap must grow with the number of
    pasted photos, not stay fixed."""
    import single_car_short

    assert single_car_short._scene_cap_for_photo_hints([]) == 8
    assert single_car_short._scene_cap_for_photo_hints(["a", "b"]) == 8
    assert single_car_short._scene_cap_for_photo_hints(["a", "b", "c", "d", "e"]) == 11
    # Deliberately uncapped -- pasting 10 photos should just work.
    assert single_car_short._scene_cap_for_photo_hints(["x"] * 10) == 16


def test_research_script_prompt_reflects_a_raised_scene_cap():
    import single_car_short

    prompt = single_car_short._research_script_prompt(
        "1993 Toyota Supra Turbo", "model year 1993",
        photo_hints=["Gauge Cluster photo: a distinctive analog cluster."], max_scenes=10,
    )
    assert "up to 10 scenes total" in prompt
    assert "5-10 scenes" in prompt


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


def test_the_hook_gets_the_car_name_taken_out_of_it():
    """Asking the model to keep the name out of sentence one failed on 47 of
    56 builds. It is the one rule that is text surgery rather than judgement,
    so it is done in code after the retries instead of asked for again."""
    import single_car_short

    assert single_car_short._strip_car_name(
        "A family vehicle with over 500 horsepower sounds unreal, but that's exactly "
        "what the R63 AMG delivers.", "Mercedes-Benz", "R63 AMG"
    ) == ("A family vehicle with over 500 horsepower sounds unreal, but that's exactly "
          "what this one delivers.")

    # A possessive keeps its article rather than welding onto the next word,
    # and a short name word ("RS") must not break the span and strand the
    # rest of the name mid-sentence.
    assert single_car_short._strip_car_name(
        "With a blistering 2.7-second zero to sixty, the GT2 RS Weissach is Porsche's "
        "most powerful 911 ever crafted for the street.", "Porsche", "911 GT2 RS Weissach"
    ) == ("With a blistering 2.7-second zero to sixty, this one is the most powerful "
          "car ever crafted for the street.")


def test_a_bare_model_word_is_replaced_by_a_noun_not_a_phrase():
    """What stands in depends on the job the name is doing, not on whether it
    came first. Run #202 shipped "the most powerful road-legal this one has"
    because a bare "911" -- the head noun of someone else's phrase -- got the
    subject stand-in. Deleting it would have been no better: "the most
    powerful road-legal ever built"."""
    import single_car_short

    assert single_car_short._strip_car_name(
        "700 horsepower from a twin-turbocharged 3.8-liter flat-six makes this the most "
        "powerful road-legal 911 ever built.", "Porsche", "911 GT2 RS Weissach"
    ) == ("700 horsepower from a twin-turbocharged 3.8-liter flat-six makes this the most "
          "powerful road-legal car ever built.")

    # A name heading its own phrase still gets the phrase stand-in, whether it
    # carries a determiner or simply opens the sentence.
    assert single_car_short._strip_car_name(
        "The 2002 Porsche 911 Turbo hits 60 in 4.0 seconds flat.", "Porsche", "911 Turbo"
    ) == "This one hits 60 in 4.0 seconds flat."


def test_an_unrepairable_hook_is_left_alone_rather_than_mangled():
    import single_car_short

    # The only number here is part of the name, so taking the name out would
    # trade one violation for another.
    assert single_car_short._strip_car_name(
        "The 2011 Corvette Z06 3LZ has an aggressive grille and a lower air intake.",
        "Chevrolet", "Corvette Z06 3LZ") is None
    # Nothing to repair: the hook never names the car.
    assert single_car_short._strip_car_name(
        "Only 500 were ever built, and almost nobody knows it.", "Dodge", "Challenger") is None


def test_repair_runs_on_the_package_and_keeps_the_word_count_honest():
    import single_car_short

    package = {"scenes": [
        {"narration": "With 755 horsepower, the Chevrolet Corvette ZR1 is the quickest yet. "
                      "It is also the loudest."},
        {"narration": "So would you daily it?"},
    ]}
    package["script"] = " ".join(s["narration"] for s in package["scenes"])
    package["word_count"] = single_car_short._word_count(package["script"])
    repaired = single_car_short._repair_script(package, "Chevrolet", "Corvette ZR1")
    opening = repaired["scenes"][0]["narration"]
    assert "Corvette" not in opening and "Chevrolet" not in opening
    # The rest of the scene survives the surgery on its first sentence.
    assert opening.endswith("It is also the loudest.")
    assert repaired["word_count"] == single_car_short._word_count(repaired["script"])


def test_a_listing_is_not_scraped_when_every_slot_is_already_pasted():
    """A listing URL used to force the scrape even with every photo pasted:
    the gallery was downloaded, AI-reviewed image by image, plate-blurred and
    background-removed, then thrown away by the manual overrides. That is
    twenty vision calls on a ten-image gallery, and the scrape is also where
    run #190 hung for fifty minutes."""
    import single_car_short

    every_slot = {field: f"https://example.com/{field}.jpg"
                  for field in single_car_short.MANUAL_PHOTO_FIELDS}
    listing = "https://carsandbids.com/auctions/abc123/2019-corvette-zr1"
    assert single_car_short._pasted_photos_are_enough(every_slot, listing)

    # A gap still needs the gallery to fill it.
    missing_interior = dict(every_slot, interior="")
    assert not single_car_short._pasted_photos_are_enough(missing_interior, listing)

    # Without a listing there is nothing to scrape either way, which is the
    # behaviour this has always had.
    assert single_car_short._pasted_photos_are_enough(missing_interior, "")
    assert single_car_short._pasted_photos_are_enough({}, "")


def test_a_named_rival_is_used_directly_and_carries_its_own_year():
    """Working the rival out from a pasted photo costs a vision call on a
    full-size image and tells you nothing about the rival's years -- which is
    how an R63's 2007 ended up searching for a "BMW X5 M (2007)", a car that
    did not exist until 2010."""
    import single_car_short

    assert single_car_short._year_in("2010 BMW X5 M") == 2010
    assert single_car_short._year_in("BMW X5 M") is None
    assert single_car_short._year_in("") is None
    # A trim with digits in it must not read as a model year.
    assert single_car_short._year_in("Porsche 911 GT2 RS") is None


def test_listing_facts_reach_the_writer_as_ground_truth():
    """Web search knows what the model makes; the listing knows what this car
    makes and what it actually sold for. That is the difference between "it
    costs about" and a figure that is true."""
    import single_car_short

    facts = {
        "title": "2024 Porsche 911 Carrera 4S Coupe",
        "price_text": "Bid to $146,000",
        "facts": {"Engine": "3.0L Turbocharged Flat-6", "Transmission": "Manual (7-Speed)"},
        "sections": {"Highlights": "rated at 443 horsepower and 390 lb-ft of torque."},
    }
    prompt = single_car_short._research_script_prompt(
        "Porsche 911 Carrera 4S", "2024", listing_facts=facts)
    assert "Bid to $146,000" in prompt
    assert "443 horsepower" in prompt
    assert "3.0L Turbocharged Flat-6" in prompt
    # It supplements the research rather than replacing it: the rules the
    # script already follows have to still be in the prompt.
    assert "165-175 words" in prompt or "155-175" in prompt


def test_a_build_without_a_listing_is_unchanged():
    """The block has to vanish entirely when there is no listing, so every
    search-based build writes exactly the prompt it did before."""
    import single_car_short

    assert single_car_short._listing_facts_block({}) == ""
    assert single_car_short._listing_facts_block(None) == ""
    assert single_car_short._research_script_prompt("Audi TT", "2017") == \
        single_car_short._research_script_prompt("Audi TT", "2017", listing_facts={})


def test_photos_json_fills_the_slots_and_individual_flags_still_win():
    """workflow_dispatch allows only 25 inputs and six were photo URLs, so
    they travel as one JSON object now. The --photo-* flags keep working."""
    import single_car_short

    parsed = single_car_short._photos_argument(
        '{"front":"https://x/f.jpg","side":" https://x/s.jpg ","rear":"","engine":null,'
        '"rival":"https://x/r.jpg"}')
    assert parsed == {"front": "https://x/f.jpg", "side": "https://x/s.jpg",
                      "rival": "https://x/r.jpg"}

    # Nothing passed is fine; something passed that cannot be read is not.
    # Run #201 built a whole video from scraped photos because the shell
    # stripped this value's quotes and it was quietly ignored.
    assert single_car_short._photos_argument("") == {}
    assert single_car_short._photos_argument(None) == {}
    for bad in ("not json", "[1,2,3]", "{front:https://x/f.jpg}"):
        with pytest.raises(SystemExit):
            single_car_short._photos_argument(bad)


def test_a_pasted_race_photo_beats_the_automatic_pick(tmp_path, monkeypatch):
    """Without one, the race car is whichever exterior shot ranks highest --
    a guess that raced run #201's GT2 RS as a head-on front shot. A side
    profile is what reads as a car driving, and only the user can see which
    photo that is."""
    import single_car_short

    downloaded = tmp_path / "images" / "manual-race" / "race.jpg"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"jpeg")
    cutout = downloaded.with_name("race-nobg.png")
    cutout.write_bytes(b"png")
    calls = []
    monkeypatch.setattr(single_car_short, "_download_car_photo",
                        lambda url, dest, stem: downloaded)
    monkeypatch.setattr(single_car_short, "_facing_direction_for_photo",
                        lambda path, entry: "right")
    monkeypatch.setattr(single_car_short, "blur_license_plates",
                        lambda path: calls.append(("blur", path)))
    monkeypatch.setattr(single_car_short, "remove_background",
                        lambda path: (calls.append(("nobg", path)), cutout)[1])

    chosen = single_car_short._pasted_race_media(
        "https://example.com/side.jpg", tmp_path / "images", {})
    # Relative to the build directory, the same shape every other media entry
    # uses ("images/manual-extra/0-front.jpg" and so on) -- and a cutout, not
    # the raw snapshot: run #202 raced a rectangle of sky and tarmac against
    # a clean rival cutout.
    assert chosen == {"path": "images/manual-race/race-nobg.png", "facing_direction": "right"}
    assert [kind for kind, _ in calls] == ["blur", "nobg"]

    # No link, or a link that will not download, leaves the automatic pick in
    # charge rather than losing the race entirely.
    assert single_car_short._pasted_race_media("", tmp_path / "images", {}) is None
    monkeypatch.setattr(single_car_short, "_download_car_photo",
                        lambda url, dest, stem: None)
    assert single_car_short._pasted_race_media(
        "https://example.com/bad", tmp_path / "images", {}) is None


def test_the_script_has_to_say_which_car_this_is():
    """The hook withholds the name on purpose, and nothing checked that the
    next beat delivered it. Run #205 left a viewer watching an unidentified
    minivan for twenty-six seconds: its only early mentions were "AMG" hung
    on an engine and "Mercedes'" hung on a differential."""
    import single_car_short

    unnamed = {"scenes": [
        {"narration": "Zero to sixty in just 4.4 seconds -- astonishing for a minivan."},
        {"narration": "Under the hood, its hand-built 6.2-liter V8 churns out 465 lb-ft, "
                      "demonstrating AMG's engineering."},
        {"narration": "The 7-speed paired with Mercedes' 4Matic AWD handles well."},
        {"narration": "Rare in its segment, the R63 AMG stood apart. So would you daily it?"},
    ]}
    found = " | ".join(single_car_short._script_violations(unnamed, "Mercedes-Benz", "R63 AMG"))
    assert "which car this is" in found

    # Naming it inside the window is enough -- the make is not required, since
    # "the 911's aerodynamics" tells a viewer exactly what they are watching.
    named = {"scenes": [
        {"narration": "700 horsepower makes this the most powerful road-legal car ever built."},
        {"narration": "The splitter and intakes enhance the 911's aerodynamics."},
        {"narration": "So would you daily it?"},
    ]}
    assert not [v for v in single_car_short._script_violations(named, "Porsche", "911 GT2 RS Weissach")
                if "which car this is" in v]


def test_a_sub_brand_on_its_own_does_not_identify_the_car():
    """"AMG", "RS", "GT" and the like name a performance division or a body
    style. They ride along on engines and trim and tell a viewer nothing, so
    they cannot be what satisfies the introduction."""
    import single_car_short

    assert "amg" in single_car_short.GENERIC_MODEL_WORDS
    assert "rs" in single_car_short.GENERIC_MODEL_WORDS
    # "GT2" is not generic -- it names this model, unlike a bare "GT".
    assert "gt2" not in single_car_short.GENERIC_MODEL_WORDS

    only_sub_brand = {"scenes": [
        {"narration": "503 horsepower in a family wagon sounds made up."},
        {"narration": "That AMG engine is hand-built by one engineer."},
        {"narration": "So would you daily it?"},
    ]}
    found = " | ".join(single_car_short._script_violations(only_sub_brand, "Mercedes-Benz", "R63 AMG"))
    assert "which car this is" in found


def test_a_mislabelled_image_still_downloads(tmp_path, monkeypatch):
    """The content-type header used to be a hard gate. Any host that labels an
    image as octet-stream lost the photo, and the build carried on without it.
    Decoding is the authoritative test."""
    import io, single_car_short
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="JPEG")

    class Response:
        content = buf.getvalue()
        headers = {"content-type": "application/octet-stream"}
        def raise_for_status(self): pass

    monkeypatch.setattr(single_car_short.requests, "get", lambda *a, **k: Response())
    path = single_car_short._download_car_photo("https://x/photo.JPG", tmp_path, "side")
    assert path is not None and path.exists()


def test_html_pretending_to_be_an_image_is_still_rejected(tmp_path, monkeypatch):
    """The gate existed for a real reason -- a pasted listing page returns real
    bytes that are HTML. Those do not decode, so the decode check covers it."""
    import single_car_short

    class Response:
        content = b"<!doctype html><html><body>not a photo</body></html>"
        headers = {"content-type": "image/jpeg"}  # even when it claims to be one
        def raise_for_status(self): pass

    monkeypatch.setattr(single_car_short.requests, "get", lambda *a, **k: Response())
    assert single_car_short._download_car_photo(
        "https://carsandbids.com/auctions/abc/2002-corvette", tmp_path, "side") is None


def test_a_pasted_photo_that_will_not_download_stops_the_build(tmp_path, monkeypatch):
    """Run #208 pasted five slots, four failed to download, and it shipped a
    Corvette story made from two photos with the drag race running the front
    shot. A pasted link is a decision, not a hint."""
    import pytest, single_car_short

    monkeypatch.setattr(single_car_short, "_download_car_photo", lambda url, d, stem: None)
    with pytest.raises(SystemExit) as excinfo:
        single_car_short.gather_manual_media(
            {"front": "https://x/f.JPG", "interior": "https://x/i.JPG"}, tmp_path / "images", {})
    message = str(excinfo.value)
    assert "front" in message and "interior" in message


def test_mirroring_flips_the_pixels_before_facing_is_measured(tmp_path, monkeypatch):
    """The race runs left to right, so a car pointing the wrong way looks like
    it is reversing. Auto-detection reads the silhouette and is usually right;
    this is the override. It has to land on the pixels before anything reads
    them, or the measured facing would describe a photo that no longer exists."""
    import single_car_short
    from PIL import Image

    source = tmp_path / "images" / "manual-race" / "race.jpg"
    source.parent.mkdir(parents=True)
    # Left half black, right half white -- mirroring has to swap them.
    image = Image.new("RGB", (10, 4), (255, 255, 255))
    for x in range(5):
        for y in range(4):
            image.putpixel((x, y), (0, 0, 0))
    image.save(source)

    order = []
    monkeypatch.setattr(single_car_short, "_download_car_photo", lambda u, d, stem: source)
    monkeypatch.setattr(single_car_short, "blur_license_plates", lambda p: None)
    monkeypatch.setattr(single_car_short, "remove_background", lambda p: p)

    def facing(path, entry):
        with Image.open(path) as opened:
            order.append("dark-left" if opened.getpixel((0, 0)) == (0, 0, 0) else "dark-right")
        return "right"
    monkeypatch.setattr(single_car_short, "_facing_direction_for_photo", facing)

    single_car_short._pasted_race_media("https://x/s.jpg", tmp_path / "images", {}, mirror=True)
    assert order == ["dark-right"], "facing was measured before the mirror was applied"
    with Image.open(source) as result:
        assert result.getpixel((0, 0)) == (255, 255, 255)


def test_without_the_toggle_the_photo_is_left_alone(tmp_path, monkeypatch):
    import single_car_short
    from PIL import Image

    source = tmp_path / "images" / "manual-race" / "race.jpg"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (4, 4), (10, 20, 30)).save(source)
    before = source.read_bytes()

    monkeypatch.setattr(single_car_short, "_download_car_photo", lambda u, d, stem: source)
    monkeypatch.setattr(single_car_short, "blur_license_plates", lambda p: None)
    monkeypatch.setattr(single_car_short, "remove_background", lambda p: p)
    monkeypatch.setattr(single_car_short, "_facing_direction_for_photo", lambda p, e: "right")

    single_car_short._pasted_race_media("https://x/s.jpg", tmp_path / "images", {})
    assert source.read_bytes() == before


def test_a_superlative_has_to_name_the_group_it_wins():
    """Run #210 called a $217,545 718 Spyder "the most luxurious car ever
    produced". The hook rules ask for superlatives, so this is the guard rail
    on that instruction: scoping is what separates a hook from a lie, and the
    true version was available -- the most expensive Spyder Porsche sells."""
    import single_car_short

    bad = {"scenes": [
        {"narration": "A jaw-dropping $217,545 price tag marks this as the most luxurious car "
                      "ever produced."},
        {"narration": "That's the Porsche 718 Spyder RS, making 493 horsepower and 331 lb-ft."},
        {"narration": "So would you take one over a 911?"},
    ]}
    found = " | ".join(single_car_short._script_violations(bad, "Porsche", "718 Spyder RS"))
    assert "claims a superlative over every car ever made" in found

    # A scoped superlative is exactly what the hook is supposed to be.
    good = dict(bad, scenes=[
        {"narration": "A jaw-dropping $217,545 makes this the most expensive Spyder Porsche has sold."},
        {"narration": "That's the 718 Spyder RS, making 493 horsepower and 331 lb-ft."},
        {"narration": "So would you take one over a 911?"},
    ])
    assert not [v for v in single_car_short._script_violations(good, "Porsche", "718 Spyder RS")
                if "superlative" in v]


def test_scoped_superlatives_across_real_builds_are_not_flagged():
    """Checked against every past build before shipping: the rule has to catch
    the false claim without firing on the honest ones."""
    import single_car_short as m

    for line in [
        "This is the most powerful road-going 911 ever built.",
        "It is the quickest Corvette ever made.",
        "The rarest AMG wagon ever sold in the States.",
        "A 700-horsepower car that hits sixty in 2.7 seconds.",
    ]:
        assert not m.UNSCOPED_SUPERLATIVE_RE.search(line), line
    for line in [
        "the most luxurious car ever produced",
        "the fastest car in the world",
        "the greatest car of all time",
    ]:
        assert m.UNSCOPED_SUPERLATIVE_RE.search(line), line


def test_a_live_auction_price_is_not_described_as_a_sale():
    """A high bid on a running auction has not bought anything. Run #217's
    listing was live, the scraper matched only ended-auction wording, and the
    price came back empty -- so the script took a number from search and told
    a viewer a 400hp 993 Turbo trades for about $70,000 while the car in the
    photos was bid to $267,000."""
    import single_car_short

    live = single_car_short._listing_facts_block(
        {"title": "1996 Porsche 911 Turbo", "price_text": "High Bid $267,000",
         "auction_state": "bidding", "facts": {}, "sections": {}})
    assert "High Bid $267,000" in live
    assert "still running" in live
    assert "sold for: High Bid" not in live

    ended = single_car_short._listing_facts_block(
        {"title": "1995 Porsche 911 Carrera", "price_text": "Sold for $67,500",
         "auction_state": "sold", "facts": {}, "sections": {}})
    assert "What it actually sold for: Sold for $67,500" in ended


def test_the_listing_scraper_reads_both_auction_states():
    """The regex only matched ended auctions, so every live listing arrived
    with no price and the value beat was written from search instead."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "scraper/car-source-scraper/src/scrape-carsandbids-facts.js").read_text()
    for wording in ("Sold for", "Winning bid", "High Bid", "Current Bid", "Bid to"):
        assert wording in source, f"{wording} is a real Cars & Bids price label"
    assert "auction_state" in source, "live and sold are different facts and must be distinguishable"


def test_the_price_comes_from_the_top_of_the_page_not_the_first_match_of_a_kind():
    """A listing page carries other auctions further down. Preferring sold
    wording over live wording meant a live listing showing a sold comparable
    underneath it would report that car's price as this one's -- $67,500
    from a Carrera on a Turbo bid to $267,000, which is the exact pair of
    numbers this pipeline already confused once."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "scraper/car-source-scraper/src/scrape-carsandbids-facts.js").read_text()
    # The rule is positional, not a preference between the two patterns.
    assert "a.index - b.index" in source, "the earliest match on the page is this car's"
    assert "soldMatch || liveMatch" not in source, "preferring a kind reintroduces the bug"


def test_the_scraper_wrapper_keeps_whether_the_auction_finished():
    """The scraper reports it and the wrapper rebuilt the dict without it,
    so a live bid reached the prompt labelled "what it actually sold for".
    Run #218 then told a viewer that 993 Turbos fetch $267,000 -- the
    standing bid on one no-reserve car, stated as the model's value."""
    import cars_and_bids
    import inspect

    source = inspect.getsource(cars_and_bids.scrape_auction_facts)
    assert '"auction_state"' in source, "the wrapper must pass the state through"

    import single_car_short

    # An unknown state must not be presented as a completed sale either.
    block = single_car_short._listing_facts_block(
        {"title": "1996 Porsche 911 Turbo", "price_text": "High Bid $267,000",
         "auction_state": "unknown", "facts": {}, "sections": {}})
    assert "What it actually sold for" not in block
