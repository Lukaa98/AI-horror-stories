"""A snapshot of the channel, written where the dashboard can read it.

The dashboard cannot ask YouTube anything directly: the refresh token lives
in a GitHub secret and deliberately never reaches the browser. So this runs
where the token already is, and leaves the answer as a file on the output
branch for the page to read.

That makes it a snapshot rather than a live feed. It is refreshed when
somebody asks for it, which for a channel posting once a day is the same
thing in practice.
"""
import argparse
import os

from youtube_tools import gh

OUTPUT_BRANCH = "cars-output"
STATUS_PATH = "youtube/channel.json"
# One page. A channel posting daily takes months to outgrow it, and the
# dashboard shows the newest first anyway.
MAX_VIDEOS = 50
# YouTube's own category ids, for the handful a car channel ever uses.
CATEGORY_NAMES = {"2": "Autos & Vehicles", "24": "Entertainment", "22": "People & Blogs",
                  "17": "Sports", "28": "Science & Technology"}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def collect(youtube):
    """Everything the dashboard shows, in one request per page of videos."""
    channels = youtube.channels().list(
        part="snippet,contentDetails,statistics", mine=True).execute()
    items = channels.get("items") or []
    if not items:
        raise SystemExit("The token is not attached to a channel.")
    channel = items[0]
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    stats = channel.get("statistics") or {}

    # The uploads playlist carries private and scheduled videos too, for the
    # owner -- which is the whole point, since those are the ones that
    # cannot be checked any other way.
    playlist = youtube.playlistItems().list(
        part="contentDetails", playlistId=uploads, maxResults=MAX_VIDEOS).execute()
    video_ids = [row["contentDetails"]["videoId"] for row in playlist.get("items") or []]

    videos = []
    if video_ids:
        detail = youtube.videos().list(
            part="snippet,status,statistics,contentDetails",
            id=",".join(video_ids)).execute()
        for row in detail.get("items") or []:
            snippet = row.get("snippet") or {}
            status = row.get("status") or {}
            counts = row.get("statistics") or {}
            thumbnails = snippet.get("thumbnails") or {}
            videos.append({
                "id": row["id"],
                "title": snippet.get("title") or "",
                "privacy": status.get("privacyStatus") or "",
                "publish_at": status.get("publishAt") or "",
                "published_at": snippet.get("publishedAt") or "",
                "duration": (row.get("contentDetails") or {}).get("duration") or "",
                "views": _int(counts.get("viewCount")),
                "likes": _int(counts.get("likeCount")),
                "comments": _int(counts.get("commentCount")),
                "category": CATEGORY_NAMES.get(str(snippet.get("categoryId") or ""),
                                               str(snippet.get("categoryId") or "")),
                "language": snippet.get("defaultAudioLanguage")
                            or snippet.get("defaultLanguage") or "",
                "synthetic": bool(status.get("containsSyntheticMedia")),
                "thumbnail": (thumbnails.get("medium") or thumbnails.get("default")
                              or {}).get("url") or "",
            })
    videos.sort(key=lambda v: v.get("published_at") or "", reverse=True)

    return {
        "channel": {
            "title": (channel.get("snippet") or {}).get("title") or "",
            "subscribers": _int(stats.get("subscriberCount")),
            "views": _int(stats.get("viewCount")),
            "videos": _int(stats.get("videoCount")),
            "hidden_subscribers": bool(stats.get("hiddenSubscriberCount")),
        },
        "videos": videos,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default=OUTPUT_BRANCH)
    parser.add_argument("--path", default=STATUS_PATH)
    args = parser.parse_args()

    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GH_PAT", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if not (repository and token):
        raise SystemExit("GITHUB_REPOSITORY and a token (GH_PAT or GITHUB_TOKEN) are required.")

    from youtube_tools.youtube_client import get_authenticated_service

    snapshot = collect(get_authenticated_service())
    from datetime import datetime, timezone
    snapshot["checked_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    gh.write_json(repository, token, args.branch, args.path, snapshot,
                  "youtube: channel snapshot")
    channel = snapshot["channel"]
    print(f"[status] {channel['title']}: {len(snapshot['videos'])} videos, "
          f"{channel['subscribers']} subscribers, {channel['views']} views.", flush=True)


if __name__ == "__main__":
    main()
