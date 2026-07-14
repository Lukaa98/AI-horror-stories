import argparse
import json
import math
import os
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat
except ModuleNotFoundError as exc:
    if exc.name == "PIL":
        raise SystemExit(
            "Missing Pillow dependency. From the repo root, run: pip install -r requirements.txt"
        ) from exc
    raise

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
HORROR_SRC = ROOT / "horror_stories" / "src"
if str(HORROR_SRC) not in sys.path:
    sys.path.insert(0, str(HORROR_SRC))

from video_pipeline.short_editor import build_short_video  # noqa: E402

OUTPUT_ROOT = ROOT / "cars" / "output" / "samples"
SAMPLE_SLUG = "mazda-mx5-miata-35th-anniversary"
CANVAS = (1080, 1920)
FAST_CANVAS = (540, 960)

DEFAULT_SOURCE_TOPIC = "mazda-mx5-miata-official"

SOURCE_PACKET = {
    "topic_signal": {
        "source_name": "automotive trend signal",
        "source_url": "https://www.youtube.com/watch?v=eU14d5BVmjM",
        "role": "topic_signal_only_not_for_script",
        "title": "Mazda MX-5 Miata 35th Anniversary search/topic signal",
        "observed_at": "2026-07-07T00:00:00Z",
        "notes": "Used only to identify a trending car/topic window; the generated video should not mention the creator.",
    },
    "verified_sources": [
        {
            "source_name": "Mazda USA Newsroom",
            "source_url": "https://news.mazdausa.com/2025-01-24-Mazda-Announces-2025-MX-5-Miata-35th-Anniversary",
            "facts_used": [
                "2025 MX-5 Miata 35th Anniversary MSRP is $36,250 before destination/taxes/fees.",
                "U.S. production is limited to 300 units.",
                "The edition uses Artisan Red Metallic paint, a tan interior, and a beige soft top.",
            ],
        },
        {
            "source_name": "Mazda USA 35th Anniversary page",
            "source_url": "https://news.mazdausa.com/35th-Anniversary-Edition-MX-5",
            "facts_used": [
                "The special edition includes tan Nappa leather, serialized badging, 17-inch wheels, and a matching key fob sleeve.",
            ],
        },
        {
            "source_name": "Car and Driver 2025 MX-5 overview",
            "source_url": "https://www.caranddriver.com/mazda/mx-5-miata-2025",
            "facts_used": [
                "The 35th Anniversary Edition is based on the Grand Touring trim and keeps the core lightweight roadster formula.",
            ],
        },
    ],
    "media_policy": (
        "Sample prefers downloaded official/source car images from scraper output, then screenshots. "
        "Falls back to generated text cards only when no approved local media is available."
    ),
}

STORYBOARD = {
    "title": "2026 Mazda MX-5 Miata: tiny roadster, real numbers",
    "hook": "The 2026 Mazda MX-5 Miata is simple: low price, light weight, and enough power to matter.",
    "narration": "",
    "visual_identity": "Fast automotive short with sharp official media, minimal overlays, and simple spec cards.",
    "music_mood": "quick premium car-news pulse",
    "cta": "Weekend toy or daily driver? Drop your score.",
    "target_seconds": 18,
    "scene_count": 5,
    "story_provider": "local-car-template",
    "character_name": "Mazda MX-5 Miata",
    "theme": "Mazda MX-5 Miata official-source spec sample",
    "scorecard": {
        "weekend_score": 31,
        "daily_score": 24,
        "total_score": 55,
        "reliability_score": 7,
        "notes": "Internal prototype score inspired by weekend/daily usability categories; not a DougScore clone. Tune with real owner data later.",
    },
    "specs": {
        "starting_price_usd": 30430,
        "as_shown_usd": 36325,
        "horsepower": 181,
        "torque_lb_ft": 151,
        "curb_weight_est_lb": 2400,
        "power_to_weight_hp_per_1000_lb": 75,
        "drivetrain": "rear-wheel drive",
    },
    "scenes": [
        {
            "stage": "hook",
            "narration": "Miata in one sentence: thirty grand, rear wheel drive, and almost no extra weight.",
            "caption": "MIATA, SIMPLIFIED",
            "stat": "$30,430 STARTING",
            "image_prompt": "official exterior hero image, focused car crop",
            "media_tags": ["exterior", "hero"],
        },
        {
            "stage": "performance",
            "narration": "You get one eighty one horsepower and one fifty one pound feet in a car around twenty four hundred pounds.",
            "caption": "181 HP / 151 LB-FT",
            "stat": "~75 HP PER 1,000 LB",
            "image_prompt": "engine or performance official media",
            "media_tags": ["performance", "engine"],
        },
        {
            "stage": "interior",
            "narration": "Inside, the selling point is still the driver position: low, tight, and built around the wheel.",
            "caption": "DRIVER-FIRST CABIN",
            "stat": "manual-friendly cockpit",
            "image_prompt": "official interior cockpit image",
            "media_tags": ["interior", "dashboard"],
        },
        {
            "stage": "roof",
            "narration": "The RF roof is the party trick: a coupe look that opens in about thirteen seconds.",
            "caption": "RF ROOF TRICK",
            "stat": "about 13 sec",
            "image_prompt": "convertible roof or rear exterior official media",
            "media_tags": ["convertible_roof", "exterior"],
        },
        {
            "stage": "score",
            "narration": "My early score: weekend thirty one, daily twenty four, total fifty five. Great toy, okay daily.",
            "caption": "OUR QUICK SCORE",
            "stat": "31 WEEKEND + 24 DAILY = 55",
            "image_prompt": "best exterior image with simple score card",
            "media_tags": ["exterior", "gallery"],
        },
    ],
}
STORYBOARD["narration"] = " ".join(scene["narration"] for scene in STORYBOARD["scenes"])

EDIT_STYLES = {
    "hook": {"motion": "push_in", "layout": "thumbstop", "crop_focus": "hero", "accent": [255, 204, 92], "caption_top": 0.47, "stat_top": 0.63},
    "performance": {"motion": "drift_left", "layout": "spec_punch", "crop_focus": "front_three_quarter", "accent": [255, 96, 72], "caption_top": 0.51, "stat_top": 0.66},
    "interior": {"motion": "rise", "layout": "walkaround", "crop_focus": "dashboard", "accent": [118, 196, 255], "caption_top": 0.53, "stat_top": 0.67},
    "roof": {"motion": "drift_right", "layout": "feature", "crop_focus": "roofline", "accent": [236, 190, 145], "caption_top": 0.50, "stat_top": 0.65},
    "detail": {"motion": "pull_back", "layout": "feature", "crop_focus": "detail", "accent": [236, 190, 145], "caption_top": 0.50, "stat_top": 0.65},
    "opinion": {"motion": "push_in", "layout": "verdict", "crop_focus": "hero", "accent": [255, 204, 92], "caption_top": 0.48, "stat_top": 0.64},
    "score": {"motion": "push_in", "layout": "verdict", "crop_focus": "hero", "accent": [255, 204, 92], "caption_top": 0.48, "stat_top": 0.64},
}


def _edit_style_for_scene(scene, index):
    style = dict(EDIT_STYLES.get(scene.get("stage"), EDIT_STYLES["detail"]))
    style["beat"] = index
    style["cut_style"] = "hard_cut" if index == 1 else "fast_match_cut"
    return style


def _apply_edit_styles(storyboard):
    for index, scene in enumerate(storyboard.get("scenes", []), start=1):
        scene.setdefault("edit_style", _edit_style_for_scene(scene, index))
    storyboard["edit_style_version"] = "cars-milestone-3a"
    storyboard["editor_notes"] = (
        "Each planned scene now carries an edit_style with motion, layout, crop focus, "
        "accent color, and cut style so the renderer can feel more like a paced car Short."
    )
    return storyboard


def _font(size):
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width, max_lines=4):
    words = text.split()
    lines = []
    current = []
    for word in words:
        candidate = " ".join([*current, word]).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines[:max_lines])


def _cover_crop(image, size):
    target_w, target_h = size
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    resized = image.resize((int(src_w * scale), int(src_h * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))



def _focus_fit_canvas(image, size, top_margin_ratio=0.06, max_height_ratio=0.50, max_horizontal_crop_ratio=0.34):
    """Create a vertical Shorts frame that feels cropped-in while preserving the main subject.

    For wide official car images, a pure 9:16 cover crop often cuts off the car. This
    keeps a blurred full-frame cover background, then enlarges the source image into a
    prominent foreground crop. Horizontal cropping is capped and audited so the subject
    remains mostly visible.
    """
    target_w, target_h = size
    background = _cover_crop(image, size).filter(ImageFilter.GaussianBlur(radius=max(12, target_w // 36)))
    background = Image.blend(background, Image.new("RGB", size, (7, 8, 10)), 0.18)

    desired_h = int(target_h * max_height_ratio)
    scale = desired_h / image.height
    resized = image.resize((max(1, int(image.width * scale)), desired_h), Image.Resampling.LANCZOS)

    crop_box = (0, 0, resized.width, resized.height)
    horizontal_crop_ratio = 0.0
    if resized.width > target_w:
        overflow = resized.width - target_w
        max_crop_px = int(resized.width * max_horizontal_crop_ratio)
        crop_width = resized.width - min(overflow, max_crop_px)
        left = max(0, (resized.width - crop_width) // 2)
        crop_box = (left, 0, left + crop_width, resized.height)
        foreground = resized.crop(crop_box)
        horizontal_crop_ratio = round((resized.width - crop_width) / max(1, resized.width), 3)
    else:
        foreground = resized

    if foreground.width != target_w:
        # Final resize keeps the foreground edge-to-edge after the visibility-safe crop.
        foreground = foreground.resize((target_w, max(1, int(foreground.height * target_w / foreground.width))), Image.Resampling.LANCZOS)

    if foreground.height > int(target_h * 0.62):
        foreground = foreground.crop((0, 0, foreground.width, int(target_h * 0.62)))

    left = (target_w - foreground.width) // 2
    top = int(target_h * top_margin_ratio)

    canvas = background.convert("RGBA")
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (left - 16, top - 16, left + foreground.width + 16, top + foreground.height + 16),
        radius=30,
        fill=(0, 0, 0, 145),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(foreground.convert("RGBA"), (left, top))

    audit = {
        "mode": "focus_fit_canvas",
        "source_size": list(image.size),
        "resized_size": [resized.width, resized.height],
        "foreground_size": [foreground.width, foreground.height],
        "foreground_top": top,
        "crop_box": list(crop_box),
        "horizontal_crop_ratio": horizontal_crop_ratio,
        "subject_visibility": "capped horizontal crop; full image retained in blurred background",
    }
    return canvas, audit

def _blurred_fit_canvas(image, size, top_margin_ratio=0.12, max_height_ratio=0.52):
    """Fill the vertical frame without cropping the car out of the source image."""
    target_w, target_h = size
    background = _cover_crop(image, size).filter(ImageFilter.GaussianBlur(radius=max(10, target_w // 45)))
    background = Image.blend(background, Image.new("RGB", size, (8, 8, 10)), 0.25)

    foreground = image.copy()
    foreground.thumbnail((int(target_w * 0.94), int(target_h * max_height_ratio)), Image.Resampling.LANCZOS)
    left = (target_w - foreground.width) // 2
    top = int(target_h * top_margin_ratio)

    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (left - 14, top - 14, left + foreground.width + 14, top + foreground.height + 14),
        radius=28,
        fill=(0, 0, 0, 120),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=16))

    canvas = background.convert("RGBA")
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(foreground.convert("RGBA"), (left, top))
    return canvas


def _candidate_source_screenshots(source_topic=DEFAULT_SOURCE_TOPIC):
    source_root = ROOT / "cars" / "output" / "sources"
    candidates = [source_topic, SAMPLE_SLUG]
    paths = []
    for topic in candidates:
        screenshot_dir = source_root / topic / "screenshots"
        for name in ["viewport.png", "full-page.png"]:
            path = screenshot_dir / name
            if path.exists():
                paths.append(path)
    return paths


def _source_root(source_topic=DEFAULT_SOURCE_TOPIC):
    return ROOT / "cars" / "output" / "sources" / source_topic


def _load_scraped_source_packet(source_topic=DEFAULT_SOURCE_TOPIC):
    packet_path = _source_root(source_topic) / "source-packet.json"
    if not packet_path.exists():
        return None
    try:
        return json.loads(packet_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_media_review(source_topic=DEFAULT_SOURCE_TOPIC):
    review_path = _source_root(source_topic) / "media-review.json"
    if not review_path.exists():
        return {}
    try:
        payload = json.loads(review_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    reviews = {}
    for item in payload.get("reviews", []):
        rel_path = item.get("path")
        if not rel_path:
            continue
        reviews[(ROOT / rel_path).resolve()] = item
    return reviews


def _load_short_plan(plan_path):
    if not plan_path:
        return None
    path = Path(plan_path)
    if not path.exists():
        raise SystemExit(f"Short plan not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Short plan is not valid JSON: {path}") from exc


def _storyboard_from_plan(plan):
    storyboard = dict(STORYBOARD)
    storyboard["title"] = plan.get("title") or storyboard["title"]
    storyboard["hook"] = plan.get("hook") or storyboard["hook"]
    storyboard["theme"] = plan.get("angle") or storyboard["theme"]
    storyboard["target_seconds"] = int(plan.get("target_seconds") or storyboard["target_seconds"])
    storyboard["story_provider"] = f"short-plan:{plan.get('planner_provider', 'unknown')}"
    scenes = []
    for scene in plan.get("scenes", []):
        scenes.append({
            "stage": scene.get("stage", "scene"),
            "narration": scene.get("narration", ""),
            "caption": scene.get("caption") or scene.get("stage", "CAR SHORT").upper(),
            "stat": scene.get("stat", ""),
            "image_prompt": scene.get("visual_need", ""),
            "media_tags": scene.get("media_tags", []),
            "planned_media": scene.get("selected_media"),
        })
    if scenes:
        storyboard["scenes"] = scenes
        storyboard["scene_count"] = len(scenes)
        storyboard["narration"] = " ".join(scene["narration"] for scene in scenes)
    storyboard["short_plan"] = plan
    return storyboard


def _candidate_source_images(source_topic=DEFAULT_SOURCE_TOPIC):
    source_root = _source_root(source_topic)
    images_dir = source_root / "images"
    packet = _load_scraped_source_packet(source_topic) or {}
    review_by_path = _load_media_review(source_topic)
    assets = []
    for item in packet.get("downloaded_images", []):
        if item.get("error"):
            continue
        rel = item.get("path")
        if rel:
            candidate = source_root / rel
            if candidate.exists():
                assets.append({
                    "path": candidate,
                    "labels": item.get("labels", []),
                    "score": item.get("score", 0),
                    "source_url": item.get("source_url"),
                })
    if images_dir.exists():
        known = {asset["path"] for asset in assets}
        for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            for path in sorted(images_dir.glob(pattern)):
                if path not in known:
                    assets.append({"path": path, "labels": _labels_from_path(path), "score": 0, "source_url": None})
                    known.add(path)
    deduped = []
    seen = set()
    for asset in assets:
        if asset["path"] not in seen:
            seen.add(asset["path"])
            asset["quality"] = _inspect_source_image(asset)
            review = review_by_path.get(asset["path"].resolve())
            if review:
                asset["ai_review"] = review
                caption_labels = review.get("caption_match") or []
                asset["labels"] = list(dict.fromkeys([*asset.get("labels", []), *caption_labels]))
                if review.get("reject"):
                    asset["quality"]["approved"] = False
                    reason = str(review.get("reason") or "no reason supplied")
                    asset["quality"].setdefault("flags", []).append(f"ai_rejected:{reason}")
                else:
                    asset["score"] = (
                        asset.get("score", 0)
                        + int(review.get("quality_score") or 0) * 5
                        + int(review.get("composition_score") or 0) * 3
                    )
            deduped.append(asset)
    return deduped


def _labels_from_path(path):
    text = str(path).lower()
    labels = []
    for label, words in {
        "hero": ["hero", "000_hero"],
        "interior": ["interior", "cabin", "seat", "leather", "nappa", "dashboard", "gauge", "cockpit"],
        "dashboard": ["dashboard", "gauge", "cockpit", "instrument"],
        "exterior": ["exterior", "hero", "360", "soulred", "soul-red", "roadster", "gallery"],
        "wheels": ["wheel", "rim", "alloy"],
        "convertible_roof": ["convertible", "soft-top", "hard-top", "roof", "rf"],
        "engine": ["engine", "skyactiv"],
        "performance": ["engine", "tach", "gauge", "instrument", "performance", "suspension", "brakes", "limited-slip", "5050", "50-50"],
        "price": ["price", "msrp"],
    }.items():
        if any(word in text for word in words):
            labels.append(label)
    return labels or ["general"]


def _blur_score(path):
    try:
        image = Image.open(path).convert("L")
        image.thumbnail((360, 360), Image.Resampling.LANCZOS)
        edges = image.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(edges)
        return round(sum(stat.var) / max(1, len(stat.var)), 2)
    except Exception:
        return 0.0


def _inspect_source_image(asset):
    path = asset["path"]
    source_url = asset.get("source_url") or ""
    text = f"{path} {source_url}".lower()
    flags = []
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        return {"approved": False, "flags": [f"unreadable:{exc}"], "blur_score": 0, "width": 0, "height": 0}

    if width < 700 or height < 420:
        flags.append("low_resolution")
    if any(bad in text for bad in ["main-nav", "homepage", "global-nav", "shopping", "community", "owner", "national-geographic", "sensor-movie"]):
        flags.append("off_topic_navigation_or_promo_asset")
    if source_url and "siteassets/vehicles/" not in source_url.lower() and "prnewswire" not in source_url.lower():
        flags.append("not_vehicle_media_path")
    blur_score = _blur_score(path)
    if blur_score < 8:
        flags.append("possibly_blurry_or_low_detail")
    return {
        "approved": not flags,
        "flags": flags,
        "blur_score": blur_score,
        "width": width,
        "height": height,
    }


def _select_source_image(scene, assets, used_paths):
    if not assets:
        return None
    desired = set(scene.get("media_tags", []))
    approved_assets = [asset for asset in assets if asset.get("quality", {}).get("approved", True)]
    candidate_pool = approved_assets or assets
    matching_pool = [
        asset for asset in candidate_pool
        if desired & set(asset.get("labels") or [])
    ]
    if matching_pool:
        candidate_pool = matching_pool
    ranked = []
    for asset in candidate_pool:
        labels = set(asset.get("labels") or [])
        match_score = len(desired & labels) * 100
        missing_match_penalty = 150 if desired and not (desired & labels) else 0
        reuse_penalty = 80 if asset["path"] in used_paths else 0
        quality_penalty = 0 if asset.get("quality", {}).get("approved", True) else 120
        ai = asset.get("ai_review") or {}
        ai_score = int(ai.get("quality_score") or 0) * 8 + int(ai.get("composition_score") or 0) * 4
        ranked.append((
            match_score + asset.get("score", 0) + ai_score - reuse_penalty - quality_penalty - missing_match_penalty,
            asset,
        ))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = ranked[0][1]
    used_paths.add(selected["path"])
    return selected


def _planned_source_image(scene, assets):
    planned = scene.get("planned_media") or {}
    planned_path = planned.get("path")
    if not planned_path:
        return None
    planned_abs = (ROOT / planned_path).resolve()
    for asset in assets:
        if asset["path"].resolve() == planned_abs and asset.get("quality", {}).get("approved", True):
            return asset
    return None


def _draw_car_image_scene(scene, index, out_path, source_image_path, fast=False):
    size = FAST_CANVAS if fast else CANVAS
    width, height = size
    scale = width / 1080
    style = scene.get("edit_style") or _edit_style_for_scene(scene, index)
    accent = tuple(style.get("accent") or [236, 190, 145])
    layout = style.get("layout", "feature")
    top_margin = 0.045 if layout in {"thumbstop", "spec_punch"} else 0.06
    max_height = 0.54 if layout in {"thumbstop", "walkaround"} else 0.50
    base = Image.open(source_image_path).convert("RGB")
    image, crop_audit = _focus_fit_canvas(base, size, top_margin_ratio=top_margin, max_height_ratio=max_height)
    scene["crop_audit"] = crop_audit
    draw = ImageDraw.Draw(image)

    title_font = _font(int((68 if layout == "thumbstop" else 58) * scale))
    body_font = _font(int(38 * scale))
    small_font = _font(int(30 * scale))
    highlight = accent

    draw.rounded_rectangle(
        (int(46 * scale), int(55 * scale), int(width - 46 * scale), int(152 * scale)),
        radius=int(24 * scale),
        fill=(0, 0, 0, 210),
        outline=highlight,
        width=max(1, int(3 * scale)),
    )
    draw.text((int(72 * scale), int(83 * scale)), f"{scene.get('stage', 'scene').upper()} CUT", font=small_font, fill=highlight)
    draw.text((int(width - 210 * scale), int(83 * scale)), f"BEAT {index}", font=small_font, fill=(255, 235, 210))

    # A tiny progress strip makes the still frame feel intentionally edited, not like a raw scrape.
    strip_y = int(164 * scale)
    strip_x1 = int(58 * scale)
    strip_x2 = int(width - 58 * scale)
    draw.rounded_rectangle((strip_x1, strip_y, strip_x2, strip_y + int(10 * scale)), radius=int(8 * scale), fill=(255, 255, 255, 55))
    progress_x = strip_x1 + int((strip_x2 - strip_x1) * min(1.0, index / max(1, scene.get("scene_count", 5))))
    draw.rounded_rectangle((strip_x1, strip_y, progress_x, strip_y + int(10 * scale)), radius=int(8 * scale), fill=highlight)

    caption = _wrap(draw, scene["caption"], title_font, int(width * 0.82), max_lines=2)
    caption_bbox = draw.multiline_textbbox((0, 0), caption, font=title_font, spacing=int(10 * scale))
    caption_top = int(height * float(style.get("caption_top", 0.50)))
    draw.rounded_rectangle(
        (
            int(50 * scale),
            caption_top - int(34 * scale),
            int(width - 50 * scale),
            caption_top + (caption_bbox[3] - caption_bbox[1]) + int(48 * scale),
        ),
        radius=int(30 * scale),
        fill=(0, 0, 0, 205),
    )
    draw.multiline_text(
        (int((width - (caption_bbox[2] - caption_bbox[0])) / 2), caption_top),
        caption,
        font=title_font,
        fill=(255, 240, 220),
        spacing=int(10 * scale),
        align="center",
        stroke_width=max(1, int(3 * scale)),
        stroke_fill=(0, 0, 0),
    )

    stat = scene.get("stat")
    if stat:
        stat_font = _font(int(34 * scale))
        stat_bbox = draw.textbbox((0, 0), stat, font=stat_font)
        stat_w = stat_bbox[2] - stat_bbox[0]
        stat_h = stat_bbox[3] - stat_bbox[1]
        stat_x = int((width - stat_w) / 2)
        stat_y = int(height * float(style.get("stat_top", 0.64)))
        draw.rounded_rectangle(
            (
                stat_x - int(28 * scale),
                stat_y - int(18 * scale),
                stat_x + stat_w + int(28 * scale),
                stat_y + stat_h + int(24 * scale),
            ),
            radius=int(22 * scale),
            fill=(145, 22, 20, 225),
            outline=highlight,
            width=max(1, int(2 * scale)),
        )
        draw.text((stat_x, stat_y), stat, font=stat_font, fill=(255, 248, 230))

    narration = _wrap(draw, scene["narration"], body_font, int(width * 0.82), max_lines=4)
    draw.rounded_rectangle(
        (int(58 * scale), int(height * 0.755), int(width - 58 * scale), int(height * 0.925)),
        radius=int(28 * scale),
        fill=(0, 0, 0, 215),
        outline=highlight if layout in {"spec_punch", "verdict"} else None,
        width=max(1, int(2 * scale)),
    )
    draw.multiline_text(
        (int(95 * scale), int(height * 0.785)),
        narration,
        font=body_font,
        fill=(255, 249, 235),
        spacing=int(8 * scale),
    )
    image.convert("RGB").save(out_path)


def _draw_source_screenshot_scene(scene, index, out_path, source_image_path, fast=False):
    size = FAST_CANVAS if fast else CANVAS
    width, height = size
    scale = width / 1080
    base = Image.open(source_image_path).convert("RGB")
    image = _cover_crop(base, size).filter(ImageFilter.GaussianBlur(radius=1.4 * scale))
    overlay = Image.new("RGBA", size, (16, 8, 6, 125))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)

    title_font = _font(int(58 * scale))
    small_font = _font(int(30 * scale))
    body_font = _font(int(42 * scale))
    badge_font = _font(int(28 * scale))
    highlight = (236, 190, 145)

    draw.rounded_rectangle(
        (int(46 * scale), int(58 * scale), int(width - 46 * scale), int(170 * scale)),
        radius=int(24 * scale),
        fill=(0, 0, 0, 220),
        outline=highlight,
        width=max(1, int(3 * scale)),
    )
    draw.text((int(72 * scale), int(88 * scale)), "OFFICIAL SOURCE VISUAL", font=small_font, fill=highlight)
    draw.text((int(width - 205 * scale), int(88 * scale)), f"SCENE {index}", font=badge_font, fill=(255, 235, 210))

    caption = _wrap(draw, scene["caption"], title_font, int(width * 0.82), max_lines=3)
    bbox = draw.multiline_textbbox((0, 0), caption, font=title_font, spacing=int(10 * scale))
    caption_top = int(height * 0.49)
    draw.rounded_rectangle(
        (int(50 * scale), caption_top - int(36 * scale), int(width - 50 * scale), caption_top + (bbox[3] - bbox[1]) + int(50 * scale)),
        radius=int(30 * scale),
        fill=(0, 0, 0, 185),
    )
    draw.multiline_text(
        (int((width - (bbox[2] - bbox[0])) / 2), caption_top),
        caption,
        font=title_font,
        fill=(255, 240, 220),
        spacing=int(10 * scale),
        align="center",
        stroke_width=max(1, int(3 * scale)),
        stroke_fill=(0, 0, 0),
    )

    narration = _wrap(draw, scene["narration"], body_font, int(width * 0.82), max_lines=4)
    draw.rounded_rectangle(
        (int(58 * scale), int(height * 0.73), int(width - 58 * scale), int(height * 0.92)),
        radius=int(28 * scale),
        fill=(0, 0, 0, 225),
    )
    draw.multiline_text(
        (int(95 * scale), int(height * 0.765)),
        narration,
        font=body_font,
        fill=(255, 249, 235),
        spacing=int(8 * scale),
    )
    image.convert("RGB").save(out_path)


def _draw_card(scene, index, out_path, fast=False):
    size = FAST_CANVAS if fast else CANVAS
    width, height = size
    scale = width / 1080
    image = Image.new("RGB", size, (18, 12, 10))
    draw = ImageDraw.Draw(image)

    for y in range(height):
        red = int(24 + 65 * (y / height))
        draw.line((0, y, width, y), fill=(red, 15, 12))

    for i in range(9):
        x = int(width * (0.1 + i * 0.1))
        draw.line((x, 0, x - int(260 * scale), height), fill=(90, 22, 18), width=max(1, int(3 * scale)))

    # Abstract roadster silhouette.
    base_y = int(height * 0.36)
    car_color = (150, 22, 20)
    highlight = (236, 190, 145)
    draw.rounded_rectangle(
        (int(width * 0.16), base_y, int(width * 0.84), base_y + int(120 * scale)),
        radius=int(55 * scale),
        fill=car_color,
    )
    draw.polygon(
        [
            (int(width * 0.34), base_y),
            (int(width * 0.47), base_y - int(95 * scale)),
            (int(width * 0.63), base_y - int(90 * scale)),
            (int(width * 0.72), base_y),
        ],
        fill=(92, 18, 20),
    )
    for wheel_x in (int(width * 0.3), int(width * 0.7)):
        draw.ellipse(
            (wheel_x - int(58 * scale), base_y + int(76 * scale), wheel_x + int(58 * scale), base_y + int(192 * scale)),
            fill=(7, 7, 8),
            outline=highlight,
            width=max(1, int(6 * scale)),
        )

    title_font = _font(int(62 * scale))
    small_font = _font(int(30 * scale))
    body_font = _font(int(44 * scale))
    badge_font = _font(int(28 * scale))

    draw.rounded_rectangle(
        (int(58 * scale), int(70 * scale), int(width - 58 * scale), int(175 * scale)),
        radius=int(24 * scale),
        fill=(0, 0, 0),
        outline=highlight,
        width=max(1, int(3 * scale)),
    )
    draw.text((int(82 * scale), int(96 * scale)), "CAR SHORT DRY RUN", font=small_font, fill=highlight)
    draw.text((int(width - 210 * scale), int(96 * scale)), f"SCENE {index}", font=badge_font, fill=(255, 235, 210))

    caption = _wrap(draw, scene["caption"], title_font, int(width * 0.82), max_lines=3)
    bbox = draw.multiline_textbbox((0, 0), caption, font=title_font, spacing=int(10 * scale))
    draw.multiline_text(
        (int((width - (bbox[2] - bbox[0])) / 2), int(height * 0.57)),
        caption,
        font=title_font,
        fill=(255, 240, 220),
        spacing=int(10 * scale),
        align="center",
        stroke_width=max(1, int(3 * scale)),
        stroke_fill=(0, 0, 0),
    )

    narration = _wrap(draw, scene["narration"], body_font, int(width * 0.82), max_lines=4)
    draw.rounded_rectangle(
        (int(70 * scale), int(height * 0.73), int(width - 70 * scale), int(height * 0.91)),
        radius=int(28 * scale),
        fill=(0, 0, 0),
    )
    draw.multiline_text(
        (int(105 * scale), int(height * 0.765)),
        narration,
        font=body_font,
        fill=(255, 249, 235),
        spacing=int(8 * scale),
    )

    image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=130, threshold=3))
    image.save(out_path)


def _write_contact_sheet(image_paths, out_path):
    if not image_paths:
        return
    thumb_w, thumb_h = 216, 384
    gutter = 18
    width = (thumb_w * len(image_paths)) + (gutter * (len(image_paths) + 1))
    height = thumb_h + 72
    sheet = Image.new("RGB", (width, height), (18, 18, 22))
    draw = ImageDraw.Draw(sheet)
    label_font = _font(22)
    for index, path in enumerate(image_paths, start=1):
        image = Image.open(path).convert("RGB")
        image.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        x = gutter + (index - 1) * (thumb_w + gutter) + (thumb_w - image.width) // 2
        y = 42 + (thumb_h - image.height) // 2
        sheet.paste(image, (x, y))
        draw.text((gutter + (index - 1) * (thumb_w + gutter), 10), f"Scene {index}", font=label_font, fill=(245, 225, 205))
    sheet.save(out_path)


def _write_media_selection_report(storyboard, run_dir):
    rows = []
    edit_rows = []
    for index, scene in enumerate(storyboard.get("scenes", []), start=1):
        media = scene.get("selected_media") or {}
        ai = media.get("ai_review") or {}
        edit_style = scene.get("edit_style") or {}
        rows.append({
            "scene": index,
            "stage": scene.get("stage"),
            "wanted_tags": scene.get("media_tags", []),
            "selected_path": media.get("path"),
            "selected_labels": media.get("labels", []),
            "quality_flags": (media.get("quality") or {}).get("flags", []),
            "ai_provider": ai.get("provider"),
            "ai_reject": ai.get("reject"),
            "ai_reason": ai.get("reason"),
        })
        edit_rows.append({
            "scene": index,
            "stage": scene.get("stage"),
            "motion": edit_style.get("motion"),
            "layout": edit_style.get("layout"),
            "crop_focus": edit_style.get("crop_focus"),
            "cut_style": edit_style.get("cut_style"),
            "caption": scene.get("caption"),
            "stat": scene.get("stat"),
            "crop_audit": scene.get("crop_audit"),
        })
    (run_dir / "media_selection_report.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (run_dir / "edit_decision_report.json").write_text(json.dumps(edit_rows, indent=2), encoding="utf-8")


def _write_silent_wav(path, duration_seconds=24, sample_rate=44100):
    frame_count = int(duration_seconds * sample_rate)
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        chunk = b"\x00\x00" * sample_rate
        remaining = frame_count
        while remaining > 0:
            n = min(sample_rate, remaining)
            wav.writeframes(chunk[: n * 2])
            remaining -= n


def _write_tone_wav(path, duration_seconds=24, sample_rate=44100):
    # Last-resort audible placeholder so local renders are not silently confusing.
    frame_count = int(duration_seconds * sample_rate)
    amplitude = 6500
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for i in range(frame_count):
            frequency = 220 + (i // sample_rate % 4) * 55
            value = int(amplitude * math.sin(2 * math.pi * frequency * (i / sample_rate)))
            frames.extend(value.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))


def _write_gtts_audio(path, text):
    try:
        from gtts import gTTS
    except ModuleNotFoundError as exc:
        raise RuntimeError("gTTS is not installed. Run: pip install -r requirements.txt") from exc
    gTTS(text=text, lang="en", tld="com", slow=False).save(str(path))


CAR_TTS_INSTRUCTIONS = (
    "Use a high-energy car trailer narrator style. Big and punchy, but still clear. "
    "Sound like an original automotive Shorts narrator, not an imitation of any real actor, "
    "franchise character, or celebrity voice."
)


def _write_openai_audio(path, text):
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set for OpenAI TTS.")
    from openai import OpenAI

    client = OpenAI()
    response = client.audio.speech.create(
        model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        voice=os.getenv("OPENAI_TTS_VOICE", "onyx"),
        input=text,
        instructions=os.getenv("OPENAI_TTS_INSTRUCTIONS", CAR_TTS_INSTRUCTIONS),
        speed=float(os.getenv("OPENAI_TTS_SPEED", "1.0")),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    response.write_to_file(str(path))


def _write_narration_audio(run_dir, storyboard, duration_seconds, provider="gtts"):
    text = storyboard["narration"]
    provider = provider.lower()
    if provider == "openai":
        path = run_dir / "narration.mp3"
        _write_openai_audio(path, text)
        return path, "openai"
    if provider == "gtts":
        path = run_dir / "narration.mp3"
        _write_gtts_audio(path, text)
        return path, "gtts"
    if provider == "tone":
        path = run_dir / "tone_placeholder.wav"
        _write_tone_wav(path, duration_seconds=duration_seconds)
        return path, "tone_placeholder"
    path = run_dir / "silent_narration.wav"
    _write_silent_wav(path, duration_seconds=duration_seconds)
    return path, "silent"


def _subtitles(storyboard, duration_seconds):
    per_scene = duration_seconds / len(storyboard["scenes"])
    subtitles = []
    for index, scene in enumerate(storyboard["scenes"]):
        subtitles.append(
            {
                "start": round(index * per_scene, 3),
                "end": round((index + 1) * per_scene, 3),
                "text": scene["narration"],
            }
        )
    return subtitles


def generate_sample(
    output_root=OUTPUT_ROOT,
    slug=SAMPLE_SLUG,
    render_video=True,
    fast=True,
    tts_provider="gtts",
    source_topic=DEFAULT_SOURCE_TOPIC,
    require_real_media=False,
    plan_path=None,
):
    run_dir = Path(output_root) / slug
    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    short_plan = _load_short_plan(plan_path)
    if short_plan and short_plan.get("source_topic"):
        source_topic = short_plan["source_topic"]
    storyboard = _storyboard_from_plan(short_plan) if short_plan else dict(STORYBOARD)
    storyboard = _apply_edit_styles(storyboard)
    for i, scene in enumerate(storyboard.get("scenes", []), start=1):
        scene["scene_count"] = len(storyboard.get("scenes", []))
    storyboard["created_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    storyboard["run_slug"] = slug
    storyboard["source_packet_path"] = "source_packet.json"
    if plan_path:
        storyboard["short_plan_path"] = str(Path(plan_path).relative_to(ROOT) if Path(plan_path).is_absolute() and ROOT in Path(plan_path).parents else plan_path)

    (run_dir / "storyboard.json").write_text(json.dumps(storyboard, indent=2), encoding="utf-8")
    (run_dir / "source_packet.json").write_text(json.dumps(SOURCE_PACKET, indent=2), encoding="utf-8")
    (run_dir / "youtube_title.txt").write_text("Is the Miata 35th Anniversary actually special?", encoding="utf-8")
    (run_dir / "youtube_description.txt").write_text(
        "Facts verified from Mazda USA Newsroom and Car and Driver.\n"
        "Sources:\n"
        "- https://news.mazdausa.com/2025-01-24-Mazda-Announces-2025-MX-5-Miata-35th-Anniversary\n"
        "- https://news.mazdausa.com/35th-Anniversary-Edition-MX-5\n"
        "- https://www.caranddriver.com/mazda/mx-5-miata-2025\n",
        encoding="utf-8",
    )

    source_images = _candidate_source_images(source_topic=source_topic)
    source_screenshots = _candidate_source_screenshots(source_topic=source_topic)
    if require_real_media and not (source_images or source_screenshots):
        raise SystemExit(
            "No official images/screenshots found. Run `cd scraper/car-source-scraper && npm run scrape:miata-official` first, "
            "or omit --require-real-media to render generated fallback cards."
        )
    if source_images:
        storyboard["visual_source"] = "official_source_images"
    elif source_screenshots:
        storyboard["visual_source"] = "official_source_screenshots"
    else:
        storyboard["visual_source"] = "generated_fallback_cards"
    storyboard["source_images_used"] = [str(asset["path"].relative_to(ROOT)) for asset in source_images]
    storyboard["source_screenshots_used"] = [str(path.relative_to(ROOT)) for path in source_screenshots]
    (run_dir / "storyboard.json").write_text(json.dumps(storyboard, indent=2), encoding="utf-8")

    image_paths = []
    used_source_paths = set()
    for index, scene in enumerate(storyboard["scenes"], start=1):
        image_path = images_dir / f"scene_{index:02d}.png"
        if source_images:
            selected_asset = _planned_source_image(scene, source_images) or _select_source_image(scene, source_images, used_source_paths)
            used_source_paths.add(selected_asset["path"])
            scene["selected_media"] = {
                "path": str(selected_asset["path"].relative_to(ROOT)),
                "labels": selected_asset.get("labels", []),
                "source_url": selected_asset.get("source_url"),
                "quality": selected_asset.get("quality", {}),
                "ai_review": selected_asset.get("ai_review"),
                "planned_media": scene.get("planned_media"),
            }
            _draw_car_image_scene(
                scene,
                index,
                image_path,
                selected_asset["path"],
                fast=fast,
            )
        elif source_screenshots:
            _draw_source_screenshot_scene(
                scene,
                index,
                image_path,
                source_screenshots[(index - 1) % len(source_screenshots)],
                fast=fast,
            )
        else:
            _draw_card(scene, index, image_path, fast=fast)
        image_paths.append(image_path)
    _write_contact_sheet(image_paths, run_dir / "scene_contact_sheet.jpg")
    _write_media_selection_report(storyboard, run_dir)
    (run_dir / "storyboard.json").write_text(json.dumps(storyboard, indent=2), encoding="utf-8")

    duration_seconds = int(storyboard.get("target_seconds", 24))
    try:
        narration_path, audio_provider = _write_narration_audio(
            run_dir, storyboard, duration_seconds=duration_seconds, provider=tts_provider
        )
    except Exception as exc:
        if tts_provider.lower() in {"gtts", "openai"}:
            print(
                f"Voice provider {tts_provider!r} failed: {exc}. "
                "Falling back to audible tone placeholder.",
                file=sys.stderr,
            )
            narration_path, audio_provider = _write_narration_audio(
                run_dir, storyboard, duration_seconds=duration_seconds, provider="tone"
            )
        else:
            raise
    storyboard["audio_provider"] = audio_provider
    storyboard["narration_audio_path"] = narration_path.name
    (run_dir / "storyboard.json").write_text(json.dumps(storyboard, indent=2), encoding="utf-8")

    if render_video:
        build_short_video(
            storyboard,
            image_paths,
            narration_path,
            run_dir / "final_short.mp4",
            subtitles=_subtitles(storyboard, duration_seconds),
        )

    return run_dir


def main():
    parser = argparse.ArgumentParser(description="Generate a local dry-run car Short package/video.")
    parser.add_argument("--no-video", action="store_true", help="Only write storyboard/source/images/audio placeholders.")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--tts-provider", choices=["gtts", "openai", "tone", "silent"], default="gtts")
    parser.add_argument("--source-topic", default=DEFAULT_SOURCE_TOPIC)
    parser.add_argument("--plan", type=Path, default=None, help="Render from an AI/heuristic short-plan.json.")
    parser.add_argument(
        "--require-real-media",
        action="store_true",
        help="Fail unless official scraper images or screenshots exist.",
    )
    args = parser.parse_args()
    run_dir = generate_sample(
        output_root=args.output_root,
        render_video=not args.no_video,
        tts_provider=args.tts_provider,
        source_topic=args.source_topic,
        require_real_media=args.require_real_media,
        plan_path=args.plan,
    )
    print(run_dir)


if __name__ == "__main__":
    main()
