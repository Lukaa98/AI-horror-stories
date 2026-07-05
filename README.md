# AI Horror Stories

This project now builds cheap vertical Shorts with this flow:

1. Generate a `~30s` storyboard and narration
2. Generate one AI image per scene
3. Generate one narration track
4. Turn images into motion shots with zoom, drift, particles, and caption overlays
5. Export a final `1080x1920` narrated MP4

## Run

From the repo root:

```powershell
python src/main.py
```

Each run creates a folder under `src/output/` containing:

- `storyboard.json`
- `images/`
- `narration.mp3`
- `final_short.mp4`

## Project Layout

- `src/video_pipeline/`: storyboard, image generation, narration, subtitles, editing
- `src/channel_tools/`: YouTube upload, channel maintenance, and privacy tools
- `src/assets/`: banner and profile art
- `src/output/`: generated runs

## Main Env Settings

Set these in `src/.env`:

- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `CHARACTER_NAME`
- `THEME`

Optional tuning:

- `SHORT_TARGET_SECONDS=30`
- `SHORT_NUM_SCENES=7`
- `STORY_MODEL=gemini-2.0-flash`
- `IMAGE_MODEL=imagen-4.0-generate-001`
- `TTS_MODEL=gpt-4o-mini-tts`
- `TTS_VOICE=verse`

## YouTube Automation

This repo includes YouTube helpers that reuse the OAuth setup from the nearby `AutoShorts` project by default.

Publish a generated episode:

```powershell
python src/publish_episode.py --run-dir src/output/video_001 --privacy private
```

Bulk hide old Fortnite videos:

```powershell
python src/bulk_update_videos.py --query fortnite --privacy private
```

Update channel description:

```powershell
python src/manage_channel.py --description-file src/channel_description.txt
```

Note: YouTube does not let this repo rename the channel title or handle through the Data API, so those should be changed manually in YouTube Studio.

## GitHub Actions Pipeline

This repo also includes a starter GitHub Actions pipeline that can:

1. choose the next story theme from `automation/content_plan.json`
2. generate a new short
3. upload it to YouTube
4. poll the uploaded video's status and stats
5. commit updated pipeline state back to the repo

See [docs/github-actions-pipeline.md](/C:/Users/13477/Desktop/DevProjects/AI-horror-stories/docs/github-actions-pipeline.md) for the required secrets and workflow layout.

## Recommended Workflow

Start with the image-first pipeline because it is much cheaper and more reliable than generating every second as video. Once the shorts are working, add video only for the highest-impact scenes.
