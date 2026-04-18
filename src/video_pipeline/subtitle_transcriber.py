import os
from pathlib import Path

from faster_whisper import WhisperModel

WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL_NAME", "tiny")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        _MODEL = WhisperModel(
            WHISPER_MODEL_NAME,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    return _MODEL


def transcribe_subtitles(audio_path):
    audio_path = Path(audio_path)
    model = _get_model()
    segments, _info = model.transcribe(
        str(audio_path),
        vad_filter=True,
        word_timestamps=False,
    )

    subtitles = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        subtitles.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
            }
        )
    return subtitles
