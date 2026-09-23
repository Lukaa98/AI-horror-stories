from pathlib import Path

# Imported inside the function, not here: this module gets imported by
# publish_video, which gets imported by its tests, and a top-level import
# meant those tests could not be collected at all without the google
# libraries present.


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

    from googleapiclient.http import MediaFileUpload

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


def set_thumbnail(youtube, video_id, file_path):
    """Attach a custom thumbnail to an already-uploaded video.

    Separate from the upload because it is a separate API call and a
    separate failure: the video is live by the time this runs, so a refused
    thumbnail is worth reporting but never worth losing the upload over.
    Custom thumbnails need a verified channel, which is the usual reason
    this is refused on a new one.
    """
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(file_path), mimetype="image/jpeg")
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    return video_id
