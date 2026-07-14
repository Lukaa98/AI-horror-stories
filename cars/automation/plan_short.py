import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DEFAULT_TOPIC = "mazda-mx5-miata-official"
DEFAULT_MODEL = os.getenv("OPENAI_SHORT_PLANNER_MODEL", "gpt-4o-mini")


def _source_root(topic):
    return ROOT / "cars" / "output" / "sources" / topic


def _looks_like_real_openai_key(value):
    value = (value or "").strip()
    if value in {"", "sk-proj", "sk-"}:
        return False
    return value.startswith(("sk-", "sk-proj-")) and len(value) > 30


def _read_json(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _load_source_packet(topic):
    return _read_json(_source_root(topic) / "source-packet.json", {})


def _load_media_review(topic):
    return _read_json(_source_root(topic) / "media-review.json", {"reviews": []})


def _asset_rows(topic, limit=40):
    root = _source_root(topic)
    packet = _load_source_packet(topic)
    review_by_path = {item.get("path"): item for item in _load_media_review(topic).get("reviews", []) if item.get("path")}
    rows = []
    for item in packet.get("downloaded_images", []):
        if item.get("error") or not item.get("path"):
            continue
        rel = f"cars/output/sources/{topic}/{item['path']}"
        review = review_by_path.get(rel, {})
        rows.append({
            "id": len(rows) + 1,
            "type": "image",
            "path": rel,
            "source_url": item.get("source_url"),
            "labels": item.get("labels", []),
            "scraper_score": item.get("score", 0),
            "ai_quality_score": review.get("quality_score"),
            "ai_composition_score": review.get("composition_score"),
            "ai_reject": review.get("reject"),
            "ai_reason": review.get("reason"),
        })
    for item in packet.get("downloaded_videos", []):
        if item.get("error") or not item.get("path"):
            continue
        rel = f"cars/output/sources/{topic}/{item['path']}"
        rows.append({
            "id": len(rows) + 1,
            "type": "video",
            "path": rel,
            "source_url": item.get("source_url"),
            "labels": item.get("labels", []),
            "scraper_score": item.get("score", 0),
        })
    rows.sort(key=lambda row: (
        row.get("ai_reject") is True,
        -(row.get("ai_quality_score") or 0),
        -(row.get("ai_composition_score") or 0),
        -row.get("scraper_score", 0),
    ))
    return rows[:limit]


def _source_summary(topic):
    packet = _load_source_packet(topic)
    pages = packet.get("pages", [])
    text_parts = []
    for page in pages[:4]:
        text_parts.append({
            "source_name": page.get("source_name"),
            "source_url": page.get("source_url"),
            "title": page.get("title"),
            "description": page.get("description"),
            "text_sample": (page.get("text_sample") or "")[:2500],
        })
    if not text_parts and packet.get("text_sample"):
        text_parts.append({"text_sample": packet.get("text_sample", "")[:2500]})
    return {
        "topic": topic,
        "title": packet.get("title"),
        "description": packet.get("description"),
        "pages": text_parts,
    }


def _fallback_plan(topic, assets):
    def pick(*wanted):
        wanted = set(wanted)
        for asset in assets:
            if asset.get("ai_reject"):
                continue
            if wanted & set(asset.get("labels") or []):
                return {"type": asset["type"], "path": asset["path"], "source_url": asset.get("source_url"), "reason": f"Matched labels {sorted(wanted)}"}
        for asset in assets:
            if not asset.get("ai_reject"):
                return {"type": asset["type"], "path": asset["path"], "source_url": asset.get("source_url"), "reason": "Best available approved asset"}
        return None

    return {
        "topic": topic,
        "source_topic": topic,
        "planner_provider": "heuristic",
        "title": "The Miata formula is annoyingly simple",
        "angle": "The Miata works because it is light, simple, and driver-focused—not because it wins a horsepower war.",
        "hook": "This is why people will not shut up about the Miata.",
        "target_seconds": 20,
        "scenes": [
            {
                "stage": "hook",
                "duration": 3.5,
                "narration": "This is why people will not shut up about the Miata.",
                "caption": "MIATA PEOPLE HAVE A POINT",
                "stat": "RWD + LOW WEIGHT",
                "media_tags": ["exterior", "hero"],
                "selected_media": pick("exterior", "hero"),
            },
            {
                "stage": "performance",
                "duration": 4.0,
                "narration": "It only has one eighty one horsepower, but it barely weighs anything.",
                "caption": "181 HP / LIGHTWEIGHT",
                "stat": "~75 HP PER 1,000 LB",
                "media_tags": ["performance", "engine"],
                "selected_media": pick("performance", "engine"),
            },
            {
                "stage": "interior",
                "duration": 4.0,
                "narration": "Inside, everything is low, tight, and pointed at the driver.",
                "caption": "DRIVER-FIRST CABIN",
                "stat": "LOW + TIGHT",
                "media_tags": ["interior", "dashboard"],
                "selected_media": pick("interior", "dashboard"),
            },
            {
                "stage": "roof",
                "duration": 4.0,
                "narration": "The RF roof is the party trick: a tiny coupe look that opens in seconds.",
                "caption": "RF ROOF TRICK",
                "stat": "ABOUT 13 SEC",
                "media_tags": ["convertible_roof", "exterior"],
                "selected_media": pick("convertible_roof", "exterior"),
            },
            {
                "stage": "opinion",
                "duration": 4.5,
                "narration": "Not the fastest car here. Maybe the one you actually want every weekend.",
                "caption": "WEEKEND SCORE: HIGH",
                "stat": "WOULD YOU DAILY IT?",
                "media_tags": ["exterior", "gallery"],
                "selected_media": pick("exterior", "gallery"),
            },
        ],
    }


def _openai_plan(topic, assets, model):
    from openai import OpenAI

    client = OpenAI()
    prompt = {
        "task": "Create a 20-second vertical YouTube Short plan from official car source data.",
        "rules": [
            "Use only facts supported by the source summary.",
            "Make the script sound human and YouTube-native, not like a spec sheet.",
            "Pick exact media paths from the provided assets for each scene.",
            "Prefer images for now because the current renderer does not cut video b-roll yet.",
            "Each scene narration must match the selected media.",
            "Return valid JSON only.",
        ],
        "required_schema": {
            "topic": "string",
            "source_topic": topic,
            "planner_provider": "openai",
            "title": "string",
            "angle": "string",
            "hook": "string",
            "target_seconds": 20,
            "scenes": [
                {
                    "stage": "hook|performance|interior|detail|opinion",
                    "duration": "number",
                    "narration": "short conversational sentence",
                    "caption": "3-6 word overlay",
                    "stat": "short stat chip or empty string",
                    "media_tags": ["exterior"],
                    "selected_media": {"type": "image", "path": "one provided asset path", "source_url": "url", "reason": "why it fits"},
                }
            ],
        },
        "source_summary": _source_summary(topic),
        "assets": assets,
    }
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(prompt)}]}],
    )
    text = response.output_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    plan = json.loads(text)
    plan["source_topic"] = topic
    plan["planner_provider"] = "openai"
    return plan


def plan_short(topic=DEFAULT_TOPIC, provider="auto", model=DEFAULT_MODEL, out=None):
    assets = _asset_rows(topic)
    use_openai = provider == "openai" or (provider == "auto" and _looks_like_real_openai_key(os.getenv("OPENAI_API_KEY")))
    if provider == "openai" and not use_openai:
        raise SystemExit("OPENAI_API_KEY is missing or still placeholder; use --provider heuristic or complete .env.")
    if use_openai:
        try:
            plan = _openai_plan(topic, assets, model)
        except Exception as exc:
            if provider == "openai":
                raise
            plan = _fallback_plan(topic, assets)
            plan["planner_provider"] = "heuristic_after_openai_error"
            plan["openai_error"] = str(exc)
    else:
        plan = _fallback_plan(topic, assets)
    plan["asset_count"] = len(assets)
    out_path = Path(out) if out else _source_root(topic) / "short-plan.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Create an AI-directed 20-second car Short plan from scraped source packets.")
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--provider", choices=["auto", "openai", "heuristic"], default="auto")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    print(plan_short(topic=args.topic, provider=args.provider, model=args.model, out=args.out))


if __name__ == "__main__":
    main()
