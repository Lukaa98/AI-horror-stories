# GitHub Actions Content Pipeline

This repo now includes a starter GitHub Actions loop that runs:

1. `decide-next.yml`
2. `generate-video.yml`
3. `upload-video.yml`

The top-level scheduler is [`.github/workflows/pipeline.yml`](/C:/Users/13477/Desktop/DevProjects/AI-horror-stories/.github/workflows/pipeline.yml).
Stats polling now runs separately through [`.github/workflows/poll-only.yml`](/C:/Users/13477/Desktop/DevProjects/AI-horror-stories/.github/workflows/poll-only.yml).

## Required GitHub Secrets

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `YOUTUBE_CLIENT_SECRET_JSON_B64`
- `YOUTUBE_TOKEN_PICKLE_B64`

The YouTube secrets should be base64-encoded versions of:

- your OAuth client secret JSON
- your `token.pickle`

## How It Works

- The decision step rotates through entries in `automation/content_plan.json`.
- The generation step exports that runtime config into environment variables and runs `python horror_stories/src/main.py`.
- The upload step posts the generated MP4 to YouTube and records upload metadata in `automation/history.json`.
- The publish pipeline now makes a daily publish decision, targets noon New York time for scheduled release, and may skip a day when recent performance is too weak to justify a 24-hour turnaround.
- The publish pipeline commits updated `automation/state.json` and `automation/history.json` back into the repo.
- The poll-only workflow fetches stats for older videos from `automation/history.json` and refreshes that file on its own schedule.

## Important Limits

- This is repo-driven automation, not a true always-on daemon.
- The polling workflow intentionally waits for older videos and should be used for delayed performance evaluation, not immediate post-upload decisions.
- If the YouTube token expires and cannot refresh non-interactively, the workflow will fail until a fresh token is generated locally and re-saved to GitHub Secrets.
- Theme selection now uses a simple performance-aware heuristic once enough older stats exist, and publish cadence uses a lightweight 24h/48h rule:
  - never publish sooner than 24 hours after the last upload
  - allow an early 24-hour follow-up only when the most recent video clears the current score threshold
  - otherwise wait until the 48-hour cap
