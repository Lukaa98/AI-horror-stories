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
