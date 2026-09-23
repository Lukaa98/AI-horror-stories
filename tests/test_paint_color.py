"""Reading a car's paint off its photo, and knowing when not to."""
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))

import paint_color


def _cutout(rgb, size=(120, 120), coverage=0.55):
    """A block of colour on transparency, like a background-removed car."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    height = int(size[1] * coverage)
    image.paste(Image.new("RGBA", (size[0], height), (*rgb, 255)), (0, (size[1] - height) // 2))
    return image


def test_it_reads_the_paint_off_a_cut_out():
    red = paint_color.dominant_paint_color(_cutout((200, 30, 30)))
    assert red[0] > red[1] and red[0] > red[2]
    green = paint_color.dominant_paint_color(_cutout((40, 170, 60)))
    assert green[1] > green[0] and green[1] > green[2]
    blue = paint_color.dominant_paint_color(_cutout((30, 90, 200)))
    assert blue[2] > blue[0] and blue[2] > blue[1]


def test_a_photo_with_its_background_still_attached_is_refused():
    """Sky and tarmac are large, saturated and blue, and they beat the car.
    Measured on real builds: the same red GT2 RS reads as (209, 0, 25) cut
    out and (92, 164, 205) with its sky attached, and the 718 Spyder goes
    from red to navy the same way. Guessing is worse than the house colour."""
    opaque = Image.new("RGBA", (120, 120), (120, 170, 220, 255))
    assert paint_color.dominant_paint_color(opaque) == paint_color.DEFAULT_COLOR


def test_a_car_with_no_hue_gets_black():
    """Black, charcoal, silver and white all land here. There is nothing to
    match -- and white is the case where matching the paint cannot work at
    all, since white type on a white frame is invisible. Black is legible
    and invents no colour the car does not have."""
    for rgb in ((18, 18, 20), (56, 56, 60), (120, 122, 126), (178, 180, 184), (248, 248, 248)):
        assert paint_color.dominant_paint_color(_cutout(rgb)) == paint_color.DARK_CAR_COLOR


def test_the_house_red_is_only_for_having_nothing_to_read():
    """Not for a car without a hue -- that car still has a lightness. The
    red is for a photo that cannot be sampled at all."""
    from PIL import Image

    opaque = Image.new("RGBA", (120, 120), (120, 170, 220, 255))
    assert paint_color.dominant_paint_color(opaque) == paint_color.DEFAULT_COLOR


def test_a_colour_that_is_barely_there_is_not_used():
    """A mostly-transparent cut-out with a few coloured pixels is a badge or
    a brake caliper, not the body."""
    image = _cutout((200, 30, 30), coverage=0.04)
    assert paint_color.dominant_paint_color(image) == paint_color.DEFAULT_COLOR


def test_pale_paint_is_darkened_until_it_works_as_type():
    """A yellow car at its own brightness is unreadable as words on white."""
    yellow = paint_color.dominant_paint_color(_cutout((255, 225, 40)))
    assert max(yellow) / 255 <= paint_color.MAX_TEXT_VALUE + 0.02
    # Still recognisably yellow, just taken down.
    assert yellow[0] > yellow[2] and yellow[1] > yellow[2]


def test_the_video_falls_back_when_no_cut_out_is_available():
    """render_narrator_video gets whatever photos the build had. Only
    cut-outs can be sampled, so a build of plain JPEGs keeps the house red
    rather than taking a colour from somebody's driveway."""
    import narrator_video

    assert narrator_video.accent_for([]) == narrator_video.ACCENT_COLOR
    assert narrator_video.accent_for(["a/front.jpg", "b/side.jpg"]) == narrator_video.ACCENT_COLOR
    assert narrator_video.accent_for(["missing/front-nobg.png"]) == narrator_video.ACCENT_COLOR


def test_the_rival_car_never_supplies_the_colour():
    """The comparison car's cut-out sits in the same media list as this
    car's, and the order follows the scenes -- so a build whose comparison
    beat lands early would take the rival's paint for the whole video."""
    import narrator_video

    paths = ["images/manual-rival/rival-nobg.png", "images/manual/front-nobg.png"]
    picked = []
    original = narrator_video.accent_for

    # Record which file actually gets sampled.
    import paint_color as pc

    real = pc.dominant_paint_color

    def spy(path, default=None):
        picked.append(str(path))
        return (1, 2, 3)

    pc.dominant_paint_color = spy
    try:
        assert original(paths) == (1, 2, 3)
    finally:
        pc.dominant_paint_color = real
    assert picked == ["images/manual/front-nobg.png"], picked
