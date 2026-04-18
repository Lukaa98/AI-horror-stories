import argparse
import json
from pathlib import Path

from channel_tools.youtube_client import get_authenticated_service
from channel_tools.youtube_uploader import post_top_level_comment, upload_video


def _default_description(storyboard):
    return (
        "AI-powered mystery short.\n\n"
        f"{storyboard['title']}\n\n"
        "Follow Last Player Leo as he explores dead servers, ghost lobbies, and hidden digital worlds.\n"
        "Vote in the comments to decide what happens next.\n\n"
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, help="Path to generated video folder")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--title")
    parser.add_argument("--description")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    storyboard = json.loads((run_dir / "storyboard.json").read_text(encoding="utf-8"))
    video_path = run_dir / "final_short.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"Missing final video: {video_path}")

    youtube = get_authenticated_service()
    title = args.title or storyboard["title"]
    description = args.description or _default_description(storyboard)
    tags = _default_tags()

    video_id = upload_video(
        youtube=youtube,
        file_path=video_path,
        title=title,
        description=description,
        tags=tags,
        privacy=args.privacy,
        is_for_kids=False,
    )

    post_top_level_comment(
        youtube,
        video_id,
        "This channel is AI-powered. Vote below to decide Leo's next move.",
    )
    for comment in _vote_comments():
        post_top_level_comment(youtube, video_id, comment)

    print(f"Published video: https://www.youtube.com/watch?v={video_id}")


if __name__ == "__main__":
    main()
