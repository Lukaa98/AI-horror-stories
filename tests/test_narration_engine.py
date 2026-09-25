"""The narration voice: which model speaks, and in what register."""
import sys
import pytest
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
    source = Path(narrator_script.__file__).read_text()
    block = source[source.index("def _shift_pitch"):]
    assert "asetrate=" in block and "atempo=" in block


def test_a_take_that_lands_out_of_register_is_asked_for_again(monkeypatch, tmp_path):
    """gpt-audio picks its own register every call -- 133 to 157 Hz on the
    same script. Shifting a high one down sounds processed, because that is
    a time-stretch. Asking for another take is not: it is a real
    performance, just a different one."""
    takes = iter([157.0, 148.1, 134.5])
    heard = []

    def fake_take(text, path):
        path.write_bytes(b"x" * 30_000)
        return path

    monkeypatch.setattr(narrator_script, "_one_take", fake_take)
    monkeypatch.setattr(narrator_script, "median_f0",
                        lambda *a, **k: heard.append(next(takes)) or heard[-1])

    narrator_script._synthesize_with_audio_model("script", tmp_path / "n.mp3", retakes=2)

    assert heard == [157.0, 148.1, 134.5], "it kept asking until one landed in the band"
    assert narrator_script._LAST_PITCH["take_hz"] == 134.5
    assert narrator_script._LAST_PITCH["takes"] == 3


def test_a_take_already_in_register_is_not_re_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(narrator_script, "_one_take",
                        lambda text, path: path.write_bytes(b"x" * 30_000) or path)
    monkeypatch.setattr(narrator_script, "median_f0", lambda *a, **k: 134.0)

    narrator_script._synthesize_with_audio_model("script", tmp_path / "n.mp3", retakes=2)

    assert narrator_script._LAST_PITCH["takes"] == 1


def test_when_no_take_lands_in_register_the_closest_one_ships(monkeypatch, tmp_path):
    """Retakes are generations, so they are capped. Ending on a bad one
    would make the cap actively harmful -- the best of what was heard is
    what ships."""
    takes = iter([157.0, 143.0, 151.0])
    written = {}

    def fake_take(text, path):
        hz = next(takes)
        path.write_bytes(str(hz).encode() + b"x" * 30_000)
        written["last"] = hz
        return path

    monkeypatch.setattr(narrator_script, "_one_take", fake_take)
    monkeypatch.setattr(narrator_script, "median_f0",
                        lambda path, *a, **k: float(Path(path).read_bytes().split(b"x")[0]))

    out = tmp_path / "n.mp3"
    narrator_script._synthesize_with_audio_model("script", out, retakes=2)

    assert written["last"] == 151.0, "the last take was the worst one"
    assert narrator_script._LAST_PITCH["take_hz"] == 143.0
    assert out.read_bytes().startswith(b"143.0"), "the closest take is the one on disk"


def test_the_model_s_own_take_ships_unshifted(monkeypatch, tmp_path):
    """Lowering the register was tried twice -- a fixed 0.91, then a
    measured 130 Hz that did land every take on the same number. It still
    sounded processed, because the shift is a time-stretch. So it is off,
    and the pitch is measured only so builds stay comparable."""
    assert narrator_script.AUDIO_TARGET_HZ == 0

    monkeypatch.setattr(narrator_script, "median_f0", lambda *a, **k: 140.4)
    monkeypatch.setattr(narrator_script, "_shift_pitch",
                        lambda path, factor: pytest.fail("nothing should be shifted"))

    narrator_script._deepen(tmp_path / "narration.mp3")

    assert narrator_script._LAST_PITCH["source"] == "off"
    assert narrator_script._LAST_PITCH["measured_hz"] == 140.4
    assert narrator_script._LAST_PITCH["result_hz"] == 140.4


def test_the_take_is_moved_onto_a_register_rather_than_down_by_a_factor():
    """gpt-audio does not hand back the same voice twice. Across three
    builds of one script its takes measured 133, 142 and 157 Hz. A fixed
    multiplier carries that spread through: one build shipped at 143 Hz
    when the approved take was 130 -- same settings, audibly not the same
    voice. So the shift is computed per take."""
    target = 130.0

    # A high take is pushed down further than an already-low one, so the
    # three land within a couple of Hz of each other instead of 24 apart.
    landed = [m * narrator_script.pitch_factor_for(m, target) for m in (157.0, 142.9, 133.2)]
    assert max(landed) - min(landed) < 4.0, f"still a spread: {landed}"
    assert all(abs(hz - 130) < 4.0 for hz in landed), landed

    # The shift is a time-stretch, so it is clamped rather than allowed to
    # sound processed: a take far from the target is left slightly off. 0.91
    # is the largest drop anyone has approved by ear, so the clamp sits just
    # past it and nothing gets stretched into a register nobody has heard.
    low, high = narrator_script.PITCH_FACTOR_LIMITS
    assert low < 0.91, "the approved drop has to be reachable"
    assert narrator_script.pitch_factor_for(400.0, target) == low
    assert narrator_script.pitch_factor_for(80.0, target) == high

    # An unmeasurable take has no factor -- the caller decides what to do,
    # rather than being handed a made-up number.
    assert narrator_script.pitch_factor_for(None, target) is None


def test_an_unmeasurable_take_is_not_shifted_by_a_guess(monkeypatch, tmp_path):
    """Falling back is fine; falling back quietly is how a build ships in a
    register nobody chose. Which one happened has to reach the manifest."""
    monkeypatch.setattr(narrator_script, "median_f0", lambda *a, **k: None)
    shifted = []
    monkeypatch.setattr(narrator_script, "_shift_pitch",
                        lambda path, factor: shifted.append(factor) or path)

    narrator_script._deepen(tmp_path / "narration.mp3", target_hz=130.0)

    assert shifted == [narrator_script.AUDIO_PITCH], "the fixed factor is the fallback"
    assert narrator_script._LAST_PITCH["source"] == "fallback"
    assert narrator_script.narration_settings()["pitch"]["source"] == "fallback"


def test_pitch_is_measured_from_the_audio(monkeypatch):
    """The estimator is the whole fix, so it is checked against tones of a
    known pitch rather than trusted."""
    import numpy as np

    for wanted in (110.0, 130.0, 180.0):
        rate = 16000
        t = np.arange(int(3 * rate)) / rate
        # A harmonic on top, because a pure sine is easier to track than
        # speech and would not prove much.
        tone = (0.5 * np.sin(2 * np.pi * wanted * t)
                + 0.25 * np.sin(4 * np.pi * wanted * t)).astype("<f4")
        monkeypatch.setattr(narrator_script, "_decode_mono",
                            lambda path, sample_rate=16000: (tone, rate))
        measured = narrator_script.median_f0("ignored.mp3")
        assert abs(measured - wanted) < 2.0, f"{wanted} Hz read as {measured}"

    # Silence is unmeasurable, and says so rather than returning a number.
    monkeypatch.setattr(narrator_script, "_decode_mono",
                        lambda path, sample_rate=16000: (np.zeros(48000, "<f4"), 16000))
    assert narrator_script.median_f0("ignored.mp3") is None


def test_a_silent_response_is_an_error_not_a_narration():
    """A call can return a few hundred bytes and no error. One preset came
    back 4.3 seconds long that way, and nothing noticed until it was
    played."""
    source = Path(narrator_script.__file__).read_text()
    block = source[source.index("def _one_take"):]
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
    assert settings["target_hz"] == narrator_script.AUDIO_TARGET_HZ
    assert settings["tts_speed"] is None, "nothing is sped up any more"

    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/single_car_short.py").read_text()
    assert '"narration": narration_settings(),' in source
