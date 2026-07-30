"""Render stage for a "startup sound battle": each approved car gets a
segment exactly as long as its own startup clip -- video on the top third
of the frame, its exterior photos cycling on the bottom two-thirds, labeled
with its rank and name. No narration; the cars' own sound is the content.
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw
from moviepy.editor import ColorClip, CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips

from generate_sample import ROOT, CANVAS, _font, _wrap

ROOT = ROOT  # re-exported for clarity
BATTLES_ROOT = ROOT / "cars" / "battles"
INTRO_SECONDS = 2.5
# Caps, not fixed zones: video and photo are stacked directly against each
# other (no gap between them) and the whole block is centered vertically, so
# any leftover space splits evenly above and below instead of each getting
# its own independent letterboxed zone (which doubled the visible black gap).
MAX_VIDEO_RATIO = 0.30
MAX_PHOTO_RATIO = 0.42
MIN_PHOTO_SECONDS = 1.0
MAX_PHOTO_SECONDS = 1.5


def _fit_content(clip, max_size):
    """Scale to fit within max_size, preserving aspect, no cropping and no
    padding -- unlike _fit_on_color, the returned clip's own w/h shrink to
    match its content so callers can stack multiple pieces tightly."""
    max_w, max_h = max_size
    scale = min(max_w / clip.w, max_h / clip.h)
    return clip.resize(scale)


def _label_frame(size, text, out_path):
    width, height = size
    scale = width / 1080
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _font(int(56 * scale))
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = int(24 * scale), int(14 * scale)
    box_top = int(20 * scale)
    draw.rounded_rectangle(
        (width / 2 - text_w / 2 - pad_x, box_top,
         width / 2 + text_w / 2 + pad_x, box_top + text_h + pad_y * 2),
        radius=int(14 * scale), fill=(0, 0, 0, 190),
    )
    draw.text(
        (width / 2 - text_w / 2, box_top + pad_y - bbox[1]),
        text, font=font, fill=(255, 249, 235),
        stroke_width=max(1, int(2 * scale)), stroke_fill=(0, 0, 0),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def _intro_frame(size, text, out_path):
    width, height = size
    scale = width / 1080
    canvas = Image.new("RGB", size, (10, 10, 12))
    draw = ImageDraw.Draw(canvas)
    font = _font(int(84 * scale))
    wrapped = _wrap(draw, text, font, int(width * 0.85), max_lines=4)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=int(14 * scale))
    draw.multiline_text(
        (width / 2 - (bbox[2] - bbox[0]) / 2, height / 2 - (bbox[3] - bbox[1]) / 2),
        wrapped, font=font, fill=(255, 204, 92), spacing=int(14 * scale),
        align="center", stroke_width=max(1, int(2 * scale)), stroke_fill=(0, 0, 0),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def _intro_clip(battle_dir, size, intro_question):
    frame_path = battle_dir / "_frames" / "intro.png"
    question = (intro_question or "Which one sounds best?").rstrip("?").upper() + "?"
    _intro_frame(size, f"{question}\nLET ME KNOW BELOW", frame_path)
    return ImageClip(str(frame_path)).set_duration(INTRO_SECONDS)


def _car_segment(battle_dir, car, size):
    width, height = size
    max_video_h = int(height * MAX_VIDEO_RATIO)
    max_photo_h = int(height * MAX_PHOTO_RATIO)

    clip_path = battle_dir / car["clip_path"]
    raw_video = VideoFileClip(str(clip_path))
    duration = min(float(car.get("clip_duration") or raw_video.duration), raw_video.duration)
    video_clip = _fit_content(raw_video.subclip(0, duration), (width, max_video_h))

    photo_paths = [battle_dir / p for p in car["photos"]]
    per_photo = max(MIN_PHOTO_SECONDS, min(MAX_PHOTO_SECONDS, duration / max(1, len(photo_paths))))
    photo_count = max(1, min(len(photo_paths), round(duration / per_photo)))
    chosen_photos = photo_paths[:photo_count]
    per_photo = duration / len(chosen_photos)
    # Every photo is boxed to the same fixed (width, max_photo_h) size so the
    # stacked block's total height stays constant across cuts -- otherwise
    # each photo's own aspect ratio would shift the video's position and the
    # composition would jitter every second or so.
    photo_clips = [
        _fit_content(ImageClip(str(path)).set_duration(per_photo), (width, max_photo_h))
        .on_color(size=(width, max_photo_h), color=(0, 0, 0), pos=("center", "center"))
        for path in chosen_photos
    ]
    photos_track = concatenate_videoclips(photo_clips, method="compose")

    content_h = video_clip.h + max_photo_h
    top_margin = max(0, (height - content_h) // 2)
    background = ColorClip(size=size, color=(0, 0, 0)).set_duration(duration)
    video_positioned = video_clip.set_position(("center", top_margin))
    photos_positioned = photos_track.set_position(("center", top_margin + video_clip.h))

    label_path = battle_dir / "_frames" / f"label-{car['index']}.png"
    _label_frame(size, f"{car['index']}. {car['label']}", label_path)
    label_clip = ImageClip(str(label_path)).set_duration(duration).set_position((0, 0))

    return CompositeVideoClip(
        [background, video_positioned, photos_positioned, label_clip], size=size
    ).set_duration(duration)


def render_battle_video(battle_dir, data, output_filename="battle_short.mp4"):
    size = CANVAS
    approved_cars = [car for car in data["cars"] if car.get("approved") and car.get("clip_path")]
    if len(approved_cars) < 2:
        raise SystemExit(
            f"Only {len(approved_cars)} of {len(data['cars'])} cars have an approved exterior startup "
            "clip; need at least 2 to render a battle."
        )

    clips = [_intro_clip(battle_dir, size, data.get("intro_question"))]
    clips.extend(_car_segment(battle_dir, car, size) for car in approved_cars)

    video = concatenate_videoclips(clips, method="compose")
    output_path = battle_dir / output_filename
    video.write_videofile(
        str(output_path), fps=24, codec="libx264", audio_codec="aac",
        preset="medium", threads=4,
    )
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render a startup-sound battle video from battle.json.")
    parser.add_argument("--battle-id", required=True)
    parser.add_argument("--output-name", default="battle_short.mp4")
    args = parser.parse_args()

    battle_dir = BATTLES_ROOT / args.battle_id
    battle_path = battle_dir / "battle.json"
    if not battle_path.exists():
        raise SystemExit(f"No battle.json found at {battle_path} -- run battle_request.py first.")
    data = json.loads(battle_path.read_text(encoding="utf-8"))

    render_battle_video(battle_dir, data, output_filename=args.output_name)

    data["status"] = "video_generated"
    data["latest_render"] = {"filename": args.output_name}
    battle_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(battle_dir / args.output_name)


if __name__ == "__main__":
    main()
