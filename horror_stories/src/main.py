from pathlib import Path

from video_pipeline.image_generator import generate_scene_images
from video_pipeline.narration import generate_voice_narration
from video_pipeline.short_editor import build_short_video
from video_pipeline.story_generator import generate_storyboard, save_storyboard
from video_pipeline.subtitle_transcriber import transcribe_subtitles


def main():
    base_dir = Path(__file__).resolve().parent
    output_root = base_dir / "output"
    output_root.mkdir(exist_ok=True)

    print("Generating storyboard for a vertical short...")
    storyboard = generate_storyboard()
    run_dir = save_storyboard(storyboard, output_root)

    print("\nNarration:\n")
    print(storyboard["narration"])

    print("\nGenerating scene images...")
    image_paths = generate_scene_images(storyboard, run_dir / "images")

    print("\nGenerating voiceover...")
    narration_path = run_dir / "narration.mp3"
    narration_path = generate_voice_narration(storyboard["narration"], narration_path)

    print("\nGenerating timed subtitles...")
    subtitles = transcribe_subtitles(narration_path)

    print("\nEditing final short...")
    final_path = run_dir / "final_short.mp4"
    build_short_video(storyboard, image_paths, narration_path, final_path, subtitles=subtitles)

    print("\nDone!")
    print(f"Run folder: {run_dir}")
    print(f"Final video: {final_path}")


if __name__ == "__main__":
    main()
