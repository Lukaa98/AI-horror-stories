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
import time
from pathlib import Path

from openai import OpenAI

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


def generate(prompt, output_path):
    client = OpenAI()
    response = _with_retry(lambda: client.images.generate(
        model="gpt-image-1", prompt=prompt, size="1024x1024", n=1,
    ))
    image_bytes = base64.b64decode(response.data[0].b64_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--concept-id", required=True)
    args = parser.parse_args()
    output_path = OUTPUT_ROOT / f"{args.concept_id}.png"
    generate(args.prompt, output_path)
    print(f"Saved {output_path}")


if __name__ == "__main__":
    main()
