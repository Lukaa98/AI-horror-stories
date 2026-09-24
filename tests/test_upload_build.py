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
                    publish_at=""):
        uploaded.update(title=title, description=description, tags=tags, privacy=privacy,
                        publish_at=publish_at)
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
