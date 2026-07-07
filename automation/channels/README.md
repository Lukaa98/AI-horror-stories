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

`automation/channels/cars/decision_rules.yaml` documents future decision rules for creator-adjacent Shorts. For example, Doug DeMuro uploads can be used as a trend/search-demand signal, but the future pipeline should create an original Short about the same car or search intent, verify facts with official sources, and avoid copying creator footage or scripts.
