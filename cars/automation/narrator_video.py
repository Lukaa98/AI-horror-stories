"""Render stage for the talking-narrator format: car media up top, the
narrator character talking underneath, captions in between -- driven by
the manifest.json that narrator_script.py produces (script text, narration
audio, and an audio-loudness mouth timeline).

The narrator itself is not rendered live; export-sprites.js pre-renders it
to a fixed set of transparent PNGs (one per mouth x eyes combination) and
this just flips between them per mouth_timeline segment, like a flipbook,
rather than driving a browser for every output frame.
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw
from moviepy.editor import (
    AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip,
    concatenate_videoclips,
)

from generate_sample import ROOT, CANVAS, _font, _wrap

SPRITES_DIR = ROOT / "narrator" / "sprites"
# Car media zone height as a fraction of the canvas -- kept smaller than a
# near-half split so the narrator (and the caption sitting between the two)
# reads as the focal point instead of the media dominating the frame.
MAX_CAR_RATIO = 0.34
CAPTION_CHUNK_WORDS = 1


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


def _narrator_track(manifest, sprites, size, duration):
    """One ImageClip per mouth_timeline segment, sprite-swapped -- eyes
    default to open throughout since the timeline only carries mouth
    state, not blink timing (a later pass could add that)."""
    max_h = int(size[1] * (1 - MAX_CAR_RATIO))
    timeline = list(manifest.get("mouth_timeline") or [])
    if not timeline:
        timeline = [{"start": 0, "end": duration, "mouth": "closed"}]
    elif timeline[-1]["mouth"] == "closed":
        # A nicer closing beat than trailing off on a flat mouth -- the
        # last silence in the clip is almost always the sign-off pause.
        timeline[-1] = {**timeline[-1], "mouth": "smile"}

    segments = []
    for entry in timeline:
        sprite_key = f"{entry['mouth']}_open"
        sprite_file = sprites["sprites"].get(sprite_key)
        if not sprite_file:
            continue
        seg_duration = max(0.01, entry["end"] - entry["start"])
        segments.append(ImageClip(str(SPRITES_DIR / sprite_file)).set_duration(seg_duration))

    if not segments:
        segments = [ImageClip(str(SPRITES_DIR / sprites["sprites"]["closed_open"])).set_duration(duration)]

    track = concatenate_videoclips(segments, method="compose")
    return _fit_content(track, (size[0], max_h))


def _car_track(media_paths, size, duration):
    max_h = int(size[1] * MAX_CAR_RATIO)
    if not media_paths:
        return ColorClip(size=(size[0], max_h), color=(255, 255, 255)).set_duration(duration)

    per_media = max(1.0, duration / len(media_paths))
    clips = []
    for index, path in enumerate(media_paths):
        path = Path(path)
        if path.suffix.lower() in {".mp4", ".mov", ".webm"}:
            raw = VideoFileClip(str(path))
            clip = raw.subclip(0, min(per_media, raw.duration))
        else:
            clip = ImageClip(str(path)).set_duration(per_media)
        fitted = _fit_content(clip, (size[0], max_h))
        # Small vertical float prevents stills from looking pinned in place;
        # the first car also enters from the left like the format reference.
        base_x = (size[0] - fitted.w) / 2
        base_y = (max_h - fitted.h) / 2
        moving = fitted.set_position(lambda t, i=index, x=base_x, y=base_y: (
            x - max(0, 1 - t / 0.55) * size[0] if i == 0 else x,
            y + 4 * __import__("math").sin(t * 1.7),
        ))
        clips.append(CompositeVideoClip([
            ColorClip(size=(size[0], max_h), color=(255, 255, 255)).set_duration(per_media),
            moving,
        ], size=(size[0], max_h)).set_duration(per_media))

    track = concatenate_videoclips(clips, method="compose")
    # concatenate_videoclips' total can drift slightly from `duration`
    # (per-media rounding, a short final source clip) -- clamp explicitly
    # so the car track and narrator track never fall out of sync.
    return track.set_duration(duration)


def render_narrator_video(car_media_paths, manifest, output_path):
    size = CANVAS
    sprites = _load_sprites_manifest()
    output_path = Path(output_path)

    audio = AudioFileClip(manifest["audio_path"])
    duration = audio.duration

    narrator_clip = _narrator_track(manifest, sprites, size, duration)
    narrator_x = (size[0] - narrator_clip.w) / 2
    narrator_y = size[1] - narrator_clip.h
    narrator_positioned = narrator_clip.set_position(
        lambda t: (narrator_x + 3 * __import__("math").sin(t * 1.15), narrator_y + 3 * __import__("math").sin(t * 1.65))
    )

    car_clip = _car_track(car_media_paths, size, duration)
    car_positioned = car_clip.set_position(("center", 0))

    # Sit on the lower part of the car-media zone (like "ENGINE" /
    # "REAR WHEEL DRIVE" captions in the reference channel), not in the
    # gap between car and narrator -- there isn't room there once the
    # narrator occupies the rest of the frame.
    caption_center_y = int(size[1] * MAX_CAR_RATIO) - 90
    caption_clips = [
        ImageClip(str(_caption_frame_path(output_path, text, start, caption_center_y, size)))
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
        _caption_frame(size, headline, int(size[1] * 0.09), frame_path, fill=(255, 214, 64), font_size=92)
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
