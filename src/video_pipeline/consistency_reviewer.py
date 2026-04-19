import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ENABLE_CONSISTENCY_REVIEW = os.getenv("ENABLE_CONSISTENCY_REVIEW", "1") == "1"
CONSISTENCY_REVIEW_MODEL = os.getenv("CONSISTENCY_REVIEW_MODEL", "gpt-4.1-mini")
CONSISTENCY_MIN_SCORE = float(os.getenv("CONSISTENCY_MIN_SCORE", "7.8"))
CONSISTENCY_MAX_RETRIES = int(os.getenv("CONSISTENCY_MAX_RETRIES", "3"))


def resolve_reference_image_path():
    raw_path = os.getenv("LEO_REFERENCE_IMAGE_PATH", "").strip()
    if not raw_path:
        return None

    reference_path = Path(raw_path)
    if not reference_path.is_absolute():
        reference_path = ENV_PATH.parent / reference_path
    return reference_path


def _to_data_url(image_path):
    mime_type = "image/png"
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def review_character_consistency(reference_image_path, candidate_image_path, scene_stage):
    response = client.chat.completions.create(
        model=CONSISTENCY_REVIEW_MODEL,
        response_format={"type": "json_object"},
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "You evaluate whether two illustrations depict the same recurring character. "
                    "Ignore scene, pose, camera angle, lighting, expression intensity, and background story action. "
                    "Focus on identity continuity only: age, face shape, hair, eyebrows, nose, mouth, headset design, hoodie silhouette, and whether the character remains clearly human. "
                    "This series starts with Leo as a normal human kid, so prefer youthful, grounded, human-looking versions of him over creepy or corrupted versions. "
                    "Prioritize face-shape continuity very heavily: slim teenage oval face, soft jawline, thick dark eyebrows, calm almond-shaped eyes, small straight nose, thin neutral lips. "
                    "Fail older-looking, rugged, chiseled, gaunt, giant-eyed, or baby-faced drift even if the headset and hoodie match. "
                    "Fail immediately if the candidate does not clearly depict Leo at all, such as placeholder cards, text-only images, CTA slides, abstract shapes, or backgrounds with no visible character. "
                    "Fail the candidate if the identity drifts too far or if the character becomes ghostly, hollow-eyed, faceless, skull-like, or monster-like. "
                    "Return compact JSON with keys score, passed, and reasons. "
                    "score must be a number from 0 to 10."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Reference image: Last Player Leo's approved base identity.\n"
                            f"Candidate image: newly generated {scene_stage} scene.\n"
                            f"This reference image is the canonical early-season Leo look.\n"
                            f"Leo should remain a clearly human teenage boy.\n"
                            f"Leo should still look like a kid, not a ghost, monster, or haunted shell.\n"
                            f"Be strict about the face itself, not just the headset and hoodie.\n"
                            f"If the candidate is a placeholder, text card, or does not clearly show Leo, score it 0 and fail it.\n"
                            f"Pass only if the candidate still feels like the same kid from the reference image.\n"
                            f"Allow expression and angle changes, but reject major face drift."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _to_data_url(reference_image_path)},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _to_data_url(candidate_image_path)},
                    },
                ],
            },
        ],
    )

    payload = json.loads(response.choices[0].message.content)
    score = float(payload.get("score", 0))
    passed = bool(payload.get("passed", False)) and score >= CONSISTENCY_MIN_SCORE
    reasons = payload.get("reasons", [])
    if isinstance(reasons, str):
        reasons = [reasons]

    return {
        "score": score,
        "passed": passed,
        "reasons": reasons,
    }
