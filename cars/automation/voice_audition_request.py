"""Generate quick voice-preset auditions without building a full video.

single_car_short.py already generates these auditions as a side effect of
a real build, but that build takes 20+ minutes (research, scraping,
rendering) just to get to a handful of mp3s the creator wants to listen to
and pick between. This script skips straight to the TTS step on a fixed
sample script, so voice options can be compared in well under a minute.
"""
import argparse
import json
from pathlib import Path

from generate_sample import ROOT
from single_car_short import generate_voice_auditions

OUTPUT_ROOT = ROOT / "cars" / "voice-auditions"

# The same Mustang script pulled from a real single-car build, read back in
# every preset -- representative real-world narration length/pacing rather
# than a generic test sentence.
DEFAULT_SCRIPT = (
    "Meet the 2020 Ford Mustang, an iconic blend of heritage and modern muscle. "
    "With aggressive styling and a powerful presence, it's more than just good looks. "
    "Let's dig into what makes it roar. Under the hood, you've got a choice, but the "
    "5.0-liter V8 is the real star, pushing out 460 horsepower. It delivers a thrilling "
    "top-end rush, making every ride an adventure. Rear-wheel drive gives you that classic "
    "Mustang feel. It sticks in the corners and rockets out of them, with a six-speed manual "
    "that truly connects you to the road. Stack it against the Chevy Camaro's 455 horsepower, "
    "and the Mustang holds its own with a balance of power and agility. Choices, choices. "
    "Inside, it's all about the driver. Supportive seats and intuitive controls make it just "
    "as comfortable for commute as it is on a track day. And if you're into tuning, the Mustang "
    "offers plenty of potential. From exhaust upgrades to turbocharging, it's a playground for "
    "modifications. So, would you choose the pure muscle of the Mustang? Or do you swing another "
    "way? Let us know what drives you."
)


def build_voice_auditions(audition_id, text=None, chosen_preset="onyx"):
    text = (text or DEFAULT_SCRIPT).strip()
    output_dir = OUTPUT_ROOT / audition_id
    output_dir.mkdir(parents=True, exist_ok=True)
    files = generate_voice_auditions(text, output_dir, chosen_preset)
    result = {"text": text, "chosen_preset": chosen_preset, "files": files}
    (output_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate quick TTS voice auditions from a fixed sample script.")
    parser.add_argument("--audition-id", required=True)
    parser.add_argument("--text", default=None, help="Override the default sample script.")
    parser.add_argument("--preset", default="onyx", help="Current chosen voice preset, included for comparison.")
    args = parser.parse_args()
    result = build_voice_auditions(args.audition_id, text=args.text, chosen_preset=args.preset)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
