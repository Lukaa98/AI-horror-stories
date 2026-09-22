"""Authenticated YouTube client, from a refresh token or a local browser.

There are two ways this runs and they need different things. In Actions
there is no browser, so the credentials are assembled from a refresh token
held in secrets -- the one the dashboard's re-authorise button mints. On a
laptop there is a browser, so the original OAuth flow still works and
caches its result in a pickle.

The google libraries are imported inside the functions rather than at the
top on purpose: importing this module should not require them. It used to,
which meant tests/test_youtube_publish.py could not even be collected
anywhere the libraries were not installed, and a test that cannot be
collected is a test that is not protecting anything.
"""
import os
import pickle
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.upload",
]
# What the device flow is allowed to ask for. force-ssl is not on Google's
# limited-input allowlist, so a token minted by the re-authorise button
# carries only these two -- enough to upload and to edit our own videos.
DEVICE_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _default_client_secret():
    return Path(__file__).resolve().parent / ".credentials" / "client_secret.json"


def _default_token_file():
    return Path(__file__).resolve().parent / ".credentials" / "token.pickle"


def refresh_token_settings(environ=None):
    """The three values needed to rebuild credentials without a browser.

    Returns None unless all three are present, because two of three is not
    a usable half -- it is a misconfiguration that should fall through to
    the interactive path or fail with a clear message, rather than produce
    a client that cannot refresh.
    """
    environ = os.environ if environ is None else environ
    client_id = (environ.get("YOUTUBE_CLIENT_ID") or "").strip()
    client_secret = (environ.get("YOUTUBE_CLIENT_SECRET") or "").strip()
    refresh_token = (environ.get("YOUTUBE_REFRESH_TOKEN") or "").strip()
    if not (client_id and client_secret and refresh_token):
        return None
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "token_uri": TOKEN_URI,
        "scopes": DEVICE_SCOPES,
    }


def _credentials_from_refresh_token(settings):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials = Credentials(token=None, **settings)
    # A refresh token alone is not an access token; this is the call that
    # turns one into the other, and the point where an expired or revoked
    # token fails loudly instead of halfway through an upload.
    credentials.refresh(Request())
    return credentials


def _credentials_from_browser(secrets_file, token_file):
    import google_auth_oauthlib.flow
    from google.auth.transport.requests import Request

    credentials = None
    if token_file.exists():
        with open(token_file, "rb") as handle:
            credentials = pickle.load(handle)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "wb") as handle:
            pickle.dump(credentials, handle)

    if not credentials or not credentials.valid:
        if os.getenv("CI", "").lower() == "true":
            raise RuntimeError(
                "No YouTube credentials in CI. Set YOUTUBE_CLIENT_ID, "
                "YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN -- the "
                "dashboard's Re-authorise button writes the last one."
            )
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            str(secrets_file), SCOPES
        )
        credentials = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "wb") as handle:
            pickle.dump(credentials, handle)

    return credentials


def get_authenticated_service():
    import googleapiclient.discovery

    settings = refresh_token_settings()
    if settings:
        credentials = _credentials_from_refresh_token(settings)
    else:
        credentials = _credentials_from_browser(
            Path(os.getenv("YOUTUBE_CLIENT_SECRET_FILE", str(_default_client_secret()))),
            Path(os.getenv("YOUTUBE_TOKEN_FILE", str(_default_token_file()))),
        )
    return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
