import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.getenv("TTS_VOICE", "verse")
TTS_INSTRUCTIONS = os.getenv(
    "TTS_INSTRUCTIONS",
    (
        "Narrate like a high-retention YouTube Shorts storyteller. "
        "Keep the pacing sharp, suspenseful, and natural. "
        "Sound cinematic, clear, and emotionally controlled."
    ),
)


def generate_voice_narration(text, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        print("Generating voice narration with OpenAI...")
        with client.audio.speech.with_streaming_response.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=text,
            instructions=TTS_INSTRUCTIONS,
            response_format="mp3",
        ) as response:
            response.stream_to_file(out_path)

        print(f"Saved narration: {out_path}")
        return out_path
    except Exception as exc:
        print(f"OpenAI TTS failed: {exc}")

    fallback_path = out_path.with_suffix(".wav")
    print("Falling back to Windows speech synthesis...")
    _generate_windows_tts(text, fallback_path)
    print(f"Saved narration: {fallback_path}")
    return fallback_path


def _generate_windows_tts(text, out_path):
    escaped_text = (
        text.replace("`", "``")
        .replace('"', '`"')
    )
    script = rf"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 1
$synth.Volume = 100
$synth.SetOutputToWaveFile("{out_path}")
$synth.Speak("{escaped_text}")
$synth.Dispose()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Windows speech synthesis failed")
