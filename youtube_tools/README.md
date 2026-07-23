# YouTube tools

Channel-neutral helpers retained for publishing reviewed car videos later. They
support OAuth authentication, resumable MP4 uploads, scheduled publishing, channel
metadata, bulk privacy updates, and basic video statistics.

Run tools as modules from the repository root, for example:

```bash
python -m youtube_tools.publish_video --help
```

OAuth credentials belong in `.credentials/` (ignored by Git) or in paths selected
with `YOUTUBE_CLIENT_SECRET_FILE` and `YOUTUBE_TOKEN_FILE`. Never commit either the
Google client-secret JSON or the generated token pickle.
