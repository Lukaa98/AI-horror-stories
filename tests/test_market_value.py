"""Pricing a model from comparable sales, and the two ways that goes wrong."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))

import market_value

# The 993 results page as it actually reads, which is where both traps live.
COMPS_993 = [
    {"title": "1997 Porsche 911 Turbo S Coupe", "price": 550000, "sold": True, "ended": "10/16/23"},
    {"title": "1998 Porsche 911 Carrera 4S Coupe", "price": 192500, "sold": True, "ended": "8/12/26"},
    {"title": "1996 Porsche 911 Turbo", "price": 187993, "sold": True, "ended": "4/26/23"},
    {"title": "1996 Porsche 911 Turbo", "price": 180000, "sold": True, "ended": "4/5/23"},
    {"title": "1997 Porsche 911 Turbo Coupe", "price": 155500, "sold": False, "ended": "8/25/23"},
    {"title": "1997 Porsche 911 Turbo", "price": 145000, "sold": False, "ended": "8/11/23"},
    {"title": "1995 Porsche 911 Carrera Coupe", "price": 110000, "sold": True, "ended": "6/27/23"},
]


def test_a_turbo_s_is_not_a_turbo():
    """Containment is what makes these look alike, and on this page the
    Turbo S sold for $550,000 against the Turbo's $180,000 -- three times
    the price, one letter apart."""
    assert not market_value.same_variant("1996 Porsche 911 Turbo",
                                         "1997 Porsche 911 Turbo S Coupe", "Porsche")
    assert not market_value.same_variant("1996 Porsche 911 Turbo",
                                         "1998 Porsche 911 Carrera 4S Coupe", "Porsche")


def test_body_style_does_not_make_it_a_different_car():
    """A Turbo Coupe and a Turbo are the same car for pricing."""
    assert market_value.same_variant("1996 Porsche 911 Turbo",
                                     "1997 Porsche 911 Turbo Coupe", "Porsche")
    assert market_value.same_variant("2018 Ferrari 488 Spider",
                                     "2017 Ferrari 488 Spider", "Ferrari")


def test_an_auction_that_did_not_sell_is_not_a_price():
    """"Bid to" means the reserve was never met -- evidence of what a car
    did NOT sell for. Averaging those in states something false and drags
    the figure down: here it would pull $185,000 toward $167,000."""
    summary = market_value.summarise_comps(COMPS_993, "1996 Porsche 911 Turbo", "Porsche")
    assert summary["count"] == 2
    assert summary["median"] == 185000
    assert all(item["price"] > 170000 for item in summary["examples"])


def test_the_whole_993_page_prices_a_turbo_at_turbo_money():
    """The end-to-end case: run #217 quoted $70,000 for this car."""
    summary = market_value.summarise_comps(COMPS_993, "1996 Porsche 911 Turbo", "Porsche")
    assert 175000 <= summary["median"] <= 195000
    assert summary["low"] == 180000 and summary["high"] == 190000


def test_a_different_variant_on_the_same_page_gets_its_own_number():
    carrera = market_value.summarise_comps(
        COMPS_993 + [{"title": "1996 Porsche 911 Carrera Coupe", "price": 76500,
                      "sold": True, "ended": "3/1/24"}],
        "1996 Porsche 911 Carrera Coupe", "Porsche")
    assert carrera["median"] < 120000


def test_too_few_comparable_sales_returns_nothing():
    """Saying nothing beats quoting the wrong variant, which is the exact
    failure this exists to prevent."""
    assert market_value.summarise_comps(COMPS_993, "1996 Porsche 911 Speedster", "Porsche") is None
    assert market_value.summarise_comps([], "1996 Porsche 911 Turbo", "Porsche") is None


def test_stale_sales_are_dropped_only_when_enough_recent_ones_remain():
    """Four-year-old sales stop describing today's market -- but two real
    old sales still beat no number at all."""
    recent = [dict(c, ended="6/1/26") for c in COMPS_993]
    assert market_value.summarise_comps(recent, "1996 Porsche 911 Turbo",
                                        "Porsche", today_year=2026)["count"] == 2
    # Only old ones: kept, because the alternative is silence.
    assert market_value.summarise_comps(COMPS_993, "1996 Porsche 911 Turbo",
                                        "Porsche", today_year=2026)["count"] == 2
