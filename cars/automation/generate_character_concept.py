"""One-off experiment, not part of the video render pipeline: generate a
candidate narrator-character illustration via OpenAI's image API instead of
hand-tuning narrator-rig.html's SVG path coordinates by eye -- repeated
manual attempts at an angular face/proportional body kept coming out wrong
(a pointed egg-shaped head, oversized balloon sleeves) without ever actually
looking at the render first. This just produces a PNG to react to; it does
not wire into narrator_video.py or narrator-rig.html on its own.

Deliberately has no imports from the rest of cars/automation (no
generate_sample/openai_retry) -- this needs to run standalone off whichever
ref it's dispatched against, including branches (like main) that predate
those helpers.
"""
import argparse
import base64
import io
import time
from pathlib import Path

from openai import OpenAI
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = ROOT / "narrator" / "concept-art"

DEFAULT_PROMPT = (
    "A simple, clean black-and-white line-art illustration of a young man, front-facing, from the "
    "waist up, for use as a talking narrator character in a car-review video. Angular, narrow face "
    "with defined cheekbones and a gently pointed (not rounded, not overly sharp) chin, short spiky "
    "hair, straight angled eyebrows, simple dot eyes, a small hook-shaped nose, a plain closed-mouth "
    "smile. Wearing a plain collared shirt. Proportional, natural human body -- normal-width "
    "shoulders and arms, not oversized balloon sleeves -- with visible hands showing five normal "
    "fingers each, not claws. Bold, even-weight outlines, flat white fill, no shading, no color, "
    "plain white background, flat vector-illustration style, symmetrical and centered in frame."
)


def _with_retry(call, max_retries=4, initial_delay=1.0, backoff=2.0):
    delay = initial_delay
    for attempt in range(max_retries + 1):
        try:
            return call()
        except Exception as exc:
            if attempt < max_retries and ("rate_limit" in str(exc) or "429" in str(exc)):
                time.sleep(delay)
                delay *= backoff
                continue
            raise


def generate(prompt, output_path, size="1024x1024"):
    client = OpenAI()
    response = _with_retry(lambda: client.images.generate(
        model="gpt-image-1", prompt=prompt, size=size, n=1,
    ))
    image_bytes = base64.b64decode(response.data[0].b64_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)


def edit(base_image_path, mask_box, prompt, output_path):
    """Surgically re-draw just one rectangular region of an existing
    generated image (e.g. the shirt/waist area) instead of a full
    from-scratch regeneration -- repeated full regenerations kept reverting
    to the same collared-shirt-plus-belt look regardless of the prompt
    wording, discarding the otherwise-good face/pose/legs each time. An
    edit call only touches the masked (transparent-alpha) region, so
    everything outside mask_box is guaranteed untouched."""
    base = Image.open(base_image_path).convert("RGBA")
    x, y, w, h = mask_box
    alpha = Image.new("L", base.size, 255)
    ImageDraw.Draw(alpha).rectangle([x, y, x + w, y + h], fill=0)
    base.putalpha(alpha)
    mask_buffer = io.BytesIO()
    base.save(mask_buffer, format="PNG")
    mask_buffer.seek(0)
    mask_buffer.name = "mask.png"

    image_buffer = io.BytesIO()
    Image.open(base_image_path).convert("RGBA").save(image_buffer, format="PNG")
    image_buffer.seek(0)
    image_buffer.name = "image.png"

    client = OpenAI()
    response = _with_retry(lambda: client.images.edit(
        model="gpt-image-1", image=image_buffer, mask=mask_buffer, prompt=prompt, n=1,
    ))
    image_bytes = base64.b64decode(response.data[0].b64_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--concept-id", required=True)
    # A full standing figure (head to feet) needs a taller canvas than
    # square, or the face -- the part that actually needs pixel precision
    # for the mouth/eye overlay -- comes back tiny relative to the frame.
    parser.add_argument("--size", default="1024x1024", choices=["1024x1024", "1024x1536", "1536x1024"])
    parser.add_argument("--edit-base", help="Path to an existing image to surgically edit instead of generating from scratch")
    parser.add_argument("--mask-box", help="x,y,w,h region (in the base image's own pixels) to redraw -- required with --edit-base")
    args = parser.parse_args()
    output_path = OUTPUT_ROOT / f"{args.concept_id}.png"
    if args.edit_base:
        mask_box = tuple(int(v) for v in args.mask_box.split(","))
        edit(args.edit_base, mask_box, args.prompt, output_path)
    else:
        generate(args.prompt, output_path, size=args.size)
    print(f"Saved {output_path}")


if __name__ == "__main__":
    main()
