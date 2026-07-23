"""Turn a free-text request ("ranking video of Corvettes", "Mustang generations
2015-2024") into a research.json draft: an AI research pass (OpenAI Responses
API with the hosted web_search tool, so facts are grounded/cited, not just
model-recalled) plus best-effort image sourcing from Cars & Bids first and
Wikimedia Commons as fallback.

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
from PIL import Image
from cars_and_bids import augment_narration_with_current_value, enrich_entry_from_manifest, scrape_entry_images

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
DRAFTS_ROOT = ROOT / "cars" / "drafts"

RESEARCH_PROMPT_TEMPLATE = """You are researching content for a short vertical "ranking" video about cars.

User request: "{request}"

Use web search to find up to 8 ranked candidate
entries based on the request scope. We will keep the highest-ranked candidates that
also have usable image coverage, so your ranking order matters. If the lineup only
naturally supports 4 strong candidates, returning 4 is acceptable.

The request may explicitly ask for one of two workflows:
1. best generations overall across the full production run of a model line
2. best versions within a specific year range, generation, or chassis focus
Honor that scope strictly when choosing the 4 entries.

If the request is overall / across the full production run / all generations:
- treat this as a GENERATION ranking, not a trim ranking
- choose 4 different generations or chassis families from across the model's history
- use exactly one representative version for each chosen generation
- do NOT let two entries come from the same generation unless the user explicitly asked for that
- example for Corvette: C1, C4, C6, C8 is valid; Stingray, Z06, ZR1, ZR1X all from C8 is NOT valid
- if the model line has fewer than 4 true generations, fall back to 4 era-defining versions across the full production run
- in that fallback case, maximize generation diversity first, then use major facelifts, flagship trims, or historically important versions
- if you use that fallback, make the order_rationale explicitly say that the model has fewer than 4 true generations

If the request is focused / in a range / generation-specific:
- treat this as a VARIANT ranking within that constrained scope
- all 4 entries may come from the same generation if that is what the request implies
- example for Corvette C8: Stingray, E-Ray, Z06, ZR1 is valid

Prefer candidates that are well-known, visually distinct, and likely to have strong
auction/photo coverage. When two candidates are similarly deserving, favor the one
more likely to have usable Cars & Bids or Commons imagery.

IMPORTANT:
- each entry still needs a clearly photographable subject
- for overall generation rankings, prefer names that combine generation plus representative trim when needed,
  such as "C6 Z06", "C4 ZR-1", "First-Gen R8 V8", or "997 GT3 RS"
- for focused rankings, each entry must be a distinctly NAMED model/trim that a photographer would
  tag as its own subject and that has its own dedicated Wikimedia Commons category --
  e.g. "Stingray", "Z06", "ZR1", "E-Ray" are good; internal option-package codes like
  "1LT", "2LT", "3LZ", "1LZ" are BAD (nobody photographs "a 2LT", they photograph "a Z06")
- if the request doesn't obviously split into 4 named variants, pick the 4 most
  distinct/well-known ones rather than an internal trim-code breakdown

For each entry, give:
- name: short identifier (e.g. "NA", "Z06", "2018")
- years: production year range or single year as a string
- introduced_year: the year this exact generation/variant was introduced
- price_usd: its ORIGINAL starting MSRP in its introduction year, in USD as a number, or null only if unavailable
- horsepower: a representative horsepower number, or null if not applicable
- label: a short (2-4 word) factual or reputation-based descriptor, e.g. "MOST UNLOVED" or "ENTRY POINT"
- one_line_fact: energetic spoken narration of 16-24 words. It MUST naturally mention the introduction year,
  original starting price, horsepower, and one meaningful enthusiast detail. Keep it to one punchy sentence.
  Write like a knowledgeable car-club friend, not a brochure or AI summary. Use contractions and varied transitions.
  The first entry should open with an enthusiastic ranking hook; later entries should flow with phrases such as
  "Then," "Next," and "At number one." Do not use Markdown, emoji, headings, or stage directions because this text
  goes directly to text-to-speech.
- search_hint: a short phrase to search Wikimedia Commons for photos of this specific thing
  (e.g. "Ford Mustang III GT", "Chevrolet Corvette Z06 C8")
- visual_highlight: the most interesting model-specific visual detail to show, such as "quad exhaust",
  "interior dashboard", "engine bay", or "rear light design"

Also give:
- title: a short ALL-CAPS-worthy video title, e.g. "RANKING EVERY CORVETTE GENERATION"
- highlight_word: the single word in the title that should be color-highlighted (usually the car model name)
- close_narration: an enthusiastic conversational choice question (max 10 words) naming the relevant lineup,
  e.g. "So, which C5 are you taking home: Coupe, Convertible, FRC, or Z06?"
- order_rationale: one sentence explaining why you ordered the 4 entries this way (worst-to-best, cheapest-to-priciest, etc)

Order the candidates from what you determine is position 8/7/etc. up to position 1, so the
best candidate is last. The final video will keep the highest-ranked 4 candidates that
have usable image coverage.

Return ONLY strict JSON, no markdown fences, no prose outside the JSON, matching:
{{
  "title": "string",
  "highlight_word": "string",
  "close_narration": "string",
  "order_rationale": "string",
  "entries": [
    {{"name": "string", "years": "string", "introduced_year": number, "price_usd": number_or_null, "horsepower": number_or_null,
      "label": "string", "one_line_fact": "string", "search_hint": "string", "visual_highlight": "string"}}
  ]
}}
Aim for 8 entries when possible, but return at least 4."""


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
    entry_count = len(data.get("entries", []))
    if entry_count < 4:
        raise SystemExit(f"Expected at least 4 entries from research, got {entry_count}. Raw: {text[:500]}")
    return data


def format_stat(entry):
    parts = []
    if entry.get("price_usd"):
        parts.append(f"MSRP ${entry['price_usd']:,.0f}")
    if entry.get("horsepower"):
        parts.append(f"{entry['horsepower']:.0f} HP")
    if entry.get("current_value_display"):
        parts.append(f"Now ~{entry['current_value_display']}")
    return " - ".join(parts) if parts else "SPEC UNAVAILABLE"


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def valid_images(directory):
    """Return only complete images that Pillow can decode."""
    valid = []
    if not directory.exists():
        return valid
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            with Image.open(path) as image:
                image.verify()
            valid.append(path)
        except (OSError, ValueError):
            print(f"[images] Removing unreadable download: {path}")
            path.unlink(missing_ok=True)
    return valid


def run_image_search(query, dest, prefix, limit=1, min_width=900):
    subprocess.run(
        [
            "node", "src/search-commons-media.js",
            f"--query={query}", f"--out-dir={dest}", f"--prefix={prefix}",
            f"--limit={limit}", f"--min-width={min_width}",
        ],
        cwd=SCRAPER_DIR,
        check=False,
    )
    return valid_images(dest)


def scrape_images(search_hint, topic_slug, draft_images_dir, visual_highlight=""):
    """Build a varied, verified image set with thematic and general fallbacks."""
    dest = draft_images_dir / topic_slug
    subprocess.run(
        [
            "node", "src/scrape-commons-category.js",
            f"--category={search_hint}",
            f"--topic=drafts-tmp/{topic_slug}",
            "--limit=4",
            "--pool-size=100",
            "--target-front=1",
            "--target-rear=1",
            "--target-side=0",
            "--target-interior=1",
            "--target-engine=1",
            "--target-wheel=0",
            "--download",
        ],
        cwd=SCRAPER_DIR,
        check=False,  # best effort -- a bad category name shouldn't kill the whole run
    )
    scraped_dir = ROOT / "cars" / "output" / "sources" / "drafts-tmp" / topic_slug / "images"
    if scraped_dir.exists():
        for path in valid_images(scraped_dir)[:4]:
            dest.mkdir(parents=True, exist_ok=True)
            out_path = dest / path.name
            out_path.write_bytes(path.read_bytes())
    dest.mkdir(parents=True, exist_ok=True)

    # Add deliberate visual variety instead of relying on whichever category
    # files sort first. Each query has a distinct prefix so results coexist.
    themed_queries = [
        (f"{search_hint} rear", "rear"),
        (f"{search_hint} interior dashboard", "interior"),
    ]
    if visual_highlight:
        themed_queries.append((f"{search_hint} {visual_highlight}", "highlight"))
    for query, prefix in themed_queries:
        if len(valid_images(dest)) >= 6:
            break
        run_image_search(query, dest, prefix)

    # General model images are the safe fallback. Retry at a lower resolution
    # threshold when Commons has sparse coverage for an older model/year.
    if len(valid_images(dest)) < 2:
        print(f"[images] Adding general fallback images for {search_hint!r}")
        run_image_search(search_hint, dest, "general", limit=3)
    if not valid_images(dest):
        run_image_search(search_hint, dest, "fallback", limit=3, min_width=600)

    return [f"images/{topic_slug}/{path.name}" for path in valid_images(dest)[:6]]


def source_entry_images(entry, images_dir):
    topic_slug = slugify(entry["name"])
    print(f"[images] {entry['name']} -> trying Cars & Bids for {entry['search_hint']!r}")
    cars_and_bids_images, cars_and_bids_manifest = scrape_entry_images(SCRAPER_DIR, images_dir, entry)
    entry["images"] = cars_and_bids_images
    enrich_entry_from_manifest(entry, cars_and_bids_manifest)
    if not entry["images"]:
        print(f"[images] Cars & Bids sparse for {entry['name']} -- falling back to Commons")
        entry["images"] = scrape_images(
            entry["search_hint"], topic_slug, images_dir, entry.get("visual_highlight", "")
        )
    entry["narration"] = augment_narration_with_current_value(entry)
    entry["one_line_fact"] = entry["narration"]
    entry["stat"] = format_stat(entry)
    return entry


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

    selected_entries = []
    skipped_entries = []
    for i, candidate in enumerate(data["entries"]):
        if i > 0:
            time.sleep(5)  # let remote rate limiters cool down between entries
        entry = source_entry_images(candidate, images_dir)
        if entry["images"]:
            selected_entries.append(entry)
            print(f"[images] Selected {entry['name']} with {len(entry['images'])} image(s)")
        else:
            skipped_entries.append({
                "name": entry["name"],
                "years": entry.get("years", ""),
                "reason": "no_images_found",
            })
            print(f"[images] Skipping {entry['name']} because no usable images were found")
        if len(selected_entries) == 4:
            break

    if len(selected_entries) < 4:
        raise SystemExit(
            f"Only found {len(selected_entries)} image-backed entries out of {len(data['entries'])} researched candidates. "
            "Try a broader request or improve source coverage."
        )

    output = {
        "request": args.request,
        "draft_id": args.draft_id,
        "title": data["title"],
        "highlight_word": data["highlight_word"],
        "close_narration": data["close_narration"],
        "order_rationale": data.get("order_rationale", ""),
        "entries": selected_entries,
        "skipped_entries": skipped_entries,
        "status": "researched",  # -> "video_generated" after stage 2
    }
    (draft_dir / "research.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"[research] Wrote {draft_dir / 'research.json'}")


if __name__ == "__main__":
    main()
