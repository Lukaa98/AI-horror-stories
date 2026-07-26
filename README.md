# Cars output storage

This branch contains generated car-ranking media only. Application source code
belongs on `main` and experimental UI code belongs on `v10`.

## Layout

```text
images/<draft-id>/...
videos/<draft-id>/final_short.mp4
metadata/<draft-id>.json
```

Each metadata JSON file should connect a draft to its generated files and retain
the original source URLs, attribution, and licensing information for every
image.

