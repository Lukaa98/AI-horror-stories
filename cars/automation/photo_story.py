"""Opt-in, section-aware photo stories carried by the existing extra_photos input.

No random placement or label guessing: only an exact photo cue selects a detail.
Legacy entries without a section retain their original behavior.
"""
import re

SECTIONS = ("exterior", "interior", "engine")
ROLES = ("hero", "angle", "detail")


def photo_metadata(item, index):
    if item.get("section") not in SECTIONS:
        return {}
    section = item["section"]
    role = item.get("role", "detail")
    role = role if role in ROLES else "detail"
    # Include the input position to disambiguate duplicate labels and IDs.
    identity = re.sub(r"[^a-zA-Z0-9_-]", "-", str(item.get("id") or "photo"))[:64]
    photo_id = f"{section}-{identity}-{index}"
    label = str(item.get("label") or f"{section.title()} {role}").strip()[:120]
    return {"section": section, "role": role, "photo_id": photo_id,
            "label": label, "cue_label": f"[{photo_id}] {label}",
            "note": str(item.get("note") or "").strip()[:500]}


def collect_photo_sections(media):
    sections = []
    for section in SECTIONS:
        photos = [dict(p) for p in media if p.get("section") == section]
        if not photos:
            continue
        hero = next((p for p in photos if p.get("role") == "hero"), None)
        hero = hero or next((p for p in photos if p.get("role") == "angle"), None)
        # Details-only input remains usable but cannot pin a nonexistent overview.
        sections.append({"id": section, "hero": hero, "photos": photos})
    return sections


def photo_story_timeline(manifest, boundaries):
    """One cue per actual narration scene, shared by imagery and live rig.

    The narrator stays on one side within a chapter; only chapter changes can
    switch sides. The first second of a new chapter is reserved for settling.
    """
    sections = {s["id"]: s for s in manifest.get("photo_sections") or []}
    if not sections:
        return []
    media = manifest.get("media") or []
    scenes = manifest.get("scenes") or []
    cues, side, previous_section, last_switch = [], "right", None, -100.0
    for index, (start, end) in enumerate(boundaries):
        scene = scenes[index] if index < len(scenes) else {}
        selected = media[index] if index < len(media) else {}
        section_id = selected.get("section")
        if not section_id:
            section_id = scene.get("media_type")
            if section_id == "wheel":
                section_id = "exterior"
        section = sections.get(section_id)
        # Never replace the rival with the primary car's hero.
        if scene.get("rival_make") or scene.get("rival_model") or not section:
            cues.append({"start": start, "end": end, "section": None,
                         "hero": selected.get("path"), "detail": None, "side": side})
            continue
        changed = section_id != previous_section
        if changed and previous_section is not None and start - last_switch >= 3.6:
            side = "left" if side == "right" else "right"
            last_switch = start
        previous_section = section_id
        hero = section.get("hero")
        if selected.get("role") == "angle":
            hero = selected
        # A detail is shown only when the scene explicitly matched that photo.
        detail = selected if hero and selected.get("role") == "detail" and scene.get("photo_label") == selected.get("cue_label") else None
        cues.append({"start": start, "end": end, "section": section_id,
                     "hero": (hero or selected).get("path"), "detail": detail,
                     "side": side, "detail_start": min(end, start + (1.0 if changed else 0.2)),
                     "thumbnails": [p for p in section["photos"] if p.get("role") == "detail"],
                     "detail_count": len([p for p in section["photos"] if p.get("role") == "detail"])})
    return cues
