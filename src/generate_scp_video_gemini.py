import os
import time
from google import genai
from google.genai import types
from moviepy.editor import VideoFileClip, concatenate_videoclips
import json
from global_style import GLOBAL_VIDEO_STYLE

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# -----------------------------------------------------------
# Video Configuration (via ENV vars)
# -----------------------------------------------------------
VIDEO_ASPECT = os.getenv("VIDEO_ASPECT_RATIO", "9:16")
VIDEO_RESOLUTION = os.getenv("VIDEO_RESOLUTION", "480p")   # cheap default
VIDEO_DURATION = int(os.getenv("VIDEO_DURATION", "4"))      # cheap default
NUM_CLIPS = int(os.getenv("VIDEO_NUM_CLIPS", "5"))          # 5 clips by default

print("🎛 Video Configuration:")
print(f"  Aspect Ratio : {VIDEO_ASPECT}")
print(f"  Resolution   : {VIDEO_RESOLUTION}")
print(f"  Duration     : {VIDEO_DURATION}s")
print(f"  Num Clips    : {NUM_CLIPS}")
print("------------------------------------------------------\n")


# -----------------------------------------------------------
# Extract Scene Prompts from entry_text
# -----------------------------------------------------------
def extract_scene_prompts(text):
    prompts = []
    for line in text.splitlines():
        if line.lower().startswith("scene prompt"):
            cleaned = line.split(":", 1)[1].strip()
            prompts.append(cleaned)

    # If fewer than NUM_CLIPS prompts, auto-generate variations
    if len(prompts) == 0:
        prompts = [
            "A gentle creature resting in its natural habitat",
            "A slow pan reveal of the creature interacting with its environment",
        ]

    # Expand until we have enough
    while len(prompts) < NUM_CLIPS:
        prompts.append(prompts[-1] + " (alternate cinematic angle)")

    return prompts[:NUM_CLIPS]


# -----------------------------------------------------------
# Generate a single Veo video clip
# -----------------------------------------------------------
def generate_video(prompt, output_path):

    styled_prompt = (
        f"{prompt}. "
        f"{GLOBAL_VIDEO_STYLE}. "
        "Wildlife documentary realism, natural habitat, gentle camera movement, "
        "soft sunlight, expressive eyes, grounded animal anatomy."
    )

    print(f"🎥 Generating {VIDEO_DURATION}s clip:\n{styled_prompt}\n")

    operation = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt=styled_prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio=VIDEO_ASPECT,
            resolution=VIDEO_RESOLUTION,
            duration_seconds=str(VIDEO_DURATION),
            person_generation="allow_all"
        )
    )

    # Poll for completion
    while not operation.done:
        print("⏳ Waiting for video generation...")
        time.sleep(8)
        operation = client.operations.get(operation)

    # Download MP4
    video = operation.response.generated_videos[0].video
    client.files.download(file=video)
    video.save(output_path)

    print(f"✅ Saved video: {output_path}")
    return output_path


# -----------------------------------------------------------
# Combine all clips into one final video
# -----------------------------------------------------------
def combine_videos(video_paths, output_path):
    print("🎬 Combining all clips...")
    clips = [VideoFileClip(p) for p in video_paths]
    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(output_path, codec="libx264", fps=24)
    print(f"📦 Final combined video saved: {output_path}")


# -----------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------
def make_videos_from_story(story_path):
    with open(story_path, "r", encoding="utf-8") as f:
        story = json.load(f)

    entry_text = story["entry_text"]
    scp_num = story["scp_number"]

    prompts = extract_scene_prompts(entry_text)

    output_dir = os.path.join("output", scp_num)
    os.makedirs(output_dir, exist_ok=True)

    video_paths = []

    for idx, prompt in enumerate(prompts, start=1):
        file_path = os.path.join(output_dir, f"scene_{idx}.mp4")
        generate_video(prompt, file_path)
        video_paths.append(file_path)

    final_path = os.path.join(output_dir, f"{scp_num}_short.mp4")
    combine_videos(video_paths, final_path)

    return final_path
