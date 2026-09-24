"""The hoodie print, which has to survive every paint colour a channel covers."""
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))

import chest_print


def _cutout(tmp_path, colour):
    """A car-shaped blob on transparency, which is what the pipeline hands us."""
    image = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    for y in range(60, 150):
        for x in range(40, 360):
            image.putpixel((x, y), colour + (255,))
    path = tmp_path / f"side-{colour[0]}.png"
    image.save(path)
    return path


def test_the_print_is_grey_whatever_the_car_is_painted(tmp_path):
    """The reason this is not a two-tone screen print: thresholding puts a
    silver car and a black car on opposite sides of the band, so they come
    out as different kinds of graphic. Desaturating does not care."""
    for colour in [(206, 32, 38), (12, 12, 14), (198, 200, 205)]:
        print_image = chest_print.build_chest_print(_cutout(tmp_path, colour))
        assert print_image is not None
        red, green, blue, _ = print_image.split()
        assert red.getextrema() == green.getextrema() == blue.getextrema()


def test_a_photo_with_its_background_still_on_is_refused(tmp_path):
    """There is no silhouette to print, and the hoodie would carry a
    rectangle of somebody's driveway."""
    path = tmp_path / "opaque.png"
    Image.new("RGBA", (400, 200), (140, 90, 60, 255)).save(path)
    assert chest_print.build_chest_print(path) is None


def test_the_print_keeps_the_car_shaped_hole_around_it(tmp_path):
    """The alpha has to come through, or the print is a grey box."""
    print_image = chest_print.build_chest_print(_cutout(tmp_path, (206, 32, 38)))
    assert print_image.split()[3].getextrema() == (255, 255)
    assert print_image.width == chest_print.PRINT_WIDTH
