import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

import listing_videos  # noqa: E402


class _Done:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_a_silent_video_is_found_out_where_the_choice_is_made(monkeypatch):
    """The M2 build picked a video, cut five seconds of silence into the
    narration, rendered the film around the gap, and only then found the
    clip had no audio stream -- for a feature whose entire point is the
    sound. The probe now answers the question at the moment of choosing."""
    for stdout, expected in [
        ("aac\n48000\n", "aac, 48 kHz"),
        ("aac\n44100\n", "aac, 44 kHz"),
        ("opus\n", "opus"),
        # ffprobe prints nothing at all when there is no audio stream.
        ("", None),
        ("\n   \n", None),
        # A rate it will not state is not a reason to call the video silent.
        ("aac\nN/A\n", "aac"),
    ]:
        monkeypatch.setattr(listing_videos, "_run", lambda *a, **k: _Done(stdout))
        assert listing_videos.audio_track("whatever") == expected, stdout


def test_the_picker_will_not_let_a_silent_video_be_chosen():
    """It showed a poster frame and a duration, which is everything except
    the one thing that decides whether the clip can work."""
    app = (ROOT / "web/src/App.jsx").read_text()
    assert 'disabled={video.audio === null}' in app
    assert '" silent"' in app
    assert "no sound" in app
    # Strict null, not falsy: a probe recorded before this check existed has
    # no audio key at all, and "unknown" must stay pickable rather than
    # silently disabling every video from an older probe.
    assert "video.audio === null" in app and "!video.audio" not in app
    # A listing with nothing usable says so instead of looking broken.
    assert "None of this listing" in app
    css = (ROOT / "web/src/App.css").read_text()
    assert ".listing-video.silent" in css

    # And the probe records it for every video it finds.
    probe = (ROOT / "cars/automation/listing_videos.py").read_text()
    assert '"audio": audio_track(url),' in probe
    assert "unusable for an engine clip" in probe, "the log says it too"
