import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DEFAULT_OUT_DIR = ROOT / "cars" / "output" / "voice_auditions"
DEFAULT_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
DEFAULT_FORMAT = "mp3"

SCRIPT_STYLES = {
    "casual_short": (
        "Okay, this is why people will not shut up about the Miata. It is not fast on paper. "
        "It has one hundred eighty one horsepower, rear wheel drive, and barely any weight. "
        "But that is the point. You are not buying numbers. You are buying a car that makes a normal corner feel like an event."
    ),
    "quirky_walkaround": (
        "Here is the weird thing about the Miata: everything sounds unimpressive until you drive it. "
        "Small engine, tiny cabin, not much trunk, and yeah, basically no flex value. "
        "But then you take one corner and suddenly every heavy performance car feels like it missed the assignment."
    ),
    "spec_punch": (
        "The Miata formula is almost annoyingly simple. Thirty grand-ish, one hundred eighty one horsepower, "
        "one hundred fifty one pound-feet, rear wheel drive, and roughly twenty four hundred pounds. "
        "No crazy horsepower. No fake drama. Just light weight doing light weight things."
    ),
    "hype_short": (
        "This little roadster is proof that horsepower is not the whole story. The Miata is light, rear wheel drive, "
        "and built around the driver instead of a spec-sheet war. It is not the fastest car here. "
        "But it might be the one you actually want to drive every weekend."
    ),
}

SAMPLE_SCRIPT = SCRIPT_STYLES["casual_short"]

VOICE_PRESETS = {
    "car_host": {
        "voice": "alloy",
        "speed": 1.05,
        "instructions": (
            "Sound like a confident modern automotive YouTube host. Human, conversational, quick, "
            "not robotic, not theatrical. Add light enthusiasm and clear pacing."
        ),
    },
    "deep_gravel": {
        "voice": "onyx",
        "speed": 0.95,
        "instructions": (
            "Use a deep, textured, gravelly automotive narrator style. Punchy and cinematic, "
            "but original and not an imitation of any actor, celebrity, or character."
        ),
    },
    "luxury_ai": {
        "voice": "echo",
        "speed": 0.98,
        "instructions": (
            "Sound like a polished premium technology assistant for a luxury car channel. "
            "Calm, precise, intelligent, slightly futuristic, and not robotic."
        ),
    },
    "warm_enthusiast": {
        "voice": "nova",
        "speed": 1.04,
        "instructions": (
            "Sound like a warm car enthusiast explaining the car to a friend. Natural, human, "
            "slightly excited, and easy to listen to on Shorts."
        ),
    },
    "trailer_hype": {
        "voice": "onyx",
        "speed": 1.0,
        "instructions": (
            "Use a high-energy car trailer narrator style. Big and punchy, but still clear. "
            "Do not imitate any real actor, franchise character, or celebrity voice."
        ),
    },
    "clean_news": {
        "voice": "shimmer",
        "speed": 1.08,
        "instructions": (
            "Sound like a clean, fast car-news presenter. Crisp, human, factual, and energetic "
            "without sounding like an advertisement."
        ),
    },
}


def _looks_like_real_openai_key(value):
    value = (value or "").strip()
    if value in {"", "sk-proj", "sk-"}:
        return False
    return value.startswith(("sk-", "sk-proj-")) and len(value) > 30


def _slug(value):
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


def _write_html_index(out_dir, entries):
    rows = []
    for entry in entries:
        rows.append(
            "\n".join(
                [
                    f"<section><h2>{entry['preset']}</h2>",
                    f"<p><strong>voice:</strong> {entry['voice']} | <strong>speed:</strong> {entry['speed']}</p>",
                    f"<p>{entry['instructions']}</p>",
                    f"<audio controls src=\"{Path(entry['file']).name}\"></audio></section>",
                ]
            )
        )
    html = "\n".join(
        [
            "<!doctype html><meta charset='utf-8'><title>Voice auditions</title>",
            "<style>body{font-family:sans-serif;max-width:900px;margin:40px auto;background:#111;color:#eee}section{padding:18px;margin:18px 0;background:#1d1d1d;border-radius:12px}audio{width:100%}</style>",
            "<h1>Car voice auditions</h1>",
            *rows,
        ]
    )
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def audition_voices(text, out_dir=DEFAULT_OUT_DIR, presets=None, model=DEFAULT_MODEL, response_format=DEFAULT_FORMAT, script_style="custom"):
    api_key = os.getenv("OPENAI_API_KEY")
    if not _looks_like_real_openai_key(api_key):
        raise SystemExit(
            "OPENAI_API_KEY is missing or still set to the placeholder value. "
            "Complete .env before running voice auditions."
        )

    selected = presets or list(VOICE_PRESETS)
    unknown = [name for name in selected if name not in VOICE_PRESETS]
    if unknown:
        raise SystemExit(f"Unknown voice preset(s): {', '.join(unknown)}. Available: {', '.join(VOICE_PRESETS)}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI()
    entries = []
    for name in selected:
        preset = VOICE_PRESETS[name]
        suffix = response_format.lstrip(".")
        path = out_dir / f"{_slug(name)}-{preset['voice']}.{suffix}"
        response = client.audio.speech.create(
            model=model,
            voice=preset["voice"],
            input=text,
            instructions=preset["instructions"],
            speed=preset["speed"],
            response_format=response_format,
        )
        response.write_to_file(path)
        entries.append({"preset": name, "file": str(path), "model": model, **preset})

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model": model,
        "response_format": response_format,
        "script_style": script_style,
        "script": text,
        "entries": entries,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_html_index(out_dir, entries)
    return out_dir


def main():
    parser = argparse.ArgumentParser(description="Generate several TTS voice auditions for the car Shorts narrator.")
    parser.add_argument("--text", default=None, help="Script text to read. Overrides --script-style.")
    parser.add_argument("--text-file", type=Path, default=None, help="Read script text from a file. Overrides --text and --script-style.")
    parser.add_argument("--script-style", choices=sorted(SCRIPT_STYLES), default="casual_short")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--format", choices=["mp3", "opus", "aac", "flac", "wav", "pcm"], default=DEFAULT_FORMAT)
    parser.add_argument(
        "--presets",
        default=",".join(VOICE_PRESETS),
        help=f"Comma-separated presets. Available: {', '.join(VOICE_PRESETS)}",
    )
    args = parser.parse_args()
    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8").strip()
        script_style = "text_file"
    elif args.text:
        text = args.text
        script_style = "custom_text"
    else:
        text = SCRIPT_STYLES[args.script_style]
        script_style = args.script_style
    presets = [item.strip() for item in args.presets.split(",") if item.strip()]
    print(audition_voices(text=text, out_dir=args.out_dir, presets=presets, model=args.model, response_format=args.format, script_style=script_style))


if __name__ == "__main__":
    main()
