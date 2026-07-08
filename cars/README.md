# Cars workspace

Top-level workspace for the future cars channel.

Generated local dry runs go here instead of under `horror_stories/src/output`:

```text
cars/output/samples/<sample-slug>/
  final_short.mp4
  storyboard.json
  source_packet.json
  narration.mp3
  images/
```

## Better local sample flow

For real-looking visuals, capture official/reputable source images/screenshots first. For the current Miata test:

```bash
cd scraper/car-source-scraper
npm install
npm run setup:linux
npm run scrape:miata-official
cd ../..
```

Then generate the local test video from the repo root:

```bash
pip install -r requirements.txt
FAST_MODE=1 python cars/automation/generate_sample.py --require-real-media
```

The renderer prefers downloaded official/source car images from `cars/output/sources/<topic>/images/`, matches each scene to labels like `exterior`, `interior`, `wheels`, `performance`, or `convertible_roof`, then falls back to official-page screenshots from `cars/output/sources/<topic>/screenshots/`. If you omit `--require-real-media`, it can still fall back to generated cards for layout testing.

## Voice options

By default, the sample uses `gTTS` to create `narration.mp3`. You can also run:

```bash
FAST_MODE=1 python cars/automation/generate_sample.py --tts-provider openai
FAST_MODE=1 python cars/automation/generate_sample.py --tts-provider tone
```

`openai` requires `OPENAI_API_KEY`. `tone` is only a last-resort audible placeholder.

This output is ignored by git so it can be inspected in Codespaces without bloating the repository.
