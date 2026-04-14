import math
import random
import textwrap
from pathlib import Path

import numpy as np
from moviepy.editor import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    concatenate_videoclips,
)
from PIL import Image, ImageDraw, ImageFilter, ImageFont

CANVAS_SIZE = (1080, 1920)
FPS = 24
FONT_PATH = "C:/Windows/Fonts/arialbd.ttf"


def _load_font(size):
    return ImageFont.truetype(FONT_PATH, size=size)


def _scene_durations(scenes, target_duration):
    weights = []
    stage_bonus = {
        "hook": 1.15,
        "setup": 1.0,
        "escalation": 1.08,
        "payoff": 1.1,
        "cta": 0.9,
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

    bg = image.copy().resize(CANVAS_SIZE, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(18))
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
    vignette = vignette.filter(ImageFilter.GaussianBlur(180))
    canvas = Image.composite(canvas, Image.new("RGB", CANVAS_SIZE, (8, 8, 12)), vignette)
    return np.array(canvas)


def _make_motion_clip(image_path, duration, seed):
    rng = random.Random(seed)
    prepared = _prepare_vertical_image(image_path)

    start_scale = 1.02 + rng.random() * 0.03
    end_scale = start_scale + 0.07 + rng.random() * 0.03
    drift_x = rng.randint(-36, 36)
    drift_y = rng.randint(-64, 64)

    def scale_at(t):
        return start_scale + (end_scale - start_scale) * (t / duration)

    def position_at(t):
        progress = t / duration
        return drift_x * progress - 40, drift_y * progress - 60

    return (
        ImageClip(prepared)
        .set_duration(duration)
        .resize(lambda t: scale_at(t))
        .set_position(lambda t: position_at(t))
    )


def _make_particle_clip(duration, seed, particle_count=28):
    rng = random.Random(seed)
    width, height = CANVAS_SIZE
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
        mask = mask.filter(ImageFilter.GaussianBlur(2))
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


def _fit_text(draw, text, font, max_width):
    lines = []
    for candidate in textwrap.wrap(text, width=20):
        bbox = draw.textbbox((0, 0), candidate, font=font, stroke_width=4)
        if bbox[2] - bbox[0] <= max_width:
            lines.append(candidate)
        else:
            lines.extend(textwrap.wrap(candidate, width=16))
    return "\n".join(lines[:3])


def _make_caption_clip(text, duration):
    width, height = CANVAS_SIZE
    caption = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(caption)
    font = _load_font(76)
    wrapped = _fit_text(draw, text.upper(), font, width - 160)
    bbox = draw.multiline_textbbox(
        (0, 0),
        wrapped,
        font=font,
        spacing=8,
        align="center",
        stroke_width=4,
    )
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    panel_x1 = 80
    panel_x2 = width - 80
    panel_y1 = height - text_h - 260
    panel_y2 = height - 120
    draw.rounded_rectangle((panel_x1, panel_y1, panel_x2, panel_y2), radius=36, fill=(0, 0, 0, 115))

    text_x = (width - text_w) // 2
    text_y = panel_y1 + ((panel_y2 - panel_y1 - text_h) // 2) - 10
    draw.multiline_text(
        (text_x, text_y),
        wrapped,
        font=font,
        fill=(248, 244, 230, 255),
        align="center",
        spacing=8,
        stroke_width=4,
        stroke_fill=(0, 0, 0, 255),
    )
    return ImageClip(np.array(caption)).set_duration(duration)


def _make_scene_clip(image_path, scene, duration, index):
    base = ColorClip(CANVAS_SIZE, color=(8, 8, 10), duration=duration)
    motion = _make_motion_clip(image_path, duration, seed=(index * 97) + 13)
    particles = _make_particle_clip(duration, seed=(index * 151) + 9)
    overlay = _make_gradient_overlay(duration)
    caption = _make_caption_clip(scene["caption"], duration)

    return CompositeVideoClip(
        [base, motion, particles, overlay, caption],
        size=CANVAS_SIZE,
    ).set_duration(duration)


def build_short_video(storyboard, image_paths, narration_path, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    narration_clip = AudioFileClip(str(narration_path))
    durations = _scene_durations(storyboard["scenes"], narration_clip.duration)

    clips = []
    for index, (scene, image_path, duration) in enumerate(zip(storyboard["scenes"], image_paths, durations), start=1):
        clips.append(_make_scene_clip(str(image_path), scene, duration, index))

    final_video = concatenate_videoclips(clips, method="compose").set_audio(narration_clip)
    final_video = final_video.set_duration(narration_clip.duration)
    final_video.write_videofile(
        str(output_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=4,
    )
    return output_path
