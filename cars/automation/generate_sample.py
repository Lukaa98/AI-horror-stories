import argparse
import json
import math
import os
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ModuleNotFoundError as exc:
    if exc.name == "PIL":
        raise SystemExit(
            "Missing Pillow dependency. From the repo root, run: pip install -r requirements.txt"
        ) from exc
    raise

ROOT = Path(__file__).resolve().parents[2]
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
    "title": "Is the Miata 35th Anniversary Actually Special?",
    "hook": "The 2025 Mazda MX-5 Miata 35th Anniversary looks subtle, but the details matter.",
    "narration": "",
    "visual_identity": "Clean automotive short with deep red Mazda-inspired color palette, tan interior accents, source cards, no creator footage.",
    "music_mood": "upbeat car-news pulse, clean and premium",
    "cta": "Would you pay collector money for this Miata, or just buy the regular one?",
    "target_seconds": 24,
    "scene_count": 7,
    "story_provider": "local-car-template",
    "character_name": "Mazda MX-5 Miata 35th Anniversary",
    "theme": "Mazda MX-5 Miata 35th Anniversary official-source sample",
    "scenes": [
        {
            "stage": "hook",
            "narration": "The Miata 35th Anniversary is a collector-spec version of Mazda’s tiny roadster.",
            "caption": "LIMITED MIATA",
            "stat": "2025 MX-5 35TH",
            "image_prompt": "deep red roadster silhouette, search trend card, no creator footage",
            "media_tags": ["exterior", "hero"],
        },
        {
            "stage": "setup",
            "narration": "The point is not extra power. It is color, scarcity, and the exact spec Mazda chose.",
            "caption": "THE SPEC IS THE STORY",
            "stat": "ARTISAN RED + TAN",
            "image_prompt": "official-source style card showing Mazda newsroom and fact-check icons",
            "media_tags": ["exterior", "gallery"],
        },
        {
            "stage": "setup",
            "narration": "Mazda says only three hundred are coming to the U.S., all in Artisan Red with a tan cabin.",
            "caption": "300 FOR THE U.S.",
            "stat": "300 UNITS",
            "image_prompt": "limited edition badge, red paint swatch, tan leather texture card",
            "media_tags": ["interior", "exterior"],
        },
        {
            "stage": "escalation",
            "narration": "The price starts at thirty six thousand two fifty before destination and fees.",
            "caption": "$36,250 MSRP",
            "stat": "BEFORE DESTINATION",
            "image_prompt": "price card comparing limited edition MSRP to regular Miata trims",
            "media_tags": ["exterior", "price"],
        },
        {
            "stage": "escalation",
            "narration": "Inside, it gets tan Nappa leather, serialized badging, special wheels, and a matching key sleeve.",
            "caption": "TAN NAPPA INTERIOR",
            "stat": "Nappa + serialized badge",
            "image_prompt": "premium detail collage, badge, wheel, key sleeve, tan leather, abstract generated",
            "media_tags": ["interior", "wheels"],
        },
        {
            "stage": "payoff",
            "narration": "The engine is the familiar one hundred eighty one horsepower Miata formula, not a power bump.",
            "caption": "181 HP ROADSTER",
            "stat": "151 LB-FT",
            "image_prompt": "split card: performance meter unchanged, rarity meter rising",
            "media_tags": ["performance", "interior"],
        },
        {
            "stage": "cta",
            "narration": "Would you chase this limited spec, or buy a regular Miata and drive it every weekend?",
            "caption": "WOULD YOU BUY IT?",
            "stat": "collector spec or driver?",
            "image_prompt": "comment prompt card with red roadster silhouette and question mark",
            "media_tags": ["convertible_roof", "exterior"],
        },
    ],
}
STORYBOARD["narration"] = " ".join(scene["narration"] for scene in STORYBOARD["scenes"])


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


def _candidate_source_images(source_topic=DEFAULT_SOURCE_TOPIC):
    source_root = _source_root(source_topic)
    images_dir = source_root / "images"
    packet = _load_scraped_source_packet(source_topic) or {}
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
            deduped.append(asset)
    return deduped


def _labels_from_path(path):
    text = str(path).lower()
    labels = []
    for label, words in {
        "interior": ["interior", "cabin", "seat", "leather", "nappa", "dashboard", "gauge"],
        "exterior": ["exterior", "hero", "360", "soulred", "soul-red", "roadster"],
        "wheels": ["wheel", "rim", "alloy"],
        "convertible_roof": ["convertible", "soft-top", "hard-top", "roof", "rf"],
        "performance": ["engine", "tach", "gauge", "instrument", "performance"],
        "price": ["price", "msrp"],
    }.items():
        if any(word in text for word in words):
            labels.append(label)
    return labels or ["general"]


def _select_source_image(scene, assets, used_paths):
    if not assets:
        return None
    desired = set(scene.get("media_tags", []))
    ranked = []
    for asset in assets:
        labels = set(asset.get("labels") or [])
        match_score = len(desired & labels) * 100
        reuse_penalty = 25 if asset["path"] in used_paths else 0
        ranked.append((match_score + asset.get("score", 0) - reuse_penalty, asset))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = ranked[0][1]
    used_paths.add(selected["path"])
    return selected


def _draw_car_image_scene(scene, index, out_path, source_image_path, fast=False):
    size = FAST_CANVAS if fast else CANVAS
    width, height = size
    scale = width / 1080
    base = Image.open(source_image_path).convert("RGB")
    image = _cover_crop(base, size)
    shade = Image.new("RGBA", size, (0, 0, 0, 70))
    image = Image.alpha_composite(image.convert("RGBA"), shade)
    draw = ImageDraw.Draw(image)

    title_font = _font(int(58 * scale))
    body_font = _font(int(42 * scale))
    small_font = _font(int(30 * scale))
    highlight = (236, 190, 145)

    draw.rounded_rectangle(
        (int(46 * scale), int(55 * scale), int(width - 46 * scale), int(152 * scale)),
        radius=int(24 * scale),
        fill=(0, 0, 0, 210),
        outline=highlight,
        width=max(1, int(3 * scale)),
    )
    draw.text((int(72 * scale), int(83 * scale)), "MAZDA MX-5 MIATA", font=small_font, fill=highlight)
    draw.text((int(width - 190 * scale), int(83 * scale)), f"SCENE {index}", font=small_font, fill=(255, 235, 210))

    caption = _wrap(draw, scene["caption"], title_font, int(width * 0.82), max_lines=2)
    caption_bbox = draw.multiline_textbbox((0, 0), caption, font=title_font, spacing=int(10 * scale))
    caption_top = int(height * 0.50)
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
        stat_y = int(height * 0.64)
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
        (int(58 * scale), int(height * 0.74), int(width - 58 * scale), int(height * 0.92)),
        radius=int(28 * scale),
        fill=(0, 0, 0, 220),
    )
    draw.multiline_text(
        (int(95 * scale), int(height * 0.772)),
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


def _write_openai_audio(path, text):
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set for OpenAI TTS.")
    from openai import OpenAI

    client = OpenAI()
    response = client.audio.speech.create(
        model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
        input=text,
    )
    response.write_to_file(path)


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
):
    run_dir = Path(output_root) / slug
    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    storyboard = dict(STORYBOARD)
    storyboard["created_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    storyboard["run_slug"] = slug
    storyboard["source_packet_path"] = "source_packet.json"

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
            selected_asset = _select_source_image(scene, source_images, used_source_paths)
            scene["selected_media"] = {
                "path": str(selected_asset["path"].relative_to(ROOT)),
                "labels": selected_asset.get("labels", []),
                "source_url": selected_asset.get("source_url"),
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
    )
    print(run_dir)


if __name__ == "__main__":
    main()
