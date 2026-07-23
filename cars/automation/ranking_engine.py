"""Generic renderer for a 4-item countdown ranking Short.

Topic-specific scripts (e.g. generate_ranking_short.py for Miata,
generate_ranking_short_mustang.py for Mustang) build a RankingConfig and
call render_ranking_video(config). All the drawing/layout logic here is
topic-agnostic; only the config content changes per video.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from generate_sample import (
    ROOT,
    CANVAS,
    FAST_CANVAS,
    _font,
    _wrap,
    _write_narration_audio,
    _write_contact_sheet,
)
from video_pipeline.short_editor import FPS, FAST_MODE
from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips

OUTPUT_ROOT = ROOT / "cars" / "output" / "samples"

ACCENT = (255, 204, 92)
RANK_COLORS = {1: (255, 204, 92), 2: (230, 60, 60), 3: (80, 190, 255), 4: (200, 80, 220)}

BAR_H_RATIO = 0.22
PHOTO_H_RATIO = 0.55
CAPTION_H_RATIO = 0.23


@dataclass
class RankEntry:
    rank: int  # 4 = worst, 1 = best
    name: str
    years: str
    images: list  # list[Path], at least one
    label: str  # short sentiment tag, e.g. "THE BOAT"
    stat: str  # short stat chip text
    narration: str  # spoken line for this rank (keep short -- see module docstring)


@dataclass
class RankingConfig:
    slug: str
    title: str  # e.g. "RANKING MIATA GENERATIONS"
    title_highlight_words: set  # words in title to color (usually just the model name)
    ranks: list  # list[RankEntry], must contain exactly ranks 4,3,2,1
    close_narration: str
    theme: str = ""
    target_seconds: int = 35


def _wrap_words(draw, words, font, max_width):
    lines = []
    current = []
    for word in words:
        candidate = " ".join([*current, word])
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current.append(word)
        else:
            lines.append(current)
            current = [word]
    if current:
        lines.append(current)
    return lines


def _draw_colored_line(draw, words, font, x_center, y, scale, highlight_words):
    space_w = draw.textbbox((0, 0), "A A", font=font)[2] - draw.textbbox((0, 0), "AA", font=font)[2]
    widths = []
    for word in words:
        bbox = draw.textbbox((0, 0), word, font=font)
        widths.append(bbox[2] - bbox[0])
    total_w = sum(widths) + space_w * max(0, len(words) - 1)
    x = x_center - total_w / 2
    for word, w in zip(words, widths):
        color = ACCENT if word.strip(",.").upper() in highlight_words else (255, 255, 255)
        draw.text((x, y), word, font=font, fill=color, stroke_width=max(1, int(2 * scale)), stroke_fill=(0, 0, 0))
        x += w + space_w


def _draw_title_bar(draw, size, title, highlight_words):
    width, height = size
    scale = width / 1080
    bar_h = int(height * BAR_H_RATIO)
    draw.rectangle((0, 0, width, bar_h), fill=(0, 0, 0))

    title_font = _font(int(92 * scale))
    lines = _wrap_words(draw, title.split(), title_font, int(width * 0.92))
    line_bbox = draw.textbbox((0, 0), "Ag", font=title_font)
    line_h = (line_bbox[3] - line_bbox[1]) + int(20 * scale)
    total_h = line_h * len(lines)
    y = bar_h / 2 - total_h / 2
    for line_words in lines:
        _draw_colored_line(draw, line_words, title_font, width / 2, y, scale, highlight_words)
        y += line_h
    return bar_h


def _draw_photo_block(canvas, image, size, bar_h):
    """Contain-fit the photo (no cropping) inside the middle band, letterboxed with
    black if needed, so the whole car stays visible instead of being cropped."""
    width, height = size
    zone_h = int(height * PHOTO_H_RATIO)
    img_ratio = image.width / image.height
    zone_ratio = width / zone_h
    if img_ratio > zone_ratio:
        new_w = width
        new_h = max(1, int(new_w / img_ratio))
    else:
        new_h = zone_h
        new_w = max(1, int(new_h * img_ratio))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    x = (width - new_w) // 2
    y = bar_h + (zone_h - new_h) // 2
    canvas.paste(resized.convert("RGBA"), (x, y))
    return y, new_h  # actual top/height of the visible (uncropped) photo


def _draw_bottom_caption(draw, size, bar_h, photo_h, text):
    if not text:
        return
    width, height = size
    scale = width / 1080
    top = bar_h + photo_h
    font = _font(int(56 * scale))
    wrapped = _wrap(draw, text, font, int(width * 0.90), max_lines=4)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=int(10 * scale))
    cap_h = height - top
    draw.multiline_text(
        (width / 2 - (bbox[2] - bbox[0]) / 2, top + (cap_h - (bbox[3] - bbox[1])) / 2),
        wrapped,
        font=font,
        fill=(255, 249, 235),
        spacing=int(10 * scale),
        align="center",
    )


def _ranking_rail_layout(size):
    """Return canvas-anchored rail geometry shared by every video frame."""
    width, height = size
    scale = width / 1080
    bar_h = int(height * BAR_H_RATIO)
    rail_x = int(68 * scale)
    rail_w = int(420 * scale)
    list_top = bar_h + int(110 * scale)
    row_h = int(152 * scale)
    row_gap = int(46 * scale)
    return [
        (rail_x, list_top + index * (row_h + row_gap), rail_x + rail_w, list_top + index * (row_h + row_gap) + row_h)
        for index in range(4)
    ]


def _fit_rank_label_font(draw, text, max_width, scale):
    """Shrink long labels inside the rail; never resize or move the rail."""
    for base_size in range(40, 21, -2):
        font = _font(int(base_size * scale))
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
    return _font(int(20 * scale))


def _draw_numbered_list(draw, size, current_rank, rank_labels):
    """Draw a fixed ranking rail over the photo zone.

    Its position and dimensions depend only on the output canvas, never on the
    source photo's aspect ratio or the width of a label.
    """
    width, _ = size
    scale = width / 1080
    number_font = _font(int(104 * scale))
    for rank_num, (rail_x, y, rail_right, row_bottom) in zip([4, 3, 2, 1], _ranking_rail_layout(size)):
        row_h = row_bottom - y
        is_current = rank_num == current_rank
        already_revealed = rank_num >= current_rank  # countdown runs 4 -> 1
        label = f"{rank_num}."
        color = RANK_COLORS[rank_num]
        number_bbox = draw.textbbox((0, 0), label, font=number_font)
        number_y = y + int(4 * scale)
        draw.text(
            (rail_x, number_y),
            label,
            font=number_font,
            fill=color,
            stroke_width=max(2, int(5 * scale)),
            stroke_fill=(0, 0, 0),
        )
        rank_label = rank_labels.get(rank_num)
        if already_revealed and rank_label:
            label_x = rail_x + int(112 * scale)
            small_font = _fit_rank_label_font(
                draw, rank_label, rail_right - label_x, scale
            )
            label_bbox = draw.textbbox((0, 0), rank_label, font=small_font)
            label_y = y + (row_h - (label_bbox[3] - label_bbox[1])) / 2 - label_bbox[1] + int(8 * scale)
            draw.text(
                (label_x, label_y),
                rank_label,
                font=small_font,
                fill=(255, 255, 255) if not is_current else color,
                stroke_width=max(1, int(4 * scale)),
                stroke_fill=(0, 0, 0),
            )
        elif not already_revealed:
            placeholder = "________"
            placeholder_font = _font(int(36 * scale))
            placeholder_bbox = draw.textbbox((0, 0), placeholder, font=placeholder_font)
            placeholder_y = y + (row_h - (placeholder_bbox[3] - placeholder_bbox[1])) / 2 - placeholder_bbox[1] + int(12 * scale)
            draw.text(
                (rail_x + int(112 * scale), placeholder_y),
                placeholder,
                font=placeholder_font,
                fill=(255, 255, 255, 150),
                stroke_width=max(1, int(3 * scale)),
                stroke_fill=(0, 0, 0),
            )


def _draw_rank_frame(config, entry, image_path, out_path, size):
    width, height = size
    scale = width / 1080
    bar_h = int(height * BAR_H_RATIO)

    canvas = Image.new("RGBA", size, (0, 0, 0, 255))
    base = Image.open(image_path).convert("RGB")
    photo_top, photo_h = _draw_photo_block(canvas, base, size, bar_h)
    draw = ImageDraw.Draw(canvas)

    _draw_title_bar(draw, size, config.title, config.title_highlight_words)
    rank_labels = {e.rank: e.label for e in config.ranks}
    _draw_numbered_list(draw, size, entry.rank, rank_labels)

    stat_font = _font(int(46 * scale))
    highlight = RANK_COLORS[entry.rank]
    stat_text = f"{entry.name} ({entry.years})  |  {entry.stat}"
    stat_bbox = draw.textbbox((0, 0), stat_text, font=stat_font)
    stat_x = width / 2 - (stat_bbox[2] - stat_bbox[0]) / 2
    stat_y = photo_top + photo_h - int(94 * scale)
    draw.rounded_rectangle(
        (
            stat_x - int(24 * scale), stat_y - int(14 * scale),
            stat_x + (stat_bbox[2] - stat_bbox[0]) + int(24 * scale), stat_y + (stat_bbox[3] - stat_bbox[1]) + int(18 * scale),
        ),
        radius=int(18 * scale),
        fill=(0, 0, 0, 190),
        outline=highlight,
        width=max(1, int(2 * scale)),
    )
    draw.text(
        (stat_x, stat_y),
        stat_text,
        font=stat_font,
        fill=(255, 248, 230),
        stroke_width=max(1, int(2 * scale)),
        stroke_fill=(0, 0, 0),
    )

    nominal_zone_h = int(height * PHOTO_H_RATIO)
    _draw_bottom_caption(draw, size, bar_h, nominal_zone_h, entry.narration)
    canvas.convert("RGB").save(out_path)


def _word_weight(text):
    return max(3, len(text.split()))


def render_ranking_video(config, output_root=OUTPUT_ROOT, render_video=True, fast=True, tts_provider="gtts"):
    ranks_by_num = {e.rank: e for e in config.ranks}
    if set(ranks_by_num) != {4, 3, 2, 1}:
        raise SystemExit(f"RankingConfig.ranks must cover ranks 4,3,2,1 exactly; got {sorted(ranks_by_num)}")
    for entry in config.ranks:
        for image_path in entry.images:
            if not Path(image_path).exists():
                raise SystemExit(f"Missing source image for {entry.name}: {image_path}")

    run_dir = Path(output_root) / config.slug
    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    size = FAST_CANVAS if fast else CANVAS

    ordered = [ranks_by_num[n] for n in (4, 3, 2, 1)]
    full_narration = " ".join([*(e.narration for e in ordered), config.close_narration])
    storyboard = {
        "title": config.title,
        "hook": ordered[0].narration,
        "narration": full_narration,
        "story_provider": "ranking-template",
        "theme": config.theme,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_slug": config.slug,
    }

    narration_path, audio_provider = _write_narration_audio(
        run_dir, storyboard, duration_seconds=config.target_seconds, provider=tts_provider
    )
    storyboard["audio_provider"] = audio_provider
    audio_clip = AudioFileClip(str(narration_path))
    total_duration = audio_clip.duration

    # Split total narration duration across segments proportional to word count,
    # then split each rank's segment across its quick-cut images.
    segments = [*[(f"rank_{e.rank}", e.narration) for e in ordered], ("close", config.close_narration)]
    weights = [_word_weight(text) for _, text in segments]
    total_weight = sum(weights)
    segment_durations = {name: total_duration * w / total_weight for (name, _), w in zip(segments, weights)}

    frame_entries = []  # (image_path_out, duration)

    for entry in ordered:
        rank_duration = segment_durations[f"rank_{entry.rank}"]
        per_image = rank_duration / len(entry.images)
        for i, image_path in enumerate(entry.images):
            out_path = images_dir / f"scene_rank_{entry.rank}_{i}.png"
            _draw_rank_frame(config, entry, image_path, out_path, size)
            frame_entries.append((out_path, per_image))

    close_path = images_dir / "scene_close.png"
    best = ordered[-1]
    close_entry = RankEntry(
        rank=best.rank, name=best.name, years=best.years, images=best.images,
        label=best.label, stat=best.stat, narration=config.close_narration,
    )
    _draw_rank_frame(config, close_entry, best.images[0], close_path, size)
    frame_entries.append((close_path, segment_durations["close"]))

    _write_contact_sheet([p for p, _ in frame_entries], run_dir / "scene_contact_sheet.jpg")
    storyboard["frames"] = [{"path": str(Path(p).relative_to(ROOT)), "duration": round(d, 3)} for p, d in frame_entries]
    (run_dir / "storyboard.json").write_text(json.dumps(storyboard, indent=2), encoding="utf-8")

    if render_video:
        clips = [ImageClip(str(path)).set_duration(duration) for path, duration in frame_entries]
        video = concatenate_videoclips(clips, method="compose").set_audio(audio_clip).set_duration(total_duration)
        video.write_videofile(
            str(run_dir / "final_short.mp4"),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast" if FAST_MODE else "medium",
            threads=4,
        )

    return run_dir
