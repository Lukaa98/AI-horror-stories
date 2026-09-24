"""The listing a build turns into, checked against YouTube's own limits."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))

import youtube_metadata as ym


def _package(**overrides):
    package = {
        "car": {"make": "Ferrari", "model": "488 Spider"},
        "start_year": 2016, "end_year": 2019,
        "key_specs": {"horsepower": "661 hp", "torque": "561 lb-ft",
                      "zero_to_sixty": "3.0 sec", "redline": "8,000 rpm",
                      "engine": "3.9L Turbocharged V8", "price": "$250K new"},
    }
    package.update(overrides)
    return package


def test_title_caser_fixes_what_research_title_cased():
    """Research writes make/model through a title-caser, which is right for
    "Spider" and wrong for "Nsx" -- and this goes on a public title."""
    assert ym.fix_name("Nsx") == "NSX"
    assert ym.fix_name("911 Gt2 Rs Weissach") == "911 GT2 RS Weissach"
    assert ym.fix_name("Challenger Srt Super Stock") == "Challenger SRT Super Stock"
    # Ordinary words are left alone.
    assert ym.fix_name("488 Spider") == "488 Spider"


def test_a_written_title_wins_and_the_spec_title_is_the_fallback():
    assert ym.title_for(_package()) == "2016-2019 Ferrari 488 Spider — 661 hp 🔥"
    written = _package(youtube_title="Is the 488 Spider still a real Ferrari? 🔥")
    assert ym.title_for(written) == "Is the 488 Spider still a real Ferrari? 🔥"


def test_titles_stay_inside_youtubes_limit_even_when_research_overruns():
    """A title over 100 characters is rejected by the API outright, so the
    cap is enforced here rather than discovered on upload."""
    long_title = _package(youtube_title="x" * 400)
    assert len(ym.title_for(long_title)) <= ym.TITLE_LIMIT
    long_name = _package(car={"make": "Mercedes-Maybach", "model": "S680 4MATIC " * 12})
    assert len(ym.title_for(long_name)) <= ym.TITLE_LIMIT


def test_description_carries_the_car_the_numbers_and_the_flag():
    text = ym.description_for(_package())
    assert "2016-2019 Ferrari 488 Spider" in text
    assert "661 hp" in text and "561 lb-ft" in text and "8,000 rpm redline" in text
    assert "🇮🇹" in text
    assert "#Ferrari" in text and "#488Spider" in text


def test_specs_research_could_not_verify_are_left_out_not_printed_as_na():
    """"n/a" is a real value in key_specs -- an electric has no redline --
    and printing it in a description reads as a broken template."""
    text = ym.description_for(_package(key_specs=dict(
        _package()["key_specs"], redline="n/a", torque="n/a")))
    assert "n/a" not in text.lower()
    assert "redline" not in text.lower()
    assert "661 hp" in text


def test_tags_stay_inside_the_total_length_budget():
    """YouTube caps tags on total length, not count, and rejects the whole
    upload when it is exceeded."""
    tags = ym.tags_for(_package(car={"make": "Mercedes-Benz", "model": "S680 4MATIC " * 20}))
    assert sum(len(tag) + 1 for tag in tags) <= ym.TAGS_TOTAL_LIMIT
    assert tags


def test_a_build_with_nothing_filled_in_still_produces_a_usable_listing():
    """Older manifests have no key_specs at all. They must still upload."""
    bare = {"car": {"make": "Audi", "model": "R8"}, "start_year": None, "end_year": None}
    meta = ym.build_metadata(bare)
    assert meta["title"].strip() and len(meta["title"]) <= ym.TITLE_LIMIT
    assert "Audi R8" in meta["description"]
    assert meta["tags"]


def test_the_photos_are_credited_by_name_not_by_link():
    """A listing URL dies when the auction is archived, and the video
    outlives it by years. The point of the line is the credit, not the
    click."""
    assert ym.photo_credit(
        "https://carsandbids.com/auctions/KYak81qw/1996-porsche-911-turbo") == "Cars & Bids"
    assert ym.photo_credit("https://www.carsandbids.com/x") == "Cars & Bids"
    assert ym.photo_credit("https://bringatrailer.com/listing/y") == "Bring a Trailer"
    # A host we have no name for gets no line at all, rather than a bare URL.
    assert ym.photo_credit("https://example.com/z") == ""
    assert ym.photo_credit("") == ""

    package = {"car": {"make": "Porsche", "model": "911 Turbo"}, "key_specs": {"horsepower": "400 hp"}}
    described = ym.description_for(
        package, credit_url="https://carsandbids.com/auctions/KYak81qw/1996-porsche-911-turbo")
    assert "Photos via Cars & Bids" in described
    assert "https://" not in described, "no link in the description"

    assert "Photos via" not in ym.description_for(package, credit_url="")
