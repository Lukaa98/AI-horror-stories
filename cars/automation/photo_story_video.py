"""Main photo on top, its close-ups tiled underneath, inside the media box.

v11.17 floated the active close-up in a card over the lower half of the
frame with a paging thumbnail rail beside it, which fought the narrator for
space and meant the supporting photos were only ever visible one at a time.
This builds the whole chapter as one picture instead: the main photo across
the top, every close-up for that slot laid out below it, and a highlight
that moves between tiles as the narration reaches them. Nothing floats over
the narrator, so the lower half of the frame is free again.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from moviepy.editor import ImageClip, CompositeVideoClip
from generate_sample import _font
# Row/height geometry is shared with the motion planner so the narrator can
# aim at a tile the renderer actually drew -- see photo_story.py.
from photo_story import collage_metrics, reveal_schedule
BACKGROUND = (255, 255, 255)
ACTIVE_OUTLINE = (225, 157, 20)
ACTIVE_OUTLINE_WIDTH = 4
# Dark band under the highlighted tile's name: a white one reads as frame
# background rather than as part of the photo.
LABEL_BACKGROUND = (24, 26, 30)
LABEL_COLOR = (255, 255, 255)


def _open(path, root):
    path = Path(path)
    path = path if path.is_absolute() else root / path
    if not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA", "P"):
                # Exterior shots come back from remove_background as RGBA, and
                # a straight convert("RGB") paints every transparent pixel
                # black -- which is why the main photo sat in a black box in
                # runs #170/#171. Composite onto the frame's own white first.
                image = image.convert("RGBA")
                # Then trim the transparent margin the cutout leaves around
                # the car. On run #172's front shot the car filled only 56% of
                # the PNG's height, so a contained fit was sizing the padding
                # rather than the subject and the main photo came out barely
                # taller than the close-up tiles beneath it.
                bounds = image.getchannel("A").getbbox()
                if bounds:
                    image = image.crop(bounds)
                canvas = Image.new("RGBA", image.size, (*BACKGROUND, 255))
                canvas.alpha_composite(image)
                return canvas.convert("RGB")
            return image.convert("RGB")
    except (OSError, ValueError):
        return None


def _paste_contained(frame, image, box):
    """Fit the whole photo inside box. Used for the main photo, where
    cropping could cut the nose or tail off the car."""
    x, y, w, h = box
    if w < 2 or h < 2:
        return
    fitted = ImageOps.contain(image, (w, h), Image.Resampling.LANCZOS)
    frame.paste(fitted, (x + (w - fitted.width) // 2, y + (h - fitted.height) // 2))


def _short_label(draw, label, font, width):
    label = str(label)
    if draw.textlength(label, font=font) <= width:
        return label
    while label and draw.textlength(label + "…", font=font) > width:
        label = label[:-1]
    return label + "…"


def collage_frame(hero_image, closeups, active, box_size, font, visible=None):
    """One rendered chapter picture. `closeups` are (image, label, photo_id).

    `visible` draws only the first N tiles while keeping the layout of the
    full set, so a close-up appears in the cell it will keep rather than the
    grid re-flowing as each one arrives.
    """
    if visible is None:
        visible = len(closeups)
    w, h = box_size
    frame = Image.new("RGB", (w, h), BACKGROUND)
    draw = ImageDraw.Draw(frame)
    metrics = collage_metrics(w, h, len(closeups))
    rows, gap = metrics["rows"], metrics["gap"]
    tile_w, row_h, main_h = metrics["tile_w"], metrics["row_h"], metrics["main_h"]
    _paste_contained(frame, hero_image, (0, 0, w, main_h))

    index, y = 0, main_h + gap
    for row in rows:
        row_x = (w - (tile_w * row + gap * (row - 1))) // 2
        for column in range(row):
            if index >= min(visible, len(closeups)):
                break
            image, label, _photo_id = closeups[index]
            x = row_x + column * (tile_w + gap)
            _paste_contained(frame, image, (x, y, tile_w, row_h))
            if active is not None and index == active:
                if label:
                    # Caption band first, outline over it, so the highlight
                    # stays a complete rectangle around the whole tile.
                    pad = max(3, gap // 2)
                    text = _short_label(draw, label, font, tile_w - 2 * pad)
                    text_h = round(getattr(font, "size", 14) * 1.6)
                    draw.rectangle((x, y + row_h - text_h, x + tile_w - 1, y + row_h - 1),
                                   fill=LABEL_BACKGROUND)
                    draw.text((x + pad, y + row_h - text_h + (text_h - getattr(font, "size", 14)) // 2),
                              text, font=font, fill=LABEL_COLOR)
                draw.rectangle((x, y, x + tile_w - 1, y + row_h - 1),
                               outline=ACTIVE_OUTLINE, width=ACTIVE_OUTLINE_WIDTH)
            index += 1
        y += row_h + gap
    return frame


def build_photo_tracks(cues, fallback_paths, root, media_box, size, duration, output_dir):
    """One clip per distinct chapter picture, crossfaded where it changes.

    Returns (media_clip, [], diagnostics). The empty list is what used to be
    the floating detail cards -- the collage absorbed them, and keeping the
    shape means narrator_video.py's composite is unchanged.
    """
    root, output_dir = Path(root), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _, _, box_w, box_h = map(int, media_box)
    font = _font(max(13, round(size[0] * 0.019)))

    groups, diagnostics = [], []
    for index, cue in enumerate(cues):
        path = cue.get("hero")
        hero = _open(path, root) if path else None
        if hero is None and index < len(fallback_paths):
            path = str(Path(fallback_paths[index]).resolve())
            hero = _open(path, root)
        if hero is None:
            continue
        closeups = []
        for closeup in cue.get("closeups") or []:
            image = _open(closeup.get("path"), root) if closeup.get("path") else None
            if image is not None:
                closeups.append((image, closeup.get("label") or "", closeup.get("photo_id")))
        active = next((i for i, (_, _, pid) in enumerate(closeups)
                       if pid and pid == cue.get("active")), None)
        key = (path, tuple(pid for _, _, pid in closeups), active)
        # Adjacent scenes inside the same chapter with the same highlight are
        # one continuous picture -- re-rendering them would restart the main
        # photo's fade partway through a sentence.
        if groups and groups[-1]["key"] == key:
            groups[-1]["end"] = cue["end"]
            continue
        groups.append({"key": key, "start": cue["start"], "end": cue["end"],
                       "chapter_start": cue.get("chapter_start", cue["start"]),
                       "chapter_end": cue.get("chapter_end", cue["end"]),
                       "hero": hero, "closeups": closeups, "active": active})
        diagnostics.append({"scene": index, "start": cue["start"], "slot": cue.get("slot"),
                            "closeups": [pid for _, _, pid in closeups],
                            "active": closeups[active][2] if active is not None else None})
    if not groups:
        return None, [], []

    clips = []
    for index, group in enumerate(groups):
        # Stage the close-ups across the chapter this group belongs to, not
        # across the group itself: a chapter is usually several scenes, and
        # a group ends whenever the highlight moves.
        steps = reveal_schedule(group["chapter_start"], group["chapter_end"], len(group["closeups"]))
        group_end = min(duration, group["end"] + 0.25) if index < len(groups) - 1 else duration
        for step_index, (step_start, visible) in enumerate(steps):
            step_end = steps[step_index + 1][0] if step_index + 1 < len(steps) else group_end
            # Only the part of this reveal step that falls inside the group.
            start = max(step_start, group["start"])
            end = min(step_end, group_end) if step_index + 1 < len(steps) else group_end
            if end - start < 0.05:
                continue
            frame = collage_frame(group["hero"], group["closeups"], group["active"],
                                  (box_w, box_h), font, visible=visible)
            path = output_dir / f"chapter-{index}-{step_index}.png"
            frame.save(path)
            clip = ImageClip(str(path)).set_duration(max(0.1, end - start))
            # Every picture after the very first one fades in, so a tile
            # arrives rather than popping.
            if clips:
                clip = clip.crossfadein(0.3 if step_index else 0.25)
            clips.append(clip.set_start(start))
    media = CompositeVideoClip(clips, size=(box_w, box_h), bg_color=BACKGROUND).set_duration(duration)
    return media, [], diagnostics
