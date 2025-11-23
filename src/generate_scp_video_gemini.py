import os
import time
from google import genai
from google.genai import types
from moviepy.editor import VideoFileClip, concatenate_videoclips
import json
from global_style import GLOBAL_VIDEO_STYLE

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# -----------------------------------------------------------
# Extract Scene Prompt 1 + 2 from entry_text
# -----------------------------------------------------------
def extract_scene_prompts(text):
    prompts = []
    for line in text.splitlines():
        if line.lower().startswith("scene prompt"):
            cleaned = line.split(":", 1)[1].strip()
            prompts.append(cleaned)

    if len(prompts) == 0:
        prompts = [
            "A gentle creature resting in its natural habitat",
            "A slow pan reveal of the creature interacting with its environment"
        ]

    return prompts[:2]


# -----------------------------------------------------------
# Generate a single vertical Veo video with Pokémon/NatGeo Style
# -----------------------------------------------------------
def generate_video(prompt, output_path):

    # Combine prompt + global cinematic Pokémon Geographic style
    styled_prompt = (
        f"{prompt}. "
        f"{GLOBAL_VIDEO_STYLE}. "
        "Wildlife documentary realism, natural habitat, gentle motion, "
        "soft sunlight, expressive eyes, grounded animal anatomy."
    )

    print(f"🎥 Generating 8s Veo video for:\n{styled_prompt}\n")

    operation = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt=styled_prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio="9:16",
            resolution="720p",
            duration_seconds="8",
            person_generation="allow_all"
        )
    )

    # Poll until done
    while not operation.done:
        print("⏳ Waiting for video generation...")
        time.sleep(10)
        operation = client.operations.get(operation)

    # Download
    video = operation.response.generated_videos[0].video
    client.files.download(file=video)
    video.save(output_path)

    print(f"✅ Saved video: {output_path}")
    return output_path


# -----------------------------------------------------------
# Combine 2 clips into 1 final YouTube Short
# -----------------------------------------------------------
def combine_videos(video_paths, output_path):
    print("🎬 Combining clips...")
    clips = [VideoFileClip(p) for p in video_paths]
    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(output_path, codec="libx264", fps=24)
    print(f"📦 Final combined video saved: {output_path}")


# -----------------------------------------------------------
# Main pipeline (still uses SCP files)
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

    # final merged video
    combined_path = os.path.join(output_dir, f"{scp_num}_short.mp4")
    combine_videos(video_paths, combined_path)

    return combined_path
