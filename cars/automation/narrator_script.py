"""Turn car facts into a narration script, TTS audio, and a mouth-state
timeline, for the on-screen narrator character in narrator/narrator-rig.html.

This is the content-side half of the narrator pipeline: script generation
and audio-driven mouth timing. The render side (compositing the character,
the car media, and captions into an actual video) is a separate, later
step - this module's job ends at producing a manifest.json with everything
the renderer will need.
"""
import json
import math
import os
import subprocess
import wave
from pathlib import Path

from openai import OpenAI

from openai_retry import with_openai_retry
from audition_voices import VOICE_PRESETS

DEFAULT_SCRIPT_MODEL = os.getenv("OPENAI_NARRATOR_SCRIPT_MODEL", "gpt-4o-mini")
DEFAULT_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
DEFAULT_VOICE_PRESET = "trailer_hype"

# Mouth state per RMS-loudness bucket, coarsest approximation of visemes:
# real speech has dozens of mouth shapes, but the rig only has three, so
# loudness (quiet consonants/pauses vs. open vowels) is a reasonable stand-in
# for how open the mouth should look at any given moment.
MOUTH_STATES = ("closed", "small", "wide")


def _looks_like_real_openai_key(value):
    value = (value or "").strip()
    if value in {"", "sk-proj", "sk-"}:
        return False
    return value.startswith(("sk-", "sk-proj-")) and len(value) > 30


_SCRIPT_PROMPT = (
    "Write a short, fast, confident narration script for a car-review social video, "
    "in the voice of a hype car-enthusiast host (think Doug DeMuro energy crossed with a car-meme "
    "YouTube Shorts channel) -- punchy short sentences, a little cocky, mild casual profanity is fine "
    "(e.g. 'as hell', 'stupid fast'), never slurs or anything genuinely offensive. "
    "Cover, in order: a confident opening hook naming the car, one standout spec or fact, the engine/drivetrain, "
    "and a closing line that asks whether the viewer would actually own one. "
    "Aim for 45-70 words total (roughly 15-22 seconds spoken). Return ONLY the narration text, no stage "
    "directions, no quotation marks, no headings."
)


def generate_narration_script(car_entry, model=DEFAULT_SCRIPT_MODEL):
    """Call OpenAI to write a narration script for one car.

    car_entry is expected to look like the dicts already used elsewhere in
    this pipeline (battle_request.py, ranking_engine.py): at minimum
    make/model/year, ideally also trim, generation_label, and any engine
    or drivetrain facts already known from research.
    """
    facts = "; ".join(
        f"{key}: {value}"
        for key, value in [
            ("make", car_entry.get("make")),
            ("model", car_entry.get("model")),
            ("trim", car_entry.get("trim") or car_entry.get("trim_used")),
            ("year", car_entry.get("year")),
            ("generation", car_entry.get("generation_label")),
            ("engine_or_drivetrain_facts", car_entry.get("engine_facts") or car_entry.get("visual_highlight")),
        ]
        if value
    )
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=model,
        input=[{"role": "user", "content": f"{_SCRIPT_PROMPT}\n\nCar facts: {facts}"}],
    ))
    return response.output_text.strip()


def synthesize_narration(text, output_path, preset=DEFAULT_VOICE_PRESET, model=DEFAULT_TTS_MODEL):
    """Render `text` to speech using one of audition_voices.py's presets,
    so a voice already chosen during auditioning carries straight through
    to real narration without redefining it here."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not _looks_like_real_openai_key(api_key):
        raise RuntimeError("OPENAI_API_KEY is missing or a placeholder; cannot synthesize narration.")
    voice = VOICE_PRESETS[preset]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = with_openai_retry(lambda: OpenAI().audio.speech.create(
        model=model,
        voice=voice["voice"],
        input=text,
        instructions=voice["instructions"],
        speed=voice["speed"],
        response_format="mp3",
    ))
    response.write_to_file(str(output_path))
    return output_path


def _extract_wav(source, wav_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)],
        check=True, capture_output=True, text=True,
    )


def _rms_windows(wav_path, frame_seconds=0.09):
    with wave.open(str(wav_path), "rb") as stream:
        rate = stream.getframerate()
        raw = stream.readframes(stream.getnframes())
    samples = [int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, len(raw), 2)]
    window = max(1, int(rate * frame_seconds))
    windows = []
    for offset in range(0, len(samples), window):
        chunk = samples[offset:offset + window]
        if not chunk:
            continue
        rms = math.sqrt(sum(value * value for value in chunk) / len(chunk))
        windows.append(rms)
    return windows, frame_seconds


def build_mouth_timeline(audio_path, frame_seconds=0.09, quiet_ratio=0.12, loud_ratio=0.55):
    """Turn narration audio into a list of {start, end, mouth} segments.

    Loudness is bucketed relative to this clip's own peak (quiet_ratio/
    loud_ratio are fractions of the peak RMS), rather than an absolute
    threshold, since TTS output loudness varies by voice/preset. Adjacent
    windows in the same bucket are merged into one segment so the renderer
    isn't stepping mouth state every single frame_seconds for no visual
    reason.
    """
    windows, step = _rms_windows(audio_path, frame_seconds)
    if not windows:
        return []
    peak = max(windows) or 1.0
    quiet_cut = peak * quiet_ratio
    loud_cut = peak * loud_ratio

    def bucket(value):
        if value <= quiet_cut:
            return "closed"
        if value >= loud_cut:
            return "wide"
        return "small"

    states = [bucket(value) for value in windows]
    segments = []
    seg_start = 0.0
    seg_state = states[0]
    for index in range(1, len(states)):
        if states[index] != seg_state:
            segments.append({"start": round(seg_start, 3), "end": round(index * step, 3), "mouth": seg_state})
            seg_start = index * step
            seg_state = states[index]
    segments.append({"start": round(seg_start, 3), "end": round(len(states) * step, 3), "mouth": seg_state})
    return segments


def build_narration_package(car_entry, output_dir, preset=DEFAULT_VOICE_PRESET):
    """End to end for one car: script -> audio -> mouth timeline -> manifest.

    Returns the manifest dict; also writes it to <output_dir>/manifest.json
    alongside the audio file, ready for the render step to consume.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    script = generate_narration_script(car_entry)
    audio_path = output_dir / "narration.mp3"
    synthesize_narration(script, audio_path, preset=preset)
    wav_path = output_dir / "narration.wav"
    _extract_wav(audio_path, wav_path)
    timeline = build_mouth_timeline(wav_path)
    wav_path.unlink(missing_ok=True)
    manifest = {
        "car": {key: car_entry.get(key) for key in ("make", "model", "trim", "year", "generation_label")},
        "script": script,
        "voice_preset": preset,
        "audio_path": str(audio_path),
        "duration_seconds": timeline[-1]["end"] if timeline else 0.0,
        "mouth_timeline": timeline,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
