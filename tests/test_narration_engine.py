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


def test_the_word_budget_comes_from_how_fast_the_narrator_really_talks():
    """The cap was 175, written for text-to-speech generated at 1.35x. The
    conversational model has no speed control, so at its own pace that is a
    78-second read needing a 1.42x squeeze to reach target -- which is
    exactly the compression that made the old voice sound like a machine."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    import single_car_short

    assert single_car_short.TARGET_DURATION_SECONDS == 55.0, "under a minute, deliberately"

    pace = single_car_short.NARRATION_WORDS_PER_SECOND
    assert 2.0 < pace < 2.5, "measured from a real read, not assumed"

    # The cap is what the narrator can say in the target without hurrying.
    spoken = single_car_short.WORD_CAP / pace
    assert abs(spoken - single_car_short.TARGET_DURATION_SECONDS) < 1.0

    # And the correction left over is small enough not to be heard.
    assert spoken / single_car_short.TARGET_DURATION_SECONDS < 1.10


def test_the_untouched_window_sits_around_the_target():
    """A window written for 58 seconds would stretch a 55-second read to
    fit it, which is the opposite of the point."""
    import inspect
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    import single_car_short

    signature = inspect.signature(single_car_short.normalize_audio_duration)
    low = signature.parameters["minimum"].default
    high = signature.parameters["maximum"].default
    assert low < single_car_short.TARGET_DURATION_SECONDS < high
    assert high <= 58.0, "nothing should be left sitting at a minute"


def test_the_delivery_is_the_one_that_measured_fastest():
    """Three instructions were read against the same script: this at 2.43
    words a second, "brisk" at 2.17, "as fast as you can" at 2.34 --
    asking harder made it slower. The wording is a measurement, not a
    preference."""
    assert "deliberately talking fast" in narrator_script.AUDIO_DELIVERY
    assert "barely pausing between" in narrator_script.AUDIO_DELIVERY

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    import single_car_short

    # And the word budget tracks it, rather than being set once and left.
    assert abs(single_car_short.NARRATION_WORDS_PER_SECOND - 138 / 56.8) < 0.001
    assert single_car_short.WORD_CAP == 134


def test_the_build_records_how_its_voice_was_actually_made():
    """It was writing voice_preset "onyx" and tts_speed 1.35 long after
    neither was true. A build that cannot say how it was made cannot be
    compared with another one."""
    settings = narrator_script.narration_settings()
    assert settings["engine"] == "gpt-audio"
    assert settings["voice"] == "echo"
    assert settings["pitch"] == 0.91
    assert settings["tts_speed"] is None, "nothing is sped up any more"

    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/single_car_short.py").read_text()
    assert '"narration": narration_settings(),' in source
