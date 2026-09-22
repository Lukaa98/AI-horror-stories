import math
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from narrator_script import _resolve_voice, build_mouth_timeline  # noqa: E402


def _write_synthetic_wav(path, rate=16000):
    """silence, loud, silence, moderate -- known loudness sections a
    correct implementation must bucket into closed/wide/closed/small."""
    segments = [(0.0, 0.3, 0), (0.3, 0.9, 12000), (0.9, 1.2, 0), (1.2, 1.8, 6000)]
    samples = []
    for start, end, amp in segments:
        count = int((end - start) * rate)
        for i in range(count):
            samples.append(0 if amp == 0 else int(amp * math.sin(2 * math.pi * 220 * i / rate)))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def test_build_mouth_timeline_buckets_by_relative_loudness(tmp_path):
    wav_path = tmp_path / "narration.wav"
    _write_synthetic_wav(wav_path)

    timeline = build_mouth_timeline(str(wav_path))

    assert [segment["mouth"] for segment in timeline] == ["closed", "wide", "closed", "small"]
    # Segments should tile the clip with no gaps or overlaps.
    for previous, current in zip(timeline, timeline[1:]):
        assert previous["end"] == current["start"]
    assert timeline[0]["start"] == 0.0
    assert timeline[-1]["end"] == pytest.approx(1.8, abs=0.05)


def test_raw_ui_voice_is_accepted_without_a_named_preset():
    voice = _resolve_voice("onyx")
    assert voice["voice"] == "onyx"
    assert voice["instructions"]


def test_unknown_voice_is_rejected_clearly():
    with pytest.raises(ValueError, match="Unknown narrator voice"):
        _resolve_voice("not-a-real-voice")
