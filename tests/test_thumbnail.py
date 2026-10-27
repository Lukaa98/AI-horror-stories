"""The channel still: what it says, and what it refuses to inherit."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))

import thumbnail


def _manifest(**overrides):
    package = {
        "car": {"make": "Ferrari", "model": "488 Spider"},
        "title": "Turbocharged Performance",
        "key_specs": {"horsepower": "661 hp", "torque": "561 lb-ft",
                      "zero_to_sixty": "3.0 sec", "redline": "8,000 rpm",
                      "engine": "3.9L Turbocharged V8", "price": "$250K new"},
        "media": [{"path": "images/manual/front-nobg.png"}],
    }
    package.update(overrides)
    return package


def test_the_title_is_the_model_not_the_scene_headline():
    """manifest["title"] is a scene headline and names no car -- run #214's
    was "Turbocharged Performance", which on a channel page tells a browsing
    viewer nothing about what they are looking at."""
    assert thumbnail._title_text(_manifest()) == "488 SPIDER"
    assert thumbnail._title_text(_manifest(car={"make": "Audi", "model": ""})) == "AUDI"


def test_the_title_shrinks_rather_than_running_off_the_image(tmp_path):
    out = thumbnail.build_thumbnail(
        _manifest(car={"make": "Mercedes-Maybach", "model": "S680 4MATIC Executive Long"}),
        tmp_path, tmp_path / "t.jpg")
    image = Image.open(out)
    assert image.size == thumbnail.THUMBNAIL_SIZE


def test_it_renders_without_any_photos_at_all(tmp_path):
    """A build whose images did not survive still needs a thumbnail; a
    missing file must not take the whole upload down."""
    out = thumbnail.build_thumbnail(_manifest(media=[]), tmp_path, tmp_path / "t.jpg")
    assert Image.open(out).size == thumbnail.THUMBNAIL_SIZE


def test_only_the_specs_that_survive_being_small_are_shown(tmp_path):
    """Engine and price are long strings -- "3.9L Turbocharged V8", "$250K
    new, ~$200K now" -- and stop being readable well before the rest does."""
    assert "engine" not in thumbnail.THUMBNAIL_SPEC_FIELDS
    assert "price" not in thumbnail.THUMBNAIL_SPEC_FIELDS
    assert len(thumbnail.THUMBNAIL_SPEC_FIELDS) <= thumbnail.MAX_SPEC_ROWS


def test_a_long_model_is_shortened_rather_than_shrunk():
    """The model is the one thing a viewer reads in a grid, so it stays big.
    A full name only fits by dropping to type too small to read, and the
    tail is the part nobody says out loud anyway."""
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", thumbnail.THUMBNAIL_SIZE))
    width = thumbnail.THUMBNAIL_SIZE[0] - 70

    text, font = draw and thumbnail._headline(draw, "S680 4MATIC EXECUTIVE LONG", width)
    assert text == "S680 4MATIC"
    assert font.size >= thumbnail.COMFORTABLE_TITLE_SIZE

    # A name that already fits is left alone.
    text, font = thumbnail._headline(draw, "488 SPIDER", width)
    assert text == "488 SPIDER"

    # Never cut below two words: one word is usually not the car.
    text, _font = thumbnail._headline(draw, "CHALLENGER SRT SUPER STOCK", width)
    assert len(text.split()) == 2


def test_the_make_goes_under_the_car_only_when_it_adds_something():
    """The model is the headline; the make is what a viewer scanning a grid
    recognises. But when the title already fell back to the make, repeating
    it twice says nothing."""
    assert thumbnail._make_text(_manifest()) == "FERRARI"
    assert thumbnail._make_text(_manifest(car={"make": "Audi", "model": ""})) == ""


def test_the_narrator_asset_carries_the_current_channel_name():
    """The exported sprite sets read "CAR SHORTS LAB", and are deleted for
    it. A thumbnail built from one would have put the old channel name on
    the channel page under every single video."""
    asset = Path(__file__).resolve().parents[1] / thumbnail.NARRATOR_SPRITE
    assert asset.is_file(), f"{thumbnail.NARRATOR_SPRITE} is missing"
    assert "sprites" not in thumbnail.NARRATOR_SPRITE


def test_it_stays_under_youtubes_two_megabyte_ceiling(tmp_path):
    out = thumbnail.build_thumbnail(_manifest(), tmp_path, tmp_path / "t.jpg")
    assert out.stat().st_size <= thumbnail.MAX_UPLOAD_BYTES


def test_the_thumbnail_hoodie_carries_the_same_car_as_the_video(tmp_path):
    """The sprite ships with a blank hoodie so the two never disagree: if the
    print did not land, the thumbnail shows a plain hoodie rather than last
    build's car."""
    from PIL import Image

    build = tmp_path / "build" / "images" / "manual"
    build.mkdir(parents=True)
    car = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    for y in range(60, 150):
        for x in range(40, 360):
            car.putpixel((x, y), (200, 40, 40, 255))
    car.save(build / "side-nobg.png")

    manifest = {"media": [{"path": "images/manual/side-nobg.png"}]}
    blank = Image.open(Path(thumbnail.__file__).resolve().parents[2]
                       / thumbnail.NARRATOR_SPRITE).convert("RGBA")
    printed = thumbnail._print_the_car_on(
        blank, thumbnail._media_paths(manifest, tmp_path / "build"), tmp_path / "build")
    assert printed.tobytes() != blank.tobytes(), "the car should be on the hoodie"

    left, top, width, height = thumbnail.CHEST_RECT
    box = (int(blank.width * left), int(blank.height * top),
           int(blank.width * (left + width)), int(blank.height * (top + height)))
    assert printed.crop(box).tobytes() != blank.crop(box).tobytes()
    # Everything outside the chest is untouched -- the print does not leak
    # onto the face or the sleeves.
    above = (0, 0, blank.width, int(blank.height * top))
    assert printed.crop(above).tobytes() == blank.crop(above).tobytes()


def test_the_headline_clears_youtube_s_crop():
    """The frame is 9:16 and YouTube's Shorts grid cell is squarer, so it
    trims top and bottom. "R63 AMG" was drawn 2.5% down and came out of the
    feed with its letters sliced off."""
    import thumbnail

    assert thumbnail.SAFE_TOP >= 0.08, "a headline this high is inside the trim"
    source = Path(thumbnail.__file__).read_text()
    block = source[source.index("highlight = _highlight("):]
    block = block[:block.index("band_h = ")]
    # Both shapes -- the detail headline and the model name -- start from
    # the same safe top, and neither draws above it.
    assert block.count("top = int(height * SAFE_TOP)") == 1
    assert "draw.text(((width - line_w) / 2, top)" in block, "the wrapped headline"
    assert "draw.text(((width - title_w) / 2, top)" in block, "the model name"
    assert "int(height * 0.025)" not in block, "that was the cropped position"


def _maserati():
    """The real GranTurismo build: an engine bay, a cabin and three cut-outs."""
    return {
        "car": {"make": "Maserati", "model": "Granturismo Sport"},
        "scenes": [
            {"headline": "Ferrari-Built V8", "media_type": "exterior"},
            {"headline": "Assembled In Maranello", "media_type": "engine"},
            {"headline": "Sticky Switchgear", "media_type": "interior"},
            {"headline": "A headline far too long to read in a grid cell at all",
             "media_type": "detail"},
        ],
        "media": [
            {"path": "images/manual/front-nobg.png", "type": "exterior"},
            {"path": "images/manual/engine.jpg", "type": "engine"},
            {"path": "images/manual/interior.jpg", "type": "interior"},
            {"path": "images/manual/detail.jpg", "type": "detail"},
        ],
    }


def test_the_thumbnail_leads_on_a_detail_shot_rather_than_another_white_coupe(tmp_path):
    """Twenty-eight thumbnails, all the same shape: the model name, a
    cut-out of the car from the front, the specs and the narrator. Left to
    pick its own frame YouTube chose "STICKY SWITCHGEAR" over a photo of
    the cabin, and that is the one that reads in a feed.

    The pieces were already here -- scenes carry a short headline and pair
    with their photo by index."""
    import thumbnail

    for name in ("engine.jpg", "interior.jpg", "detail.jpg"):
        path = tmp_path / "images" / "manual" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 600), (90, 90, 90)).save(path)

    found = thumbnail._highlight(_maserati(), tmp_path)
    assert found is not None
    path, headline = found
    # The flaw wins over the engine bay: "Sticky Switchgear" is the thing
    # anybody considering one of these is already worried about, and a
    # bidder on that listing asked about it unprompted. Photo type only
    # breaks ties now.
    assert headline == "Sticky Switchgear"
    assert path.name == "interior.jpg"
    # And never an exterior cut-out: a detail is wanted whole, background
    # and all, the way the video shows it.
    assert "nobg" not in path.name

    # A headline too long for a grid cell is not used, so scene 4 is
    # skipped even though it has a detail photo.
    long_only = _maserati()
    long_only["scenes"] = long_only["scenes"][3:]
    long_only["media"] = long_only["media"][3:]
    assert thumbnail._highlight(long_only, tmp_path) is None

    # With nothing but exteriors it falls back to the old shape.
    exteriors = _maserati()
    exteriors["scenes"] = exteriors["scenes"][:1]
    exteriors["media"] = exteriors["media"][:1]
    assert thumbnail._highlight(exteriors, tmp_path) is None


def test_a_headline_is_wrapped_rather_than_cut_short():
    """_headline drops trailing words to keep the type big, which is right
    for a model name -- "S680 4MATIC Executive Long" is still the car as
    "S680 4MATIC" -- and wrong for a sentence. Routed through it,
    "Assembled In Maranello" came out as "ASSEMBLED IN", which says nothing
    and names nowhere."""
    import thumbnail

    frame = Image.new("RGB", (1080, 1920), (255, 255, 255))
    draw = ImageDraw.Draw(frame)

    for text in ("ASSEMBLED IN MARANELLO", "STICKY SWITCHGEAR",
                 "FERRARI-BUILT V8", "$130K TO $43K"):
        lines, font = thumbnail._wrapped_headline(draw, text, 1080 - 70)
        assert " ".join(lines).split() == text.split(), f"{text} lost words: {lines}"
        assert len(lines) <= 2
        assert font.size >= 58

    # One word that cannot fit is set at the floor rather than altered.
    lines, font = thumbnail._wrapped_headline(draw, "SUPERCALIFRAGILISTIC" * 3, 400)
    assert "".join(lines).count("SUPERCALIFRAGILISTIC") == 3
    assert lines, "never returns nothing to draw"
    assert thumbnail._wrapped_headline(draw, "", 900)[0] == []


def test_the_most_clickable_headline_wins_not_the_prettiest_photo():
    """Scored against every headline four real builds produced. The two
    sticky-interior beats come top, the Maranello pair next, and "Premium
    Seats", "Rear Transaxle" and "Wheel Horsepower" sort to the bottom
    where they belong."""
    import thumbnail

    def score(headline, make="Maserati", model="Granturismo Sport"):
        return thumbnail._clickability(headline, make, model)

    # A fault is the most clickable thing a car thumbnail can promise.
    assert score("Sticky Switchgear") > score("Assembled In Maranello")
    assert score("Sticky switches") > score("Maranello-built")
    # A borrowed name beats parts vocabulary.
    assert score("Assembled In Maranello") > score("Premium Seats", "Nissan", "Gt R")
    assert score("Pininfarina") > score("Factory Aerodynamics")
    # Money reads.
    assert score("$130K → $43K") > score("MC Sport Line")

    # Headlines are Title Case, so every word looks like a proper noun.
    # "Premium Seats" scored as a borrowed name and beat the engine beat on
    # a GT-R build until ordinary words were excluded.
    for dull in ("Premium Seats", "Rear Transaxle", "Wheel Horsepower",
                 "Hydraulic Steering", "Remote Exhaust Valves"):
        assert score(dull, "Nissan", "Gt R Premium") <= 0, dull
    # The car's own name is not a borrowed one.
    assert score("Maserati Badge") <= 0


def test_the_narrator_is_not_the_same_man_in_the_same_stance_every_time():
    """Twenty-eight thumbnails with one pose is why they stopped being
    used. Chosen from the build rather than at random, so a rebuild of the
    same car matches and two different cars do not."""
    import thumbnail

    def pick(make, model, title, words):
        return thumbnail._pick_narrator(
            {"car": {"make": make, "model": model}, "title": title, "word_count": words})

    a = pick("Maserati", "Granturismo Sport", "A", 150)
    b = pick("Nissan", "Gt R Premium", "B", 154)
    assert a[0] is not None and b[0] is not None
    # Same build, same man.
    assert pick("Maserati", "Granturismo Sport", "A", 150)[0].size == a[0].size
    # Only the steady poses, because the others put an arm where the print
    # goes.
    sprites = sorted(Path(thumbnail.__file__).resolve().parents[2]
                     .glob(thumbnail.SPRITE_POSE_GLOB))
    assert sprites, "the sprite library moved"
    assert all("pose-steady" in p.name for p in sprites)
    assert len(sprites) > 20, "not enough variety to be worth it"

    # Across a spread of builds both framings turn up.
    framings = {pick("Make", f"Model{n}", f"T{n}", 140 + n)[2] for n in range(30)}
    assert framings == {True, False}, "every build got the same framing"
