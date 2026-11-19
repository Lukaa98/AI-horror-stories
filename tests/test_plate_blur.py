"""Hiding a number plate by hand, where the automatic pass did not."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars/automation"))

def test_a_hand_drawn_box_hides_a_plate_and_nothing_else(tmp_path):
    """The automatic pass reads each photo with a vision model and fails
    open, which is the right trade for a pass nobody asked for and the
    wrong one when you can see the plate and it did not."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    from plate_blur import blur_box

    photo = tmp_path / "rear.jpg"
    image = Image.new("RGB", (800, 600), (40, 40, 48))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 36)
    # A readable plate, because a flat rectangle comes out of a blur as the
    # same flat rectangle and cannot tell a working blur from a missing one.
    draw.rectangle([300, 330, 500, 390], fill="white")
    draw.text((320, 345), "XZL 7203", fill="black", font=font)
    draw.text((60, 60), "SHELBY", fill="white", font=font)
    image.save(photo, quality=95)

    before = np.asarray(Image.open(photo).convert("L"), dtype=float)
    assert blur_box(photo, {"x": 300 / 800, "y": 330 / 600,
                            "w": 200 / 800, "h": 60 / 600}) is True
    after = np.asarray(Image.open(photo).convert("L"), dtype=float)

    changed = np.abs(after - before)
    assert changed[330:390, 300:500].mean() > 20, "the plate is still readable"
    # The badge is the same badge. (Not zero: the file is re-encoded.)
    assert changed[60:110, 60:260].mean() < 3, "the blur leaked outside its box"


def test_an_unreadable_box_is_ignored_rather_than_raised(tmp_path):
    """A privacy preference must never be the reason a build dies, and the
    automatic pass still gets its turn afterwards."""
    from PIL import Image

    from plate_blur import blur_box

    photo = tmp_path / "car.jpg"
    Image.new("RGB", (64, 48), (120, 120, 120)).save(photo)
    for bad in (None, {}, "0,0,1,1", [0, 0, 1, 1], {"x": "a", "y": 0, "w": 1, "h": 1},
                {"x": 0, "y": 0, "w": 0, "h": 0.5}, {"x": 1.5, "y": 0, "w": 0.2, "h": 0.2},
                {"x": 0, "y": 0, "w": -0.5, "h": 0.5}):
        assert blur_box(photo, bad) is False, bad
    assert blur_box(tmp_path / "missing.jpg", {"x": 0, "y": 0, "w": 1, "h": 1}) is False


def test_the_hand_drawn_box_runs_before_the_automatic_pass():
    """Both of them, in that order: the box is certain and the model is a
    guess, and the model can still find a second plate the hand missed."""
    import inspect

    import single_car_short

    for func in (single_car_short.gather_manual_media,
                 single_car_short.gather_extra_media):
        body = inspect.getsource(func)
        assert "blur_box(" in body, func.__name__
        assert body.index("blur_box(") < body.index("blur_license_plates("), func.__name__


def test_the_slot_plate_boxes_survive_the_photos_input():
    """Same trap engine_clip was written around: --photos is filtered to
    strings, so a map of boxes would be dropped without a word."""
    from single_car_short import _photos_argument

    kept = _photos_argument(json.dumps({
        "front": "https://example.com/front.jpg",
        "rear": "https://example.com/rear.jpg",
        "blur": {"rear": {"x": 0.35, "y": 0.55, "w": 0.25, "h": 0.15}, "front": "nonsense"},
    }))
    assert kept["rear"] == "https://example.com/rear.jpg"
    assert kept["blur"] == {"rear": {"x": 0.35, "y": 0.55, "w": 0.25, "h": 0.15}}
