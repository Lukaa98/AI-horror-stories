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
    # --state-file defaults to the working directory, so without this the
    # test drops a device-code file into the repo root -- which is exactly
    # how one got committed.
    state_file = tmp_path / "state.json"
    monkeypatch.setattr("sys.argv", ["device_auth", "--code-file", str(code_file),
                                     "--state-file", str(state_file)])

    device_auth.main()

    assert stored == {"name": "YOUTUBE_REFRESH_TOKEN", "value": "SECRET-VALUE"}
    written = code_file.read_text()
    assert "SECRET-VALUE" not in written
    # And once it is stored the code is cleared too, so a stale file cannot
    # be mistaken for a live prompt.
    assert json.loads(written)["status"] == "stored"
    assert "user_code" not in json.loads(written)
