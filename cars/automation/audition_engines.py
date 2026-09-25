"""Read one script through every engine, so naturalness can be compared.

The voice presets all come from the same text-to-speech model, so
auditioning them answers "which timbre" and never "why does it not sound
like a person". That is a different question, and it has three candidate
answers worth hearing side by side:

  tts-fast      what ships today -- gpt-4o-mini-tts generated at 1.35x
  tts-natural   the same model at 1.0x, told to speak quickly instead
  gpt-audio     the conversational audio model, which generates speech
                rather than reading text

The middle one exists because compressing audio to 1.35x squeezes the
pauses and breaths unevenly, which is the thing that reads as robotic.
Asking the model to talk fast keeps them proportional.
"""
import argparse
import base64
import json
from pathlib import Path

from generate_sample import ROOT

OUT_ROOT = ROOT / "cars" / "voice-auditions"
TTS_MODEL = "gpt-4o-mini-tts"
# Generally available since January 2026. It is the model behind spoken
# conversation rather than a reader, which is the whole point of the test.
AUDIO_MODEL = "gpt-audio"

BRISK = (
    "Speak like a confident American car-YouTube host talking fast because the clip is "
    "short. Quick and energetic, natural rhythm, clear consonants. Do not sound like an "
    "announcer reading a script, and do not pause between every clause."
)

ENGINES = {
    "a-tts-fast-1_35x": {
        "engine": "tts", "voice": "alloy", "speed": 1.35,
        "instructions": "Sound like a confident modern automotive YouTube host. Human, "
                        "conversational, quick, not robotic, not theatrical.",
    },
    "b-tts-natural-brisk": {
        "engine": "tts", "voice": "alloy", "speed": 1.0, "instructions": BRISK,
    },
    "c-gpt-audio": {"engine": "audio", "voice": "alloy", "instructions": BRISK},
    "d-gpt-audio-echo": {"engine": "audio", "voice": "echo", "instructions": BRISK},
}


def _tts(client, spec, text, out_path):
    response = client.audio.speech.create(
        model=TTS_MODEL, voice=spec["voice"], input=text,
        instructions=spec["instructions"], speed=spec["speed"], response_format="mp3",
    )
    response.write_to_file(str(out_path))


def _gpt_audio(client, spec, text, out_path):
    """One chat call that answers in speech instead of text.

    The text is handed over as something to say verbatim rather than as a
    question, because the model would otherwise reply to it.
    """
    completion = client.chat.completions.create(
        model=AUDIO_MODEL,
        modalities=["text", "audio"],
        audio={"voice": spec["voice"], "format": "mp3"},
        messages=[
            {"role": "system", "content": spec["instructions"]
                + " Say the user's message back word for word. Add nothing and skip nothing."},
            {"role": "user", "content": text},
        ],
    )
    audio = completion.choices[0].message.audio
    out_path.write_bytes(base64.b64decode(audio.data))


def render(text, out_dir, engines=None):
    from openai import OpenAI

    client = OpenAI()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made, failed = {}, {}
    for name, spec in ENGINES.items():
        if engines and name not in engines:
            continue
        out_path = out_dir / f"{name}.mp3"
        try:
            (_tts if spec["engine"] == "tts" else _gpt_audio)(client, spec, text, out_path)
            # A call can return a few hundred bytes of silence and no error,
            # which is how a broken sample gets shipped looking fine.
            size = out_path.stat().st_size
            if size < 20_000:
                raise RuntimeError(f"only {size} bytes came back")
            made[name] = out_path.name
            print(f"[engines] {name}: {size // 1024}KB", flush=True)
        except Exception as error:
            failed[name] = str(error)
            print(f"[engines] {name} FAILED: {error}", flush=True)
            out_path.unlink(missing_ok=True)
    return {"made": made, "failed": failed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audition-id", required=True)
    parser.add_argument("--text-file", type=Path, default=None)
    parser.add_argument("--engines", default="")
    args = parser.parse_args()

    from voice_audition_request import DEFAULT_SCRIPT

    text = (args.text_file.read_text(encoding="utf-8") if args.text_file else DEFAULT_SCRIPT).strip()
    wanted = [name.strip() for name in args.engines.split(",") if name.strip()]
    out_dir = OUT_ROOT / args.audition_id / "engines"
    result = render(text, out_dir, engines=wanted or None)
    (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
