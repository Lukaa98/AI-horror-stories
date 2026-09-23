"""The car's own paint colour, for the text that sits next to it.

A red headline over a red Ferrari and a green one over a green AMG make the
frame look designed rather than templated. The colour has to come from the
photo, because nothing else in the manifest knows it -- research records the
model, not what this particular car was painted.

The hard part is not finding a colour, it is ignoring everything that is not
paint: tyres and glass are near-black, the background and the highlights are
near-white, and a chrome trim strip is grey. What is left after those go is
almost always the body.
"""
import colorsys
from collections import Counter

from PIL import Image

# Below this saturation a pixel is glass, chrome, shadow or background, not
# paint. Above the value ceiling it is a specular highlight, which is the
# colour of the light source rather than of the car.
MIN_SATURATION = 0.32
MIN_VALUE = 0.18
# Specular highlights are white, so saturation already excludes them and
# this only has to leave room for genuinely bright paint. Any ceiling below
# 1.0 discarded a bright yellow car as a reflection of itself.
MAX_VALUE = 1.0
# Hue buckets, in degrees. Narrow enough to separate red from orange, wide
# enough that one metallic panel does not split across two.
HUE_BUCKET = 15
# A car has to be meaningfully coloured before its colour is used. White,
# silver, black and gunmetal cars have no hue worth reading, and guessing
# one from a stray reflection is worse than not trying.
MIN_COLOURED_FRACTION = 0.10
# The cut-out has to actually contain a car. A handful of opaque pixels is
# a badge or a brake caliper left behind by background removal, and its
# colour is not the body's.
MIN_SUBJECT_FRACTION = 0.06

# When there is nothing to sample at all -- no cut-out, no pixels -- the
# channel's own red.
DEFAULT_COLOR = (226, 32, 32)
# A car with no hue still has a lightness, and that decides what its text
# can be. White and silver cannot supply their own colour: white type on a
# white frame is invisible and silver is barely better, so they get gold --
# the same treatment pale paint already gets, and the one warm colour that
# still reads at headline size on white.
PALE_CAR_COLOR = (209, 148, 32)
# Black and charcoal keep theirs, because black type on white is the one
# case where matching the car and staying legible are the same answer.
DARK_CAR_COLOR = (26, 26, 28)
# Where the line between them falls, on the median value of the body.
PALE_CAR_VALUE = 0.42
# Text sits on white, so a pale colour has to be taken down before it is
# legible -- a yellow car at its own brightness is unreadable as type.
MAX_TEXT_VALUE = 0.82
MIN_TEXT_SATURATION = 0.55


# A cut-out is mostly transparent. Anything less than this has a background
# still attached.
MIN_TRANSPARENT_FRACTION = 0.10


def _sample(image, limit=160):
    """Opaque pixels, at a size where this is fast.

    Returns None when the image still has its background, because then this
    cannot work: sky and tarmac are large, saturated and blue, and they beat
    the car. Measured on real builds -- the same red GT2 RS reads as
    (209, 0, 25) cut out and (92, 164, 205) with its sky attached, and the
    718 Spyder goes from red to navy the same way.
    """
    copy = image.copy()
    copy.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    copy = copy.convert("RGBA")
    pixels = list(copy.getdata())
    if not pixels:
        return None
    transparent = sum(1 for _r, _g, _b, a in pixels if a < 40)
    if transparent / len(pixels) < MIN_TRANSPARENT_FRACTION:
        return None
    opaque = [(r, g, b) for r, g, b, a in pixels if a > 200]
    if len(opaque) / len(pixels) < MIN_SUBJECT_FRACTION:
        return None
    return opaque


def dominant_paint_color(path_or_image, default=DEFAULT_COLOR):
    """The body colour, or the channel red when there isn't one."""
    image = path_or_image if isinstance(path_or_image, Image.Image) else Image.open(path_or_image)
    pixels = _sample(image)
    if not pixels:
        return default

    buckets, members = Counter(), {}
    for red, green, blue in pixels:
        hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if saturation < MIN_SATURATION or not (MIN_VALUE < value <= MAX_VALUE):
            continue
        bucket = int(hue * 360) // HUE_BUCKET
        buckets[bucket] += 1
        members.setdefault(bucket, []).append((hue, saturation, value))

    if not buckets or buckets.most_common(1)[0][1] / len(pixels) < MIN_COLOURED_FRACTION:
        return _achromatic_color(pixels, default)
    bucket = buckets.most_common(1)[0][0]

    # The median of the winning bucket, not the mean: one blown-out highlight
    # drags a mean but not a median.
    group = sorted(members[bucket], key=lambda hsv: hsv[2])
    hue, saturation, value = group[len(group) // 2]
    return text_safe(hue, saturation, value)


def _achromatic_color(pixels, default):
    """What a white, silver or black car gets.

    Falling back to the house red for all of them threw away the one thing
    these cars do tell you, which is whether they are light or dark -- and a
    white car is the case where matching the paint literally cannot work.
    """
    if not pixels:
        return default
    values = sorted(colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[2] for r, g, b in pixels)
    median = values[len(values) // 2]
    return DARK_CAR_COLOR if median < PALE_CAR_VALUE else PALE_CAR_COLOR


def text_safe(hue, saturation, value):
    """The same hue, forced to something readable as type on white."""
    value = min(value, MAX_TEXT_VALUE)
    saturation = max(saturation, MIN_TEXT_SATURATION)
    # Yellow and lime read far lighter than their value suggests, so they
    # need taking down further or the words disappear into the page.
    degrees = hue * 360
    if 40 <= degrees <= 90:
        value = min(value, 0.62)
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return (int(red * 255), int(green * 255), int(blue * 255))
