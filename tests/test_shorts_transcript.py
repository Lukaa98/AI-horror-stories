import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "youtube_tools"))


def test_ids_come_out_of_whatever_got_pasted():
    """A channel page, a list of URLs, or bare ids -- whatever is to hand."""
    import shorts_transcript as st

    assert st.video_ids_from_text(
        "https://www.youtube.com/shorts/2dxa9oz1AZw and "
        "https://www.youtube.com/watch?v=abcdefghijk plus "
        '{"videoId":"ZZZZZZZZZZZ"}'
    ) == ["2dxa9oz1AZw", "abcdefghijk", "ZZZZZZZZZZZ"]

    # Repeats collapse, order is kept: a channel page lists each twice.
    assert st.video_ids_from_text(
        "shorts/2dxa9oz1AZw shorts/2dxa9oz1AZw shorts/abcdefghijk"
    ) == ["2dxa9oz1AZw", "abcdefghijk"]
    assert st.video_ids_from_text("nothing here") == []


def test_cues_group_into_beats_at_the_pauses():
    """Auto-captions carry no punctuation, so sentences are not recoverable.
    Pauses are: a gap is where a beat ended, which is what makes beat order
    and beat length readable at all."""
    import shorts_transcript as st

    entries = [
        {"text": "three hundred horsepower", "start": 0.0, "duration": 1.0},
        {"text": "and nobody noticed", "start": 1.1, "duration": 0.9},   # 0.10 gap: same beat
        {"text": "it was built in Japan", "start": 3.0, "duration": 1.2},  # 1.00 gap: new beat
    ]
    grouped = st.beats(entries)
    assert len(grouped) == 2
    assert grouped[0]["words"] == 6
    assert grouped[1]["words"] == 5
    assert grouped[0]["rate"] == round(6 / grouped[0]["seconds"], 2)


def test_the_shape_measures_the_opening_separately():
    """The opening is the whole retention decision, so averaging it into the
    rest hides it -- an SLR build read its hook at 3.57 w/s against a 2.81
    average and that gap was the complaint."""
    import shorts_transcript as st

    entries = [
        {"text": "three hundred horsepower", "start": 0.5, "duration": 1.0},
        {"text": "and nobody noticed", "start": 0.6 + 1.0, "duration": 0.9},
        {"text": "would you buy one?", "start": 4.0, "duration": 1.5},
    ]
    shape = st.summarise("vid00000001", entries)
    assert shape["words"] == 10
    assert shape["beats"] == 2
    assert shape["opening_words"] == 6
    assert shape["opening_has_number"] is False
    assert shape["closes_on_question"] is True
    assert shape["lead_in_seconds"] == 0.5
    assert "beats_text" in shape, "the words are kept: the shape is the point of having them"
    assert "beats_text" not in st.summarise("vid00000001", entries, keep_text=False)

    numeric = st.summarise("vid00000002", [
        {"text": "0 to 60 in 4.4 seconds", "start": 0.0, "duration": 2.0}])
    assert numeric["opening_has_number"] is True
    assert numeric["closes_on_question"] is False


def test_a_skip_says_which_of_the_three_things_went_wrong():
    """"No captions" is three problems wearing one face: the video has none,
    YouTube changed the page, or a datacenter IP got a consent wall. Three
    runs reported green while the dashboard showed an empty box, and the
    logs could not tell them apart."""
    import shorts_transcript as st

    with patch.object(st, "_get", side_effect=OSError("tunnel refused")):
        assert "fetch failed" in st.caption_track("vid00000010")[1]

    with patch.object(st, "_get", return_value="x" * 30000 + "consent.youtube.com"):
        assert "consent wall" in st.caption_track("vid00000011")[1]

    with patch.object(st, "_get", return_value="<html>tiny</html>"):
        assert "too small" in st.caption_track("vid00000012")[1]

    player = json.dumps({"playabilityStatus": {"status": "OK"}, "captions": {}})
    with patch.object(st, "_get", return_value=f"ytInitialPlayerResponse = {player};var x"):
        url, why = st.caption_track("vid00000013")
    assert url is None and "no caption track" in why

    # And the reason reaches the caller that collects them.
    why = []
    with patch.object(st, "_get", side_effect=OSError("boom")):
        assert st.cues("vid00000014", report_why=why) == []
    assert why and "fetch failed" in why[0]


def test_a_video_without_captions_is_skipped_not_fatal():
    """Some Shorts have no caption track, and YouTube changes the player
    shape when it likes. A run over twenty videos must not die on one."""
    import shorts_transcript as st

    assert st.summarise("vid00000003", []) == {"video_id": "vid00000003", "cues": 0}

    with patch.object(st, "_get", side_effect=OSError("tunnel refused")):
        assert st.caption_track_url("vid00000004") is None
        assert st.cues("vid00000004") == []

    with patch.object(st, "_get", return_value="<html>no player response here</html>"):
        assert st.caption_track_url("vid00000005") is None


def test_the_uploaders_own_track_beats_the_machine_guess():
    """ASR is a guess at what was said; a track the uploader wrote is what
    they meant, punctuation included."""
    import shorts_transcript as st

    player = json.dumps({"captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
        {"kind": "asr", "baseUrl": "https://example.com/asr"},
        {"baseUrl": "https://example.com/written"},
    ]}}})
    with patch.object(st, "_get", return_value=f"ytInitialPlayerResponse = {player};var x"):
        assert st.caption_track_url("vid00000006") == "https://example.com/written&fmt=json3"


def test_json3_events_become_cues():
    import shorts_transcript as st

    payload = json.dumps({"events": [
        {"tStartMs": 500, "dDurationMs": 1200, "segs": [{"utf8": "three hundred"}, {"utf8": " horsepower"}]},
        {"tStartMs": 1700, "dDurationMs": 900, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2000, "dDurationMs": 800, "segs": [{"utf8": "and nobody  noticed"}]},
    ]})
    with patch.object(st, "caption_track", return_value=("https://example.com/t", "ok")), \
         patch.object(st, "_get", return_value=payload):
        got = st.cues("vid00000007")
    assert [c["text"] for c in got] == ["three hundred horsepower", "and nobody noticed"]
    assert got[0]["start"] == 0.5 and got[0]["duration"] == 1.2
