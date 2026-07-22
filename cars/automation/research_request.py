"""Turn a free-text request ("ranking video of Corvettes", "Mustang generations
2015-2024") into a research.json draft: an AI research pass (OpenAI Responses
API with the hosted web_search tool, so facts are grounded/cited, not just
model-recalled) plus best-effort image sourcing from Wikimedia Commons.

Writes cars/drafts/<draft-id>/research.json and cars/drafts/<draft-id>/images/.
Does NOT render a video -- that's generate_from_research.py, a separate stage,
so a human can review facts/photos before committing to a render.
"""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
DRAFTS_ROOT = ROOT / "cars" / "drafts"

RESEARCH_PROMPT_TEMPLATE = """You are researching content for a short vertical "ranking" video about cars.

User request: "{request}"

Use web search to find real, current, verifiable facts. Identify exactly 4 specific
things to rank (e.g. 4 generations of one car, or 4 distinctly-named trims/models within
one generation, or 4 specific model years -- whatever best matches the request).

IMPORTANT: each entry must be a distinctly NAMED model/trim that a photographer would
tag as its own subject and that has its own dedicated Wikimedia Commons category --
e.g. "Stingray", "Z06", "ZR1", "E-Ray" are good; internal option-package codes like
"1LT", "2LT", "3LZ", "1LZ" are BAD (nobody photographs "a 2LT", they photograph "a Z06").
If the request doesn't obviously split into 4 named variants, pick the 4 most
distinct/well-known ones rather than an internal trim-code breakdown.

For each entry, give:
- name: short identifier (e.g. "NA", "Z06", "2018")
- years: production year range or single year as a string
- price_usd: a representative price in USD as a number (starting MSRP if applicable), or null if not applicable
- horsepower: a representative horsepower number, or null if not applicable
- label: a short (2-4 word) factual or reputation-based descriptor, e.g. "MOST UNLOVED" or "ENTRY POINT"
- one_line_fact: ONE short spoken sentence (max 16 words) stating a real, specific fact or verified community
  reputation point about this entry. No filler, no generic praise -- a concrete fact or citable claim.
- search_hint: a short phrase to search Wikimedia Commons for photos of this specific thing
  (e.g. "Ford Mustang III GT", "Chevrolet Corvette Z06 C8")

Also give:
- title: a short ALL-CAPS-worthy video title, e.g. "RANKING EVERY CORVETTE GENERATION"
- highlight_word: the single word in the title that should be color-highlighted (usually the car model name)
- close_narration: a short closing question/line (max 10 words), e.g. "Which one would you actually buy?"
- order_rationale: one sentence explaining why you ordered the 4 entries this way (worst-to-best, cheapest-to-priciest, etc)

Order the 4 entries from what you determine is position 4 (first shown) to position 1 (last shown, the "best"/highest).

Return ONLY strict JSON, no markdown fences, no prose outside the JSON, matching:
{{
  "title": "string",
  "highlight_word": "string",
  "close_narration": "string",
  "order_rationale": "string",
  "entries": [
    {{"name": "string", "years": "string", "price_usd": number_or_null, "horsepower": number_or_null,
      "label": "string", "one_line_fact": "string", "search_hint": "string"}}
  ]
}}
Exactly 4 entries."""


def slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "draft"


def run_research(request_text):
    from openai import OpenAI

    client = OpenAI()
    prompt = RESEARCH_PROMPT_TEMPLATE.format(request=request_text)
    response = client.responses.create(
        model="gpt-4o",
        tools=[{"type": "web_search_preview"}],
        input=prompt,
    )
    text = response.output_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    data = json.loads(text)
    if len(data.get("entries", [])) != 4:
        raise SystemExit(f"Expected 4 entries from research, got {len(data.get('entries', []))}. Raw: {text[:500]}")
    return data


def format_stat(entry):
    parts = []
    if entry.get("price_usd"):
        parts.append(f"${entry['price_usd']:,.0f}")
    if entry.get("horsepower"):
        parts.append(f"{entry['horsepower']:.0f} HP")
    return " - ".join(parts) if parts else "SPEC UNAVAILABLE"


def scrape_images(search_hint, topic_slug, draft_images_dir):
    """Best effort: try the hint as an exact Commons category first (higher
    quality when it hits -- classified by shot type). If that comes up empty
    (AI-guessed category names are often close but not exact), fall back to a
    free-text Commons file search, which isn't category-tree dependent."""
    dest = draft_images_dir / topic_slug
    subprocess.run(
        [
            "node", "src/scrape-commons-category.js",
            f"--category={search_hint}",
            f"--topic=drafts-tmp/{topic_slug}",
            "--limit=4",
            "--pool-size=100",
            "--download",
        ],
        cwd=SCRAPER_DIR,
        check=False,  # best effort -- a bad category name shouldn't kill the whole run
    )
    scraped_dir = ROOT / "cars" / "output" / "sources" / "drafts-tmp" / topic_slug / "images"
    images = []
    if scraped_dir.exists():
        for path in sorted(scraped_dir.glob("*.jpg"))[:2]:
            dest.mkdir(parents=True, exist_ok=True)
            out_path = dest / path.name
            out_path.write_bytes(path.read_bytes())
            images.append(f"images/{topic_slug}/{path.name}")

    if not images:
        print(f"[images] Category match empty, falling back to keyword search for {search_hint!r}")
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "node", "src/search-commons-media.js",
                f"--query={search_hint}",
                f"--out-dir={dest}",
                "--limit=2",
            ],
            cwd=SCRAPER_DIR,
            check=False,
        )
        images = [f"images/{topic_slug}/{p.name}" for p in sorted(dest.glob("search-*"))]
    return images


def main():
    parser = argparse.ArgumentParser(description="AI-research a free-text car ranking request into a draft JSON + images.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--draft-id", required=True)
    args = parser.parse_args()

    draft_dir = DRAFTS_ROOT / args.draft_id
    images_dir = draft_dir / "images"
    draft_dir.mkdir(parents=True, exist_ok=True)

    print(f"[research] Request: {args.request!r}")
    data = run_research(args.request)
    print(f"[research] Title: {data['title']}")

    for i, entry in enumerate(data["entries"]):
        if i > 0:
            time.sleep(5)  # let Wikimedia's rate limiter cool down between entries
        topic_slug = slugify(entry["name"])
        print(f"[images] {entry['name']} -> searching Commons for {entry['search_hint']!r}")
        entry["images"] = scrape_images(entry["search_hint"], topic_slug, images_dir)
        entry["stat"] = format_stat(entry)
        if not entry["images"]:
            print(f"[images] WARNING: no images found for {entry['name']}")

    output = {
        "request": args.request,
        "draft_id": args.draft_id,
        "title": data["title"],
        "highlight_word": data["highlight_word"],
        "close_narration": data["close_narration"],
        "order_rationale": data.get("order_rationale", ""),
        "entries": data["entries"],
        "status": "researched",  # -> "video_generated" after stage 2
    }
    (draft_dir / "research.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"[research] Wrote {draft_dir / 'research.json'}")


if __name__ == "__main__":
    main()
