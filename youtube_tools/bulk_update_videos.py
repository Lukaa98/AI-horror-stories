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
            yield item
        request = youtube.search().list_next(request, response)


def get_video_details(youtube, video_id):
    response = youtube.videos().list(part="snippet,status", id=video_id).execute()
    items = response.get("items", [])
    return items[0] if items else None


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
    parser.add_argument("--query", default="fortnite", help="Case-insensitive text to match against title/description/tags")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    youtube = get_authenticated_service()
    query = args.query.lower()
    matches = []

    for item in iter_my_uploads(youtube):
        video_id = item["id"]["videoId"]
        details = get_video_details(youtube, video_id)
        if not details:
            continue

        snippet = details["snippet"]
        haystack = " ".join(
            [
                snippet.get("title", ""),
                snippet.get("description", ""),
                " ".join(snippet.get("tags", [])),
            ]
        ).lower()
        if query in haystack:
            matches.append(details)

    for video in matches:
        print(f"Match: {video['id']} | {video['snippet'].get('title')}")
        if not args.dry_run:
            update_video_privacy(youtube, video, args.privacy)
            print(f"  updated to {args.privacy}")

    if not matches:
        print("No matching videos found.")


if __name__ == "__main__":
    main()
