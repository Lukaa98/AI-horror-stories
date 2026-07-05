# GitHub Actions Content Pipeline

This repo now includes a starter GitHub Actions loop that runs:

1. `decide-next.yml`
2. `generate-video.yml`
3. `upload-video.yml`
4. `poll-results.yml`

The top-level scheduler is [`.github/workflows/pipeline.yml`](/C:/Users/13477/Desktop/DevProjects/AI-horror-stories/.github/workflows/pipeline.yml).

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
- The generation step exports that runtime config into environment variables and runs `python src/main.py`.
- The upload step posts the generated MP4 to YouTube and records upload metadata in `automation/history.json`.
- The poll step fetches the current stats for the uploaded video and updates `automation/history.json`.
- The final commit step pushes updated `automation/state.json` and `automation/history.json` back into the repo.

## Important Limits

- This is repo-driven automation, not a true always-on daemon.
- The current polling step runs immediately after upload, so it is useful for upload/processing state and early stats, not deep performance evaluation.
- If the YouTube token expires and cannot refresh non-interactively, the workflow will fail until a fresh token is generated locally and re-saved to GitHub Secrets.
- Immediate auto-improvement is intentionally simple right now: it rotates through planned themes. Smarter selection can be added once there is enough history to optimize against.
