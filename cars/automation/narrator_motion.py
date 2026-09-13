"""Plan and capture the V21 narrator's continuous, audio-timed performance."""
import json
import math
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIN_SHOT_SECONDS = 3.6
SHOT_CYCLE = (
    ("bottom-right", "half"), ("close-right", "bust"),
    ("bottom-center", "half"), ("bottom-left", "half"),
    ("close-left", "bust"), ("bottom-center", "half"),
)


def build_motion_plan(manifest, duration, scene_boundaries, size=(1080, 1920), fps=24):
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
    boundaries = list(scene_boundaries) or [(0.0, duration)]
    # A single long scene still gets a few measured changes of framing.
    if len(boundaries) == 1 and duration > 8:
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
                            "look_at": [270, 245], "brows": bool(scene.get("headline"))})
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
