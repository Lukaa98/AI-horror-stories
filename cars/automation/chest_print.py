"""The car printed on the narrator's hoodie, made from the car in the video.

A fixed Ferrari on the hoodie is a costume. The car being reviewed on the
hoodie is a detail a viewer notices on the second video and enjoys on the
third, and it costs nothing, because the side profile is already in the
build.

The print is the photo in black and white rather than a two-tone screen
print. Thresholding to two tones was tried first and is not survivable
across a channel's worth of cars: the band that flattered a red Ferrari
turned a silver GT500 into blobs and a black R63 into a solid lump, because
where a car's tones fall depends on its paint. Desaturating does not care
what colour the car is, so every video's hoodie looks like the same
garment.
"""
from PIL import Image, ImageEnhance, ImageOps

# The print's working width. Large enough that the wheels survive, small
# enough that the base64 of it does not dominate the rig file.
PRINT_WIDTH = 300
# Enough contrast to read as a print rather than a washed-out photo, and
# dark enough to sit on a white hoodie without disappearing into it.
CONTRAST = 2.30
BRIGHTNESS = 0.72
# A photo with its background still attached has no silhouette to print.
MIN_TRANSPARENT_FRACTION = 0.10


def _trim(image):
    box = image.getbbox()
    return image.crop(box) if box else image


def build_chest_print(source_path, width=PRINT_WIDTH):
    """The hoodie print for one car, or None if the photo cannot carry one."""
    image = Image.open(source_path).convert("RGBA")
    alpha = image.split()[3]
    # Measured before trimming: trimming to the subject is what removes the
    # transparent margin this is looking for.
    transparent = sum(count for value, count in enumerate(alpha.histogram())
                      if value < 128)
    if transparent < image.width * image.height * MIN_TRANSPARENT_FRACTION:
        return None

    image = _trim(image)
    if not image.width or not image.height:
        return None
    image = image.resize((width, max(1, round(image.height * width / image.width))),
                         Image.Resampling.LANCZOS)

    grey = ImageOps.grayscale(image.convert("RGB"))
    grey = ImageEnhance.Contrast(grey).enhance(CONTRAST)
    grey = ImageEnhance.Brightness(grey).enhance(BRIGHTNESS)
    out = grey.convert("RGBA")
    out.putalpha(image.split()[3])
    return out
