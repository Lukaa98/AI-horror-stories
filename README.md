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

## Recommended Workflow

Start with the image-first pipeline because it is much cheaper and more reliable than generating every second as video. Once the shorts are working, add video only for the highest-impact scenes.
