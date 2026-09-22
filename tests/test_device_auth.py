"""The re-authorisation flow, minus the network."""
import json
import time

import pytest

from youtube_tools import device_auth


def _fake_poster(responses):
    """Returns a _post_form stand-in that answers from a queue."""
    queue = list(responses)

    def poster(url, fields):
        return queue.pop(0)

    return poster


def test_pending_is_waited_through_and_the_token_is_returned(monkeypatch):
    """"authorization_pending" is the normal answer for as long as the
    person has not finished approving -- it is not a failure."""
    monkeypatch.setattr(device_auth, "_post_form", _fake_poster([
        {"error": "authorization_pending"},
        {"error": "authorization_pending"},
        {"refresh_token": "rt", "access_token": "at"},
    ]))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    token = device_auth.poll_for_token("id", "secret", "dc", interval=5,
                                       deadline=time.time() + 60)
    assert token["refresh_token"] == "rt"


def test_slow_down_backs_off_rather_than_giving_up(monkeypatch):
    waits = []
    monkeypatch.setattr(device_auth, "_post_form", _fake_poster([
        {"error": "slow_down"},
        {"refresh_token": "rt"},
    ]))
    monkeypatch.setattr(time, "sleep", lambda seconds: waits.append(seconds))
    device_auth.poll_for_token("id", "secret", "dc", interval=5,
                               deadline=time.time() + 60)
    assert waits[1] > waits[0]


def test_a_real_refusal_stops_immediately(monkeypatch):
    """access_denied means the person said no, or the app is still in
    Testing and they are not a listed tester. Polling on would just hang."""
    monkeypatch.setattr(device_auth, "_post_form", _fake_poster([
        {"error": "access_denied", "error_description": "denied"},
    ]))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(SystemExit, match="access_denied"):
        device_auth.poll_for_token("id", "secret", "dc", interval=5,
                                   deadline=time.time() + 60)


def test_an_expired_code_is_reported_rather_than_polled_forever(monkeypatch):
    monkeypatch.setattr(device_auth, "_post_form",
                        lambda url, fields: {"error": "authorization_pending"})
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(SystemExit, match="expired"):
        device_auth.poll_for_token("id", "secret", "dc", interval=5,
                                   deadline=time.time() - 1)


def test_the_code_file_never_carries_the_token(tmp_path, monkeypatch):
    """The dashboard reads this file over a public branch, so it may hold
    the user code -- useless without the channel's Google login -- and must
    never hold anything that grants access."""
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "csec")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_PAT", "pat")
    monkeypatch.setattr(device_auth, "request_device_code", lambda _cid: {
        "device_code": "dc", "user_code": "ABC-DEF", "expires_in": 600, "interval": 5,
    })
    monkeypatch.setattr(device_auth, "poll_for_token",
                        lambda *a, **k: {"refresh_token": "SECRET-VALUE",
                                         "refresh_token_expires_in": 0})
    stored = {}
    monkeypatch.setattr(device_auth, "store_secret",
                        lambda repo, pat, name, value: stored.update(name=name, value=value))
    code_file = tmp_path / "code.json"
    monkeypatch.setattr("sys.argv", ["device_auth", "--code-file", str(code_file)])

    device_auth.main()

    assert stored == {"name": "YOUTUBE_REFRESH_TOKEN", "value": "SECRET-VALUE"}
    written = code_file.read_text()
    assert "SECRET-VALUE" not in written
    # And once it is stored the code is cleared too, so a stale file cannot
    # be mistaken for a live prompt.
    assert json.loads(written)["status"] == "stored"
    assert "user_code" not in json.loads(written)


def test_the_code_is_published_before_the_wait_and_cleared_after(tmp_path, monkeypatch):
    """Ordering is the whole point. Run #1 of this workflow published the
    code with git, which switched the checkout to the output branch and
    killed the step that was meant to do the waiting -- and it spent three
    minutes doing it, against a code that expires in thirty. The publish has
    to land before polling starts, and the advert has to come down after,
    whatever the outcome."""
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "csec")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs")
    monkeypatch.setenv("GH_PAT", "pat")
    monkeypatch.setattr(device_auth, "request_device_code", lambda _cid: {
        "device_code": "dc", "user_code": "ABC-DEF", "expires_in": 600, "interval": 5,
    })

    events = []
    monkeypatch.setattr(device_auth, "publish_code",
                        lambda repo, token, branch, path, payload:
                        events.append(("publish", payload.get("status"), payload.get("user_code"))))

    def fake_poll(*a, **k):
        events.append(("poll", None, None))
        return {"refresh_token": "SECRET-VALUE", "refresh_token_expires_in": 0}

    monkeypatch.setattr(device_auth, "poll_for_token", fake_poll)
    monkeypatch.setattr(device_auth, "store_secret", lambda *a: events.append(("store", None, None)))
    monkeypatch.setattr("sys.argv", ["device_auth",
                                     "--code-file", str(tmp_path / "code.json"),
                                     "--publish-branch", "cars-output"])

    device_auth.main()

    kinds = [event[0] for event in events]
    assert kinds.index("publish") < kinds.index("poll"), kinds
    assert kinds[0] == "publish" and events[0][1] == "waiting"
    assert events[0][2] == "ABC-DEF"
    # Nothing published ever carries the token, at any stage.
    assert not any("SECRET-VALUE" in str(event) for event in events if event[0] == "publish")
    assert kinds[-1] == "publish" and events[-1][1] == "stored"
    # A successful run must never blink "idle" between the approval and the
    # store -- the dashboard reads that as the run having died.
    assert [e[1] for e in events if e[0] == "publish"] == ["waiting", "stored"]


def test_a_failed_approval_takes_the_code_down(tmp_path, monkeypatch):
    """A prompt left on screen for a code that can no longer be approved
    sends someone to Google to type something that will not work."""
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "csec")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs")
    monkeypatch.setattr(device_auth, "request_device_code", lambda _cid: {
        "device_code": "dc", "user_code": "ABC-DEF", "expires_in": 600, "interval": 5,
    })
    published = []
    monkeypatch.setattr(device_auth, "publish_code",
                        lambda repo, token, branch, path, payload: published.append(payload))
    monkeypatch.setattr(device_auth, "poll_for_token",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit("denied")))
    monkeypatch.setattr("sys.argv", ["device_auth",
                                     "--code-file", str(tmp_path / "code.json"),
                                     "--publish-branch", "cars-output"])

    with pytest.raises(SystemExit):
        device_auth.main()
    assert published[-1] == {"status": "idle"}
