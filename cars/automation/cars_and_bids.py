import base64
import json
import os
import re
import subprocess
from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_REVIEW_MODEL = os.getenv("OPENAI_CAR_IMAGE_REVIEW_MODEL", "gpt-4o-mini")


def _tokenize(value):
    return re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?", str(value or ""))


def infer_search_params(search_hint):
    tokens = _tokenize(search_hint)
    if len(tokens) < 2:
        return None
    make = tokens[0].lower()
    model = tokens[1].lower()
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
        "shot_type string from [front, rear, side, front_3q, rear_3q, interior, engine, wheel, detail, exterior, other], "
        "scene_fit_tags array from [hero, exterior, interior, engine, detail, wheel, front, rear], "
        "quality_score 1-10, composition_score 1-10, target_match_confidence 1-10, reject boolean, reason string. "
        f"Target vehicle make: {search.get('make') or 'unknown'}. "
        f"Target vehicle model: {search.get('model') or 'unknown'}. "
        f"Specific variant context: {entry.get('name', '')}. "
        "Reject wrong cars, collages, ads, screenshots with unrelated cars, or images where the target car is not the main subject."
    )
    response = client.responses.create(
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
    )
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
    desired = ["front_3q", "front", "rear_3q", "rear", "exterior"]
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
    approved = [item for item in reviews if not item.get("reject") and (dest / item["path"]).exists()]
    pool = approved or [item for item in reviews if (dest / item["path"]).exists()]
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


def scrape_entry_images(scraper_dir, draft_images_dir, entry):
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
    manifest = load_manifest(manifest_path)
    review_payload = review_draft_images(dest, entry)
    images = choose_reviewed_images(dest, entry, review_payload, limit=6)
    manifest["ai_review"] = review_payload
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return images, manifest
