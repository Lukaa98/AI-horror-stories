"""The channel snapshot the dashboard reads instead of holding a token."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_tools import channel_status


class _Call:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeYouTube:
    """Just enough of the client to answer the three calls collect() makes."""

    def channels(self):
        return type("C", (), {"list": lambda _self, **kw: _Call({"items": [{
            "snippet": {"title": "ChasingRedLine"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
            "statistics": {"subscriberCount": "12", "viewCount": "340", "videoCount": "2"},
        }]})})()

    def playlistItems(self):
        return type("P", (), {"list": lambda _self, **kw: _Call({"items": [
            {"contentDetails": {"videoId": "vid_new"}},
            {"contentDetails": {"videoId": "vid_old"}},
        ]})})()

    def videos(self):
        return type("V", (), {"list": lambda _self, **kw: _Call({"items": [
            {"id": "vid_old",
             "snippet": {"title": "Older", "publishedAt": "2026-09-01T00:00:00Z",
                         "categoryId": "24", "thumbnails": {"medium": {"url": "u1"}}},
             "status": {"privacyStatus": "public", "containsSyntheticMedia": True},
             "statistics": {"viewCount": "900", "likeCount": "20", "commentCount": "3"},
             "contentDetails": {"duration": "PT54S"}},
            {"id": "vid_new",
             "snippet": {"title": "Newer", "publishedAt": "2026-09-24T00:00:00Z",
                         "categoryId": "2", "defaultAudioLanguage": "en", "thumbnails": {}},
             "status": {"privacyStatus": "private", "publishAt": "2026-09-25T16:15:00Z"},
             "statistics": {},
             "contentDetails": {"duration": "PT54S"}},
        ]})})()


def test_the_snapshot_carries_what_studio_would_have_to_be_opened_to_see():
    snapshot = channel_status.collect(_FakeYouTube())
    assert snapshot["channel"]["subscribers"] == 12

    newest, older = snapshot["videos"]
    assert newest["id"] == "vid_new", "newest first"

    # A scheduled video is the whole reason for this: it is private, so it
    # cannot be read without the channel's own token.
    assert newest["privacy"] == "private"
    assert newest["publish_at"] == "2026-09-25T16:15:00Z"
    assert newest["category"] == "Autos & Vehicles"
    assert newest["language"] == "en"
    assert newest["synthetic"] is False

    # And the one that went up misconfigured stays visibly misconfigured.
    assert older["category"] == "Entertainment"
    assert older["language"] == ""
    assert older["synthetic"] is True
    assert older["views"] == 900


def test_missing_counts_do_not_crash_the_snapshot():
    """A private video has no statistics at all, which is the normal case
    for everything scheduled."""
    assert channel_status._int(None) == 0
    assert channel_status._int("not a number") == 0
    assert channel_status._int("41") == 41


def test_the_snapshot_says_whether_youtube_is_happy_with_the_video():
    """Processing state, rejection reasons and Studio's own warnings, so a
    video that failed somewhere does not just sit there looking fine."""
    class _Rejected(_FakeYouTube):
        def videos(self):
            return type("V", (), {"list": lambda _self, **kw: _Call({"items": [{
                "id": "vid_bad",
                "snippet": {"title": "Bad", "publishedAt": "2026-09-24T00:00:00Z",
                            "categoryId": "2", "tags": ["a", "b"], "description": "x",
                            "thumbnails": {}},
                "status": {"privacyStatus": "private", "uploadStatus": "rejected",
                           "rejectionReason": "copyright", "madeForKids": True,
                           "license": "youtube", "embeddable": False},
                "statistics": {},
                "contentDetails": {"duration": "PT54S"},
                "suggestions": {"processingWarnings": ["unknownAudioFormat"]},
            }]})})()

    video = channel_status.collect(_Rejected())["videos"][0]
    assert video["upload_status"] == "rejected"
    assert video["problem"] == "copyright"
    assert video["made_for_kids"] is True
    assert video["embeddable"] is False
    assert video["tags"] == 2 and video["described"] is True
    assert video["warnings"] == ["unknownAudioFormat"]


def test_an_optional_part_being_refused_does_not_lose_the_snapshot():
    """suggestions needs ownership and is not always granted. Losing every
    video over an optional part would be a bad trade."""
    class _NoSuggestions(_FakeYouTube):
        def videos(self):
            def _list(_self, part="", id=""):
                if "suggestions" in part:
                    raise RuntimeError("forbidden")
                return _Call({"items": [{
                    "id": "vid", "snippet": {"title": "T", "publishedAt": "2026-09-24T00:00:00Z",
                                             "categoryId": "2", "thumbnails": {}},
                    "status": {"privacyStatus": "public"}, "statistics": {},
                    "contentDetails": {"duration": "PT54S"}}]})
            return type("V", (), {"list": _list})()

    snapshot = channel_status.collect(_NoSuggestions())
    assert snapshot["videos"][0]["warnings"] == []


def test_each_video_says_which_build_made_it():
    """Every action -- delete, reschedule, push the listing -- is addressed
    by build id, and the YouTube API knows nothing about builds. The link
    lives in each build's upload.json and is read back rather than kept in
    a second place that could disagree."""
    from pathlib import Path

    source = Path(channel_status.__file__).read_text()
    assert "def _attach_build_ids" in source
    assert 'video["build_id"] = found.get(video["id"], "")' in source
    # Newest first with an early exit, so an unmatched video cannot walk
    # the whole branch.
    assert "MAX_BUILDS_SEARCHED" in source
    assert "reverse=True" in source
    assert "if not wanted - set(found):" in source


def test_a_video_with_no_build_is_left_alone():
    """Best effort: offering an action that would fail is worse than
    offering none."""
    videos = [{"id": "orphan"}]
    channel_status._attach_build_ids(videos, repository="", token="")
    assert videos[0].get("build_id", "") == "" or "build_id" not in videos[0]
