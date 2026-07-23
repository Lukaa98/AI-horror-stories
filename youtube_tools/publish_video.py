"""Upload a rendered car Short with explicit, channel-neutral metadata."""

import argparse
from datetime import datetime
from pathlib import Path

from youtube_tools.youtube_client import get_authenticated_service
from youtube_tools.youtube_uploader import upload_video


def normalize_publish_at(raw_value):
    if not raw_value:
        return None
    normalized = raw_value.strip()
    if normalized.endswith("Z"):
        return normalized
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("--publish-at must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError("--publish-at must include a timezone offset")
    return parsed.isoformat().replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser(description="Upload a rendered car video to YouTube.")
    parser.add_argument("--video", required=True, help="Path to the MP4 to upload")
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--description-file")
    parser.add_argument("--tags", default="cars,car rankings,shorts")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--publish-at", help="ISO 8601 publish time including a timezone")
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.is_file():
        raise FileNotFoundError(f"Missing video: {video_path}")
    description = args.description
    if args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8").strip()
    publish_at = normalize_publish_at(args.publish_at)
    privacy = "private" if publish_at else args.privacy

    video_id = upload_video(
        youtube=get_authenticated_service(),
        file_path=video_path,
        title=args.title,
        description=description,
        tags=[tag.strip() for tag in args.tags.split(",") if tag.strip()],
        privacy=privacy,
        publish_at=publish_at,
        is_for_kids=False,
        category_id="2",
    )
    print(f"Published video: https://www.youtube.com/watch?v={video_id}")


if __name__ == "__main__":
    main()
