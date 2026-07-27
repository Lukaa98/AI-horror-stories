"""Detect and blur visible license plates in car photos before publishing.

Listing photos are real cars with real, current plates, so a rendered
video shouldn't broadcast someone's actual license number. This is a
best-effort privacy pass, not a certified redaction tool -- it fails open
(leaves the original image untouched) whenever detection is unavailable
or errors, since a missed plate on an unlikely-to-be-legible auction photo
is a smaller problem than blocking the whole render.
"""
import base64
import json
import os
from pathlib import Path

from PIL import Image, ImageFilter

_PLATE_PROMPT = (
    "Look at this car photo for any visible license plate (front or rear, "
    "straight-on or angled, fully or partially in frame). Return only JSON "
    "with keys: has_plate boolean, boxes (array of [x_min, y_min, x_max, y_max], "
    "each a plate's bounding box as fractions from 0 to 1 of the image width and "
    "height; empty array if none). Include a plate even if small, angled, or "
    "partially cropped. Do not include manufacturer badges, grille text, or "
    "other non-plate signage."
)


def _detect_plate_boxes(image_path):
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key.startswith("sk-") or len(key) < 30:
        return []
    from openai import OpenAI

    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    response = OpenAI().responses.create(
        model=os.getenv("OPENAI_CAR_VIDEO_REVIEW_MODEL", "gpt-4o-mini"),
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": _PLATE_PROMPT},
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}"},
        ]}],
    )
    text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
    result = json.loads(text)
    if not result.get("has_plate"):
        return []
    return result.get("boxes") or []


def blur_license_plates(image_path):
    """Detect and Gaussian-blur any visible license plates in place.

    Returns True if the image was modified, False otherwise (including on
    any detection failure, so callers can treat this as fire-and-forget).
    """
    try:
        boxes = _detect_plate_boxes(image_path)
    except Exception:
        return False
    if not boxes:
        return False

    try:
        image = Image.open(image_path).convert("RGB")
    except Exception:
        return False
    width, height = image.size
    blurred_any = False
    for box in boxes:
        try:
            x_min, y_min, x_max, y_max = (float(value) for value in box)
        except (TypeError, ValueError):
            continue
        # A few pixels of padding around the model's box so a slightly tight
        # box doesn't leave a sliver of readable plate at the edge.
        left = max(0, int(x_min * width) - 4)
        top = max(0, int(y_min * height) - 4)
        right = min(width, int(x_max * width) + 4)
        bottom = min(height, int(y_max * height) + 4)
        if right <= left or bottom <= top:
            continue
        region = image.crop((left, top, right, bottom))
        region = region.filter(ImageFilter.GaussianBlur(radius=max(6, (right - left) // 6)))
        image.paste(region, (left, top))
        blurred_any = True

    if blurred_any:
        image.save(image_path, quality=92)
    return blurred_any
