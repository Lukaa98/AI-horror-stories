import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from narrator_video import (  # noqa: E402
    CANVAS,
    _blink_intervals,
    _caption_chunks,
    _caption_timeline,
    _drag_race_track,
    _drift_doodle_track,
    _merged_boundaries,
    _pose_intervals,
    _progress_bar_track,
    _scene_time_boundaries,
    _typing_headline_positions,
    _value_at,
    _wobble_intervals,
)


def _make_transparent_png(path, size=(200, 300)):
    from PIL import Image
    Image.new("RGBA", size, (0, 0, 0, 0)).save(path)


def test_caption_chunks_tile_the_full_duration_with_no_gaps():
    script = "This is a short narration script with exactly twelve words in it here."
    chunks = _caption_chunks(script, duration=12.0, words_per_chunk=4)

    assert chunks[0][1] == 0.0
    assert chunks[-1][2] == 12.0
    for (_, _, end), (_, next_start, _) in zip(chunks, chunks[1:]):
        assert end == next_start

    # Every word should show up in exactly one chunk, in order.
    joined = " ".join(text for text, _, _ in chunks)
    assert joined.split() == script.split()


def test_caption_chunks_empty_script_yields_no_captions():
    assert _caption_chunks("", duration=10.0) == []


def test_default_captions_are_one_spoken_word_at_a_time():
    chunks = _caption_chunks("exact same turbo", duration=3.0)
    assert [text for text, _, _ in chunks] == ["exact", "same", "turbo"]


def test_real_word_timestamps_override_estimated_caption_timing():
    manifest = {
        "script": "exact turbo",
        "word_timeline": [{"word": "exact", "start": 0.2, "end": 0.7}, {"word": "turbo", "start": 0.8, "end": 1.4}],
    }
    assert _caption_timeline(manifest, 2.0) == [("exact", 0.2, 0.7), ("turbo", 0.8, 1.4)]


def test_pose_intervals_fall_back_to_a_fixed_clock_without_word_timeline():
    intervals = _pose_intervals({}, 10.0)
    assert intervals[0][0] == 0.0
    assert intervals[-1][2] in {"steady", "jolt", "lean_left", "lean_right"}
    for (_, end, _), (next_start, _, _) in zip(intervals, intervals[1:]):
        assert end == next_start
    assert intervals[-1][1] == 10.0
    assert len(intervals) > 1


def test_pose_intervals_split_on_real_pauses_and_cycle_through_all_four_poses():
    # A ~0.5s gap between "one" and "Meet" should read as a sentence
    # boundary; the much smaller gaps elsewhere should not.
    word_timeline = [
        {"word": "This", "start": 0.0, "end": 0.3},
        {"word": "is", "start": 0.35, "end": 0.5},
        {"word": "one", "start": 0.55, "end": 0.9},
        {"word": "Meet", "start": 1.4, "end": 1.7},
        {"word": "two", "start": 1.75, "end": 2.0},
    ]
    manifest = {"word_timeline": word_timeline}
    intervals = _pose_intervals(manifest, 3.0)
    assert [pose for _, _, pose in intervals] == ["steady", "jolt"]
    assert intervals[0][0] == 0.0
    assert intervals[-1][1] == 3.0
    # The cut should land in the gap, not exactly on either word boundary.
    assert 0.9 < intervals[0][1] < 1.4


def test_blink_intervals_are_short_and_spaced_out():
    intervals = _blink_intervals(10.0)
    assert intervals
    for start, end, value in intervals:
        assert value == "blink"
        assert end - start <= 0.13


def test_wobble_intervals_alternate_fast_and_cover_the_full_duration():
    intervals = _wobble_intervals(2.0)
    assert intervals[0][0] == 0.0
    assert intervals[-1][1] == 2.0
    for (_, end, _), (next_start, _, _) in zip(intervals, intervals[1:]):
        assert end == next_start
    values = [value for _, _, value in intervals]
    assert set(values) == {"a", "b"}
    # Alternates rather than repeating the same seed back to back.
    assert all(a != b for a, b in zip(values, values[1:]))


def test_merged_boundaries_combine_all_interval_lists_without_duplicates():
    mouth = [(0.0, 1.0, "closed"), (1.0, 2.0, "wide")]
    pose = [(0.0, 0.7, "a"), (0.7, 1.4, "b"), (1.4, 2.0, "c")]
    bounds = _merged_boundaries([mouth, pose], 2.0)
    assert bounds == sorted(set(bounds))
    assert bounds[0] == 0.0 and bounds[-1] == 2.0
    assert 1.0 in bounds and 0.7 in bounds and 1.4 in bounds


def test_value_at_falls_back_to_default_outside_all_intervals():
    intervals = [(0.0, 1.0, "open")]
    assert _value_at(intervals, 0.5, "closed") == "open"
    assert _value_at(intervals, 1.5, "closed") == "closed"


def test_scene_time_boundaries_falls_back_to_even_split_without_word_timeline():
    scenes = [{"narration": "a b c"}, {"narration": "d e"}]
    boundaries = _scene_time_boundaries(scenes, [], 10.0)
    assert boundaries == [(0.0, 5.0), (5.0, 10.0)]


def test_scene_time_boundaries_cuts_at_the_pause_midpoint_between_scenes():
    # Scene 0's narration is 3 words, scene 1's is 2 words, with a 0.5s
    # pause between "three" ending and "four" starting -- the cut should
    # land at that pause's midpoint, not at an even 1.75s split.
    scenes = [{"narration": "one two three"}, {"narration": "four five"}]
    word_timeline = [
        {"word": "one", "start": 0.0, "end": 0.3},
        {"word": "two", "start": 0.35, "end": 0.6},
        {"word": "three", "start": 0.65, "end": 1.9},
        {"word": "four", "start": 2.4, "end": 2.7},
        {"word": "five", "start": 2.75, "end": 3.0},
    ]
    boundaries = _scene_time_boundaries(scenes, word_timeline, 3.5)
    assert boundaries[0] == (0.0, 2.15)
    assert boundaries[1] == (2.15, 3.5)


def test_scene_time_boundaries_names_a_rival_car_only_where_it_is_actually_spoken():
    """The regression this whole feature exists to fix: a scene naming a
    rival car must get a display window that actually contains where its
    own words are spoken, not an even slice of the whole clip that has no
    relation to it (which is how a rival's photo ended up showing at the
    end of a video instead of during the sentence that names it)."""
    scenes = [
        {"narration": "one two"},
        {"narration": "three four five"},
        {"narration": "six"},
    ]
    word_timeline = [
        {"word": "one", "start": 0.0, "end": 0.2},
        {"word": "two", "start": 0.25, "end": 0.4},
        {"word": "three", "start": 0.8, "end": 1.0},
        {"word": "four", "start": 1.05, "end": 1.3},
        {"word": "five", "start": 1.35, "end": 1.6},
        {"word": "six", "start": 2.0, "end": 2.3},
    ]
    boundaries = _scene_time_boundaries(scenes, word_timeline, 2.6)
    rival_start, rival_end = boundaries[1]
    # The rival scene's actual spoken window (0.8-1.6) must fall entirely
    # inside its assigned display window -- an even 3-way split of a 2.6s
    # clip ((0.867, 1.733)) would have clipped "five" (ends at 1.6, so
    # that alone wouldn't catch this bug), so also pin the boundary close
    # to the real pause midpoints (0.4/0.8 -> 0.6, 1.6/2.0 -> 1.8).
    assert rival_start <= 0.8 and rival_end >= 1.6
    assert 0.5 <= rival_start <= 0.7
    assert 1.7 <= rival_end <= 1.9


def test_typing_headline_positions_reveal_one_character_at_a_time():
    positions = _typing_headline_positions("GTS", 0.0, 5.0)
    assert [prefix for prefix, _, _ in positions] == ["G", "GT", "GTS"]
    # Each reveal step starts exactly where the previous one ended.
    starts = [start for _, start, _ in positions]
    durations = [duration for _, _, duration in positions]
    assert starts[0] == 0.0
    for i in range(len(positions) - 1):
        assert starts[i] + durations[i] == starts[i + 1]
    # The full headline (last prefix) holds for whatever time is left in
    # the scene, not just one more tick -- it shouldn't vanish right after
    # finishing typing.
    assert starts[-1] + durations[-1] == 5.0
    assert durations[-1] > durations[0]


def test_typing_headline_positions_caps_the_char_rate_to_fit_a_short_scene():
    # A scene shorter than total_chars * TYPING_CHAR_SECONDS must still
    # finish typing by `end`, not overrun it.
    positions = _typing_headline_positions("HELLO", 0.0, 0.05)
    assert positions[-1][1] + positions[-1][2] == 0.05


def test_typing_headline_positions_empty_text_yields_no_frames():
    assert _typing_headline_positions("", 0.0, 5.0) == []


def test_drift_doodle_track_returns_no_clips_without_a_cutout_path(tmp_path):
    assert _drift_doodle_track(None, CANVAS, 5.0) == []
    assert _drift_doodle_track(str(tmp_path / "missing.png"), CANVAS, 5.0) == []


def test_drift_doodle_track_builds_small_clips_for_the_full_duration(tmp_path):
    cutout = tmp_path / "car.png"
    _make_transparent_png(cutout)
    clips = _drift_doodle_track(str(cutout), CANVAS, 6.0)
    # The car itself plus a few trailing smoke puffs -- a handful of small
    # clips, not one nested full-canvas composite (which was the actual
    # performance regression this shape fixes).
    assert len(clips) >= 2
    for clip in clips:
        assert clip.duration == 6.0
        # Each clip should stay inside the corner region across a couple
        # of spin cycles, not drift off toward the middle of the frame or
        # off-canvas.
        for t in (0.0, 1.1, 2.7, 5.9):
            x, y = clip.pos(t)
            assert 0 <= x <= CANVAS[0]
            assert 0 <= y <= CANVAS[1]


def test_drag_race_track_returns_empty_without_both_cutouts(tmp_path):
    cutout = tmp_path / "car.png"
    _make_transparent_png(cutout)
    assert _drag_race_track(None, str(cutout), 400, 300, CANVAS, 1.0, 4.0) == []
    assert _drag_race_track(str(cutout), str(tmp_path / "missing.png"), 400, 300, CANVAS, 1.0, 4.0) == []


def test_drag_race_track_returns_empty_for_a_too_short_beat(tmp_path):
    cutout = tmp_path / "car.png"
    _make_transparent_png(cutout)
    assert _drag_race_track(str(cutout), str(cutout), 400, 300, CANVAS, 1.0, 1.1) == []


def test_drag_race_track_the_higher_horsepower_car_reaches_the_bottom_first(tmp_path):
    main_cutout = tmp_path / "main.png"
    rival_cutout = tmp_path / "rival.png"
    _make_transparent_png(main_cutout)
    _make_transparent_png(rival_cutout)

    main_clip, rival_clip = _drag_race_track(
        str(main_cutout), str(rival_cutout), main_hp=300, rival_hp=500, size=CANVAS, seg_start=2.0, seg_end=6.0
    )
    assert main_clip.start == 2.0 and rival_clip.start == 2.0
    assert main_clip.duration == 4.0 and rival_clip.duration == 4.0

    # Rival (500hp) should win: it reaches further down the track by the
    # end of the beat than the main car (300hp) does, at the same instant.
    _, main_y_end = main_clip.pos(main_clip.duration)
    _, rival_y_end = rival_clip.pos(rival_clip.duration)
    assert rival_y_end > main_y_end

    # Both lanes stay on their own side of the frame the whole time.
    main_x, _ = main_clip.pos(0.0)
    rival_x, _ = rival_clip.pos(0.0)
    assert main_x < CANVAS[0] / 2 < rival_x


def test_progress_bar_track_fills_left_to_right_over_the_real_duration():
    clip = _progress_bar_track(CANVAS, 10.0)
    assert clip.duration == 10.0
    start_frame = clip.get_frame(0.0)
    mid_frame = clip.get_frame(5.0)
    end_frame = clip.get_frame(10.0)
    assert start_frame[:, :5].sum() == 0  # nothing filled yet
    assert mid_frame[:, :5].sum() > 0 and mid_frame[:, -5:].sum() == 0
    assert end_frame[:, -5:].sum() > 0  # fully filled by the end


def test_scene_time_boundaries_recovers_from_earlier_tokenization_drift():
    """An earlier scene's word count under-counting relative to Whisper's
    own tokenization (e.g. "5.0-liter" splitting into more word entries
    than a plain text .split() sees) must not shift every later scene's
    boundary by however many words it was off -- re-anchoring to the next
    scene's own first word should recover, not compound the drift."""
    scenes = [
        # .split() sees 3 words here, but the transcribed audio actually
        # has 5 word entries for this scene (a tokenization mismatch).
        {"narration": "It has 5.0-liter power"},
        {"narration": "Rival name here"},
    ]
    word_timeline = [
        {"word": "It", "start": 0.0, "end": 0.1},
        {"word": "has", "start": 0.15, "end": 0.3},
        {"word": "5", "start": 0.35, "end": 0.5},
        {"word": "0", "start": 0.55, "end": 0.7},
        {"word": "liter", "start": 0.75, "end": 0.9},
        {"word": "power", "start": 0.95, "end": 1.1},
        {"word": "Rival", "start": 2.0, "end": 2.3},
        {"word": "name", "start": 2.35, "end": 2.6},
        {"word": "here", "start": 2.65, "end": 2.9},
    ]
    boundaries = _scene_time_boundaries(scenes, word_timeline, 3.2)
    rival_start, rival_end = boundaries[1]
    # Without re-anchoring, scene 2 would start reading from word_index=4
    # ("liter"/"power"/"Rival" -> wrongly spans ~0.75-2.3, cutting off
    # most of "Rival") instead of correctly finding "Rival" at index 6.
    assert rival_end >= 2.9
    assert rival_start > 1.0
