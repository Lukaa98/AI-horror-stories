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
BUILD_ROOT = "cars/single-car-shorts"
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


def _list_videos(youtube, video_ids, parts):
    """videos.list, falling back if a part is refused.

    "suggestions" needs ownership and is not always granted; losing the
    whole snapshot over an optional part would be a bad trade.
    """
    try:
        return youtube.videos().list(part=parts, id=",".join(video_ids)).execute()
    except Exception:
        reduced = ",".join(part for part in parts.split(",") if part != "suggestions")
        return youtube.videos().list(part=reduced, id=",".join(video_ids)).execute()


# Newest first, and stop once every video is accounted for. A channel
# posting daily will always find its uploads in the first handful of
# builds; the cap is there so an unmatched video cannot walk the whole
# branch.
MAX_BUILDS_SEARCHED = 80


def _attach_build_ids(videos, repository=None, token=None, branch=OUTPUT_BRANCH):
    """Say which build made each video, so the dashboard can act on it.

    The YouTube API knows nothing about builds, and every action -- delete,
    reschedule, push the listing -- is addressed by build id. The link
    exists in each build's upload.json, so it is read back here rather than
    kept in a second place that could disagree.

    Best effort: a video whose build cannot be found simply has no build_id
    and the dashboard offers no actions for it, which is better than
    offering one that would fail.
    """
    import os

    wanted = {video["id"] for video in videos}
    if not wanted:
        return
    repository = repository or os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = token or (os.environ.get("GH_PAT", "").strip()
                      or os.environ.get("GITHUB_TOKEN", "").strip())
    if not (repository and token):
        return

    try:
        listing = gh.api(repository, token, f"/contents/{BUILD_ROOT}?ref={branch}")
    except Exception as error:
        print(f"[status] could not list builds: {error}", flush=True)
        return
    if not listing:
        # A silent return here is what hid a malformed path: every video
        # came back with no build and the message blamed the branch.
        print(f"[status] no builds at {BUILD_ROOT} on {branch}", flush=True)
        return
    names = sorted((row["name"] for row in listing if row.get("type") == "dir"), reverse=True)

    found = {}
    for name in names[:MAX_BUILDS_SEARCHED]:
        if not wanted - set(found):
            break
        try:
            upload = gh.read_json(repository, token, branch,
                                  f"{BUILD_ROOT}/{name}/upload.json")
        except Exception:
            continue
        video_id = (upload or {}).get("video_id")
        if video_id in wanted:
            found[video_id] = name
    for video in videos:
        video["build_id"] = found.get(video["id"], "")


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
        # "suggestions" is what Studio shows as processing problems and
        # editor hints. It is only available for a channel's own videos,
        # and it is the closest thing to an audit the API offers.
        detail = _list_videos(youtube, video_ids,
                              "snippet,status,statistics,contentDetails,suggestions")
        for row in detail.get("items") or []:
            snippet = row.get("snippet") or {}
            status = row.get("status") or {}
            counts = row.get("statistics") or {}
            thumbnails = snippet.get("thumbnails") or {}
            suggestions = row.get("suggestions") or {}
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
                # Whether YouTube is happy with the file itself. "processed"
                # is the only good answer; "rejected" and "failed" carry a
                # reason, and a video stuck on "uploaded" never finished.
                "upload_status": status.get("uploadStatus") or "",
                "problem": (status.get("rejectionReason")
                            or status.get("failureReason") or ""),
                "made_for_kids": bool(status.get("madeForKids")),
                "license": status.get("license") or "",
                "embeddable": bool(status.get("embeddable")),
                "tags": len(snippet.get("tags") or []),
                "described": bool((snippet.get("description") or "").strip()),
                # What Studio would show as warnings on the video.
                "warnings": list(suggestions.get("processingWarnings") or [])
                            + list(suggestions.get("processingErrors") or []),
                "suggestions": list(suggestions.get("editorSuggestions") or []),
            })
    videos.sort(key=lambda v: v.get("published_at") or "", reverse=True)
    _attach_build_ids(videos)

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
