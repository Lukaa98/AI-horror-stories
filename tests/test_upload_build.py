"""The refusals that stop a bad upload, and the record kept after a good one."""
import json
import sys
import types

import pytest

from youtube_tools import upload_build


def test_a_build_that_already_has_a_video_id_is_refused():
    """YouTube accepts the same file twice and hands back two ids, leaving a
    duplicate on the channel and upload.json pointing at one of them. Re-running
    a workflow is a normal thing to do, so refusing has to be the default."""
    with pytest.raises(SystemExit, match="already uploaded"):
        upload_build.check_not_already_uploaded({"video_id": "abc123"}, force=False)
    # --force is the deliberate override.
    upload_build.check_not_already_uploaded({"video_id": "abc123"}, force=True)
    # A build that has never been uploaded passes either way.
    upload_build.check_not_already_uploaded({"status": "ready"}, force=False)
    upload_build.check_not_already_uploaded(None, force=False)


def _run(monkeypatch, listing, uploaded, written, **extra):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_PAT", "pat")
    monkeypatch.setattr(upload_build.gh, "read_json", lambda *a: listing)
    monkeypatch.setattr(upload_build.gh, "write_json",
                        lambda repo, token, branch, path, payload, message:
                        written.update(path=path, payload=payload))

    def fake_download(url, destination, **kwargs):
        destination.write_bytes(b"x" * (2 << 20))
        return destination

    monkeypatch.setattr(upload_build.gh, "download", fake_download)

    client = types.ModuleType("youtube_tools.youtube_client")
    client.get_authenticated_service = lambda: "service"
    uploader = types.ModuleType("youtube_tools.youtube_uploader")

    def fake_upload(youtube, file_path, title, description, tags, privacy="public",
                    publish_at="", contains_synthetic_media=False,
                    category_id="2", language="en"):
        uploaded.update(title=title, description=description, tags=tags, privacy=privacy,
                        publish_at=publish_at,
                        contains_synthetic_media=contains_synthetic_media,
                        category_id=category_id, language=language)
        return "vid123"

    uploader.upload_video = fake_upload
    uploader.set_thumbnail = lambda youtube, video_id, file_path: video_id
    monkeypatch.setitem(sys.modules, "youtube_tools.youtube_client", client)
    monkeypatch.setitem(sys.modules, "youtube_tools.youtube_uploader", uploader)
    monkeypatch.setattr("sys.argv", ["upload_build", "--build-id", "a-build", *extra.get("argv", [])])
    return upload_build.main()


def test_a_good_upload_sends_the_reviewed_listing_and_records_the_id(monkeypatch):
    """The point of writing upload.json at build time is that what goes up is
    the text someone already read -- not something regenerated at publish."""
    listing = {"title": "A Title 🔥", "description": "Body", "tags": ["Ferrari"],
               "video": "single_car_short.mp4", "privacy": "private", "status": "ready"}
    uploaded, written = {}, {}
    _run(monkeypatch, listing, uploaded, written)

    assert uploaded["title"] == "A Title 🔥"
    assert uploaded["tags"] == ["Ferrari"]
    assert uploaded["privacy"] == "private"
    assert written["payload"]["video_id"] == "vid123"
    assert written["payload"]["video_url"] == "https://youtu.be/vid123"
    assert written["payload"]["status"] == "uploaded"
    # Recorded where the dashboard will look for it.
    assert written["path"].endswith("a-build/upload.json")


def test_an_untitled_build_is_refused(monkeypatch):
    """An upload with no title becomes a video called "single_car_short" on a
    public channel."""
    with pytest.raises(SystemExit, match="no title"):
        _run(monkeypatch, {"title": "  ", "video": "x.mp4"}, {}, {})


def test_a_missing_upload_json_says_why(monkeypatch):
    with pytest.raises(SystemExit, match="re-render"):
        _run(monkeypatch, None, {}, {})


def test_a_privacy_the_api_would_reject_is_caught_here(monkeypatch):
    with pytest.raises(SystemExit, match="privacy must be"):
        _run(monkeypatch, {"title": "T", "privacy": "friends", "video": "x.mp4"}, {}, {})


def test_a_video_that_did_not_download_is_not_uploaded(monkeypatch):
    """A 404 from the raw URL returns a short error page, not an MP4. Sending
    it would put a broken video on the channel."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_PAT", "pat")
    monkeypatch.setattr(upload_build.gh, "read_json",
                        lambda *a: {"title": "T", "video": "x.mp4", "privacy": "private"})
    monkeypatch.setattr(upload_build.gh, "download",
                        lambda url, destination, **k: destination.write_bytes(b"404: Not Found") or destination)
    monkeypatch.setattr("sys.argv", ["upload_build", "--build-id", "a-build"])
    with pytest.raises(SystemExit, match="not a video"):
        upload_build.main()


def test_the_upload_workflow_passes_its_inputs_through_the_environment():
    """${{ }} is substituted as literal text before bash parses the line, so
    an input interpolated inline is read as shell syntax. That cost run #201
    seventeen minutes when a JSON value lost its quotes; a build id is a
    smaller target but the same mistake."""
    from pathlib import Path

    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/youtube-upload.yml"
    text = workflow.read_text()
    run_blocks = text.split("run: |")[1:]
    for block in run_blocks:
        assert "${{" not in block.split("\n      - ")[0], \
            "workflow inputs must reach the script through env vars, not inline"
    assert "BUILD_ID: ${{ inputs.build_id }}" in text


def test_a_refused_thumbnail_does_not_fail_an_upload_that_worked(monkeypatch):
    """Custom thumbnails need a verified channel, which a new one is not.
    By the time this runs the video is already live, so a refusal is worth
    recording and never worth turning a successful upload into a failure."""
    listing = {"title": "T", "video": "v.mp4", "thumbnail": "thumbnail.jpg",
               "privacy": "private", "tags": []}
    uploaded, written = {}, {}
    monkeypatch.setattr(upload_build.gh, "read_json", lambda *a: listing)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_PAT", "pat")
    monkeypatch.setattr(upload_build.gh, "write_json",
                        lambda repo, token, branch, path, payload, message:
                        written.update(payload=payload))

    def fake_download(url, destination, **kwargs):
        destination.write_bytes(b"x" * (2 << 20))
        return destination

    monkeypatch.setattr(upload_build.gh, "download", fake_download)

    client = types.ModuleType("youtube_tools.youtube_client")
    client.get_authenticated_service = lambda: "service"
    uploader = types.ModuleType("youtube_tools.youtube_uploader")
    uploader.upload_video = lambda youtube, **kwargs: "vid123"

    def refuse(youtube, video_id, file_path):
        raise RuntimeError("thumbnailNotVerified")

    uploader.set_thumbnail = refuse
    monkeypatch.setitem(sys.modules, "youtube_tools.youtube_client", client)
    monkeypatch.setitem(sys.modules, "youtube_tools.youtube_uploader", uploader)
    monkeypatch.setattr("sys.argv", ["upload_build", "--build-id", "a-build"])

    assert upload_build.main() == 0
    assert written["payload"]["video_id"] == "vid123"
    assert written["payload"]["thumbnail_set"] is False
    assert "thumbnailNotVerified" in written["payload"]["thumbnail_error"]


def test_a_scheduled_upload_must_name_an_instant():
    """Midnight is a different moment in every timezone, and the machine
    that runs the upload is a GitHub runner in UTC -- not the one the time
    was typed on."""
    import pytest

    assert upload_build.normalize_publish_at("2099-09-25T00:15:00+04:00") == "2099-09-24T20:15:00Z"
    assert upload_build.normalize_publish_at("2099-09-24T20:15:00Z") == "2099-09-24T20:15:00Z"
    assert upload_build.normalize_publish_at("") == ""

    with pytest.raises(SystemExit, match="timezone offset"):
        upload_build.normalize_publish_at("2099-09-25T00:15:00")
    with pytest.raises(SystemExit, match="ISO 8601"):
        upload_build.normalize_publish_at("tomorrow")
    # Caught before the video is uploaded rather than after.
    with pytest.raises(SystemExit, match="in the past"):
        upload_build.normalize_publish_at("2020-01-01T00:00:00+00:00")


def test_a_scheduled_video_goes_up_private():
    """YouTube ignores publishAt on anything but a private video -- set
    public with a publish time, it goes live the instant the upload
    finishes, which is the one outcome scheduling exists to avoid."""
    from pathlib import Path

    source = Path(upload_build.__file__).read_text()
    assert 'if publish_at:\n        privacy = "private"' in source
    assert "publish_at=publish_at," in source


def test_the_upload_workflow_is_dispatchable():
    """workflow_dispatch resolves a workflow by filename on the DEFAULT
    branch -- the ref only chooses which copy runs. youtube-upload.yml
    lived on v11 alone, so every upload came back 404 before it reached a
    runner, and the button had never worked since the day it was built."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert (root / ".github/workflows/youtube-upload.yml").is_file()
    listed = subprocess.run(
        ["git", "ls-tree", "--name-only", "origin/main", ".github/workflows/"],
        cwd=root, capture_output=True, text=True,
    )
    if listed.returncode == 0 and listed.stdout.strip():
        assert "youtube-upload.yml" in listed.stdout, \
            "the workflow has to be on the default branch to be dispatchable at all"


def test_the_ai_disclosure_is_not_answered_on_the_channel_s_behalf():
    """The uploader used to declare synthetic media on every video without
    asking. YouTube's disclosure asks three specific things -- a real person
    made to say something, real footage altered, a realistic scene that
    never happened -- and a cartoon narrator over unaltered car photos is
    none of them. It is the channel's answer to give, not the uploader's."""
    from pathlib import Path

    uploader = (Path(__file__).resolve().parents[1]
                / "youtube_tools/youtube_uploader.py").read_text()
    assert "contains_synthetic_media=False," in uploader

    source = Path(upload_build.__file__).read_text()
    assert 'contains_synthetic_media=bool(listing.get("contains_synthetic_media"))' in source, \
        "a build can still turn it on for itself"


def test_a_thumbnail_can_be_attached_without_uploading_twice():
    """YouTube refuses custom thumbnails from an unverified channel, so a
    video that went up before verification keeps a frame picked out of
    itself. Re-running the upload would publish a duplicate, so the
    thumbnail is attachable on its own."""
    from pathlib import Path

    source = Path(upload_build.__file__).read_text()
    assert "def set_thumbnail_on_existing" in source
    assert "--thumbnail-only" in source
    # It refuses rather than guessing when there is nothing to attach to.
    assert 'raise SystemExit("This build has no video_id' in source

    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/youtube-upload.yml").read_text()
    assert "thumbnail_only:" in workflow and "--thumbnail-only" in workflow


def test_the_video_goes_up_as_a_car_video_in_a_stated_language():
    """It was going up as Entertainment with no language declared -- the
    category where a car channel is least likely to sit beside other car
    videos, and no language means YouTube guesses, which is how auto-
    captions and translations go wrong."""
    import inspect
    from youtube_tools import youtube_uploader

    signature = inspect.signature(youtube_uploader.upload_video)
    assert signature.parameters["category_id"].default == "2", "Autos & Vehicles"
    assert signature.parameters["language"].default == "en"

    from pathlib import Path
    source = Path(upload_build.__file__).read_text()
    assert 'category_id=str(listing.get("category_id") or "2")' in source
    assert 'language=str(listing.get("language") or "en")' in source


def test_a_published_listing_can_be_corrected_without_re_uploading():
    """The first video went up as Entertainment, with no language, and the
    only way to fix that was by hand in Studio. videos.update can apply the
    build's own listing to a video that is already on the channel."""
    import inspect
    from pathlib import Path
    from youtube_tools import youtube_uploader

    assert hasattr(youtube_uploader, "update_video")
    signature = inspect.signature(youtube_uploader.update_video)
    assert signature.parameters["category_id"].default == "2"
    assert signature.parameters["language"].default == "en"

    # videos.update replaces whole parts rather than merging fields, so a
    # partial snippet wipes what it leaves out.
    body = Path(youtube_uploader.__file__).read_text()
    block = body[body.index("def update_video"):]
    for field in ('"title"', '"description"', '"tags"', '"categoryId"'):
        assert field in block, f"a partial snippet would blank {field}"

    source = Path(upload_build.__file__).read_text()
    assert "def update_existing" in source
    assert 'raise SystemExit("upload.json has no title' in source, \
        "refuse rather than blanking a published title"

    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/youtube-upload.yml").read_text()
    assert "update_metadata:" in workflow and "--update-metadata" in workflow


def test_publishing_now_keeps_the_rest_of_the_video_s_status():
    """videos.update replaces a whole part, so writing a status of just
    privacyStatus would blank the made-for-kids declaration, the licence and
    the AI answer along with the schedule. The current status is read first
    and written back with only the privacy changed."""
    from pathlib import Path
    from youtube_tools import youtube_uploader

    block = Path(youtube_uploader.__file__).read_text()
    block = block[block.index("def publish_now"):]
    assert 'part="status", id=video_id' in block, "read the status before replacing it"
    assert 'status.pop("publishAt", None)' in block
    assert 'status["privacyStatus"] = "public"' in block
    # The API refuses these back, so they have to come out.
    assert '"uploadStatus"' in block and '"rejectionReason"' in block

    source = Path(upload_build.__file__).read_text()
    assert "def publish_existing" in source
    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/youtube-upload.yml").read_text()
    assert "publish_now:" in workflow and "--publish-now" in workflow


def test_a_scheduled_video_can_be_unscheduled_without_publishing_it():
    """Changing your mind about a schedule should not mean publishing it or
    deleting it. The video stays on the channel, private, with no date."""
    from pathlib import Path
    from youtube_tools import youtube_uploader

    block = Path(youtube_uploader.__file__).read_text()
    block = block[block.index("def unschedule"):]
    assert 'part="status", id=video_id' in block, "read the status before replacing it"
    assert 'status.pop("publishAt", None)' in block
    assert 'status["privacyStatus"] = "private"' in block

    source = Path(upload_build.__file__).read_text()
    assert "def unschedule_existing" in source


def test_deleting_a_video_keeps_the_build_that_made_it():
    """The point of deleting is to undo the upload, not the work. The
    video, thumbnail and listing stay on the output branch so the same
    build can go up again."""
    from pathlib import Path

    source = Path(upload_build.__file__).read_text()
    block = source[source.index("def delete_existing"):]
    assert "for field in UPLOAD_RECORD_FIELDS" in block, \
        "the delete has to clear the whole upload record, not a hand-listed part of it"
    assert 'listing["deleted_video_id"] = video_id' in block, \
        "a deleted upload is not the same as one that never happened"
    assert 'raise SystemExit("This build has no video_id -- there is nothing to delete' in source

    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/youtube-upload.yml").read_text()
    for flag in ("unschedule:", "delete_video:", "--unschedule", "--delete-video"):
        assert flag in workflow


def test_a_deleted_build_keeps_nothing_that_describes_the_deleted_video(monkeypatch):
    """A half-cleared record is worse than none: the dashboard offers an
    upload while the file still says the build is "uploaded" at a URL that
    404s. Whatever the upload writes, the delete has to take back."""
    before = {"title": "A Title", "description": "Body", "tags": [],
              "video": "single_car_short.mp4", "privacy": "private", "status": "ready"}
    written = {}
    _run(monkeypatch, dict(before), {}, written,
         argv=["--publish-at", "2030-01-01T12:15:00-05:00"])

    after_upload = written["payload"]
    added = set(after_upload) - set(before)
    changed = {k for k in before if after_upload.get(k) != before[k]}
    assert added, "the upload records nothing, so this test is watching the wrong thing"
    # Every trace the upload leaves is a trace the delete knows to remove.
    assert (added | changed) - {"privacy"} <= set(upload_build.UPLOAD_RECORD_FIELDS)

    listing = dict(after_upload)
    for field in upload_build.UPLOAD_RECORD_FIELDS:
        listing.pop(field, None)
    assert "vid123" not in json.dumps(listing), \
        "something still names the video that was just deleted"
    assert listing.get("status") != "uploaded"


def test_a_schedule_can_be_moved_rather_than_only_cancelled():
    """Changing when a video goes out should not mean cancelling it and
    scheduling a new upload."""
    from pathlib import Path
    from youtube_tools import youtube_uploader

    block = Path(youtube_uploader.__file__).read_text()
    block = block[block.index("def set_publish_time"):]
    assert 'status["privacyStatus"] = "private"' in block, \
        "a schedule means nothing on a public video"
    assert 'status["publishAt"] = publish_at' in block

    source = Path(upload_build.__file__).read_text()
    assert "def reschedule_existing" in source
    assert 'raise SystemExit("--reschedule needs --publish-at' in source

    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/youtube-upload.yml").read_text()
    assert "reschedule:" in workflow and "--reschedule" in workflow
