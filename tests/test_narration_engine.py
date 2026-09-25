"""The narration voice: which model speaks, and in what register."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))

import narrator_script


def test_narration_comes_from_the_conversational_model():
    """gpt-4o-mini-tts reads text and guesses prosody from punctuation,
    which is what put pauses in odd places. gpt-audio generates speech.
    Chosen by ear against every text-to-speech preset."""
    assert narrator_script.NARRATION_ENGINE == "gpt-audio"
    assert narrator_script.AUDIO_MODEL == "gpt-audio"
    assert narrator_script.AUDIO_VOICE == "echo"

    source = Path(narrator_script.__file__).read_text()
    branch = source[source.index("def synthesize_narration"):]
    assert 'if NARRATION_ENGINE == "gpt-audio":' in branch
    # The text-to-speech path stays reachable behind the env var.
    assert "audio.speech.create" in source


def test_the_register_is_dropped_without_changing_the_performance():
    """gpt-audio has no pitch control, so the take is lowered afterwards:
    asetrate drops the pitch and slows it, atempo puts the speed back. The
    phrasing, emphasis and accent survive -- only the register moves."""
    assert narrator_script.AUDIO_PITCH == 0.91

    source = Path(narrator_script.__file__).read_text()
    block = source[source.index("def _deepen"):]
    assert "asetrate=" in block and "atempo=" in block
    assert "if not factor or abs(factor - 1.0) < 0.001:" in block, "1.0 should be a no-op"


def test_a_silent_response_is_an_error_not_a_narration():
    """A call can return a few hundred bytes and no error. One preset came
    back 4.3 seconds long that way, and nothing noticed until it was
    played."""
    source = Path(narrator_script.__file__).read_text()
    block = source[source.index("def _synthesize_with_audio_model"):]
    assert "< 20_000" in block
    assert "word for word" in source, "a conversational model would otherwise reply to the script"
