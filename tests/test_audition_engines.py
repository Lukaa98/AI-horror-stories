"""Comparing engines, rather than timbres of the same engine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))

import audition_engines


def test_the_comparison_isolates_the_speed_up_from_the_model():
    """The current sound has two possible causes -- the model reads text
    instead of speaking, and the audio is compressed to 1.35x afterwards.
    Only one of them is worth paying to fix, so they are rendered apart."""
    fast = audition_engines.ENGINES["a-tts-fast-1_35x"]
    natural = audition_engines.ENGINES["b-tts-natural-brisk"]
    assert fast["speed"] == 1.35 and natural["speed"] == 1.0
    assert fast["voice"] == natural["voice"], "same voice, so only the pacing differs"
    assert "talking fast" in natural["instructions"], "asked to speak quickly, not sped up"

    assert audition_engines.ENGINES["c-gpt-audio"]["engine"] == "audio"


def test_a_short_file_counts_as_a_failure():
    """One preset came back 4.3 seconds long with no error, and nothing
    noticed. A call that returns a few hundred bytes of silence is a failed
    call, not a sample."""
    source = Path(audition_engines.__file__).read_text()
    assert "if size < 20_000:" in source
    assert "out_path.unlink(missing_ok=True)" in source, "do not leave a broken sample behind"


def test_the_audio_model_is_told_to_read_rather_than_reply():
    """gpt-audio is a conversational model: handed a script as a user turn
    it would answer it instead of saying it."""
    source = Path(audition_engines.__file__).read_text()
    assert "word for word" in source


def test_the_comparison_can_run_on_a_real_build_s_narration():
    """A sample Mustang script is fine for picking a timbre. Judging
    naturalness wants the words that actually ship, with their numbers,
    em-dashes and closing question."""
    source = Path(audition_engines.__file__).read_text()
    assert "def script_from_build" in source
    # Over HTTP, because the output branch is 2.5GB of video and is never
    # cloned into the runner.
    assert "raw.githubusercontent.com" in source
    assert "has no script in its result.json" in source, "refuse rather than read silence"

    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/cars-research.yml").read_text()
    assert "startsWith(inputs.query, 'engines')" in workflow
    assert "--from-build" in workflow
