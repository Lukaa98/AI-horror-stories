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
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from cars_and_bids import discover_entry_engine_videos, scrape_entry_images
from engine_video import prepare_engine_clip
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


def build_car_entry(make, model, year, index, trim=""):
    start_year, end_year, generation_label = lookup_generation_range(make, model, year)
    model_display = f"{model} {trim}".strip() if trim else model
    label = f"{year} {make} {model_display}".strip()
    return {
        "index": index,
        "label": label,
        "make": make,
        "model": model,
        "trim": trim or None,
        "year": year,
        "generation_start": start_year,
        "generation_end": end_year,
        "generation_label": generation_label,
        "name": label,
        "years": f"{start_year}-{end_year}" if start_year != end_year else str(start_year),
        "search_hint": f"{make} {model_display}",
        "visual_highlight": "",
        "commons_search_terms": [],
    }


def gather_exterior_photos(car_entry, images_dir, limit=PHOTOS_PER_CAR):
    images, manifest = scrape_entry_images(SCRAPER_DIR, images_dir, car_entry)
    reviews = (manifest.get("ai_review") or {}).get("reviews", [])
    by_name = {review.get("path"): review for review in reviews}
    exterior = [
        relative for relative in images
        if by_name.get(Path(relative).name, {}).get("shot_type", "exterior") in EXTERIOR_SHOT_TYPES
    ][:limit]
    for relative in exterior:
        blur_license_plates(images_dir.parent / relative)
    return exterior


def build_startup_clip(car_entry, images_dir):
    videos = discover_entry_engine_videos(SCRAPER_DIR, images_dir, car_entry)
    if not videos:
        return {"approved": False, "error": "No listing videos discovered for this car/generation"}
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
        min_duration=MIN_CLIP_SECONDS,
        max_duration=MAX_CLIP_SECONDS,
    )
    return result or {"approved": False, "error": "No usable engine video found for this car/generation"}


def process_car(car_entry, images_dir):
    print(f"[battle] {car_entry['label']} -> generation range {car_entry['years']}")
    photos = gather_exterior_photos(car_entry, images_dir)
    clip_result = build_startup_clip(car_entry, images_dir)
    approved = bool(clip_result.get("approved")) and len(photos) >= 2
    entry = {
        "index": car_entry["index"],
        "label": car_entry["label"],
        "make": car_entry["make"],
        "model": car_entry["model"],
        "trim": car_entry["trim"],
        "year": car_entry["year"],
        "generation_start": car_entry["generation_start"],
        "generation_end": car_entry["generation_end"],
        "generation_label": car_entry["generation_label"],
        "photos": photos,
        "approved": approved,
        "detected_onset_seconds": clip_result.get("detected_onset_seconds"),
        "engine_event_score": clip_result.get("engine_event_score"),
        "scene_review": clip_result.get("scene_review"),
        "source": clip_result.get("source"),
    }
    if clip_result.get("path"):
        relative_clip = Path(clip_result["path"]).relative_to(images_dir.parent)
        entry["clip_path"] = str(relative_clip).replace("\\", "/")
        entry["clip_duration"] = round(float(clip_result.get("duration") or MIN_CLIP_SECONDS), 3)
    else:
        entry["clip_path"] = None
        entry["clip_duration"] = None
    entry["error"] = None if approved else (
        clip_result.get("error")
        or ("Not enough verified exterior photos found" if len(photos) < 2 else "Startup clip rejected")
    )
    return entry


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

    entries = []
    for index, car in enumerate(cars, start=1):
        make = str(car.get("make", "")).strip()
        model = str(car.get("model", "")).strip()
        trim = str(car.get("trim", "")).strip()
        year = str(car.get("year", "")).strip()
        if not make or not model or not year:
            raise SystemExit(f"Car #{index} is missing make/model/year")
        car_entry = build_car_entry(make, model, year, index, trim=trim)
        entries.append(process_car(car_entry, images_dir))

    approved_count = sum(1 for entry in entries if entry["approved"])
    output = {
        "battle_id": args.battle_id,
        "cars": entries,
        "title": "WHICH ONE SOUNDS BEST?",
        "status": "researched",
        "approved_count": approved_count,
        "total_count": len(entries),
    }
    (battle_dir / "battle.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"[battle] Wrote {battle_dir / 'battle.json'} ({approved_count}/{len(entries)} approved)")


if __name__ == "__main__":
    main()
