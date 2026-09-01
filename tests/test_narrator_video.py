import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from narrator_video import _caption_chunks, _caption_timeline  # noqa: E402


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
