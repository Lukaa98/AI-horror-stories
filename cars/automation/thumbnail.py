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

# The video's own shape. These are Shorts, and a Short's tile on the channel
# page and in the Shorts feed is vertical -- a 16:9 still gets cropped to
# fit it, which takes the sides off the car and half the spec table with
# them. Same aspect as the render, so nothing is lost.
THUMBNAIL_SIZE = (1080, 1920)
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
    label_font, value_font = _font(38), _font(52)
    row_h, x, y = 84, origin[0], origin[1]
    header_h = 64
    total_h = header_h + row_h * len(rows)
    draw.rectangle([x, y, x + width, y + header_h], fill=TABLE_COLOR)
    draw.text((x + 22, y + 12), "KEY SPECS", font=_font(40), fill=(255, 255, 255))
    draw.rectangle([x, y + header_h, x + width, y + total_h], fill=TABLE_TINT)
    for index, (_field, label, value) in enumerate(rows):
        top = y + header_h + index * row_h
        draw.text((x + 22, top + 22), label.upper(), font=label_font, fill=TABLE_COLOR)
        value_w = draw.textlength(value, font=value_font)
        draw.text((x + width - 22 - value_w, top + 14), value, font=value_font, fill=(20, 20, 20))
        if index:
            draw.line([(x + 16, top), (x + width - 16, top)], fill=(255, 255, 255), width=3)
    return total_h


def build_thumbnail(manifest, build_dir, out_path, size=THUMBNAIL_SIZE):
    """Compose the thumbnail. Returns the path, or None with nothing to draw.

    Stacked rather than side by side, because the frame is taller than it is
    wide: the model across the top, the car through the middle at the size
    that actually sells it, and the numbers and the narrator sharing the
    bottom band.
    """
    width, height = size
    frame = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(frame)

    title = _title_text(manifest)
    title_font = _fit_text(draw, title, width - 70, start_size=190, min_size=60)
    title_w = draw.textlength(title, font=title_font)
    draw.text(((width - title_w) / 2, int(height * 0.025)), title,
              font=title_font, fill=ACCENT_COLOR)
    top = int(height * 0.025) + title_font.size + int(height * 0.03)

    # A car photo is far wider than it is tall, so fitting one to this frame's
    # width leaves a band of white above and below it. Rather than let that
    # sit as two gaps, the car hangs directly under the title and the
    # narrator is tall enough to close the space from below.
    band_h = int(height * 0.52)
    band_top = height - band_h

    narrator_path = Path(__file__).resolve().parents[2] / NARRATOR_SPRITE
    narrator_top = height
    if narrator_path.is_file():
        narrator = _fit(_trim(Image.open(narrator_path).convert("RGBA")),
                        int(width * 0.42), band_h)
        narrator_top = height - narrator.height
        frame.paste(narrator, (width - narrator.width - int(width * 0.03),
                               narrator_top), narrator)

    hero = _hero_path(manifest, build_dir)
    if hero:
        car = _fit(_trim(Image.open(hero).convert("RGBA")),
                   width - 30, int((narrator_top - top) * 1.1))
        frame.paste(car, (int((width - car.width) / 2),
                          top + max(0, int((narrator_top - top - car.height) * 0.55))), car)

    # Sat against the narrator's feet rather than floating, so the two read
    # as one band instead of two objects adrift in white.
    _draw_specs(frame, manifest,
                (int(width * 0.04), narrator_top + int(band_h * 0.30)),
                width=int(width * 0.55))

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
