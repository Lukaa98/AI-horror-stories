"""Slot-shaped photo stories: one main photo per slot, with nested close-ups.

The editor exposes exactly the five slots it always had -- front, side,
rear, engine bay, interior. Each slot takes one main photo, which is what
the narration for that chapter is actually about, plus up to four
close-ups nested underneath it. Close-ups are supporting detail shown
alongside the main photo (exhaust tip, carbon trim, a headlight), not
subjects of their own, so a close-up never replaces the main image on
screen and never has to carry a scene by itself.

This replaces the v11.17 exterior/interior/engine grouping with its
hero/angle/detail roles: a slot is the group, its own Image URL is the
hero, and everything nested under it is a detail. Entries written by the
older editor are mapped onto the nearest slot rather than rejected.
"""
import re

SLOTS = ("front", "side", "rear", "engine", "interior")
SLOT_LABELS = {"front": "Front", "side": "Side", "rear": "Rear",
               "engine": "Engine bay", "interior": "Interior"}
# Four is where the collage stops reading as a layout and starts reading as
# a contact sheet.
MAX_CLOSEUPS = 4

# Collage geometry lives here rather than in photo_story_video.py because two
# things need it: the renderer that draws the tiles, and the motion planner
# that aims the narrator's eyes and hand at one. Keeping one copy is what
# stops the character pointing at where a tile used to be.
#
# Approved layout: one row under the main photo for one to three close-ups,
# two rows of two for four. Four in a single row leaves tiles too small to
# read at phone size, and 3+1 leaves an obvious hole.
COLLAGE_ROWS = {0: [], 1: [1], 2: [2], 3: [3], 4: [2, 2]}
# Share of the media box the main photo keeps. It stays the subject, so it
# never drops below half even when sharing with four close-ups.
MAIN_HEIGHT_RATIO = {0: 1.0, 1: 0.68, 2: 0.66, 3: 0.66, 4: 0.52}
# The media band is much wider than it is tall, so a tile row is a wide,
# short strip. One close-up laid across the whole width would be a 5:1
# letterbox, so a lone tile takes a half-width cell and sits centred.
MIN_TILE_COLUMNS = 2
# Close-ups are shown whole, never cropped, so a cell that is much wider than
# a photo just adds white either side. Cells are capped to roughly photo
# shape (listing photos are almost all 3:2) and the row is centred, which
# keeps each tile nearly filled instead of letterboxed across the full band.
TILE_ASPECT = 1.5


def collage_rows(count):
    """How many tiles sit in each row, for `count` close-ups."""
    return COLLAGE_ROWS.get(max(0, min(count, MAX_CLOSEUPS)), [])


def tile_centers(count):
    """Centre of each close-up tile as a fraction of the media box, in the
    order the tiles are filled. Ignores the inter-tile gap, which is under
    2% -- fine for aiming a gaze, and the renderer does its own exact
    pixel maths from the same rows."""
    rows = collage_rows(count)
    if not rows:
        return []
    main = MAIN_HEIGHT_RATIO.get(count, 1.0)
    row_h = (1.0 - main) / len(rows)
    centers = []
    for row_index, row in enumerate(rows):
        columns = max(row, MIN_TILE_COLUMNS)
        tile_w = 1.0 / columns
        row_x = (1.0 - tile_w * row) / 2
        for column in range(row):
            centers.append((row_x + tile_w * (column + 0.5),
                            main + row_h * (row_index + 0.5)))
    return centers

# Which manual photo field (by its review category) owns each slot.
CATEGORY_SLOTS = {"exterior_front": "front", "exterior_side": "side",
                  "exterior_rear": "rear", "engine_bay": "engine",
                  "interior": "interior"}

# v11.17 grouped sections mapped onto the slots that replaced them. An old
# request rerun on this version keeps working; "exterior" has no way to say
# which of the three exterior slots it meant, so it lands on the front.
LEGACY_SECTION_SLOTS = {"exterior": "front", "interior": "interior", "engine": "engine"}


def slot_of(item):
    """The slot an assembled media entry belongs to, or None."""
    slot = item.get("slot")
    if slot in SLOTS:
        return slot
    return CATEGORY_SLOTS.get(item.get("category"))


def photo_metadata(item, index):
    """Cue metadata for one nested close-up, or {} for an ungrouped extra.

    Every close-up gets a unique bracketed id so research can point a scene
    at one exact photo instead of at "some interior shot"; the input
    position is folded in because two close-ups may legitimately share a
    label.
    """
    slot = item.get("slot")
    if slot not in SLOTS:
        slot = LEGACY_SECTION_SLOTS.get(item.get("section"))
    if slot not in SLOTS:
        return {}
    identity = re.sub(r"[^a-zA-Z0-9_-]", "-", str(item.get("id") or "photo"))[:64]
    photo_id = f"{slot}-{identity}-{index}"
    label = str(item.get("label") or f"{SLOT_LABELS[slot]} close-up").strip()[:120]
    return {"slot": slot, "role": "detail", "photo_id": photo_id, "label": label,
            "cue_label": f"[{photo_id}] {label}", "note": str(item.get("note") or "").strip()[:500]}


def collect_photo_sections(media):
    """One entry per slot that actually has close-ups nested under it.

    Deliberately keyed off the close-ups rather than off the main photos:
    every ordinary build already has front/side/rear/engine/interior media,
    so keying off those would switch every video to the collage layout.
    Nesting a close-up is the opt-in.
    """
    sections = []
    for slot in SLOTS:
        closeups = [dict(p) for p in media if p.get("slot") == slot][:MAX_CLOSEUPS]
        if not closeups:
            continue
        hero = next((dict(p) for p in media if CATEGORY_SLOTS.get(p.get("category")) == slot), None)
        sections.append({"id": slot, "label": SLOT_LABELS[slot], "hero": hero, "photos": closeups})
    return sections


def photo_story_timeline(manifest, boundaries):
    """One cue per narration scene: which main photo is up, which close-ups
    sit under it, and which of those the narration is on right now.

    The close-ups for a slot are all on screen for as long as that slot's
    chapter runs -- they are context, not a slideshow -- so a cue changes
    the picture only when the chapter does. `active` just moves a highlight
    between tiles that are already visible.
    """
    sections = {s["id"]: s for s in manifest.get("photo_sections") or []}
    if not sections:
        return []
    media = manifest.get("media") or []
    scenes = manifest.get("scenes") or []
    cues = []
    for index, (start, end) in enumerate(boundaries):
        scene = scenes[index] if index < len(scenes) else {}
        selected = media[index] if index < len(media) else {}
        section = sections.get(slot_of(selected))
        # The comparison beat is about the rival's own photo; never replace
        # it with the primary car's main image.
        if scene.get("rival_make") or scene.get("rival_model") or not section:
            cues.append({"start": start, "end": end, "slot": None, "label": None,
                         "hero": selected.get("path"), "closeups": [], "active": None,
                         "active_index": None, "active_center": None})
            continue
        hero = section.get("hero") or selected
        # A tile is highlighted only when research pinned that exact photo
        # to this scene -- no guessing from media_type, which would light up
        # an unrelated tile whenever a chapter had more than one close-up.
        active_index = next((i for i, p in enumerate(section["photos"])
                             if p.get("cue_label") and p.get("cue_label") == scene.get("photo_label")), None)
        centers = tile_centers(len(section["photos"]))
        cues.append({
            "start": start, "end": end, "slot": section["id"], "label": section["label"],
            "hero": hero.get("path"),
            "closeups": [{"path": p.get("path"), "label": p.get("label") or "",
                          "photo_id": p.get("photo_id")} for p in section["photos"]],
            "active": section["photos"][active_index].get("photo_id") if active_index is not None else None,
            "active_index": active_index,
            # Where that tile sits inside the media box, so the narrator can
            # actually look and gesture at it instead of at a fixed spot.
            "active_center": centers[active_index] if active_index is not None and active_index < len(centers) else None,
        })
    return cues
