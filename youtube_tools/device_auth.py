"""Re-authorise the channel without a browser on the machine running this.

Google's device flow is the one grant type that separates the two: this
prints a short code, the person approves it in whatever browser they happen
to have, and this polls until they do. That is what lets the whole thing run
in Actions and be driven from the dashboard.

The refresh token it ends up with is written straight into a GitHub Actions
secret and never printed, never committed, and never returned to the
browser. The only thing that leaves this process is the user code, which is
useless to anyone who cannot also log into the channel's Google account.

    python -m youtube_tools.device_auth --code-file out/code.json

Needs YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in the environment, and
GH_PAT plus GITHUB_REPOSITORY to store the result.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from youtube_tools import gh

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# youtube.force-ssl is rejected by the device endpoint -- it is not on the
# limited-input allowlist. These two cover uploading and editing our own
# videos, which is all the pipeline does.
SCOPES = "https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/youtube.upload"
SECRET_NAME = "YOUTUBE_REFRESH_TOKEN"


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read())


def request_device_code(client_id):
    payload = _post_form(DEVICE_CODE_URL, {"client_id": client_id, "scope": SCOPES})
    if "device_code" not in payload:
        raise SystemExit(f"Google refused the device request: {payload.get('error_description') or payload}")
    return payload


def poll_for_token(client_id, client_secret, device_code, interval, deadline):
    """Wait for the approval, returning the token payload.

    "authorization_pending" is the normal answer for as long as the person
    has not finished; "slow_down" means back off. Anything else is final.
    """
    wait = max(int(interval or 5), 5)
    while time.time() < deadline:
        time.sleep(wait)
        payload = _post_form(TOKEN_URL, {
            "client_id": client_id,
            "client_secret": client_secret,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        error = payload.get("error")
        if not error:
            return payload
        if error == "slow_down":
            wait += 5
            continue
        if error != "authorization_pending":
            raise SystemExit(f"Authorisation failed: {error} -- {payload.get('error_description', '')}")
    raise SystemExit("The code expired before it was approved. Start again.")


def publish_code(repository, token, branch, path, payload):
    """Put the user code on the output branch through the contents API.

    Committing it with git was the obvious way and the wrong one: it meant
    switching the checkout to the output branch, which replaced the code
    this very script lives in -- the run died on "No module named
    youtube_tools.device_auth" one step later. It also spent three minutes
    fetching a 2.5GB branch to write 200 bytes, which is most of the time a
    person is sat waiting for a code that expires.
    """
    gh.write_json(repository, token, branch, path, payload,
                  "youtube: publish device code for approval")


def store_secret(repository, pat, name, value):
    """Seal the token to the repo's public key and PUT it as a secret."""
    try:
        from nacl import encoding, public
    except ImportError as exc:  # pragma: no cover - depends on the runner
        raise SystemExit("PyNaCl is required to store the secret (pip install pynacl)") from exc

    def _api(path, method="GET", body=None):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {pat}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}

    key = _api("/actions/secrets/public-key")
    sealed = public.SealedBox(
        public.PublicKey(key["key"].encode(), encoding.Base64Encoder)
    ).encrypt(value.encode())
    _api(f"/actions/secrets/{name}", method="PUT", body={
        "encrypted_value": base64.b64encode(sealed).decode(),
        "key_id": key["key_id"],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-file", required=True,
                        help="Where to write the user code for the dashboard to show")
    parser.add_argument("--publish-branch", default="",
                        help="Branch to publish the user code to for the dashboard to read.")
    parser.add_argument("--publish-path", default="youtube/auth-code.json")
    parser.add_argument("--secret-name", default=SECRET_NAME)
    args = parser.parse_args()

    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET must be set.")

    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    pat = os.environ.get("GH_PAT", "").strip()
    publish_token = os.environ.get("GITHUB_TOKEN", "").strip() or pat

    device = request_device_code(client_id)
    expires_at = time.time() + int(device.get("expires_in", 1800))
    payload = {
        "user_code": device["user_code"],
        "verification_url": device.get("verification_url") or "https://www.google.com/device",
        "expires_at": int(expires_at),
        "status": "waiting",
    }
    with open(args.code_file, "w") as handle:
        json.dump(payload, handle, indent=2)
    # Published before the wait starts -- nobody can approve a code they
    # cannot see, and the clock is already running on it.
    if args.publish_branch and repository and publish_token:
        publish_code(repository, publish_token, args.publish_branch, args.publish_path, payload)
    print(f"Approve at {payload['verification_url']} with code {payload['user_code']}", flush=True)

    try:
        token = poll_for_token(client_id, client_secret, device["device_code"],
                               device.get("interval", 5), expires_at)
    except BaseException:
        # Only on the way out badly. Clearing it unconditionally would blink
        # "idle" between the approval and the store, and the dashboard reads
        # that as the run having died.
        if args.publish_branch and repository and publish_token:
            publish_code(repository, publish_token, args.publish_branch,
                         args.publish_path, {"status": "idle"})
        raise
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise SystemExit("Google returned no refresh token. Re-run and make sure you approve as the channel owner.")

    if not (repository and pat):
        raise SystemExit("GH_PAT and GITHUB_REPOSITORY are needed to store the new token.")
    store_secret(repository, pat, args.secret_name, refresh_token)

    # Google reports how long the refresh token lasts: about seven days
    # while the OAuth app is in Testing, and 0 once it is published, meaning
    # it does not expire. The dashboard shows this, so it has to be the
    # token's life -- not expires_at above, which is the device code's
    # thirty minutes and would have the panel claiming the token dies today.
    lifetime = int(token.get("refresh_token_expires_in") or 0)
    stored_payload = {
        "status": "stored",
        "authorised_at": int(time.time()),
        "token_expires_at": int(time.time()) + lifetime if lifetime else 0,
    }
    with open(args.code_file, "w") as handle:
        json.dump(stored_payload, handle, indent=2)
    if args.publish_branch and repository and publish_token:
        publish_code(repository, publish_token, args.publish_branch,
                     args.publish_path, stored_payload)
    print(f"Stored a new {args.secret_name}. Expires in "
          f"{round(int(token.get('refresh_token_expires_in', 0)) / 86400, 1)} days "
          "(0 means it does not expire).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
