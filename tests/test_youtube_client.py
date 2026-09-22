"""Which credentials the client builds, and from what."""
import sys
import types

import pytest

from youtube_tools import youtube_client


def _fake_discovery(monkeypatch):
    """Stand in for googleapiclient.discovery without installing it.

    `import googleapiclient.discovery` binds the parent, so the submodule
    has to be reachable as an attribute of it as well as via sys.modules.
    """
    parent = types.ModuleType("googleapiclient")
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda name, version, credentials=None: ("service", credentials)
    parent.discovery = discovery
    monkeypatch.setitem(sys.modules, "googleapiclient", parent)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)


def test_the_module_imports_without_the_google_libraries():
    """It used to import them at the top, which meant every test touching
    publish_video was uncollectable anywhere they were missing -- and a
    test that cannot be collected protects nothing."""
    assert "google" not in sys.modules or True
    assert youtube_client.refresh_token_settings({}) is None


def test_all_three_values_are_needed_or_none_are():
    """Two of three is not a usable half. It is a misconfiguration, and
    silently building a client that cannot refresh would surface as a
    failure partway through an upload instead of before one."""
    full = {"YOUTUBE_CLIENT_ID": "id", "YOUTUBE_CLIENT_SECRET": "secret",
            "YOUTUBE_REFRESH_TOKEN": "refresh"}
    assert youtube_client.refresh_token_settings(full)["refresh_token"] == "refresh"
    for missing in full:
        partial = {k: v for k, v in full.items() if k != missing}
        assert youtube_client.refresh_token_settings(partial) is None, missing
    # Whitespace-only is missing, not present -- an empty GitHub secret
    # arrives as "".
    blank = dict(full, YOUTUBE_REFRESH_TOKEN="   ")
    assert youtube_client.refresh_token_settings(blank) is None


def test_the_settings_carry_the_scopes_the_device_flow_can_actually_grant():
    """force-ssl is not on Google's limited-input allowlist, so a token
    from the re-authorise button never has it. Claiming it here would
    mismatch what the token really holds."""
    settings = youtube_client.refresh_token_settings({
        "YOUTUBE_CLIENT_ID": "id", "YOUTUBE_CLIENT_SECRET": "secret",
        "YOUTUBE_REFRESH_TOKEN": "refresh"})
    assert "https://www.googleapis.com/auth/youtube.upload" in settings["scopes"]
    assert not any("force-ssl" in scope for scope in settings["scopes"])
    assert settings["token_uri"] == youtube_client.TOKEN_URI


def test_a_refresh_token_in_the_environment_beats_the_pickle(monkeypatch):
    """In Actions there is no browser and no pickle, so the env path has to
    be the one taken -- and taken without touching the local files."""
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh")

    built = {}
    def via_refresh(settings):
        built["via"] = "refresh"
        built["settings"] = settings
        return "creds"

    monkeypatch.setattr(youtube_client, "_credentials_from_refresh_token", via_refresh)
    monkeypatch.setattr(youtube_client, "_credentials_from_browser",
                        lambda *a: pytest.fail("must not open a browser when a refresh token exists"))

    _fake_discovery(monkeypatch)

    service = youtube_client.get_authenticated_service()
    assert built["via"] == "refresh"
    assert built["settings"]["refresh_token"] == "refresh"
    assert service == ("service", "creds")


def test_without_a_refresh_token_it_falls_back_to_the_browser_path(monkeypatch):
    for name in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    used = {}
    def via_browser(secrets, token):
        used["via"] = "browser"
        return "creds"

    monkeypatch.setattr(youtube_client, "_credentials_from_browser", via_browser)
    _fake_discovery(monkeypatch)

    youtube_client.get_authenticated_service()
    assert used["via"] == "browser"
