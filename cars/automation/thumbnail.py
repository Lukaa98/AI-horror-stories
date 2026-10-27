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
from paint_color import dominant_paint_color

# How far down the headline starts. The frame is 9:16 and YouTube's Shorts
# grid cell is squarer, so it crops top and bottom: a title sat 2.5% down
# came out of the feed with its letters sliced off.
SAFE_TOP = 0.09

# The video's own shape. These are Shorts, and a Short's tile on the channel
# page and in the Shorts feed is vertical -- a 16:9 still gets cropped to
# fit it, which takes the sides off the car and half the spec table with
# them. Same aspect as the render, so nothing is lost.
THUMBNAIL_SIZE = (1080, 1920)
BACKGROUND = (255, 255, 255)
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # YouTube rejects anything larger.

# The numbers that still read when the whole image is an inch wide. Engine
# and price are excluded: both are long strings ("3.9L Turbocharged V8",
# "$250K new, ~$200K now") that turn to mush well before the rest does.
THUMBNAIL_SPEC_FIELDS = ("horsepower", "torque", "zero_to_sixty", "redline")
MAX_SPEC_ROWS = 4
# Rendered from narrator-rig-v21.html, which is the live rig. The exported
# sprites under narrator/sprites-v4 still read "CAR SHORTS LAB" on the
# hoodie -- the old channel name -- and a thumbnail carrying it would put
# that on the channel page under every video.
#
# The sprite is exported with a blank hoodie and the car printed on at build
# time, so the thumbnail carries the same car the video does. The rect is
# where #chest-car lands in the sprite, measured off a render of the rig
# rather than worked out from the rig's coordinates -- the stage applies a
# transform between the two.
NARRATOR_SPRITE = "narrator/thumbnail-narrator.png"
CHEST_RECT = (0.21143, 0.36414, 0.56762, 0.0612)


def _print_the_car_on(narrator, media_paths, build_dir):
    """Put this build's car on the hoodie. Returns the sprite either way."""
    from narrator_video import chest_car_source
    from chest_print import build_chest_print

    source = chest_car_source(media_paths)
    if not source:
        return narrator
    try:
        art = build_chest_print(source)
    except OSError:
        return narrator
    if art is None:
        return narrator

    left, top, width, height = CHEST_RECT
    box_w = int(narrator.width * width)
    box_h = int(narrator.height * height)
    # The rig scales the print to fit its box and centres it, so a car with
    # a different aspect sits in the middle rather than being stretched.
    art = _fit(art, box_w, box_h)
    narrator = narrator.copy()
    narrator.alpha_composite(
        art,
        (int(narrator.width * left) + (box_w - art.width) // 2,
         int(narrator.height * top) + (box_h - art.height) // 2),
    )
    return narrator


def _trim(image):
    box = image.getbbox()
    return image.crop(box) if box else image


def _fit(image, width, height):
    """Scale to fit inside a box, keeping proportions."""
    copy = image.copy()
    copy.thumbnail((width, height), Image.Resampling.LANCZOS)
    return copy


def _deepen(color, factor=0.72):
    """The same colour, darker. Used to separate the marque from the model."""
    return tuple(max(0, int(channel * factor)) for channel in color)


def _make_text(manifest):
    """The marque, for under the car.

    The model is the headline because it is what is specific; the make is
    what a viewer scanning a grid recognises first, so it gets its own line
    rather than being crammed into the title.
    """
    car = manifest.get("car") or {}
    make = str(car.get("make") or "").strip()
    model = str(car.get("model") or "").strip()
    # Nothing to add when the title already had to fall back to the make.
    return make.upper() if (make and model) else ""


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


# Below this the model stops being the thing a viewer notices first, which
# is the only job it has.
COMFORTABLE_TITLE_SIZE = 116


def _headline(draw, model, max_width):
    """The model, shortened rather than shrunk.

    A full name like "S680 4MATIC Executive Long" only fits by dropping to
    type too small to read in a grid, and the tail of it is the part nobody
    says out loud. So trailing words come off until what is left can be set
    large -- "S680 4MATIC" at full size beats the whole name at half of it.
    Never below two words, because one word is usually not the car.
    """
    words = [word for word in str(model or "").split() if word]
    while len(words) > 2:
        font = _fit_text(draw, " ".join(words), max_width, start_size=190, min_size=40)
        if font.size >= COMFORTABLE_TITLE_SIZE:
            break
        words = words[:-1]
    text = " ".join(words)
    return text, _fit_text(draw, text, max_width, start_size=190, min_size=56)


def _wrapped_headline(draw, text, max_width, max_lines=2, start=176, min_size=58):
    """A headline set across up to two lines, never shortened.

    _headline drops trailing words to keep the type big, which is right for
    a model name -- "S680 4MATIC Executive Long" is still the car as
    "S680 4MATIC" -- and wrong for a sentence. Routed through it,
    "Assembled In Maranello" came out as "ASSEMBLED IN", which says
    nothing and names nowhere.

    So this wraps instead. Words are never dropped: if it will not fit on
    one line it goes on two, and if it will not fit on two it is set at the
    floor and allowed to be small rather than altered.
    """
    words = [word for word in str(text or "").split() if word]
    if not words:
        return [], _font(min_size)
    size = start
    while size >= min_size:
        font = _font(size)
        lines, current = [], ""
        for word in words:
            trial = f"{current} {word}".strip()
            if current and draw.textlength(trial, font=font) > max_width:
                lines.append(current)
                current = word
            else:
                current = trial
        lines.append(current)
        if len(lines) <= max_lines and all(
                draw.textlength(line, font=font) <= max_width for line in lines):
            return lines, font
        size -= 4
    font = _font(min_size)
    return [" ".join(words)], font


def _media_paths(manifest, build_dir):
    """Every media file this build actually has on disk, as full paths."""
    build_dir = Path(build_dir)
    paths = []
    for item in manifest.get("media") or []:
        relative = item.get("path")
        if not relative:
            continue
        full = build_dir / relative
        if full.is_file() and str(full) not in paths:
            paths.append(str(full))
    return paths


# What a detail shot is, best first. An engine bay and a cabin are the two
# that read at thumbnail size and say something an exterior cannot.
HIGHLIGHT_TYPES = ("engine", "interior", "detail", "wheel")
# A headline has to be readable in a grid cell, so it is a few words or it
# is not used. "Sticky Switchgear" works; a sentence does not.
MAX_HIGHLIGHT_WORDS = 4


def _highlight(manifest, build_dir):
    """The detail photo worth leading with, and the headline that goes with it.

    Every thumbnail was the same shape: the model name, a cut-out of the
    car from the front, the specs and the narrator. Twenty-eight of them
    and the only thing that changed was the car. YouTube, left to pick its
    own frame, chose "STICKY SWITCHGEAR" over a photo of the cabin -- a
    specific, odd detail rather than another white coupe -- and that is the
    one that reads in a feed.

    The pieces were already here. Scenes carry a short headline and pair
    with their photo by index, so the thumbnail can use the same beat the
    video does. Returns (path, headline), or None to keep the old shape.
    """
    build_dir = Path(build_dir)
    scenes = manifest.get("scenes") or []
    media = manifest.get("media") or []
    found = []
    for index, scene in enumerate(scenes):
        headline = str(scene.get("headline") or "").strip()
        if not headline or len(headline.split()) > MAX_HIGHLIGHT_WORDS:
            continue
        kind = str(scene.get("media_type") or "").strip().lower()
        if kind not in HIGHLIGHT_TYPES or index >= len(media):
            continue
        relative = media[index].get("path")
        # The cut-outs are exteriors with their background removed; a
        # detail shot is wanted whole, background and all, the way the
        # video shows it.
        if not relative or "nobg" in relative:
            continue
        full = build_dir / relative
        if full.is_file():
            found.append((HIGHLIGHT_TYPES.index(kind), index, full, headline))
    if not found:
        return None
    found.sort()
    _, _, path, headline = found[0]
    return path, headline


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
    ordered = {field: index for index, field in enumerate(THUMBNAIL_SPEC_FIELDS)}
    rows = sorted(
        (row for row in _spec_rows(manifest.get("key_specs") or {}) if row[0] in ordered),
        key=lambda row: ordered[row[0]],
    )[:MAX_SPEC_ROWS]
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
        label_w = draw.textlength(label.upper(), font=label_font)
        draw.text((x + 22, top + 22), label.upper(), font=label_font, fill=TABLE_COLOR)
        # "561 lb-ft" and "8,000 rpm" are wider than "3.0 sec"; a row that
        # cannot fit both at full size shrinks its value rather than letting
        # the two run through each other.
        row_font = value_font
        while row_font.size > 30 and label_w + draw.textlength(value, font=row_font) > width - 60:
            row_font = _font(row_font.size - 3)
        value_w = draw.textlength(value, font=row_font)
        draw.text((x + width - 22 - value_w, top + 14 + (value_font.size - row_font.size) // 2),
                  value, font=row_font, fill=(20, 20, 20))
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

    # The model is set in the car's own paint, so a red Ferrari and a green
    # AMG look like different videos rather than one template. A black,
    # white or silver car has no hue to borrow and keeps the channel red.
    hero = _hero_path(manifest, build_dir)
    accent = dominant_paint_color(hero, default=ACCENT_COLOR) if hero else ACCENT_COLOR

    # A detail shot and its own headline when the build has one, the model
    # name and a cut-out when it does not.
    highlight = _highlight(manifest, build_dir)
    # Below the safe top, not against the edge. The frame is 9:16 and
    # YouTube's grid cell is not, so it crops top and bottom: "R63 AMG" sat
    # 4% down, which is inside the trim, and came out of the feed with its
    # letters sliced off. Nothing that has to be read goes in the top band.
    top = int(height * SAFE_TOP)
    if highlight:
        lines, title_font = _wrapped_headline(draw, highlight[1].upper(), width - 70)
        for line in lines:
            line_w = draw.textlength(line, font=title_font)
            draw.text(((width - line_w) / 2, top), line, font=title_font, fill=accent)
            top += int(title_font.size * 1.06)
        top += int(height * 0.02)
    else:
        title, title_font = _headline(draw, _title_text(manifest), width - 70)
        title_w = draw.textlength(title, font=title_font)
        draw.text(((width - title_w) / 2, top), title, font=title_font, fill=accent)
        top += title_font.size + int(height * 0.03)

    # A car photo is far wider than it is tall, so fitting one to this frame's
    # width leaves a band of white above and below it. Rather than let that
    # sit as two gaps, the car hangs directly under the title and the
    # narrator is tall enough to close the space from below.
    band_h = int(height * (0.50 if highlight else 0.66))
    band_top = height - band_h

    narrator_path = Path(__file__).resolve().parents[2] / NARRATOR_SPRITE
    narrator_top = height
    narrator_x = width
    if narrator_path.is_file():
        narrator = _print_the_car_on(
            Image.open(narrator_path).convert("RGBA"),
            _media_paths(manifest, build_dir), build_dir)
        narrator = _fit(_trim(narrator), int(width * (0.34 if highlight else 0.42)), band_h)
        narrator_top = height - narrator.height
        narrator_x = width - narrator.width - int(width * 0.03)
        frame.paste(narrator, (narrator_x, narrator_top), narrator)

    car_bottom = top
    if highlight:
        # A detail photo keeps its background, so it is placed as a panel
        # rather than floated on the white the cut-outs sit on.
        shot = Image.open(highlight[0]).convert("RGB")
        shot = _fit(shot, width - 60, int((narrator_top - top) * 0.92))
        shot_y = top + max(0, int((narrator_top - top - shot.height) * 0.30))
        shot_x = int((width - shot.width) / 2)
        # A hairline in the car's own paint, so the panel reads as placed
        # rather than pasted.
        draw.rectangle([shot_x - 4, shot_y - 4, shot_x + shot.width + 3,
                        shot_y + shot.height + 3], outline=accent, width=4)
        frame.paste(shot, (shot_x, shot_y))
        car_bottom = shot_y + shot.height
    elif hero:
        car = _fit(_trim(Image.open(hero).convert("RGBA")),
                   width - 30, int((narrator_top - top) * 1.02))
        car_y = top + max(0, int((narrator_top - top - car.height) * 0.38))
        frame.paste(car, (int((width - car.width) / 2), car_y), car)
        car_bottom = car_y + car.height

    make = _make_text(manifest)
    if make:
        # Centred on the space beside the narrator, not on the whole frame:
        # "FERRARI" clears him, "MERCEDES-BENZ" would have run into his head.
        lane = max(int(width * 0.5), narrator_x - int(width * 0.02))
        make_font = _fit_text(draw, make, lane - 60, start_size=104, min_size=40)
        make_w = draw.textlength(make, font=make_font)
        # Sat right under the car rather than centred in the space below it.
        # A car photo is wide and short, so that space is enormous, and
        # centring in it left the marque floating a long way from anything.
        gap_bottom = narrator_top + int(band_h * 0.30)
        make_y = min(car_bottom + int(height * 0.02),
                     gap_bottom - make_font.size - int(height * 0.02))
        # The marque takes the paint colour too, a shade deeper than the
        # model above it so the two lines read as a hierarchy rather than
        # one block of colour.
        draw.text(((lane - make_w) / 2, make_y), make, font=make_font, fill=_deepen(accent))

    # Sat against the narrator's feet rather than floating, so the two read
    # as one band instead of two objects adrift in white.
    _draw_specs(frame, manifest,
                (int(width * 0.04), narrator_top + int(band_h * 0.46)),
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
