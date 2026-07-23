import argparse

from youtube_tools.youtube_client import get_authenticated_service


def iter_my_uploads(youtube):
    request = youtube.search().list(
        part="snippet",
        forMine=True,
        type="video",
        maxResults=50,
        order="date",
    )
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            yield item["id"]["videoId"]
        request = youtube.search().list_next(request, response)


def batched(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def fetch_video_details(youtube, video_ids):
    details = []
    for batch in batched(video_ids, 50):
        response = youtube.videos().list(
            part="snippet,status,statistics",
            id=",".join(batch),
            maxResults=50,
        ).execute()
        details.extend(response.get("items", []))
    return details


def update_video_privacy(youtube, video, privacy_status):
    youtube.videos().update(
        part="status",
        body={
            "id": video["id"],
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": video["status"].get("selfDeclaredMadeForKids", False),
            },
        },
    ).execute()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", type=int, default=10, help="Number of most-viewed public videos to keep public")
    parser.add_argument("--privacy", default="unlisted", choices=["private", "unlisted", "public"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    youtube = get_authenticated_service()
    video_ids = list(iter_my_uploads(youtube))
    videos = fetch_video_details(youtube, video_ids)

    public_videos = [v for v in videos if v["status"].get("privacyStatus") == "public"]
    public_videos.sort(
        key=lambda v: int(v.get("statistics", {}).get("viewCount", 0)),
        reverse=True,
    )

    keep_ids = {video["id"] for video in public_videos[: args.keep]}
    to_change = [video for video in public_videos if video["id"] not in keep_ids]

    print(f"Public videos found: {len(public_videos)}")
    print(f"Keeping top {min(args.keep, len(public_videos))} public videos:")
    for video in public_videos[: args.keep]:
        print(
            f"  KEEP {video['id']} | {video['snippet'].get('title')} | {video.get('statistics', {}).get('viewCount', '0')} views"
        )

    print(f"\nVideos to update to {args.privacy}: {len(to_change)}")
    for video in to_change:
        print(
            f"  CHANGE {video['id']} | {video['snippet'].get('title')} | {video.get('statistics', {}).get('viewCount', '0')} views"
        )
        if not args.dry_run:
            update_video_privacy(youtube, video, args.privacy)


if __name__ == "__main__":
    main()
