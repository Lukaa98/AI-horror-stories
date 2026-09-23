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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True, help="Folder name under cars/single-car-shorts")
    parser.add_argument("--branch", default=OUTPUT_BRANCH)
    parser.add_argument("--build-root", default=BUILD_ROOT)
    parser.add_argument("--force", action="store_true",
                        help="Upload even if this build already has a video id.")
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
    check_not_already_uploaded(listing, args.force)

    title = (listing.get("title") or "").strip()
    if not title:
        raise SystemExit("upload.json has no title. Refusing to publish an untitled video.")
    privacy = listing.get("privacy") or "private"
    if privacy not in ALLOWED_PRIVACY:
        raise SystemExit(f"privacy must be one of {ALLOWED_PRIVACY}, not {privacy!r}.")

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
        from youtube_tools.youtube_uploader import upload_video

        youtube = get_authenticated_service()
        print(f"[upload] Sending as {privacy}: {title}", flush=True)
        video_id = upload_video(
            youtube,
            file_path=local,
            title=title,
            description=listing.get("description") or "",
            tags=listing.get("tags") or [],
            privacy=privacy,
        )

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
