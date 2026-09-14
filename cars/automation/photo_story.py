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

# Which manual photo field (by its review category) owns each slot.
CATEGORY_SLOTS = {"exterior_front": "front", "exterior_side": "side",
                  "exterior_rear": "rear", "engine_bay": "engine",
                  "interior": "interior"}

# v11.17 grouped sections mapped onto the slots that replaced them. An old
# request rerun on this version keeps working; "exterior" has no way to say
# which of the three exterior slots it meant, so it lands on the front.
LEGACY_SECTION_SLOTS = {"exterior": "front", "interior": "interior", "engine": "engine"}

# Collage geometry lives here rather than in photo_story_video.py because two
# things need it: the renderer that draws the tiles, and the motion planner
# that aims the narrator's eyes and hand at one. Keeping one copy is what
# stops the character pointing at where a tile used to be.
#
# Never more than two tiles across. Three in a row was cramped at phone size
# -- each cell ends up a third of the band wide, so the photo inside it is
# tiny -- so three close-ups stack as a pair plus a centred single instead.
COLLAGE_ROWS = {0: [], 1: [1], 2: [2], 3: [2, 1], 4: [2, 2]}
# The media band is much wider than it is tall, so a lone tile takes a
# half-width cell rather than stretching across the whole band.
MIN_TILE_COLUMNS = 2
# Listing photos are almost all 3:2, and a close-up is shown whole, so cells
# are cut to that shape -- a cell much wider than its photo is just white.
TILE_ASPECT = 1.5
# The main photo is wide, so it is cheap to make it shorter; a tile is
# photo-shaped, so its height is what its width costs. Tiles are therefore
# sized from the band's width first and the main photo takes what is left,
# down to this floor -- which is what stops a two-row grid squeezing the
# subject out.
MIN_MAIN_HEIGHT_RATIO = 0.34
GAP_RATIO = 0.018

# The close-ups arrive one at a time as the narration reaches them, rather
# than the whole chapter landing at once: the main photo holds alone first,
# then each tile appears into the slot the finished layout has already
# reserved for it, so nothing reflows as they come in.
MAIN_HOLD_SECONDS = 1.2
# All the tiles are up by this far through the chapter, leaving the rest of
# it on the complete picture.
REVEAL_TAIL_RATIO = 0.7


def reveal_schedule(start, end, count):
    """[(time, visible_count)] for one chapter -- when each close-up joins.

    The layout never changes as tiles arrive: collage_metrics() is keyed off
    the chapter's *total* close-up count, so a tile fades into its final
    cell instead of the grid re-flowing under it.
    """
    span = max(0.0, end - start)
    if count <= 0 or span <= 0:
        return [(start, count if span > 0 else 0)]
    hold = min(MAIN_HOLD_SECONDS, span * 0.35)
    last = start + max(hold, span * REVEAL_TAIL_RATIO)
    steps = [(start, 0)]
    for index in range(count):
        share = index / (count - 1) if count > 1 else 0.0
        steps.append((min(end, start + hold + (last - start - hold) * share), index + 1))
    # A chapter too short to stage the reveal collapses to showing them all.
    deduped = []
    for time, visible in steps:
        if deduped and time - deduped[-1][0] < 0.25:
            deduped[-1] = (deduped[-1][0], visible)
        else:
            deduped.append((time, visible))
    return deduped


def collage_rows(count):
    """How many tiles sit in each row, for `count` close-ups."""
    return COLLAGE_ROWS.get(max(0, min(count, MAX_CLOSEUPS)), [])


def collage_metrics(box_w, box_h, count):
    """Pixel geometry for one chapter picture: how tall the main photo is,
    and the size and shape of the close-up cells under it.

    Shared by the renderer that draws the tiles and the motion planner that
    aims the narrator's eyes and hand at one, so the character cannot point
    at a cell the renderer laid out somewhere else.
    """
    rows = collage_rows(count)
    gap = max(4, round(min(box_w, box_h) * GAP_RATIO))
    if not rows:
        return {"rows": [], "gap": gap, "main_h": box_h, "row_h": 0, "tile_w": 0}
    columns = max(max(rows), MIN_TILE_COLUMNS)
    # Widest the cells can be without overflowing the band, then as tall as
    # that width allows at photo shape -- so a contained close-up fills its
    # cell and the row reaches the edges.
    tile_w = (box_w - gap * (columns - 1)) // columns
    row_h = round(tile_w / TILE_ASPECT)
    tiles_h = row_h * len(rows) + gap * (len(rows) - 1)
    main_h = box_h - tiles_h - gap
    floor = round(box_h * MIN_MAIN_HEIGHT_RATIO)
    if main_h < floor:
        # Two rows at full width would leave the main photo a sliver, so give
        # it its floor and shrink the cells to suit, keeping their shape.
        tiles_h = box_h - floor - gap
        row_h = (tiles_h - gap * (len(rows) - 1)) // len(rows)
        tile_w = round(row_h * TILE_ASPECT)
        main_h = floor
    return {"rows": rows, "gap": gap, "main_h": main_h, "row_h": row_h, "tile_w": tile_w}


def tile_centers(box_w, box_h, count):
    """Centre of each close-up cell as a fraction of the media box, in the
    order the cells are filled."""
    metrics = collage_metrics(box_w, box_h, count)
    rows, gap, tile_w, row_h = metrics["rows"], metrics["gap"], metrics["tile_w"], metrics["row_h"]
    if not rows or not box_w or not box_h:
        return []
    centers, y = [], metrics["main_h"] + gap
    for row in rows:
        row_x = (box_w - (tile_w * row + gap * (row - 1))) / 2
        for column in range(row):
            centers.append(((row_x + tile_w * (column + 0.5) + gap * column) / box_w,
                            (y + row_h / 2) / box_h))
        y += row_h + gap
    return centers


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
                         "active_index": None})
            continue
        hero = section.get("hero") or selected
        # A tile is highlighted only when research pinned that exact photo
        # to this scene -- no guessing from media_type, which would light up
        # an unrelated tile whenever a chapter had more than one close-up.
        active_index = next((i for i, p in enumerate(section["photos"])
                             if p.get("cue_label") and p.get("cue_label") == scene.get("photo_label")), None)

        cues.append({
            "start": start, "end": end, "slot": section["id"], "label": section["label"],
            "hero": hero.get("path"),
            "closeups": [{"path": p.get("path"), "label": p.get("label") or "",
                          "photo_id": p.get("photo_id")} for p in section["photos"]],
            "active": section["photos"][active_index].get("photo_id") if active_index is not None else None,
            # The index is enough for the narrator to aim at: the cell's
            # position depends on the media box, which only the compositor
            # knows, so tile_centers() is applied there rather than baked in
            # here where it could drift from what was drawn.
            "active_index": active_index,
        })
    return _mark_chapters(cues)


def _mark_chapters(cues):
    """Tag every cue with the span of the run of scenes sharing its picture.

    The reveal is paced against the chapter, not the scene: a slot usually
    covers several scenes, and staging the close-ups per scene would either
    restart the reveal each time or never finish it.
    """
    index = 0
    while index < len(cues):
        end = index
        while (end + 1 < len(cues) and cues[end + 1].get("slot") == cues[index].get("slot")
               and cues[end + 1].get("hero") == cues[index].get("hero")):
            end += 1
        span = (cues[index]["start"], cues[end]["end"])
        for cue in cues[index:end + 1]:
            cue["chapter_start"], cue["chapter_end"] = span
        index = end + 1
    return cues
