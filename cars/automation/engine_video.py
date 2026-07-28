"""Prepare short, original-audio engine clips discovered during research."""
import base64
import json
import math
import os
import subprocess
import wave
from itertools import zip_longest
from pathlib import Path

from PIL import Image

from openai_retry import with_openai_retry

# How many discovered videos prepare_engine_clip will classify by thumbnail
# before giving up, and how many of those it will fully probe with real
# audio extraction. Classifying only ever the first few candidates meant an
# entry with dozens of discovered videos could still come back with "no
# engine-relevant video thumbnail found" if none of those first few happened
# to have a good-looking static thumbnail - even with plenty of good clips
# sitting further down the list.
MAX_THUMBNAIL_CLASSIFICATIONS = 20
MAX_PROBE_CANDIDATES = 3


def interleave_by_listing(videos):
    """Cycle across distinct listings instead of taking a flat top-N slice.

    Discovered videos are pre-sorted labeled-first, but a single listing with
    several unlabeled video embeds and a high search score can otherwise fill
    every slot before a different listing is ever tried. Grouping by listing
    and round-robining preserves that ranking within each listing while
    guaranteeing every discovered listing gets a turn.
    """
    groups = {}
    order = []
    for video in videos:
        key = video.get("auction_url") or video.get("url")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(video)
    interleaved = []
    for row in zip_longest(*(groups[key] for key in order)):
        interleaved.extend(item for item in row if item is not None)
    return interleaved

# Audio-rise multiplier strong enough to count as a real engine event even
# when the frame at that moment doesn't visually confirm it (e.g. a plain
# rear-exterior or cockpit shot during a rev). Chosen from observed data:
# real events have cleared 4x, while roof/interior/ambient noise stays <2x.
STRONG_AUDIO_OVERRIDE_SCORE = 3.0

# Cabin audio (cockpit gauges, generic interior shots) is muffled compared to
# a mic outside the car catching the same engine event, so a raw event-score
# race can let a middling interior clip beat a genuinely better exterior one.
# Rank candidates by scene tier first (exhaust/rear shots catch the real
# exhaust note best, other exterior views next, cabin shots last) and only
# compare audio scores *within* the best available tier, so interior only
# wins when literally nothing exterior was found - not just when it happens
# to be louder.
EXHAUST_SCENE_TYPES = {"exhaust_closeup", "rear_exterior"}
INTERIOR_SCENE_TYPES = {"cockpit", "interior_detail"}


def _selection_tier(scene_type):
    if scene_type in EXHAUST_SCENE_TYPES:
        return 0
    if scene_type in INTERIOR_SCENE_TYPES:
        return 2
    return 1


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


_SCENE_PROMPT = (
    "Classify this frame from a car-auction video. Return only JSON with keys: "
    "scene_type (one of exhaust_closeup, rear_exterior, cockpit, engine_bay, full_exterior, "
    "roof_operation, hood_or_trunk_operation, interior_detail, walkaround, unknown), "
    "engine_relevance integer 1-10, likely_engine_audio boolean, "
    "text_indicates_engine_event boolean, reason string. "
    "We want cold-start, startup, exhaust, or revving footage. Exhaust closeups, rear views, "
    "cockpit views with gauges, and engine-bay views are highly relevant. Convertible roof "
    "movement, hood/trunk operation, silent interior details, and generic walkarounds are not. "
    "{vehicle_line}classify scene purpose separately from whether the vehicle identity matches. "
    "{text_line}"
    "Set text_indicates_engine_event true only if that text explicitly names a cold start, "
    "engine start, startup, exhaust, or revving clip - a keyword scan on this same text may have "
    "already missed a real label due to wording or formatting, so read it yourself rather than "
    "assuming it was already checked. Do not set it true just because the video is of a car."
)


def _candidate_text_context(candidate):
    """Whatever raw title/section/context text was scraped for this video.

    A fixed keyword regex on this same text (done upstream, in the scraper)
    can miss a real "Cold Start"/"Engine Start" label due to wording or
    formatting quirks, so this is handed to the vision model too - it can
    read the text itself rather than trust only the regex's verdict.
    """
    parts = [candidate.get("section"), candidate.get("context")]
    text = " | ".join(part for part in parts if part)
    return text[:600] if text else None


def _classify_scene_image(image_ref, entry, text_context=None):
    """Send one frame (URL or data URI) to vision review for scene purpose."""
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key.startswith("sk-") or len(key) < 30 or not image_ref:
        return {
            "scene_type": "unknown",
            "engine_relevance": 5,
            "likely_engine_audio": True,
            "text_indicates_engine_event": False,
            "reason": "Vision classification unavailable; kept as a tolerant candidate",
            "provider": "metadata_fallback",
        }
    from openai import OpenAI

    vehicle_line = f"The expected vehicle family is {entry.name} ({entry.years}), but " if entry else ""
    text_line = f"The video's own scraped title/section/context text is: {text_context!r}. " if text_context else ""
    prompt = _SCENE_PROMPT.format(vehicle_line=vehicle_line, text_line=text_line)
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=os.getenv("OPENAI_CAR_VIDEO_REVIEW_MODEL", "gpt-4o-mini"),
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": image_ref},
        ]}],
    ))
    text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
    result = json.loads(text)
    result["provider"] = "openai"
    return result


def classify_video_thumbnail(candidate, entry):
    """Classify what the listing-video thumbnail is actually showing.

    This is a cheap upfront filter only. The thumbnail is a single fixed
    platform-generated frame (usually near t=0) and may not depict whatever
    is happening at the actual audio event, so it must not be the final
    word on engine relevance - see classify_scene_at_time for that.
    """
    if candidate.get("scene_review"):
        return candidate["scene_review"]
    thumbnail_url = candidate.get("thumbnail_url") or candidate.get("url")
    return _classify_scene_image(thumbnail_url, entry, text_context=_candidate_text_context(candidate))


def classify_scene_at_time(source, timestamp, entry, frame_path, text_context=None):
    """Grab a frame at `timestamp` from `source` and classify what it shows.

    Unlike the platform thumbnail, this looks at the moment the audio engine
    detected, so a rear-exterior or cockpit shot caught mid-rev is judged on
    what it actually shows during the event instead of on an unrelated
    earlier frame.
    """
    _run([
        "ffmpeg", "-y", "-i", source, "-ss", f"{max(0.0, timestamp):.3f}",
        "-frames:v", "1", "-update", "1", str(frame_path),
    ])
    encoded = base64.b64encode(Path(frame_path).read_bytes()).decode("ascii")
    return _classify_scene_image(f"data:image/jpeg;base64,{encoded}", entry, text_context=text_context)


def _extract_analysis_audio(source, wav_path):
    _run([
        "ffmpeg", "-y", "-i", source, "-t", "45", "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path),
    ])


def _rise_series(wav_path):
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
    rises = [
        (rms[index] + rms[index + 1]) / 2 - sum(rms[max(0, index - 3):index]) / 3
        for index in range(3, len(rms) - 1)
    ] if len(rms) >= 4 else []
    return rises


def detect_engine_onset(wav_path):
    """Find the strongest sustained audio rise, normally the starter/ignition or a rev."""
    rises = _rise_series(wav_path)
    if not rises:
        return 0.0
    # Ignore the opening half-second, where player/container noise can dominate.
    best_offset = max(range(len(rises)), key=lambda index: rises[index])
    return max(0.0, (best_offset + 3) * 0.2 - 0.35)


def detect_secondary_event(wav_path, primary_onset, min_gap=1.5):
    """Find a second distinct audio rise (e.g. a rev after a cold-start idle).

    Returns None when no other rise is at least `min_gap` seconds away from
    the primary onset, or when it is too weak to be worth surfacing.
    """
    rises = _rise_series(wav_path)
    if not rises:
        return None
    candidates = [
        (index, value) for index, value in enumerate(rises)
        if abs(((index + 3) * 0.2 - 0.35) - primary_onset) >= min_gap
    ]
    if not candidates:
        return None
    best_index, best_value = max(candidates, key=lambda item: item[1])
    if best_value <= 0:
        return None
    return max(0.0, (best_index + 3) * 0.2 - 0.35)


def _engine_event_score(wav_path, onset):
    """Ratio of energy after the onset vs before it, discounted when that
    rise doesn't sustain.

    A real engine start or idle stays elevated for as long as it plays; a
    one-off mechanical noise (a door slam, a convertible-top motor engaging
    then stopping) spikes once and decays within about a second. Averaging
    the whole 2-second "after" window can still let a loud-but-brief spike
    read as a strong event, so energy late in that window is also compared
    against energy right at its start - a rise that has basically vanished
    by the second half gets scaled down instead of scoring the same as a
    genuinely sustained one.
    """
    with wave.open(str(wav_path), "rb") as stream:
        rate = stream.getframerate()
        samples = stream.readframes(stream.getnframes())
    values = [int.from_bytes(samples[i:i + 2], "little", signed=True) for i in range(0, len(samples), 2)]
    center = int((onset + 0.35) * rate)
    before = values[max(0, center - rate):max(1, center - rate // 4)]
    after = values[center:min(len(values), center + rate * 2)]
    rms = lambda chunk: math.sqrt(sum(value * value for value in chunk) / max(1, len(chunk)))
    raw_score = rms(after) / max(1.0, rms(before))

    early_after = values[center:min(len(values), center + rate // 2)]
    late_after = values[center + rate:min(len(values), center + rate * 2)]
    if len(late_after) < rate // 4:
        return raw_score  # Clip too short to judge sustain; don't penalize for it.
    early_rms = rms(early_after)
    late_rms = rms(late_after)
    sustain = min(1.0, late_rms / early_rms) if early_rms > 0 else 1.0
    return raw_score * sustain


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
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=os.getenv("OPENAI_CAR_VIDEO_REVIEW_MODEL", "gpt-4o-mini"),
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}"},
        ]}],
    ))
    text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
    result = json.loads(text)
    result["provider"] = "openai"
    return result


def prepare_engine_clip(entry, output_dir, duration=5.0, allow_irrelevant=False):
    candidates = [item for item in entry.engine_videos if item.get("playback_url") or item.get("url")]
    if not candidates:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    clip_path = output_dir / f"rank-{entry.rank}-engine.mp4"
    try:
        # Labeled cold starts win immediately. Otherwise, classify thumbnails
        # progressively - round-robining across listings so one listing's
        # several embeds can't crowd out every other listing - stopping as
        # soon as we have enough good candidates instead of paying for
        # classifications we no longer need, but still willing to go through
        # MAX_THUMBNAIL_CLASSIFICATIONS candidates if the early ones aren't
        # usable rather than giving up after only the first few.
        ordered_candidates = interleave_by_listing(candidates)
        classified = []
        labeled = []
        relevant = []
        for candidate in ordered_candidates[:MAX_THUMBNAIL_CLASSIFICATIONS]:
            try:
                scene_review = classify_video_thumbnail(candidate, entry)
            except Exception as exc:
                scene_review = {
                    "scene_type": "unknown",
                    "engine_relevance": 5,
                    "likely_engine_audio": True,
                    "text_indicates_engine_event": False,
                    "reason": f"Thumbnail review failed open: {exc}",
                    "provider": "fallback_after_error",
                }
            classified.append((candidate, scene_review))
            # Trust either signal as a "labeled" candidate: the scraper's own
            # regex-based type, or the vision model independently reading the
            # same scraped text - the regex can miss a real label due to
            # wording/formatting, so neither alone should be the only check.
            if candidate.get("type") == "cold_start" or scene_review.get("text_indicates_engine_event"):
                labeled.append((candidate, scene_review))
            elif (
                int(scene_review.get("engine_relevance") or 0) >= 5
                and scene_review.get("scene_type") not in {"roof_operation", "hood_or_trunk_operation"}
            ):
                relevant.append((candidate, scene_review))
            if labeled or len(relevant) >= MAX_PROBE_CANDIDATES:
                break
        probe_candidates = labeled[:1] or relevant[:MAX_PROBE_CANDIDATES]
        if not probe_candidates and allow_irrelevant:
            probe_candidates = classified[:MAX_PROBE_CANDIDATES]
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
                    "probe_wav": probe_wav,
                })
            except Exception:
                probe_wav.unlink(missing_ok=True)
                continue
        if not analyses:
            return {"approved": False, "error": "No discovered video had usable audio", "source": candidates[0]}

        best_tier = min(_selection_tier(item["scene_review"].get("scene_type")) for item in analyses)
        tier_candidates = [
            item for item in analyses
            if _selection_tier(item["scene_review"].get("scene_type")) == best_tier
        ]
        chosen = max(tier_candidates, key=lambda item: item["event_score"])
        for item in analyses:
            if item is not chosen:
                item["probe_wav"].unlink(missing_ok=True)
        candidate = chosen["candidate"]
        source = candidate.get("playback_url") or candidate.get("url")
        source_duration = _media_duration(source) or chosen["audio_duration"]
        onset = min(chosen["onset"], max(0.0, source_duration - duration))

        # The platform thumbnail is a fixed early frame and can miss whatever
        # is actually happening at the detected audio event (e.g. a rev
        # caught from a rear-exterior shot). Re-classify using a frame taken
        # at the real onset before making the final relevance call.
        secondary_onset = detect_secondary_event(chosen["probe_wav"], chosen["onset"])
        secondary_event_score = (
            _engine_event_score(chosen["probe_wav"], secondary_onset)
            if secondary_onset is not None else None
        )
        chosen["probe_wav"].unlink(missing_ok=True)

        onset_frame_path = output_dir / f"rank-{entry.rank}-onset-frame.jpg"
        try:
            onset_scene_review = classify_scene_at_time(
                source, onset, entry, onset_frame_path,
                text_context=_candidate_text_context(candidate),
            )
        except Exception:
            onset_scene_review = chosen["scene_review"]
        finally:
            onset_frame_path.unlink(missing_ok=True)
        # The site itself labels some videos "Cold Start"/"Engine Start" - trust
        # that over guessing from pixels, but only once the audio actually
        # shows something happening. A labeled clip caught mid-idle-drive or
        # over background noise (wind, insects, footsteps) can still read as
        # a weak ~1-2x rise with nothing engine-related in it, so the label
        # alone is not enough - it must clear the same bar as an unlabeled
        # clip winning purely on audio. Vision can only judge what a frame
        # shows, not what it sounds like: a rear-exterior or cockpit shot
        # during a real rev looks the same as one during silence, so a strong
        # audio rise is allowed to override a low vision score for scene
        # types where the sound plausibly comes from the engine. Scenes whose
        # sound is clearly unrelated to the engine (roof motors, hood/trunk
        # latches, cabin rustling, footsteps) never get the audio override.
        scene_type = onset_scene_review.get("scene_type")
        has_strong_audio = chosen["event_score"] >= STRONG_AUDIO_OVERRIDE_SCORE
        platform_labeled = (
            candidate.get("type") in {"cold_start", "engine_sound"}
            or bool(onset_scene_review.get("text_indicates_engine_event"))
        )
        engine_relevant = (platform_labeled and has_strong_audio) or (
            scene_type not in {"roof_operation", "hood_or_trunk_operation", "interior_detail", "walkaround"}
            and (
                int(onset_scene_review.get("engine_relevance") or 0) >= 5
                or has_strong_audio
            )
        )
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
            return {"approved": False, "review": review, "scene_review": onset_scene_review, "source": candidate}
        return {
            "approved": bool(review.get("approved") and engine_relevant),
            "path": clip_path,
            "duration": duration,
            "detected_onset_seconds": round(onset, 3),
            "engine_event_score": round(chosen["event_score"], 3),
            "scene_review": onset_scene_review,
            "thumbnail_scene_review": chosen["scene_review"],
            "engine_relevant": engine_relevant,
            "secondary_event_seconds": round(secondary_onset, 3) if secondary_onset is not None else None,
            "secondary_event_score": round(secondary_event_score, 3) if secondary_event_score is not None else None,
            "review": review,
            "source": candidate,
        }
    except Exception as exc:
        return {"approved": False, "error": str(exc), "source": candidate}
