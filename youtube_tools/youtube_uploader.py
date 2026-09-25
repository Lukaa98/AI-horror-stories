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
    # 2 is Autos & Vehicles. The default was 24, Entertainment, which is
    # where a car channel's videos are least likely to be placed next to
    # other car videos.
    category_id="2",
    language="en",
    # YouTube's disclosure asks three specific questions: does it make a
    # real person appear to say or do something they did not, does it alter
    # footage of a real event or place, does it generate a realistic scene
    # that never occurred. A cartoon narrator is not a real person, the car
    # photos are real and unaltered beyond having their backgrounds cut out,
    # and nothing in the frame is passed off as real footage. So this is off
    # by default and a build turns it on if it ever needs to -- rather than
    # the uploader answering a compliance question on the channel's behalf,
    # which is what it used to do.
    contains_synthetic_media=False,
):
    file_path = Path(file_path)
    request_body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
            # Both, deliberately: defaultLanguage is what the title and
            # description are written in, defaultAudioLanguage what is
            # spoken. Left unset, YouTube guesses, and auto-captions and
            # translation are the things that get it wrong.
            **({"defaultLanguage": language, "defaultAudioLanguage": language}
               if language else {}),
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


def update_video(youtube, video_id, title, description, tags, category_id="2",
                 language="en", contains_synthetic_media=False):
    """Push a listing onto a video that is already on the channel.

    videos.update replaces whole parts rather than merging fields, so the
    snippet has to be sent complete -- a partial one silently wipes what it
    leaves out. That is also why this is driven from upload.json: the file
    is the whole listing, so nothing can go missing from it.
    """
    body = {
        "id": video_id,
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
            **({"defaultLanguage": language} if language else {}),
        },
        "status": {
            "containsSyntheticMedia": contains_synthetic_media,
        },
    }
    if language:
        body["snippet"]["defaultAudioLanguage"] = language
    return youtube.videos().update(part="snippet,status", body=body).execute()


def publish_now(youtube, video_id):
    """Make a scheduled video public immediately.

    The current status is read first and written back with only the privacy
    changed, because videos.update replaces a whole part: sending a status
    of just privacyStatus would blank the made-for-kids declaration, the
    licence and the AI answer along with the schedule.
    """
    found = youtube.videos().list(part="status", id=video_id).execute()
    items = found.get("items") or []
    if not items:
        raise RuntimeError(f"No video {video_id} on this channel.")
    status = dict(items[0].get("status") or {})
    status.pop("publishAt", None)
    status["privacyStatus"] = "public"
    # Read-only fields the API rejects on the way back in.
    for field in ("uploadStatus", "failureReason", "rejectionReason"):
        status.pop(field, None)
    return youtube.videos().update(
        part="status", body={"id": video_id, "status": status}).execute()


def unschedule(youtube, video_id):
    """Cancel a scheduled publish and leave the video private.

    Same care as publish_now: the current status is read and written back
    with only the schedule and privacy changed, because videos.update
    replaces a whole part and a bare status would blank the made-for-kids
    declaration, the licence and the AI answer.
    """
    found = youtube.videos().list(part="status", id=video_id).execute()
    items = found.get("items") or []
    if not items:
        raise RuntimeError(f"No video {video_id} on this channel.")
    status = dict(items[0].get("status") or {})
    status.pop("publishAt", None)
    status["privacyStatus"] = "private"
    for field in ("uploadStatus", "failureReason", "rejectionReason"):
        status.pop(field, None)
    return youtube.videos().update(
        part="status", body={"id": video_id, "status": status}).execute()


def delete_video(youtube, video_id):
    """Remove the video from the channel. There is no undo for this."""
    return youtube.videos().delete(id=video_id)
