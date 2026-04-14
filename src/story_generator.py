import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI

ENV_PATH = Path(__file__).resolve().with_name(".env")
load_dotenv(ENV_PATH)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CHAR = os.getenv("CHARACTER_NAME", "Pikachu")
THEME = os.getenv("THEME", "creepy forest mystery")
TARGET_SECONDS = int(os.getenv("SHORT_TARGET_SECONDS", "30"))
NUM_SCENES = int(os.getenv("SHORT_NUM_SCENES", "7"))
STORY_TONE = os.getenv("STORY_TONE", "suspenseful, emotional, highly visual")
OPENAI_STORY_MODEL = os.getenv("OPENAI_STORY_MODEL", "gpt-4o-mini")
USE_GEMINI_TEXT = os.getenv("USE_GEMINI_TEXT", "0") == "1"


def _slugify(text):
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned or "short"


def _build_prompt():
    return f"""
Create a YouTube Shorts storyboard for a vertical AI video.

Main character or subject: {CHAR}
Theme: {THEME}
Target runtime: about {TARGET_SECONDS} seconds
Number of scenes: exactly {NUM_SCENES}
Tone: {STORY_TONE}

The short must follow this pacing:
- 0-3s: hook
- 3-10s: setup
- 10-22s: escalation
- 22-28s: payoff or twist
- 28-30s: ending line or CTA

Requirements:
- Keep the narration punchy and easy to follow out loud.
- Write for retention: strong opening, escalating stakes, memorable twist.
- Keep it PG-13 and non-graphic.
- Avoid copyrighted franchise names, logos, and exact branded character descriptions.
- Prefer original character phrasing that feels familiar but not directly borrowed from existing IP.
- Every scene needs one narration line, one short on-screen caption, and one cinematic image prompt.
- Reuse one consistent visual identity string for the subject so image generations stay coherent.
- The image prompts should describe a single strong frame, not camera instructions for a video model.
- Keep captions short enough to read on a phone.
- Return JSON only.
"""


def _response_schema():
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "hook": {"type": "string"},
            "narration": {"type": "string"},
            "visual_identity": {"type": "string"},
            "music_mood": {"type": "string"},
            "cta": {"type": "string"},
            "scenes": {
                "type": "array",
                "minItems": NUM_SCENES,
                "maxItems": NUM_SCENES,
                "items": {
                    "type": "object",
                    "properties": {
                        "stage": {
                            "type": "string",
                            "enum": ["hook", "setup", "escalation", "payoff", "cta"],
                        },
                        "narration": {"type": "string"},
                        "caption": {"type": "string"},
                        "image_prompt": {"type": "string"},
                    },
                    "required": ["stage", "narration", "caption", "image_prompt"],
                },
            },
        },
        "required": [
            "title",
            "hook",
            "narration",
            "visual_identity",
            "music_mood",
            "cta",
            "scenes",
        ],
    }


def _validate_storyboard(storyboard):
    required_keys = {
        "title",
        "hook",
        "narration",
        "visual_identity",
        "music_mood",
        "cta",
        "scenes",
    }
    missing = required_keys - set(storyboard.keys())
    if missing:
        raise ValueError(f"Storyboard missing keys: {sorted(missing)}")

    if len(storyboard["scenes"]) != NUM_SCENES:
        raise ValueError(f"Expected {NUM_SCENES} scenes, got {len(storyboard['scenes'])}")

    for index, scene in enumerate(storyboard["scenes"], start=1):
        for field in ("stage", "narration", "caption", "image_prompt"):
            if not scene.get(field):
                raise ValueError(f"Scene {index} missing {field}")
    return storyboard


def _finalize_storyboard(storyboard, provider):
    storyboard = _validate_storyboard(storyboard)
    storyboard["character_name"] = CHAR
    storyboard["theme"] = THEME
    storyboard["target_seconds"] = TARGET_SECONDS
    storyboard["scene_count"] = len(storyboard["scenes"])
    storyboard["created_at"] = datetime.now().isoformat(timespec="seconds")
    storyboard["run_slug"] = _slugify(storyboard["title"])
    storyboard["story_provider"] = provider
    return storyboard


def _generate_storyboard_with_gemini():
    response = client.models.generate_content(
        model=os.getenv("STORY_MODEL", "gemini-2.0-flash"),
        contents=_build_prompt(),
        config=types.GenerateContentConfig(
            temperature=0.9,
            response_mime_type="application/json",
            response_json_schema=_response_schema(),
        ),
    )
    return json.loads(response.text)


def _generate_storyboard_with_openai():
    response = openai_client.chat.completions.create(
        model=OPENAI_STORY_MODEL,
        temperature=0.9,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You write high-retention vertical short-video storyboards. "
                    "Return only valid JSON. "
                    "The JSON must contain title, hook, narration, visual_identity, music_mood, cta, and scenes. "
                    f"Scenes must contain exactly {NUM_SCENES} items. "
                    "Each scene must include stage, narration, caption, and image_prompt."
                ),
            },
            {"role": "user", "content": _build_prompt()},
        ],
    )
    content = response.choices[0].message.content
    return json.loads(content)


def _generate_local_storyboard():
    title = f"The Night {CHAR} Heard Its Name"
    scenes = [
        {
            "stage": "hook",
            "narration": f"{CHAR} heard someone whisper from the trees, even though nobody was there.",
            "caption": "HE HEARD A VOICE",
            "image_prompt": f"{CHAR} frozen in a moonlit forest clearing as a distant shadow watches from the trees",
        },
        {
            "stage": "setup",
            "narration": "The path home was empty, but the footprints ahead were still appearing one by one.",
            "caption": "FOOTSTEPS WITH NO ONE",
            "image_prompt": "fresh footprints forming by themselves on a dark forest path under cold blue moonlight",
        },
        {
            "stage": "setup",
            "narration": f"When {CHAR} ran, the whisper changed into a laugh behind his shoulder.",
            "caption": "THEN IT LAUGHED",
            "image_prompt": f"{CHAR} sprinting through tangled woods while a blurred grin glows behind him",
        },
        {
            "stage": "escalation",
            "narration": "The cabin door slammed shut before he even touched it.",
            "caption": "THE DOOR MOVED FIRST",
            "image_prompt": "an old wooden cabin door snapping shut on its own in a stormy forest night",
        },
        {
            "stage": "escalation",
            "narration": f"Inside, muddy prints crossed the floor and stopped inches from {CHAR}.",
            "caption": "IT WAS ALREADY INSIDE",
            "image_prompt": f"muddy footprints stopping directly in front of {CHAR} inside a dim cabin lit by a single lantern",
        },
        {
            "stage": "payoff",
            "narration": "Then the whisper came from under the bed and said his name perfectly.",
            "caption": "IT KNEW HIS NAME",
            "image_prompt": "a dark shape under a bed in a cabin, lantern light revealing a grin in the darkness",
        },
        {
            "stage": "cta",
            "narration": "If you heard that voice too, would you look under the bed or run?",
            "caption": "LOOK OR RUN?",
            "image_prompt": "a final tense horror frame inside a cabin with the bed in the foreground and a doorway to the night behind",
        },
    ]
    narration = " ".join(scene["narration"] for scene in scenes)
    return {
        "title": title,
        "hook": scenes[0]["narration"],
        "narration": narration,
        "visual_identity": (
            f"{CHAR} rendered as a cinematic, expressive, high-detail character with consistent silhouette, "
            "face shape, color palette, and proportions across every scene"
        ),
        "music_mood": "dark pulse, eerie atmosphere, rising suspense",
        "cta": scenes[-1]["narration"],
        "scenes": scenes,
    }


def generate_storyboard():
    errors = []

    if USE_GEMINI_TEXT:
        try:
            print("Trying Gemini for storyboard generation...")
            return _finalize_storyboard(_generate_storyboard_with_gemini(), "gemini")
        except Exception as exc:
            errors.append(f"Gemini failed: {exc}")
            print(f"Gemini storyboard failed: {exc}")

    try:
        print("Falling back to OpenAI for storyboard generation...")
        return _finalize_storyboard(_generate_storyboard_with_openai(), "openai")
    except Exception as exc:
        errors.append(f"OpenAI failed: {exc}")
        print(f"OpenAI storyboard failed: {exc}")

    print("Falling back to a local storyboard template so the pipeline can still run.")
    storyboard = _finalize_storyboard(_generate_local_storyboard(), "local-template")
    storyboard["generation_errors"] = errors
    return storyboard


def save_storyboard(storyboard, output_root):
    output_root = Path(output_root)
    existing_indexes = []
    for child in output_root.iterdir():
        if child.is_dir() and child.name.startswith("video_"):
            suffix = child.name.replace("video_", "", 1)
            if suffix.isdigit():
                existing_indexes.append(int(suffix))

    next_index = max(existing_indexes, default=0) + 1
    run_dir = output_root / f"video_{next_index:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    storyboard_path = run_dir / "storyboard.json"
    with open(storyboard_path, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, indent=2, ensure_ascii=False)

    return run_dir
