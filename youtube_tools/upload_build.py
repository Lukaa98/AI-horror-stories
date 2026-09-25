"""Upload one finished build to the channel.

Reads the upload.json written when the build rendered -- the title and
description someone has already looked at in the dashboard -- fetches the
rendered MP4, sends it, and records the video id back onto the same file.

It goes up private. Publishing is a decision made after watching the thing,
not a side effect of rendering it.

    python -m youtube_tools.upload_build --build-id single-ferrari-488-...

Needs YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN to
authenticate, and GH_PAT plus GITHUB_REPOSITORY to read and record.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from youtube_tools import gh

OUTPUT_BRANCH = "cars-output"
BUILD_ROOT = "cars/single-car-shorts"
ALLOWED_PRIVACY = ("private", "unlisted", "public")

# Everything the uploader writes into upload.json to record a YouTube upload.
# Deleting the video clears exactly this set, so a deleted build cannot be
# left half-describing a video that no longer exists.
UPLOAD_RECORD_FIELDS = (
    "video_id",
    "video_url",
    "uploaded_at",
    "status",
    "publish_at",
    "thumbnail_set",
    "thumbnail_error",
)


def raw_url(repository, branch, path):
    return f"https://raw.githubusercontent.com/{repository}/{branch}/{path}"


def check_not_already_uploaded(listing, force):
    """Refuse a second upload of the same build.

    YouTube will happily accept the same file twice and give back two video
    ids, leaving a duplicate on the channel and this file pointing at only
    one of them. A re-run of a workflow is a normal thing to do, so this
    has to be the default rather than a warning.
    """
    video_id = (listing or {}).get("video_id")
    if video_id and not force:
        raise SystemExit(
            f"This build is already uploaded as https://youtu.be/{video_id}. "
            "Pass --force only if you mean to put a second copy on the channel."
        )


def normalize_publish_at(raw_value):
    """An ISO 8601 instant for YouTube, or "".

    Rejected here rather than at the API, because a publish time YouTube
    does not understand comes back as a generic 400 after the whole video
    has already been uploaded.
    """
    from datetime import datetime, timezone

    value = str(raw_value or "").strip()
    if not value:
        return ""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"--publish-at must be ISO 8601, not {raw_value!r}.")
    if parsed.tzinfo is None:
        raise SystemExit(
            f"--publish-at needs a timezone offset, e.g. {value}+04:00 -- otherwise "
            "midnight means a different moment on every machine that reads it."
        )
    if parsed <= datetime.now(timezone.utc):
        raise SystemExit(f"--publish-at is in the past ({parsed.isoformat()}).")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def reschedule_existing(args, listing):
    """Move a scheduled publish to a different time."""
    video_id = listing.get("video_id")
    if not video_id:
        raise SystemExit("This build has no video_id -- upload it before scheduling it.")
    publish_at = normalize_publish_at(args.publish_at)
    if not publish_at:
        raise SystemExit("--reschedule needs --publish-at.")

    from youtube_tools.youtube_client import get_authenticated_service
    from youtube_tools.youtube_uploader import set_publish_time

    print(f"[upload] Rescheduling {video_id} for {publish_at}", flush=True)
    set_publish_time(get_authenticated_service(), video_id, publish_at)
    print(f"[upload] Scheduled: https://youtu.be/{video_id}", flush=True)


def unschedule_existing(listing):
    """Cancel a scheduled publish. The video stays up, privately."""
    video_id = listing.get("video_id")
    if not video_id:
        raise SystemExit("This build has no video_id -- nothing is scheduled.")

    from youtube_tools.youtube_client import get_authenticated_service
    from youtube_tools.youtube_uploader import unschedule

    print(f"[upload] Cancelling the schedule on {video_id}", flush=True)
    unschedule(get_authenticated_service(), video_id)
    print(f"[upload] Private, unscheduled: https://youtu.be/{video_id}", flush=True)


def delete_existing(repository, token, args, listing, listing_path):
    """Take the video off the channel and forget it was ever uploaded.

    The build's own files are untouched -- the video, the thumbnail and the
    listing all stay on the output branch, so it can be uploaded again. Only
    the record of the YouTube upload is cleared, which is what puts the
    dashboard back to offering an upload.
    """
    video_id = listing.get("video_id")
    if not video_id:
        raise SystemExit("This build has no video_id -- there is nothing to delete.")

    from youtube_tools.youtube_client import get_authenticated_service
    from youtube_tools.youtube_uploader import delete_video

    print(f"[upload] Deleting {video_id} from the channel", flush=True)
    delete_video(get_authenticated_service(), video_id).execute()

    for field in UPLOAD_RECORD_FIELDS:
        listing.pop(field, None)
    # Kept so a deleted upload is distinguishable from one that never
    # happened, which matters when the same build is uploaded twice.
    listing["deleted_video_id"] = video_id
    gh.write_json(repository, token, args.branch, listing_path, listing,
                  f"youtube: deleted video for {args.build_id}")
    print(f"[upload] Deleted {video_id}. The build can be uploaded again.", flush=True)


def publish_existing(listing):
    """Take a scheduled video public now, whatever it was scheduled for."""
    video_id = listing.get("video_id")
    if not video_id:
        raise SystemExit("This build has no video_id -- upload it before publishing it.")

    from youtube_tools.youtube_client import get_authenticated_service
    from youtube_tools.youtube_uploader import publish_now

    print(f"[upload] Publishing {video_id} now", flush=True)
    publish_now(get_authenticated_service(), video_id)
    print(f"[upload] Public: https://youtu.be/{video_id}", flush=True)


def update_existing(repository, token, args, listing, build_dir):
    """Apply the build's listing to the video it already uploaded."""
    video_id = listing.get("video_id")
    if not video_id:
        raise SystemExit("This build has no video_id -- upload it before updating it.")
    title = (listing.get("title") or "").strip()
    if not title:
        raise SystemExit("upload.json has no title. Refusing to blank a published title.")

    from youtube_tools.youtube_client import get_authenticated_service
    from youtube_tools.youtube_uploader import update_video

    print(f"[upload] Updating {video_id}: {title}", flush=True)
    update_video(
        get_authenticated_service(),
        video_id,
        title=title,
        description=listing.get("description") or "",
        tags=listing.get("tags") or [],
        category_id=str(listing.get("category_id") or "2"),
        language=str(listing.get("language") or "en"),
        contains_synthetic_media=bool(listing.get("contains_synthetic_media")),
    )
    print(f"[upload] Updated: https://youtu.be/{video_id}", flush=True)


def set_thumbnail_on_existing(repository, token, args, listing, listing_path, build_dir):
    """Attach the thumbnail to a video that is already on the channel.

    YouTube refuses custom thumbnails from an unverified channel, so a video
    uploaded before verification keeps a frame picked out of itself. Once
    the channel is verified the thumbnail can be attached without sending
    the video a second time -- which would publish a duplicate.
    """
    video_id = listing.get("video_id")
    if not video_id:
        raise SystemExit("This build has no video_id -- upload it before setting a thumbnail.")
    thumbnail_name = listing.get("thumbnail")
    if not thumbnail_name:
        raise SystemExit("This build has no thumbnail to set.")

    from youtube_tools.youtube_client import get_authenticated_service
    from youtube_tools.youtube_uploader import set_thumbnail

    with tempfile.TemporaryDirectory() as workspace:
        local_thumb = Path(workspace) / thumbnail_name
        gh.download(raw_url(repository, args.branch, f"{build_dir}/{thumbnail_name}"), local_thumb)
        print(f"[upload] Attaching {thumbnail_name} to {video_id}", flush=True)
        set_thumbnail(get_authenticated_service(), video_id, local_thumb)

    listing["thumbnail_set"] = True
    gh.write_json(repository, token, args.branch, listing_path, listing,
                  f"youtube: thumbnail for {args.build_id}")
    print(f"[upload] Thumbnail set: https://youtu.be/{video_id}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True, help="Folder name under cars/single-car-shorts")
    parser.add_argument("--branch", default=OUTPUT_BRANCH)
    parser.add_argument("--build-root", default=BUILD_ROOT)
    parser.add_argument("--force", action="store_true",
                        help="Upload even if this build already has a video id.")
    parser.add_argument(
        "--reschedule", action="store_true",
        help="Move the already-uploaded video's publish time to --publish-at.",
    )
    parser.add_argument(
        "--unschedule", action="store_true",
        help="Cancel the scheduled publish and leave the video private. The video stays "
             "on the channel; only the schedule goes.",
    )
    parser.add_argument(
        "--delete-video", action="store_true",
        help="Remove the video from the channel entirely. There is no undo, and the "
             "build keeps its files so it can be uploaded again.",
    )
    parser.add_argument(
        "--publish-now", action="store_true",
        help="Make the already-uploaded video public immediately, overriding whatever "
             "publish time it was scheduled for.",
    )
    parser.add_argument(
        "--update-metadata", action="store_true",
        help="Push this build's title, description, tags, category, language and AI "
             "declaration onto the video it already uploaded, without sending the video "
             "again. For fixing a listing after it went up.",
    )
    parser.add_argument(
        "--thumbnail-only", action="store_true",
        help="Attach the build's thumbnail to the video it already uploaded, without "
             "sending the video again. For a video that went up before the channel was "
             "verified, when YouTube refused the thumbnail at the time.",
    )
    parser.add_argument(
        "--publish-at", default="",
        help="ISO 8601 time with an offset, e.g. 2026-09-25T00:15:00+04:00. Given, the video "
             "goes up private and YouTube makes it public at that moment.",
    )
    args = parser.parse_args()

    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GH_PAT", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if not (repository and token):
        raise SystemExit("GITHUB_REPOSITORY and a token (GH_PAT or GITHUB_TOKEN) are required.")

    build_dir = f"{args.build_root}/{args.build_id}"
    listing_path = f"{build_dir}/upload.json"
    listing = gh.read_json(repository, token, args.branch, listing_path)
    if not listing:
        raise SystemExit(
            f"No upload.json at {listing_path} on {args.branch}. Builds made before "
            "this existed do not have one; re-render to get it."
        )
    if args.delete_video:
        delete_existing(repository, token, args, listing, listing_path)
        return
    if args.reschedule:
        reschedule_existing(args, listing)
        return
    if args.unschedule:
        unschedule_existing(listing)
        return
    if args.publish_now:
        publish_existing(listing)
        return
    if args.update_metadata:
        update_existing(repository, token, args, listing, build_dir)
        return
    if args.thumbnail_only:
        set_thumbnail_on_existing(repository, token, args, listing, listing_path, build_dir)
        return
    check_not_already_uploaded(listing, args.force)

    title = (listing.get("title") or "").strip()
    if not title:
        raise SystemExit("upload.json has no title. Refusing to publish an untitled video.")
    privacy = listing.get("privacy") or "private"
    if privacy not in ALLOWED_PRIVACY:
        raise SystemExit(f"privacy must be one of {ALLOWED_PRIVACY}, not {privacy!r}.")
    # A scheduled video has to go up private: YouTube ignores publishAt on
    # anything else, which would publish it the instant it finished
    # uploading rather than at the time that was asked for.
    publish_at = normalize_publish_at(args.publish_at or listing.get("publish_at") or "")
    if publish_at:
        privacy = "private"

    video_name = listing.get("video") or "single_car_short.mp4"
    source = raw_url(repository, args.branch, f"{build_dir}/{video_name}")
    print(f"[upload] Fetching {video_name}", flush=True)
    with tempfile.TemporaryDirectory() as workspace:
        local = Path(workspace) / video_name
        gh.download(source, local)
        size_mb = local.stat().st_size / (1 << 20)
        if size_mb < 0.1:
            raise SystemExit(f"{video_name} came back as {size_mb:.2f}MB -- that is not a video.")
        print(f"[upload] {size_mb:.1f}MB. Authenticating.", flush=True)

        # Imported here so a missing google library is an error from this
        # command rather than from importing the module.
        from youtube_tools.youtube_client import get_authenticated_service
        from youtube_tools.youtube_uploader import set_thumbnail, upload_video

        youtube = get_authenticated_service()
        when = f", public at {publish_at}" if publish_at else ""
        print(f"[upload] Sending as {privacy}{when}: {title}", flush=True)
        video_id = upload_video(
            youtube,
            file_path=local,
            title=title,
            description=listing.get("description") or "",
            tags=listing.get("tags") or [],
            privacy=privacy,
            publish_at=publish_at,
            contains_synthetic_media=bool(listing.get("contains_synthetic_media")),
            category_id=str(listing.get("category_id") or "2"),
            language=str(listing.get("language") or "en"),
        )

        if publish_at:
            listing["publish_at"] = publish_at

        thumbnail_name = listing.get("thumbnail")
        if thumbnail_name:
            local_thumb = Path(workspace) / thumbnail_name
            try:
                gh.download(raw_url(repository, args.branch, f"{build_dir}/{thumbnail_name}"),
                            local_thumb)
                set_thumbnail(youtube, video_id, local_thumb)
                listing["thumbnail_set"] = True
                print("[upload] Thumbnail set.", flush=True)
            except Exception as error:  # noqa: BLE001 - the video is already live
                # Custom thumbnails need a verified channel, which a new one
                # is not. Worth saying; never worth failing an upload that
                # has already succeeded.
                listing["thumbnail_set"] = False
                listing["thumbnail_error"] = str(error)[:300]
                print(f"[upload] Thumbnail refused ({error}). The video is up regardless.",
                      flush=True)

    listing.update({
        "video_id": video_id,
        "video_url": f"https://youtu.be/{video_id}",
        "uploaded_at": int(time.time()),
        "status": "uploaded",
        "privacy": privacy,
    })
    gh.write_json(repository, token, args.branch, listing_path, listing,
                  f"youtube: uploaded {args.build_id} as {video_id}")
    print(f"[upload] Done: https://youtu.be/{video_id} (private)", flush=True)
    print(f"[upload] Review it, then publish from YouTube Studio.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
