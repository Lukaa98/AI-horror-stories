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


def test_the_script_is_never_told_to_mention_where_the_price_came_from():
    """A viewer is being told what the car costs, not shown a listing. The
    auction is how we know the number, not part of the story -- and it also
    dates the video the moment that auction ends."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    import single_car_short

    block = single_car_short._market_block(
        {"count": 2, "median": 185000, "low": 180000, "high": 190000, "examples": []})
    assert "they go for about $185,000 today" in block
    assert "Never mention the auction" in block
    for phrasing in ("this one sold for", "currently bid to", "one recently went for"):
        assert phrasing in block, f"{phrasing!r} should be named as banned"


def test_an_unfinished_auction_alone_is_not_used_as_a_price():
    """With no comparable sales and only a live bid, there is no number
    worth saying: the auction could land anywhere, and no figure beats a
    wrong one."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    import single_car_short

    block = single_car_short._listing_facts_block(
        {"title": "1996 Porsche 911 Turbo", "price_text": "High Bid $267,000",
         "auction_state": "bidding", "facts": {}, "sections": {}})
    assert "skip the current-value claim entirely" in block
    assert "Never narrate the auction" in block


def test_the_comps_scraper_waits_for_the_model_link_and_can_derive_it():
    """Run #218 reported "No model results link on the listing page" for a
    page that has one: the quick-facts table is client-rendered and the link
    was looked for the instant the document was ready. A live auction also
    lacks the "this auction has ended, see more X here" banner, so the rows
    may be the only route -- hence the fallback that builds the URL from
    Make and Model."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "scraper/car-source-scraper/src/scrape-carsandbids-comps.js").read_text()
    assert "waitForSelector" in source, "the model link is rendered client side"
    assert "/search/${slug(rows.make)}/${slug(rows.model)}" in source, \
        "a live listing may only expose the model through its quick-facts rows"


def test_the_price_label_only_claims_a_sale_when_there_was_one():
    """Three states reach the prompt and only one of them is a sale. Run
    #218 got "unknown" -- the wrapper had dropped the field -- fell to the
    sold wording, and reported a standing bid as the model's value."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    import single_car_short

    def label_for(state):
        return single_car_short._listing_facts_block(
            {"title": "1996 Porsche 911 Turbo", "price_text": "High Bid $267,000",
             "auction_state": state, "facts": {}, "sections": {}})

    assert "What it actually sold for" in label_for("sold")
    assert "NOT a sale" in label_for("bidding")
    assert "do not" in label_for("unknown") and "What it actually sold for" not in label_for("unknown")


def test_a_page_of_the_wrong_variant_yields_no_price():
    """Run #219's real scrape: twenty 993 results, every one a Carrera, not a
    Turbo among them. The guard has to produce nothing rather than pricing a
    400hp Turbo off base-Carrera sales -- which is what the run did."""
    carreras = [
        {"title": "1998 Porsche 911 Carrera 4S Coupe", "price": 192500, "sold": True},
        {"title": "1997 Porsche 911 Carrera Cabriolet", "price": 86500, "sold": True},
        {"title": "1996 Porsche 911 Carrera Coupe", "price": 76500, "sold": True},
        {"title": "1998 Porsche 911 Carrera S Coupe", "price": 101500, "sold": True},
    ]
    assert market_value.summarise_comps(carreras, "1996 Porsche 911 Turbo", "Porsche") is None


def test_the_comps_scraper_takes_the_title_from_the_link_not_the_card():
    """A results card carries the listing's subtitle under its title, and
    sweeping the card's text swallowed it: "1995 Porsche 911 Carrera Coupe
    6-Speed Manual". Those trailing words are not trim words, but an exact
    token match cannot know that, so every comp read as a different variant
    and run #219 found no comparable sales at all."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "scraper/car-source-scraper/src/scrape-carsandbids-comps.js").read_text()
    assert 'a[href^="${slug}"]' in source, "the title comes off the title link"


def test_the_comps_scraper_harvests_while_it_scrolls():
    """Run #219 read one screenful, so #220 scrolled for more -- and came
    back with five instead of twenty. The list is virtualised: rows that
    scroll out of view are unmounted, so scrolling to the bottom and then
    reading leaves only what is still on screen. Every pass has to bank what
    it can see before moving on."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "scraper/car-source-scraper/src/scrape-carsandbids-comps.js").read_text()
    assert "readMountedCards" in source
    assert "scrollBy" in source and "scrollHeight" not in source, \
        "one viewport at a time, so no row is passed between two harvests"
    assert "found.has(comp.url)" in source, "merged by auction url across passes"


def test_a_finished_build_is_not_thrown_away_on_a_failed_push():
    """Run #223 rendered its video, wrote its thumbnail and its metadata,
    then lost all of it to a GitHub 500 on the last line. Four builds
    pushing to one branch at once makes the race routine, and the loser of
    a race and a flaky remote both look the same from here."""
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/cars-research.yml").read_text()
    step = workflow[workflow.index("Commit single-car short"):]
    step = step[:step.index("- name: Commit voice audition")]
    assert "for attempt in" in step, "one push attempt is not enough"
    assert step.count("git pull --rebase") >= 1, "rebase between attempts, not just retry"


def test_an_invented_current_value_is_caught_rather_than_discouraged():
    """Run #219 declined to name a figure and run #220 put "$70,000" on a
    993 Turbo worth nearly three times that -- same code, same prompt, two
    different answers. With no comparable sales the original price is the
    only money figure there is evidence for, so a second one is checked for
    rather than asked against."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
    import single_car_short

    package = {"scenes": [
        {"narration": "Four hundred horsepower, and the badge barely says so."},
        {"narration": "Originally priced at around $110,000, these models now trade "
                      "for about $70,000, reflecting strong enthusiast demand."},
    ]}
    problems = single_car_short._script_violations(package, "Porsche", "911 Turbo", market=None)
    assert any("$70,000" in problem for problem in problems)

    # One figure is the original price, which is a fact, not a guess.
    package["scenes"][1]["narration"] = "Originally priced at around $110,000, and the good ones have not been cheap since."
    assert not [p for p in single_car_short._script_violations(package, "Porsche", "911 Turbo", market=None)
                if "more than one price" in p]

    # With comparable sales behind it, a second figure is the whole point.
    package["scenes"][1]["narration"] = "Originally $110,000; they go for about $185,000 today."
    assert not [p for p in single_car_short._script_violations(
        package, "Porsche", "911 Turbo", market={"median": 185000}) if "more than one price" in p]


def test_a_price_you_type_is_the_price_that_is_used():
    """Scraping got this wrong twice in a week on the same car -- $70,000
    once, $267,000 the other -- and both times the right answer was four
    characters somebody already knew."""
    assert market_value.stated_value("300k") == 300_000
    assert market_value.stated_value("$185,000") == 185_000
    assert market_value.stated_value("~$1.2m") == 1_200_000
    assert market_value.stated_value("") is None
    assert market_value.stated_value(None) is None
    assert market_value.stated_value("tbd") is None

    stated = market_value.value_from_input("185k")
    assert stated["median"] == 185_000 and stated["source"] == "stated"
    # No range and no examples: there is no sample behind a stated figure,
    # and inventing a spread around it would be the same lie in a new place.
    assert stated["low"] == stated["high"] == 185_000
    assert stated["examples"] == []


def test_a_stated_price_skips_the_scraper_entirely():
    """The point of the field is to stop the pipeline arguing with itself."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/single_car_short.py").read_text()
    assert "market = value_from_input(args.current_price)" in source
    assert "if market is None and args.auction_url:" in source, \
        "comps are the fallback, not a second opinion"

    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/cars-research.yml").read_text()
    assert "current_price:" in workflow
    assert "--current-price" in workflow


def test_the_price_comes_from_the_best_source_available():
    """Three sources, ordered by how much each knows about the car on
    screen: what the person typed, then what this exact car sold for, then
    what the model generally goes for."""
    # A finished sale is a price. An unfinished one is not.
    sale = market_value.value_from_sale("Sold for $277,000", "sold")
    assert sale["median"] == 277_000 and sale["source"] == "sale"
    assert market_value.value_from_sale("Sold After for $113,000", "sold")["median"] == 113_000
    assert market_value.value_from_sale("High Bid $267,000", "bidding") is None
    assert market_value.value_from_sale("Bid to $331,000", "bidding") is None
    # Sold wording on a page still bidding is a comparable further down it,
    # not this car.
    assert market_value.value_from_sale("Sold for $277,000", "bidding") is None
    assert market_value.value_from_sale("", "sold") is None

    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]
              / "cars/automation/single_car_short.py").read_text()
    typed = source.index("market = value_from_input(args.current_price)")
    sold = source.index("market = value_from_sale(listing_facts.get(\"price_text\")")
    comps = source.index("comps = scrape_auction_comps(")
    assert typed < sold < comps, "typed price, then this car's sale, then the model's"
