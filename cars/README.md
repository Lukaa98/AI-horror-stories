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

For real-looking visuals, capture official/reputable source screenshots first. For the current Miata test:

```bash
cd scraper/car-source-scraper
npm install
npm run scrape:miata
cd ../..
```

Then generate the local test video from the repo root:

```bash
pip install -r requirements.txt
FAST_MODE=1 python cars/automation/generate_sample.py --require-real-media
```

The renderer uses official-page screenshots from `cars/output/sources/<topic>/screenshots/` when they exist. If you omit `--require-real-media`, it can still fall back to generated cards for layout testing.

## Voice options

By default, the sample uses `gTTS` to create `narration.mp3`. You can also run:

```bash
FAST_MODE=1 python cars/automation/generate_sample.py --tts-provider openai
FAST_MODE=1 python cars/automation/generate_sample.py --tts-provider tone
```

`openai` requires `OPENAI_API_KEY`. `tone` is only a last-resort audible placeholder.

This output is ignored by git so it can be inspected in Codespaces without bloating the repository.
