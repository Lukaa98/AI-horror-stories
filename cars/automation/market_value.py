"""What a model actually sells for, from dated comparable results.

The listing gives one car's price. A live auction gives an unfinished one.
Neither is what a viewer means by "what does it cost" -- and a video outlives
both, so this reads the site's own results page instead and reports the
middle of recent sales.

Two distinctions do all the work here, and getting either wrong produces a
confident, wrong number:

"Bid to" is not a sale. It is an auction that failed to meet its reserve, so
it is evidence of what a car did NOT sell for. Averaging those in drags the
figure down and states something false.

A trim is not a substring. "911 Turbo S" contains "911 Turbo", and on the
993 results page the Turbo S sold for $550,000 against the Turbo's $180,000
-- three times the price, one letter apart.
"""
import re
import statistics

# Words that describe the body, not the car's place in the range. A Turbo
# Coupe and a Turbo are the same car for pricing; a Turbo and a Turbo S are
# not.
BODY_WORDS = frozenset({
    "coupe", "cabriolet", "convertible", "targa", "roadster", "spyder",
    "spider", "sedan", "wagon", "hatchback", "suv", "hardtop", "fastback",
})
# Ignored when comparing trims: years, the make, and filler.
NOISE_WORDS = frozenset({"the", "a", "an", "with", "and", "edition"})
# Sales older than this stop describing today's market.
MAX_COMP_AGE_YEARS = 4
MIN_COMPS = 2


def _tokens(title, make=""):
    """The trim-defining words of a title, lowercased."""
    words = re.findall(r"[A-Za-z0-9]+", str(title or "").lower())
    make_words = set(re.findall(r"[A-Za-z0-9]+", str(make or "").lower()))
    return [
        word for word in words
        if not re.fullmatch(r"(19|20)\d{2}", word)
        and word not in BODY_WORDS
        and word not in NOISE_WORDS
        and word not in make_words
    ]


def same_variant(our_title, comp_title, make=""):
    """Whether a comp is the same car as ours, ignoring body style.

    An exact token match rather than containment, because containment is
    what makes a Turbo S look like a Turbo.
    """
    return _tokens(our_title, make) == _tokens(comp_title, make)


def _ended_year(comp):
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", str(comp.get("ended") or ""))
    if not match:
        return None
    year = int(match.group(3))
    return year + 2000 if year < 100 else year


def _round_price(value):
    """A price a person would say out loud."""
    if value >= 100_000:
        return round(value / 5_000) * 5_000
    if value >= 20_000:
        return round(value / 1_000) * 1_000
    return round(value / 500) * 500


def summarise_comps(comps, our_title, make="", today_year=None):
    """The middle of recent, comparable, completed sales -- or None.

    None is a real answer: with nothing comparable, saying nothing beats
    quoting the wrong variant, which is how a 400hp Turbo once got a base
    Carrera's value.
    """
    matched = [
        comp for comp in comps or []
        if comp.get("sold") and comp.get("price")
        and same_variant(our_title, comp.get("title"), make)
    ]
    if today_year:
        fresh = [c for c in matched
                 if (_ended_year(c) or today_year) >= today_year - MAX_COMP_AGE_YEARS]
        # Only narrow to recent sales when enough of them survive; two old
        # real sales beat no number at all.
        if len(fresh) >= MIN_COMPS:
            matched = fresh
    if len(matched) < MIN_COMPS:
        return None

    prices = sorted(comp["price"] for comp in matched)
    return {
        "count": len(prices),
        "median": _round_price(statistics.median(prices)),
        "low": _round_price(prices[0]),
        "high": _round_price(prices[-1]),
        "examples": [
            {"title": c["title"], "price": c["price"], "ended": c.get("ended", "")}
            for c in matched[:4]
        ],
    }
