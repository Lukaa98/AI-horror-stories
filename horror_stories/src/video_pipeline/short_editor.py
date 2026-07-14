import math
import os
import random
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# MoviePy 1.x still references Image.ANTIALIAS, which Pillow 10 removed.
# Keep the renderer compatible with Python 3.12-friendly Pillow wheels.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from moviepy.editor import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    concatenate_videoclips,
)

FAST_MODE = os.getenv("FAST_MODE", "1") == "1"
CANVAS_SIZE = (540, 960) if FAST_MODE else (1080, 1920)
FPS = 12 if FAST_MODE else 24
DEFAULT_FONT_CANDIDATES = [
    os.getenv("CAPTION_FONT_PATH", ""),
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]


def _load_font(size):
    for font_path in DEFAULT_FONT_CANDIDATES:
        if not font_path:
            continue
        try:
            return ImageFont.truetype(font_path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scene_durations(scenes, target_duration):
    weights = []
    stage_bonus = {
        "setup": 1.0,
        "escalation": 1.08,
        "payoff": 1.1,
        "cta": 0.9,
        "hook": 1.2,
        "performance": 1.05,
        "interior": 1.0,
        "roof": 1.0,
        "detail": 1.0,
        "opinion": 0.95,
        "score": 1.0,
    }
    for scene in scenes:
        word_count = max(4, len(scene["narration"].split()))
        weights.append(word_count * stage_bonus.get(scene["stage"], 1.0))

    total_weight = sum(weights)
    durations = [target_duration * weight / total_weight for weight in weights]
    return durations


def _prepare_vertical_image(image_path):
    image = Image.open(image_path).convert("RGB")
    canvas_w, canvas_h = CANVAS_SIZE

    blur_radius = 10 if FAST_MODE else 18
    bg = image.copy().resize(CANVAS_SIZE, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(blur_radius))
    bg = bg.point(lambda value: int(value * 0.45))

    fg = image.copy()
    fg.thumbnail((canvas_w, canvas_h), Image.Resampling.LANCZOS)

    canvas = bg
    fg_x = (canvas_w - fg.width) // 2
    fg_y = (canvas_h - fg.height) // 2
    canvas.paste(fg, (fg_x, fg_y))

    vignette = Image.new("L", CANVAS_SIZE, 0)
    draw = ImageDraw.Draw(vignette)
    draw.ellipse((-180, -80, canvas_w + 180, canvas_h + 160), fill=215)
    vignette = vignette.filter(ImageFilter.GaussianBlur(90 if FAST_MODE else 180))
    canvas = Image.composite(canvas, Image.new("RGB", CANVAS_SIZE, (8, 8, 12)), vignette)
    return np.array(canvas)


def _make_motion_clip(image_path, duration, seed, style_override=None):
    rng = random.Random(seed)
    prepared = _prepare_vertical_image(image_path)

    motion_styles = ["push_in", "drift_left", "drift_right", "rise", "pull_back", "float"]
    style = style_override if style_override in motion_styles else motion_styles[seed % len(motion_styles)]

    if style == "push_in":
        start_scale = 1.01 + rng.random() * 0.02
        end_scale = start_scale + 0.08 + rng.random() * 0.03
        start_x, end_x = rng.randint(-28, 28), rng.randint(-46, 46)
        start_y, end_y = rng.randint(-36, 24), rng.randint(-72, 42)
    elif style == "pull_back":
        end_scale = 1.01 + rng.random() * 0.02
        start_scale = end_scale + 0.07 + rng.random() * 0.02
        start_x, end_x = rng.randint(-52, 52), rng.randint(-18, 18)
        start_y, end_y = rng.randint(-80, 40), rng.randint(-28, 22)
    elif style == "drift_left":
        start_scale = 1.05 + rng.random() * 0.02
        end_scale = start_scale + 0.02
        start_x, end_x = 18, -72
        start_y, end_y = rng.randint(-44, 8), rng.randint(-62, 26)
    elif style == "drift_right":
        start_scale = 1.05 + rng.random() * 0.02
        end_scale = start_scale + 0.02
        start_x, end_x = -18, 72
        start_y, end_y = rng.randint(-44, 8), rng.randint(-62, 26)
    elif style == "rise":
        start_scale = 1.04 + rng.random() * 0.02
        end_scale = start_scale + 0.04
        start_x, end_x = rng.randint(-18, 18), rng.randint(-30, 30)
        start_y, end_y = 24, -94
    else:
        start_scale = 1.03 + rng.random() * 0.02
        end_scale = start_scale + 0.03
        start_x, end_x = rng.randint(-22, 22), rng.randint(-38, 38)
        start_y, end_y = rng.randint(-32, 18), rng.randint(-54, 30)

    def scale_at(t):
        progress = min(1.0, max(0.0, t / duration))
        eased = 0.5 - 0.5 * math.cos(progress * math.pi)
        return start_scale + (end_scale - start_scale) * eased

    def position_at(t):
        progress = min(1.0, max(0.0, t / duration))
        eased = 0.5 - 0.5 * math.cos(progress * math.pi)
        x = start_x + ((end_x - start_x) * eased) - 40
        y = start_y + ((end_y - start_y) * eased) - 60
        return x, y

    return (
        ImageClip(prepared)
        .set_duration(duration)
        .resize(lambda t: scale_at(t))
        .set_position(lambda t: position_at(t))
    )


def _make_particle_clip(duration, seed, particle_count=None):
    rng = random.Random(seed)
    width, height = CANVAS_SIZE
    particle_count = particle_count or (10 if FAST_MODE else 28)
    particles = []
    for _ in range(particle_count):
        particles.append(
            {
                "x": rng.uniform(0, width),
                "y": rng.uniform(0, height),
                "radius": rng.uniform(2.0, 6.5),
                "speed_x": rng.uniform(-8, 8),
                "speed_y": rng.uniform(-26, -8),
                "alpha": rng.uniform(0.12, 0.28),
                "phase": rng.uniform(0, math.tau),
            }
        )

    def make_rgb(_t):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        return frame

    def make_mask(t):
        mask = Image.new("L", CANVAS_SIZE, 0)
        draw = ImageDraw.Draw(mask)
        for particle in particles:
            x = (particle["x"] + particle["speed_x"] * t + 18 * math.sin(t + particle["phase"])) % width
            y = (particle["y"] + particle["speed_y"] * t) % height
            radius = particle["radius"] + 0.8 * math.sin((t * 1.5) + particle["phase"])
            alpha = int(255 * particle["alpha"])
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=alpha)
        mask = mask.filter(ImageFilter.GaussianBlur(1 if FAST_MODE else 2))
        return np.array(mask).astype(np.float32) / 255.0

    rgb_clip = VideoClip(make_rgb, duration=duration)
    mask_clip = VideoClip(make_mask, ismask=True, duration=duration)
    return rgb_clip.set_mask(mask_clip).set_opacity(0.45)


def _make_gradient_overlay(duration):
    width, height = CANVAS_SIZE
    overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for index in range(height):
        top_alpha = int(110 * max(0, (220 - index) / 220))
        bottom_alpha = int(175 * max(0, (index - 1180) / 740))
        alpha = max(top_alpha, bottom_alpha)
        if alpha:
            draw.line((0, index, width, index), fill=(0, 0, 0, alpha))
    return ImageClip(np.array(overlay)).set_duration(duration)


def _make_scanline_overlay(duration):
    width, height = CANVAS_SIZE
    overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    spacing = 5 if FAST_MODE else 7
    alpha = 22 if FAST_MODE else 18
    for y in range(0, height, spacing):
        draw.line((0, y, width, y), fill=(110, 160, 180, alpha), width=1)
    return ImageClip(np.array(overlay)).set_duration(duration)


def _make_glitch_overlay(duration, seed):
    rng = random.Random(seed)
    width, height = CANVAS_SIZE
    flash_times = sorted(rng.uniform(0.15, max(0.2, duration - 0.2)) for _ in range(3))

    def make_frame(t):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        intensity = 0
        for flash_time in flash_times:
            distance = abs(t - flash_time)
            if distance < 0.08:
                intensity = max(intensity, int(140 * (1 - (distance / 0.08))))
        if intensity:
            for band in range(0, height, 48 if FAST_MODE else 64):
                if (band // (48 if FAST_MODE else 64)) % 2 == 0:
                    frame[band:band + 10, :, 1] = intensity // 2
                    frame[band:band + 10, :, 2] = intensity
        return frame

    def make_mask(t):
        mask = np.zeros((height, width), dtype=np.float32)
        for flash_time in flash_times:
            distance = abs(t - flash_time)
            if distance < 0.08:
                value = 0.24 * (1 - (distance / 0.08))
                mask[:, :] = np.maximum(mask, value)
        return mask

    rgb_clip = VideoClip(make_frame, duration=duration)
    mask_clip = VideoClip(make_mask, ismask=True, duration=duration)
    return rgb_clip.set_mask(mask_clip)


def _make_scene_overlays(scene, duration, seed):
    overlays = [_make_gradient_overlay(duration)]
    stage = scene.get("stage", "setup")

    if stage in {"setup", "escalation", "payoff", "performance", "interior", "roof", "detail", "score", "opinion"}:
        overlays.append(_make_scanline_overlay(duration).set_opacity(0.12 if FAST_MODE else 0.10))
    if stage in {"escalation", "payoff", "cta", "hook", "performance", "score", "opinion"}:
        overlays.append(_make_glitch_overlay(duration, seed + 41).set_opacity(0.45 if FAST_MODE else 0.35))
    return overlays


def _fit_text(draw, text, font, max_width):
    lines = []
    wrap_width = 24 if FAST_MODE else 22
    fallback_width = 19 if FAST_MODE else 18
    stroke_width = 2 if FAST_MODE else 3
    for candidate in textwrap.wrap(text, width=wrap_width):
        bbox = draw.textbbox((0, 0), candidate, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            lines.append(candidate)
        else:
            lines.extend(textwrap.wrap(candidate, width=fallback_width))
    return "\n".join(lines[:3])


def _make_caption_clip(text, duration):
    width, height = CANVAS_SIZE
    caption = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(caption)
    font = _load_font(34 if FAST_MODE else 62)
    wrapped = _fit_text(draw, text.upper(), font, width - 120)
    bbox = draw.multiline_textbbox(
        (0, 0),
        wrapped,
        font=font,
        spacing=6 if FAST_MODE else 8,
        align="center",
        stroke_width=2 if FAST_MODE else 3,
    )
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    panel_x1 = 80
    panel_x2 = width - 80
    panel_y1 = height - text_h - 230
    panel_y2 = height - 110
    draw.rounded_rectangle((panel_x1, panel_y1, panel_x2, panel_y2), radius=22 if FAST_MODE else 34, fill=(0, 0, 0, 105))

    text_x = (width - text_w) // 2
    text_y = panel_y1 + ((panel_y2 - panel_y1 - text_h) // 2) - 10
    draw.multiline_text(
        (text_x, text_y),
        wrapped,
        font=font,
        fill=(248, 244, 230, 255),
        align="center",
        spacing=6 if FAST_MODE else 8,
        stroke_width=2 if FAST_MODE else 3,
        stroke_fill=(0, 0, 0, 255),
    )
    return ImageClip(np.array(caption)).set_duration(duration)


def _split_subtitle_chunks(text):
    words = text.split()
    if not words:
        return []

    chunks = []
    current = []
    max_words = 8 if FAST_MODE else 10

    for word in words:
        current.append(word)
        if len(current) >= max_words or word.endswith((".", "!", "?", ",")):
            chunks.append(" ".join(current))
            current = []

    if current:
        chunks.append(" ".join(current))

    return chunks


def _make_subtitle_sequence(text, duration, start_time=0.0):
    chunks = _split_subtitle_chunks(text)
    if not chunks:
        return []

    weights = [max(1, len(chunk.split())) for chunk in chunks]
    total_weight = sum(weights)
    current_start = start_time
    clips = []
    for index, chunk in enumerate(chunks):
        chunk_duration = duration * (weights[index] / total_weight)
        gap = 0.04 if FAST_MODE else 0.03
        start = current_start
        visible_duration = max(0.1, chunk_duration - gap)
        clip = _make_caption_clip(chunk, visible_duration).set_start(start)
        clips.append(clip)
        current_start += chunk_duration
    return clips


def _make_scene_clip(image_path, scene, duration, index):
    base = ColorClip(CANVAS_SIZE, color=(8, 8, 10), duration=duration)
    edit_style = scene.get("edit_style") or {}
    motion = _make_motion_clip(image_path, duration, seed=(index * 97) + 13, style_override=edit_style.get("motion"))
    particles = _make_particle_clip(duration, seed=(index * 151) + 9)
    overlays = _make_scene_overlays(scene, duration, seed=(index * 191) + 5)

    return CompositeVideoClip(
        [base, motion, particles, *overlays],
        size=CANVAS_SIZE,
    ).set_duration(duration)


def _make_global_subtitle_clips(subtitles):
    clips = []
    for item in subtitles:
        start = max(0.0, float(item["start"]))
        end = max(start + 0.1, float(item["end"]))
        clips.extend(_make_subtitle_sequence(item["text"], end - start, start_time=start))
    return clips


def build_short_video(storyboard, image_paths, narration_path, output_path, subtitles=None):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    narration_clip = AudioFileClip(str(narration_path))
    durations = _scene_durations(storyboard["scenes"], narration_clip.duration)

    clips = []
    for index, (scene, image_path, duration) in enumerate(zip(storyboard["scenes"], image_paths, durations), start=1):
        clips.append(_make_scene_clip(str(image_path), scene, duration, index))

    boosted_narration = narration_clip.volumex(1.8)
    final_audio = CompositeAudioClip([boosted_narration]).set_duration(narration_clip.duration)
    subtitle_clips = _make_global_subtitle_clips(subtitles or [])

    base_video = concatenate_videoclips(clips, method="compose")
    final_video = CompositeVideoClip([base_video, *subtitle_clips], size=CANVAS_SIZE).set_audio(final_audio)
    final_video = final_video.set_duration(narration_clip.duration)
    final_video.write_videofile(
        str(output_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="ultrafast" if FAST_MODE else "medium",
        threads=4,
    )
    return output_path
