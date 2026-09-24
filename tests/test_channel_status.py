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
