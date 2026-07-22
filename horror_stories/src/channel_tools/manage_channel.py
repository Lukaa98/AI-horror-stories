import argparse
import json
from pathlib import Path

from googleapiclient.http import MediaFileUpload

from channel_tools.youtube_client import get_authenticated_service


def get_my_channel(youtube):
    response = youtube.channels().list(
        part="snippet,brandingSettings,contentDetails",
        mine=True,
    ).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("No authenticated YouTube channel found.")
    return items[0]


def update_channel_description(
    youtube,
    description,
    keywords=None,
    unsubscribed_trailer_video_id=None,
):
    channel = get_my_channel(youtube)
    channel["brandingSettings"] = channel.get("brandingSettings", {})
    channel["brandingSettings"]["channel"] = channel["brandingSettings"].get("channel", {})
    channel["brandingSettings"]["channel"]["description"] = description
    if keywords:
        channel["brandingSettings"]["channel"]["keywords"] = keywords
    if unsubscribed_trailer_video_id:
        channel["brandingSettings"]["channel"]["unsubscribedTrailer"] = unsubscribed_trailer_video_id

    youtube.channels().update(
        part="brandingSettings",
        body={
            "id": channel["id"],
            "brandingSettings": channel["brandingSettings"],
        },
    ).execute()
    print("Updated channel description/branding settings.")


def update_channel_banner(youtube, banner_path):
    banner_path = Path(banner_path)
    upload_response = youtube.channelBanners().insert(
        media_body=MediaFileUpload(str(banner_path), mimetype="image/png"),
    ).execute()
    banner_url = upload_response["url"]

    channel = get_my_channel(youtube)
    channel["brandingSettings"] = channel.get("brandingSettings", {})
    channel["brandingSettings"]["image"] = channel["brandingSettings"].get("image", {})
    channel["brandingSettings"]["image"]["bannerExternalUrl"] = banner_url

    youtube.channels().update(
        part="brandingSettings",
        body={
            "id": channel["id"],
            "brandingSettings": channel["brandingSettings"],
        },
    ).execute()
    print("Updated channel banner.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--description-file", help="Path to a text/markdown file containing channel description")
    parser.add_argument("--keywords", help="Comma-separated keywords")
    parser.add_argument("--banner", help="Path to banner image")
    parser.add_argument("--trailer-video-id", help="Optional unsubscribed trailer video id")
    parser.add_argument("--title", help="Manual-only channel title hint; YouTube API does not support renaming here")
    args = parser.parse_args()

    if args.title:
        print("Channel title/handle must be changed manually in YouTube Studio; the API helper will skip that step.")

    youtube = get_authenticated_service()
    if args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8").strip()
        update_channel_description(
            youtube,
            description=description,
            keywords=args.keywords,
            unsubscribed_trailer_video_id=args.trailer_video_id,
        )

    if args.banner:
        update_channel_banner(youtube, args.banner)

    if not args.description_file and not args.banner and not args.title:
        channel = get_my_channel(youtube)
        print(json.dumps(channel, indent=2))


if __name__ == "__main__":
    main()
