from channel_tools.youtube_client import get_authenticated_service


def main():
    youtube = get_authenticated_service()

    channel = youtube.channels().list(
        part="contentDetails,snippet",
        mine=True,
    ).execute()["items"][0]

    uploads_playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    latest_item = youtube.playlistItems().list(
        part="contentDetails,snippet",
        playlistId=uploads_playlist,
        maxResults=1,
    ).execute()["items"][0]

    video_id = latest_item["contentDetails"]["videoId"]

    video = youtube.videos().list(
        part="snippet,statistics,status",
        id=video_id,
    ).execute()["items"][0]

    snippet = video.get("snippet", {})
    stats = video.get("statistics", {})
    status = video.get("status", {})

    print("LATEST VIDEO STATS")
    print(f"title: {snippet.get('title', '<no title>')}")
    print(f"video_id: {video_id}")
    print(f"published: {snippet.get('publishedAt', '?')}")
    print(f"privacy: {status.get('privacyStatus', '?')}")
    print(f"views: {stats.get('viewCount', '0')}")
    print(f"likes: {stats.get('likeCount', 'hidden')}")
    print(f"comments: {stats.get('commentCount', '0')}")


if __name__ == "__main__":
    main()
