import argparse
import json
from datetime import datetime
from pathlib import Path

from channel_tools.youtube_client import get_authenticated_service
from channel_tools.youtube_uploader import post_top_level_comment, upload_video


def _default_description(storyboard):
    return (
        "AI-powered mystery short.\n\n"
        f"{storyboard['title']}\n\n"
        "Follow Last Player Leo as he explores dead servers, ghost lobbies, and hidden digital worlds.\n"
        "New episode next week.\n\n"
        "#shorts #aistory #lastplayerleo #mystery #gaming"
    )


def _default_tags():
    return [
        "shorts",
        "ai story",
        "last player leo",
        "gaming mystery",
        "ghost server",
        "digital mystery",
        "interactive story",
    ]


def _vote_comments():
    return [
        "A. Leo follows the signal.",
        "B. Leo opens the locked server door.",
        "C. Leo trusts the ghost in chat.",
    ]


def _normalize_publish_at(raw_value):
    if not raw_value:
        return None

    normalized = raw_value.strip()
    if normalized.endswith("Z"):
        return normalized

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "--publish-at must be an ISO 8601 datetime such as 2026-04-24T16:20:00Z "
            "or 2026-04-24T12:20:00-04:00"
        ) from exc

    if parsed.tzinfo is None:
        raise ValueError(
            "--publish-at must include a timezone offset. Example: 2026-04-24T12:20:00-04:00"
        )

    return parsed.isoformat().replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, help="Path to generated video folder")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--publish-at", help="Schedule publish time in ISO 8601, e.g. 2026-04-24T12:20:00-04:00")
    parser.add_argument("--title")
    parser.add_argument("--description")
    parser.add_argument("--title-file")
    parser.add_argument("--description-file")
    parser.add_argument("--post-comments", action="store_true")
    parser.add_argument("--metadata-out", help="Optional JSON path to write upload result metadata")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    storyboard = json.loads((run_dir / "storyboard.json").read_text(encoding="utf-8"))
    video_path = run_dir / "final_short.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"Missing final video: {video_path}")

    youtube = get_authenticated_service()
    title = args.title
    if args.title_file:
        title = Path(args.title_file).read_text(encoding="utf-8").strip()
    if not title:
        title = storyboard["title"]

    description = args.description
    if args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8").strip()
    if not description:
        description = _default_description(storyboard)
    tags = _default_tags()
    publish_at = _normalize_publish_at(args.publish_at)

    privacy = args.privacy
    if publish_at and privacy == "public":
        privacy = "private"

    video_id = upload_video(
        youtube=youtube,
        file_path=video_path,
        title=title,
        description=description,
        tags=tags,
        privacy=privacy,
        publish_at=publish_at,
        is_for_kids=False,
    )

    if args.post_comments and publish_at:
        print("Skipping auto-comments for scheduled uploads; post them after the video goes public.")
    elif args.post_comments:
        try:
            post_top_level_comment(
                youtube,
                video_id,
                "This channel is AI-powered. Vote below to decide Leo's next move.",
            )
            for comment in _vote_comments():
                post_top_level_comment(youtube, video_id, comment)
        except Exception as exc:
            print(f"Warning: upload succeeded, but posting comments failed: {exc}")

    if args.metadata_out:
        metadata_path = Path(args.metadata_out)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(
                {
                    "video_id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "title": title,
                    "privacy": privacy,
                    "publish_at": publish_at,
                    "run_dir": str(run_dir),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"Published video: https://www.youtube.com/watch?v={video_id}")


if __name__ == "__main__":
    main()
