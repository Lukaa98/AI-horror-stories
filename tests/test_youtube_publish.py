import pytest

from youtube_tools.publish_video import normalize_publish_at


def test_normalize_publish_at_accepts_zulu_and_converts_utc_offset():
    assert normalize_publish_at("2026-07-24T14:00:00Z") == "2026-07-24T14:00:00Z"
    assert normalize_publish_at("2026-07-24T10:00:00-04:00") == "2026-07-24T10:00:00-04:00"


def test_normalize_publish_at_requires_a_timezone():
    with pytest.raises(ValueError, match="timezone offset"):
        normalize_publish_at("2026-07-24T14:00:00")


def test_channel_research_reads_the_api_rather_than_scraping():
    """Another channel's page shows today's numbers and nothing about the
    shape underneath -- whether it climbed or broke out once, which titles
    travelled. The Data API answers all three, and the dashboard is already
    signed in, so it needs no key and no workflow."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "web/src"
    client = (web / "youtubeResearch.js").read_text()

    # forHandle is exact and costs one unit; search costs a hundred and guesses.
    assert "forHandle" in client
    assert 'youtube("search"' not in client, "search costs 100 units and guesses at the channel"
    # Uploads come back fifty at a time, and the page walk is bounded.
    assert 'maxResults: "50"' in client and "MAX_PAGES" in client
    # Stats have to come from videos.list; playlistItems does not carry them.
    assert '"videos"' in client and "statistics" in client

    panel = (web / "ResearchPanel.jsx").read_text()
    # The finding is the trend, so the oldest-to-newest split is the point.
    assert "sort((a, b) => a.published.localeCompare(b.published))" in panel
    # And the caps/age confound is admitted rather than sold as a cause.
    assert "hint rather than a cause" in panel

    app = (web / "App.jsx").read_text()
    # The panel takes settings now, for the workflow it starts to read other
    # channels' scripts -- so this pins that it is mounted, not its props.
    assert 'view === "research"' in app and "<ResearchPanel" in app
