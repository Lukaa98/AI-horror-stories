import argparse
import json
from pathlib import Path

from channel_tools.youtube_client import get_authenticated_service


def fetch_video_stats(youtube, video_id):
    response = youtube.videos().list(
        part="snippet,statistics,status,contentDetails,processingDetails",
        id=video_id,
    ).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"Video not found: {video_id}")

    video = items[0]
    snippet = video.get("snippet", {})
    statistics = video.get("statistics", {})
    status = video.get("status", {})
    content_details = video.get("contentDetails", {})
    processing_details = video.get("processingDetails", {})

    return {
        "video_id": video_id,
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "published_at": snippet.get("publishedAt"),
        "channel_id": snippet.get("channelId"),
        "channel_title": snippet.get("channelTitle"),
        "category_id": snippet.get("categoryId"),
        "tags": snippet.get("tags", []),
        "default_language": snippet.get("defaultLanguage"),
        "default_audio_language": snippet.get("defaultAudioLanguage"),
        "privacy": status.get("privacyStatus"),
        "upload_status": status.get("uploadStatus"),
        "license": status.get("license"),
        "embeddable": status.get("embeddable"),
        "public_stats_viewable": status.get("publicStatsViewable"),
        "made_for_kids": status.get("madeForKids"),
        "self_declared_made_for_kids": status.get("selfDeclaredMadeForKids"),
        "duration": content_details.get("duration"),
        "definition": content_details.get("definition"),
        "dimension": content_details.get("dimension"),
        "caption": content_details.get("caption"),
        "projection": content_details.get("projection"),
        "views": int(statistics.get("viewCount", 0)),
        "likes": int(statistics.get("likeCount", 0)) if statistics.get("likeCount") else None,
        "favorites": int(statistics.get("favoriteCount", 0)),
        "comments": int(statistics.get("commentCount", 0)),
        "processing_status": processing_details.get("processingStatus"),
        "processing_failure_reason": processing_details.get("processingFailureReason"),
        "processing_issues_availability": processing_details.get("processingIssuesAvailability"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    youtube = get_authenticated_service()
    payload = fetch_video_stats(youtube, args.video_id)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
