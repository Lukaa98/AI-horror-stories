import argparse
import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
AUTOMATION_DIR = Path(__file__).resolve().parent

if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

from topics import TOPICS  # noqa: E402
from ranking_engine import render_ranking_video as _render_config  # noqa: E402
from auto_topic import build_auto_config  # noqa: E402


def scrape_topic(topic_key, force=False):
    config = TOPICS[topic_key]
    for job in config["scrapes"]:
        images_dir = ROOT / "cars" / "output" / "sources" / job["topic"] / "images"
        if not force and images_dir.exists() and any(images_dir.iterdir()):
            print(f"[scrape] Skipping {job['topic']} -- images already present.")
            continue
        print(f"[scrape] Category:{job['category']} -> {job['topic']}")
        subprocess.run(
            [
                "node", "src/scrape-commons-category.js",
                f"--category={job['category']}",
                f"--topic={job['topic']}",
                "--limit=8",
                "--pool-size=200",
                "--download",
            ],
            cwd=SCRAPER_DIR,
            check=True,
        )


def render_topic(topic_key, tts_provider, fast):
    config = TOPICS[topic_key]
    module = importlib.import_module(config["render_module"])
    return module.render_ranking_video(module.CONFIG, tts_provider=tts_provider, fast=fast)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape (if needed) and render a ranking video. If --topic matches a "
        "configured entry in topics.py, uses that hand-verified config. Otherwise treats "
        "--topic as a free-text car name and attempts a best-effort auto-built DRAFT."
    )
    parser.add_argument("--topic", required=True, help="A configured topic key, or any car name for auto-draft mode.")
    parser.add_argument("--tts-provider", default="gtts", choices=["gtts", "openai", "tone", "silent"])
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping even if images are missing (configured topics only).")
    parser.add_argument("--force-scrape", action="store_true", help="Re-scrape even if images already exist (configured topics only).")
    parser.add_argument("--full-res", action="store_true", help="Render at full 1080x1920 instead of the fast preview canvas.")
    args = parser.parse_args()

    if args.topic in TOPICS:
        if not args.skip_scrape:
            scrape_topic(args.topic, force=args.force_scrape)
        run_dir = render_topic(args.topic, args.tts_provider, fast=not args.full_res)
    else:
        print(f"[auto] {args.topic!r} is not a configured topic -- attempting best-effort auto-draft.")
        config = build_auto_config(args.topic)
        run_dir = _render_config(config, tts_provider=args.tts_provider, fast=not args.full_res)
    print(run_dir)


if __name__ == "__main__":
    main()
