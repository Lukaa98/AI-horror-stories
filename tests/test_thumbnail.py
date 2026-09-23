"""The channel still: what it says, and what it refuses to inherit."""
import sys
from pathlib import Path

from PIL import Image

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
    """Six rows at thumbnail scale is a grey smudge. Three is the limit, and
    engine and price are the two that read worst."""
    assert "engine" not in thumbnail.THUMBNAIL_SPEC_FIELDS
    assert "price" not in thumbnail.THUMBNAIL_SPEC_FIELDS
    assert len(thumbnail.THUMBNAIL_SPEC_FIELDS) <= 3


def test_the_narrator_asset_carries_the_current_channel_name():
    """The exported sprites still read "CAR SHORTS LAB". A thumbnail built
    from one would put the old channel name on the channel page under every
    single video."""
    asset = Path(__file__).resolve().parents[1] / thumbnail.NARRATOR_SPRITE
    assert asset.is_file(), f"{thumbnail.NARRATOR_SPRITE} is missing"
    assert "sprites-v4" not in thumbnail.NARRATOR_SPRITE


def test_it_stays_under_youtubes_two_megabyte_ceiling(tmp_path):
    out = thumbnail.build_thumbnail(_manifest(), tmp_path, tmp_path / "t.jpg")
    assert out.stat().st_size <= thumbnail.MAX_UPLOAD_BYTES
