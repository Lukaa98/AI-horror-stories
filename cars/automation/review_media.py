import argparse
import base64
import json
import os
from pathlib import Path

from generate_sample import DEFAULT_SOURCE_TOPIC, ROOT, _inspect_source_image, _labels_from_path

DEFAULT_MODEL = os.getenv("OPENAI_MEDIA_REVIEW_MODEL", "gpt-4o-mini")


def _source_root(topic):
    return ROOT / "cars" / "output" / "sources" / topic


def _image_paths(topic):
    images_dir = _source_root(topic) / "images"
    if not images_dir.exists():
        return []
    paths = []
    for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        paths.extend(sorted(images_dir.glob(pattern)))
    return paths


def _data_url(path):
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{suffix};base64,{encoded}"


def _heuristic_review(path):
    labels = _labels_from_path(path)
    quality = _inspect_source_image({"path": path, "labels": labels, "source_url": None})
    text = str(path).lower()
    reject = not quality["approved"]
    reason = "; ".join(quality["flags"]) if quality["flags"] else "passes local quality checks"
    if any(word in text for word in ["main-nav", "homepage", "global-nav", "shopping", "community"]):
        reject = True
        reason = "looks like navigation/homepage/promo media, not a focused vehicle shot"
    return {
        "path": str(path.relative_to(ROOT)),
        "provider": "heuristic",
        "is_target_vehicle": not reject,
        "asset_type": labels[0] if labels else "general",
        "caption_match": labels,
        "quality_score": 8 if quality["approved"] else 3,
        "focus_score": 8 if quality.get("blur_score", 0) >= 8 else 3,
        "composition_score": 7 if quality["approved"] else 3,
        "vertical_crop_score": 6,
        "has_random_people": False,
        "has_page_ui_or_nav": any(word in text for word in ["main-nav", "homepage", "global-nav", "shopping", "community"]),
        "reject": reject,
        "reason": reason,
        "quality": quality,
    }


def _openai_review(path, model):
    from openai import OpenAI

    client = OpenAI()
    prompt = (
        "Review this car source image for a vertical YouTube Short. Return ONLY compact JSON with: "
        "is_target_vehicle boolean, asset_type string, caption_match array of labels from "
        "[exterior, interior, wheels, convertible_roof, performance, price, gallery], "
        "quality_score 1-10, focus_score 1-10, composition_score 1-10, vertical_crop_score 1-10, "
        "has_random_people boolean, has_page_ui_or_nav boolean, reject boolean, reason string. "
        "Reject blurry, off-topic, navigation/promo, tiny-detail, or badly cropped images."
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
    data["path"] = str(path.relative_to(ROOT))
    data["provider"] = "openai"
    data["quality"] = _inspect_source_image({"path": path, "labels": data.get("caption_match", []), "source_url": None})
    return data


def review_media(topic=DEFAULT_SOURCE_TOPIC, provider="auto", model=DEFAULT_MODEL, out=None):
    paths = _image_paths(topic)
    reviews = []
    use_openai = provider == "openai" or (provider == "auto" and os.getenv("OPENAI_API_KEY"))
    for path in paths:
        if use_openai:
            try:
                reviews.append(_openai_review(path, model))
                continue
            except Exception as exc:
                fallback = _heuristic_review(path)
                fallback["provider"] = "heuristic_after_openai_error"
                fallback["openai_error"] = str(exc)
                reviews.append(fallback)
                continue
        reviews.append(_heuristic_review(path))

    payload = {
        "topic": topic,
        "provider": "openai" if use_openai else "heuristic",
        "model": model if use_openai else None,
        "image_count": len(paths),
        "approved_count": sum(1 for item in reviews if not item.get("reject")),
        "rejected_count": sum(1 for item in reviews if item.get("reject")),
        "reviews": reviews,
    }
    out_path = Path(out) if out else _source_root(topic) / "media-review.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Review scraped car media and write media-review.json.")
    parser.add_argument("--topic", default=DEFAULT_SOURCE_TOPIC)
    parser.add_argument("--provider", choices=["auto", "heuristic", "openai"], default="auto")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    print(review_media(topic=args.topic, provider=args.provider, model=args.model, out=args.out))


if __name__ == "__main__":
    main()
