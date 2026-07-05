import argparse
import json
from pathlib import Path

from channel_tools.youtube_client import get_authenticated_service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    youtube = get_authenticated_service()
    response = youtube.videos().list(
        part="snippet,statistics,status,processingDetails",
        id=args.video_id,
    ).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"Video not found: {args.video_id}")

    video = items[0]
    payload = {
        "video_id": args.video_id,
        "title": video.get("snippet", {}).get("title"),
        "published_at": video.get("snippet", {}).get("publishedAt"),
        "privacy": video.get("status", {}).get("privacyStatus"),
        "upload_status": video.get("status", {}).get("uploadStatus"),
        "views": int(video.get("statistics", {}).get("viewCount", 0)),
        "likes": int(video.get("statistics", {}).get("likeCount", 0)) if video.get("statistics", {}).get("likeCount") else None,
        "comments": int(video.get("statistics", {}).get("commentCount", 0)),
        "processing_status": video.get("processingDetails", {}).get("processingStatus"),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
