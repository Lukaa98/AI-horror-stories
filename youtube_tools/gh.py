"""Reading and writing single files on a branch, through the API.

Doing this with git means cloning, and cars-output is a 2.5GB branch of
rendered video. Fetching all of it to read 200 bytes of JSON, or to push
200 bytes back, is the difference between a job that takes seconds and one
that takes minutes -- and it was also how the re-authorise workflow
destroyed its own checkout by switching branches mid-run.
"""
import base64
import json
import urllib.error
import urllib.request

API = "https://api.github.com"


def api(repository, token, path, method="GET", body=None, timeout=60):
    """One API call. Returns None on 404 rather than raising."""
    request = urllib.request.Request(
        f"{API}/repos/{repository}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def read_json(repository, token, branch, path):
    """A JSON file's contents, or None if it is not there."""
    found = api(repository, token, f"/contents/{path}?ref={branch}")
    if not found or "content" not in found:
        return None
    return json.loads(base64.b64decode(found["content"]).decode("utf-8"))


def write_json(repository, token, branch, path, payload, message):
    """Create or replace a JSON file on a branch.

    The sha of the existing file has to be sent or the API rejects the
    write as a conflict, so this always looks first.
    """
    existing = api(repository, token, f"/contents/{path}?ref={branch}")
    body = {
        "message": message,
        "content": base64.b64encode(
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        ).decode(),
        "branch": branch,
    }
    if existing and existing.get("sha"):
        body["sha"] = existing["sha"]
    return api(repository, token, f"/contents/{path}", method="PUT", body=body)


def download(url, destination, timeout=300):
    """Stream a file to disk. Used for the rendered MP4."""
    with urllib.request.urlopen(url, timeout=timeout) as response, open(destination, "wb") as handle:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
    return destination
