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
# a contact sheet -- see collage_rows in photo_story_video.py.
MAX_CLOSEUPS = 4

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
                         "hero": selected.get("path"), "closeups": [], "active": None})
            continue
        hero = section.get("hero") or selected
        # A tile is highlighted only when research pinned that exact photo
        # to this scene -- no guessing from media_type, which would light up
        # an unrelated tile whenever a chapter had more than one close-up.
        active = next((p.get("photo_id") for p in section["photos"]
                       if p.get("cue_label") and p.get("cue_label") == scene.get("photo_label")), None)
        cues.append({
            "start": start, "end": end, "slot": section["id"], "label": section["label"],
            "hero": hero.get("path"),
            "closeups": [{"path": p.get("path"), "label": p.get("label") or "",
                          "photo_id": p.get("photo_id")} for p in section["photos"]],
            "active": active,
        })
    return cues
