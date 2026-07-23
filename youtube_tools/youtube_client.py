import os
import pickle
from pathlib import Path

from google.auth.transport.requests import Request
import google_auth_oauthlib.flow
import googleapiclient.discovery

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.upload",
]


def _default_client_secret():
    return Path(__file__).resolve().parent / ".credentials" / "client_secret.json"


def _default_token_file():
    return Path(__file__).resolve().parent / ".credentials" / "token.pickle"


def get_authenticated_service():
    secrets_file = Path(os.getenv("YOUTUBE_CLIENT_SECRET_FILE", str(_default_client_secret())))
    token_file = Path(os.getenv("YOUTUBE_TOKEN_FILE", str(_default_token_file())))
    running_in_ci = os.getenv("CI", "").lower() == "true"

    credentials = None
    if token_file.exists():
        with open(token_file, "rb") as token:
            credentials = pickle.load(token)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "wb") as token:
            pickle.dump(credentials, token)

    if not credentials or not credentials.valid:
        if running_in_ci:
            raise RuntimeError(
                "YouTube credentials are missing or invalid in CI. "
                "Set YOUTUBE_CLIENT_SECRET_FILE and YOUTUBE_TOKEN_FILE from GitHub Secrets."
            )
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            str(secrets_file),
            SCOPES,
        )
        credentials = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "wb") as token:
            pickle.dump(credentials, token)

    return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
