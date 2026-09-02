"""Render stage for the talking-narrator format: car media up top, the
narrator character talking underneath, captions in between -- driven by
the manifest.json that narrator_script.py produces (script text, narration
audio, and an audio-loudness mouth timeline).

The narrator itself is not rendered live; export-sprites.js pre-renders it
to a fixed set of transparent PNGs (one per mouth x eyes x pose
combination) and this flips between them like a flipbook: mouth state
follows the audio-loudness mouth_timeline, eyes follow a blink timeline,
and the body pose advances once per sentence (steady -> jolt -> lean_left
-> lean_right -> ...), detected from the natural pauses in the real
per-word timeline rather than a fixed clock, with a short crossfade at
each pose change so it reads as a transition into the new stance instead
of a jump cut. The one exception is the small idle lean, which stays a
smooth, continuous per-frame rotation on top of whichever pose is showing
(_apply_body_sway), since baking *that* into discrete sprites read as
jerky rather than a smooth sway.
"""
import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw
from moviepy.editor import (
    AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip,
    concatenate_videoclips,
)

from generate_sample import ROOT, CANVAS, _font, _wrap

SPRITES_DIR = ROOT / "narrator" / "sprites"
# The top of the frame is a stack of three bands, top to bottom: a headline
# band, the car media itself, then a caption band -- in that order so
# neither piece of text sits on top of the picture the way it used to when
# both were just absolutely positioned over the whole top region. Together
# they're kept smaller than a near-half split so the narrator reads as the
# focal point instead of the media dominating the frame.
TOP_STACK_RATIO = 0.45
HEADLINE_ZONE_RATIO = 0.095
CAPTION_ZONE_RATIO = 0.075
# Margin on every edge of the media's own band -- the picture is inset
# instead of stretched edge-to-edge, so it reads as a framed photo rather
# than a banner. Trimmed further (0.125 -> 0.05 -> 0.02) each time the
# picture needed to read bigger; still a thin margin, not edge-to-edge.
MEDIA_INSET_RATIO = 0.02
CAPTION_CHUNK_WORDS = 1

# Four poses, cycled once per sentence: steady/jolt are the small "redraw
# flicker" (line weight + a tiny arm/brow twitch, no lean); lean_left/
# lean_right are a bigger, deliberate weight-shift -- one arm swaps to a
# bent-elbow variant, that side's leg rotates outward, and the body tilts
# a few degrees toward that side (see export-sprites.js POSES). Sentence
# boundaries come from the real per-word timeline's own pause gaps rather
# than word-counting a sentence split against the script text, since
# Whisper's tokenization doesn't line up with a naive `sentence.split()`
# (e.g. "3.7-liter" comes back as three separate word entries) -- a pause
# of at least SENTENCE_PAUSE_THRESHOLD_SECONDS between two words' audio is
# a much more reliable signal of "the narrator just finished a sentence."
POSE_CYCLE = ["steady", "jolt", "lean_left", "lean_right"]
SENTENCE_PAUSE_THRESHOLD_SECONDS = 0.4
POSE_TRANSITION_SECONDS = 0.18
# Fallback cadence only used when there's no real word_timeline to find
# pauses in (estimated caption timing) -- some pose variety beats none.
POSE_FALLBACK_SEGMENT_SECONDS = 3.0
# A blink timeline of its own -- the flipbook otherwise defaults to
# permanently-open eyes for the whole video.
BLINK_START_SECONDS = 1.2
BLINK_INTERVAL_SECONDS = 3.0
# Smooth, continuous body lean -- matches the rig's bodySway keyframes
# (rotate -0.8deg..0.8deg over ~4.6s), applied as a per-frame rotation
# instead of discrete sprite poses so it doesn't read as a jerky snap.
BODY_SWAY_DEGREES = 0.8
BODY_SWAY_PERIOD_SECONDS = 4.6
BLINK_DURATION_SECONDS = 0.12


def _load_sprites_manifest():
    manifest_path = SPRITES_DIR / "sprites.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"No sprite manifest at {manifest_path} -- run "
            "`cd narrator/render && node export-sprites.js` first."
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _fit_content(clip, max_size):
    """Scale to fit within max_size, preserving aspect -- the clip's own
    w/h shrink to match its content rather than being padded, so it can be
    stacked/boxed by the caller."""
    max_w, max_h = max_size
    scale = min(max_w / clip.w, max_h / clip.h)
    return clip.resize(scale)


def _caption_chunks(script, duration, words_per_chunk=CAPTION_CHUNK_WORDS):
    """Split the script into short on-screen caption chunks, spaced evenly
    across the clip's duration.

    This is a rough approximation, not real per-word timing: the mouth
    timeline is audio-loudness-driven (see narrator_script.py) and doesn't
    carry word boundaries, so there's no ground truth to align captions to
    more precisely without a forced aligner. Even spacing across the known
    total duration is close enough for a caption banner, just not
    frame-accurate to the words as spoken.
    """
    words = script.split()
    if not words:
        return []
    chunks = [
        " ".join(words[i:i + words_per_chunk])
        for i in range(0, len(words), words_per_chunk)
    ]
    per_chunk = duration / len(chunks)
    return [
        (chunk, index * per_chunk, (index + 1) * per_chunk)
        for index, chunk in enumerate(chunks)
    ]


def _caption_timeline(manifest, duration):
    aligned = list(manifest.get("word_timeline") or [])
    if aligned:
        return [
            (str(item["word"]).strip(), float(item["start"]), min(duration, float(item["end"])))
            for item in aligned
            if str(item.get("word") or "").strip() and float(item.get("end", 0)) > float(item.get("start", 0))
        ]
    return _caption_chunks(manifest["script"], duration)


def _caption_frame(size, text, center_y, out_path, fill=(238, 44, 44), font_size=64):
    """Render a full-canvas-sized transparent image with the caption
    baked in at an absolute position, matching how _label_frame/
    _intro_frame in battle_engine.py already work -- the caller places the
    *whole* image at (0, 0) rather than separately positioning it, since a
    canvas-sized image plus a non-zero set_position() double-offsets."""
    width, height = size
    scale = width / 1080
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _font(int(font_size * scale))
    wrapped = _wrap(draw, text.upper(), font, int(width * 0.85), max_lines=2)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=int(10 * scale))
    draw.multiline_text(
        (width / 2 - (bbox[2] - bbox[0]) / 2, center_y - (bbox[3] - bbox[1]) / 2),
        wrapped, font=font, fill=fill, spacing=int(10 * scale),
        align="center", stroke_width=max(2, int(3 * scale)), stroke_fill=(0, 0, 0),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def _sentence_boundaries_from_pauses(word_timeline, duration):
    """Split the clip wherever there's a real pause of at least
    SENTENCE_PAUSE_THRESHOLD_SECONDS between two words -- a much more
    reliable "end of sentence" signal than counting words per sentence
    against the script text, since the word_timeline's own tokenization
    doesn't match a naive text split (numbers/decimals routinely come back
    as several separate word entries)."""
    if not word_timeline:
        return None
    cuts = [0.0]
    for prev, nxt in zip(word_timeline, word_timeline[1:]):
        gap = float(nxt["start"]) - float(prev["end"])
        if gap >= SENTENCE_PAUSE_THRESHOLD_SECONDS:
            cuts.append(float(prev["end"]) + gap / 2)
    cuts.append(duration)
    return list(zip(cuts, cuts[1:]))


def _pose_intervals(manifest, duration):
    word_timeline = list(manifest.get("word_timeline") or [])
    sentence_bounds = _sentence_boundaries_from_pauses(word_timeline, duration)
    if not sentence_bounds:
        # No real word timing to find pauses in -- fall back to a fixed
        # clock so there's still some pose variety instead of one static
        # pose for the whole clip.
        sentence_bounds = []
        t = 0.0
        while t < duration:
            end = min(duration, t + POSE_FALLBACK_SEGMENT_SECONDS)
            sentence_bounds.append((t, end))
            t = end
    return [
        (start, end, POSE_CYCLE[i % len(POSE_CYCLE)])
        for i, (start, end) in enumerate(sentence_bounds)
    ]


def _blink_intervals(duration):
    intervals = []
    t = BLINK_START_SECONDS
    while t < duration:
        end = min(duration, t + BLINK_DURATION_SECONDS)
        intervals.append((t, end, "blink"))
        t += BLINK_INTERVAL_SECONDS
    return intervals


def _value_at(intervals, t, default):
    for start, end, value in intervals:
        if start <= t < end:
            return value
    return default


def _merged_boundaries(interval_lists, duration):
    bounds = {0.0, duration}
    for intervals in interval_lists:
        for start, end, _ in intervals:
            bounds.add(min(duration, max(0.0, start)))
            bounds.add(min(duration, max(0.0, end)))
    return sorted(bounds)


def _media_zone_geometry(size):
    """Pixel geometry for the headline/media/caption stack.

    Returns (headline_center_y, media_box, caption_center_y) where
    media_box is (x, y, w, h) -- the inset box the car media is fit and
    centered into, leaving a visible margin on every edge instead of
    spanning the full band.
    """
    headline_h = size[1] * HEADLINE_ZONE_RATIO
    caption_h = size[1] * CAPTION_ZONE_RATIO
    band_h = size[1] * TOP_STACK_RATIO - headline_h - caption_h
    media_w = size[0] * (1 - 2 * MEDIA_INSET_RATIO)
    media_h = band_h * (1 - 2 * MEDIA_INSET_RATIO)
    media_x = (size[0] - media_w) / 2
    media_y = headline_h + band_h * MEDIA_INSET_RATIO
    headline_center_y = headline_h / 2
    caption_center_y = headline_h + band_h + caption_h / 2
    return headline_center_y, (media_x, media_y, media_w, media_h), caption_center_y


def _narrator_segments(manifest, sprites, duration):
    """Build the (start, end, pose, sprite_file) list for one merged mouth/
    pose/blink timeline -- split out from _narrator_track so the pose-change
    detection driving crossfades (see _narrator_track) has a plain list to
    walk instead of re-deriving it from clips."""
    mouth_timeline = list(manifest.get("mouth_timeline") or [])
    if not mouth_timeline:
        mouth_timeline = [{"start": 0, "end": duration, "mouth": "closed"}]
    elif mouth_timeline[-1]["mouth"] == "closed":
        # A nicer closing beat than trailing off on a flat mouth -- the
        # last silence in the clip is almost always the sign-off pause.
        mouth_timeline[-1] = {**mouth_timeline[-1], "mouth": "smile"}
    mouth_intervals = [(entry["start"], entry["end"], entry["mouth"]) for entry in mouth_timeline]

    pose_intervals = _pose_intervals(manifest, duration)
    blink_intervals = _blink_intervals(duration)
    boundaries = _merged_boundaries([mouth_intervals, pose_intervals, blink_intervals], duration)

    segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        if end - start < 0.005:
            continue
        mid = (start + end) / 2
        mouth = _value_at(mouth_intervals, mid, "closed")
        pose = _value_at(pose_intervals, mid, POSE_CYCLE[0])
        eyes = _value_at(blink_intervals, mid, "open")
        sprite_file = sprites["sprites"].get(f"{mouth}_{eyes}_{pose}") or sprites["sprites"].get(f"{mouth}_{eyes}")
        if not sprite_file:
            continue
        segments.append({"start": start, "end": end, "pose": pose, "sprite": sprite_file})
    return segments


def _narrator_track(manifest, sprites, size, duration):
    """One ImageClip per merged mouth/pose/blink segment, sprite-swapped,
    with a short crossfade wherever the *pose* changes (sentence to
    sentence) so that switch reads as a transition into the new stance --
    mouth/blink changes within the same pose stay instant cuts, since
    crossfading those would blur the lipsync."""
    max_h = int(size[1] * (1 - TOP_STACK_RATIO))
    segments = _narrator_segments(manifest, sprites, duration)

    if not segments:
        fallback = next(iter(sprites["sprites"].values()))
        track = ImageClip(str(SPRITES_DIR / fallback)).set_duration(duration)
        return _fit_content(track, (size[0], max_h))

    clips = []
    current_time = 0.0
    for index, seg in enumerate(segments):
        seg_duration = seg["end"] - seg["start"]
        next_seg = segments[index + 1] if index + 1 < len(segments) else None
        # The *outgoing* clip needs to keep playing through the overlap
        # window so there's something for the next clip to fade in over --
        # extending its own duration into that window (not just starting
        # the next clip early) is what actually avoids a blank gap where
        # the character flashes out of existence mid-transition.
        transition_out = (
            min(POSE_TRANSITION_SECONDS, seg_duration * 0.4)
            if next_seg and next_seg["pose"] != seg["pose"] else 0.0
        )
        transition_in = (
            min(POSE_TRANSITION_SECONDS, seg_duration * 0.4)
            if index > 0 and seg["pose"] != segments[index - 1]["pose"] else 0.0
        )
        clip = ImageClip(str(SPRITES_DIR / seg["sprite"])).set_duration(seg_duration + transition_out)
        if transition_in > 0.01:
            clip = clip.crossfadein(transition_in)
        clips.append(clip.set_start(current_time))
        current_time += seg_duration

    frame_size = clips[0].size
    track = CompositeVideoClip(clips, size=frame_size).set_duration(duration)
    return _fit_content(track, (size[0], max_h))


def _scene_time_boundaries(scenes, word_timeline, duration):
    """Real per-scene (start, end) times, one per scene, derived from each
    scene's own "narration" word span walked cumulatively against the
    actual word_timeline -- not an even split of total duration, which has
    no relation to how long each scene's beat actually took to say (that
    mismatch is why a named rival car's photo could show up at the end of
    the video instead of during the sentence that names it, and why
    headlines drifted out of sync with their own beat).

    The cut between two scenes lands at the midpoint of the pause between
    the first scene's last word and the second's first word (mirroring
    _sentence_boundaries_from_pauses), not simply at the previous scene's
    end -- assigning a whole silent gap entirely to whichever scene comes
    next would still leave a rival scene's display window starting long
    before it's actually spoken, just less extremely than an even split.

    Falls back to an even split only when there's no real word_timeline to
    walk (estimated caption timing).
    """
    if not scenes:
        return []
    if not word_timeline:
        n = len(scenes)
        return [(duration * i / n, duration * (i + 1) / n) for i in range(n)]

    spans = []
    word_index = 0
    for scene in scenes:
        word_count = len(str(scene.get("narration") or "").split())
        chunk = word_timeline[word_index:word_index + word_count]
        if chunk:
            spans.append((float(chunk[0]["start"]), float(chunk[-1]["end"])))
        else:
            # Ran out of real words (a mismatch between narration text and
            # the transcribed audio) -- keep the previous scene's span
            # rather than collapsing to a zero-length window.
            spans.append(spans[-1] if spans else (0.0, 0.0))
        word_index += word_count

    cut_points = [0.0]
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        cut_points.append(prev_end + (next_start - prev_end) / 2 if next_start > prev_end else prev_end)
    cut_points.append(duration)
    return list(zip(cut_points, cut_points[1:]))


def _car_track(media_paths, box_size, duration, boundaries=None):
    """Renders the car media into a box of exactly box_size -- the caller
    positions that box within the inset media band (see
    _media_zone_geometry), so this only needs to fit/center content and
    animate it within its own bounds. `boundaries`, one (start, end) pair
    per media item, times each item to when its own scene is actually
    spoken; an even split across `duration` is only a fallback for when
    real timing isn't available."""
    box_w, box_h = box_size
    if not media_paths:
        return ColorClip(size=(box_w, box_h), color=(255, 255, 255)).set_duration(duration)

    if not boundaries or len(boundaries) != len(media_paths):
        per_media = max(1.0, duration / len(media_paths))
        boundaries = [(i * per_media, (i + 1) * per_media) for i in range(len(media_paths))]

    clips = []
    for index, path in enumerate(media_paths):
        seg_start, seg_end = boundaries[index]
        per_media = max(0.5, seg_end - seg_start)
        path = Path(path)
        if path.suffix.lower() in {".mp4", ".mov", ".webm"}:
            raw = VideoFileClip(str(path))
            clip = raw.subclip(0, min(per_media, raw.duration))
        else:
            clip = ImageClip(str(path)).set_duration(per_media)
        fitted = _fit_content(clip, (box_w, box_h))
        # Small vertical float prevents stills from looking pinned in place;
        # the first car also enters from the left like the format reference.
        base_x = (box_w - fitted.w) / 2
        base_y = (box_h - fitted.h) / 2
        moving = fitted.set_position(lambda t, i=index, x=base_x, y=base_y: (
            x - max(0, 1 - t / 0.55) * box_w if i == 0 else x,
            y + 4 * math.sin(t * 1.7),
        ))
        clips.append(CompositeVideoClip([
            ColorClip(size=(box_w, box_h), color=(255, 255, 255)).set_duration(per_media),
            moving,
        ], size=(box_w, box_h)).set_duration(per_media))

    track = concatenate_videoclips(clips, method="compose")
    # concatenate_videoclips' total can drift slightly from `duration`
    # (per-media rounding, a short final source clip) -- clamp explicitly
    # so the car track and narrator track never fall out of sync.
    return track.set_duration(duration)


def _apply_body_sway(clip):
    """Smooth, continuous idle lean -- a per-frame rotation instead of
    baking several lean angles into discrete sprites, which read as a
    jerky snap between pictures rather than a smooth sway."""
    def angle(t):
        return BODY_SWAY_DEGREES * math.sin(2 * math.pi * t / BODY_SWAY_PERIOD_SECONDS)
    return clip.rotate(angle, expand=False)


def render_narrator_video(car_media_paths, manifest, output_path):
    size = CANVAS
    sprites = _load_sprites_manifest()
    output_path = Path(output_path)

    audio = AudioFileClip(manifest["audio_path"])
    duration = audio.duration

    narrator_clip = _apply_body_sway(_narrator_track(manifest, sprites, size, duration))
    narrator_x = (size[0] - narrator_clip.w) / 2
    narrator_y = size[1] - narrator_clip.h
    narrator_positioned = narrator_clip.set_position(
        lambda t: (narrator_x + 3 * math.sin(t * 1.15), narrator_y + 3 * math.sin(t * 1.65))
    )

    headline_center_y, media_box, caption_center_y = _media_zone_geometry(size)
    media_x, media_y, media_w, media_h = media_box
    scenes = list(manifest.get("scenes") or [])
    word_timeline = list(manifest.get("word_timeline") or [])
    scene_boundaries = _scene_time_boundaries(scenes, word_timeline, duration)
    car_clip = _car_track(car_media_paths, (int(media_w), int(media_h)), duration, scene_boundaries)
    car_positioned = car_clip.set_position((media_x, media_y))

    # The caption band sits below the picture, not on top of it -- distinct
    # from the headline band above the picture.
    caption_clips = [
        ImageClip(str(_caption_frame_path(output_path, text, start, int(caption_center_y), size)))
        .set_start(start).set_duration(end - start).set_position((0, 0))
        for text, start, end in _caption_timeline(manifest, duration)
    ]
    headline_clips = []
    for index, scene in enumerate(scenes):
        headline = str(scene.get("headline") or "").strip()
        if not headline:
            continue
        start, end = scene_boundaries[index] if index < len(scene_boundaries) else (0.0, duration)
        frame_path = output_path.parent / "_frames" / f"headline-{index}.png"
        _caption_frame(size, headline, int(headline_center_y), frame_path, fill=(255, 214, 64), font_size=92)
        headline_clips.append(
            ImageClip(str(frame_path)).set_start(start).set_duration(end - start).set_position((0, 0))
        )

    background = ColorClip(size=size, color=(255, 255, 255)).set_duration(duration)
    video = CompositeVideoClip(
        [background, car_positioned, *headline_clips, *caption_clips, narrator_positioned], size=size
    ).set_duration(duration).set_audio(audio)

    video.write_videofile(
        str(output_path), fps=24, codec="libx264", audio_codec="aac", preset="medium", threads=4,
    )
    return output_path


def _caption_frame_path(output_path, text, start, center_y, size):
    frame_path = output_path.parent / "_frames" / f"caption-{round(start, 2)}.png"
    _caption_frame(size, text, center_y, frame_path)
    return frame_path


def main():
    parser = argparse.ArgumentParser(description="Render a talking-narrator car video from a narration manifest.")
    parser.add_argument("--manifest", required=True, type=Path, help="manifest.json from narrator_script.py")
    parser.add_argument("--media", nargs="*", default=[], help="Car photo/video paths to show up top, in order.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    output_path = render_narrator_video(args.media, manifest, args.output)
    print(output_path)


if __name__ == "__main__":
    main()
