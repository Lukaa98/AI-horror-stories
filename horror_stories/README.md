# Horror stories workspace

The current production horror pipeline still uses the existing root automation files for backwards compatibility:

```text
automation/content_plan.json
automation/history.json
automation/state.json
```

The staged future per-channel horror files live at:

```text
automation/channels/horror/
```

This folder is reserved for future top-level horror-specific assets, outputs, and channel-only tooling once the channel layout is fully split.
