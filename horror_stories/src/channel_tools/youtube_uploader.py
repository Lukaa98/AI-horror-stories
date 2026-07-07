from pathlib import Path

from googleapiclient.http import MediaFileUpload


def upload_video(
    youtube,
    file_path,
    title,
    description,
    tags,
    privacy="public",
    publish_at=None,
    is_for_kids=False,
    category_id="24",
    contains_synthetic_media=True,
):
    file_path = Path(file_path)
    request_body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": is_for_kids,
            "containsSyntheticMedia": contains_synthetic_media,
        },
    }
    if publish_at:
        request_body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(
        part="snippet,status",
        body=request_body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")

    video_id = response.get("id")
    print(f"Upload complete. Video ID: {video_id}")
    return video_id


def post_top_level_comment(youtube, video_id, text):
    response = youtube.commentThreads().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        },
    ).execute()
    return response["id"]
