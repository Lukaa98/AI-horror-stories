"""The channel thumbnail for a finished build.

A Short's thumbnail is what a browsing viewer sees on the channel page and
in search, at a size where almost nothing survives. So this carries four
things and no more: the model in red, the car big enough to recognise at a
glance, two headline numbers, and the narrator so the channel's videos look
like each other.

Deliberately not the video's own opening frame, which is a collage -- six
small photos read as noise at 210 pixels wide.
"""
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw

from generate_sample import _font
from narrator_video import ACCENT_COLOR, TABLE_COLOR, TABLE_TINT, _spec_rows

# YouTube's own thumbnail size. A Short plays vertically, but the still is
# shown in 16:9 wherever it is shown at all.
THUMBNAIL_SIZE = (1280, 720)
BACKGROUND = (255, 255, 255)
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # YouTube rejects anything larger.

# Only the numbers that still read when the whole image is an inch wide.
THUMBNAIL_SPEC_FIELDS = ("horsepower", "zero_to_sixty", "redline")
# Rendered from narrator-rig-v21.html, which is the live rig. The exported
# sprites under narrator/sprites-v4 still read "CAR SHORTS LAB" on the
# hoodie -- the old channel name -- and a thumbnail carrying it would put
# that on the channel page under every video.
NARRATOR_SPRITE = "narrator/thumbnail-narrator.png"


def _trim(image):
    box = image.getbbox()
    return image.crop(box) if box else image


def _fit(image, width, height):
    """Scale to fit inside a box, keeping proportions."""
    copy = image.copy()
    copy.thumbnail((width, height), Image.Resampling.LANCZOS)
    return copy


def _title_text(manifest):
    """The model, as the viewer would say it -- not the scene headline.

    manifest["title"] is a scene headline and reads as a fragment out of
    context; run #214's was "Turbocharged Performance", which names no car.
    """
    car = manifest.get("car") or {}
    model = str(car.get("model") or "").strip()
    make = str(car.get("make") or "").strip()
    return (model or make or "").upper()


def _fit_text(draw, text, max_width, start_size, min_size=40):
    """The largest size at which the title still fits across the image."""
    size = start_size
    while size > min_size:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 4
    return _font(min_size)


def _hero_path(manifest, build_dir):
    """The biggest, cleanest photo of the car itself.

    Background-removed exteriors first: a cut-out sits on the white
    background the rest of the frame uses, where a raw photo brings its own
    parking lot with it.
    """
    build_dir = Path(build_dir)
    candidates = [item.get("path") for item in (manifest.get("media") or []) if item.get("path")]
    ordered = (
        [p for p in candidates if "nobg" in p and re.search(r"front|three|hero", p)]
        + [p for p in candidates if "nobg" in p and "rival" not in p]
        + [p for p in candidates if "rival" not in p]
    )
    for relative in ordered:
        full = build_dir / relative
        if full.is_file():
            return full
    return None


def _draw_specs(frame, manifest, origin, width):
    """Two or three numbers, in the video's own table colours."""
    rows = [row for row in _spec_rows(manifest.get("key_specs") or {})
            if row[0] in THUMBNAIL_SPEC_FIELDS][:3]
    if not rows:
        return 0
    draw = ImageDraw.Draw(frame)
    label_font, value_font = _font(26), _font(36)
    row_h, x, y = 60, origin[0], origin[1]
    header_h = 46
    total_h = header_h + row_h * len(rows)
    draw.rectangle([x, y, x + width, y + header_h], fill=TABLE_COLOR)
    draw.text((x + 16, y + 9), "KEY SPECS", font=_font(28), fill=(255, 255, 255))
    draw.rectangle([x, y + header_h, x + width, y + total_h], fill=TABLE_TINT)
    for index, (_field, label, value) in enumerate(rows):
        top = y + header_h + index * row_h
        draw.text((x + 16, top + 14), label.upper(), font=label_font, fill=TABLE_COLOR)
        value_w = draw.textlength(value, font=value_font)
        draw.text((x + width - 16 - value_w, top + 10), value, font=value_font, fill=(20, 20, 20))
        if index:
            draw.line([(x + 12, top), (x + width - 12, top)], fill=(255, 255, 255), width=2)
    return total_h


def build_thumbnail(manifest, build_dir, out_path, size=THUMBNAIL_SIZE):
    """Compose the thumbnail. Returns the path, or None with nothing to draw."""
    width, height = size
    frame = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(frame)

    title = _title_text(manifest)
    title_font = _fit_text(draw, title, width - 80, start_size=132)
    title_w = draw.textlength(title, font=title_font)
    title_h = title_font.size
    draw.text(((width - title_w) / 2, 18), title, font=title_font, fill=ACCENT_COLOR)

    top = int(title_h + 46)

    # The narrator, anchored bottom-right, sized so the car keeps the frame.
    narrator_path = Path(__file__).resolve().parents[2] / NARRATOR_SPRITE
    narrator_w = 0
    if narrator_path.is_file():
        narrator = _fit(_trim(Image.open(narrator_path).convert("RGBA")),
                        int(width * 0.22), int(height - top - 10))
        narrator_w = narrator.width
        frame.paste(narrator, (width - narrator_w - 24, height - narrator.height), narrator)

    hero = _hero_path(manifest, build_dir)
    if hero:
        car = _fit(_trim(Image.open(hero).convert("RGBA")),
                   int(width - narrator_w - 90), int((height - top) * 0.78))
        car_x = int((width - narrator_w - car.width) / 2) + 10
        frame.paste(car, (max(20, car_x), top), car)

    # Wide enough for "HORSEPOWER" and its value side by side. At 0.28 the
    # label ran straight through the number.
    _draw_specs(frame, manifest, (30, height - 240), width=int(width * 0.36))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.save(out_path, "JPEG", quality=88, optimize=True)
    if out_path.stat().st_size > MAX_UPLOAD_BYTES:
        frame.save(out_path, "JPEG", quality=72, optimize=True)
    return out_path


if __name__ == "__main__":
    import sys

    manifest = json.loads(Path(sys.argv[1]).read_text())
    print(build_thumbnail(manifest, sys.argv[2], sys.argv[3]))
