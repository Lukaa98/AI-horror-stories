# Channel automation layout

The current production pipeline still reads the root `automation/content_plan.json`, `automation/history.json`, and `automation/state.json` files for backwards compatibility.

Use this folder as the staging layout for future multi-channel automation:

```text
automation/channels/
  horror/
    content_plan.json
    history.json
    state.json
  cars/
    content_plan.json
    history.json
    state.json
```

Each channel should eventually have its own YouTube credentials, upload cadence, content plan, history, and state. Do not mix unrelated niches on one YouTube channel; Fortnite subscribers, horror viewers, and car-news viewers should be treated as different audiences.

## Cars channel staging

The cars folder is intentionally **not** wired into the production upload workflow yet. The current production pipeline still uses the root horror plan.

For cars, avoid hardcoded evergreen topics as the final source of truth. Use `automation/channels/cars/content_plan.json` as a research configuration: it defines source policy, RSS/news discovery sources, and reusable format templates. Run `python src/automation/discover_car_topics.py` to produce `automation/channels/cars/researched_topics.json` with fresh candidate topics before any future car-video generation step.

A future cars workflow should:

1. Discover current/viral topics from configured sources.
2. Require enough source coverage for the selected format.
3. Prefer official manufacturer/configurator pages for specs, pricing, trims, and screenshots.
4. Store source URLs for the YouTube description.
5. Only then generate script, voiceover, visuals, and upload to the separate cars channel.


### Creator-adjacent car ideas

`automation/channels/cars/decision_rules.yaml` documents future decision rules for creator-adjacent Shorts. For example, Doug DeMuro uploads can be used only to identify what car/topic people may search for next. The future pipeline should then create an original Short about that same car or search intent, verify facts with independent official/reputable sources, and avoid using creator footage, scripts, or review content as source material.

### Source acquisition and scraping

There is no full scraping/browser-capture pipeline wired in yet. Current implemented cars logic is limited to `src/automation/discover_car_topics.py`, which reads configured RSS/Atom feeds and emits topic candidates. `automation/channels/cars/source_acquisition.yaml` documents the future acquisition stages: feed discovery, independent source verification, optional Playwright/Puppeteer browser capture for official configurators, and GPT script generation from verified source packets.

GPT should not be the source of current car topics by itself. The intended flow is: discover fresh topic signals, verify with official/reputable sources, optionally capture allowed official screenshots, and only then ask GPT to write the Short from that source packet.

### Cars launch requirements

To make the cars channel actually upload videos, we still need a separate cars YouTube channel, cars-specific YouTube OAuth credentials/secrets, and a separate cars workflow. `automation/channels/cars/launch_checklist.yaml` tracks those requirements. Browser automation is not needed for basic RSS/YouTube topic discovery, but it is needed for official configurators, build-and-price pages, and screenshot capture.

### Local car video dry runs

Use `python src/automation/generate_car_sample.py` to generate a local, non-uploaded Miata sample package under `src/output/car_samples/`. The current sample uses generated graphic cards and a silent placeholder narration track so we can judge pacing/layout without needing a new YouTube channel, car-channel credentials, or AI image/voice API calls.
