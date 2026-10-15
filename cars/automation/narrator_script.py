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

# The narration engine. gpt-4o-mini-tts reads text and infers prosody from
# punctuation, which is what put pauses in odd places and read as machine.
# gpt-audio generates speech instead -- the model behind spoken
# conversation -- and was picked by ear over every text-to-speech preset.
NARRATION_ENGINE = os.getenv("NARRATION_ENGINE", "gpt-audio")
AUDIO_MODEL = os.getenv("OPENAI_AUDIO_MODEL", "gpt-audio")
AUDIO_VOICE = os.getenv("OPENAI_AUDIO_VOICE", "echo")
# gpt-audio has no speed or pitch control: one voice, one natural read. The
# register is dropped afterwards instead, which keeps the performance --
# the phrasing, the emphasis, the accent -- and moves only the pitch.
#
# The model's own take ships. Lowering the register was tried two ways --
# a fixed 0.91, then onto a measured 130 Hz -- and the second did land every
# take on the same number, but both sounded processed, because the shift is
# a time-stretch underneath and no amount of aiming it changes that. The
# shifting is gone rather than switched off: it was dead code behind an env
# var nobody was going to set.
#
# The pitch is still MEASURED, which is a different thing and worth keeping:
# gpt-audio picks its own register per call and the takes land anywhere from
# 132 to 160 Hz, so the number is how one build's voice gets compared with
# another's. Recorded here for the manifest.
_LAST_PITCH = {}
# gpt-audio picks its own register every call -- measured across this
# channel its takes came back anywhere from 132 to 157 Hz. Two attempts
# were made at evening that out: shifting the pitch, which sounded
# processed because it is a time-stretch, and then re-recording anything
# outside a band, which kept the voice honest but spent two extra
# generations per build to reject a read the model meant.
#
# Neither is wanted. The take is the performance, and a script is the thing
# to change when a video is not good enough. The pitch is still measured,
# because it is how one build's voice gets compared with another's, and
# nothing is done about it.
# Measured, not chosen. Three instructions of increasing urgency were read
# against the same script: this one came back at 2.43 words a second,
# "brisk" at 2.17, and "as fast as you can" at 2.34 -- asking harder made
# it slower, presumably by over-enunciating. The model has a pace it will
# not leave, and this is the closest it gets to leaving it.
AUDIO_DELIVERY = (
    "Speak like an American car-YouTube host who is deliberately talking fast to fit a "
    "lot into a short clip. Rapid, urgent, high energy, barely pausing between "
    "sentences. Keep every word clear, but move. "
    "Say the user's message back word for word. Add nothing and skip nothing."
)
DEFAULT_VOICE_PRESET = "trailer_hype"
RAW_TTS_VOICES = {"alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin", "nova", "onyx", "sage", "shimmer", "verse"}
RAW_VOICE_INSTRUCTIONS = (
    "Sound like a confident, quick automotive YouTube host. Keep it conversational, punchy, "
    "human, and clear without imitating any real presenter or celebrity."
)

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


def _resolve_voice(preset):
    if preset in VOICE_PRESETS:
        return VOICE_PRESETS[preset]
    if preset in RAW_TTS_VOICES:
        return {"voice": preset, "speed": 1.0, "instructions": RAW_VOICE_INSTRUCTIONS}
    raise ValueError(
        f"Unknown narrator voice or preset: {preset}. "
        f"Use a preset ({', '.join(VOICE_PRESETS)}) or supported voice ({', '.join(sorted(RAW_TTS_VOICES))})."
    )


def narration_settings():
    """What the narration was actually made with.

    Recorded in the manifest, because the build was writing voice_preset
    "onyx" and tts_speed 1.35 long after neither was true -- a build that
    cannot say how it was made cannot be compared with another one.
    """
    if NARRATION_ENGINE == "gpt-audio":
        return {"engine": NARRATION_ENGINE, "model": AUDIO_MODEL,
                "voice": AUDIO_VOICE,
                # What this build's take measured. Empty means nothing has
                # been synthesized yet.
                "pitch": dict(_LAST_PITCH) or None, "tts_speed": None}
    return {"engine": "tts", "model": DEFAULT_TTS_MODEL, "voice": None,
            "pitch": None, "tts_speed": None}


def _decode_mono(audio_path, sample_rate=16000):
    """The file as one float channel, which is what pitch measurement needs."""
    import numpy as np

    raw = subprocess.run(
        [os.environ.get("FFMPEG_BINARY", "ffmpeg"), "-v", "error", "-i", str(audio_path),
         "-f", "f32le", "-ac", "1", "-ar", str(sample_rate), "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4"), sample_rate


def median_f0(audio_path, floor_hz=60, ceiling_hz=300):
    """The take's median speaking pitch, or None if it cannot be measured.

    Autocorrelation over 40ms windows. Quiet windows and windows whose best
    lag is a weak match are dropped rather than guessed at, so an unvoiced
    stretch does not drag the median. Returning None is a real answer: a
    take too breathy to measure must not be shifted by a made-up amount.
    """
    import numpy as np

    samples, rate = _decode_mono(audio_path)
    window, hop = int(0.04 * rate), int(0.02 * rate)
    low, high = int(rate / ceiling_hz), int(rate / floor_hz)
    pitches = []
    for start in range(0, len(samples) - window, hop):
        frame = samples[start:start + window]
        if np.sqrt((frame ** 2).mean()) < 0.02:
            continue
        frame = frame - frame.mean()
        correlation = np.correlate(frame, frame, "full")[window - 1:]
        candidates = correlation[low:high]
        if not len(candidates):
            continue
        lag = int(np.argmax(candidates)) + low
        # A voiced frame repeats itself; an unvoiced one does not.
        if correlation[lag] < 0.3 * correlation[0]:
            continue
        pitches.append(rate / lag)
    # A second or so of voiced speech before the median means anything.
    if len(pitches) < 50:
        return None
    return float(np.median(pitches))


def measure_pitch(audio_path):
    """Measure the take's register and record it, without changing it.

    This used to shift the audio towards a target as well, which is why it
    was called _deepen. The shifting is gone; the measurement stays because
    it is the only way to tell one build's voice from another's, and it is
    what the dashboard shows beside the word count.
    """
    audio_path = Path(audio_path)
    measured = None
    try:
        measured = median_f0(audio_path)
    except Exception as error:  # noqa: BLE001 - a build is worth more than a measurement
        print(f"[narration] Could not measure pitch ({error}).", flush=True)
    _LAST_PITCH.clear()
    _LAST_PITCH.update(measured_hz=measured, result_hz=measured)
    print(f"[narration] Take measured {measured:.1f} Hz; shipping it as recorded."
          if measured else "[narration] Shipping the take as recorded.", flush=True)
    return audio_path


def _one_take(text, output_path):
    """One chat call that answers in speech rather than text.

    The script is handed over as something to say verbatim, because a
    conversational model given a script as a user turn would otherwise
    reply to it.
    """
    import base64

    completion = with_openai_retry(lambda: OpenAI().chat.completions.create(
        model=AUDIO_MODEL,
        modalities=["text", "audio"],
        audio={"voice": AUDIO_VOICE, "format": "mp3"},
        messages=[
            {"role": "system", "content": AUDIO_DELIVERY},
            {"role": "user", "content": text},
        ],
    ))
    audio = completion.choices[0].message.audio
    output_path.write_bytes(base64.b64decode(audio.data))
    # A call can come back with a few hundred bytes of silence and no
    # error, which is how a four-second narration ships looking fine.
    if output_path.stat().st_size < 20_000:
        raise RuntimeError(
            f"{AUDIO_MODEL} returned only {output_path.stat().st_size} bytes of audio."
        )
    return output_path


def _synthesize_with_audio_model(text, output_path):
    """Record the script once and keep what comes back."""
    _one_take(text, output_path)
    return measure_pitch(output_path)


def synthesize_narration(text, output_path, preset=DEFAULT_VOICE_PRESET, model=DEFAULT_TTS_MODEL, speed=None):
    """Render `text` to speech using one of audition_voices.py's presets,
    so a voice already chosen during auditioning carries straight through
    to real narration without redefining it here."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not _looks_like_real_openai_key(api_key):
        raise RuntimeError("OPENAI_API_KEY is missing or a placeholder; cannot synthesize narration.")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if NARRATION_ENGINE == "gpt-audio":
        return _synthesize_with_audio_model(text, output_path)
    voice = _resolve_voice(preset)
    response = with_openai_retry(lambda: OpenAI().audio.speech.create(
        model=model,
        voice=voice["voice"],
        input=text,
        instructions=voice["instructions"],
        speed=speed if speed is not None else voice["speed"],
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
