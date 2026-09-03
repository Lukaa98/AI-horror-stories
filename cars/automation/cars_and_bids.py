import base64
import json
import os
import re
import subprocess
from pathlib import Path

from openai_retry import with_openai_retry


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_REVIEW_MODEL = os.getenv("OPENAI_CAR_IMAGE_REVIEW_MODEL", "gpt-4o-mini")


def _tokenize(value):
    return re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?", str(value or ""))


MULTI_WORD_MODEL_PREFIXES = {"gr"}


def infer_search_params(search_hint):
    tokens = _tokenize(search_hint)
    if len(tokens) < 2:
        return None
    make = tokens[0].lower()
    model = tokens[1].lower()
    # Toyota's GR sub-brand (GR86, GR Corolla, GR Supra) shares "GR" as a
    # prefix, so taking just tokens[1] collapsed every one of them to the
    # same "gr" model and searched the whole sub-brand instead of the
    # specific car -- that's why a "Toyota GR Supra" search surfaced GR
    # Corolla listings first. Earlier this joined *every* remaining token
    # into the model to fix that, but the generation/provenance matching
    # elsewhere in this pipeline (_auction_provenance_matches_entry,
    # _generation_commons_terms) depends on model being exactly one word,
    # with trim/generation words handled separately -- so this only merges
    # the next word in for this one known prefix, not generally.
    if model in MULTI_WORD_MODEL_PREFIXES and len(tokens) > 2:
        model = f"{model} {tokens[2].lower()}"
    return {"make": make, "model": model}


def parse_year_range(value):
    years = [int(part) for part in re.findall(r"\b(?:19|20)\d{2}\b", str(value or ""))]
    if not years:
        return None, None
    if len(years) == 1:
        return years[0], years[0]
    return min(years), max(years)


def load_manifest(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _looks_like_real_openai_key(value):
    value = (value or "").strip()
    if value in {"", "sk-proj", "sk-"}:
        return False
    return value.startswith(("sk-", "sk-proj-")) and len(value) > 30


def _data_url(path):
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{suffix};base64,{encoded}"


def _draft_image_paths(dest):
    return sorted([path for path in dest.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES])


def _perceptual_hash(path):
    """Difference-hash (dHash): shrink to 9x8 grayscale and compare each
    pixel to its right neighbor, giving a 64-bit fingerprint where visually
    similar images (even at different resolutions or crops) hash close
    together. Matches the algorithm research_request.py's
    _image_fingerprints already uses for cross-entry duplicate detection --
    kept as a separate copy here since research_request.py imports from
    this module, not the other way around."""
    from PIL import Image

    with Image.open(path) as image:
        pixels = list(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).getdata())
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | int(pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return bits


def dedupe_similar_images(dest, max_distance=5):
    """Cars & Bids sometimes serves the same cover/hero photo at several
    different URLs (a thumbnail vs. the full-size version, different CDN
    resize params) -- the scraper's own URL-based dedup only catches exact
    URL matches, so several near-identical copies of one photo can get
    downloaded under different filenames, wasting every downstream review
    and scene slot on one repeated picture instead of real variety.
    Compares every downloaded image with a perceptual hash and deletes
    near-duplicates, keeping the first (typically highest-priority) copy
    of each distinct photo.
    """
    kept_hashes = []
    for path in _draft_image_paths(dest):
        try:
            digest = _perceptual_hash(path)
        except Exception:
            continue
        if any(bin(digest ^ existing).count("1") <= max_distance for existing in kept_hashes):
            path.unlink(missing_ok=True)
            continue
        kept_hashes.append(digest)


def _fallback_shot_type(path):
    stem = path.stem.lower()
    if stem.startswith("interior"):
        return "interior"
    if stem.startswith("engine"):
        return "engine"
    if stem.startswith("rear"):
        return "rear"
    if stem.startswith("front"):
        return "front"
    if stem.startswith("detail"):
        return "detail"
    return "exterior"


def _heuristic_review(path, entry):
    shot_type = _fallback_shot_type(path)
    tags = [shot_type]
    if shot_type in {"front", "rear", "side", "front_3q", "rear_3q", "detail"}:
        tags.append("exterior")
    if shot_type in {"front", "front_3q", "rear", "rear_3q", "side", "exterior"}:
        tags.append("hero")
    return {
        "path": path.name,
        "provider": "heuristic",
        "is_target_vehicle": True,
        "detected_make": infer_search_params(entry.get("search_hint", "") or "").get("make") if infer_search_params(entry.get("search_hint", "")) else None,
        "detected_model": infer_search_params(entry.get("search_hint", "") or "").get("model") if infer_search_params(entry.get("search_hint", "")) else None,
        "shot_type": shot_type,
        "scene_fit_tags": list(dict.fromkeys(tags)),
        "quality_score": 6,
        "composition_score": 6,
        "target_match_confidence": 6,
        "reject": False,
        "reason": "heuristic fallback review",
    }


def _openai_review(path, entry, model):
    from openai import OpenAI

    search = infer_search_params(entry.get("search_hint", "")) or {}
    client = OpenAI()
    prompt = (
        "Review this car photo for a short-form ranking video. Return ONLY compact JSON with keys: "
        "is_target_vehicle boolean, detected_make string_or_null, detected_model string_or_null, "
        "detected_generation_or_year string_or_null, detected_variant_or_engine string_or_null, "
        "visible_match_evidence array of short strings, generation_match_confidence 1-10, "
        "variant_match_confidence 1-10, "
        "shot_type string from [front, rear, side, front_3q, rear_3q, interior, engine, wheel, detail, exterior, other], "
        "scene_fit_tags array from [hero, exterior, interior, engine, detail, wheel, front, rear], "
        "quality_score 1-10, composition_score 1-10, target_match_confidence 1-10, reject boolean, reason string. "
        f"Target vehicle make: {search.get('make') or 'unknown'}. "
        f"Target vehicle model: {search.get('model') or 'unknown'}. "
        f"Specific variant context: {entry.get('name', '')}. "
        f"Target production years: {entry.get('years', 'unknown')}. "
        f"Expected visual identifiers: {', '.join(entry.get('visual_identifiers') or []) or 'not provided'}. "
        "The approval priority is the correct make, model, and generation/chassis. Look for generation-specific "
        "bodywork, lights, proportions, interior design, or source-year evidence. Record exact trim or engine "
        "confidence when visible, but DO NOT reject an otherwise correct-generation photo merely because an S, "
        "GTS, Spyder, engine, package, or badge cannot be proven from this angle. Reject a visibly wrong "
        "generation or a visibly contradictory variant. "
        "Reject wrong cars, collages, ads, screenshots with unrelated cars, or images where the target car is not the main subject."
    )
    response = with_openai_retry(lambda: client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": _data_url(path)},
                ],
            }
        ],
    ))
    text = response.output_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    data = json.loads(text)
    data["path"] = path.name
    data["provider"] = "openai"
    return data


def review_draft_images(dest, entry, provider="auto", model=DEFAULT_REVIEW_MODEL):
    paths = _draft_image_paths(dest)
    reviews = []
    api_key = os.getenv("OPENAI_API_KEY")
    has_real_key = _looks_like_real_openai_key(api_key)
    use_openai = provider == "openai" or (provider == "auto" and has_real_key)

    for path in paths:
        if use_openai:
            try:
                reviews.append(_openai_review(path, entry, model))
                continue
            except Exception as exc:
                fallback = _heuristic_review(path, entry)
                fallback["provider"] = "heuristic_after_openai_error"
                fallback["openai_error"] = str(exc)
                reviews.append(fallback)
                continue
        reviews.append(_heuristic_review(path, entry))

    payload = {
        "provider": "openai" if use_openai else "heuristic",
        "model": model if use_openai else None,
        "review_count": len(reviews),
        "reviews": reviews,
    }
    (dest / "ai-review.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _desired_shot_types(entry):
    text = " ".join(
        str(value or "")
        for value in [entry.get("name"), entry.get("visual_highlight"), entry.get("one_line_fact"), entry.get("label")]
    ).lower()
    # Cars & Bids listings almost always have a clean side-profile shot,
    # and it reads as the strongest establishing shot for a car-review
    # hook -- but it wasn't in this list at all before, so it only ever
    # showed up by accident (whenever it happened to rank high on quality
    # among leftover images), not because it was actually preferred.
    desired = ["side", "front_3q", "front", "rear_3q", "rear", "exterior"]
    if any(word in text for word in ["interior", "cabin", "cockpit", "dashboard"]):
        desired.insert(1, "interior")
    if any(word in text for word in ["engine", "v8", "v10", "horsepower", "powertrain"]):
        desired.insert(2, "engine")
    if any(word in text for word in ["brake", "wheel", "carbon", "aero", "detail", "blade"]):
        desired.append("detail")
        desired.append("wheel")
    desired.append("interior")
    desired.append("engine")
    desired.append("detail")
    return desired


def choose_reviewed_images(dest, entry, review_payload, limit=6):
    reviews = review_payload.get("reviews", [])
    desired_order = _desired_shot_types(entry)
    approved = [
        item for item in reviews
        if not item.get("reject")
        and item.get("is_target_vehicle")
        and int(
            item.get("generation_match_confidence")
            or item.get("target_match_confidence")
            or item.get("variant_match_confidence")
            or 0
        ) >= 6
        and (dest / item["path"]).exists()
    ]
    pool = approved
    if not pool:
        return []

    used = set()
    selected = []
    for desired in desired_order:
        match = next(
            (
                item for item in pool
                if item["path"] not in used
                and (item.get("shot_type") == desired or desired in (item.get("scene_fit_tags") or []))
            ),
            None,
        )
        if match:
            used.add(match["path"])
            selected.append(match)
        if len(selected) >= limit:
            break

    ranked_rest = sorted(
        [item for item in pool if item["path"] not in used],
        key=lambda item: (
            -(int(item.get("target_match_confidence") or 0)),
            -(int(item.get("quality_score") or 0)),
            -(int(item.get("composition_score") or 0)),
        ),
    )
    for item in ranked_rest:
        if len(selected) >= limit:
            break
        selected.append(item)

    return [f"images/{re.sub(r'[^a-z0-9]+', '-', entry['name'].lower()).strip('-') or 'entry'}/{item['path']}" for item in selected[:limit]]


def round_current_value(value):
    if value is None:
        return None
    value = int(value)
    if value >= 100_000:
        return int(round(value / 10_000.0) * 10_000)
    if value >= 10_000:
        return int(round(value / 1_000.0) * 1_000)
    if value >= 1_000:
        return int(round(value / 1_000.0) * 1_000)
    return int(round(value / 100.0) * 100)


def format_current_value(value):
    rounded = round_current_value(value)
    if rounded is None:
        return None
    if rounded >= 1000:
        return f"${int(round(rounded / 1000.0))}K"
    return f"${rounded}"


def enrich_entry_from_manifest(entry, manifest):
    selected = manifest.get("selected_auction") or {}
    sale_price = selected.get("sale_price")
    if sale_price:
        entry["current_value_usd"] = int(sale_price)
        entry["current_value_display"] = format_current_value(sale_price)
        entry["current_value_note"] = f"Recent Cars & Bids examples trade around {entry['current_value_display']}."
    if selected.get("url"):
        entry["image_source"] = {
            "provider": "cars_and_bids",
            "search_url": manifest.get("search_url"),
            "auction_url": selected.get("url"),
            "auction_title": selected.get("page_title") or selected.get("title"),
        }
    entry["engine_videos"] = manifest.get("videos", [])
    return entry


def augment_narration_with_current_value(entry):
    current_value = entry.get("current_value_display")
    if not current_value:
        return entry.get("one_line_fact", "")
    text = str(entry.get("one_line_fact", "")).strip()
    if not text:
        return f"Today, clean examples trade around {current_value}."
    if re.search(r"cars\s*&?\s*bids|sold for|trades around|worth", text, flags=re.I):
        return text
    joiner = "" if text.endswith((".", "!", "?")) else "."
    return f"{text}{joiner} Today, clean examples trade around {current_value}."


def scrape_entry_images(scraper_dir, draft_images_dir, entry, limit=6):
    params = infer_search_params(entry.get("search_hint", ""))
    if not params:
        return [], {}

    start_year, end_year = parse_year_range(entry.get("years"))
    topic_slug = re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-") or "entry"
    dest = draft_images_dir / topic_slug
    manifest_path = dest / "carsandbids-manifest.json"
    dest.mkdir(parents=True, exist_ok=True)

    cmd = [
        "node",
        "src/scrape-carsandbids-gallery.js",
        f"--make={params['make']}",
        f"--model={params['model']}",
        f"--query={entry.get('search_hint', '')}",
        f"--out-dir={dest}",
        f"--out-json={manifest_path}",
        f"--visual-highlight={entry.get('visual_highlight', '')}",
    ]
    if start_year:
        cmd.append(f"--start-year={start_year}")
    if end_year:
        cmd.append(f"--end-year={end_year}")

    subprocess.run(cmd, cwd=scraper_dir, check=False)
    dedupe_similar_images(dest)
    manifest = load_manifest(manifest_path)
    review_payload = review_draft_images(dest, entry)
    images = choose_reviewed_images(dest, entry, review_payload, limit=limit)
    manifest["ai_review"] = review_payload
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return images, manifest


def scrape_auction_images(scraper_dir, draft_images_dir, entry, auction_url, limit=6):
    """Like scrape_entry_images, but fetches one already-known listing
    directly instead of searching and picking from the top results --
    guarantees the photos come from the exact same car as a video already
    found on this auction, rather than whichever listing a separate,
    independently-ranked photo search happened to land on."""
    params = infer_search_params(entry.get("search_hint", "")) or {"make": "", "model": ""}
    topic_slug = re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-") or "entry"
    dest = draft_images_dir / topic_slug
    manifest_path = dest / "carsandbids-manifest.json"
    dest.mkdir(parents=True, exist_ok=True)

    cmd = [
        "node",
        "src/scrape-carsandbids-gallery.js",
        f"--auction-url={auction_url}",
        f"--make={params['make']}",
        f"--model={params['model']}",
        f"--query={entry.get('search_hint', '')}",
        f"--out-dir={dest}",
        f"--out-json={manifest_path}",
        f"--visual-highlight={entry.get('visual_highlight', '')}",
    ]

    subprocess.run(cmd, cwd=scraper_dir, check=False)
    dedupe_similar_images(dest)
    manifest = load_manifest(manifest_path)
    review_payload = review_draft_images(dest, entry)
    images = choose_reviewed_images(dest, entry, review_payload, limit=limit)
    manifest["ai_review"] = review_payload
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return images, manifest


def discover_entry_engine_videos(scraper_dir, draft_images_dir, entry):
    """Run a video-only Cars & Bids search for this entry's engine clips.

    scrape_entry_images visits only the top few price-sorted listings so its
    photo galleries stay high quality, but that same narrow, price-sorted
    pool routinely misses real seller-uploaded engine videos that happen to
    sit in cheaper listings - the standalone video-test tool searches up to
    20 listings in the site's default order specifically to avoid that, and
    this mirrors it so the main pipeline finds engine clips just as
    reliably. Fails open (returns []) on any error since a missing engine
    clip is optional, not a reason to fail the whole research run.
    """
    params = infer_search_params(entry.get("search_hint", ""))
    if not params:
        return []

    start_year, end_year = parse_year_range(entry.get("years"))
    topic_slug = re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-") or "entry"
    dest = draft_images_dir / topic_slug
    manifest_path = dest / "carsandbids-video-manifest.json"
    dest.mkdir(parents=True, exist_ok=True)

    cmd = [
        "node",
        "src/scrape-carsandbids-gallery.js",
        f"--make={params['make']}",
        f"--model={params['model']}",
        f"--query={entry.get('search_hint', '')}",
        f"--out-dir={dest}",
        f"--out-json={manifest_path}",
        "--skip-images=true",
    ]
    if start_year:
        cmd.append(f"--start-year={start_year}")
    if end_year:
        cmd.append(f"--end-year={end_year}")

    try:
        subprocess.run(cmd, cwd=scraper_dir, check=False, timeout=240)
        manifest = load_manifest(manifest_path)
    except Exception:
        return []
    return manifest.get("videos", [])
