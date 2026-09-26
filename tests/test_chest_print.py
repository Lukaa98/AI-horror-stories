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


def test_the_hoodie_never_carries_the_other_car():
    """The rival's cut-out and the race composite are both in the media list.
    Either would print somebody else's car on the narrator's chest -- the
    race frame would print two."""
    import narrator_video

    paths = [
        "build/images/manual/front-nobg.png",
        "build/images/manual/rival-side-nobg.png",
        "build/images/manual/race-side-nobg.png",
        "build/images/manual/side-nobg.png",
    ]
    assert narrator_video.chest_car_source(paths).endswith("manual/side-nobg.png")
    assert narrator_video.chest_car_source(
        ["build/images/manual/rival-front-nobg.png"]) is None


def test_the_rig_prints_on_an_addressable_node_with_a_clear_chest():
    """The renderer swaps the car into the loaded page rather than rewriting
    a 90KB rig per build, so the node has to be findable. The drawstrings
    are gone because they crossed the print."""
    rig = (Path(__file__).resolve().parents[1]
           / "narrator/narrator-rig-v21.html").read_text()
    assert '<image id="chest-car"' in rig
    assert 'd="M177 258V322M226 258V322"' not in rig, "the drawstrings crossed the print"
    assert '<circle class="black" cx="177" cy="326"' not in rig


def test_the_channel_mark_bounces_in_the_bottom_left():
    """It moved off the narrator's chest, where a head-and-shoulders shot
    cropped it out of the video entirely."""
    import narrator_video

    clip = narrator_video._channel_mark_clip((1080, 1920), 10.0)
    assert clip is not None
    left, low = clip.pos(0.0)
    _, high = clip.pos(narrator_video.CHANNEL_MARK_BOUNCE_SECONDS / 2)
    assert left < 1080 * 0.1, "hugs the left edge"
    assert low > 1920 * 0.8, "sits near the bottom"
    assert low - high == narrator_video.CHANNEL_MARK_BOUNCE_PX


def test_the_hoodie_carries_the_car_and_nothing_else():
    """The tach badge lived on the chest before it moved to the frame's
    corner. Nothing of it is allowed to survive on the garment -- a second
    mark competes with the print at the size the character actually appears."""
    import re

    for name in ("narrator-rig-v21.html", "narrator-rig-v4.html"):
        rig = (Path(__file__).resolve().parents[1] / "narrator" / name).read_text()
        block = re.search(r'<g id="chest-art">(.*?)</g>', rig, re.S)
        assert block, name
        assert block.group(1).count("<") == 1, f"{name}: the print is the only thing on it"
        assert block.group(1).lstrip().startswith('<image id="chest-car"'), name
        # The tach's two signature colours, anywhere in the rig.
        assert "#E52020" not in rig and "#8E9096" not in rig, name


def test_the_old_channel_name_is_nowhere_in_the_repo():
    """It survived as pixels in two pre-rendered sprite sets long after the
    channel was renamed. Those sets are also the reason this is a hard
    deletion rather than a re-export: a pre-rendered character cannot carry
    the build's own car, so every video would wear the same one."""
    root = Path(__file__).resolve().parents[1]
    assert not (root / "narrator" / "sprites-v4").exists()
    assert not (root / "narrator" / "sprites-v3").exists()
    workflow = (root / ".github/workflows/cars-research.yml").read_text()
    assert "narrator_character" not in workflow
    assert "NARRATOR_RENDERER: v21" in workflow


def test_the_print_clears_the_hoodie_outline():
    """At 158 the car ran to the seams on both sides, so the torso's own
    outline touched the photo's edge and the print read as a decal stuck
    over the garment rather than something on it."""
    import re

    rig = (Path(__file__).resolve().parents[1] / "narrator/narrator-rig-v21.html").read_text()
    box = re.search(r'<image id="chest-car" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', rig)
    assert box
    left, width = float(box.group(1)), float(box.group(2))
    # The torso spans rig x 104-276.
    assert left - 104 >= 12, "clearance on the left seam"
    assert 276 - (left + width) >= 12, "clearance on the right seam"


def test_the_video_holds_a_beat_after_the_last_word():
    """It used to end on the exact sample the narration did: the closing
    question's decay was cut and the music stopped mid-fade, which read as
    the file running out rather than the video finishing."""
    import narrator_video

    assert narrator_video.END_PAD_SECONDS >= 0.4
    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/narrator_video.py").read_text()
    assert "duration = audio.duration + END_PAD_SECONDS" in source
    assert ".set_duration(duration)" in source, "the mix has to cover the pad too"


def test_the_print_is_made_from_the_side_profile_the_build_already_resolved(tmp_path):
    """The SLR build printed its head-on front cut-out. Its side profile was
    a manual photo that never entered the scenes' media list, so the scan
    fell through to the only "nobg" path there was -- a nearly square front
    shot, which scales down to fit the chest and reads as a badge rather
    than a car. The build knows which cut-out is the side profile; it is
    what the drag race runs."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    import narrator_video

    from PIL import Image

    def cutout(name, size):
        path = tmp_path / name
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        # A solid block inside a transparent margin: a silhouette to print.
        block = Image.new("RGBA", (size[0] // 2, size[1] // 2), (90, 90, 90, 255))
        image.paste(block, (size[0] // 4, size[1] // 4))
        image.save(path)
        return str(path)

    front = cutout("front-nobg.png", (400, 400))
    side = cutout("side-nobg.png", (1200, 400))

    # The scan alone finds only the front, exactly as the SLR build did.
    assert narrator_video.chest_car_source([front]) == front

    made = narrator_video.chest_print_for([front], tmp_path / "work", side)
    from PIL import Image as _Image
    printed = _Image.open(made)
    assert printed.width > printed.height * 2, \
        "the side profile is a wide shot; a square print means the front was used"

    # Without one, the scan is still the fallback rather than nothing.
    assert narrator_video.chest_print_for([front], tmp_path / "work2", None) is not None
    # And a path that is not on disk does not take precedence over one that is.
    assert narrator_video.chest_print_for([front], tmp_path / "work3",
                                          str(tmp_path / "gone.png")) is not None


def test_a_cutout_is_trimmed_by_its_alpha_alone(tmp_path):
    """getbbox() on RGBA calls a pixel non-zero if any channel is, so a
    cut-out that left colour behind its transparent pixels would not trim,
    and an untrimmed print is a small car in a large empty box once it is
    scaled to fit the chest."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    from chest_print import build_chest_print

    from PIL import Image

    # Transparent everywhere, but every pixel still carries colour.
    image = Image.new("RGBA", (1200, 400), (120, 40, 40, 0))
    block = Image.new("RGBA", (600, 100), (90, 90, 90, 255))
    image.paste(block, (300, 150))
    path = tmp_path / "coloured-transparency.png"
    image.save(path)

    printed = build_chest_print(path)
    assert printed is not None
    assert abs(printed.width / printed.height - 6.0) < 0.5, \
        "it should trim to the 600x100 block, not keep the 1200x400 frame"
