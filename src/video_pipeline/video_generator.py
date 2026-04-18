import os
import time
from pathlib import Path
from moviepy.editor import VideoFileClip, concatenate_videoclips
from dotenv import load_dotenv
from google import genai
from google.genai import types
from .global_style import GLOBAL_VIDEO_STYLE

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

CHAR = os.getenv("CHARACTER_NAME", "Character")
ASPECT = os.getenv("VIDEO_ASPECT_RATIO", "9:16")
RESOLUTION = os.getenv("VIDEO_RESOLUTION", "480p")
CLIP_DURATION = int(os.getenv("VIDEO_CLIP_DURATION", "4"))
NUM_CLIPS = int(os.getenv("VIDEO_NUM_CLIPS", "2"))


def generate_veo_clip(prompt, out_path):
    styled_prompt = (
        f"{prompt}. Featuring {CHAR}. "
        f"{GLOBAL_VIDEO_STYLE}. "
        "Consistent character appearance, accurate colors, silhouette, and proportions."
    )

    print(f"\n🎥 Generating clip (Veo 3.1)...\n{styled_prompt}\n")

    # Restore OLD, WORKING MODEL
    operation = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt=styled_prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio=ASPECT,
            resolution=RESOLUTION,
            duration_seconds=CLIP_DURATION,
            person_generation="allow_all",
        ),
    )

    # 🔥 Correct polling (the ONLY fix that was missing)
    while not operation.done:
        print("⏳ Waiting for video generation...")
        time.sleep(6)
        operation = client.operations.get(operation)


    # Download video
    video = operation.response.generated_videos[0].video
    data = client.files.download(file=video)

    with open(out_path, "wb") as f:
        f.write(data)

    print(f"✅ Saved video: {out_path}")
    return out_path


def combine_clips(paths, out_path):
    clips = [VideoFileClip(p) for p in paths]
    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(out_path, codec="libx264", fps=24)
    return out_path
