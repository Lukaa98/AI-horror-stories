import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from narrator_video import (  # noqa: E402
    _blink_intervals,
    _caption_chunks,
    _caption_timeline,
    _merged_boundaries,
    _pose_intervals,
    _value_at,
)


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


def test_pose_intervals_cycle_and_cover_the_full_duration():
    intervals = _pose_intervals(2.0)
    assert intervals[0][0] == 0.0
    assert intervals[-1][2] in {"a", "b", "c"}
    for (_, end, _), (next_start, _, _) in zip(intervals, intervals[1:]):
        assert end == next_start
    assert intervals[-1][1] == 2.0


def test_blink_intervals_are_short_and_spaced_out():
    intervals = _blink_intervals(10.0)
    assert intervals
    for start, end, value in intervals:
        assert value == "blink"
        assert end - start <= 0.13


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
