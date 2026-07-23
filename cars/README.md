# Cars workspace

Primary workspace for the car-ranking Shorts channel.

Generated local dry runs are stored under the cars workspace:

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
python cars/automation/plan_short.py --provider auto
FAST_MODE=1 python cars/automation/generate_sample.py --require-real-media --plan cars/output/sources/mazda-mx5-miata-official/short-plan.json
```

`review_media.py --provider auto` uses OpenAI vision review when `OPENAI_API_KEY` is set; otherwise it writes a local heuristic `media-review.json`. `plan_short.py --provider auto` reads the scraped source packet plus media review and writes `short-plan.json`, including the 20-second angle, narration, scene overlays, and planned media for each scene. The renderer prefers downloaded official/source car images from `cars/output/sources/<topic>/images/`, applies the media review to reject blurry/off-topic/nav images, follows planned scene media when available, then falls back to label matching or official-page screenshots from `cars/output/sources/<topic>/screenshots/`. If you omit `--require-real-media`, it can still fall back to generated cards for layout testing.

To explicitly render the AI/heuristic plan:

```bash
FAST_MODE=1 python cars/automation/generate_sample.py --require-real-media --plan cars/output/sources/mazda-mx5-miata-official/short-plan.json
```

After generation, inspect `scene_contact_sheet.jpg` for a fast visual overview, `media_selection_report.json` to see which asset was selected for each scene, and `edit_decision_report.json` to see the Milestone 3 edit choices: motion style, layout, crop focus, cut style, caption, stat chip, and crop audit for each scene. The crop audit confirms the renderer used a single full-frame subject crop, so the Short is filled without duplicating the same image as a separate top card and zoomed background.

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
