"""Build a fast, one-car narrated Short from researched facts and stills.

This is intentionally separate from ranking and startup-battle modes. It
uses exterior/engine/interior/detail photos, a 55-60 second script, the committed
narrator sprites, word-sized captions, and gentle motion so still images do
not feel frozen.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from cars_and_bids import scrape_entry_images
from generate_sample import ROOT
from narrator_script import _extract_wav, build_mouth_timeline, synthesize_narration
from narrator_video import render_narrator_video
from openai_retry import with_openai_retry
from plate_blur import blur_license_plates

load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
OUTPUT_ROOT = ROOT / "cars" / "single-car-shorts"
TARGET_WORDS = (175, 190)
# The prompt targets the tight range above, but word counts from a grounded
# structured response can land a little outside it. Runtime is normalized
# from the actual audio below, so only reject clearly broken short/long
# responses instead of throwing away an otherwise good 199-word script.
ACCEPTABLE_WORDS = (140, 220)
FAST_TTS_SPEED = 1.12
TARGET_DURATION_SECONDS = 58.0
ALLOWED_MEDIA_TYPES = {"exterior", "engine", "interior", "detail", "wheel"}

PACKAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "script", "scenes", "sources"],
    "properties": {
        "title": {"type": "string"},
        "script": {"type": "string"},
        "scenes": {
            "type": "array", "minItems": 5, "maxItems": 7,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["media_type", "headline", "fact"],
                "properties": {
                    "media_type": {"type": "string", "enum": ["exterior", "engine", "interior", "detail", "wheel"]},
                    "headline": {"type": "string"},
                    "fact": {"type": "string"},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
    },
}


def _word_count(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


def research_script(make, model, trim="", start_year=None, end_year=None):
    label = " ".join(value for value in [make, model, trim] if value).strip()
    year_scope = (
        f"model years {start_year}-{end_year}" if start_year and end_year
        else f"model year {start_year or end_year}" if start_year or end_year else "the best-known generation"
    )
    prompt = f"""Research and write one original vertical car-video package about {label}, scoped to {year_scope}.
Use web search and verify every technical comparison. Write a quick, conversational script of {TARGET_WORDS[0]}-{TARGET_WORDS[1]} words so faster TTS lands near 55-60 seconds. Start with a strong value/performance hook, name the exact car early, then cover engine/turbo, drivetrain, one comparison or ownership insight, tuning potential only when supportable, and finish with a direct viewer-choice question. Use short spoken sentences and natural contractions. Do not imitate or quote any creator.

Return 5-7 scenes in script order. Headlines are only for important facts and must be 1-4 words (examples: model/chassis, engine code, AWD, horsepower, price gap); use an empty string for ordinary beats. Use exterior media for the hook/close, engine for powertrain, wheel for drivetrain when useful, detail for modification/technical beats, and interior only when the script specifically discusses the cabin, seats, controls, or practicality. Sources must be direct URLs supporting the claims."""
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model="gpt-4o",
        input=prompt,
        tools=[{"type": "web_search_preview"}],
        text={"format": {"type": "json_schema", "name": "single_car_short", "strict": True, "schema": PACKAGE_SCHEMA}},
    ))
    package = json.loads(response.output_text.strip())
    count = _word_count(package["script"])
    if not ACCEPTABLE_WORDS[0] <= count <= ACCEPTABLE_WORDS[1]:
        raise RuntimeError(
            f"Single-car script is outside the safe {ACCEPTABLE_WORDS[0]}-{ACCEPTABLE_WORDS[1]} word range; "
            f"model returned {count}."
        )
    if not TARGET_WORDS[0] <= count <= TARGET_WORDS[1]:
        print(
            f"[single-car] Script returned {count} words outside the preferred "
            f"{TARGET_WORDS[0]}-{TARGET_WORDS[1]} range; audio timing will normalize the final runtime."
        )
    package["word_count"] = count
    return package


def gather_media(make, model, trim, start_year, end_year, images_dir):
    search_hint = " ".join(value for value in [make, model, trim] if value).strip()
    if start_year or end_year:
        first, last = start_year or end_year, end_year or start_year
        generation = ""
    else:
        first, last, generation = "", "", ""
    entry = {
        "name": search_hint,
        "label": search_hint,
        "years": f"{first}-{last}" if first and first != last else str(first),
        "search_hint": search_hint,
        "visual_highlight": "exterior engine turbo drivetrain wheel interior dashboard seats performance detail",
        "generation_label": generation,
    }
    selected, manifest = scrape_entry_images(SCRAPER_DIR, images_dir, entry)
    reviews = (manifest.get("ai_review") or {}).get("reviews", [])
    review_by_name = {item.get("path"): item for item in reviews}
    media = []
    for relative in selected:
        review = review_by_name.get(Path(relative).name, {})
        shot_type = review.get("shot_type", "exterior")
        if shot_type not in ALLOWED_MEDIA_TYPES:
            continue
        path = images_dir.parent / relative
        if path.exists():
            blur_license_plates(path)
            media.append({"path": relative.replace("\\", "/"), "type": shot_type})
    if not media:
        raise RuntimeError("No approved exterior, engine, detail, or wheel images were found.")
    return media, manifest.get("selected_auction") or {}


def normalize_audio_duration(audio_path, target=TARGET_DURATION_SECONDS, minimum=55.0, maximum=60.0):
    """Keep the final voice close to one minute without asking TTS twice."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(audio_path)],
        check=True, capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    if minimum <= duration <= maximum:
        return duration
    tempo = duration / target
    # atempo accepts 0.5-2.0; this range is ample for a tightly constrained script.
    tempo = max(0.5, min(2.0, tempo))
    adjusted = audio_path.with_name(f"{audio_path.stem}-timed{audio_path.suffix}")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path), "-filter:a", f"atempo={tempo:.5f}", str(adjusted)],
        check=True, capture_output=True, text=True,
    )
    adjusted.replace(audio_path)
    return target


def transcribe_word_timeline(audio_path):
    """Get real per-word timestamps for captions; rendering has a safe fallback."""
    with Path(audio_path).open("rb") as audio_file:
        client = OpenAI()
        def request_alignment():
            audio_file.seek(0)
            return client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
        result = with_openai_retry(request_alignment)
    words = []
    for item in getattr(result, "words", None) or []:
        if isinstance(item, dict):
            word, start, end = item.get("word"), item.get("start"), item.get("end")
        else:
            word, start, end = getattr(item, "word", None), getattr(item, "start", None), getattr(item, "end", None)
        if word and start is not None and end is not None:
            words.append({"word": str(word).strip(), "start": float(start), "end": float(end)})
    return words


def build_short(args):
    output_dir = OUTPUT_ROOT / args.short_id
    images_dir = output_dir / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    package = research_script(args.make, args.model, args.trim, args.start_year, args.end_year)
    media, selected_auction = gather_media(
        args.make, args.model, args.trim, args.start_year, args.end_year, images_dir
    )
    # Put images in the same semantic order as the written scenes. Reuse a
    # strong exterior when a requested niche shot was unavailable.
    ordered_media = []
    for scene in package["scenes"]:
        requested = scene["media_type"]
        match = next((item for item in media if item["type"] == requested), None)
        match = match or next((item for item in media if item["type"] == "exterior"), None) or media[0]
        ordered_media.append(match)
    media = ordered_media
    audio_path = output_dir / "narration.mp3"
    synthesize_narration(package["script"], audio_path, preset=args.voice, speed=FAST_TTS_SPEED)
    normalized_duration = normalize_audio_duration(audio_path)
    try:
        word_timeline = transcribe_word_timeline(audio_path)
    except Exception as exc:
        print(f"[single-car] Word alignment failed; falling back to estimated caption timing: {exc}")
        word_timeline = []
    wav_path = output_dir / "narration.wav"
    _extract_wav(audio_path, wav_path)
    timeline = build_mouth_timeline(wav_path)
    wav_path.unlink(missing_ok=True)
    manifest = {
        "car": {"make": args.make, "model": args.model, "trim": args.trim or None},
        **package,
        "voice_preset": args.voice,
        "tts_speed": FAST_TTS_SPEED,
        "target_duration_seconds": TARGET_DURATION_SECONDS,
        "normalized_audio_duration_seconds": round(normalized_duration, 3),
        "audio_path": str(audio_path),
        "duration_seconds": timeline[-1]["end"] if timeline else 0,
        "mouth_timeline": timeline,
        "word_timeline": word_timeline,
        "media": media,
        "selected_auction": selected_auction,
    }
    manifest_path = output_dir / "result.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    media_paths = [output_dir / item["path"] for item in media]
    video_path = output_dir / "single_car_short.mp4"
    render_narrator_video(media_paths, manifest, video_path)
    manifest["video"] = video_path.name
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Create one fast, narrated, stills-based car Short.")
    parser.add_argument("--make", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--trim", default="")
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--short-id", required=True)
    parser.add_argument("--voice", default="onyx")
    args = parser.parse_args()
    print(json.dumps(build_short(args), indent=2))


if __name__ == "__main__":
    main()
