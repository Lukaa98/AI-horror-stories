import argparse
import json
import math
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video_pipeline.short_editor import build_short_video  # noqa: E402

OUTPUT_ROOT = SRC / "output" / "car_samples"
SAMPLE_SLUG = "mazda-mx5-miata-35th-anniversary"
CANVAS = (1080, 1920)
FAST_CANVAS = (540, 960)

SOURCE_PACKET = {
    "topic_signal": {
        "source_name": "Doug DeMuro",
        "source_url": "https://www.youtube.com/watch?v=eU14d5BVmjM",
        "role": "topic_signal_only",
        "title": "The Mazda Miata 35th Anniversary Is as Good as Ever",
        "observed_at": "2026-07-07T00:00:00Z",
        "notes": "Used only to identify the car/topic people may search for; facts below come from independent sources.",
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
    "media_policy": "Sample uses generated text/shape cards only. Replace with allowed official screenshots/images after source rights are checked.",
}

STORYBOARD = {
    "title": "Should You Care About the Miata 35th Anniversary?",
    "hook": "Doug just reviewed the Miata 35th Anniversary, but here is the real question: is this spec actually special?",
    "narration": "",
    "visual_identity": "Clean automotive short with deep red Mazda-inspired color palette, tan interior accents, source cards, no creator footage.",
    "music_mood": "upbeat car-news pulse, clean and premium",
    "cta": "Would you pay collector money for this Miata, or just buy the regular one?",
    "target_seconds": 24,
    "scene_count": 7,
    "story_provider": "local-car-template",
    "character_name": "Mazda MX-5 Miata 35th Anniversary",
    "theme": "Doug topic signal: Mazda MX-5 Miata 35th Anniversary",
    "scenes": [
        {
            "stage": "hook",
            "narration": "Doug just put the Miata 35th Anniversary back in the spotlight.",
            "caption": "MIATA IS TRENDING",
            "image_prompt": "deep red roadster silhouette, search trend card, no creator footage",
        },
        {
            "stage": "setup",
            "narration": "But the real story is not the review. It is what Mazda actually built.",
            "caption": "NOT THE REVIEW — THE CAR",
            "image_prompt": "official-source style card showing Mazda newsroom and fact-check icons",
        },
        {
            "stage": "setup",
            "narration": "Mazda says only three hundred are coming to the U.S., all in Artisan Red with a tan cabin.",
            "caption": "300 FOR THE U.S.",
            "image_prompt": "limited edition badge, red paint swatch, tan leather texture card",
        },
        {
            "stage": "escalation",
            "narration": "The price starts at thirty six thousand two fifty before destination and fees.",
            "caption": "$36,250 MSRP",
            "image_prompt": "price card comparing limited edition MSRP to regular Miata trims",
        },
        {
            "stage": "escalation",
            "narration": "You get Nappa leather, serialized badging, special wheels, and a matching key sleeve.",
            "caption": "SPECIAL, BUT SUBTLE",
            "image_prompt": "premium detail collage, badge, wheel, key sleeve, tan leather, abstract generated",
        },
        {
            "stage": "payoff",
            "narration": "So this is not a faster Miata. It is a scarcity-and-spec Miata.",
            "caption": "NOT FASTER. RARER.",
            "image_prompt": "split card: performance meter unchanged, rarity meter rising",
        },
        {
            "stage": "cta",
            "narration": "Would you pay collector money for this one, or just buy the regular Miata?",
            "caption": "WOULD YOU BUY IT?",
            "image_prompt": "comment prompt card with red roadster silhouette and question mark",
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


def generate_sample(output_root=OUTPUT_ROOT, slug=SAMPLE_SLUG, render_video=True, fast=True):
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
        "Topic signal: Doug DeMuro's Miata 35th Anniversary upload.\n"
        "Facts verified from Mazda USA Newsroom and Car and Driver.\n"
        "Sources:\n"
        "- https://news.mazdausa.com/2025-01-24-Mazda-Announces-2025-MX-5-Miata-35th-Anniversary\n"
        "- https://news.mazdausa.com/35th-Anniversary-Edition-MX-5\n"
        "- https://www.caranddriver.com/mazda/mx-5-miata-2025\n",
        encoding="utf-8",
    )

    image_paths = []
    for index, scene in enumerate(storyboard["scenes"], start=1):
        image_path = images_dir / f"scene_{index:02d}.png"
        _draw_card(scene, index, image_path, fast=fast)
        image_paths.append(image_path)

    narration_path = run_dir / "silent_narration.wav"
    duration_seconds = int(storyboard.get("target_seconds", 24))
    _write_silent_wav(narration_path, duration_seconds=duration_seconds)

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
    args = parser.parse_args()
    run_dir = generate_sample(output_root=args.output_root, render_video=not args.no_video)
    print(run_dir)


if __name__ == "__main__":
    main()
