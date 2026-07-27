"""Fast standalone Cars & Bids video discovery/extraction experiment."""
import argparse
import json
import re
import subprocess
from itertools import zip_longest
from pathlib import Path
from types import SimpleNamespace

from engine_video import classify_video_thumbnail, prepare_engine_clip


ROOT = Path(__file__).resolve().parents[2]
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
OUTPUT_ROOT = ROOT / "cars" / "video-tests"


def _interleave_by_listing(videos):
    """Cycle across distinct listings instead of taking the global top-N.

    Discovered videos are pre-sorted labeled-first, but a single listing with
    several unlabeled video embeds and a high search score can otherwise fill
    every slot before a different listing is ever tried, which is why the
    same car kept coming back across an entire test run. Grouping by listing
    and round-robining preserves that ranking within each listing while
    guaranteeing every discovered listing gets a turn.
    """
    groups = {}
    order = []
    for video in videos:
        key = video.get("auction_url") or video.get("url")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(video)
    interleaved = []
    for row in zip_longest(*(groups[key] for key in order)):
        interleaved.extend(item for item in row if item is not None)
    return interleaved


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--make", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--query", default="")
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--test-id", required=True)
    parser.add_argument("--target-approved", type=int, default=4)
    parser.add_argument("--max-attempts", type=int, default=20)
    args = parser.parse_args()

    output_dir = OUTPUT_ROOT / args.test_id
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "discovery.json"
    query = args.query.strip() or f"{args.make} {args.model}"
    cmd = [
        "node",
        "src/scrape-carsandbids-gallery.js",
        f"--make={_slug(args.make)}",
        f"--model={_slug(args.model)}",
        f"--query={query}",
        f"--out-dir={output_dir}",
        f"--out-json={manifest_path}",
        "--skip-images=true",
    ]
    if args.start_year:
        cmd.append(f"--start-year={args.start_year}")
    if args.end_year:
        cmd.append(f"--end-year={args.end_year}")
    subprocess.run(cmd, cwd=SCRAPER_DIR, check=True)

    discovery = json.loads(manifest_path.read_text(encoding="utf-8"))
    deduped = []
    seen = set()
    for candidate in discovery.get("videos", []):
        key = candidate.get("playback_url") or candidate.get("url")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    # Cycle across listings rather than taking a flat top-N slice, so one
    # listing with several unlabeled video embeds and a high search score
    # can't fill every attempt before a different listing is ever tried.
    candidates = _interleave_by_listing(deduped)[: args.max_attempts]

    clips = []
    declined = 0
    clips_dir = output_dir / "clips"
    years = "–".join(str(value) for value in (args.start_year, args.end_year) if value) or "any year"
    for index, candidate in enumerate(candidates, start=1):
        if len(clips) >= args.target_approved:
            break
        entry = SimpleNamespace(
            rank=index,
            name=f"{args.make} {args.model}",
            years=years,
            engine_videos=[candidate],
        )
        try:
            thumbnail_review = classify_video_thumbnail(candidate, entry)
        except Exception as exc:
            thumbnail_review = {
                "scene_type": "unknown",
                "engine_relevance": 5,
                "likely_engine_audio": True,
                "reason": f"Thumbnail review failed open: {exc}",
            }
        candidate["scene_review"] = thumbnail_review
        result = prepare_engine_clip(entry, clips_dir, allow_irrelevant=True)
        # prepare_engine_clip re-classifies scene purpose from a frame taken
        # at the actual detected audio event when a clip was extracted, which
        # is far more reliable than the static platform thumbnail used above
        # for the initial cheap filter - prefer that verdict when available.
        scene_review = (result.get("scene_review") if result else None) or thumbnail_review
        item = {
            "index": index,
            "approved": bool(result and result.get("approved")),
            "clip_extracted": bool(result and result.get("path")),
            "source_listing": candidate.get("auction_url"),
            "source_title": candidate.get("auction_title") or candidate.get("title"),
            "source_year": candidate.get("auction_year"),
            "source_type": candidate.get("type"),
            "thumbnail_url": candidate.get("thumbnail_url") or candidate.get("url"),
            "scene_review": scene_review,
            "detected_onset_seconds": result.get("detected_onset_seconds") if result else None,
            "engine_event_score": result.get("engine_event_score") if result else None,
            "secondary_event_seconds": result.get("secondary_event_seconds") if result else None,
            "secondary_event_score": result.get("secondary_event_score") if result else None,
            "review": result.get("review") if result else None,
            "error": result.get("error") if result else "No result",
        }
        if result and result.get("path"):
            item["clip"] = str(Path(result["path"]).relative_to(output_dir)).replace("\\", "/")
        if item["approved"]:
            item["index"] = len(clips) + 1
            clips.append(item)
        else:
            declined += 1

    output = {
        "test_id": args.test_id,
        "query": query,
        "make": args.make,
        "model": args.model,
        "start_year": args.start_year,
        "end_year": args.end_year,
        "listings_considered": discovery.get("auctions_considered", []),
        "videos_discovered": len(discovery.get("videos", [])),
        "listings_with_video_attempted": len({c.get("auction_url") for c in candidates}),
        "attempts_made": len(clips) + declined,
        "declined_count": declined,
        "clips": clips,
        "status": "complete",
    }
    (output_dir / "result.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
