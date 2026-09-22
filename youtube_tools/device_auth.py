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
    parser.add_argument("--state-file", default=".device-auth-state.json",
                        help="Runner-local file holding the device code between phases. Never committed.")
    parser.add_argument("--phase", choices=("request", "wait", "both"), default="both",
                        help="'request' writes the code and exits so it can be published; "
                             "'wait' polls for the approval. The dashboard cannot show a code "
                             "that is still inside a running process, so the two are separable.")
    parser.add_argument("--secret-name", default=SECRET_NAME)
    args = parser.parse_args()

    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET must be set.")

    if args.phase in ("request", "both"):
        device = request_device_code(client_id)
        expires_at = time.time() + int(device.get("expires_in", 1800))
        # Written before the wait starts, because the dashboard is watching
        # for this file and nobody can approve a code they cannot see.
        payload = {
            "user_code": device["user_code"],
            "verification_url": device.get("verification_url") or "https://www.google.com/device",
            "expires_at": int(expires_at),
            "status": "waiting",
        }
        with open(args.code_file, "w") as handle:
            json.dump(payload, handle, indent=2)
        # The device code grants the pending grant, so it stays on the
        # runner and never goes near the branch the dashboard reads.
        with open(args.state_file, "w") as handle:
            json.dump({"device_code": device["device_code"],
                       "interval": device.get("interval", 5),
                       "expires_at": expires_at}, handle)
        print(f"Approve at {payload['verification_url']} with code {payload['user_code']}", flush=True)
        if args.phase == "request":
            return 0

    with open(args.state_file) as handle:
        state = json.load(handle)
    with open(args.code_file) as handle:
        payload = json.load(handle)
    expires_at = float(state["expires_at"])

    token = poll_for_token(client_id, client_secret, state["device_code"],
                           state.get("interval", 5), expires_at)
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise SystemExit("Google returned no refresh token. Re-run and make sure you approve as the channel owner.")

    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    pat = os.environ.get("GH_PAT", "").strip()
    if not (repository and pat):
        raise SystemExit("GH_PAT and GITHUB_REPOSITORY are needed to store the new token.")
    store_secret(repository, pat, args.secret_name, refresh_token)

    payload["status"] = "stored"
    payload.pop("user_code", None)
    with open(args.code_file, "w") as handle:
        json.dump(payload, handle, indent=2)
    print(f"Stored a new {args.secret_name}. Expires in "
          f"{round(int(token.get('refresh_token_expires_in', 0)) / 86400, 1)} days "
          "(0 means it does not expire).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
