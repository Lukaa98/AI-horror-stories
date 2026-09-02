"""Render stage for the talking-narrator format: car media up top, the
narrator character talking underneath, captions in between -- driven by
the manifest.json that narrator_script.py produces (script text, narration
audio, and an audio-loudness mouth timeline).

The narrator itself is not rendered live; export-sprites.js pre-renders it
to a fixed set of transparent PNGs (one per mouth x eyes x flicker-pose
combination) and this flips between them like a flipbook: mouth state
follows the audio-loudness mouth_timeline, the flicker pose follows its own
fast fixed-clock timeline, and eyes follow a blink timeline, rather than
driving a browser for every output frame. The one exception is the body's
idle lean, which is smooth and continuous in the interactive rig -- baking
that into discrete sprites and cycling through them made it look like a
jerky snap between pictures instead of a smooth sway, so it's applied here
as a continuous per-frame rotation instead (_apply_body_sway).
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
TOP_STACK_RATIO = 0.40
HEADLINE_ZONE_RATIO = 0.095
CAPTION_ZONE_RATIO = 0.075
# Margin on every edge of the media's own band -- the picture is inset
# instead of stretched edge-to-edge, so it reads as a framed photo rather
# than a banner. Trimmed from 0.125 to grow the picture itself by ~1/5
# ((1 - 2*0.05) / (1 - 2*0.125) = 1.2) while keeping a visible, if
# thinner, margin on every edge.
MEDIA_INSET_RATIO = 0.05
CAPTION_CHUNK_WORDS = 1

# The sprite flipbook only ever varied by mouth state, so the rendered
# character read as "a stuck body with a moving mouth" even though the
# interactive rig (narrator-rig.html) has idle body sway, a line-flicker,
# arm sway, and brow lift. export-sprites.js now bakes the fast, small
# hand-drawn "redraw" flicker (line weight + a tiny arm/brow twitch) into 2
# fixed poses (steady/jolt); cycling through them here on a fixed clock --
# the same trick the mouth timeline already uses, just time-driven instead
# of loudness-driven -- gets that motion into the actual video instead of
# only ever appearing in the live rig preview. The slower body-wide lean is
# handled separately below (_apply_body_sway) as a continuous rotation,
# since baking that into the same discrete cycle read as jerky rather than
# a smooth sway.
POSE_CYCLE = ["steady", "jolt"]
POSE_SEGMENT_SECONDS = 0.24
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


def _pose_intervals(duration):
    intervals = []
    t, i = 0.0, 0
    while t < duration:
        end = min(duration, t + POSE_SEGMENT_SECONDS)
        intervals.append((t, end, POSE_CYCLE[i % len(POSE_CYCLE)]))
        t = end
        i += 1
    return intervals


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


def _narrator_track(manifest, sprites, size, duration):
    """One ImageClip per merged mouth/pose/blink segment, sprite-swapped.

    Mouth state comes from the audio-loudness mouth_timeline; pose and
    blink are driven by their own fixed-clock timelines (see POSE_CYCLE /
    BLINK_* above) since nothing in the manifest carries that timing. The
    three timelines are merged into one set of cut points so each resulting
    segment has one unambiguous (mouth, eyes, pose) sprite for its whole
    duration.
    """
    max_h = int(size[1] * (1 - TOP_STACK_RATIO))
    mouth_timeline = list(manifest.get("mouth_timeline") or [])
    if not mouth_timeline:
        mouth_timeline = [{"start": 0, "end": duration, "mouth": "closed"}]
    elif mouth_timeline[-1]["mouth"] == "closed":
        # A nicer closing beat than trailing off on a flat mouth -- the
        # last silence in the clip is almost always the sign-off pause.
        mouth_timeline[-1] = {**mouth_timeline[-1], "mouth": "smile"}
    mouth_intervals = [(entry["start"], entry["end"], entry["mouth"]) for entry in mouth_timeline]

    pose_intervals = _pose_intervals(duration)
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
        segments.append(ImageClip(str(SPRITES_DIR / sprite_file)).set_duration(end - start))

    if not segments:
        fallback = next(iter(sprites["sprites"].values()))
        segments = [ImageClip(str(SPRITES_DIR / fallback)).set_duration(duration)]

    track = concatenate_videoclips(segments, method="compose")
    return _fit_content(track, (size[0], max_h))


def _car_track(media_paths, box_size, duration):
    """Renders the car media into a box of exactly box_size -- the caller
    positions that box within the inset media band (see
    _media_zone_geometry), so this only needs to fit/center content and
    animate it within its own bounds."""
    box_w, box_h = box_size
    if not media_paths:
        return ColorClip(size=(box_w, box_h), color=(255, 255, 255)).set_duration(duration)

    per_media = max(1.0, duration / len(media_paths))
    clips = []
    for index, path in enumerate(media_paths):
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
    car_clip = _car_track(car_media_paths, (int(media_w), int(media_h)), duration)
    car_positioned = car_clip.set_position((media_x, media_y))

    # The caption band sits below the picture, not on top of it -- distinct
    # from the headline band above the picture.
    caption_clips = [
        ImageClip(str(_caption_frame_path(output_path, text, start, int(caption_center_y), size)))
        .set_start(start).set_duration(end - start).set_position((0, 0))
        for text, start, end in _caption_timeline(manifest, duration)
    ]
    scenes = list(manifest.get("scenes") or [])
    scene_duration = duration / len(scenes) if scenes else duration
    headline_clips = []
    for index, scene in enumerate(scenes):
        headline = str(scene.get("headline") or "").strip()
        if not headline:
            continue
        start = index * scene_duration
        end = min(duration, (index + 1) * scene_duration)
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
