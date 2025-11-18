import os
import time
import base64
from google import genai
from google.genai import types
from moviepy.editor import VideoFileClip, concatenate_videoclips
import json

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# -----------------------------------------------------------
# Extract the 2 scene prompts (Scene Prompt 1 + 2)
# -----------------------------------------------------------
def extract_scene_prompts(text):
    prompts = []
    for line in text.splitlines():
        if line.lower().startswith("scene prompt"):
            cleaned = line.split(":", 1)[1].strip()
            prompts.append(cleaned)

    # fallback
    if len(prompts) == 0:
        prompts = [
            "a dark breathing hallway narrowing as lights flicker",
            "a vault door cracked open as fog spills out silently"
        ]

    return prompts[:2]


# -----------------------------------------------------------
# Generate a single vertical 9:16 Veo video
# -----------------------------------------------------------
def generate_video(prompt, output_path):
    print(f"🎥 Generating 8s Veo video for:\n{prompt}")

    operation = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt=prompt,
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

    # Download MP4
    video = operation.response.generated_videos[0].video
    client.files.download(file=video)
    video.save(output_path)

    print(f"✅ Saved video: {output_path}")
    return output_path


# -----------------------------------------------------------
# Combine videos into one TikTok/Short style video
# -----------------------------------------------------------
def combine_videos(video_paths, output_path):
    print("🎬 Combining clips...")
    clips = [VideoFileClip(p) for p in video_paths]
    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(output_path, codec="libx264", fps=24)
    print(f"📦 Final combined video saved: {output_path}")


# -----------------------------------------------------------
# Main function used by run_scp_pipeline
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

    # Combine both 8-second clips
    combined_path = os.path.join(output_dir, f"{scp_num}_short.mp4")
    combine_videos(video_paths, combined_path)

    return combined_path
