import os
from base64 import b64decode
from pathlib import Path
import re

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "imagen-4.0-generate-001")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_IMAGE_SIZE = os.getenv("OPENAI_IMAGE_SIZE", "1024x1536")
USE_GEMINI_IMAGES = os.getenv("USE_GEMINI_IMAGES", "0") == "1"
FAST_MODE = os.getenv("FAST_MODE", "1") == "1"
CHARACTER_BIBLE = os.getenv("CHARACTER_BIBLE", "")
STYLE_BIBLE = os.getenv("STYLE_BIBLE", "")
NEGATIVE_PROMPT = os.getenv(
    "IMAGE_NEGATIVE_PROMPT",
    (
        "low detail, blurry, extra limbs, duplicate subjects, deformed anatomy, "
        "cropped face, text artifacts, watermark, logo, oversaturated, flat lighting"
    ),
)
FONT_PATH = "C:/Windows/Fonts/arialbd.ttf"


def _safe_visual_subject(name):
    lowered = name.strip().lower()
    aliases = {
        "spiderman": "an original masked red-and-blue city vigilante with agile athletic movement and web-like gear",
        "spider-man": "an original masked red-and-blue city vigilante with agile athletic movement and web-like gear",
        "pikachu": "an original small yellow electric creature mascot with bright cheeks and a playful silhouette",
        "batman": "an original dark caped night detective with a dramatic silhouette and tactical suit",
        "mario": "an original cheerful mustached platform hero in a red cap and blue overalls",
        "kirby": "an original round pink star-powered creature mascot",
        "godzilla": "an original towering reptilian titan with massive scale and dorsal spines",
        "last player leo": "an original human teenage gamer with short dark brown hair, natural brown eyes, a worn charcoal hoodie, and a glowing amber-ring headset, lit by blue screenlight in a lonely late-night multiplayer world",
    }
    return aliases.get(lowered, name)


def _sanitize_visual_text(text):
    replacements = {
        r"\bspiderman\b": "masked city vigilante",
        r"\bspider-man\b": "masked city vigilante",
        r"\bpikachu\b": "small electric creature mascot",
        r"\bbatman\b": "dark caped detective",
        r"\bmario\b": "platform hero",
        r"\bkirby\b": "round pink mascot creature",
        r"\bgodzilla\b": "towering reptilian titan",
        r"\bthreat\b": "mystery",
        r"\blurks\b": "waits",
        r"\bwhisper\b": "faint voice",
        r"\blaugh\b": "strange sound",
        r"\bshadow watches\b": "figure stands in the distance",
        r"\bdark shape\b": "hidden silhouette",
        r"\bgrin\b": "expression",
        r"\bstormy\b": "dramatic",
        r"\bunder the bed\b": "near the bed",
        r"\bfootprints\b": "mysterious tracks",
        r"\bmuddy\b": "dimly visible",
        r"\bcreepy\b": "mysterious",
        r"\bhorror\b": "cinematic suspense",
        r"\bterror\b": "suspense",
        r"\bviolent\b": "intense",
    }
    cleaned = text
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def _stage_visual_direction(stage):
    directions = {
        "hook": "an attention-grabbing opening frame with mystery and strong silhouette",
        "setup": "a clear story setup frame with environment detail and character focus",
        "escalation": "a dynamic, tense but non-graphic action frame",
        "payoff": "a dramatic reveal frame with cinematic energy and emotional impact",
        "cta": "a final cliffhanger frame that invites curiosity and suspense",
    }
    return directions.get(stage, "a cinematic story frame")


def _human_face_guardrails():
    return (
        "Leo must remain a clearly human teenage boy with natural human eyes, visible eyebrows, a readable nose and mouth, and grounded facial anatomy. "
        "Do not make Leo faceless, skull-like, undead, masked, hollow-eyed, alien, or ghostly. "
        "Do not give Leo glowing eyes, blank white eyes, black void eyes, or a shadow-only face. "
        "If supernatural elements appear, keep them in the background, reflections, screens, or separate ghost figures while Leo stays human in the foreground."
    )


def _build_scene_prompt(storyboard, scene):
    visual_subject = _safe_visual_subject(storyboard["character_name"])
    safe_theme = _sanitize_visual_text(storyboard["theme"])
    safe_identity = _sanitize_visual_text(storyboard["visual_identity"])
    safe_brief = _sanitize_visual_text(scene["image_prompt"])
    stage_direction = _stage_visual_direction(scene["stage"])

    return (
        "Create a polished illustrated cinematic keyframe for a vertical YouTube Short. "
        f"Main subject: {visual_subject}. "
        f"Theme: {safe_theme}. "
        f"Locked character bible: {_sanitize_visual_text(CHARACTER_BIBLE)}. "
        f"Locked style bible: {_sanitize_visual_text(STYLE_BIBLE)}. "
        f"Consistent visual identity: {safe_identity}. "
        f"Scene stage: {scene['stage']}. "
        f"Stage direction: {stage_direction}. "
        f"Image brief: {safe_brief}. "
        f"Human-face guardrails: {_human_face_guardrails()} "
        "Keep it PG-13, non-graphic, family-friendly, adventurous, mysterious, and stylized. "
        "Comic-inspired digital illustration, not a real person, not a real celebrity, not an existing franchise character. "
        "Keep the same face shape, same headset design, same hoodie, same age, same brown eyes, and same core palette in every scene. "
        "Vertical composition, strong focal subject, clean readable shapes, dramatic lighting, rich atmosphere, "
        "believable depth, no text in the image, no logos, no watermarks. "
        "Avoid ghost-Leo, monster-Leo, possession-face, empty eye sockets, glowing pupil-less eyes, and face-obscuring shadows on Leo."
    )


def _generate_with_gemini(prompt, output_path):
    response = client.models.generate_images(
        model=IMAGE_MODEL,
        prompt=prompt,
        config=types.GenerateImagesConfig(
            numberOfImages=1,
            aspectRatio="9:16",
            outputMimeType="image/png",
        ),
    )

    generated = response.generated_images[0].image
    if not generated or not generated.image_bytes:
        raise RuntimeError("Gemini returned no image bytes")

    with open(output_path, "wb") as f:
        f.write(generated.image_bytes)


def _generate_with_openai(prompt, output_path):
    response = openai_client.images.generate(
        model=OPENAI_IMAGE_MODEL,
        prompt=prompt,
        size=OPENAI_IMAGE_SIZE,
        quality="low" if FAST_MODE else "medium",
        output_format="png",
    )
    first_image = response.data[0]
    b64_data = getattr(first_image, "b64_json", None)
    if b64_data:
        with open(output_path, "wb") as f:
            f.write(b64decode(b64_data))
        return

    if getattr(first_image, "url", None):
        raise RuntimeError("OpenAI returned an image URL instead of inline bytes")

    raise RuntimeError("OpenAI returned no image bytes")


def _generate_placeholder_image(storyboard, scene, output_path, index):
    image = Image.new("RGB", (1024, 1792), (18, 18, 28))
    draw = ImageDraw.Draw(image)
    accent = [(255, 174, 66), (99, 196, 255), (255, 92, 92), (173, 125, 255)][index % 4]

    for offset in range(0, 12):
        alpha_rect = Image.new("RGBA", (1024, 1792), (0, 0, 0, 0))
        alpha_draw = ImageDraw.Draw(alpha_rect)
        alpha_draw.ellipse(
            (-180 - offset * 8, 240 - offset * 16, 1180 + offset * 10, 1450 + offset * 6),
            fill=(*accent, max(0, 34 - offset * 2)),
        )
        image = Image.alpha_composite(image.convert("RGBA"), alpha_rect).convert("RGB")

    image = image.filter(ImageFilter.GaussianBlur(1))
    draw = ImageDraw.Draw(image)

    title_font = ImageFont.truetype(FONT_PATH, 74)
    body_font = ImageFont.truetype(FONT_PATH, 42)

    draw.text((80, 120), storyboard["character_name"], font=title_font, fill=(246, 236, 220))
    draw.text((80, 220), scene["stage"].upper(), font=body_font, fill=accent)
    draw.rounded_rectangle((70, 320, 954, 860), radius=28, outline=accent, width=4)
    draw.multiline_text((100, 370), scene["caption"], font=title_font, fill=(255, 255, 255), spacing=12)
    draw.multiline_text((100, 980), scene["image_prompt"], font=body_font, fill=(225, 225, 235), spacing=10)
    draw.text((100, 1610), "Placeholder visual generated locally", font=body_font, fill=(180, 180, 190))
    image.save(output_path)


def generate_scene_images(storyboard, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    image_paths = []
    for index, scene in enumerate(storyboard["scenes"], start=1):
        prompt = _build_scene_prompt(storyboard, scene)
        print(f"Generating image {index}/{len(storyboard['scenes'])}...")
        image_path = output_dir / f"scene_{index:02d}.png"

        if USE_GEMINI_IMAGES:
            try:
                _generate_with_gemini(prompt, image_path)
                print("  image provider: Gemini")
                image_paths.append(image_path)
                continue
            except Exception as gemini_exc:
                print(f"  Gemini image failed: {gemini_exc}")

        try:
            _generate_with_openai(prompt, image_path)
            print("  image provider: OpenAI")
        except Exception as openai_exc:
            print(f"  OpenAI image failed: {openai_exc}")
            _generate_placeholder_image(storyboard, scene, image_path, index)
            print("  image provider: local placeholder")

        image_paths.append(image_path)

    return image_paths
