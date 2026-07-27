"""Prepare short, original-audio engine clips discovered during research."""
import base64
import json
import math
import os
import subprocess
import wave
from pathlib import Path

from PIL import Image


def choose_video_candidate(candidates):
    """Prefer labeled cold starts, but tolerate any same-search engine video."""
    usable = [item for item in (candidates or []) if item.get("playback_url") or item.get("url")]
    if not usable:
        return None
    type_score = {"cold_start": 3, "engine_sound": 2, "video": 1, "walkaround": 0}
    return max(
        usable,
        key=lambda item: (
            type_score.get(item.get("type"), 0),
            int(item.get("search_score") or 0),
        ),
    )


def _run(command):
    return subprocess.run(command, check=True, capture_output=True, text=True)


def _media_duration(source):
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", source,
    ])
    value = result.stdout.strip()
    if not value or value.upper() == "N/A":
        return 0.0
    return max(0.0, float(value))


def _wav_duration(path):
    with wave.open(str(path), "rb") as stream:
        return stream.getnframes() / max(1, stream.getframerate())


def classify_video_thumbnail(candidate, entry):
    """Classify what the listing-video thumbnail is actually showing."""
    if candidate.get("scene_review"):
        return candidate["scene_review"]
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    thumbnail_url = candidate.get("thumbnail_url") or candidate.get("url")
    if not key.startswith("sk-") or len(key) < 30 or not thumbnail_url:
        return {
            "scene_type": "unknown",
            "engine_relevance": 5,
            "likely_engine_audio": True,
            "reason": "Vision classification unavailable; kept as a tolerant candidate",
            "provider": "metadata_fallback",
        }
    from openai import OpenAI

    prompt = (
        "Classify this thumbnail from a car-auction video. Return only JSON with keys: "
        "scene_type (one of exhaust_closeup, rear_exterior, cockpit, engine_bay, full_exterior, "
        "roof_operation, hood_or_trunk_operation, interior_detail, walkaround, unknown), "
        "engine_relevance integer 1-10, likely_engine_audio boolean, reason string. "
        "We want cold-start, startup, exhaust, or revving footage. Exhaust closeups, rear views, "
        "cockpit views with gauges, and engine-bay views are highly relevant. Convertible roof "
        "movement, hood/trunk operation, silent interior details, and generic walkarounds are not. "
        f"The expected vehicle family is {entry.name} ({entry.years}), but classify scene purpose "
        "separately from whether the vehicle identity matches."
    )
    response = OpenAI().responses.create(
        model=os.getenv("OPENAI_CAR_VIDEO_REVIEW_MODEL", "gpt-4o-mini"),
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": thumbnail_url},
        ]}],
    )
    text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
    result = json.loads(text)
    result["provider"] = "openai"
    return result


def _extract_analysis_audio(source, wav_path):
    _run([
        "ffmpeg", "-y", "-i", source, "-t", "45", "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path),
    ])


def detect_engine_onset(wav_path):
    """Find the strongest sustained audio rise, normally the starter/ignition."""
    with wave.open(str(wav_path), "rb") as stream:
        rate = stream.getframerate()
        samples = stream.readframes(stream.getnframes())
    values = [int.from_bytes(samples[i:i + 2], "little", signed=True) for i in range(0, len(samples), 2)]
    window = max(1, rate // 5)
    rms = []
    for offset in range(0, len(values), window):
        chunk = values[offset:offset + window]
        if chunk:
            rms.append(math.sqrt(sum(value * value for value in chunk) / len(chunk)))
    if len(rms) < 4:
        return 0.0
    # Ignore the opening half-second, where player/container noise can dominate.
    best_index = max(
        range(3, len(rms) - 1),
        key=lambda index: (rms[index] + rms[index + 1]) / 2 - sum(rms[max(0, index - 3):index]) / 3,
    )
    return max(0.0, best_index * 0.2 - 0.35)


def _engine_event_score(wav_path, onset):
    with wave.open(str(wav_path), "rb") as stream:
        rate = stream.getframerate()
        samples = stream.readframes(stream.getnframes())
    values = [int.from_bytes(samples[i:i + 2], "little", signed=True) for i in range(0, len(samples), 2)]
    center = int((onset + 0.35) * rate)
    before = values[max(0, center - rate):max(1, center - rate // 4)]
    after = values[center:min(len(values), center + rate * 2)]
    rms = lambda chunk: math.sqrt(sum(value * value for value in chunk) / max(1, len(chunk)))
    return rms(after) / max(1.0, rms(before))


def _contact_sheet(frame_paths, output_path):
    images = [Image.open(path).convert("RGB") for path in frame_paths]
    width = 480
    resized = []
    for image in images:
        height = max(1, int(image.height * width / image.width))
        resized.append(image.resize((width, height), Image.Resampling.LANCZOS))
    canvas = Image.new("RGB", (width, sum(image.height for image in resized)), "black")
    y = 0
    for image in resized:
        canvas.paste(image, (0, y))
        y += image.height
    canvas.save(output_path, quality=88)


def _verify_frames(contact_sheet, entry):
    """Broad, fail-open verification: make/model matters more than exact trim."""
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key.startswith("sk-") or len(key) < 30:
        return {"approved": True, "provider": "metadata_fallback", "reason": "OpenAI vision unavailable"}
    from openai import OpenAI

    encoded = base64.b64encode(contact_sheet.read_bytes()).decode("ascii")
    prompt = (
        "These are three frames from one car video. Return only JSON with keys approved boolean, "
        "detected_vehicle string, confidence integer 1-10, reason string. Approve when the frames "
        f"plausibly show the same make/model or generation family as {entry.name} ({entry.years}). "
        "Exact trim, engine, package, or model year does not need to match. Reject only a clearly "
        "different make/model, unrelated footage, or unusable frames."
    )
    response = OpenAI().responses.create(
        model=os.getenv("OPENAI_CAR_VIDEO_REVIEW_MODEL", "gpt-4o-mini"),
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}"},
        ]}],
    )
    text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
    result = json.loads(text)
    result["provider"] = "openai"
    return result


def prepare_engine_clip(entry, output_dir, duration=3.0, allow_irrelevant=False):
    candidates = [item for item in entry.engine_videos if item.get("playback_url") or item.get("url")]
    if not candidates:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    clip_path = output_dir / f"rank-{entry.rank}-engine.mp4"
    try:
        # Labeled cold starts win immediately. When labels are hidden by the
        # site's player, compare up to three embeds and choose the strongest
        # starter/rev-like jump in audio energy.
        classified = []
        for candidate in candidates[:4]:
            try:
                scene_review = classify_video_thumbnail(candidate, entry)
            except Exception as exc:
                scene_review = {
                    "scene_type": "unknown",
                    "engine_relevance": 5,
                    "likely_engine_audio": True,
                    "reason": f"Thumbnail review failed open: {exc}",
                    "provider": "fallback_after_error",
                }
            classified.append((candidate, scene_review))
        labeled = [
            item for item in classified
            if item[0].get("type") == "cold_start"
        ]
        relevant = [
            item for item in classified
            if int(item[1].get("engine_relevance") or 0) >= 5
            and item[1].get("scene_type") not in {"roof_operation", "hood_or_trunk_operation"}
        ]
        probe_candidates = labeled[:1] or relevant[:3]
        if not probe_candidates and allow_irrelevant:
            probe_candidates = classified[:3]
        if not probe_candidates:
            return {
                "approved": False,
                "error": "No engine-relevant video thumbnail found",
                "scene_reviews": [review for _, review in classified],
                "source": candidates[0],
            }
        analyses = []
        for index, (candidate, scene_review) in enumerate(probe_candidates):
            probe_wav = output_dir / f"rank-{entry.rank}-engine-probe-{index}.wav"
            try:
                source = candidate.get("playback_url") or candidate.get("url")
                _extract_analysis_audio(source, probe_wav)
                onset = detect_engine_onset(probe_wav)
                analyses.append({
                    "candidate": candidate,
                    "scene_review": scene_review,
                    "onset": onset,
                    "event_score": _engine_event_score(probe_wav, onset),
                    "audio_duration": _wav_duration(probe_wav),
                })
            except Exception:
                continue
            finally:
                probe_wav.unlink(missing_ok=True)
        if not analyses:
            return {"approved": False, "error": "No discovered video had usable audio", "source": candidates[0]}
        chosen = max(analyses, key=lambda item: item["event_score"])
        candidate = chosen["candidate"]
        source = candidate.get("playback_url") or candidate.get("url")
        source_duration = _media_duration(source) or chosen["audio_duration"]
        onset = min(chosen["onset"], max(0.0, source_duration - duration))
        _run([
            # Seek after opening the HLS input. This is slower than input-side
            # seeking but does not jump past the final video keyframe when the
            # interesting exhaust event occurs at the end of the source.
            "ffmpeg", "-y", "-i", source, "-ss", f"{onset:.3f}", "-t", str(duration),
            "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(clip_path),
        ])
        clip_duration = min(duration, _media_duration(str(clip_path)))
        if clip_duration < 0.35:
            raise RuntimeError(f"Extracted clip is too short ({clip_duration:.2f}s)")
        frame_paths = []
        frame_moments = (
            min(0.1, clip_duration / 4),
            clip_duration / 2,
            max(0.05, clip_duration - 0.1),
        )
        for index, moment in enumerate(frame_moments):
            frame_path = output_dir / f"rank-{entry.rank}-verify-{index}.jpg"
            _run([
                "ffmpeg", "-y", "-i", str(clip_path), "-ss", str(moment),
                "-frames:v", "1", "-update", "1", str(frame_path),
            ])
            frame_paths.append(frame_path)
        contact_sheet = output_dir / f"rank-{entry.rank}-video-review.jpg"
        _contact_sheet(frame_paths, contact_sheet)
        review = _verify_frames(contact_sheet, entry)
        if not review.get("approved"):
            clip_path.unlink(missing_ok=True)
            return {"approved": False, "review": review, "source": candidate}
        return {
            "approved": True,
            "path": clip_path,
            "duration": duration,
            "detected_onset_seconds": round(onset, 3),
            "engine_event_score": round(chosen["event_score"], 3),
            "scene_review": chosen["scene_review"],
            "review": review,
            "source": candidate,
        }
    except Exception as exc:
        return {"approved": False, "error": str(exc), "source": candidate}
