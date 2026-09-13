# Grouped photo stories (v10)

In the single-car editor, enable **Override photos**. Expand **Exterior**, **Interior**,
or **Engine bay**. Add a **Main photo**, then named **Close-ups** or **Angles**.
A specific listing is optional. The legacy single-photo overrides remain available.

For the first Lotus test, use one exterior overview plus diffuser, light, intake,
and wheel details; then one cabin overview plus gauges, console, and steering-wheel
details. Run the single-car pipeline on **v10**. A rerun of an old request preserves
its old inputs; create a grouped request to exercise the new layout.

## Contract

The existing `extra_photos` workflow input remains a JSON array. Old `{label,url}`
entries work unchanged. New entries optionally include `id`, `section` (exterior,
interior, engine), `role` (hero, angle, detail), and `note`. Reordering is array order.
Notes are research suggestions, not verified claims.

The collector assigns unique cue labels before research. Exact `scene.photo_label`
matches select details; type-only matching never chooses an unrelated grouped detail
when another overview exists. The manifest retains the full `photo_sections` pool,
including photos not selected as the per-scene media. Images retain their backgrounds.

The renderer and narrator share actual narration scene boundaries. An overview stays
above captions while the active detail fades/slides into a lower card. Up to four
context thumbnails appear below it, with an outline on the active image. Additional
details page the thumbnail rail. Adjacent identical overviews are merged, so a detail
change does not restart the main image. Alternate overview angles can replace the main
image for their own scene.

The character stays on one side within a chapter. Chapter changes can switch sides
with the existing eased camera (minimum 3.6 seconds between switches). Cards wait
one second on chapter changes, then gaze leads a single open-hand presentation.
This is a presentation gesture, not exact fingertip pointing. Face, blink, compact
mouth frames, and continuous wrist motion remain independently animated.

Grouped mode uses current-scene stats in a compact strip, not the cumulative table.
The old lower-half drag-race overlay is omitted to protect the detail card; comparison
photos, narration, and stats remain. Ungrouped requests keep the original layout.
Missing details are skipped. Without a usable hero/angle, a section falls back to the
ordinary selected photo instead of pinning a nonexistent overview.

## Validation and diagnostics

- `python -m pytest -q tests/test_photo_story.py tests/test_narrator_motion.py tests/test_narrator_video.py tests/test_single_car_short.py`
- `PUPPETEER_EXECUTABLE_PATH=/path/to/chrome node narrator/render/test-v21.js`
- From `web`: `node --test src/photoSections.test.js`, `npm run build`, `npm run lint`.

Actions runs the photo-story Python checks and the extended browser rig checks before
single-car rendering. Inspect `manifest.json` → `photo_presentation` for cue times,
selected detail IDs and rectangles; `_frames/narrator/narrator-motion.json` has the same
photo cues plus camera, gaze, gesture, and mouth tracks.

Local checks: production UI build and Python regression suite passed; static Lotus
compositions inspected. Live browser checks could not run in the local workspace
because Chrome's socket creation was denied. Actions is the required full-motion
verification before judging the final video.
