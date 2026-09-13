"""Persistent hero and focused detail cards for opt-in photo stories."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from moviepy.editor import ImageClip, CompositeVideoClip
from generate_sample import _font


def detail_box(size, side):
    w, h = size
    return (round(w * (.025 if side == "right" else .515)), round(h * .55),
            round(w * .46), round(h * .30))


def _open(path, root):
    path = Path(path)
    path = path if path.is_absolute() else root / path
    if not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            return ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError):
        return None


def _fit(image, box):
    return ImageOps.contain(image, box, Image.Resampling.LANCZOS)


def _short_label(draw, label, font, width):
    label = str(label)
    if draw.textlength(label, font=font) <= width:
        return label
    while label and draw.textlength(label + "…", font=font) > width:
        label = label[:-1]
    return label + "…"


def detail_frame(cue, root, size):
    """Contain (not crop) the focal feature, with up to four context thumbs."""
    _, _, w, h = detail_box(size, cue["side"])
    frame = Image.new("RGB", (w, h), (246, 246, 244))
    draw = ImageDraw.Draw(frame)
    font = _font(max(14, round(size[0] * .023)))
    pad = max(6, round(w * .035))
    header_h, footer_h = round(h * .13), round(h * .24)
    label = cue["detail"].get("label") or "Detail"
    draw.text((pad, pad), _short_label(draw, f"{cue['section'].title()} · {label}", font, w - 2 * pad), font=font, fill=(28, 30, 34))
    raw = _open(cue["detail"]["path"], root)
    if raw is None:
        return None
    photo = _fit(raw, (w - 2 * pad, h - header_h - footer_h))
    frame.paste(photo, ((w - photo.width) // 2, header_h + (h - header_h - footer_h - photo.height) // 2))
    thumbs = cue.get("thumbnails") or [cue["detail"]]
    active = next((i for i, p in enumerate(thumbs) if p.get("photo_id") == cue["detail"].get("photo_id")), 0)
    page = (active // 4) * 4
    visible = thumbs[page:page + 4]
    thumb_w = (w - 5 * pad) // 4
    thumb_h = max(1, footer_h - 2 * pad)
    for i, thumb in enumerate(visible):
        raw_thumb = _open(thumb["path"], root)
        if raw_thumb is None:
            continue
        image = _fit(raw_thumb, (thumb_w, thumb_h))
        x, y = pad + i * (thumb_w + pad), h - footer_h + pad
        frame.paste(image, (x + (thumb_w - image.width) // 2, y + (thumb_h - image.height) // 2))
        if page + i == active:
            draw.rectangle((x - 2, y - 2, x + thumb_w + 2, y + thumb_h + 2), outline=(225, 157, 20), width=3)
    return frame


def build_photo_tracks(cues, fallback_paths, root, media_box, size, duration, output_dir):
    """Merge identical adjacent heroes, crossfade changes; never restart per detail."""
    root, output_dir = Path(root), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _, _, box_w, box_h = map(int, media_box)
    groups, details, diagnostics = [], [], []
    for index, cue in enumerate(cues):
        path = cue.get("hero")
        raw = _open(path, root) if path else None
        if raw is None and index < len(fallback_paths):
            path = str(Path(fallback_paths[index]).resolve())
            raw = _open(path, root)
        if raw is not None:
            if groups and groups[-1]["path"] == path:
                groups[-1]["end"] = cue["end"]
            else:
                groups.append({"path": path, "image": raw, "start": cue["start"], "end": cue["end"]})
        if not cue.get("detail"):
            continue
        start, end = cue["detail_start"], cue["end"]
        if end - start < .8:
            continue
        frame = detail_frame(cue, root, size)
        if frame is None:
            continue
        dest = output_dir / f"detail-{index}.png"
        frame.save(dest)
        x, y, w, h = detail_box(size, cue["side"])
        # Ease a small vertical slide and opacity together; chapter side switches
        # finish before a new card appears. Both shoulders remain unobstructed.
        clip = ImageClip(str(dest)).set_duration(end - start).set_position(
            lambda t, x=x, y=y: (x, y + 14 * max(0, 1 - t / .4) ** 3))
        clip = clip.crossfadein(.25).crossfadeout(min(.2, (end - start) / 4)).set_start(start)
        details.append(clip)
        diagnostics.append({"scene": index, "start": start, "end": end,
                            "photo_id": cue["detail"]["photo_id"], "box": [x, y, w, h]})
    hero_clips = []
    for index, group in enumerate(groups):
        canvas = Image.new("RGB", (box_w, box_h), "white")
        photo = _fit(group["image"], (box_w, box_h))
        canvas.paste(photo, ((box_w - photo.width) // 2, (box_h - photo.height) // 2))
        path = output_dir / f"hero-{index}.png"
        canvas.save(path)
        clip = ImageClip(str(path)).set_duration(min(duration, group["end"] + .25) - group["start"])
        if index:
            clip = clip.crossfadein(.25)
        hero_clips.append(clip.set_start(group["start"]))
    hero = CompositeVideoClip(hero_clips, size=(box_w, box_h), bg_color=(255, 255, 255)).set_duration(duration)
    return hero, details, diagnostics
