"""Standalone preview: composite this project's narrator character with a
real car's exterior photo into a few still images -- no audio, no video,
just checking how the narrator+car pairing actually looks for a specific
car before wiring up the full talking-video pipeline (narrator_script.py /
narrator_video.py) for it.

Mirrors video_probe.py's CLI shape (make/model/query/start-year/end-year)
so it slots into the same "lightweight standalone test" family in the
Create UI and cars-research.yml, rather than introducing new input names.
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from battle_request import gather_exterior_photos
from generate_sample import ROOT, CANVAS, _font, _wrap

OUTPUT_ROOT = ROOT / "cars" / "narrator-previews"
SPRITES_DIR = ROOT / "narrator" / "sprites"
MAX_CAR_RATIO = 0.42
# The three mouth shapes worth previewing -- not a talk cycle, just enough
# to see the character next to the car in a resting, a mid-talk, and a
# wide-open pose.
PREVIEW_MOUTHS = ["closed", "wide", "smile"]


def _fit_to_box(image, box_size):
    box_w, box_h = box_size
    scale = min(box_w / image.width, box_h / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.LANCZOS)


def _draw_caption(canvas, text, center_y):
    width = canvas.width
    scale = width / 1080
    draw = ImageDraw.Draw(canvas)
    font = _font(int(64 * scale))
    wrapped = _wrap(draw, text.upper(), font, int(width * 0.85), max_lines=2)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=int(10 * scale))
    draw.multiline_text(
        (width / 2 - (bbox[2] - bbox[0]) / 2, center_y - (bbox[3] - bbox[1]) / 2),
        wrapped, font=font, fill=(255, 214, 64), spacing=int(10 * scale),
        align="center", stroke_width=max(2, int(3 * scale)), stroke_fill=(0, 0, 0),
    )


def compose_preview(car_photo_path, sprite_path, label, out_path):
    size = CANVAS
    width, height = size
    max_car_h = int(height * MAX_CAR_RATIO)

    canvas = Image.new("RGB", size, (255, 255, 255))

    car_image = Image.open(car_photo_path).convert("RGB")
    fitted_car = _fit_to_box(car_image, (width, max_car_h))
    canvas.paste(fitted_car, ((width - fitted_car.width) // 2, (max_car_h - fitted_car.height) // 2))

    sprite = Image.open(sprite_path).convert("RGBA")
    fitted_sprite = _fit_to_box(sprite, (width, height - max_car_h))
    sprite_x = (width - fitted_sprite.width) // 2
    sprite_y = height - fitted_sprite.height
    canvas.paste(fitted_sprite, (sprite_x, sprite_y), fitted_sprite)

    _draw_caption(canvas, label, max_car_h - 90)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def main():
    parser = argparse.ArgumentParser(
        description="Preview the narrator character next to a real car's photo -- stills only, no audio/video."
    )
    parser.add_argument("--make", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--query", default="")
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--test-id", required=True)
    args = parser.parse_args()

    if not SPRITES_DIR.exists():
        raise SystemExit(
            f"No committed narrator sprites at {SPRITES_DIR} -- run "
            "`cd narrator/render && node export-sprites.js` and copy sprites/*.png there first."
        )

    output_dir = OUTPUT_ROOT / args.test_id
    images_dir = output_dir / "images"

    query = args.query.strip() or f"{args.make} {args.model}"
    years = "-".join(str(value) for value in (args.start_year, args.end_year) if value)
    variant = {
        "name": query,
        "label": query,
        "years": years or "any year",
        "search_hint": f"{args.make} {args.model}",
        "visual_highlight": "",
        "commons_search_terms": [],
    }

    photos = gather_exterior_photos(variant, images_dir)
    label = f"{args.start_year or ''} {args.make} {args.model}".strip()

    previews = []
    if photos:
        car_photo_path = output_dir / photos[0]
        for mouth in PREVIEW_MOUTHS:
            sprite_path = SPRITES_DIR / f"mouth-{mouth}_eyes-open.png"
            out_path = output_dir / "previews" / f"preview-{mouth}.png"
            compose_preview(car_photo_path, sprite_path, label, out_path)
            previews.append(str(out_path.relative_to(output_dir)).replace("\\", "/"))

    result = {
        "test_id": args.test_id,
        "make": args.make,
        "model": args.model,
        "query": query,
        "start_year": args.start_year,
        "end_year": args.end_year,
        "label": label,
        "photos_found": len(photos),
        "photos": photos,
        "previews": previews,
        "status": "complete" if previews else "no_photos_found",
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
