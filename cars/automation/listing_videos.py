"""Every video on one Cars & Bids listing, with a poster frame each.

A listing carries a handful of clips -- the GT500 Heritage had Walkaround,
Interior Tour and Start-Up (Engine Bay) -- and exactly one of them is the
one worth cutting an engine note out of. Picking it automatically is what
the battle pipeline does, and it manages it 4 times in 14.

So this does not pick. It lists what is there, extracts a frame from each
so one can be told from another at a glance, and leaves the choosing to a
person who is already looking at the listing.

    python listing_videos.py --auction-url https://carsandbids.com/auctions/... \
        --probe-id listing-videos-20261022

Writes videos.json and poster-N.jpg into cars/listing-videos/<probe-id>/.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
OUTPUT_ROOT = ROOT / "cars" / "listing-videos"

# A poster taken from the first frame is often black -- a fade-in, or a
# player splash. A second in is past that and still inside the shortest
# clip these listings carry.
POSTER_AT_SECONDS = 1.0
FFMPEG = os.environ.get("FFMPEG_BINARY", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BINARY", "ffprobe")


def _run(command, timeout=180):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def media_duration(source):
    """Seconds, or None. An HLS manifest answers this as readily as a file."""
    done = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(source)])
    try:
        return round(float(done.stdout.strip()), 2)
    except (TypeError, ValueError):
        return None


def poster_frame(source, out_path, at=POSTER_AT_SECONDS):
    """One JPEG from `at` seconds in. Returns the path, or None."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = _run([FFMPEG, "-v", "error", "-ss", f"{at:.2f}", "-i", str(source),
                 "-frames:v", "1", "-q:v", "4", "-y", str(out_path)])
    if done.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    # Past the end of a very short clip: take the opening frame instead.
    done = _run([FFMPEG, "-v", "error", "-i", str(source),
                 "-frames:v", "1", "-q:v", "4", "-y", str(out_path)])
    return out_path if done.returncode == 0 and out_path.exists() else None


# What the page says around a player. The scraper collects a block of
# nearby text rather than one tidy caption, so this is reported raw as well
# as trimmed -- until a real listing has been through it, which of the two
# is actually usable as a name is a guess.
_PLAYER_CHROME = re.compile(
    r"\b(play|pause|mute|unmute|fullscreen|picture-in-picture|remaining time"
    r"|loaded|progress|current time|duration|\d+:\d\d)\b", re.I)


def tidy_label(context, section=""):
    """A short name for one clip, from the text scraped around its player."""
    text = re.sub(r"\s+", " ", str(context or "")).strip()
    # Player chrome is the same under every clip on the page and says
    # nothing about which one this is.
    text = _PLAYER_CHROME.sub(" ", text)
    text = re.sub(r"[|·]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    if not text:
        text = re.sub(r"\s+", " ", str(section or "")).strip()
    return text[:80]


def collect(auction_url, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "scrape.json"
    done = _run([
        "node", "src/scrape-carsandbids-gallery.js",
        f"--auction-url={auction_url}",
        "--skip-images=true",
        f"--out-dir={output_dir / '_scrape'}",
        f"--out-json={manifest_path}",
    ], timeout=600)
    if done.returncode != 0:
        print(done.stdout[-2000:], file=sys.stderr)
        print(done.stderr[-2000:], file=sys.stderr)
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}

    seen, videos = set(), []
    for raw in manifest.get("videos") or []:
        url = str(raw.get("playback_url") or raw.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        index = len(videos) + 1
        poster = poster_frame(url, output_dir / f"poster-{index}.jpg")
        videos.append({
            "index": index,
            "label": tidy_label(raw.get("context"), raw.get("section")),
            # Kept whole so a label that comes back useless can be diagnosed
            # from the output rather than by running the scrape again.
            "raw_context": str(raw.get("context") or "")[:500],
            "section": str(raw.get("section") or "")[:120],
            "playback_url": url,
            "duration_seconds": media_duration(url),
            "poster": poster.name if poster else None,
        })
    return {"auction_url": auction_url, "video_count": len(videos), "videos": videos}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auction-url", required=True)
    parser.add_argument("--probe-id", required=True)
    args = parser.parse_args()

    output_dir = OUTPUT_ROOT / args.probe_id
    found = collect(args.auction_url, output_dir)
    (output_dir / "videos.json").write_text(
        json.dumps(found, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[listing-videos] {found['video_count']} video(s) on {args.auction_url}")
    for video in found["videos"]:
        print(f"  {video['index']}. {video['label'] or '(no label)'} "
              f"- {video['duration_seconds']}s - poster={video['poster']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
