"""Title, description and tags for a finished build, ready to upload.

The manifest already holds everything a listing needs -- make, model, the
generation years, the six headline specs -- so this is a read of that rather
than a second round of research. The one thing it cannot derive is a good
hook line, so a build whose research returned "youtube_title" uses it and
everything else falls back to a spec title that cannot be wrong.
"""
import json
import re

# Research writes make/model through a title-caser, which is right for
# "Spider" and wrong for "Nsx". These are the ones that are initialisms or
# carry a fixed house style, and they show up in a public title.
NAME_FIXES = {
    "nsx": "NSX", "gtr": "GTR", "gt": "GT", "gts": "GTS", "gt2": "GT2", "gt3": "GT3",
    "gt4": "GT4", "gti": "GTI", "gtd": "GTD", "rs": "RS", "srt": "SRT", "amg": "AMG",
    "sti": "STI", "wrx": "WRX", "zr1": "ZR1", "z06": "Z06", "zl1": "ZL1", "ss": "SS",
    "lt1": "LT1", "lt4": "LT4", "lt5": "LT5", "lm7": "LM7", "tt": "TT", "r8": "R8",
    "m3": "M3", "m4": "M4", "m5": "M5", "m2": "M2", "x5": "X5", "ct5": "CT5",
    "v": "V", "sv": "SV", "svj": "SVJ", "sq5": "SQ5", "rs3": "RS3", "rs4": "RS4",
    "rs5": "RS5", "rs6": "RS6", "rs7": "RS7", "s4": "S4", "s5": "S5", "s6": "S6",
    "p530": "P530", "s680": "S680", "4matic": "4MATIC", "r63": "R63", "e63": "E63",
    "c63": "C63", "g63": "G63", "sl63": "SL63", "cls63": "CLS63", "718": "718",
    "911": "911", "918": "918", "959": "959", "991": "991", "992": "992",
    "488": "488", "458": "458", "430": "430", "360": "360", "720s": "720S",
    "570s": "570S", "650s": "650S", "12c": "12C", "p1": "P1", "f8": "F8",
    "sf90": "SF90", "lp610": "LP610", "lp640": "LP640", "iii": "III", "iv": "IV",
    "v6": "V6", "v8": "V8", "v10": "V10", "v12": "V12", "3lz": "3LZ", "3zr": "3ZR",
}

# A flag reads as a car-channel signature rather than decoration, so it is
# the make's country rather than anything about the car.
MAKE_FLAGS = {
    "ferrari": "🇮🇹", "lamborghini": "🇮🇹", "maserati": "🇮🇹", "alfa romeo": "🇮🇹",
    "pagani": "🇮🇹", "abarth": "🇮🇹", "lancia": "🇮🇹",
    "porsche": "🇩🇪", "bmw": "🇩🇪", "mercedes": "🇩🇪", "mercedes-benz": "🇩🇪",
    "mercedes-amg": "🇩🇪", "mercedes-maybach": "🇩🇪", "audi": "🇩🇪",
    "volkswagen": "🇩🇪", "opel": "🇩🇪", "rui": "🇩🇪",
    "toyota": "🇯🇵", "nissan": "🇯🇵", "honda": "🇯🇵", "acura": "🇯🇵", "mazda": "🇯🇵",
    "subaru": "🇯🇵", "mitsubishi": "🇯🇵", "lexus": "🇯🇵", "infiniti": "🇯🇵",
    "suzuki": "🇯🇵", "datsun": "🇯🇵",
    "ford": "🇺🇸", "chevrolet": "🇺🇸", "dodge": "🇺🇸", "cadillac": "🇺🇸",
    "chrysler": "🇺🇸", "gmc": "🇺🇸", "jeep": "🇺🇸", "tesla": "🇺🇸", "buick": "🇺🇸",
    "pontiac": "🇺🇸", "plymouth": "🇺🇸", "shelby": "🇺🇸", "hennessey": "🇺🇸",
    "jaguar": "🇬🇧", "aston martin": "🇬🇧", "bentley": "🇬🇧", "mclaren": "🇬🇧",
    "lotus": "🇬🇧", "land rover": "🇬🇧", "range rover": "🇬🇧", "mini": "🇬🇧",
    "rolls-royce": "🇬🇧", "tvr": "🇬🇧", "morgan": "🇬🇧",
    "volvo": "🇸🇪", "koenigsegg": "🇸🇪", "saab": "🇸🇪",
    "bugatti": "🇫🇷", "renault": "🇫🇷", "peugeot": "🇫🇷", "alpine": "🇫🇷",
    "citroen": "🇫🇷", "ds": "🇫🇷",
    "skoda": "🇨🇿", "hyundai": "🇰🇷", "kia": "🇰🇷", "genesis": "🇰🇷",
    "seat": "🇪🇸", "cupra": "🇪🇸",
}

# YouTube's own ceilings. A title over 100 characters is rejected outright;
# tags are capped on the total, not the count.
TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000
TAGS_TOTAL_LIMIT = 460  # 500 with headroom, since YouTube counts quotes
FIXED_TAGS = ("cars", "car review", "performance cars", "CarTok")


def fix_name(value):
    """"Nsx" -> "NSX", "911 Gt2 Rs Weissach" -> "911 GT2 RS Weissach"."""
    parts = re.split(r"(\s+|-)", str(value or "").strip())
    out = []
    for part in parts:
        key = part.lower()
        out.append(NAME_FIXES.get(key, part))
    return "".join(out)


def _years(package):
    start, end = package.get("start_year"), package.get("end_year")
    if start and end and start != end:
        return f"{start}-{end}"
    return str(start or end or "").strip()


def _spec(package, field):
    return str((package.get("key_specs") or {}).get(field) or "").strip()


def _is_real(value):
    return bool(value) and value.lower() not in ("n/a", "na", "-")


def title_for(package):
    """The hook line research wrote, or a spec title that cannot be wrong."""
    written = str(package.get("youtube_title") or "").strip()
    if written:
        return written[:TITLE_LIMIT].strip()
    car = package.get("car") or {}
    make, model = fix_name(car.get("make")), fix_name(car.get("model"))
    year = _years(package)
    name = " ".join(part for part in (year, make, model) if part)
    horsepower = _spec(package, "horsepower")
    if _is_real(horsepower):
        candidate = f"{name} — {horsepower} 🔥"
        if len(candidate) <= TITLE_LIMIT:
            return candidate
    return f"{name} 🔥"[:TITLE_LIMIT]


def _hashtags(package):
    car = package.get("car") or {}
    make, model = fix_name(car.get("make")), fix_name(car.get("model"))
    tags = []
    for value in (make, model):
        squashed = re.sub(r"[^A-Za-z0-9]", "", value)
        if squashed:
            tags.append(f"#{squashed}")
    # The model's own words carry the search terms people actually type --
    # "GT2", "Weissach" -- which the squashed version buries.
    for word in re.split(r"[\s\-]+", model):
        squashed = re.sub(r"[^A-Za-z0-9]", "", word)
        if squashed and len(squashed) > 1 and f"#{squashed}" not in tags:
            tags.append(f"#{squashed}")
    engine = _spec(package, "engine").lower()
    for needle, tag in (("turbo", "#Turbo"), ("supercharg", "#Supercharged"),
                        ("v8", "#V8"), ("v10", "#V10"), ("v12", "#V12"),
                        ("flat-six", "#FlatSix"), ("electric", "#EV")):
        if needle in engine and tag not in tags:
            tags.append(tag)
    tags.extend(["#CarTok", "#Cars", "#performancecars"])
    return " ".join(dict.fromkeys(tags))


# Where the photos came from, by name. Not a link: the listing is taken
# down when the auction is archived, which leaves a dead URL at the bottom
# of a video that outlives it by years, and the point of the line is the
# credit rather than the click.
PHOTO_SOURCES = {
    "carsandbids.com": "Cars & Bids",
    "bringatrailer.com": "Bring a Trailer",
}


def photo_credit(url):
    """The name of the site a listing URL belongs to, or ""."""
    host = re.sub(r"^https?://", "", str(url or "")).split("/")[0].lower()
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    for domain, name in PHOTO_SOURCES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return ""


def description_for(package, credit_url=""):
    car = package.get("car") or {}
    make, model = fix_name(car.get("make")), fix_name(car.get("model"))
    name = " ".join(part for part in (_years(package), make, model) if part)
    flag = MAKE_FLAGS.get(str(car.get("make") or "").strip().lower(), "")

    figures = [value for value in (
        _spec(package, "horsepower"),
        _spec(package, "torque"),
        (lambda v: f"0-60 in {v}" if _is_real(v) else "")(_spec(package, "zero_to_sixty")),
        (lambda v: f"{v} redline" if _is_real(v) else "")(_spec(package, "redline")),
    ) if _is_real(value)]

    lines = [title_for(package), ""]
    spec_line = f"Taking a look at the {name}"
    if figures:
        spec_line += " — " + ", ".join(figures)
    lines.append(f"{spec_line}. {flag}💨".strip())
    lines += ["", "Subscribe for more fire car content. 🔥", "", _hashtags(package)]
    credit = photo_credit(credit_url)
    if credit:
        lines += ["", f"Photos via {credit}"]
    return "\n".join(lines)[:DESCRIPTION_LIMIT]


def tags_for(package):
    """Plain keyword tags, trimmed to YouTube's total-length budget."""
    car = package.get("car") or {}
    make, model = fix_name(car.get("make")), fix_name(car.get("model"))
    candidates = [make, model, f"{make} {model}".strip()]
    candidates += [w for w in re.split(r"[\s\-]+", model) if len(w) > 1]
    engine = _spec(package, "engine")
    if _is_real(engine):
        candidates.append(engine)
    candidates += list(FIXED_TAGS)

    tags, used = [], 0
    for tag in dict.fromkeys(t.strip() for t in candidates if t and t.strip()):
        if used + len(tag) + 1 > TAGS_TOTAL_LIMIT:
            continue
        tags.append(tag)
        used += len(tag) + 1
    return tags


def build_metadata(package, credit_url=""):
    return {
        "title": title_for(package),
        "description": description_for(package, credit_url=credit_url),
        "tags": tags_for(package),
    }


if __name__ == "__main__":
    import sys
    with open(sys.argv[1]) as handle:
        print(json.dumps(build_metadata(json.load(handle)), indent=2, ensure_ascii=False))
