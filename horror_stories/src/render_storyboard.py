import argparse
import json
from pathlib import Path

from video_pipeline.image_generator import generate_scene_images
from video_pipeline.narration import generate_voice_narration
from video_pipeline.short_editor import build_short_video
from video_pipeline.story_generator import save_storyboard
from video_pipeline.subtitle_transcriber import transcribe_subtitles


def _write_storyboard_to_run_dir(storyboard, run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    storyboard_path = run_dir / "storyboard.json"
    storyboard_path.write_text(json.dumps(storyboard, indent=2, ensure_ascii=False), encoding="utf-8")
    return run_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("storyboard_file", help="Path to a storyboard JSON file")
    parser.add_argument(
        "--output-root",
        help="Optional output root directory. Defaults to horror_stories/src/output",
    )
    parser.add_argument(
        "--run-name",
        help="Optional fixed folder name inside the output root, e.g. video_2",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    output_root = Path(args.output_root) if args.output_root else (base_dir / "output")
    output_root.mkdir(exist_ok=True)

    storyboard_path = Path(args.storyboard_file)
    storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    if args.run_name:
        run_dir = _write_storyboard_to_run_dir(storyboard, output_root / args.run_name)
    else:
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
