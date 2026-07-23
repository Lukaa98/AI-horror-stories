# AI Car Ranking Shorts

This repository researches, reviews, and renders vertical car-ranking videos. The
active product is the Cars UI in `web/` and all automation is car-focused.

## How it works

1. Enter a make, model, and ranking scope in the Cars UI.
2. Dispatch the research workflow, which gathers grounded facts and usable photos.
3. Review the generated `research.json` on the `cars-output` branch.
4. Dispatch the render workflow to build a narrated `1080x1920` MP4.
5. Review the video and, when ready, publish it with the reusable YouTube tools.

Research and rendering are separate so a bad fact or image can be corrected before
spending time and API credits on a full render.

## Project layout

- `cars/automation/` — topic research, image review, ranking, narration, and rendering
- `cars/drafts/` — reviewable research and rendered draft artifacts
- `cars/strategy/` — staged content, sourcing, and channel-launch policy
- `scraper/car-source-scraper/` — Cars & Bids and Wikimedia image acquisition
- `web/` — React/Vite control panel for the two-stage GitHub Actions flow
- `youtube_tools/` — reusable upload, channel-management, and video-stat utilities
- `.github/workflows/` — research, rendering, and GitHub Pages deployment

## Local development

Install Python dependencies and run the tests:

```bash
pip install -r requirements.txt
python -m pytest -q
```

Run the web application:

```bash
cd web
npm ci
npm run dev
```

See [`cars/README.md`](cars/README.md) for the local research and rendering flow.

## GitHub Actions

- `cars-research.yml` builds a reviewable draft and writes it to `cars-output`.
- `cars-generate-from-research.yml` renders an approved draft from `cars-output`.
- `cars-ranking-generate.yml` provides the older combined manual flow.
- `deploy-pages.yml` publishes the Cars UI.

The research/render workflows require the API secrets referenced in their YAML,
including `OPENAI_API_KEY` for research and OpenAI narration.

## YouTube tools

YouTube support is intentionally retained as channel-neutral infrastructure. Put
OAuth files in the ignored `youtube_tools/.credentials/` directory:

```text
youtube_tools/.credentials/client_secret.json
youtube_tools/.credentials/token.pickle
```

You can instead set `YOUTUBE_CLIENT_SECRET_FILE` and `YOUTUBE_TOKEN_FILE` to files
outside the repository. The first local command opens Google's OAuth flow and saves
the resulting token; CI never starts an interactive login.

Upload a reviewed car Short:

```bash
python -m youtube_tools.publish_video \
  --video cars/drafts/example/final_short.mp4 \
  --title "Ranking the Best Corvette Generations" \
  --description-file description.txt \
  --tags "cars,corvette,car rankings,shorts" \
  --privacy private
```

Other retained utilities:

```bash
python -m youtube_tools.pull_latest_video_stats
python -m youtube_tools.manage_channel
python -m youtube_tools.bulk_update_videos --query "old title" --dry-run
python -m youtube_tools.keep_top_public --keep 10 --dry-run
```

Keep uploads private until the title, description, thumbnail, facts, image licenses,
and final render have been reviewed.
