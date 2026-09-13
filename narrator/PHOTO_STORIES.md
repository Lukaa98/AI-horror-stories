# Photos: one main shot per slot, close-ups nested under it (v11.18)

In the single-car editor, enable **Override photos**. You get the five slots the
pipeline has always had — **Front, Side, Rear, Engine bay, Interior** — each with one
**main photo** URL and, nested under it, up to **four close-ups**, each just a URL and
an optional name.

That replaces v11.17's Exterior/Interior/Engine groups with their hero/angle/detail
roles, which asked the user to reason about the layout before they could paste a link.

Example for the Lotus test: Front main photo plus two close-ups (headlight, front
splitter); Rear main photo plus two (exhaust, diffuser); Interior main photo plus two
or three (gauges, shift knob, seat stitching).

## What reaches the video

A slot's main photo sits across the top of the media band. Its close-ups are tiled
underneath and stay on screen for that whole chapter — they are context, not a
slideshow:

| Close-ups | Layout under the main photo |
|---|---|
| 0 | main photo alone, full band (unchanged from an ordinary build) |
| 1 | one half-width tile, centred |
| 2 | two side by side |
| 3 | three side by side |
| 4 | two rows of two |

When a scene is specifically about one close-up, that tile gets an outline and its
name; otherwise nothing is highlighted. Nothing floats over the lower half of the
frame any more, so the narrator has its full range of framings back.

The comparison drag-race overlay stays suppressed in collage mode. Removing the
floating card removed the *reason* it was disabled but not the conflict: the race is
an overlay drawn against the full frame, so over a collage chapter it lays a small car
and a checkered flag across the tile row, clipped at the right edge (seen in run #170).
The comparison photo, narration and stats still play.

## What the narrator does with them

Framing is the ordinary cycle — bottom-left / bottom-right / bottom-centre at half
body, close-left / close-right at head-and-chest, changing on scene boundaries with at
least 3.6s between moves.

On top of that, when a scene is about one specific close-up, the character looks at
that tile and raises the hand on that side (`presentLeft` / `presentRight`) for the
length of the beat, then returns to rest; generic conversational gestures inside that
window are dropped so two arm poses never fight over the same second. The tile's
position comes from `tile_centers()` in `photo_story.py`, which is the same geometry
the renderer lays the tiles out with, so the hand goes where the tile actually is.

Gaze is a direction (`aim`: -1..1 per axis), worked out in `build_motion_plan` from
where the character was placed and where the target is. It used to be a *point* put
through `#head`'s CTM inverse in the rig — but `getCTM()` returns rendered pixels, not
the rig's own 540×960 frame units, so the answer depended on the capture's render size:
every shipped value clamped hard against the vertical limit, and the "look right at the
card" case actually looked left. `narrator/render/test-v21.js` now asserts the pupils
travel in the aimed direction, in a real browser.

## Narration

Unchanged and already where it should be: `TARGET_WORD_CENTER = 175` words against
`TARGET_DURATION_SECONDS = 58`. What changed is what research is told to do with the
photos. Main photos are the subjects — each one must carry its own scene. Close-ups
are explicitly *not* subjects: touch them in a clause where there is something real to
say (a spoiler, carbon trim, an exhaust tip, a headlight), never build a scene around
one, never force a mention, and never let one push out a history/mechanical beat.

## Contract

The `extra_photos` workflow input is still a JSON array. Old `{label, url}` entries are
still ungrouped extras. A nested close-up adds `slot` (`front`, `side`, `rear`,
`engine`, `interior`) and an optional `note`; `role` is gone, because a close-up is
always a detail and a slot's main photo always comes from that slot's own URL field.
v11.17 entries carrying `section` are mapped onto the nearest slot rather than
rejected (`exterior` → `front`, since it cannot say which exterior slot it meant).

Grouping is opt-in via the close-ups, not the main photos: every ordinary build
already has front/side/rear/engine/interior media, so keying off those would put every
video into the collage layout. No close-ups anywhere means `photo_sections` is empty
and the original one-photo-per-scene layout runs untouched.

The collector gives every close-up a unique bracketed cue id before research, so a
scene can point at one exact photo instead of "some interior shot". Only an exact
`scene.photo_label` match highlights a tile. Adjacent scenes inside one chapter with
the same highlight render as one continuous picture, so the main photo does not
restart mid-sentence. A dead close-up link drops that tile and the chapter still
renders; if every photo in a chapter fails, the build falls back to the ordinary
per-scene media track.

Inspect `manifest.json` → `photo_presentation` (version 2) for cue times, per-chapter
slot, tile ids and which tile was active.

## Validation

- `python -m pytest -q tests/test_photo_story.py tests/test_narrator_motion.py tests/test_narrator_video.py tests/test_single_car_short.py`
- From `web`: `node --test src/photoSections.test.js`, `npm run build`, `npm run lint`
- `PUPPETEER_EXECUTABLE_PATH=/path/to/chrome node narrator/render/test-v21.js`

Local status for this change: the full Python suite (147 tests), the live v21 browser
test (528 frames, in Chromium), the UI serializer
tests, the production build and lint all pass, and the four collage layouts were
rendered and inspected at the real media-box size (1047×614). A full-motion video has
not been rendered locally — Actions remains the verification before judging the result.
