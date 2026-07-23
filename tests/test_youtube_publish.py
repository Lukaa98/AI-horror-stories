import pytest

from youtube_tools.publish_video import normalize_publish_at


def test_normalize_publish_at_accepts_zulu_and_converts_utc_offset():
    assert normalize_publish_at("2026-07-24T14:00:00Z") == "2026-07-24T14:00:00Z"
    assert normalize_publish_at("2026-07-24T10:00:00-04:00") == "2026-07-24T10:00:00-04:00"


def test_normalize_publish_at_requires_a_timezone():
    with pytest.raises(ValueError, match="timezone offset"):
        normalize_publish_at("2026-07-24T14:00:00")
