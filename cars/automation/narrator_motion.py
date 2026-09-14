"""Plan and capture the V21 narrator's continuous, audio-timed performance."""
import json
import math
import os
import subprocess
from pathlib import Path
from photo_story import photo_story_timeline, reveal_schedule, tile_centers


ROOT = Path(__file__).resolve().parents[2]
MIN_SHOT_SECONDS = 3.6
# Where the character's head lands horizontally, as a fraction of frame
# width, for each layout anchor. Mirrors composeShot()'s headCenter in
# narrator-rig-v21.html (22 + 84*scale from the left, 518 - 84*scale from the
# right, in that rig's 540-wide frame) at the scales render mode actually
# uses; the gap between the bottom-* and close-* variants is under 0.01, so
# one value per anchor is enough to aim with.
HEAD_X_BY_ANCHOR = {"left": 0.17, "right": 0.83, "center": 0.5}
# The rig pins the top of the head to safe_top, so the eyes sit a little way
# below it.
HEAD_Y_BELOW_SAFE_TOP = 0.05
# A target half a frame away from the head gives full pupil deflection.
AIM_SPAN = 0.5
SHOT_CYCLE = (
    ("bottom-right", "half"), ("close-right", "bust"),
    ("bottom-center", "half"), ("bottom-left", "half"),
    ("close-left", "bust"), ("bottom-center", "half"),
)


def _anchor_of(layout):
    return "left" if layout.endswith("left") else "right" if layout.endswith("right") else "center"


def _aim(layout, safe_top, target):
    """Pupil direction, -1..1 per axis, from the character's head to a point
    in the frame (both as fractions of frame size).

    The rig used to be handed the target as a point and put it through
    #head's CTM inverse -- but getCTM() is in rendered pixels, not the rig's
    own frame units, so the answer depended on the capture's render size and
    every shipped value came out clamped against the vertical limit. Working
    the direction out here keeps it in the one place that knows both where
    the character was placed and where the photo is.
    """
    head_x = HEAD_X_BY_ANCHOR[_anchor_of(layout)]
    head_y = safe_top + HEAD_Y_BELOW_SAFE_TOP
    return [max(-1.0, min(1.0, (target[0] - head_x) / AIM_SPAN)),
            max(-1.0, min(1.0, (target[1] - head_y) / AIM_SPAN))]


def build_motion_plan(manifest, duration, scene_boundaries, size=(1080, 1920), fps=24, media_box=None):
    """Reuse the HTML's named presets, with space reserved for the car/text.

    Camera changes follow scenes; gestures happen after the camera settles and
    return to rest. The mouth has its own audio clock, including during motion.
    Nothing depends on random numbers or browser wall-clock timing.
    """
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Narrator duration must be positive and finite")
    scenes = list(manifest.get("scenes") or [])
    has_stats = any(s.get("stat_label") or s.get("stat_label_2") for s in scenes)
    safe_top = 0.66 if has_stats else 0.59
    photo_cues = photo_story_timeline(manifest, scene_boundaries)
    if photo_cues:
        safe_top = 0.65
    # Where the car photography actually is, so gaze and gestures can point
    # at it rather than at a hardcoded spot. Falls back to the band the
    # layout reserves for it when the caller doesn't pass the real box.
    if media_box:
        media_pixels = tuple(float(v) for v in media_box)
    else:
        media_pixels = (0.0, size[1] * 0.09, float(size[0]), size[1] * 0.32)
    media_frame = (media_pixels[0] / size[0], media_pixels[1] / size[1],
                   media_pixels[2] / size[0], media_pixels[3] / size[1])
    media_center = (media_frame[0] + media_frame[2] / 2, media_frame[1] + media_frame[3] / 2)
    boundaries = list(scene_boundaries) or [(0.0, duration)]
    # A single long scene still gets a few measured changes of framing.
    if not photo_cues and len(boundaries) == 1 and duration > 8:
        boundaries = [(t, min(t + 6, duration)) for t in range(0, math.ceil(duration), 6)]
    shots, expressions, gestures = [], [], []
    side = "right"
    for index, (start, end) in enumerate(boundaries):
        start, end = max(0.0, float(start)), min(duration, float(end))
        if end <= start:
            continue
        scene = scenes[index] if index < len(scenes) else {}
        layout, framing = SHOT_CYCLE[len(shots) % len(SHOT_CYCLE)]
        is_detail = scene.get("media_type") in {"engine", "interior", "detail", "wheel"}
        if index == len(boundaries) - 1 and index > 0 and not scene.get("headline"):
            layout, framing = "bottom-center", "half"
        elif scene.get("stat_label") and not scene.get("rival_make"):
            layout, framing = "close-" + side, "bust"
        elif is_detail or scene.get("rival_make"):
            layout, framing = "bottom-" + side, "half"
        if not shots or (start - shots[-1]["start"] >= MIN_SHOT_SECONDS and end - start >= 1.2):
            if not shots or (layout, framing) != (shots[-1]["layout"], shots[-1]["framing"]):
                shots.append({"start": start, "layout": layout, "framing": framing})
                if layout.endswith("left"):
                    side = "left"
                elif layout.endswith("right"):
                    side = "right"
        # Briefly look toward the car as its new photo/fact appears.
        expressions.append({"start": start + 0.2, "end": min(end, start + 1.55),
                            "aim": _aim(shots[-1]["layout"] if shots else "bottom-right",
                                        safe_top, media_center),
                            "brows": bool(scene.get("headline"))})
    if not shots:
        shots = [{"start": 0.0, "layout": "bottom-right", "framing": "half"}]
    shots[0]["start"] = 0.0
    # At most one conversational arm gesture at a time, separated by rest.
    # Open hands is the only two-arm accent; both move outward.
    gesture_names = ("leftTalk", "rightTalk", "open", "chest")
    t, index = 0.65, 0
    while t < duration - 0.8:
        for shot in shots[1:]:
            if shot["start"] - 0.3 <= t < shot["start"] + 1.15:
                t = shot["start"] + 1.15
        if t >= duration - 0.8:
            break
        gestures.append({"start": round(t, 6), "pose": gesture_names[index % len(gesture_names)]})
        gestures.append({"start": round(min(t + 1.45, duration - 0.2), 6), "pose": "rest"})
        t += 3.4
        index += 1
    # The close-ups live inside the media box with the main photo rather
    # than floating in a card over the lower half, so the framing cycle
    # above is left alone -- photo-story builds get the same varied shots as
    # everything else. What the cues do drive is the character reacting to
    # them: when a scene is about one specific close-up, look at that tile
    # and present it with the hand on that side.
    for cue in photo_cues:
        active_index = cue.get("active_index")
        if active_index is None:
            continue
        # Laid out from the same geometry the renderer draws with, against
        # the real media box, so the hand goes where the tile actually is.
        centers = tile_centers(media_pixels[2], media_pixels[3], len(cue.get("closeups") or []))
        if active_index >= len(centers):
            continue
        center = centers[active_index]
        target = (media_frame[0] + center[0] * media_frame[2],
                  media_frame[1] + center[1] * media_frame[3])
        # Wait until the tile is actually on screen. The close-ups arrive one
        # at a time now, so pointing on the scene's own clock could put the
        # hand on an empty cell.
        appears = next((t for t, visible in reveal_schedule(
            cue.get("chapter_start", cue["start"]), cue.get("chapter_end", cue["end"]),
            len(cue.get("closeups") or [])) if visible > active_index), cue["start"])
        start = max(cue["start"] + 0.35, appears + 0.2)
        end = min(cue["end"] - 0.2, start + 2.6)
        # Too short to read as a deliberate point; the generic gesture cycle
        # covers that scene instead.
        if end - start < 1.0:
            continue
        layout = next((shot["layout"] for shot in reversed(shots) if shot["start"] <= start),
                      shots[0]["layout"] if shots else "bottom-right")
        head_x = HEAD_X_BY_ANCHOR[_anchor_of(layout)]
        expressions.append({"start": start, "end": end,
                            "aim": _aim(layout, safe_top, target), "brows": True})
        # presentLeft raises the screen-left arm, presentRight the screen-right
        # one, so the hand goes up on the side the tile is actually on.
        gestures.append({"start": round(start + 0.15, 6),
                         "pose": "presentLeft" if target[0] < head_x else "presentRight"})
        gestures.append({"start": round(end, 6), "pose": "rest"})
        # Drop any generic conversational gesture inside the window -- two
        # arm poses fighting over the same second is what made the old
        # version look twitchy.
        gestures = [g for g in gestures
                    if not (start - 0.3 < g["start"] < end and not g["pose"].startswith("present")
                            and g["start"] != round(end, 6))]
    gestures.sort(key=lambda g: g["start"])
    mouths = []
    valid_mouths = {"closed", "small", "mbp", "ee", "ah", "oh", "fv", "wide", "teeth", "smile"}
    for entry in manifest.get("mouth_timeline") or []:
        start, end = max(0.0, float(entry["start"])), min(duration, float(entry["end"]))
        if math.isfinite(start) and math.isfinite(end) and end > start:
            mouth = entry.get("mouth")
            # Older manifests used a separate OO frame; the compact OH frame
            # now covers both rounded vowel sounds.
            if mouth == "oo":
                mouth = "oh"
            mouths.append({"start": start, "end": end,
                           "mouth": mouth if mouth in valid_mouths else "closed"})
    if not mouths:
        for index, word in enumerate(manifest.get("word_timeline") or []):
            start, end = max(0.0, float(word["start"])), min(duration, float(word["end"]))
            if end > start:
                mouths.append({"start": start, "end": end, "mouth": ("small", "wide", "teeth")[index % 3]})
    return {"version": "v21", "duration": duration, "fps": fps,
            "width": int(size[0]), "height": int(size[1]), "safe_top": safe_top,
            "shots": shots, "gestures": gestures, "expressions": expressions,
            "photo_cues": photo_cues,
            "mouth_timeline": sorted(mouths, key=lambda m: m["start"])}


def render_live_narrator(plan, output_dir):
    """Capture an alpha video; return paths for compositing and diagnostics."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "narrator-motion.json"
    video_path = output_dir / "narrator-v21.mov"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    script = ROOT / "narrator" / "render" / "render-v21.js"
    subprocess.run([os.environ.get("NODE_BINARY", "node"), str(script),
                    "--plan", str(plan_path.resolve()), "--output", str(video_path.resolve())],
                   check=True, timeout=max(180, plan["duration"] * 20))
    return video_path, plan_path
