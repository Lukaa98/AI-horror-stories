import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from narrator_video import _caption_chunks  # noqa: E402


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
