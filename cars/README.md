# Cars workspace

Top-level workspace for the future cars channel.

Generated local dry runs go here instead of under `horror_stories/src/output`:

```text
cars/output/samples/<sample-slug>/
  final_short.mp4
  storyboard.json
  source_packet.json
  images/
```

Generate the current Miata sample from the repo root:

```bash
pip install -r requirements.txt
FAST_MODE=1 python cars/automation/generate_sample.py
```

This output is ignored by git so it can be inspected in Codespaces without bloating the repository.
