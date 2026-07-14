# Cars workspace

Top-level workspace for the future cars channel.

Generated local dry runs go here instead of under `horror_stories/src/output`:

```text
cars/output/samples/<sample-slug>/
  final_short.mp4
  storyboard.json
  media_selection_report.json
  scene_contact_sheet.jpg
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

Then review the scraped media and generate the local test video from the repo root:

```bash
pip install -r requirements.txt
python cars/automation/review_media.py --provider auto
FAST_MODE=1 python cars/automation/generate_sample.py --require-real-media
```

`review_media.py --provider auto` uses OpenAI vision review when `OPENAI_API_KEY` is set; otherwise it writes a local heuristic `media-review.json`. The renderer prefers downloaded official/source car images from `cars/output/sources/<topic>/images/`, applies the media review to reject blurry/off-topic/nav images, matches each scene to labels like `exterior`, `interior`, `wheels`, `performance`, or `convertible_roof`, then falls back to official-page screenshots from `cars/output/sources/<topic>/screenshots/`. If you omit `--require-real-media`, it can still fall back to generated cards for layout testing.

After generation, inspect `scene_contact_sheet.jpg` for a fast visual overview and `media_selection_report.json` to see which asset was selected for each scene, which labels matched, and whether AI review approved or rejected it.

## Voice options

By default, the sample uses `gTTS` to create `narration.mp3`. You can also run:

```bash
FAST_MODE=1 python cars/automation/generate_sample.py --tts-provider openai
FAST_MODE=1 python cars/automation/generate_sample.py --tts-provider tone
```

`openai` requires `OPENAI_API_KEY`. By default it uses the selected `spec_punch` / `trailer_hype` direction from the auditions: OpenAI voice `onyx`, punchy automotive instructions, and `OPENAI_TTS_SPEED=1.0`. Override with `OPENAI_TTS_VOICE`, `OPENAI_TTS_INSTRUCTIONS`, or `OPENAI_TTS_SPEED` if needed. `tone` is only a last-resort audible placeholder.

To quickly compare narrator styles before rendering a full video, generate voice auditions:

```bash
python cars/automation/audition_voices.py
```

By default this now regenerates the selected audition direction: `spec_punch` script style with the `trailer_hype` voice preset, which writes `spec-punch-trailer-hype-onyx.mp3`. Auditions are written to `cars/output/voice_auditions/` with an `index.html` player and `manifest.json`. Other presets remain available when you explicitly request them.

If the voices feel too similar, test different script modes too:

```bash
python cars/automation/audition_voices.py --script-style casual_short
python cars/automation/audition_voices.py --script-style quirky_walkaround
python cars/automation/audition_voices.py --script-style spec_punch
python cars/automation/audition_voices.py --script-style hype_short
```

To limit the matrix to a couple scripts and voices:

```bash
python cars/automation/audition_voices.py --script-styles casual_short,quirky_walkaround --presets car_host,deep_gravel,warm_enthusiast
```

To regenerate every built-in script/voice combo again:

```bash
python cars/automation/audition_voices.py --script-styles casual_short,quirky_walkaround,spec_punch,hype_short --presets car_host,deep_gravel,luxury_ai,warm_enthusiast,trailer_hype,clean_news
```

This output is ignored by git so it can be inspected in Codespaces without bloating the repository.
