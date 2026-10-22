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


def test_the_word_cap_is_a_ceiling_set_by_the_narrator_s_own_pace():
    # A ceiling, not a midpoint, and derived rather than chosen: the cap is
    # what the narrator can say inside the target without being hurried.
    # It was 175 when the voice was text-to-speech generated at 1.35x; the
    # conversational model talks at its own fixed pace and 175 of its words
    # is a 78-second read.
    from single_car_short import (NARRATION_WORDS_PER_SECOND, SPOKEN_TEMPO,
                                  TARGET_DURATION_SECONDS, WORD_CAP)
    assert WORD_CAP == round(TARGET_DURATION_SECONDS * NARRATION_WORDS_PER_SECOND
                             * SPOKEN_TEMPO)
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
    """The whole point of ticking a pasted photo (like the labeled extra
    "Gauge Cluster") is that the script ends up talking about what's
    actually in it -- so each hint pairs a human-readable label with a
    concrete AI-described detail. An unticked close-up is shown and not
    narrated, which is covered in its own test."""
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
        [{"label": "Gauge Cluster", "describe": True, "url": "https://example.com/gauges.jpg"}],
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
    # Classic's floor is seven scenes now, not five: the old "5-8, the higher
    # end only when you have several pasted photos" told a build with no
    # photos to use five or six, which is ~108 words at one 18-word sentence
    # a scene against a 145-154 target.
    assert "7-10 scenes" in prompt


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
    # What it forbids is the segment's fields, which is what the segment is.
    # It used to forbid naming any competitor in the words as well; that was
    # never needed to suppress a visual.
    assert "turned off the rival-comparison SEGMENT" in prompt
    assert "do NOT set rival_make/rival_model/main_horsepower/" in prompt
    assert "do NOT name any specific competitor car anywhere" not in prompt


def test_research_script_prompt_omits_the_no_comparison_block_by_default():
    import single_car_short

    prompt = single_car_short._research_script_prompt("1993 Toyota Supra Turbo", "model year 1993")
    assert "turned off the rival-comparison SEGMENT" not in prompt


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


def _mazdaspeed_package():
    """The Mazdaspeed3 script as it shipped, minus the torque figure: seven
    good beats and one broken rule."""
    import single_car_short

    scenes = [
        {"narration": "Unleashing 263 horsepower, this compact hatchback hides serious performance."},
        {"narration": "The second-generation Mazdaspeed3 got stiffer springs and a bigger intake."},
        {"narration": "The MZR 2.3 DISI turbo spools quickly and pulls hard."},
        {"narration": "So would you daily one, or is it too much?"},
    ]
    script = " ".join(scene["narration"] for scene in scenes)
    return {"scenes": scenes, "script": script,
            "word_count": single_car_short._word_count(script),
            # The build did look the torque up; the script just never said it.
            "key_specs": {"horsepower": "263 hp", "torque": "280 lb-ft",
                          "engine": "2.3L Turbo I4"}}


def test_a_broken_rule_is_fixed_one_scene_at_a_time(monkeypatch):
    """The retry loop's only move is to rewrite the whole script, which
    throws away six good beats to fix a seventh -- the Mazdaspeed3 build
    lost its closing on all four attempts while everything else came out
    well. A rule that survives the retries is now asked about on its own."""
    import single_car_short

    asked = []

    def fake(violation, numbered, label, specs=""):
        asked.append((violation, specs))
        # Not restating the 263 the script already opens on -- a repair that
        # repeats a figure is discarded as no better, which is the point of
        # the repetition rule and is what a real repair is told to avoid.
        return 3, "The MZR 2.3 DISI turbo puts 280 lb-ft through the front wheels, and pulls hard."

    monkeypatch.setattr(single_car_short, "_rewrite_scene_for", fake)
    package = single_car_short._repair_violations(
        _mazdaspeed_package(), "Mazda", "Mazdaspeed3", {"median": 16000}, "Mazdaspeed3")

    assert "torque" in asked[0][0], "the missing torque figure is what it was asked about"
    assert "280 lb-ft" in asked[0][1], "and it was handed the figure to use"
    assert "280 lb-ft" in package["script"]
    assert package["word_count"] == single_car_short._word_count(package["script"])
    assert not single_car_short._script_violations(
        package, "Mazda", "Mazdaspeed3", {"median": 16000})


def _with_history(narrations):
    import single_car_short
    scenes = [{"narration": text} for text in narrations]
    script = " ".join(narrations)
    return {"scenes": scenes, "script": script,
            "word_count": single_car_short._word_count(script),
            "key_specs": {"horsepower": "656 hp", "torque": "590 lb-ft"},
            "history": {"year": 1988, "fact": "Won its class at Le Mans."}}


def test_a_researched_history_fact_that_never_gets_spoken_is_a_violation():
    """The prompt has asked for a history beat right after the hook for a
    long time, and three builds running came back with the specs and then
    four beats about how the car looks. Whether the narration says the fact
    is checkable even though "has history" is not."""
    import single_car_short

    unsaid = _with_history([
        "With 656 horsepower and 590 lb-ft, this thing moves.",
        "The red calipers look sharp against the black wheels.",
        "So would you buy one?",
    ])
    assert any("1988" in rule for rule in
               single_car_short._script_violations(unsaid, "Aston Martin", "Vantage",
                                                   market={"median": 200000}))

    said = _with_history([
        "With 656 horsepower and 590 lb-ft, this thing moves.",
        "In 1988 it won its class at Le Mans.",
        "So would you buy one?",
    ])
    assert not any("1988" in rule for rule in
                   single_car_short._script_violations(said, "Aston Martin", "Vantage",
                                                       market={"median": 200000}))

    # A build whose research found no dateable fact is not punished for it.
    undated = _with_history(["It makes 656 hp and 590 lb-ft.", "So would you?"])
    undated["history"] = {"year": None, "fact": "Well liked."}
    assert not any("never said it" in rule for rule in
                   single_car_short._script_violations(undated, "Aston Martin", "Vantage",
                                                       market={"median": 200000}))


def test_the_repair_can_reach_the_researched_history():
    """A scene that says nothing is usually a scene with nothing to say.
    The repair has no web search, so the fact has to be handed to it."""
    import single_car_short

    sheet = single_car_short._spec_sheet(_with_history(["anything"]))
    assert "history -- 1988: Won its class at Le Mans." in sheet


def _rules(narrations, **extra):
    import single_car_short
    package = {"scenes": [{"narration": t} for t in narrations], "script": " ".join(narrations),
               "start_year": 2025, "end_year": 2025,
               "history": {"year": 1988, "fact": "Won its class at Le Mans."}}
    package.update(extra)
    return single_car_short._script_violations(package, "Aston Martin", "Vantage",
                                               market={"median": 200000})


def test_a_history_with_nothing_specific_in_it_is_rejected():
    """The first rule keyed on "debuted" and threw out "debuted as a
    limited-production supercar, blending Mercedes luxury with McLaren's
    racing pedigree" -- which is thin, but thin is the reason, and the year
    a car arrived is a real part of its story. What makes a fact specific
    is something to hold on to: a figure that is not just its own year, or
    a name that is not the car's own."""
    import single_car_short

    def rejected(fact, year, make="Mercedes Benz", model="Slr Mclaren Roadster"):
        scenes = [f"It makes 617 hp and 575 lb-ft in {year}.", "So would you?"]
        package = {"scenes": [{"narration": t} for t in scenes], "script": " ".join(scenes),
                   "start_year": year, "end_year": year + 1,
                   "history": {"year": year, "fact": fact}}
        return any("nothing specific" in rule for rule in
                   single_car_short._script_violations(package, make, model,
                                                       market={"median": 300000}))

    # A figure, or a name that is not the car's own.
    assert not rejected("Only 2,157 were built before Mercedes ended production.", 2009)
    assert not rejected("The tub was built by McLaren in Woking while the V8 "
                        "was assembled at Affalterbach.", 2003)

    # Neither.
    assert rejected("Revered by enthusiasts, it has a real performance legacy.", 2008)
    assert rejected("Aston Martin launched the updated Vantage in 2025 with significant "
                    "revisions over its predecessor.", 2025, "Aston Martin", "Vantage")

    # Its own year is not a figure, and its own name is not a name.
    assert rejected("In 2008 the SLR McLaren Roadster arrived as a supercar.", 2008)


def test_a_scene_that_asserts_a_part_matters_without_saying_why_is_caught():
    """The Vantage build spent eight of ten scenes on the grille, the air
    vents, the rear haunches, the character lines, the inspection plaque
    and the taillight. The ban was in the prompt and nothing read it."""
    caught = _rules([
        "In 1988 it won its class at Le Mans, and it makes 656 hp and 590 lb-ft.",
        "The hand-built inspection plaque highlights the bespoke craftsmanship of the Vantage.",
        "So would you?",
    ])
    assert any("without saying anything about it" in rule for rule in caught)

    # The same verb with a real claim attached is not padding.
    assert not any("without saying anything about it" in rule for rule in _rules([
        "In 1988 it won its class at Le Mans, and it makes 656 hp and 590 lb-ft.",
        "That rear diffuser cuts lift by 15% over the old car.",
        "So would you?",
    ]))


def test_a_refused_repair_says_what_it_was_offered(monkeypatch):
    """The closing question failed twice running with two independent
    repairs both declining it, and the reason each printed sat in a runner
    log unreachable by the time anyone asked. A build that cannot say why
    it shipped broken can only be debugged by guessing."""
    import single_car_short

    single_car_split = single_car_short._REPAIR_LOG
    single_car_split.clear()
    monkeypatch.setattr(single_car_short, "_rewrite_closing_as_question",
                        lambda *a: "It really is a great car.")
    package = {"scenes": [{"narration": "It makes 263 hp."},
                          {"narration": "Revered in enthusiast circles."}]}
    single_car_short._repair_closing(package, "Mazdaspeed3")

    assert single_car_split[-1]["outcome"] == "unusable"
    assert "great car" in single_car_split[-1]["returned"], "what came back is what matters"

    # And a repair that trades one rule for another says which rule it left.
    single_car_split.clear()
    monkeypatch.setattr(
        single_car_short, "_rewrite_scene_for",
        lambda *a, **k: (3, "It makes 280 lb-ft, and the seats give it a sporty feel."))
    single_car_short._repair_violations(
        _mazdaspeed_package(), "Mazda", "Mazdaspeed3", {"median": 16000}, "Mazdaspeed3")
    assert single_car_split[-1]["outcome"] in ("did not help", "traded for another")
    assert single_car_split[-1]["still"], "the rules it left behind are named"
    single_car_split.clear()


def test_the_repair_is_given_the_figures_the_build_already_looked_up():
    """The Mazdaspeed3 build knew its torque was 280 lb-ft -- it is in
    key_specs -- and still shipped with the rule broken, because the repair
    was handed the scenes alone and told not to invent anything. It had no
    way to supply the one fact it was asked for."""
    import single_car_short

    sheet = single_car_short._spec_sheet(_mazdaspeed_package())
    assert "torque: 280 lb-ft" in sheet
    # Blank fields are left out rather than offered as facts.
    assert single_car_short._spec_sheet({"key_specs": {"torque": ""}}) == ""
    assert single_car_short._spec_sheet({}) == ""


def test_a_repair_that_trades_one_violation_for_another_is_thrown_away(monkeypatch):
    """This is the whole reason a model is safe to let loose on a finished
    script: the same checker that found the problem decides whether the fix
    was one."""
    import single_car_short

    # Adds the torque figure, but smuggles in a banned phrase.
    monkeypatch.setattr(
        single_car_short, "_rewrite_scene_for",
        lambda *a: (3, "It makes 280 lb-ft, and the bolstered seats give it a sporty feel."))
    package = single_car_short._repair_violations(
        _mazdaspeed_package(), "Mazda", "Mazdaspeed3", {"median": 16000}, "Mazdaspeed3")

    assert "sporty feel" not in package["script"]
    assert "280 lb-ft" not in package["script"], "the whole edit is discarded, not half of it"


def test_a_repair_that_cannot_be_made_does_not_fail_the_build(monkeypatch):
    import single_car_short

    def boom(*a):
        raise RuntimeError("no")

    monkeypatch.setattr(single_car_short, "_rewrite_scene_for", boom)
    before = _mazdaspeed_package()
    after = single_car_short._repair_violations(
        _mazdaspeed_package(), "Mazda", "Mazdaspeed3", {"median": 16000}, "Mazdaspeed3")
    assert after["scenes"] == before["scenes"]

    # A scene number that does not exist is refused rather than applied.
    monkeypatch.setattr(single_car_short, "_rewrite_scene_for", lambda *a: (99, "Anything."))
    after = single_car_short._repair_violations(
        _mazdaspeed_package(), "Mazda", "Mazdaspeed3", {"median": 16000}, "Mazdaspeed3")
    assert after["scenes"] == before["scenes"]


def test_a_rejected_history_is_researched_again_rather_than_reworded(monkeypatch):
    """Asked twice to fix its history, the SLR build returned "the SLR
    debuted as a limited-production supercar" and then "in 2008, the SLR
    Roadster debuted as a limited-production model" -- the same fact in new
    words. Rewording is all a scene rewrite can do, and the rule needs a
    different fact."""
    import single_car_short

    asked = {}

    def research(label, rejected, year_scope=""):
        asked.update(label=label, rejected=rejected)
        return {"year": 1999, "fact": "Mercedes built 75 of the 300 SLR's successor for homologation."}

    monkeypatch.setattr(single_car_short, "_research_replacement_history", research)
    monkeypatch.setattr(single_car_short, "_rewrite_scene_for",
                        lambda *a, **k: pytest.fail("the history is not a wording problem"))

    scenes = ["It makes 617 hp and 575 lb-ft.",
              "In 2008 the SLR Roadster debuted as a limited-production model.",
              "So would you?"]
    package = {"scenes": [{"narration": t} for t in scenes], "script": " ".join(scenes),
               "start_year": 2008, "end_year": 2009,
               "history": {"year": 2008, "fact": "It arrived as a supercar."}}

    fixed = single_car_short._repair_violations(
        package, "Mercedes-Benz", "SLR McLaren", {"median": 300000}, "SLR McLaren")

    assert "arrived as a supercar" in asked["rejected"], \
        "the rejected fact is named so it is not returned again"
    # The fact does not state 1999, so the year field is dropped rather than
    # the fact -- the sentence is the thing, the year is metadata.
    assert fixed["history"]["year"] is None
    assert "75 of the 300 SLR" in fixed["script"], "and the narration actually says it"
    single_car_short._REPAIR_LOG.clear()


def test_a_researched_history_that_is_no_better_is_discarded(monkeypatch):
    import single_car_short

    monkeypatch.setattr(single_car_short, "_research_replacement_history",
                        lambda *a, **k: {"year": 2008, "fact": "It was a supercar."})
    scenes = ["It makes 617 hp and 575 lb-ft.",
              "In 2008 the SLR Roadster debuted as a limited-production model.",
              "So would you?"]
    original = list(scenes)
    package = {"scenes": [{"narration": t} for t in scenes], "script": " ".join(scenes),
               "start_year": 2008, "end_year": 2009,
               "history": {"year": 2008, "fact": "It arrived as a supercar."}}

    fixed = single_car_short._repair_violations(
        package, "Mercedes-Benz", "SLR McLaren", {"median": 300000}, "SLR McLaren")
    assert [s["narration"] for s in fixed["scenes"]] == original
    assert single_car_short._REPAIR_LOG[-1]["outcome"] == "did not help"
    single_car_short._REPAIR_LOG.clear()


def test_a_rule_that_cannot_be_fixed_does_not_block_the_ones_that_can(monkeypatch):
    """The SLR build shipped with three violations because the first one --
    its history -- could not be repaired and the loop stopped there. The
    padding and the empty scene were never attempted."""
    import single_car_short

    scenes = [
        "In 2003 the SLR debuted, which is when it went on sale.",
        "It makes 617 hp and 575 lb-ft.",
        "The gear shifter emphasizes its race-bred DNA.",
        "So would you?",
    ]
    package = {"scenes": [{"narration": t} for t in scenes], "script": " ".join(scenes),
               "start_year": 2003, "end_year": 2009,
               "history": {"year": 2003, "fact": "The SLR debuted in 2003."}}

    seen = []

    def fake(violation, numbered, label, specs=""):
        seen.append(violation)
        if "history" in violation:
            return 1, "In 2003 the SLR debuted, which is when it went on sale."  # no change
        return 3, "The shifter sits where the 300 SLR's did, 8 inches further back."

    monkeypatch.setattr(single_car_short, "_rewrite_scene_for", fake)
    # The history rule goes to the researcher, not the scene rewrite; here
    # it finds nothing better, which is the refusal being tested.
    monkeypatch.setattr(single_car_short, "_repair_history",
                        lambda *a: seen.append("history rule") or None)
    fixed = single_car_short._repair_violations(
        package, "Mercedes-Benz", "SLR McLaren", {"median": 300000}, "SLR McLaren")

    assert any("history" in rule for rule in seen), "it tried the history first"
    assert any("without saying anything" in rule for rule in seen), \
        "and went on to the rule it could fix"
    assert "8 inches" in fixed["script"]
    single_car_short._REPAIR_LOG.clear()


def test_repairs_stop_rather_than_looping_on_a_rule_nothing_can_fix(monkeypatch):
    import single_car_short

    calls = []
    monkeypatch.setattr(single_car_short, "_rewrite_scene_for",
                        lambda v, n, l: calls.append(v) or (3, "It pulls hard."))
    single_car_short._repair_violations(
        _mazdaspeed_package(), "Mazda", "Mazdaspeed3", {"median": 16000}, "Mazdaspeed3")
    assert len(calls) <= single_car_short.MAX_TARGETED_REPAIRS


def test_the_closing_is_rewritten_into_a_question_on_its_own(monkeypatch):
    """The whole-script retry asks for the closing question among six other
    constraints and loses it -- the Mazdaspeed3 build failed the rule on all
    four attempts and shipped "the Mazdaspeed3's legacy is built on balance
    and excitement." Asked on its own, with just the hook to call back to,
    it is a much easier request."""
    import single_car_short

    asked = {}

    def fake(hook, closing, label):
        asked.update(hook=hook, closing=closing, label=label)
        return "So would you daily a 263-horsepower hatch, or is that too much?"

    monkeypatch.setattr(single_car_short, "_rewrite_closing_as_question", fake)
    package = {"scenes": [
        {"narration": "Unleashing 263 horsepower, this hatchback hides serious performance."},
        {"narration": "Revered in enthusiast circles, its legacy is built on balance."},
    ]}
    single_car_short._repair_closing(package, "Mazdaspeed3")

    assert package["scenes"][-1]["narration"].endswith("?")
    assert package["word_count"] == single_car_short._word_count(package["script"])
    # It is given the hook so the question can call back to it.
    assert "263 horsepower" in asked["hook"]


def test_a_closing_that_already_asks_something_is_left_alone(monkeypatch):
    import single_car_short

    monkeypatch.setattr(single_car_short, "_rewrite_closing_as_question",
                        lambda *a: pytest.fail("nothing to rewrite"))
    package = {"scenes": [{"narration": "It makes 263 hp."},
                          {"narration": "So would you daily one?"}]}
    single_car_short._repair_closing(package, "Mazdaspeed3")
    assert package["scenes"][-1]["narration"] == "So would you daily one?"


def test_an_unusable_rewrite_is_discarded_rather_than_shipped(monkeypatch):
    """Same rule the hook surgery follows: a repair that is not an
    improvement is thrown away, not shipped."""
    import single_car_short

    original = "Revered in enthusiast circles, its legacy is built on balance."
    for bad in ("It is a great car.",                       # not a question
                "So " + "really " * 30 + "would you?"):     # blows the budget
        monkeypatch.setattr(single_car_short, "_rewrite_closing_as_question",
                            lambda *a, _b=bad: _b)
        package = {"scenes": [{"narration": "It makes 263 hp."}, {"narration": original}]}
        single_car_short._repair_closing(package, "Mazdaspeed3")
        assert package["scenes"][-1]["narration"] == original

    # A failed call is not a failed build either.
    def boom(*a):
        raise RuntimeError("no")

    monkeypatch.setattr(single_car_short, "_rewrite_closing_as_question", boom)
    package = {"scenes": [{"narration": "It makes 263 hp."}, {"narration": original}]}
    single_car_short._repair_closing(package, "Mazdaspeed3")
    assert package["scenes"][-1]["narration"] == original


def test_a_depreciation_rate_is_caught_as_padding():
    """The prompt has banned stat-shaped padding since run #176, but the ban
    was a list of literal phrases and this is a shape. The Mazdaspeed3 build
    shipped "depreciating roughly 4% per year" -- a number computed from two
    prices already in the same sentence."""
    import single_car_short

    def rules(sentence):
        return single_car_short._script_violations(
            {"scenes": [{"narration": "Making 263 horsepower and 280 lb-ft, it flies."},
                        {"narration": sentence},
                        {"narration": "So would you daily one?"}]},
            "Mazda", "Mazdaspeed3", market={"median": 16000})

    for padded in ("It went from $24,000 to $16,000, depreciating roughly 4% per year.",
                   "That is an annual depreciation of about 8%.",
                   "Call it 5% a year off the sticker."):
        assert any("padding" in rule for rule in rules(padded)), padded

    # The two prices on their own are the point -- they are not padding.
    assert not any("padding" in rule
                   for rule in rules("It cost $24,000 new and goes for $16,000 today."))
    # Nor is a percentage that is about the car rather than its price.
    assert not any("padding" in rule
                   for rule in rules("Torque split can send 50% of it rearward."))


def test_a_name_revealed_at_the_end_is_not_swapped_for_a_stand_in():
    """Run #231 shipped "thanks to the touch of car - this one." The hook
    had built up to naming the car after a dash; taking the name out left
    the build-up pointing at nothing. No stand-in repairs that, so the hook
    keeps its name instead."""
    import single_car_short

    assert single_car_short._strip_car_name(
        "This is the only minivan that rockets from zero to sixty in just 4.4 seconds, "
        "thanks to the touch of AMG \u2014 the R63 AMG.", "Mercedes-Benz", "R63 AMG") is None


def test_a_bare_noun_is_only_used_where_the_phrase_has_an_article():
    """"the most powerful road-legal 911" supplies one, so "car" drops
    straight in. "the touch of Mercedes-Benz AMG" does not -- the name
    there is the maker, not the phrase's head noun -- and substituting
    produced "the touch of car"."""
    import single_car_short

    assert single_car_short._strip_car_name(
        "This is the only minivan that rockets from zero to sixty in just 4.4 seconds, "
        "thanks to the touch of Mercedes-Benz AMG.", "Mercedes-Benz", "R63 AMG") is None

    # A possessive counts as the phrase's article; a preposition ends the
    # phrase before one is found.
    assert single_car_short._phrase_has_determiner("is Porsche's most powerful ")
    assert single_car_short._phrase_has_determiner("makes this the most powerful road-legal ")
    assert not single_car_short._phrase_has_determiner("thanks to the touch of ")


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
    # Rounded on the way in: handed an exact figure as ground truth the
    # writer repeats it, and "about $150,000" costs fewer spoken words than
    # "a hundred and forty-six thousand" for the same information.
    assert "Bid to $150,000" in prompt
    assert "443 horsepower" in prompt
    assert "3.0L Turbocharged Flat-6" in prompt
    # It supplements the research rather than replacing it: the rules the
    # script already follows have to still be in the prompt.
    # The band the prompt asks for is the one the constants define, rather
    # than a number pasted into the test and left behind when it moves.
    from single_car_short import TARGET_WORDS as _target
    assert f"{_target[0]}-{_target[1]} words" in prompt


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
    assert "High Bid $270,000" in live, "the figure is rounded for speaking"
    assert "still running" in live
    assert "sold for: High Bid" not in live

    ended = single_car_short._listing_facts_block(
        {"title": "1995 Porsche 911 Carrera", "price_text": "Sold for $67,500",
         "auction_state": "sold", "facts": {}, "sections": {}})
    assert "What it actually sold for: Sold for $68,000" in ended


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


def test_asked_for_angles_replace_a_beat_rather_than_lengthening_the_video():
    """The person knows things research will not surface on its own -- that
    the SLR launched against the Enzo and the Carrera GT at roughly the same
    money and is the only one of the three that did not go up. Asked for,
    that is a beat; not asked for, the script is exactly what it was."""
    import single_car_short

    assert single_car_short._angles_block("") == ""
    assert single_car_short._angles_block(None) == ""
    assert single_car_short._angles_block("   \n\n  ") == ""

    block = single_car_short._angles_block(
        "- the trinity: Enzo, Carrera GT, SLR\n\n  only the SLR didn't go up\n")
    assert "1. the trinity: Enzo, Carrera GT, SLR" in block
    assert "2. only the SLR didn't go up" in block, "bullets and blank lines are tidied"
    # The video does not get longer; a styling beat goes first.
    assert "REPLACES a beat you would otherwise have written" in block
    assert "how the car looks first" in block


def test_wrapped_notes_stay_one_bullet_so_the_ranking_is_the_one_typed():
    """The SLR run was handed three notes in a box that held five lines,
    because two of them ran over. Splitting on newlines made "sold ~1,400 by
    end of 2007." a bullet of its own, ranked second, ahead of the gearbox
    note it had nothing to do with -- which makes the ranking meaningless."""
    import single_car_short

    assert single_car_short._split_angles(
        "McLaren wanted a sports car, Mercedes wanted a GT - planned 3,500 cars,\n"
        "sold ~1,400 by end of 2007.\n"
        "617hp carbon supercar with a 5-speed torque converter auto - the complaint\n"
        "that sticks, and why it sat still while the Enzo and Carrera GT climbed.\n"
        "722 Edition: 150 built, 641 bhp, 44kg lighter."
    ) == [
        "McLaren wanted a sports car, Mercedes wanted a GT - planned 3,500 cars, "
        "sold ~1,400 by end of 2007.",
        "617hp carbon supercar with a 5-speed torque converter auto - the complaint "
        "that sticks, and why it sat still while the Enzo and Carrera GT climbed.",
        "722 Edition: 150 built, 641 bhp, 44kg lighter.",
    ]

    # Notes are rarely punctuated carefully, so the join cannot depend on a
    # trailing full stop -- a wrapped line starts lower-case either way.
    assert single_car_short._split_angles(
        "722 badge = Moss's 7:22am start\nat the 1955 Mille Miglia\n150 built, 641 bhp"
    ) == ["722 badge = Moss's 7:22am start at the 1955 Mille Miglia", "150 built, 641 bhp"]

    # Markers and blank lines both start a bullet whatever its case, so a
    # deliberately lower-case note can still be written as one.
    assert single_car_short._split_angles("- first\n- second\n- third") == [
        "first", "second", "third"]
    assert single_car_short._split_angles("1. first\n2. second") == ["first", "second"]
    assert single_car_short._split_angles("the gearbox\n\nonly the SLR didn't go up") == [
        "the gearbox", "only the SLR didn't go up"]
    assert single_car_short._split_angles("") == []
    assert single_car_short._split_angles(None) == []
    assert single_car_short._split_angles("  \n\n - \n") == []


def test_a_typed_note_may_name_a_rival_the_comparison_checkbox_would_have_gagged():
    """The checkbox turns off the drag-race segment. Written as "anywhere in
    the script", it also gagged the notes box: twice the SLR was asked for
    "why it sat still while the Enzo and Carrera GT climbed" and twice
    neither car was nameable, so it came back as "didn't quite rival
    contemporaries" -- a claim about driving feel, not the one about value
    that was asked for. A car the person typed outranks a checkbox."""
    import single_car_short

    # No carve-out is needed any more, because nothing is being gagged: the
    # checkbox switches off the segment and the words were never part of it.
    plain = single_car_short._research_script_prompt(
        "Mercedes-Benz SLR McLaren", "model years 2004-2009", disable_comparison=True)
    assert "What is NOT switched off is the words" in plain
    assert "do NOT name any specific competitor car anywhere" not in plain

    asked = single_car_short._research_script_prompt(
        "Mercedes-Benz SLR McLaren", "model years 2004-2009", disable_comparison=True,
        angles="why it sat still while the Enzo and Carrera GT climbed")
    # The segment is still off: the fields stay null whatever the words say.
    assert "do NOT set rival_make/rival_model/main_horsepower/" in asked
    assert "even if the narration does name " in asked
    # And their phrasing is protected from the softening that actually happened.
    assert 'do not soften it into "rivals"' in asked
    assert "supercars costing three times as much" in asked

    # And the carve-out only exists when the comparison was turned off.
    assert "ONE exception" not in single_car_short._research_script_prompt(
        "Mercedes-Benz SLR McLaren", "model years 2004-2009",
        angles="why it sat still while the Enzo and Carrera GT climbed")


def test_a_long_note_list_degrades_by_dropping_the_tail_whole():
    """Every note used to be mandatory, so five of them bought five thin
    beats -- one build finished on "But what do you think?" because four
    angles had eaten the words. Ranked, the top ones are written properly
    and the rest are left out, rather than all of them getting a clause."""
    import single_car_short

    block = single_car_short._angles_block("\n".join(f"Note {n}" for n in range(1, 6)))
    assert "IN THEIR ORDER OF PREFERENCE -- 1 first" in block
    assert "  1. Note 1" in block and "  5. Note 5" in block
    assert "leave the rest out ENTIRELY" in block
    assert "never shorten or drop a higher-numbered note to make room for a lower one" in block
    assert "Say nothing about the ones you leave out" in block

    # Three tiers, so a theory is not dressed up as a fact and a wrong claim
    # is not repeated back.
    assert "state it as fact" in block
    assert "say it as what people say" in block
    assert "do NOT repeat it" in block
    assert "never say the person asked for it" in block


def test_the_angles_reach_the_prompt_and_the_workflow_can_send_them():
    import single_car_short
    from pathlib import Path

    prompt = single_car_short._research_script_prompt(
        "Mercedes-Benz SLR McLaren", "model years 2004-2009",
        angles="the trinity: Enzo, Carrera GT, SLR")
    assert "the trinity: Enzo, Carrera GT, SLR" in prompt
    # And nothing is added to a build that asked for nothing.
    assert "ASKED FOR THESE SPECIFICALLY" not in single_car_short._research_script_prompt(
        "Mercedes-Benz SLR McLaren", "model years 2004-2009")

    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/cars-research.yml").read_text()
    assert "angles:" in workflow and "--angles" in workflow


def test_the_manifest_records_what_the_build_was_asked_for():
    """The build that proved the angles feature worked could not say it had
    been used: the trinity beat was in the narration and build_inputs read
    as though nobody had asked for it. current_price was missing the same
    way, so the create form read it back empty however it had been set."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/single_car_short.py").read_text()
    recorded = source[source.index('"build_inputs": {'):]
    recorded = recorded[:recorded.index("\n        },")]

    for field in ('"angles": args.angles', '"current_price": args.current_price'):
        assert field in recorded, f"{field} never reaches the manifest"

    # Every key the create form refills itself from has to be written here,
    # or the field silently comes back blank.
    form = (Path(__file__).resolve().parents[1] / "web/src/App.jsx").read_text()
    read_back = set(re.findall(r"inputs\.([a-z_]+)", form))
    written = set(re.findall(r'"([a-z_]+)":', recorded))
    missing = read_back - written - {"photos"}
    assert not missing, f"the form refills from {sorted(missing)}, which no build records"


def test_the_history_research_call_does_not_ask_for_json_mode():
    """The API refuses web search together with JSON mode -- "Web Search
    cannot be used with JSON mode", a 400 that made every history repair
    fail before it started. Research is the point of that call, so the
    shape gives way and the answer is parsed."""
    from pathlib import Path
    import single_car_short

    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/single_car_short.py").read_text()
    block = source[source.index("def _research_replacement_history"):]
    block = block[:block.index("def _repair_history")]
    assert "web_search_preview" in block, "the whole point is that it researches"
    assert "json_object" not in block, "the two cannot be asked for together"

    # So the reply is parsed however it arrives.
    assert single_car_short._loose_json(
        '```json\n{"year": 2007, "fact": "Only 178 were sold in the US."}\n```'
    )["year"] == 2007
    assert single_car_short._loose_json(
        'Here you go: {"year": 2007, "fact": "x"} hope that helps')["fact"] == "x"
    with pytest.raises(ValueError):
        single_car_short._loose_json("no object here")


def test_the_empty_scene_rewrite_is_told_what_would_satisfy_the_rule():
    """Offered "the quad exhaust tips not only underscore its AMG sporting
    credentials but also enhance the engine's deep growl" for exactly that
    violation -- the verb kept, another clause added, still no number. What
    satisfies the rule is worth saying outright."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/single_car_short.py").read_text()
    block = source[source.index("def _rewrite_scene_for"):]
    block = block[:block.index("def _repair_violations")]
    assert 'if "without saying anything about it" in violation:' in block
    assert "put a real number in" in block
    assert "Do not keep" in block and "add another clause" in block


def test_the_history_fact_is_spoken_as_written(monkeypatch):
    """Prepending the year produced "In 2007, in 2007, Mercedes-Benz
    produced approximately 200 units" when the fact already carried one,
    and pinned a whole-production-run figure to a single model year when it
    did not -- about 200 R63s exist in total, not in 2007. The fact is
    asked for as a complete sentence, so it is used as one."""
    import single_car_short

    def package():
        return {"scenes": [{"narration": "503 hp and 465 lb-ft."}, {"narration": "old"},
                           {"narration": "So would you daily a 503-horsepower minivan?"}],
                "start_year": 2006, "end_year": 2013,
                "history": {"year": None, "fact": "old"}}

    def spoken(found):
        monkeypatch.setattr(single_car_short, "_research_replacement_history",
                            lambda *a, **k: found)
        fixed = single_car_short._repair_history(
            package(), "Mercedes-Benz", "R63 AMG", {"median": 64500}, "R63 AMG")
        return fixed["scenes"][1]["narration"] if fixed else None

    # A fact spanning the run has no year, and that is a real answer.
    assert spoken({"year": None,
                   "fact": "only about 200 R63 AMGs were built across the whole run."}) == \
        "Only about 200 R63 AMGs were built across the whole run."
    # A dated event keeps its own year, and only says it once.
    dated = spoken({"year": 1989, "fact": "In 1989 it won its class at Le Mans."})
    assert dated.count("1989") == 1
    # Nothing to say is still unusable.
    assert spoken({"year": 1989, "fact": ""}) is None
    single_car_short._REPAIR_LOG.clear()


def test_a_question_mark_is_not_an_ending():
    """Squeezed for words by four asked-for angles, one build finished on
    "But what do you think?" -- which passes the ends-on-a-question rule and
    could end any video ever made. Length is not the test: "so would you
    daily it?" is the same five words and proposes something."""
    import single_car_short

    def rules(closing):
        scenes = ["It makes 503 hp and 465 lb-ft in 2007.", closing]
        package = {"scenes": [{"narration": t} for t in scenes], "script": " ".join(scenes),
                   "start_year": 2007, "end_year": 2007,
                   "history": {"year": 2007, "fact": "Only 200 were built."}}
        return [v for v in single_car_short._script_violations(
            package, "Mercedes-Benz", "R63 AMG", market={"median": 64500}) if "last scene" in v]

    assert rules("But what do you think?")
    assert rules("Thoughts?")
    assert rules("It is a great car.")          # not a question at all
    assert not rules("So would you daily it?")  # short, but about this car
    assert not rules("So would you daily a five-hundred-horsepower minivan, "
                     "or is that a step too far?")


def test_a_pasted_photo_with_no_beat_is_a_violation():
    """The R63 build downloaded the engine photo, described it, handed it to
    the model under "you MUST write one scene for each" -- and wrote seven
    scenes, none of them typed engine. So the photo was not in the video.
    The prompt had said it the whole time; nothing checked."""
    import single_car_short

    scenes = [("exterior", "Four-point-four seconds to sixty in a minivan with 503 horsepower."),
              ("exterior", "In 2007 Mercedes built around 200 R63 AMGs, and it makes 465 lb-ft."),
              ("interior", "Six or seven seats, and the middle row folds flat."),
              ("exterior", "So would you daily a 503-horsepower minivan?")]
    package = {"scenes": [{"media_type": t, "narration": n} for t, n in scenes],
               "script": " ".join(n for _, n in scenes),
               "start_year": 2007, "end_year": 2007,
               "history": {"year": 2007, "fact": "Only 200 were built."}}

    def orphans(slots):
        return [v for v in single_car_short._script_violations(
            package, "Mercedes-Benz", "R63 AMG", market={"median": 64500},
            pasted_slots=slots) if "photo and no scene" in v]

    assert orphans(("front", "side", "rear", "engine", "interior"))
    # The interior photo does have a beat, so only the engine is named.
    assert "engine" in orphans(("engine", "interior"))[0]
    assert len(orphans(("engine", "interior"))) == 1
    # A slot that was never pasted is not demanded.
    assert not orphans(("front", "side", "rear", "interior"))
    assert not orphans(())


def test_the_pasted_slots_reach_the_checker():
    """A rule nothing passes its input to is a rule that never fires."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/single_car_short.py").read_text()
    assert "pasted_slots=tuple(slot for slot in MANUAL_PHOTO_FIELDS" in source, \
        "build_short has to tell research_script which photos were pasted"
    # And the retry loop has to see it, or the model is never told to fix it.
    loop = source[source.index("def research_script("):]
    loop = loop[:loop.index("def _visual_highlight_for_scenes")]
    assert loop.count("pasted_slots") >= 4


def test_a_reveal_cannot_be_stripped_of_the_name_it_exists_to_say():
    """"...400 horsepower -- this is the Porsche 911 Turbo" came back as
    "this is this one", a sentence that introduces nothing. The dash guard
    missed it because the dash sits before "this is", not before the name."""
    import single_car_short

    for reveal in ("A twin-turbocharged air-cooled beauty that delivers 400 horsepower"
                   "—this is the Porsche 911 Turbo.",
                   "Four hundred horsepower, and that's the 911 Turbo.",
                   "Meet the Porsche 911 Turbo, with 400 horsepower."):
        assert single_car_short._strip_car_name(reveal, "Porsche", "911 Turbo") is None, reveal

    # A name that is not being revealed is still repaired.
    assert single_car_short._strip_car_name(
        "A family vehicle with over 500 horsepower sounds unreal, but that's exactly "
        "what the R63 AMG delivers.", "Mercedes-Benz", "R63 AMG"
    ) == ("A family vehicle with over 500 horsepower sounds unreal, but that's exactly "
          "what this one delivers.")


def test_a_rate_of_appreciation_is_the_same_padding_as_depreciation():
    """"appreciating annually around 5%" shipped in the 911 build. Both
    directions are arithmetic on two prices the sentence already gave."""
    import single_car_short

    for padded in ("appreciating annually around 5%", "depreciating roughly 4% per year",
                   "an annual appreciation of 8%", "a 5% annual depreciation rate"):
        assert single_car_short.DEPRECIATION_PADDING_RE.search(padded), padded
    for fine in ("it costs $277,000 today", "it makes 400 hp, up 5% on the old car"):
        assert not single_car_short.DEPRECIATION_PADDING_RE.search(fine), fine


def test_an_empty_alignment_is_a_failure_not_an_answer():
    """Run #37340574003 shipped 152 words with an empty word_timeline. The
    whisper call returned 200 with no per-word timings, nothing raised, and
    the renderer split 55 seconds evenly across seven scenes -- so captions
    drifted against a voice whose beats run from 3 to 9 seconds. No
    violation, no log line, a healthy-looking build and a broken video."""
    import single_car_short
    from unittest.mock import patch

    class Result:
        def __init__(self, words): self.words = words

    empty = Result([])
    good = Result([{"word": "Only", "start": 0.0, "end": 0.4}])

    with patch.object(single_car_short, "OpenAI"), \
         patch.object(single_car_short, "with_openai_retry", return_value=empty) as call:
        try:
            single_car_short.transcribe_word_timeline(__file__)
        except ValueError as exc:
            assert "no word timings" in str(exc)
        else:
            raise AssertionError("an empty alignment must not be returned as a timeline")
    assert call.call_count == single_car_short.ALIGNMENT_ATTEMPTS, "it asks again before giving up"

    # And one good answer after an empty one is enough.
    with patch.object(single_car_short, "OpenAI"), \
         patch.object(single_car_short, "with_openai_retry", side_effect=[empty, good]):
        assert single_car_short.transcribe_word_timeline(__file__) == [
            {"word": "Only", "start": 0.0, "end": 0.4}]


def test_a_build_can_speak_a_previous_build_s_script_again():
    """A build can be good and still ship broken. Researching the car a third
    time to get the same words back is a coin toss, because the script is
    written fresh every run."""
    import single_car_short
    from unittest.mock import patch
    import io, json as _json
    from pathlib import Path

    manifest = _json.dumps({
        "title": "A Roadster Born from Legends",
        "youtube_title": "How the SLR Became a Legend",
        "scenes": [{"narration": "New, it was $495,000."}, {"narration": "Would that sway you?"}],
        "history": {"year": 2007, "fact": "x"},
    }).encode()

    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with patch("urllib.request.urlopen", return_value=Response(manifest)):
        package = single_car_short.script_package_from_build("single-slr-20261001014518")
    assert package["script"] == "New, it was $495,000. Would that sway you?"
    assert package["word_count"] == 9
    assert len(package["scenes"]) == 2
    assert package["title"] == "A Roadster Born from Legends"

    # The flag exists and the workflow can send it.
    workflow = (Path(single_car_short.__file__).resolve().parents[2]
                / ".github/workflows/cars-research.yml").read_text()
    assert "reuse_script:" in workflow and "--reuse-script" in workflow


def test_a_second_writing_style_sits_beside_the_one_everything_shipped_with():
    """Four of Driving Lab's Shorts, read off their own transcripts, come out
    alike: 192-204 words in 14-19 sentences averaging twelve words. Ours run
    139 in seven beats of twenty, which reads as a tour rather than an
    argument. This is that shape, opt-in, with the published path untouched."""
    import single_car_short
    from pathlib import Path

    classic = single_car_short.style_of("classic")
    dense = single_car_short.style_of("dense")
    assert classic["words"] == single_car_short.TARGET_WORDS, "the default is what shipped"
    assert classic["seconds"] == single_car_short.TARGET_DURATION_SECONDS
    assert classic["block"] == "", "nothing is added to a build that did not ask"
    # Anything unrecognised falls back rather than failing a build.
    assert single_car_short.style_of("nonsense") is classic
    assert single_car_short.style_of(None) is classic
    assert single_car_short.style_of("") is classic

    # 177 words over 63s was 2.81 a second after a 1.12x squeeze and read too
    # fast. At 155-165 the raw take is 62-66s, inside the 58-68 window, so
    # nothing is compressed -- the density is entirely runtime.
    assert dense["words"] == (155, 165)
    low, high = dense["words"]
    assert dense["window"][0] <= low / 2.5 and high / 2.5 <= dense["window"][1], \
        "the whole range has to land inside the window or the voice gets squeezed"
    assert dense["seconds"] == 63.0
    assert dense["window"] == (58.0, 68.0)

    plain = single_car_short._research_script_prompt("Subaru Impreza", "2001-2003")
    packed = single_car_short._research_script_prompt("Subaru Impreza", "2001-2003", style="dense")
    assert "144-154 words" in plain and "155-165 words" in packed
    assert "ONE OR TWO complete sentences" in plain
    assert "TWO OR THREE short sentences" in packed
    assert "HOW THIS ONE IS WRITTEN" not in plain
    # The style has the last word: it lands after the angles and comparison
    # blocks, and says so, because the base prompt asks for the opposite.
    assert packed.index("HOW THIS ONE IS WRITTEN") > packed.index("Write a narration")
    assert "this overrides the pacing guidance above" in packed
    assert "Do NOT tour the" in packed
    assert "two- or three-word line on its own" in packed
    # Every one of the nine transcripts read closes on a binary the viewer has
    # to take a side in, which is what a 262-comment thread is made of. The
    # base prompt only asks for "a question", which "would you buy one?" meets.
    assert "makes the viewer pick a side" in packed
    assert "Would you buy one?" in packed and "there is nothing to disagree with" in packed

    # The flag, the workflow and the dashboard all carry it.
    workflow = (Path(single_car_short.__file__).resolve().parents[2]
                / ".github/workflows/cars-research.yml").read_text()
    assert "script_style:" in workflow and "--style" in workflow
    app = (Path(single_car_short.__file__).resolve().parents[2] / "web/src/App.jsx").read_text()
    # Three values in one field now that a 35-second length sits beside it.
    assert ('script_style: shortStyle ? "short" : mediumStyle ? "medium" '
            ': denseStyle ? "dense" : "classic"') in app
    assert "Dense script" in app
    assert "useState(false)" in app.split("const [denseStyle, setDenseStyle] = ")[1][:20], \
        "off by default, so the daily upload is undisturbed"


def test_the_dense_style_asks_for_more_sentences_not_merely_shorter_ones():
    """Two dense builds came back at 144 and 136 words against a 170-185
    target. They wrote the sentence length (10-11 words against the usual 20)
    and not the volume -- twelve or thirteen sentences instead of sixteen to
    twenty. The base prompt was telling them to "prefer fewer, fuller scenes
    over many thin one-liners", which is the opposite, and a trailing
    override does not beat a specific instruction."""
    import single_car_short

    plain = single_car_short._research_script_prompt("Subaru Impreza WRX", "2002")
    packed = single_car_short._research_script_prompt("Subaru Impreza WRX", "2002", style="dense")

    # Classic keeps the clause word for word; dense gets the opposite.
    assert "Prefer fewer, fuller scenes over many thin one-liners" in plain
    assert "Thin, punchy one-liners are wanted here, not avoided" in packed
    # "At least THREE in every scene" was here and is deliberately gone:
    # seven scenes of three is 21 sentences, roughly 210 words, against a
    # 155-165 target -- so it contradicted both the per-scene range and the
    # total, and ten dense builds spread from 110 to 177 words. One number,
    # stated the same way in all three places.
    assert "at least THREE in every scene" not in packed
    assert "SIXTEEN OR SEVENTEEN sentences across the whole script" in packed
    assert "SIXTEEN OR SEVENTEEN of them" in packed
    # And the arithmetic is spelled out, since the failure is silent otherwise.
    assert "seven scenes carrying two or three each" in packed
    # Classic gets its own count for the same reason.
    assert "EIGHT OR NINE sentences across the whole script" in plain
    assert "have to be MORE of them" in packed


def test_a_short_script_is_told_how_far_short_and_how_to_fix_it():
    """Four rewrites of "outside the target" produced 136 and 144 words. A
    verdict is not an instruction: the retry now says the shortfall, converts
    it to sentences, and rules out the two things the model reaches for --
    longer sentences and repeating a number it already gave."""
    import single_car_short
    from unittest.mock import patch

    seen = {}

    def capture(prompt, max_scenes=8):
        seen.setdefault("prompts", []).append(prompt)
        return {"scenes": [{"narration": "Short.", "media_type": "exterior"}],
                "script": "Short.", "word_count": 136, "title": "t", "youtube_title": "t"}

    with patch.object(single_car_short, "_request_script_package", side_effect=capture), \
         patch.object(single_car_short, "_script_violations", return_value=[]), \
         patch.object(single_car_short, "_repair_history", side_effect=lambda p, *a, **k: p), \
         patch.object(single_car_short, "_repair_violations", side_effect=lambda p, *a, **k: p):
        single_car_short.research_script("Subaru", "Impreza WRX", max_attempts=2, style="dense")

    retry = seen["prompts"][1]
    assert "You were 19 words SHORT" in retry, "155 - 136"
    assert "more short sentences spread across the scenes you already have" in retry
    assert "do NOT make the existing sentences longer" in retry
    assert "do not repeat a number or a fact you have already given" in retry
    # The first attempt has no shortfall to report.
    assert "words SHORT" not in seen["prompts"][0]


def test_the_dense_scene_count_is_set_by_the_word_target_not_by_pasted_photos():
    """Scene count is what actually makes the word count: scenes x sentences
    x sentence length. Three dense builds sat at 144, 136 and 138 words while
    the other two terms moved under instruction -- sentences per scene went
    2.0 to 3.0 and sentence length 11.3 to 8.9 -- because the clause setting
    the scene count gated it on pasted photos. None were pasted, so it took
    the low end: five scenes, three sentences, nine words, 135."""
    import single_car_short

    plain = single_car_short._research_script_prompt("Subaru Impreza WRX", "2002")
    packed = single_car_short._research_script_prompt("Subaru Impreza WRX", "2002", style="dense")

    # The photo gate is gone from classic too -- it was the same bug, fixed
    # for dense and left in classic, and it is why the no-photo M5 CS builds
    # came back at 126 and 119 words while the 12-photo RS5 build hit 147.
    assert "only when you have" not in plain
    assert "split across 7-8 scenes" in plain
    assert "split across 7-8 scenes" in packed
    assert "The scene count is what makes the word count here" in packed
    assert "How many photos were pasted does NOT limit it" in packed
    # max_scenes still flows in rather than being hard-coded into the style.
    assert "split across 7-6 scenes" in single_car_short._research_script_prompt(
        "Subaru Impreza WRX", "2002", style="dense", max_scenes=6)


def test_the_trim_uses_the_style_s_cap_not_the_classic_constant():
    """_enforce_word_cap runs last on every build and was hard-coded to
    WORD_CAP, the classic 154. That made the dense target of 170-185
    unreachable by construction: whatever the model wrote, the trim cut it
    back, and because it drops whole sentences and then whole scenes it
    overshoots downward. Three dense builds landed at 144, 136 and 138 while
    every prompt change was aimed at a model that may have been writing the
    right length all along."""
    import single_car_short

    def package(words):
        sentence = ("word " * 9) + "end."
        sentences = [sentence] * (words // 10)
        scenes = [{"narration": " ".join(sentences[i:i + 3]), "media_type": "exterior"}
                  for i in range(0, len(sentences), 3)]
        return {"scenes": scenes, "script": " ".join(sentences)}

    classic = package(200)
    single_car_short._enforce_word_cap(classic, cap=single_car_short.TARGET_WORDS[1])
    assert single_car_short._word_count(classic["script"]) <= 154

    dense_high = single_car_short.style_of("dense")["words"][1]
    assert dense_high == 165
    packed = package(200)
    single_car_short._enforce_word_cap(packed, cap=dense_high)
    kept = single_car_short._word_count(packed["script"])
    assert 150 <= kept <= 165, f"a dense script has to survive its own target, got {kept}"

    # And the call site passes the style's cap rather than defaulting.
    from pathlib import Path
    source = Path(single_car_short.__file__).read_text()
    assert "cap=target_high," in source, "the trim must take the style's cap"


def test_the_title_withholds_the_name_and_pays_off_the_first_sentence():
    """The one upload with a title that did not name the car took 87% of its
    views from the Shorts feed against 12-30% for everything else, and 209
    views in an hour against 75-138 in a week. It also lost 89.2% of them in
    the first seconds, because the title promised a single model year and the
    video opened on horsepower -- they swiped in for one thing and heard
    another."""
    import single_car_short

    scenes = [{"narration": "543 horsepower, but more interesting than that is the weight.",
               "media_type": "exterior"}]

    named = single_car_short._script_violations(
        {"youtube_title": "Can the 2024 M3 CS Really Dominate the Track? 🔥", "scenes": scenes},
        "BMW", "M3 CS")
    assert any("your title names the car" in v for v in named)
    assert any("different things" in v for v in named), "and it promises the wrong thing"

    # A title built from the hook, withholding the name, passes both.
    good = single_car_short._script_violations(
        {"youtube_title": "543 horsepower, and the weight is the real story 🔥", "scenes": scenes},
        "BMW", "M3 CS")
    assert not any("your title" in v for v in good), good

    # Sharing one real word is enough; filler words are not evidence.
    bare = single_car_short._script_violations(
        {"youtube_title": "This is the one you want 🔥", "scenes": scenes}, "BMW", "M3 CS")
    assert any("different things" in v for v in bare)

    # Nothing to check when research returned no title -- the fallback covers it.
    assert not any("your title" in v for v in single_car_short._script_violations(
        {"youtube_title": "", "scenes": scenes}, "BMW", "M3 CS"))


def test_the_fallback_title_withholds_the_name_too():
    """A build whose research returned no title would otherwise quietly undo
    the rule, since the fallback was built out of year, make and model."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars/automation"))
    import youtube_metadata

    package = {"car": {"make": "BMW", "model": "M3 CS"},
               "key_specs": {"horsepower": "543 hp", "engine": "3.0L Turbocharged I6"}}
    title = youtube_metadata.title_for(package)
    assert "BMW" not in title and "M3" not in title, title
    assert "543 hp" in title

    # And with nothing to say, it still says nothing about the name.
    assert "BMW" not in youtube_metadata.title_for({"car": {"make": "BMW", "model": "M3 CS"}})


def test_a_thirty_five_second_style_sits_beside_the_other_two():
    """Average view duration is about nineteen seconds across eleven uploads
    whatever the runtime -- 0:19 on a 0:49 video and 0:19 on a 1:04 one. So
    completion is arithmetic: the same nineteen seconds is 39% of 49 seconds
    and 30% of 64, and the feed rewards the ratio. At 35 it is 54%."""
    import single_car_short
    from pathlib import Path

    short = single_car_short.style_of("short")
    assert short["words"] == (80, 100)
    assert short["seconds"] == 35.0
    assert short["window"] == (30.0, 40.0)
    low, high = short["words"]
    assert short["window"][0] <= low / 2.5 and high / 2.5 <= short["window"][1], \
        "the whole range must land inside the window or the voice gets squeezed"

    prompt = single_car_short._research_script_prompt("BMW M3", "2024", style="short")
    assert "80-100 words" in prompt
    assert "4-5 scenes" in prompt
    assert "This is a 35-second video, not a minute" in prompt
    assert "ONE argument and nothing else" in prompt
    # The other two are untouched by it.
    assert "80-100" not in single_car_short._research_script_prompt("BMW M3", "2024")
    assert "80-100" not in single_car_short._research_script_prompt(
        "BMW M3", "2024", style="dense")

    # One field, three values, so the two boxes cannot both win.
    app = (Path(single_car_short.__file__).resolve().parents[2] / "web/src/App.jsx").read_text()
    assert ('script_style: shortStyle ? "short" : mediumStyle ? "medium" '
            ': denseStyle ? "dense" : "classic"') in app
    assert "35-second short" in app
    assert "if (e.target.checked) { setDenseStyle(false); setMediumStyle(false); }" in app
    assert "if (e.target.checked) { setShortStyle(false); setMediumStyle(false); }" in app
    # Reopening a past build restores whichever it was.
    assert 'setShortStyle(String(inputs.script_style || "") === "short");' in app


def test_a_number_said_twice_is_caught_but_a_name_is_not():
    """At 95 words, "only about 800 were made" twice costs a third of the
    video; an earlier build said "227 horsepower" three times in 136. Run
    across sixteen finished builds this flags nine, every one a genuine
    repeated specification."""
    import single_car_short as s

    assert s._repeated_specs(
        "Only 800 units were produced worldwide. The arches are flared. "
        "Only about 800 were made worldwide.", "Mercedes-Benz", "C63 AMG") == [("800", 2)]

    assert s._repeated_specs(
        "227 horsepower from the flat-four. It makes 227 horsepower through all four wheels. "
        "227 horsepower is plenty.", "Subaru", "Impreza WRX") == [("227", 3)]

    # A chassis code is not a specification. "this one 993 car" matched an
    # earlier version because "cars" was in the unit list; it is not now.
    assert s._repeated_specs(
        "This one 993 car is renowned. Only 5,978 units of the 911 Turbo (993) were built. "
        "The 993 Turbo's wide body is unmistakable.", "Porsche", "911 Turbo") == []

    # Nor is an edition name, nor the car's own number, nor a year.
    assert s._repeated_specs(
        "The 722 badge recalls Moss. The 722 Edition was lighter.",
        "Mercedes-Benz", "SLR McLaren") == []
    assert s._repeated_specs(
        "Built in 2007. By 2007 it was gone, 1,400 units sold.",
        "Mercedes-Benz", "SLR") == []
    assert s._repeated_specs(
        "The 500 makes 471 hp. The LC 500 is rare.", "Lexus", "LC 500") == []

    # And it reaches the violations a build is judged on.
    found = s._script_violations(
        {"scenes": [{"narration": "Only 800 units were produced. Only about 800 were made.",
                     "media_type": "exterior"}],
         "youtube_title": ""}, "Mercedes-Benz", "C63 AMG")
    assert any("you state the same number more than once" in v for v in found)
    assert any('"800" 2 times' in v for v in found)


# --- the word-count floor -------------------------------------------------
#
# _enforce_word_cap has always closed overshoot deterministically. Undershoot
# was only ever asked for in the prompt, and measured across 29 real builds
# the asking worked 11 times: classic averaged 137 words against a 145-154
# target, dense 145 against 155-165, with dense ranging 110-177. These pin
# the other half of that.


def _floor_package(sentences, words_each=10):
    """A package of `sentences` scenes, one sentence each, so the word count
    is exactly predictable."""
    import single_car_short

    scenes = [{"narration": " ".join(f"w{i}x{j}" for j in range(words_each)) + "."}
              for i in range(sentences)]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes)}
    package["word_count"] = single_car_short._word_count(package["script"])
    return package


def test_a_short_script_is_lengthened_until_it_reaches_the_floor(monkeypatch):
    import single_car_short

    package = _floor_package(7)          # 7 scenes x 10 words = 70
    assert package["word_count"] == 70
    calls = []

    def fake_sentences(numbered, label, shortfall, specs="", avoid=""):
        calls.append(shortfall)
        # Ten words into one middle scene per round.
        return [(3, " ".join(f"new{len(calls)}n{j}" for j in range(10)) + ".")]

    monkeypatch.setattr(single_car_short, "_more_sentences_for", fake_sentences)
    monkeypatch.setattr(single_car_short, "_script_violations",
                        lambda *a, **k: [])

    out = single_car_short._extend_to_floor(
        package, floor=90, cap=200, label="Test Car", make="Test", model="Car")
    assert out["word_count"] >= 90, "the floor is the point"
    # Measured each round rather than asked for once: the second call knows
    # the first one landed.
    assert calls == [20, 10]
    assert out["script"] == " ".join(s["narration"] for s in out["scenes"])


def test_a_script_already_at_the_floor_is_left_exactly_alone(monkeypatch):
    import single_car_short

    package = _floor_package(10)         # 100 words
    before = dict(package)

    def fail(*a, **k):
        raise AssertionError("nothing should be asked for when the count is fine")

    monkeypatch.setattr(single_car_short, "_more_sentences_for", fail)
    monkeypatch.setattr(single_car_short, "_research_extra_beat", fail)
    out = single_car_short._extend_to_floor(
        package, floor=100, cap=200, label="Test Car", make="Test", model="Car")
    assert out["word_count"] == before["word_count"] == 100


def test_the_floor_never_writes_over_the_hook_or_the_closing_question(monkeypatch):
    import single_car_short

    package = _floor_package(5)
    hook, closer = package["scenes"][0]["narration"], package["scenes"][-1]["narration"]

    # Offered for scene 1 and the last scene, which are both out of bounds.
    monkeypatch.setattr(single_car_short, "_more_sentences_for",
                        lambda *a, **k: [(1, "Hook rewrite."), (5, "Closing rewrite.")])
    monkeypatch.setattr(single_car_short, "_research_extra_beat", lambda *a, **k: "")
    monkeypatch.setattr(single_car_short, "_script_violations", lambda *a, **k: [])

    out = single_car_short._extend_to_floor(
        package, floor=200, cap=400, label="Test Car", make="Test", model="Car")
    assert out["scenes"][0]["narration"] == hook, "the hook carries the retention decision"
    assert out["scenes"][-1]["narration"] == closer, "the closing question is shaped"


def test_an_addition_that_breaks_a_rule_is_discarded_not_shipped(monkeypatch):
    import single_car_short

    package = _floor_package(7)
    before = package["word_count"]

    monkeypatch.setattr(single_car_short, "_more_sentences_for",
                        lambda *a, **k: [(3, "Repeats 627 horsepower again and again here now.")])
    monkeypatch.setattr(single_car_short, "_research_extra_beat", lambda *a, **k: "")
    # Clean before, broken after: the same asymmetry _repair_violations uses
    # to decide a repair was not one.
    monkeypatch.setattr(single_car_short, "_script_violations",
                        lambda pkg, *a, **k: ["you state the same number more than once"]
                        if "627" in (pkg.get("script") or "") else [])

    out = single_car_short._extend_to_floor(
        package, floor=200, cap=400, label="Test Car", make="Test", model="Car")
    assert out["word_count"] == before, "a longer script that broke a rule is not an improvement"


def test_the_floor_will_not_push_the_script_past_the_cap(monkeypatch):
    import single_car_short

    package = _floor_package(14)         # 140 words
    monkeypatch.setattr(single_car_short, "_more_sentences_for",
                        lambda *a, **k: [(3, " ".join(f"big{j}" for j in range(80)) + ".")])
    monkeypatch.setattr(single_car_short, "_research_extra_beat", lambda *a, **k: "")
    monkeypatch.setattr(single_car_short, "_script_violations", lambda *a, **k: [])

    out = single_car_short._extend_to_floor(
        package, floor=155, cap=165, label="Test Car", make="Test", model="Car")
    assert out["word_count"] <= 165, "overshooting the cap undoes the trim that ran before this"
    assert out["word_count"] == 140, "so the oversized addition is dropped"


def test_research_takes_over_once_the_spec_sheet_stops_landing(monkeypatch):
    """The spec sheet gets spent -- it is one car's figures -- and every
    further sentence off it repeats something the script already said, which
    the rule check rejects. That is the M5 CS case, and it is where a build
    stalls short unless something goes and finds new material."""
    import single_car_short

    package = _floor_package(7)
    researched = []

    monkeypatch.setattr(single_car_short, "_more_sentences_for",
                        lambda *a, **k: [(3, "Nothing new to add from the sheet.")])

    def fake_research(label, already, year_scope=""):
        researched.append(label)
        return " ".join(f"fact{j}" for j in range(30)) + "."

    monkeypatch.setattr(single_car_short, "_research_extra_beat", fake_research)
    # The spec-sheet sentence is a repeat; the researched beat is not.
    monkeypatch.setattr(single_car_short, "_script_violations",
                        lambda pkg, *a, **k: ["you state the same number more than once"]
                        if "Nothing new to add" in (pkg.get("script") or "") else [])

    out = single_car_short._extend_to_floor(
        package, floor=95, cap=200, label="Test Car", make="Test", model="Car")
    assert researched == ["Test Car"], "it went and found new material instead of stalling"
    assert out["word_count"] >= 95


def test_a_floor_it_cannot_reach_says_so_instead_of_shipping_quietly(monkeypatch, capsys):
    import single_car_short

    package = _floor_package(7)
    monkeypatch.setattr(single_car_short, "_more_sentences_for", lambda *a, **k: [])
    monkeypatch.setattr(single_car_short, "_research_extra_beat", lambda *a, **k: "")
    monkeypatch.setattr(single_car_short, "_script_violations", lambda *a, **k: [])

    out = single_car_short._extend_to_floor(
        package, floor=160, cap=200, label="Test Car", make="Test", model="Car")
    assert out["word_count"] == 70
    printed = capsys.readouterr().out
    assert "WARNING" in printed and "160-word floor" in printed


def test_a_failed_lengthening_call_leaves_the_build_standing(monkeypatch):
    """A build is worth more than a word count -- the same trade the repair
    loop makes."""
    import single_car_short

    package = _floor_package(7)

    def boom(*a, **k):
        raise RuntimeError("the API said no")

    monkeypatch.setattr(single_car_short, "_more_sentences_for", boom)
    monkeypatch.setattr(single_car_short, "_script_violations", lambda *a, **k: [])
    out = single_car_short._extend_to_floor(
        package, floor=160, cap=200, label="Test Car", make="Test", model="Car")
    assert out["word_count"] == 70


def test_every_style_states_one_sentence_budget_not_three():
    """Dense asked for "TWO TO FOUR" sentences a scene, "at least THREE" in
    every scene, and "15-18" in total, all at once. Seven scenes of three is
    21 sentences -- about 210 words against a 155-165 target -- so the
    instructions could not all be met and which one won was luck, which is
    how ten dense builds spread from 110 to 177 words."""
    import single_car_short

    dense = single_car_short.SCRIPT_STYLES["dense"]
    assert "at least THREE in every scene" not in dense["scene_note"]
    assert "SIXTEEN OR SEVENTEEN" in dense["scene_note"]
    assert "SIXTEEN OR SEVENTEEN" in dense["block"]
    assert "TWO OR THREE" in dense["sentences"]

    # Classic gated its scene count on pasted photos, so a build with none
    # was told to use five or six -- about 108 words at one 18-word sentence
    # a scene, against a 145-154 target. Dense had this clause removed
    # already; classic kept it.
    classic = single_car_short.SCRIPT_STYLES["classic"]
    assert "only when you have" not in classic["scene_count"]
    assert classic["scene_count"].startswith("7-")

    # The arithmetic has to actually reach each style's own target.
    for name in ("classic", "dense"):
        style = single_car_short.SCRIPT_STYLES[name]
        low, high = style["words"]
        assert low < high <= 200, name


# --- prices, rounded ------------------------------------------------------


def test_an_exact_price_in_the_narration_is_a_violation():
    """Rounding has been asked for in the prompt and builds still shipped
    "they go for about $172,233 today" -- nine words of speech where "about
    a hundred and seventy grand" is six and says the same thing. So it is
    checked, like every other rule that asking did not fix."""
    import single_car_short

    scenes = [
        {"headline": "", "narration": "627 horsepower in a four-door. That's the M5 CS."},
        {"headline": "", "narration": "Originally $142,995, they go for about $172,233 today."},
        {"headline": "", "narration": "So would you daily it, or keep it for weekends?"},
    ]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes),
               "youtube_title": "Four Doors, 627 Horses"}
    found = [v for v in single_car_short._script_violations(package, "Bmw", "M5 Cs")
             if "exact price" in v]
    assert found, "both figures are exact and both cost words"
    # The repair is told what to say instead, not just what is wrong.
    assert "$142,995" in found[0] and "about $140,000" in found[0]
    assert "$172,233" in found[0] and "about $170,000" in found[0]


def test_a_price_already_round_is_left_alone():
    import single_car_short

    scenes = [
        {"headline": "", "narration": "627 horsepower in a four-door. That's the M5 CS."},
        {"headline": "", "narration": "About $140,000 new, and about $170,000 today."},
        {"headline": "", "narration": "So would you daily it, or keep it for weekends?"},
    ]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes)}
    assert not [v for v in single_car_short._script_violations(package, "Bmw", "M5 Cs")
                if "exact price" in v]


def test_an_option_price_under_ten_thousand_is_left_alone():
    """The dense M5 build said "adaptive suspension costs $3,500" -- that is
    an option, not the car, and it is already two words spoken."""
    import single_car_short

    scenes = [
        {"headline": "", "narration": "627 horsepower in a four-door. That's the M5 CS."},
        {"headline": "", "narration": "The adaptive suspension was a $3,500 option."},
        {"headline": "", "narration": "So would you tick that box?"},
    ]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes)}
    assert not [v for v in single_car_short._script_violations(package, "Bmw", "M5 Cs")
                if "exact price" in v]


def test_a_year_is_not_mistaken_for_a_price():
    """"In 2022" and "1,132 units" are four-digit numbers that are not
    prices, and flagging them would make the rule noise."""
    import single_car_short

    scenes = [
        {"headline": "", "narration": "In 2022, BMW built 1,132 of them. That's the M5 CS."},
        {"headline": "", "narration": "It makes 627 horsepower and 553 lb-ft."},
        {"headline": "", "narration": "So would you have one?"},
    ]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes)}
    assert not [v for v in single_car_short._script_violations(package, "Bmw", "M5 Cs")
                if "exact price" in v]


def test_the_exact_figure_never_reaches_the_writer_in_any_style():
    """Rounding the prompt matters more than the rule: handed "$172,233" as
    ground truth, the writer repeats it, and the rule then has to argue with
    the evidence it was given."""
    import single_car_short

    for style in ("classic", "dense", "short"):
        for source in ("sale", "stated"):
            prompt = single_car_short._research_script_prompt(
                "Bmw M5 Cs", "2022", style=style,
                market={"median": 172233, "source": source})
            worth = prompt[prompt.index("WHAT THIS CAR IS WORTH"):]
            worth = worth[:worth.index("Round every price")]
            assert "172,233" not in worth, (style, source)
            assert "$170,000" in worth, (style, source)
            assert "Round every price" in prompt, (style, source)


def test_the_rounding_rule_reaches_a_build_with_no_verified_value():
    """The no-market branch still lets the writer state an original price,
    so it is the one case the rule has to cover on its own. It is also a
    plain string, not an f-string, which is how the rule first landed in the
    prompt as a literal "{_ROUND_PRICE_RULE}"."""
    import single_car_short

    prompt = single_car_short._research_script_prompt("Bmw M5 Cs", "2022")
    assert "Round every price" in prompt
    assert "{_ROUND_PRICE_RULE}" not in prompt


def test_the_listing_s_own_price_is_rounded_on_the_way_in():
    import single_car_short

    block = single_car_short._listing_facts_block(
        {"price_text": "Sold for $172,233", "auction_state": "sold",
         "facts": {}, "sections": {}})
    assert "$170,000" in block and "172,233" not in block
    # And the label is not doubled up: price_text carries its own wording.
    assert "about Sold for" not in block


# --- the ending, and the value beat --------------------------------------


def test_the_tail_pad_gives_the_closing_question_room_to_land(monkeypatch, tmp_path):
    """Across 25 shipped builds the silence after the last word was whatever
    gpt-audio happened to leave: 0.16s to 0.83s, median 0.30. The E63 build
    at 0.16s ends while the question's last syllable is still decaying."""
    import single_car_short

    calls = []

    class Done:
        stdout = "55.00"

    def fake_run(args, **kwargs):
        calls.append(args)
        return Done()

    monkeypatch.setattr(single_car_short.subprocess, "run", fake_run)
    monkeypatch.setattr(single_car_short.Path, "replace", lambda self, other: None, raising=False)

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    # Last word ends at 54.84 of 55.00 -- 0.16s, the real E63 figure.
    out = single_car_short.pad_tail_silence(audio, 54.84)
    assert out == pytest.approx(55.0 + (single_car_short.MIN_TAIL_SILENCE - 0.16), abs=0.02)
    pad = [a for a in calls if "ffmpeg" in a[0]]
    assert pad, "it has to actually pad the file"
    assert any("apad=pad_dur=" in str(part) for part in pad[0])


def test_audio_that_already_ends_cleanly_is_left_alone(monkeypatch, tmp_path):
    import single_car_short

    class Done:
        stdout = "55.00"

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return Done()

    monkeypatch.setattr(single_car_short.subprocess, "run", fake_run)
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    # A full second of tail already.
    assert single_car_short.pad_tail_silence(audio, 54.0) == 55.0
    assert not [a for a in calls if "ffmpeg" in a[0]], "nothing to do, so nothing is re-encoded"
    # And a build whose alignment failed has no last word to measure from.
    assert single_car_short.pad_tail_silence(audio, 0) == 55.0


def test_a_value_trend_with_no_figure_behind_it_is_still_padding():
    """Both E63 phrasings got past this. "nearly a 2% annual drop" wanted
    the literal word depreciation beside "annual"; "reflecting a subtle
    depreciation over time" carries no number at all, which is worse -- a
    value claim with nothing behind it, in the beat that gave both prices."""
    from single_car_short import DEPRECIATION_PADDING_RE as rule

    for padding in ("That's nearly a 2% annual drop.",
                    "reflecting a subtle depreciation over time",
                    "holding significant value",
                    "appreciating around 8%",
                    "a 2% annual rise"):
        assert rule.search(padding), padding
    # And the things that are not this: a spec, a count, an acceleration
    # figure, or two prices stated plainly.
    for fine in ("627 lb-ft of torque", "only 999 were built",
                 "it drops to sixty in 3.4 seconds",
                 "Originally $110,000, now about $137,000", "up 12% since 2020"):
        assert not rule.search(fine), fine


def test_the_original_price_must_be_the_sticker_not_a_used_asking_price():
    """Every E63 build said "$170,000 new, about $137,000 today" and called
    it depreciation. The car listed near $113,000 and has gone up -- $170,000
    was an asking price off a classified, and having the original wrong
    inverted the whole beat in all three builds."""
    import single_car_short

    for style in ("classic", "dense", "short"):
        prompt = single_car_short._research_script_prompt(
            "Mercedes Amg E63 S", "2023", style=style,
            market={"median": 136500, "source": "sale"})
        assert "manufacturer's list" in prompt, style
        assert "never an asking price" in prompt, style
    # Including the branch where nothing verifies today's value, which is
    # the one case that only ever states an original price.
    assert "manufacturer's list" in single_car_short._research_script_prompt("X", "2023")


# --- the RS6 round of fixes ----------------------------------------------


def _three_scene_package(middle, title="Four Doors, Real Speed"):
    scenes = [{"headline": "", "narration": "591 horsepower in a wagon. That's the RS6 Avant."},
              {"headline": "", "narration": middle},
              {"headline": "", "narration": "So would you daily one, or is a sedan enough?"}]
    return {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes),
            "youtube_title": title}


def test_an_ordinary_redline_is_not_worth_the_words():
    """A 7,200rpm limiter is where ordinary road cars sit -- a diesel SUV is
    near it -- so saying it is a spec dump, not a reason to watch. Six of the
    shipped builds spent words on redlines between 6,500 and 7,200."""
    import single_car_short

    flagged = [v for v in single_car_short._script_violations(
        _three_scene_package("It redlines at 7,200 rpm and pulls hard."), "Audi", "Rs6 Avant")
        if "redline" in v]
    assert flagged and "7,200 rpm" in flagged[0]
    # And the other way round: a genuinely high one is the point of the car.
    for high in ("It redlines at 9,000 rpm, screaming all the way.",
                 "An 8,500 rpm redline is the whole point."):
        assert not [v for v in single_car_short._script_violations(
            _three_scene_package(high), "Audi", "Rs6 Avant") if "redline" in v], high


def test_the_script_never_says_a_figure_was_unavailable():
    """One build said "this engine doesn't have a redline listed, but it
    roars" -- the model narrating a hole in its own research. A viewer does
    not know a number was missing until they are told."""
    import single_car_short

    flagged = [v for v in single_car_short._script_violations(
        _three_scene_package("This engine doesn't have a redline listed, but it roars."),
        "Audi", "Rs6 Avant") if "not available" in v]
    assert flagged


def test_an_emoji_in_the_spoken_words_is_a_leaked_title():
    """The RS6 dense build opened by speaking "A Wagon That Bucks Typical
    Depreciation" and a fire emoji out loud."""
    import single_car_short

    leaked = _three_scene_package("A Wagon That Bucks Depreciation 🔥 It holds on well.")
    assert [v for v in single_car_short._script_violations(leaked, "Audi", "Rs6 Avant")
            if "belongs in the title" in v]


def test_a_title_echoing_its_own_hook_is_not_a_leak():
    """A check on the title's words appearing in scene 1 flagged the M5 CS
    build, whose title is echoed by its hook deliberately -- the opening is
    supposed to pay off the title in the same words. Matching text cannot be
    the signal; a spoken emoji can."""
    import single_car_short

    package = _three_scene_package(
        "BMW built only 1,132 of them.",
        title="The lightest, most powerful M5 ever built. 🔥")
    package["scenes"][0]["narration"] = (
        "With 627 horsepower, it's the lightest, most powerful M5 ever built. "
        "This is the 2022 BMW M5 CS.")
    package["script"] = " ".join(s["narration"] for s in package["scenes"])
    assert not [v for v in single_car_short._script_violations(package, "Bmw", "M5 Cs")
                if "belongs in the title" in v or "opens with the title" in v]


def test_value_claims_without_a_figure_are_caught_in_every_phrasing():
    """Three of the four RS6 builds had one. "holds its value" and "keeps its
    value" use a verb form the rule did not have; "a rare appreciation in
    value" carries no figure at all."""
    from single_car_short import DEPRECIATION_PADDING_RE as rule

    for padding in ("holds its value", "keeps its value", "retains its value",
                    "reflecting a rare appreciation in value",
                    "hasn't really depreciated", "has not depreciated"):
        assert rule.search(padding), padding
    for fine in ("they go for about $130,000 today", "the value of a good one is obvious",
                 "591 horsepower and 590 lb-ft", "up 12% since 2020"):
        assert not rule.search(fine), fine


def test_a_note_is_never_the_opening_line():
    """Both RS6 notes builds opened on "they trade for about $130,000 today"
    because price was note 1, which buried the eighteen-year story that was
    the reason to watch. Priority orders the beats, not the script."""
    import single_car_short

    prompt = single_car_short._research_script_prompt(
        "Audi Rs6 Avant", "2022",
        angles="- About $117,000 new and they trade near that today.\n"
               "- Americans waited eighteen years for this wagon.")
    assert "NONE OF THESE NOTES IS THE OPENING" in prompt
    assert "Note 1 is the most important thing to COVER" in prompt
    # The ranking itself still reaches the model.
    assert "1. About $117,000" in prompt and "2. Americans waited" in prompt


def test_the_reserved_beats_scale_down_for_the_35_second_format():
    """Five protected beats -- hook, history, engine, price, closing -- is
    nearly the whole video when there are only four or five scenes, so a
    note had nowhere to land. The one short build that used notes said
    "only 800 units" in two separate scenes and spent a third on "the bold
    flared arches signify a wider track"."""
    import single_car_short

    short = single_car_short._research_script_prompt(
        "Mercedes Benz S580", "2023", style="short", angles="- one thing\n- another thing")
    assert "REPLACES the history beat or the price beat" in short
    assert "never say the same figure in two scenes" in short
    # The longer formats have room for all five, so they keep them.
    for style in ("classic", "dense"):
        prompt = single_car_short._research_script_prompt(
            "Mercedes Benz S580", "2023", style=style, angles="- one thing\n- another thing")
        assert "the history, the engine figures, the price and the closing question all stay" in prompt
        assert "REPLACES the history beat" not in prompt
    # And no style leaks the placeholder.
    for style in ("classic", "dense", "short"):
        assert "{keeps}" not in single_car_short._research_script_prompt(
            "X", "2023", style=style, angles="- a")


def test_a_build_with_no_notes_is_unaffected_by_the_beat_scaling():
    import single_car_short

    for style in ("classic", "dense", "short"):
        prompt = single_car_short._research_script_prompt("X", "2023", style=style)
        assert "THE PERSON COMMISSIONING THIS VIDEO ASKED FOR THESE" not in prompt
        assert "{keeps}" not in prompt


def test_the_floor_takes_the_sentences_that_fit_instead_of_dropping_them_all(monkeypatch):
    """Asked for 16 words the model offers 25, and discarding the whole
    offering put the classic S580 build three rounds deep and still six
    words short of its floor: "overshot the cap" twice, nothing kept."""
    import single_car_short

    package = _floor_package(14)      # 140 words
    # One short sentence that fits the 10 words of room, then a long one
    # that cannot. The old code threw both away.
    monkeypatch.setattr(single_car_short, "_more_sentences_for", lambda *a, **k: [
        (3, "Alpha beta gamma delta epsilon zeta eta theta iota kappa. "
            + " ".join(f"over{j}" for j in range(40)) + ".")])
    monkeypatch.setattr(single_car_short, "_research_extra_beat", lambda *a, **k: "")
    monkeypatch.setattr(single_car_short, "_script_violations", lambda *a, **k: [])

    out = single_car_short._extend_to_floor(
        package, floor=150, cap=150, label="X", make="X", model="X")
    assert out["word_count"] == 150, "the sentence that fits is kept, the one that does not is not"
    assert "Alpha beta gamma" in out["script"]
    assert "over0" not in out["script"]


def test_a_round_whose_every_sentence_is_too_long_is_still_reported(monkeypatch):
    import single_car_short

    package = _floor_package(14)
    monkeypatch.setattr(single_car_short, "_more_sentences_for", lambda *a, **k: [
        (3, " ".join(f"big{j}" for j in range(40)) + ".")])
    monkeypatch.setattr(single_car_short, "_research_extra_beat", lambda *a, **k: "")
    monkeypatch.setattr(single_car_short, "_script_violations", lambda *a, **k: [])
    out = single_car_short._extend_to_floor(
        package, floor=150, cap=145, label="X", make="X", model="X")
    assert out["word_count"] == 140, "nothing fit, so nothing changed"


def test_a_price_written_with_a_k_suffix_is_checked_too():
    """The dense S580 build said "$148K" and the classic one said "$150,000"
    for the same car. The pattern wanted comma-grouped digits or four-plus of
    them, so the shorthand went straight past it."""
    import single_car_short

    assert single_car_short._round_price_text("a price tag of $148K") == "a price tag of $150,000"
    assert single_car_short._round_price_text("about $120K new") == "about $120K new", \
        "already round, so left as written"
    for text, value in (("$148K", 148_000), ("$1.2M", 1_200_000), ("$3.5K", 3_500)):
        match = single_car_short._EXACT_PRICE_RE.search(text)
        assert single_car_short._price_value(match) == value, text

    scenes = [{"headline": "", "narration": "496 horsepower in a limousine. That's the S580."},
              {"headline": "", "narration": "With a price tag of $148K, it sets the benchmark."},
              {"headline": "", "narration": "So would you have one?"}]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes)}
    flagged = [v for v in single_car_short._script_violations(package, "Mercedes Benz", "S580")
               if "exact price" in v]
    assert flagged and "about $150,000" in flagged[0]


def test_a_rival_note_cannot_take_the_opening_of_a_35_second_video():
    """The S580 short build spent its hook on Audi's order books and came
    out titled "Audi Says Goodbye to A8s in 2026" -- a video about a
    Mercedes, named after an Audi. Context needs setting up before it means
    anything, and at this length there are no words for the setup."""
    import single_car_short

    short = single_car_short._research_script_prompt(
        "Mercedes Benz S580", "2023", style="short",
        angles="- Audi stopped taking A8 orders\n- the suspension lifts the car")
    assert "about a DIFFERENT car or brand" in short
    assert "never make it the opening" in short
    # The longer formats have room to set a rival up, so they are left alone.
    for style in ("classic", "dense"):
        assert "about a DIFFERENT car or brand" not in single_car_short._research_script_prompt(
            "Mercedes Benz S580", "2023", style=style, angles="- Audi stopped taking A8 orders")


def test_a_broken_hook_outweighs_a_repeated_figure():
    """The R8 shipped with its title promising one thing and its opening
    delivering another, because the repair that fixed it introduced a
    smaller rule and the violation COUNT did not drop. Counted, a hook and
    a repeated number are worth the same; they are not."""
    import single_car_short

    hook = ('your title and your first sentence are about different things -- the title is '
            '"A V10 powerhouse" and the video opens "The hexagonal grille boosts aerodynamics."')
    polish = 'you state the same number more than once -- "602" 2 times. Say each figure once'
    unknown = "some rule nobody has classified yet"

    assert single_car_short._violation_weight(hook) == single_car_short.HOOK_WEIGHT
    assert single_car_short._violation_weight(polish) == single_car_short.POLISH_WEIGHT
    # Unclassified sits in the middle: it cannot outrank the hook, and it is
    # not waved through as cosmetic either.
    assert single_car_short._violation_weight(unknown) == single_car_short.CONTENT_WEIGHT
    assert (single_car_short.POLISH_WEIGHT
            < single_car_short.CONTENT_WEIGHT
            < single_car_short.HOOK_WEIGHT)

    # The exact trade the R8 refused: fix the hook, gain a repeated figure.
    before = [hook, "you state the horsepower, but not until after scene 3."]
    after = ["you state the horsepower, but not until after scene 3.", polish]
    assert len(after) >= len(before), "counted, this trade looks like a wash"
    assert single_car_short._violation_cost(after) < single_car_short._violation_cost(before), (
        "weighed, fixing the hook is worth gaining a repeated figure")


def test_a_title_rule_is_repaired_by_retitling_not_by_rewriting_a_scene(monkeypatch):
    """Two of the R8's three shipped rules were about its title, and both
    were handed to the scene rewriter -- asked to change a line of narration
    to fix text that is not narration. One came back "did not help", the
    other "traded for another"."""
    import single_car_short

    package = {
        "youtube_title": "The Audi R8 Is A V10 Powerhouse",
        "scenes": [{"headline": "", "narration": "Six hundred and two horsepower, no turbos."},
                   {"headline": "", "narration": "This is the Audi R8 V10 Performance."},
                   {"headline": "", "narration": "So would you take it over a Huracan?"}],
    }
    package["script"] = " ".join(s["narration"] for s in package["scenes"])

    violations = single_car_short._script_violations(package, "Audi", "R8")
    titled = [v for v in violations if v.startswith(single_car_short.TITLE_VIOLATION_MARKER)]
    assert titled, "the title names the car, which is the rule under test"

    asked = {}

    def fake_rewrite(violation, title, opening, label):
        asked.update(violation=violation, title=title, opening=opening)
        return "Six Hundred Horsepower And Not One Turbo"

    monkeypatch.setattr(single_car_short, "_rewrite_title_for", fake_rewrite)
    repaired = single_car_short._repair_title(
        package, titled[0], "Audi", "R8", None, "2020 Audi R8")

    # It was given the title and the opening sentence, not the scene list.
    assert asked["title"] == package["youtube_title"]
    assert asked["opening"].startswith("Six hundred and two horsepower")
    # The title moved and the narration did not.
    assert repaired["youtube_title"] == "Six Hundred Horsepower And Not One Turbo"
    assert [s["narration"] for s in repaired["scenes"]] == \
           [s["narration"] for s in package["scenes"]]
    assert titled[0] not in single_car_short._script_violations(repaired, "Audi", "R8")


def test_a_new_title_that_does_not_help_is_thrown_away(monkeypatch):
    """Same bargain as a scene repair: better or not at all."""
    import single_car_short

    package = {
        "youtube_title": "The Audi R8 Is A V10 Powerhouse",
        "scenes": [{"headline": "", "narration": "Six hundred and two horsepower, no turbos."},
                   {"headline": "", "narration": "This is the Audi R8 V10 Performance."},
                   {"headline": "", "narration": "So would you take it over a Huracan?"}],
    }
    package["script"] = " ".join(s["narration"] for s in package["scenes"])
    titled = [v for v in single_car_short._script_violations(package, "Audi", "R8")
              if v.startswith(single_car_short.TITLE_VIOLATION_MARKER)]

    # Still names the car, so the rule it targeted survives.
    monkeypatch.setattr(single_car_short, "_rewrite_title_for",
                        lambda *a, **k: "The Audi R8 Is Still Named Here")
    assert single_car_short._repair_title(
        package, titled[0], "Audi", "R8", None, "2020 Audi R8"
    )["youtube_title"] == package["youtube_title"]

    # A call that blows up leaves the build alone rather than killing it.
    def boom(*a, **k):
        raise RuntimeError("the model is down")

    monkeypatch.setattr(single_car_short, "_rewrite_title_for", boom)
    assert single_car_short._repair_title(
        package, titled[0], "Audi", "R8", None, "2020 Audi R8"
    )["youtube_title"] == package["youtube_title"]


def test_a_note_the_script_passes_over_is_not_a_violation():
    """Notes are suggestions, not a brief to discharge. A script that leaves
    one out is doing what it was told, so nothing may flag it -- a marque
    named in the notes and absent from the script used to be a violation,
    which re-imposed in the checker the requirement the prompt lifts."""
    import single_car_short

    scenes = [{"headline": "", "narration": "1,064 horsepower and 828 lb-ft. That's the ZR1."},
              {"headline": "", "narration": "It out-accelerates almost anything on the road."},
              {"headline": "", "narration": "So would you take one?"}]
    package = {"scenes": scenes, "script": " ".join(s["narration"] for s in scenes),
               "_angles": "- more power than a Ferrari SF90 or a Lamborghini Revuelto"}
    violations = single_car_short._script_violations(package, "Chevrolet", "Corvette Zr1")
    assert not [v for v in violations if "note" in v.lower()]

    # Taking the note and softening it is still wrong, but that is the
    # model's judgement to make against everything else competing for the
    # beat -- so the instruction lives in the prompt, not in a rule.
    block = single_car_short._angles_block(package["_angles"])
    assert "name it in full" in block
    assert "hypercars costing three times as much" in block


def test_the_notes_are_offered_rather_than_required():
    """Every note was mandatory, which is how six notes split out of one
    pasted paragraph took four beats and left the rarity and the comparison
    unsaid. Ignoring all of them has to be a legitimate outcome."""
    import single_car_short

    block = single_car_short._angles_block("- talk about the Le Mans anniversary")
    assert "offered, not required" in block
    assert "valid outcome rather than a failed one" in block
    assert "not a brief to discharge" in block


def test_the_no_comparison_checkbox_switches_off_the_segment_not_the_words():
    """The checkbox turns off a visual -- the rival photo, the horsepower
    bars and the drag race -- all of which are gated purely on the rival_*
    and main_horsepower fields being set. It was also forbidding the
    narration from naming any competitor, which it never needed to do, and
    that cost the ZR1 builds their best line: "more power than a Ferrari
    SF90 or a Lamborghini Revuelto, at a third of the price"."""
    import single_car_short

    for angles in ("- more power than a Ferrari SF90 or a Lamborghini Revuelto", None):
        prompt = single_car_short._research_script_prompt(
            "Chevrolet Corvette Zr1", "2026", disable_comparison=True, angles=angles)
        # The fields are what make the segment, so they are what is switched off.
        assert "turned off the rival-comparison SEGMENT" in prompt
        assert "Leave every one of those\nnull, on every scene, even if the narration does name " \
               "another car" in prompt
        # The words are explicitly not switched off.
        assert "What is NOT switched off is the words" in prompt
        # And the old blanket gag is gone for good.
        assert "do NOT name any specific competitor car anywhere" not in prompt
        # What stays banned is the duel itself, which is what the segment was.
        assert "no scene\nbuilt as a spec-for-spec duel" in prompt

    # With notes, their wording is protected from being softened.
    with_notes = single_car_short._research_script_prompt(
        "Chevrolet Corvette Zr1", "2026", disable_comparison=True,
        angles="- more power than a Ferrari SF90")
    assert "supercars costing three times as much" in with_notes, \
        "the exact softening that happened is named as the thing not to do"


def test_the_drag_race_only_ever_depended_on_the_horsepower_fields():
    """Why the rewrite above is safe: narrator_video builds a race window
    only where both horsepower fields are non-null, so leaving them null is
    sufficient to suppress the segment no matter what the words say."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/narrator_video.py").read_text()
    assert ('if scene.get("main_horsepower") is None or scene.get("rival_horsepower") is None:'
            in source), "the race gate moved; the no-comparison wording depends on it"


def test_a_researched_beat_is_also_trimmed_to_the_room_left(monkeypatch):
    """One path applied its own addition and skipped the trim, so a
    researched beat was still discarded whole for overshooting the cap --
    which is how the dense ZR1 build shipped at 140 against a 155 floor
    after three rounds."""
    import single_car_short

    package = _floor_package(14)      # 140 words
    monkeypatch.setattr(single_car_short, "_more_sentences_for", lambda *a, **k: [(3, "Repeat.")])
    monkeypatch.setattr(single_car_short, "_research_extra_beat", lambda *a, **k: (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa. "
        + " ".join(f"over{j}" for j in range(40)) + "."))
    # First round's spec sentence is a repeat, which pushes it to research.
    monkeypatch.setattr(single_car_short, "_script_violations",
                        lambda pkg, *a, **k: ["repeats a figure"]
                        if "Repeat." in (pkg.get("script") or "") else [])
    out = single_car_short._extend_to_floor(
        package, floor=150, cap=150, label="X", make="X", model="X")
    assert out["word_count"] == 150, "the researched sentence that fits is kept"
    assert "Alpha beta gamma" in out["script"] and "over0" not in out["script"]


def test_the_45_second_format_sits_between_the_short_one_and_the_classic():
    """The 35-second cut kept the same watch time as a 56-second one, but
    three of its five beats go to notes and nothing is left for a history
    fact or the one feature worth seeing. This buys back a beat and stays
    under a minute."""
    import single_car_short

    medium = single_car_short.SCRIPT_STYLES["medium"]
    short = single_car_short.SCRIPT_STYLES["short"]
    classic = single_car_short.SCRIPT_STYLES["classic"]
    assert short["words"][1] < medium["words"][0] < medium["words"][1] < classic["words"][0]
    assert short["seconds"] < medium["seconds"] < classic["seconds"]
    # The whole range has to land inside its window or the voice gets squeezed.
    low, high = medium["words"]
    assert medium["window"][0] <= low / 2.5 and high / 2.5 <= medium["window"][1]

    prompt = single_car_short._research_script_prompt(
        "Chevrolet Corvette Zr1", "2026", style="medium")
    assert "exactly 110-125 words" in prompt
    assert "5-6 scenes" in prompt
    # One arithmetic, stated the same way as the other styles.
    assert "TWELVE OR THIRTEEN sentences across the whole script" in prompt
    assert "about 12-13 sentences" in prompt
    # And the rules that apply everywhere still reach it.
    for shared in ("Round every price", "Mention the redline ONLY", "manufacturer's list"):
        assert shared in prompt, shared


def test_the_last_caption_holds_until_the_video_ends():
    """The clip runs about half a second past the final word so the closing
    question has room to land, and captions are timed off the speech -- so
    the caption band sat empty over those last frames while the headline,
    the photo and the narrator all stayed put. Watched back it reads as the
    last word flickering out."""
    import narrator_video

    manifest = {"script": "one two three", "word_timeline": [
        {"word": "one", "start": 0.0, "end": 0.4},
        {"word": "two", "start": 0.4, "end": 0.8},
        {"word": "three", "start": 0.8, "end": 1.2},
    ]}
    captions = narrator_video._caption_timeline(manifest, 1.65)
    assert captions[-1] == ("three", 0.8, 1.65), "the last word holds to the end"
    # The ones before it are untouched.
    assert captions[0] == ("one", 0.0, 0.4)
    assert captions[1] == ("two", 0.4, 0.8)
    # And nothing is stretched backwards when the audio already fills the clip.
    assert narrator_video._caption_timeline(manifest, 1.2)[-1] == ("three", 0.8, 1.2)


def test_the_spec_table_rounds_its_price_like_the_narration_does():
    """The short ZR1 build's table read "$224K new" while the classic one
    read "About $220,000" for the same car -- the build disagreeing with
    itself in two places a viewer can see at once."""
    import single_car_short

    assert single_car_short._round_price_text("$224K new") == "$220,000 new"
    assert single_car_short._round_price_text("About $220,000") == "About $220,000"


def test_an_unticked_closeup_is_shown_but_never_described(monkeypatch, tmp_path):
    """A close-up reaches the screen either way -- 452 of 455 pasted photos
    across 27 builds did. The tick decides only whether the script is told
    what is in it, and a hint is a demand for a beat: nineteen photos on
    the Ford GT build took three of its seven beats for a rear wing, a
    gearshift and an air intake, and why Ford built the car went unsaid."""
    import single_car_short

    described = []
    monkeypatch.setattr(single_car_short, "_download_car_photo", lambda url, d, n: tmp_path / "x.jpg")
    monkeypatch.setattr(single_car_short, "_describe_photo_for_script",
                        lambda path, label, car: described.append(label) or f"a photo of {label}")

    hints = single_car_short.gather_photo_script_hints(
        {"front": "https://x/f.jpg"},
        [{"url": "https://x/1.jpg", "label": "exhaust", "slot": "rear", "describe": True},
         {"url": "https://x/2.jpg", "label": "wheel", "slot": "side", "describe": False},
         {"url": "https://x/3.jpg", "label": "badge", "slot": "front"}],
        tmp_path, "Ford GT")

    # The main photo is always described; only the ticked close-up joins it.
    assert described == ["exterior front", "exhaust"]
    assert len(hints) == 2
    # And an unticked one costs nothing: no hint means no vision call.
    assert "wheel" not in described and "badge" not in described


def test_the_prompt_says_how_many_beats_there_are_when_more_is_asked_for():
    """A photo hint and a note are both requests for a beat, and nothing
    ever told the model how many it had. Nineteen hints and five notes
    against seven beats is arithmetic, not a judgement call."""
    import single_car_short

    notes = "\n".join(f"- note {i}" for i in range(1, 6))
    hints = [f'CLOSE-UP -- photo_label: "p{i}" -- a thing' for i in range(14)]
    prompt = single_car_short._research_script_prompt(
        "Ford Gt", "2005", style="classic", photo_hints=hints, angles=notes)
    assert "MORE HAS BEEN ASKED FOR THAN FITS" in prompt
    assert "19 things for 4 beats" in prompt
    # Notes outrank photos, because a photo is on screen either way.
    assert "The notes, in the order they are numbered" in prompt
    assert "A photo you leave out still appears in the" in prompt

    # Inside the budget it says nothing -- the arithmetic only matters when
    # it does not work out.
    fits = single_car_short._research_script_prompt(
        "Ford Gt", "2005", style="classic", photo_hints=hints[:1], angles="- one note")
    assert "MORE HAS BEEN ASKED FOR" not in fits

    # A shorter format has fewer beats, so it runs out sooner.
    assert "MORE HAS BEEN ASKED FOR" in single_car_short._research_script_prompt(
        "X", "2005", style="short", photo_hints=hints[:2], angles="- a\n- b")


def test_every_style_declares_how_many_beats_it_has():
    import single_car_short

    expected = {"classic": 7, "dense": 7, "medium": 5, "short": 4}
    for style, beats in expected.items():
        assert single_car_short.SCRIPT_STYLES[style]["beats"] == beats, style
        # The budget has to leave room for something after the three
        # required beats, or every build would be over before it started.
        assert beats > single_car_short.RESERVED_BEATS, style


def test_truncation_is_measured_by_alignment_not_by_counting_words():
    """Counting words does not work. Whisper splits hyphenated words and
    numbers, so it usually returns MORE entries than the script has: across
    49 builds the ratio ran 0.95 to 1.12 with no relation to whether
    anything was missing, and the old "under 95% of the words" rule caught
    none of the real cases."""
    import single_car_short

    script = ("So is a track beast without hybrid gimmicks your style, "
              "or would you opt for electric assists?")
    def timeline(text):
        return [{"word": w} for w in text.split()]

    # The Viper ACR: stopped after "your style", ratio 1.00, seven words
    # of closing question never spoken.
    cut = single_car_short.unspoken_tail(
        script, timeline("So is a track beast without hybrid gimmicks your style"))
    assert cut > single_car_short.TRUNCATION_CHARS
    assert cut == 31

    # The same script read to the end is clean.
    assert single_car_short.unspoken_tail(script, timeline(script)) == 0


def test_a_different_hearing_of_the_same_ending_is_not_truncation():
    """"send-off" heard as "send off", "LC 500" as "LC500", "Mazdaspeed3"
    as "Mazda Speed 3" -- a naive tail comparison called all of those cut
    off, and none of them were."""
    import single_car_short

    for written, heard in (
        ("So is it a worthy send-off?", "So is it a worthy send off"),
        ("inside the LC 500 Inspiration Series?", "inside the LC500 Inspiration Series"),
        ("would you drive the Mazdaspeed3 Sport?", "would you drive the Mazda Speed 3 Sport"),
        ("supercharged heritage or modern-day speed?", "supercharged heritage or modern day speed"),
    ):
        unspoken = single_car_short.unspoken_tail(
            written, [{"word": w} for w in heard.split()])
        assert unspoken <= single_car_short.TRUNCATION_CHARS, (written, unspoken)


def test_nothing_to_compare_is_not_reported_as_truncation():
    import single_car_short

    assert single_car_short.unspoken_tail("", [{"word": "a"}]) == 0
    assert single_car_short.unspoken_tail("some words here", []) == 0
    assert single_car_short.unspoken_tail("some words here", None) == 0


def test_a_cut_off_narration_is_read_again_and_then_recorded():
    """gpt-audio cutting off is not deterministic, so a retake is worth one
    call where re-researching the script would not be -- and if the second
    take is short too, the build says so instead of shipping a closing
    question nobody hears."""
    from pathlib import Path

    import single_car_short

    body = (Path(__file__).resolve().parents[1]
            / "cars/automation/single_car_short.py").read_text()
    # Three reads before a short one ships: the cut-off is non-deterministic
    # and independent, so at one in ten a single retake still leaves about
    # one build in a hundred short.
    assert single_car_short.MAX_NARRATION_TAKES == 3
    assert "for take in range(2, MAX_NARRATION_TAKES + 1):" in body
    assert "synthesize_narration(package[\"script\"], audio_path" in body
    # Each take re-measures rather than assuming it worked.
    assert body.count("unspoken = unspoken_tail(package[\"script\"], word_timeline)") >= 2
    # A failed take stops the loop rather than burning the rest on the same error.
    assert "keeping the one before it" in body
    assert 'package["narration_unspoken_chars"] = unspoken' in body
    # And the old word-count rule is gone.
    assert "spoken < written * 0.95" not in body


def _timeline(words, per=0.5):
    return [{"word": w, "start": i * per, "end": i * per + per * 0.8}
            for i, w in enumerate(words)]


def test_the_start_up_cuts_in_where_the_script_hands_over_to_it():
    """The script is told scene 2 ends by handing over to the sound, so the
    clip belongs at the end of scene 2 -- in the gap after a finished
    sentence, not inside a word, and before the twenty seconds the average
    viewer actually watches."""
    import single_car_short

    scenes = [{"narration": "One two three four"},
              {"narration": "Five six seven eight"},
              {"narration": "Nine ten eleven twelve"},
              {"narration": "So would you have one?"}]
    words = " ".join(s["narration"] for s in scenes).split()
    manifest = {"scenes": scenes, "word_timeline": _timeline(words),
                "duration_seconds": len(words) * 0.5}

    at = single_car_short.engine_clip_seam(manifest)
    assert at is not None
    # End of scene 2 -- after eight words at half a second each.
    assert 3.5 <= at <= 4.5, at
    assert at < single_car_short.ENGINE_CLIP_LATEST_SECONDS


def test_no_seam_rather_than_one_nobody_is_still_watching():
    """A script that runs long pushes scene 2 past the window the clip
    exists to land inside. Better no clip than one that plays to people who
    left ten seconds earlier."""
    import single_car_short

    scenes = [{"narration": " ".join(["word"] * 30)},
              {"narration": " ".join(["word"] * 30)},
              {"narration": "and then some more"},
              {"narration": "So would you have one?"}]
    words = " ".join(s["narration"] for s in scenes).split()
    manifest = {"scenes": scenes, "word_timeline": _timeline(words),
                "duration_seconds": len(words) * 0.5}
    assert single_car_short.engine_clip_seam(manifest) is None


def test_a_build_with_nothing_to_measure_gets_no_seam():
    """Word alignment is allowed to fail -- the build falls back to
    estimated caption timing. Cutting a film on a guess is worse than not
    cutting it."""
    import single_car_short

    scenes = [{"narration": "One two"}, {"narration": "Three four"},
              {"narration": "Five six"}]
    assert single_car_short.engine_clip_seam(
        {"scenes": scenes, "word_timeline": [], "duration_seconds": 30}) is None
    assert single_car_short.engine_clip_seam(
        {"scenes": [], "word_timeline": _timeline(["a", "b"]), "duration_seconds": 30}) is None
    # Fewer scenes than the seam sits after: nothing follows the clip.
    assert single_car_short.engine_clip_seam(
        {"scenes": scenes[:2], "word_timeline": _timeline(["a", "b", "c", "d"]),
         "duration_seconds": 2}) is None


def test_the_script_is_told_to_hand_over_and_not_to_react():
    """It introduces the sound and then stops talking. Scene 3 must not
    open by reacting to a noise the narrator did not hear with the
    viewer."""
    import single_car_short

    block = single_car_short._engine_clip_block()
    assert "ENDS BY HANDING OVER TO IT" in block
    assert "Do not say the word \"clip\" or \"video\"" in block
    assert "incredible, right?" in block, "names the reaction it is forbidding"

    with_clip = single_car_short._research_script_prompt("a car", "2020", engine_clip=True)
    without = single_car_short._research_script_prompt("a car", "2020")
    assert "STARTING UP PLAYS AFTER SCENE" in with_clip
    assert "STARTING UP PLAYS AFTER SCENE" not in without, (
        "a build with no clip must not be told to hand over to one")


def test_the_gap_moves_every_word_after_it_and_leaves_the_rest(monkeypatch, tmp_path):
    """The silence is cut into the narration before the render, so the words
    on the far side of it are spoken later than they were transcribed. Not
    shifting them would leave every caption, scene boundary and mouth shape
    after the seam reading off a timeline the audio no longer matches."""
    import single_car_short

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        Path(str(args[-1])).write_bytes(b"gapped")
        return type("Done", (), {"stdout": "", "stderr": ""})()

    monkeypatch.setattr(single_car_short.subprocess, "run", fake_run)
    words = [{"word": "a", "start": 1.0, "end": 1.4},
             {"word": "b", "start": 3.9, "end": 4.0},
             {"word": "c", "start": 4.2, "end": 4.6},
             {"word": "d", "start": 9.0, "end": 9.5}]
    shifted = single_car_short.open_engine_gap(audio, words, 4.1, 5.0)

    assert [w["start"] for w in shifted] == [1.0, 3.9, 9.2, 14.0]
    assert [w["end"] for w in shifted] == [1.4, 4.0, 9.6, 14.5]
    assert [w["word"] for w in shifted] == ["a", "b", "c", "d"]
    assert words[2]["start"] == 4.2, "the timeline it was handed is not mutated"

    filters = str(calls[0][calls[0].index("-filter_complex") + 1])
    assert "anullsrc" in filters and "concat=n=3" in filters
    # Both the narration and the silence pinned to one format. The concat
    # negotiates between its inputs, and a mono 24kHz silence winning that
    # negotiation would downsample the whole narration.
    assert filters.count("aformat=sample_rates=48000:channel_layouts=stereo") == 1
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in filters


def test_a_failed_gap_leaves_the_narration_alone():
    """Opening the gap is the one step that cannot be undone later: the film
    is rendered around it. If it fails, the build has to be the build it
    would have been without a clip, not one with five silent seconds in it."""
    import single_car_short

    assert single_car_short.open_engine_gap(
        "/nonexistent/narration.mp3", [{"word": "a", "start": 0, "end": 1}],
        4.0, 5.0) is None


def test_a_clip_that_cannot_be_read_is_found_out_before_the_gap_is_opened():
    """The fetch runs first precisely so an unreadable clip costs nothing.
    Anything that is not a real url and a real span is refused without
    ffmpeg being asked."""
    import single_car_short

    for clip in ({}, {"url": "", "start": 0, "end": 5},
                 {"url": "http://x/v.m3u8", "start": 5, "end": 5},
                 {"url": "http://x/v.m3u8", "start": 9, "end": 4},
                 {"url": "http://x/v.m3u8", "start": "", "end": 5}):
        assert single_car_short.fetch_engine_clip(clip, "/tmp/never.mp4") is None, clip


def test_the_clip_is_cut_to_what_the_video_actually_has(monkeypatch, tmp_path):
    """Asking for 12s-17s of a video that ends at 14 gets two seconds. The
    gap has to match what there is to play, not what was typed, or the film
    sits silent over a still photo for the difference."""
    import single_car_short

    dest = tmp_path / "engine_clip.mp4"

    def fake_run(args, **kwargs):
        dest.write_bytes(b"clip")
        return type("Done", (), {"stdout": "", "stderr": ""})()

    monkeypatch.setattr(single_car_short.subprocess, "run", fake_run)
    monkeypatch.setattr(single_car_short, "_media_duration", lambda path: 2.0)
    monkeypatch.setattr(single_car_short, "_has_audio", lambda path: True)
    path, length = single_car_short.fetch_engine_clip(
        {"url": "http://x/v.m3u8", "start": 12, "end": 17}, dest)
    assert path == dest
    assert length == 2.0

    # And a probe that cannot answer is not treated as a short clip.
    monkeypatch.setattr(single_car_short, "_media_duration", lambda path: None)
    assert single_car_short.fetch_engine_clip(
        {"url": "http://x/v.m3u8", "start": 12, "end": 17}, dest)[1] == 5.0


def test_the_clip_plays_in_the_photo_box_not_over_the_whole_frame(monkeypatch, tmp_path):
    """The layout exists to keep the headline, the spec table and the
    narrator on screen. Cutting to the clip full frame hid all three, so it
    is overlaid into the same inset box the renderer fits photos into."""
    import single_car_short
    from narrator_video import _media_zone_geometry, CANVAS

    video = tmp_path / "single_car_short.mp4"
    video.write_bytes(b"film")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        Path(str(args[-1])).write_bytes(b"joined")
        return type("Done", (), {"stdout": "", "stderr": ""})()

    monkeypatch.setattr(single_car_short.subprocess, "run", fake_run)
    monkeypatch.setattr(single_car_short.Path, "replace",
                        lambda self, other: None, raising=False)
    assert single_car_short.overlay_engine_clip(video, tmp_path / "clip.mp4",
                                                at=11.5, length=5.0)

    args = calls[0]
    filters = str(args[args.index("-filter_complex") + 1])
    _, (box_x, box_y, box_w, box_h), _ = _media_zone_geometry(CANVAS)
    assert f"overlay=x={int(round(box_x))}:y={int(round(box_y))}" in filters
    assert f"scale={int(round(box_w))}:{int(round(box_h))}" in filters
    # Nothing is concatenated: the film's own length and timing are the
    # render's, because the quiet was already made in the narration.
    assert "concat" not in filters
    assert "enable='between(t,11.500,16.500)'" in filters
    # The box is filled by a blurred copy of the clip rather than bars, and
    # the fitted copy is never cropped.
    assert "gblur" in filters
    assert "force_original_aspect_ratio=decrease" in filters
    # The clip is held off until the seam, and the film outruns it.
    assert "tpad=start_duration=11.500" in filters
    assert "eof_action=pass" in filters
    # Summed, not averaged -- normalize=1 would duck the narration for the
    # whole film to make room for five seconds of engine.
    assert "normalize=0" in filters
    assert "adelay=11500|11500" in filters
    # The sound is trimmed to the gap as well. The picture is bounded by
    # `enable`, but a seek into an HLS manifest does not land exactly, so a
    # clip that comes back long would play its overrun on top of the
    # narration starting again.
    assert "atrim=0:5.000" in filters
    assert f"loudnorm=I={single_car_short.ENGINE_CLIP_LUFS}" in filters
    # Both rates pinned on the output: a drifting timebase took a 24fps film
    # to 25, and loudnorm's internal 192kHz shipped as 96kHz AAC.
    assert args[args.index("-r") + 1] == "24"
    assert args[args.index("-ar") + 1] == "48000"


def test_a_failed_overlay_ships_the_film_as_it_was_rendered():
    """A hook is worth less than the build."""
    import single_car_short

    assert single_car_short.overlay_engine_clip(
        "/nonexistent/film.mp4", "/nonexistent/clip.mp4", at=11.5, length=5.0) is False


def test_the_build_makes_the_room_before_it_renders_and_fills_it_after(tmp_path):
    """The order is the whole design. The clip plays inside the photo box,
    so the film has to be running underneath it -- which means the quiet has
    to be in the narration before the render, not cut into the finished mp4.
    And the clip is fetched before the gap is opened, because a gap cannot
    be taken back once the film is built around it."""
    import inspect
    import single_car_short

    body = inspect.getsource(single_car_short.build_short)
    fetch = body.index("fetch_engine_clip(")
    gap = body.index("open_engine_gap(")
    mouth = body.index("build_mouth_timeline(")
    render = body.index("render_narrator_video(")
    overlay = body.index("overlay_engine_clip(")
    assert fetch < gap < mouth < render < overlay, (
        "fetch, then the gap, then the mouth timeline reads the gapped file, "
        "then the render, then the overlay")
    # The seam is measured on the timeline before the shift -- recomputing it
    # afterwards would find the midpoint of the gap it just opened.
    assert body.index("engine_clip_seam(") < gap
    assert "engine_clip_seam" not in body[render:]


def test_a_rebuild_carries_the_start_up_clip_with_the_rest_of_the_form():
    """Everything else came back from "Start from a previous build" -- the
    car, the notes, every photo url -- and the clip did not. The box arrived
    unticked and the times blank, so pressing Build on a restored build
    quietly produced the same Short without its engine."""
    import inspect
    import single_car_short

    # The pipeline records it, in the same snapshot the dropdown reads.
    body = inspect.getsource(single_car_short.build_short)
    inputs = body.split('"build_inputs": {')[1].split("\n        },")[0]
    assert '"engine_clip": engine_clip or None' in inputs

    app = (Path(single_car_short.__file__).resolve().parents[2]
           / "web/src/App.jsx").read_text()
    # The dashboard writes it before dispatch too, so a run that never
    # finished refills exactly like one that did.
    assert "engine_clip: useEngineClip && chosenVideo" in app
    # And reads all four pieces of it back onto the form.
    assert "const clip = inputs.engine_clip;" in app
    assert "setUseEngineClip(carried);" in app
    assert "setClipStart(carried ? clipStamp(clip.start)" in app
    assert "setClipEnd(carried ? clipStamp(clip.end)" in app
    assert "playback_url: clip.url" in app
    # The probe no longer throws a carried-over choice away before it has
    # anything to replace it with.
    assert "setChosenVideo(null);\n    const carried" not in app
    assert "reconcileChosenVideo(" in app
    # The picker highlights by video, not by position in the list: a
    # restored clip has no index to match on.
    assert "chosenVideo?.index === video.index" not in app
    assert "sameListingVideo(chosenVideo?.playback_url" in app


def test_the_form_does_not_still_promise_a_clip_at_the_front():
    """It played at the front when it was audio only. It now interrupts the
    script where the narrator hands over, inside the photo frame -- and the
    help text is the only place anybody finds that out."""
    import single_car_short

    app = (Path(single_car_short.__file__).resolve().parents[2]
           / "web/src/App.jsx").read_text()
    assert "plays it at the front, before the narration starts" not in app
    assert "played before the narration" not in app
    assert "inside the photo frame" in app
    assert "ten to fifteen seconds in" in app
    # And that the pause is why length no longer costs words.
    assert "The narration pauses for it " in app


def test_a_silent_video_is_caught_at_the_fetch_not_by_the_overlay(monkeypatch, tmp_path):
    """The first real build picked a listing video with no audio track. The
    fetch said nothing, the gap was cut into the narration, the film was
    rendered around it, and only then did the overlay fail on "[1:a] matches
    no streams" -- so five seconds of nothing shipped. The whole point of
    the clip is the sound, so no sound has to mean no clip, decided before
    anything is committed to."""
    import single_car_short

    dest = tmp_path / "engine_clip.mp4"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        dest.write_bytes(b"clip")
        return type("Done", (), {"stdout": "", "stderr": ""})()

    monkeypatch.setattr(single_car_short.subprocess, "run", fake_run)
    monkeypatch.setattr(single_car_short, "_media_duration", lambda path: 5.0)
    monkeypatch.setattr(single_car_short, "_has_audio", lambda path: False)

    assert single_car_short.fetch_engine_clip(
        {"url": "http://x/v.m3u8", "start": 8, "end": 13}, dest) is None
    # Asked twice, the second time seeking on the output. A master playlist
    # keeps its audio in a separate rendition and a seek taken at open time
    # can leave it unopened, which reads as a video with no sound at all.
    assert len(calls) == 2, "it tries the other way of asking before giving up"
    first, second = calls
    assert first.index("-ss") < first.index("-i"), "cheap seek first"
    assert second.index("-i") < second.index("-ss"), "then seek on the output"

    # And a clip that does have sound is taken on the first pass.
    calls.clear()
    monkeypatch.setattr(single_car_short, "_has_audio", lambda path: True)
    assert single_car_short.fetch_engine_clip(
        {"url": "http://x/v.m3u8", "start": 8, "end": 13}, dest)[1] == 5.0
    assert len(calls) == 1


def test_an_unanswerable_audio_probe_is_not_read_as_silence(monkeypatch, tmp_path):
    """ffprobe failing says nothing about the file. Treating that as silence
    would drop clips for an unrelated reason, and the overlay fails cleanly
    on a file with no audio anyway -- after which the gap gets closed."""
    import single_car_short

    def fake_run(args, **kwargs):
        raise OSError("no ffprobe here")

    monkeypatch.setattr(single_car_short.subprocess, "run", fake_run)
    assert single_car_short._has_audio(tmp_path / "clip.mp4") is True


def test_the_gap_is_taken_back_out_when_the_clip_does_not_arrive(monkeypatch, tmp_path):
    """The room has to be made before the render, so anything that goes
    wrong afterwards leaves the film holding five seconds of nothing -- no
    voice, no picture, no engine. That is what shipped the first time. The
    gap is reversible so a build without a clip is a build without a clip,
    not a build with a hole in it."""
    import single_car_short

    video = tmp_path / "single_car_short.mp4"
    video.write_bytes(b"film")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        Path(str(args[-1])).write_bytes(b"closed")
        return type("Done", (), {"stdout": "", "stderr": ""})()

    monkeypatch.setattr(single_car_short.subprocess, "run", fake_run)
    monkeypatch.setattr(single_car_short.Path, "replace",
                        lambda self, other: None, raising=False)
    assert single_car_short.close_engine_gap(video, at=11.32, length=5.0) == 5.0

    filters = str(calls[0][calls[0].index("-filter_complex") + 1])
    # Everything up to the seam, then everything from where the clip would
    # have ended -- two pieces, joined.
    assert "trim=0:11.320" in filters and "trim=start=16.320" in filters
    assert "atrim=0:11.320" in filters and "atrim=start=16.320" in filters
    assert "concat=n=2:v=1:a=1" in filters
    assert calls[0][calls[0].index("-r") + 1] == "24"

    # A failure to close says so loudly, because what is left is a video
    # nobody should upload.
    assert single_car_short.close_engine_gap(
        "/nonexistent/film.mp4", at=11.32, length=5.0) == 0.0


def test_the_build_closes_the_gap_rather_than_shipping_the_hole():
    """Fetching first stops a fetch from leaving a hole and checking for
    sound stops the known cause, but neither covers whatever fails next."""
    import inspect
    import single_car_short

    body = inspect.getsource(single_car_short.build_short)
    overlay = body.index("overlay_engine_clip(")
    close = body.index("close_engine_gap(")
    assert overlay < close, "only reached when the overlay did not work"
    # The length the viewer sees is the length that is recorded.
    assert 'manifest.get("duration_seconds") or 0) - removed' in body
    # And a build that asked for a clip and did not get one says so, rather
    # than looking identical to one that never asked.
    assert '"engine_clip_failed"' in body
