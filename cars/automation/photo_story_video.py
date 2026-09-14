"""Main photo on top, its close-ups tiled underneath, inside the media box.

v11.17 floated the active close-up in a card over the lower half of the
frame with a paging thumbnail rail beside it, which fought the narrator for
space and meant the supporting photos were only ever visible one at a time.
This builds the whole chapter as one picture instead: the main photo across
the top, every close-up for that slot laid out below it, and a highlight
that moves between tiles as the narration reaches them. Nothing floats over
the narrator, so the lower half of the frame is free again.

Each piece is its own layer -- the main photo, each close-up, each highlight
-- rather than one flat picture re-rendered whenever anything changes. The
flat version had to crossfade the *entire* band every time a close-up
arrived, so the main photo and every tile already on screen dissolved into
identical copies of themselves: the whole thing appeared to re-render on
every reveal. Layering means only the piece that actually changed animates.
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


def _cell_boxes(box_w, box_h, count):
    """(x, y, w, h) for each close-up cell, in fill order."""
    metrics = collage_metrics(box_w, box_h, count)
    rows, gap = metrics["rows"], metrics["gap"]
    tile_w, row_h = metrics["tile_w"], metrics["row_h"]
    boxes, y = [], metrics["main_h"] + gap
    for row in rows:
        row_x = (box_w - (tile_w * row + gap * (row - 1))) // 2
        for column in range(row):
            boxes.append((row_x + column * (tile_w + gap), y, tile_w, row_h))
        y += row_h + gap
    return boxes, metrics["main_h"], gap


def _contained(image, size):
    """The photo fitted whole inside size, on its own white tile."""
    w, h = size
    tile = Image.new("RGB", (max(1, w), max(1, h)), BACKGROUND)
    fitted = ImageOps.contain(image, (max(1, w), max(1, h)), Image.Resampling.LANCZOS)
    tile.paste(fitted, ((w - fitted.width) // 2, (h - fitted.height) // 2))
    return tile


def _highlight_overlay(size, label, font, gap):
    """The outline and name drawn over an already-placed tile, as its own
    transparent layer -- so highlighting a tile never redraws the photo."""
    w, h = size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if label:
        pad = max(3, gap // 2)
        text = _short_label(draw, label, font, w - 2 * pad)
        text_h = round(getattr(font, "size", 14) * 1.6)
        draw.rectangle((0, h - text_h, w - 1, h - 1), fill=(*LABEL_BACKGROUND, 255))
        draw.text((pad, h - text_h + (text_h - getattr(font, "size", 14)) // 2),
                  text, font=font, fill=(*LABEL_COLOR, 255))
    draw.rectangle((0, 0, w - 1, h - 1), outline=(*ACTIVE_OUTLINE, 255), width=ACTIVE_OUTLINE_WIDTH)
    return overlay


def build_photo_tracks(cues, fallback_paths, root, media_box, size, duration, output_dir):
    """Layered media track: (media_clip, [], diagnostics).

    One clip for each main photo, one for each close-up starting when it is
    revealed, one for each highlight. Nothing that is already on screen is
    re-drawn when something new arrives, which is what stopped the whole
    band appearing to re-render on every reveal.

    The empty list is what used to be the floating detail cards -- the
    collage absorbed them, and keeping the shape means narrator_video.py's
    composite is unchanged.
    """
    root, output_dir = Path(root), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _, _, box_w, box_h = map(int, media_box)
    font = _font(max(13, round(size[0] * 0.019)))

    # One chapter per run of scenes sharing a picture; cues already carry the
    # span, so the reveal is paced against the whole chapter rather than
    # restarting at each scene.
    chapters, diagnostics = [], []
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
        start = cue.get("chapter_start", cue["start"])
        end = cue.get("chapter_end", cue["end"])
        if not chapters or chapters[-1]["key"] != (path, start):
            chapters.append({"key": (path, start), "start": start, "end": end,
                             "hero": hero, "closeups": closeups, "highlights": []})
        chapters[-1]["end"] = max(chapters[-1]["end"], end)
        if active is not None:
            chapters[-1]["highlights"].append((cue["start"], cue["end"], active))
        diagnostics.append({"scene": index, "start": cue["start"], "slot": cue.get("slot"),
                            "closeups": [pid for _, _, pid in closeups],
                            "active": closeups[active][2] if active is not None else None})
    if not chapters:
        return None, [], []

    layers = []
    for index, chapter in enumerate(chapters):
        start = chapter["start"]
        # Hold the last chapter to the end of the video so the band never
        # goes blank on a trailing beat.
        end = chapters[index + 1]["start"] if index + 1 < len(chapters) else duration
        if end - start < 0.05:
            continue
        cells, main_h, gap = _cell_boxes(box_w, box_h, len(chapter["closeups"]))

        main_path = output_dir / f"chapter-{index}-main.png"
        _contained(chapter["hero"], (box_w, main_h)).save(main_path)
        main = ImageClip(str(main_path)).set_duration(end - start).set_position((0, 0))
        # Only a change of main photo crossfades; a close-up arriving under
        # it leaves it completely alone.
        layers.append((main.crossfadein(0.25) if index else main).set_start(start))

        steps = reveal_schedule(start, chapter["end"], len(chapter["closeups"]))
        appears = {}
        for step_time, visible in steps:
            for tile_index in range(visible):
                appears.setdefault(tile_index, step_time)
        for tile_index, (image, _label, _pid) in enumerate(chapter["closeups"]):
            if tile_index >= len(cells):
                break
            x, y, w, h = cells[tile_index]
            tile_start = min(max(appears.get(tile_index, start), start), end)
            if end - tile_start < 0.05:
                continue
            tile_path = output_dir / f"chapter-{index}-tile-{tile_index}.png"
            _contained(image, (w, h)).save(tile_path)
            layers.append(ImageClip(str(tile_path))
                          .set_duration(end - tile_start)
                          .set_position((x, y))
                          .crossfadein(0.35)
                          .set_start(tile_start))

        for highlight_start, highlight_end, tile_index in chapter["highlights"]:
            if tile_index >= len(cells):
                continue
            x, y, w, h = cells[tile_index]
            label = chapter["closeups"][tile_index][1]
            # Never before the tile it marks has arrived.
            highlight_start = max(highlight_start, appears.get(tile_index, start))
            highlight_end = min(highlight_end, end)
            if highlight_end - highlight_start < 0.2:
                continue
            overlay_path = output_dir / f"chapter-{index}-mark-{tile_index}.png"
            _highlight_overlay((w, h), label, font, gap).save(overlay_path)
            layers.append(ImageClip(str(overlay_path), transparent=True)
                          .set_duration(highlight_end - highlight_start)
                          .set_position((x, y))
                          .crossfadein(0.2)
                          .set_start(highlight_start))

    media = CompositeVideoClip(layers, size=(box_w, box_h), bg_color=BACKGROUND).set_duration(duration)
    return media, [], diagnostics
