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
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from moviepy.editor import (
    AudioFileClip, ColorClip, CompositeAudioClip, CompositeVideoClip, ImageClip,
    VideoClip, VideoFileClip, concatenate_videoclips,
)
import moviepy.video.fx.all as vfx

from generate_sample import ROOT, CANVAS, _font, _wrap

SPRITES_DIR = ROOT / "narrator" / "sprites"
SFX_DIR = ROOT / "narrator" / "sfx"
# A soft whoosh that plays the instant a new car photo slides in -- swapped
# in from a supplied sample (replacing an earlier synthesized "chime" that
# read as an alerting/notification pitch rather than a photo transition).
# Volume is kept below the narration track so it reads as a texture, not a
# competing sound.
# .wav, not .mp3 -- an MP3-encoded sfx has a well-known ~50-100ms silent
# lead-in baked in by the LAME encoder (its "encoder delay"), which reads
# as the sound starting perceptibly late relative to the visual it's
# supposed to be synced to. WAV has no such padding.
PHOTO_POP_SFX = "photo_pop.wav"
PHOTO_POP_VOLUME = 0.34  # 15% quieter, on request
# A short slice of a keyboard-typing bed, played once per headline (not
# once per character -- looping/retriggering a full hit per character was
# the earlier "typing is too loud" complaint) under the typing animation,
# trimmed to however long that headline actually takes to type out.
TYPING_SFX = "typing.wav"
TYPING_VOLUME = 0.36  # 20% louder, on request

# A small "easter egg" loop in a corner: the car's own side-profile cutout
# (see single_car_short._select_side_profile_media) spun around an
# off-center pivot so it reads as doing donuts, with a trail of a few
# procedurally generated smoke puffs -- decorative only, not tied to any
# scene, meant to give the video some ambient personality without pulling
# focus from the narrator.
DOODLE_WIDTH_RATIO = 0.2
DOODLE_MARGIN_RATIO = 0.05
# A flat 2D photo spun a full 360deg reads as the car flipping upside down
# for half of every cycle, not drifting -- so this swings the car through a
# wide pendulum-style arc (a fixed pivot above the car, swinging side to
# side) instead of either a full rotation or a flat side-to-side slide. The
# arc itself is the "cornering" motion (it's a real curved path, not just a
# rotation-in-place, which is what read as "swinging like a baby's cradle"
# rather than drifting); the car's own rotation stays capped well short of
# upside down. An earlier version also squashed the image's width per frame
# for a pseudo-3D look, but combined with a real (imperfectly-masked) photo
# cutout that read as a washed-out outline rather than a car -- dropped
# entirely in favor of transforms that don't touch the image's own pixels.
DOODLE_SWAY_PERIOD_SECONDS = 1.5
DOODLE_ARC_RADIUS_RATIO = 0.075
DOODLE_ARC_HALF_ANGLE_DEG = 42
DOODLE_MAX_ROTATION_DEG = 30
SMOKE_PUFF_DIAMETER_RATIO = 0.03
SMOKE_PULSE_PERIOD_SECONDS = 0.9

# Two small side-profile cutouts drag-racing left-to-right in their own
# lane, positioned above the media/headline stack -- never overlapping the
# narrator -- side-profile photos read as "racing" moving horizontally,
# not vertically, since that's the angle they're actually photographed at.
# Winner is whichever car has the shorter (verified) quarter-mile time when
# available, falling back to more horsepower otherwise -- a deliberately
# simplified stand-in for a real drag race, not a physics simulation.
RACE_CAR_WIDTH_RATIO = 0.14
RACE_LANE_INSET_RATIO = 0.02
RACE_LANE_GAP_RATIO = 0.065
# A pause at the finish line once the winner arrives, so there's actually
# time to show the checkered flag / winner badge instead of the race
# finishing in the same instant the scene ends.
RACE_CELEBRATION_SECONDS = 0.9
# A drag-strip "Christmas tree" -- red/yellow/green lights at the start
# line, one second apart, before the cars actually move -- instead of a
# generic countdown at the start of the whole video (which had nothing to
# do with the race it was supposedly leading into).
RACE_COUNTDOWN_STEP_SECONDS = 1.0
RACE_COUNTDOWN_STEPS = 3
RACE_MIN_SECONDS_FOR_COUNTDOWN = 4.5
COUNTDOWN_SFX = "countdown_beep.wav"
COUNTDOWN_GO_SFX = "countdown_go.wav"
COUNTDOWN_SFX_VOLUME = 0.5

# A thin growing bar along the very top edge -- a subtle, near-zero-cost
# retention cue so viewers can subconsciously track how much is left.
PROGRESS_BAR_HEIGHT_PX = 6
PROGRESS_BAR_COLOR = (255, 214, 64)
# The top of the frame is a stack of three bands, top to bottom: a headline
# band, the car media itself, then a caption band -- in that order so
# neither piece of text sits on top of the picture the way it used to when
# both were just absolutely positioned over the whole top region. Together
# they're kept smaller than a near-half split so the narrator reads as the
# focal point instead of the media dominating the frame.
TOP_STACK_RATIO = 0.50
HEADLINE_ZONE_RATIO = 0.095
CAPTION_ZONE_RATIO = 0.075
# Margin on every edge of the media's own band -- the picture is inset
# instead of stretched edge-to-edge, so it reads as a framed photo rather
# than a banner. Trimmed further each time the picture needed to read
# bigger (0.125 -> 0.05 -> 0.02 -> 0.015). Growing TOP_STACK_RATIO (0.45
# -> 0.50) matters more for the reported "white space above/below the
# car" than shrinking this further does -- a wider-than-tall media box
# (>1.9:1) was letterboxing typical car photo aspect ratios (~1.5-1.78:1)
# top/bottom; a taller box brings the box's own aspect ratio closer to
# the photos' so _fit_content's scale is less likely to be height-limited
# with width left over.
MEDIA_INSET_RATIO = 0.015
CAPTION_CHUNK_WORDS = 1
# How long each revealed character of a headline stays on screen before the
# next one appears -- a typewriter reveal instead of the whole headline
# popping in at once. "Fairly quick": the full headline is typically fully
# typed out in well under a second.
# Slowed down further on request ("make typing sounds like 1 second
# longer") -- a typical headline now takes close to a full second to type
# out, closer to the typing.wav bed's own natural length (~1.3s) instead of
# getting trimmed down to a fraction of it.
TYPING_CHAR_SECONDS = 0.1

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
# A plain opacity crossfade between two differently-posed sprites (e.g. arm
# hanging straight vs. bent at the elbow) reads as a double exposure of two
# still pictures rather than the arm actually moving, since the crossfade
# only blends pixels -- it never moves anything. Layering a small scale +
# horizontal shift on top of that blend (see _apply_pose_transition_motion)
# gives the eye an actual motion cue -- a quick "wind-up" leaning toward the
# next stance as a pose hands off, and a "settle" into place as the next one
# takes over -- so the transition reads as the character shifting weight
# instead of one picture dissolving into another.
POSE_TRANSITION_LEAN_PX = 10
POSE_TRANSITION_POP_SCALE = 0.04
POSE_TRANSITION_WINDUP_SCALE = 0.03
POSE_LEAN_DIRECTION = {"steady": 0, "jolt": 0, "lean_left": -1, "lean_right": 1}
# Fallback cadence only used when there's no real word_timeline to find
# pauses in (estimated caption timing) -- some pose variety beats none.
POSE_FALLBACK_SEGMENT_SECONDS = 3.0
# A blink timeline of its own -- the flipbook otherwise defaults to
# permanently-open eyes for the whole video.
BLINK_START_SECONDS = 1.2
BLINK_INTERVAL_SECONDS = 3.0
# Fast alternation between the two fixed wobble-filter seeds (see
# WOBBLE_SEEDS in export-sprites.js) for the hand-drawn outline jitter --
# independent of, and much faster than, the pose cycle above.
WOBBLE_CYCLE = ["a", "b"]
WOBBLE_SEGMENT_SECONDS = 0.32
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


def _sfx_clip(name, start, volume, duration=None):
    """A single sound-effect hit positioned at `start`, or None if the
    asset isn't present -- missing sfx should never break a render.
    `duration`, when given, trims the asset down to at most that long (used
    to keep the typing bed from ever outrunning how long a headline
    actually takes to type)."""
    path = SFX_DIR / name
    if not path.exists():
        return None
    clip = AudioFileClip(str(path)).volumex(volume)
    if duration is not None and duration < clip.duration:
        trimmed = max(0.05, duration)
        clip = clip.subclip(0, trimmed)
        # A trim this short lands well before the source file's own
        # built-in fade-out (see narrator/sfx generation), so without one
        # of its own the clip just stops dead mid-sound -- audible as an
        # abrupt cutoff rather than the typing bed finishing naturally.
        clip = clip.audio_fadeout(min(0.15, trimmed * 0.4))
    return clip.set_start(start)


def _typing_headline_positions(text, start, end, char_seconds=TYPING_CHAR_SECONDS):
    """(prefix, seg_start, seg_duration) for each character revealed of
    `text`, one prefix per character -- the last prefix (the full string)
    holds for whatever's left of the scene instead of just one more
    char_seconds tick, so the headline doesn't vanish right after finishing
    typing."""
    total_chars = len(text)
    if total_chars == 0 or end <= start:
        return []
    per_char = min(char_seconds, (end - start) / total_chars)
    positions = []
    t = start
    for i in range(1, total_chars + 1):
        if i < total_chars:
            seg_duration = per_char
        else:
            seg_duration = max(0.01, end - t)
        positions.append((text[:i], t, seg_duration))
        t += seg_duration
    return positions


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


def _wobble_intervals(duration):
    """Fast, small alternation between the two fixed wobble-filter seeds
    export-sprites.js captured (see WOBBLE_SEEDS there) -- approximates
    the rig's own randomly-retriggering hand-drawn outline jitter well
    enough to read as "alive" rather than a perfectly static line, without
    needing a genuinely random clock (which wouldn't be reproducible)."""
    intervals = []
    t, i = 0.0, 0
    while t < duration:
        end = min(duration, t + WOBBLE_SEGMENT_SECONDS)
        intervals.append((t, end, WOBBLE_CYCLE[i % len(WOBBLE_CYCLE)]))
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
    wobble_intervals = _wobble_intervals(duration)
    boundaries = _merged_boundaries(
        [mouth_intervals, pose_intervals, blink_intervals, wobble_intervals], duration
    )

    segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        if end - start < 0.005:
            continue
        mid = (start + end) / 2
        mouth = _value_at(mouth_intervals, mid, "closed")
        pose = _value_at(pose_intervals, mid, POSE_CYCLE[0])
        eyes = _value_at(blink_intervals, mid, "open")
        wobble = _value_at(wobble_intervals, mid, WOBBLE_CYCLE[0])
        sprite_file = (
            sprites["sprites"].get(f"{mouth}_{eyes}_{pose}_{wobble}")
            or sprites["sprites"].get(f"{mouth}_{eyes}_{pose}")
            or sprites["sprites"].get(f"{mouth}_{eyes}")
        )
        if not sprite_file:
            continue
        segments.append({"start": start, "end": end, "pose": pose, "sprite": sprite_file})
    return segments


def _apply_pose_transition_motion(clip, seg_duration, transition_in, transition_out, in_direction, out_direction):
    """Layer a small scale + horizontal shift on top of the pose crossfade
    (see POSE_TRANSITION_LEAN_PX above for why the crossfade alone isn't
    enough). The incoming edge pops in slightly oversized and settles to
    its own pose's lean direction; the outgoing edge winds up a little
    toward whichever pose is taking over. Centered around the sprite's own
    midpoint (not its top-left corner) so it reads as the character
    shifting weight, not the image growing from a corner."""
    base_w, base_h = clip.size

    def scale_at(t):
        if transition_in > 0.01 and t < transition_in:
            return 1.0 + POSE_TRANSITION_POP_SCALE * (1 - t / transition_in)
        if transition_out > 0.01 and t > seg_duration:
            return 1.0 + POSE_TRANSITION_WINDUP_SCALE * (t - seg_duration) / transition_out
        return 1.0

    def offset_at(t):
        if transition_in > 0.01 and t < transition_in:
            return -in_direction * POSE_TRANSITION_LEAN_PX * (1 - t / transition_in)
        if transition_out > 0.01 and t > seg_duration:
            return out_direction * POSE_TRANSITION_LEAN_PX * 0.5 * (t - seg_duration) / transition_out
        return 0.0

    def position_at(t):
        scale = scale_at(t)
        return (base_w * (1 - scale) / 2 + offset_at(t), base_h * (1 - scale) / 2)

    return clip.resize(scale_at).set_position(position_at)


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
    frame_size = None
    current_time = 0.0
    for index, seg in enumerate(segments):
        seg_duration = seg["end"] - seg["start"]
        next_seg = segments[index + 1] if index + 1 < len(segments) else None
        prev_seg = segments[index - 1] if index > 0 else None
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
            if prev_seg and seg["pose"] != prev_seg["pose"] else 0.0
        )
        clip = ImageClip(str(SPRITES_DIR / seg["sprite"])).set_duration(seg_duration + transition_out)
        # Captured from the raw, unwrapped clip -- resize() below makes a
        # clip's own reported size time-varying, so grabbing it after
        # wrapping (and specifically off segments[0], which is exactly the
        # clip CompositeVideoClip's frame size was read from before) would
        # silently break the composite's canvas size.
        if frame_size is None:
            frame_size = clip.size
        if transition_in > 0.01 or transition_out > 0.01:
            clip = _apply_pose_transition_motion(
                clip, seg_duration, transition_in, transition_out,
                POSE_LEAN_DIRECTION.get(seg["pose"], 0),
                POSE_LEAN_DIRECTION.get(next_seg["pose"], 0) if next_seg else 0,
            )
        if transition_in > 0.01:
            clip = clip.crossfadein(transition_in)
        clips.append(clip.set_start(current_time))
        current_time += seg_duration

    track = CompositeVideoClip(clips, size=frame_size).set_duration(duration)
    return _fit_content(track, (size[0], max_h))


_WORD_NORM_RE = re.compile(r"[^a-z0-9]")


def _find_word_index(word_timeline, expected_index, target_word, window=6):
    """Re-anchor to the nearest real match of target_word around
    expected_index, instead of trusting pure cumulative word-count
    advancement -- a single decimal/number earlier in the script tokenizing
    differently in Whisper's output than a plain text split (e.g.
    "5.0-liter" coming back as several separate word entries) shifts every
    later scene's boundary by however many words it was off by, and that
    drift compounds scene over scene. Searching a small window around
    where the word "should" be and snapping to an actual text match resets
    that drift at every scene instead of carrying it forward indefinitely
    -- this is the fix for a rival car's photo still landing a couple
    seconds late several scenes into the video."""
    target_norm = _WORD_NORM_RE.sub("", target_word.lower())
    if not target_norm:
        return expected_index
    for offset in range(window + 1):
        for idx in (expected_index + offset, expected_index - offset):
            if 0 <= idx < len(word_timeline):
                candidate = _WORD_NORM_RE.sub("", str(word_timeline[idx].get("word") or "").lower())
                if candidate == target_norm:
                    return idx
    return expected_index


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
        scene_words = str(scene.get("narration") or "").split()
        word_count = len(scene_words)
        if scene_words:
            word_index = _find_word_index(word_timeline, word_index, scene_words[0])
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


def _generate_smoke_puff_image(diameter):
    """A soft, grayscale radial-falloff puff -- procedural so the drift
    doodle doesn't need a real smoke asset or a particle library, just a
    cheap numpy distance field turned into an alpha channel."""
    yy, xx = np.mgrid[0:diameter, 0:diameter]
    center = diameter / 2
    dist = np.sqrt((xx - center) ** 2 + (yy - center) ** 2) / center
    alpha = (np.clip(1.0 - dist, 0, 1) ** 1.6 * 150).astype(np.uint8)
    rgb = np.full((diameter, diameter, 3), 210, dtype=np.uint8)
    return np.dstack([rgb, alpha])


def _drift_doodle_track(cutout_path, size, duration):
    """A tiny version of the car's own side-profile cutout, swinging through
    a genuine curved arc in a corner -- like a pendulum hung from a pivot
    above the car -- so the motion reads as cornering/drifting rather than
    a flat side-to-side sway (which just looked like rocking a cradle).
    A couple of smoke puffs breathe near the rear wheels, trailing the
    car's own swinging center -- an ambient decoration for the whole
    video, not tied to any particular scene.

    Only rotates and repositions the image -- an earlier version also
    squashed its width per frame for a pseudo-3D look, but combined with a
    real (imperfectly-masked) photo cutout that read as a washed-out
    outline rather than an actual car, so this sticks to transforms that
    don't touch the image's own pixel data.

    Returns a plain list of small clips (not a nested CompositeVideoClip)
    for the caller to splice straight into its own composite -- wrapping
    even a handful of tiny clips in their own full-canvas-sized
    CompositeVideoClip forces every frame to build a whole extra
    1080x1920 buffer that's almost entirely empty, which made render time
    balloon for no visual benefit."""
    if not cutout_path or not Path(cutout_path).exists():
        return []
    width, height = size
    base = ImageClip(str(cutout_path)).resize(width=width * DOODLE_WIDTH_RATIO)
    car_w, car_h = base.size
    radius = width * DOODLE_ARC_RADIUS_RATIO
    half_angle_rad = math.radians(DOODLE_ARC_HALF_ANGLE_DEG)
    max_rot_rad = math.radians(DOODLE_MAX_ROTATION_DEG)
    # A rotated rectangle's bounding box grows a bit beyond its own resting
    # size -- sizing the safe area off that *rotated* extent (not the
    # resting size) plus the full swing of the arc is what keeps the car
    # (and its rotation) from ever clipping the canvas edge.
    rotated_half_width = (car_w * abs(math.cos(max_rot_rad)) + car_h * abs(math.sin(max_rot_rad))) / 2
    rotated_half_height = (car_w * abs(math.sin(max_rot_rad)) + car_h * abs(math.cos(max_rot_rad))) / 2
    horiz_amplitude = radius * math.sin(half_angle_rad)
    # The pendulum hangs lowest (largest drop below the pivot) when the
    # swing angle is 0, at the center of the arc -- that's the point the
    # bottom margin has to clear, not the resting position at an extreme.
    pivot_x = width - width * DOODLE_MARGIN_RATIO - rotated_half_width - horiz_amplitude
    pivot_y = height - height * DOODLE_MARGIN_RATIO - rotated_half_height - radius

    def swing_angle_deg(t):
        return DOODLE_ARC_HALF_ANGLE_DEG * math.sin(2 * math.pi * t / DOODLE_SWAY_PERIOD_SECONDS)

    def car_center(t):
        angle_rad = math.radians(swing_angle_deg(t))
        return (pivot_x + radius * math.sin(angle_rad), pivot_y + radius * math.cos(angle_rad))

    def rotation_angle(t):
        return DOODLE_MAX_ROTATION_DEG * math.sin(2 * math.pi * t / DOODLE_SWAY_PERIOD_SECONDS)

    def car_position(t):
        cx, cy = car_center(t)
        return (cx - car_w / 2, cy - car_h / 2)

    swinging_car = (
        base.set_duration(duration)
        .rotate(rotation_angle, expand=False)
        .set_position(car_position)
    )

    puff_diameter = max(10, int(width * SMOKE_PUFF_DIAMETER_RATIO))
    puff_image = _generate_smoke_puff_image(puff_diameter)
    puff_clips = []
    # Two small puffs near the base of the car, clear of the body itself,
    # each breathing in size on its own phase-shifted pulse, and trailing
    # the car's own swinging center so they stay near its rear wheels
    # through the whole arc instead of sitting at a single fixed spot.
    for phase, x_offset in ((0.0, -car_w * 0.32), (0.55, car_w * 0.32)):
        def puff_scale(t, phase=phase):
            return 0.5 + 0.35 * (0.5 + 0.5 * math.sin(2 * math.pi * t / SMOKE_PULSE_PERIOD_SECONDS + phase))

        def puff_position(t, x_offset=x_offset, phase=phase):
            cx, cy = car_center(t)
            d = puff_diameter * puff_scale(t, phase)
            return (cx + x_offset - d / 2, cy + car_h * 0.45 - d / 2)

        puff_clips.append(
            ImageClip(puff_image).set_duration(duration).resize(puff_scale).set_position(puff_position)
        )

    return [*puff_clips, swinging_car]


def _traffic_light_image(width, height, lit_count):
    """A small red/yellow/green light stack -- lit_count lights glow (from
    the top down) with the rest dim, like a drag-strip "Christmas tree"."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=width * 0.2, fill=(25, 25, 25, 235))
    dim = [(90, 20, 20, 255), (95, 80, 10, 255), (15, 70, 25, 255)]
    lit = [(255, 40, 40, 255), (255, 205, 40, 255), (50, 230, 70, 255)]
    bulb_d = width * 0.66
    gap = (height - 3 * bulb_d) / 4
    for i in range(3):
        color = lit[i] if i < lit_count else dim[i]
        x0 = (width - bulb_d) / 2
        y0 = gap + i * (bulb_d + gap)
        draw.ellipse([x0, y0, x0 + bulb_d, y0 + bulb_d], fill=color)
    return np.array(img)


def _generate_checkered_flag_image(width, height):
    """A small black/white checkerboard marker for the drag-strip finish
    line -- procedural so it doesn't need a real flag asset."""
    cols, rows = 4, 10
    cell_w = max(1, width // cols)
    cell_h = max(1, height // rows)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for row in range(rows):
        for col in range(cols):
            if (row + col) % 2 == 0:
                x0, y0 = col * cell_w, row * cell_h
                draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill=(20, 20, 20, 235))
            else:
                x0, y0 = col * cell_w, row * cell_h
                draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill=(245, 245, 245, 235))
    return np.array(img)


def _generate_winner_badge_image(diameter):
    """A soft green glow halo to drop behind the winning car once it
    crosses the finish line -- same radial-falloff approach as the smoke
    puff, just green instead of gray, so it reads as a "winner" highlight
    rather than more exhaust."""
    yy, xx = np.mgrid[0:diameter, 0:diameter]
    center = diameter / 2
    dist = np.sqrt((xx - center) ** 2 + (yy - center) ** 2) / center
    alpha = (np.clip(1.0 - dist, 0, 1) ** 1.4 * 200).astype(np.uint8)
    rgb = np.zeros((diameter, diameter, 3), dtype=np.uint8)
    rgb[..., 1] = 230
    rgb[..., 0] = 60
    return np.dstack([rgb, alpha])


def _drag_race_lane_clip(
    path, facing_direction, car_width, y_center, start_x, finish_x,
    seg_start, total_duration, countdown_duration, race_duration, finish_cap,
):
    """One car's clip for _drag_race_track -- sits at the start line during
    the countdown, then moves left-to-right, reaching finish_x exactly at
    the end of race_duration if finish_cap is 1.0, or stopping short
    (proportionally, per the real quarter-mile ratio) if it's the slower
    car, then holding that position for whatever's left of total_duration
    (the celebration buffer) so the winner is clearly parked at the line
    rather than still appearing to move. `set_start(seg_start)` makes
    moviepy pass this clip *local* time (0 at seg_start) into position().

    The cutout is flipped horizontally whenever the AI-reviewed
    facing_direction says the car's nose points left in its own photo --
    left-to-right travel means the nose has to point right, or the car
    reads as racing backwards."""
    car = ImageClip(str(path)).resize(width=car_width)
    if facing_direction == "left":
        car = car.fx(vfx.mirror_x)
    car_w, car_h = car.size
    travel = finish_x - start_x - car_w

    def position(local_t):
        race_t = local_t - countdown_duration
        if race_t <= 0:
            return (start_x, y_center - car_h / 2)
        progress = min(finish_cap, race_t / race_duration) if race_duration else finish_cap
        return (start_x + travel * progress, y_center - car_h / 2)

    return car.set_duration(total_duration).set_start(seg_start).set_position(position)


def _drag_race_track(
    main_cutout_path, rival_cutout_path, main_facing, rival_facing, main_hp, rival_hp,
    main_quarter_mile, rival_quarter_mile, size, seg_start, seg_end,
):
    """Two small side-profile cutouts drag-racing left-to-right in their
    own lane -- above the narrator, never crossing it -- during a
    horsepower/quarter-mile comparison beat, led in by a one-second-per-
    light countdown. The car with the shorter (verified) quarter-mile time
    "wins", falling back to more horsepower when a quarter-mile time isn't
    available. Both finish at the same line; the slower car is capped
    short of it by the real ratio between the two times, so it visibly
    trails instead of arriving together or racing forever past when the
    faster one already finished. A checkered flag marks that finish line,
    and the winner gets a green glow once it parks there, so which car
    actually won is never ambiguous."""
    if not main_cutout_path or not rival_cutout_path:
        return [], []
    if not Path(main_cutout_path).exists() or not Path(rival_cutout_path).exists():
        return [], []
    seg_duration = seg_end - seg_start
    if seg_duration <= 0.5:
        return [], []
    width, height = size

    use_countdown = seg_duration >= RACE_MIN_SECONDS_FOR_COUNTDOWN
    countdown_duration = RACE_COUNTDOWN_STEPS * RACE_COUNTDOWN_STEP_SECONDS if use_countdown else 0.0
    # A slice of the scene is held back as a celebration buffer -- the
    # race itself finishes early enough that the winner can sit parked at
    # the line, badge lit, before the beat ends -- instead of the two cars
    # visibly still moving (or arriving) right as the scene cuts away.
    celebration = min(RACE_CELEBRATION_SECONDS, max(0.0, seg_duration - countdown_duration - 0.5))
    race_duration = max(0.1, seg_duration - countdown_duration - celebration)

    if main_quarter_mile and rival_quarter_mile and main_quarter_mile > 0 and rival_quarter_mile > 0:
        main_time, rival_time = main_quarter_mile, rival_quarter_mile
    else:
        # No verified quarter-mile times -- fall back to a synthetic "time"
        # inversely proportional to horsepower (only the ratio between the
        # two matters here, not the absolute value).
        main_time = 1.0 / max(main_hp or 1, 1)
        rival_time = 1.0 / max(rival_hp or 1, 1)
    fastest = min(main_time, rival_time)
    main_cap = fastest / main_time
    rival_cap = fastest / rival_time
    main_wins = main_time <= rival_time

    car_width = width * RACE_CAR_WIDTH_RATIO
    lane_inset = width * RACE_LANE_INSET_RATIO
    start_x = lane_inset
    finish_x = width - lane_inset
    # Both lanes sit *above* the top-stack boundary (never below it, where
    # the narrator's own bounding box starts) -- subtracting the gap from
    # narrator_top instead of adding it is what actually keeps the cars
    # clear of the character, since the two zones share that same y=960
    # line with no natural gap of their own.
    narrator_top = height * TOP_STACK_RATIO
    lane_gap = height * RACE_LANE_GAP_RATIO
    main_y = narrator_top - lane_gap
    rival_y = narrator_top - lane_gap * 2.4

    total_duration = countdown_duration + race_duration + celebration
    car_clips = [
        _drag_race_lane_clip(
            main_cutout_path, main_facing, car_width, main_y, start_x, finish_x,
            seg_start, total_duration, countdown_duration, race_duration, main_cap,
        ),
        _drag_race_lane_clip(
            rival_cutout_path, rival_facing, car_width, rival_y, start_x, finish_x,
            seg_start, total_duration, countdown_duration, race_duration, rival_cap,
        ),
    ]

    light_clips = []
    sfx_clips = []
    if use_countdown:
        light_w = max(18, int(width * 0.035))
        light_h = int(light_w * 2.6)
        light_x = start_x
        light_y = (main_y + rival_y) / 2 - light_h / 2
        # Three one-second steps -- red, then +yellow, then +green (the
        # green step doubles as "go", with its own sfx) -- not a fast
        # flash: each light gets a full second, same as a real tree.
        for step in range(RACE_COUNTDOWN_STEPS):
            step_start = seg_start + step * RACE_COUNTDOWN_STEP_SECONDS
            light_image = _traffic_light_image(light_w, light_h, step + 1)
            light_clips.append(
                ImageClip(light_image)
                .set_start(step_start).set_duration(RACE_COUNTDOWN_STEP_SECONDS).set_position((light_x, light_y))
            )
            is_go_step = step == RACE_COUNTDOWN_STEPS - 1
            sfx_clip = _sfx_clip(COUNTDOWN_GO_SFX if is_go_step else COUNTDOWN_SFX, step_start, COUNTDOWN_SFX_VOLUME)
            if sfx_clip is not None:
                sfx_clips.append(sfx_clip)

    # The finish line itself -- a checkered marker spanning both lanes, up
    # for the whole race so there's always a visible destination, not just
    # two cars racing toward an unmarked edge of the screen.
    flag_w = max(14, int(width * 0.03))
    flag_h = int(abs(rival_y - main_y) + car_width * 0.4)
    flag_x = finish_x - flag_w
    flag_y = min(main_y, rival_y) - car_width * 0.2
    flag_clip = (
        ImageClip(_generate_checkered_flag_image(flag_w, flag_h))
        .set_start(seg_start).set_duration(total_duration).set_position((flag_x, flag_y))
    )

    # A green glow behind the winning car, timed to switch on exactly when
    # it parks at the finish line, so which car actually won never comes
    # down to "they looked like they finished together."
    winner_y = main_y if main_wins else rival_y
    badge_diameter = car_width * 0.9
    badge_start = seg_start + countdown_duration + race_duration
    badge_clip = (
        ImageClip(_generate_winner_badge_image(int(badge_diameter)))
        .set_start(badge_start).set_duration(max(0.1, total_duration - countdown_duration - race_duration))
        .set_position((finish_x - car_width - (badge_diameter - car_width) / 2, winner_y - badge_diameter / 2))
    )

    return [flag_clip, *light_clips, badge_clip, *car_clips], sfx_clips


def _progress_bar_track(size, duration):
    """A thin bar along the very top edge that fills left-to-right over the
    video's real duration -- a subtle "how much is left" cue."""
    width, _ = size

    def make_frame(t):
        frame = np.zeros((PROGRESS_BAR_HEIGHT_PX, width, 3), dtype=np.uint8)
        filled = int(width * min(1.0, max(0.0, t / duration))) if duration else width
        if filled > 0:
            frame[:, :filled] = PROGRESS_BAR_COLOR
        return frame

    return VideoClip(make_frame, duration=duration).set_position((0, 0))


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
    # A pop the instant each new car photo slides in, timed to the same
    # per-scene boundaries the photo itself uses -- not an even split,
    # which is exactly the "the rival's photo popped in a couple seconds
    # late" timing bug this whole boundary system exists to fix.
    photo_pop_clips = [
        _sfx_clip(PHOTO_POP_SFX, start, PHOTO_POP_VOLUME) for start, _ in scene_boundaries
    ]

    # The caption band sits below the picture, not on top of it -- distinct
    # from the headline band above the picture.
    caption_clips = [
        ImageClip(str(_caption_frame_path(output_path, text, start, int(caption_center_y), size)))
        .set_start(start).set_duration(end - start).set_position((0, 0))
        for text, start, end in _caption_timeline(manifest, duration)
    ]
    headline_clips = []
    typing_sfx_clips = []
    for index, scene in enumerate(scenes):
        headline = str(scene.get("headline") or "").strip()
        if not headline:
            continue
        start, end = scene_boundaries[index] if index < len(scene_boundaries) else (0.0, duration)
        positions = _typing_headline_positions(headline, start, end)
        for char_index, (prefix, seg_start, seg_duration) in enumerate(positions):
            frame_path = output_path.parent / "_frames" / f"headline-{index}-{char_index}.png"
            _caption_frame(size, prefix, int(headline_center_y), frame_path, fill=(255, 214, 64), font_size=92)
            headline_clips.append(
                ImageClip(str(frame_path)).set_start(seg_start).set_duration(seg_duration).set_position((0, 0))
            )
        if positions:
            # One typing-bed hit for the whole headline, trimmed to however
            # long it actually took to type -- not one hit per character,
            # which stacked into an overly loud wall of sound.
            typing_duration = positions[-1][1] - positions[0][1]
            typing_sfx_clips.append(_sfx_clip(TYPING_SFX, start, TYPING_VOLUME, duration=typing_duration))

    # Decorative extras: an ambient corner doodle reusing the car's own
    # side-profile cutout (single_car_short._select_side_profile_media),
    # plus a drag race (with its own lead-in countdown lights) wherever a
    # scene states both cars' quarter-mile time or horsepower.
    side_profile_path = manifest.get("side_profile_media_path")
    side_profile_facing = manifest.get("side_profile_facing_direction", "unclear")
    media_entries = list(manifest.get("media") or [])
    decorative_clips = []
    decorative_sfx = []
    decorative_clips.extend(_drift_doodle_track(side_profile_path, size, duration))
    for index, scene in enumerate(scenes):
        main_hp = scene.get("main_horsepower")
        rival_hp = scene.get("rival_horsepower")
        if main_hp is None or rival_hp is None:
            continue
        if index >= len(car_media_paths) or index >= len(scene_boundaries):
            continue
        seg_start, seg_end = scene_boundaries[index]
        rival_facing = media_entries[index].get("facing_direction", "unclear") if index < len(media_entries) else "unclear"
        race_clips, race_sfx = _drag_race_track(
            side_profile_path, car_media_paths[index], side_profile_facing, rival_facing, main_hp, rival_hp,
            scene.get("main_quarter_mile_seconds"), scene.get("rival_quarter_mile_seconds"),
            size, seg_start, seg_end,
        )
        decorative_clips.extend(race_clips)
        decorative_sfx.extend(race_sfx)

    progress_clip = _progress_bar_track(size, duration)

    background = ColorClip(size=size, color=(255, 255, 255)).set_duration(duration)
    sfx_clips = [
        clip for clip in (*photo_pop_clips, *typing_sfx_clips, *decorative_sfx) if clip is not None
    ]
    full_audio = CompositeAudioClip([audio, *sfx_clips]) if sfx_clips else audio
    video = CompositeVideoClip(
        [
            background, car_positioned, *headline_clips, *caption_clips, narrator_positioned,
            *decorative_clips, progress_clip,
        ],
        size=size,
    ).set_duration(duration).set_audio(full_audio)

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
