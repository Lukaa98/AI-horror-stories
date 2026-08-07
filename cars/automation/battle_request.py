"""Research stage for a "startup sound battle": 3-5 user-named cars, each
reduced to its full generation/body-style year range, then searched for an
exterior-only cold-start clip and a few exterior photos.

Unlike research_request.py (AI picks and ranks candidates from a free-text
request), the cars here are exactly what the user typed -- this module only
finds media for them. Writes cars/battles/<battle-id>/battle.json.
"""
import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from cars_and_bids import discover_entry_engine_videos, scrape_auction_images, scrape_entry_images
from engine_video import MAX_THUMBNAIL_CLASSIFICATIONS, prepare_engine_clip
from openai_retry import with_openai_retry
from plate_blur import blur_license_plates

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
BATTLES_ROOT = ROOT / "cars" / "battles"

MIN_CARS = 3
MAX_CARS = 5
MIN_CLIP_SECONDS = 3.0
MAX_CLIP_SECONDS = 8.0
PHOTOS_PER_CAR = 4
EXTERIOR_SHOT_TYPES = {"front", "rear", "side", "front_3q", "rear_3q", "exterior", "detail", "wheel"}
# Each car's search+scrape+classify pipeline is I/O-bound (network, ffmpeg,
# OpenAI calls) and fully independent of the others, so running them
# concurrently is a real wall-clock win. Capped modestly since a trim
# fallback can multiply one car's own work 2-4x and each attempt already
# makes many sequential OpenAI calls -- too much parallelism just trades
# wall-clock time for API rate-limit backoff instead of avoiding the cost.
MAX_CONCURRENT_CARS = 3
# Was cut to 8 to speed battle mode up, but a real run showed multiple
# clearly-labeled, clean rear-shot startup videos (e.g. titled "Engine
# Start - Exhaust") sitting later in the interleaved candidate order than
# an 8-classification budget ever reached, so cars with real good clips
# were failing outright. rear_shot_only also means fewer of any given
# batch of classified candidates survive to begin with, so a small budget
# now costs accuracy more than it used to. Concurrent car processing
# already covers most of the wall-clock win this was trying to buy, so
# restored to the flagship pipeline's own budget.
BATTLE_MAX_THUMBNAIL_CLASSIFICATIONS = MAX_THUMBNAIL_CLASSIFICATIONS
# How far into the source video to search for a distant rev after a long
# idle (e.g. cold start at 0:10, revs at 1:00) -- if found, it's cut as a
# separate short clip and stitched on right after the startup clip.
DISTANT_REV_SEARCH_SECONDS = 300.0

GENERATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["start_year", "end_year", "generation_label"],
    "properties": {
        "start_year": {"type": "number"},
        "end_year": {"type": "number"},
        "generation_label": {"type": "string"},
    },
}


def slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "car"


def lookup_generation_range(make, model, year):
    """Widen a single model year to its full generation/body-style production
    run, so entering "2015 Mustang" also searches 2015-2023 S550 listings
    instead of only literal 2015-titled ones."""
    try:
        from openai import OpenAI

        prompt = (
            f"For the {year} {make} {model}, identify the exact generation/body-style "
            "production run this model year belongs to (not a trim or facelift-only split). "
            "Return only JSON with start_year, end_year (the full first-to-last production "
            "year of that generation/body style), and generation_label (e.g. 'S550', "
            "'Sixth generation')."
        )
        response = with_openai_retry(lambda: OpenAI().responses.create(
            model="gpt-4o-mini",
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "generation_range",
                    "strict": True,
                    "schema": GENERATION_SCHEMA,
                }
            },
        ))
        data = json.loads(response.output_text.strip())
        start_year, end_year = int(data["start_year"]), int(data["end_year"])
        if start_year <= int(year) <= end_year:
            return start_year, end_year, str(data.get("generation_label") or "")
    except Exception as exc:
        print(f"[battle] Generation lookup failed for {year} {make} {model}: {exc}")
    return int(year), int(year), ""


def trim_fallback_ladder(trim):
    """Progressively broaden a trim by dropping its last word, e.g.
    "GT4 RS" -> ["GT4 RS", "GT4", ""] -- the last rung is always the bare
    model with no trim at all, so there's always somewhere to land."""
    words = trim.split()
    ladder = [" ".join(words[:i]) for i in range(len(words), 0, -1)]
    if not ladder or ladder[-1] != "":
        ladder.append("")
    return ladder


def build_car_entry(make, model, year, index, trim=""):
    start_year, end_year, generation_label = lookup_generation_range(make, model, year)
    return {
        "index": index,
        "make": make,
        "model": model,
        "trim": trim or None,
        "year": year,
        "generation_start": start_year,
        "generation_end": end_year,
        "generation_label": generation_label,
        "years": f"{start_year}-{end_year}" if start_year != end_year else str(start_year),
    }


def build_search_variant(car_entry, trim_variant):
    """A make/model/(optional trim) combination to actually search for --
    one rung of the trim fallback ladder."""
    model_display = f"{car_entry['model']} {trim_variant}".strip() if trim_variant else car_entry["model"]
    label = f"{car_entry['year']} {car_entry['make']} {model_display}".strip()
    return {
        "name": label,
        "label": label,
        "years": car_entry["years"],
        "search_hint": f"{car_entry['make']} {model_display}",
        "visual_highlight": "",
        "commons_search_terms": [],
    }


def _filter_exterior(images, manifest):
    reviews = (manifest.get("ai_review") or {}).get("reviews", [])
    by_name = {review.get("path"): review for review in reviews}
    return [
        relative for relative in images
        if by_name.get(Path(relative).name, {}).get("shot_type", "exterior") in EXTERIOR_SHOT_TYPES
    ]


def gather_exterior_photos(car_entry, images_dir, limit=PHOTOS_PER_CAR, auction_url=None):
    """Fetch exterior photos. When `auction_url` is given (the winning
    clip's own source listing), fetch directly from that specific listing
    so the photos depict the same physical car as the video -- a separate,
    independently-ranked search for photos routinely lands on a different
    auction entirely (photo search only ever visits the top 4 by price;
    video search casts a much wider net), so matching by title after the
    fact was a coincidence at best. Only falls back to a broader search
    when that exact listing has no usable exterior photos of its own."""
    exterior = []
    if auction_url:
        images, manifest = scrape_auction_images(SCRAPER_DIR, images_dir, car_entry, auction_url)
        exterior = _filter_exterior(images, manifest)
    if not exterior:
        images, manifest = scrape_entry_images(SCRAPER_DIR, images_dir, car_entry)
        exterior = _filter_exterior(images, manifest)
    exterior = exterior[:limit]
    for relative in exterior:
        blur_license_plates(images_dir.parent / relative)
    return exterior


def build_startup_clip(car_entry, images_dir):
    videos = discover_entry_engine_videos(SCRAPER_DIR, images_dir, car_entry)
    listing_urls = sorted({v["auction_url"] for v in videos if v.get("auction_url")})
    if not videos:
        return {"approved": False, "error": "No listing videos discovered for this car/generation", "listing_urls": []}
    pseudo_entry = SimpleNamespace(
        rank=slugify(car_entry["name"]),
        name=car_entry["name"],
        years=car_entry["years"],
        engine_videos=videos,
    )
    clip_dir = images_dir.parent / "engine_clips"
    result = prepare_engine_clip(
        pseudo_entry, clip_dir,
        duration=None,
        allow_irrelevant=True,
        exterior_only=True,
        rear_shot_only=True,
        min_duration=MIN_CLIP_SECONDS,
        max_duration=MAX_CLIP_SECONDS,
        max_thumbnail_classifications=BATTLE_MAX_THUMBNAIL_CLASSIFICATIONS,
        search_distant_rev=True,
        distant_rev_search_seconds=DISTANT_REV_SEARCH_SECONDS,
    )
    result = result or {"approved": False, "error": "No usable engine video found for this car/generation"}
    result["listing_urls"] = listing_urls
    return result


def process_car(car_entry, images_dir):
    requested_trim = car_entry["trim"]
    ladder = trim_fallback_ladder(requested_trim or "")
    photos, clip_result, matched_trim = [], {"approved": False}, ladder[-1]
    attempted_clip_paths = []
    all_listing_urls = set()
    for trim_variant in ladder:
        variant = build_search_variant(car_entry, trim_variant)
        rung_started = time.monotonic()
        print(f"[battle] Car #{car_entry['index']}: trying '{variant['search_hint']}' ({variant['years']})")
        # Find the clip first, then scope the photo search to the same
        # listing it came from -- otherwise photos and video can end up
        # showing two different physical cars. Skips the photo scrape
        # entirely when no clip is approved, since that rung is going to be
        # discarded anyway.
        variant_clip = build_startup_clip(variant, images_dir)
        all_listing_urls.update(variant_clip.get("listing_urls") or [])
        variant_photos = []
        if variant_clip.get("approved"):
            source = variant_clip.get("source") or {}
            auction_url = source.get("auction_url")
            variant_photos = gather_exterior_photos(variant, images_dir, auction_url=auction_url)
        rung_seconds = round(time.monotonic() - rung_started, 1)
        print(
            f"[battle] Car #{car_entry['index']}: '{variant['search_hint']}' took {rung_seconds}s "
            f"-> clip approved={bool(variant_clip.get('approved'))}, {len(variant_photos)} photos"
        )
        if variant_clip.get("path"):
            attempted_clip_paths.append(Path(variant_clip["path"]))
        if variant_clip.get("distant_rev_path"):
            attempted_clip_paths.append(Path(variant_clip["distant_rev_path"]))
        photos, clip_result, matched_trim = variant_photos, variant_clip, trim_variant
        if bool(variant_clip.get("approved")) and len(variant_photos) >= 2:
            break

    final_path = clip_result.get("path")
    final_rev_path = clip_result.get("distant_rev_path")
    for path in attempted_clip_paths:
        if path != final_path and path != final_rev_path:
            path.unlink(missing_ok=True)

    approved = bool(clip_result.get("approved")) and len(photos) >= 2
    matched_trim = matched_trim or None
    model_display = f"{car_entry['model']} {matched_trim}".strip() if matched_trim else car_entry["model"]
    label = f"{car_entry['year']} {car_entry['make']} {model_display}".strip()
    entry = {
        "index": car_entry["index"],
        "label": label,
        "make": car_entry["make"],
        "model": car_entry["model"],
        "trim_requested": requested_trim,
        "trim_used": matched_trim if approved else None,
        "fallback_applied": bool(approved and requested_trim and matched_trim != requested_trim),
        "year": car_entry["year"],
        "generation_start": car_entry["generation_start"],
        "generation_end": car_entry["generation_end"],
        "generation_label": car_entry["generation_label"],
        "photos": photos,
        "approved": approved,
        "detected_onset_seconds": clip_result.get("detected_onset_seconds"),
        "engine_event_score": clip_result.get("engine_event_score"),
        "scene_review": clip_result.get("scene_review"),
        "rev_detected": bool(clip_result.get("rev_detected")),
        "rev_events": clip_result.get("rev_events") or [],
        "source": clip_result.get("source"),
        "listings_considered": sorted(all_listing_urls),
    }
    if final_path:
        relative_clip = Path(final_path).relative_to(images_dir.parent)
        entry["clip_path"] = str(relative_clip).replace("\\", "/")
        entry["clip_duration"] = round(float(clip_result.get("duration") or MIN_CLIP_SECONDS), 3)
    else:
        entry["clip_path"] = None
        entry["clip_duration"] = None
    if approved and final_rev_path:
        relative_rev = Path(final_rev_path).relative_to(images_dir.parent)
        entry["rev_clip_path"] = str(relative_rev).replace("\\", "/")
        entry["rev_clip_duration"] = round(float(clip_result.get("distant_rev_duration") or 0), 3)
        entry["rev_clip_onset_seconds"] = clip_result.get("distant_rev_onset_seconds")
    else:
        entry["rev_clip_path"] = None
        entry["rev_clip_duration"] = None
        entry["rev_clip_onset_seconds"] = None
    if approved:
        entry["error"] = None
    elif not clip_result.get("approved"):
        # Photos are only fetched once a clip is approved (see the loop
        # above), so if the clip itself never got approved, that's always
        # the real reason -- blaming "not enough photos" here would be
        # misleading since photos were never even searched for.
        entry["error"] = clip_result.get("error") or "No usable exterior startup clip found"
    else:
        entry["error"] = "Not enough verified exterior photos found for the matched listing"
    return entry


def generate_intro_question(entries):
    """A short, category-aware on-screen question for the intro card, e.g.
    "Which modern muscle car sounds best?" for a Mustang/Challenger/Camaro
    battle, or "Which entry-level supercar sounds best?" for a 911/Supra/
    GT-R one -- instead of always showing the same generic line."""
    approved = [entry for entry in entries if entry.get("approved")] or entries
    labels = [entry["label"] for entry in approved]
    fallback = "Which one sounds best?"
    if not labels:
        return fallback
    try:
        from openai import OpenAI

        prompt = (
            "These cars are being compared by their cold-start/exhaust sound in a short video: "
            + ", ".join(labels) + ". Write ONE short, punchy on-screen question (6 words or fewer "
            "after \"Which\") asking viewers which one sounds best, naming an accurate category for "
            "these specific cars -- e.g. \"Which modern muscle car sounds best?\", \"Which "
            "entry-level supercar sounds best?\", \"Which JDM legend sounds best?\". If they don't "
            "share an obvious category, use \"Which one sounds best?\". Return only the question "
            "text, no quotes, no extra commentary."
        )
        response = with_openai_retry(lambda: OpenAI().responses.create(model="gpt-4o-mini", input=prompt))
        text = response.output_text.strip().strip('"').strip()
        return text or fallback
    except Exception as exc:
        print(f"[battle] Intro question generation failed: {exc}")
        return fallback


def main():
    parser = argparse.ArgumentParser(description="Research a startup-sound battle between 3-5 user-specified cars.")
    parser.add_argument("--cars", required=True, help="JSON array of {make, model, trim, year}")
    parser.add_argument("--battle-id", required=True)
    args = parser.parse_args()

    cars = json.loads(args.cars)
    if not (MIN_CARS <= len(cars) <= MAX_CARS):
        raise SystemExit(f"Expected {MIN_CARS}-{MAX_CARS} cars, got {len(cars)}")

    battle_dir = BATTLES_ROOT / args.battle_id
    images_dir = battle_dir / "images"
    battle_dir.mkdir(parents=True, exist_ok=True)

    parsed_cars = []
    for index, car in enumerate(cars, start=1):
        make = str(car.get("make", "")).strip()
        model = str(car.get("model", "")).strip()
        trim = str(car.get("trim", "")).strip()
        year = str(car.get("year", "")).strip()
        if not make or not model or not year:
            raise SystemExit(f"Car #{index} is missing make/model/year")
        parsed_cars.append((index, make, model, trim, year))

    def run_car(index, make, model, trim, year):
        car_entry = build_car_entry(make, model, year, index, trim=trim)
        return process_car(car_entry, images_dir)

    # Each car's pipeline (generation lookup, search, scrape, clip
    # extraction) is independent and I/O-bound, so running them concurrently
    # instead of one at a time is a direct wall-clock win -- see
    # MAX_CONCURRENT_CARS.
    started_at = time.monotonic()
    results_by_index = {}
    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_CARS, len(parsed_cars))) as pool:
        futures = {pool.submit(run_car, *args): args[0] for args in parsed_cars}
        for future in as_completed(futures):
            results_by_index[futures[future]] = future.result()
    entries = [results_by_index[index] for index, *_ in parsed_cars]
    print(f"[battle] All {len(entries)} cars processed in {round(time.monotonic() - started_at, 1)}s total")

    approved_count = sum(1 for entry in entries if entry["approved"])
    intro_question = generate_intro_question(entries)
    output = {
        "battle_id": args.battle_id,
        "cars": entries,
        "title": intro_question.upper(),
        "intro_question": intro_question,
        "status": "researched",
        "approved_count": approved_count,
        "total_count": len(entries),
    }
    (battle_dir / "battle.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"[battle] Wrote {battle_dir / 'battle.json'} ({approved_count}/{len(entries)} approved)")


if __name__ == "__main__":
    main()
