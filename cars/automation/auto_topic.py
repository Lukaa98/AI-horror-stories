"""Best-effort auto-build of a RankingConfig for a car that isn't in topics.py.

Discovers generation-like Commons categories via a naming heuristic (see
scraper/car-source-scraper/src/discover-generations.js), scrapes images for
each, and produces a config with DRAFT placeholders for narration/stats --
we deliberately do NOT have an AI invent specs or sentiment without a real
research step, since confidently-wrong facts are worse than an honest
placeholder. A human must fill in real facts before this is publishable.
"""
import json
import re
import subprocess
from pathlib import Path

from ranking_engine import ROOT, RankEntry, RankingConfig

SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"


def slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "car"


def discover_generations(car_name):
    result = subprocess.run(
        ["node", "src/discover-generations.js", f"--car={car_name}", "--max-results=4"],
        cwd=SCRAPER_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def scrape_generation(category, topic_slug):
    subprocess.run(
        [
            "node", "src/scrape-commons-category.js",
            f"--category={category}",
            f"--topic={topic_slug}",
            "--limit=8",
            "--pool-size=200",
            "--download",
        ],
        cwd=SCRAPER_DIR,
        check=True,
    )


def pick_images(topic_slug, count=2):
    manifest_path = ROOT / "cars" / "output" / "sources" / topic_slug / "commons-manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    images = manifest.get("images", [])
    fronts = [i for i in images if i.get("shot_type") == "front"]
    others = [i for i in images if i.get("shot_type") != "front"]
    ordered = [*fronts, *others]
    return [ROOT / "cars" / "output" / "sources" / topic_slug / i["path"] for i in ordered[:count] if i.get("path")]


def build_auto_config(car_name):
    discovery = discover_generations(car_name)
    if not discovery.get("usable"):
        raise SystemExit(
            f"Could not auto-discover generation categories for {car_name!r} "
            f"(found {discovery.get('usable_count', 0)} usable candidates, need at least 2). "
            "This car may use a Commons naming convention the heuristic doesn't recognize -- "
            "add it to cars/automation/topics.py manually instead."
        )

    selected = discovery["selected"][:4]
    ranks = []
    car_slug = slugify(car_name)
    for i, item in enumerate(selected):
        rank_num = 4 - i  # order is by photo count, NOT a real sentiment/quality ranking
        category = item["category"]
        gen_label = category.replace(car_name, "").strip(" ()") or category
        topic_slug = f"{car_slug}-{slugify(gen_label)}"
        scrape_generation(category, topic_slug)
        images = pick_images(topic_slug)
        if not images:
            continue
        ranks.append(RankEntry(
            rank=rank_num,
            name=gen_label,
            years="YEARS TBD",
            images=images,
            label="UNVERIFIED DRAFT",
            stat="SPEC TBD",
            narration=f"{gen_label}. Facts and community verdict not yet verified -- this is a draft.",
        ))

    if len(ranks) != 4:
        raise SystemExit(
            f"Only found usable images for {len(ranks)}/4 discovered generations of {car_name!r}. "
            "Try again, or configure this car manually in cars/automation/topics.py."
        )

    highlight_words = {w.strip(",.").upper() for w in car_name.split()}
    return RankingConfig(
        slug=f"{car_slug}-generation-ranking-DRAFT",
        title=f"DRAFT - RANKING {car_name.upper()} GENERATIONS",
        title_highlight_words=highlight_words,
        theme=f"AUTO-DRAFT: {car_name} generation ranking -- unverified, needs a fact-check pass before publishing",
        close_narration="This is an auto-generated draft. Facts and ranking order need human review before publishing.",
        ranks=ranks,
    )
