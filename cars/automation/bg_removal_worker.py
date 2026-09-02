"""Standalone background-removal worker, run as a subprocess in its own
isolated virtualenv (see background_removal.py) instead of importing rembg
into the main process.

rembg pins pillow>=12, which conflicts with this repo's own
Pillow>=10.4,<11 pin (moviepy<2 and the rest of the pipeline are only
verified against that range) -- running it as a subprocess in a separate
venv keeps that newer Pillow fully isolated from everything else instead
of forcing a risky, repo-wide Pillow upgrade just for this one feature.
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from PIL import Image
    from rembg import remove

    with Image.open(args.input) as image:
        result = remove(image.convert("RGB"))
    result.save(args.output)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"bg_removal_worker failed: {exc}", file=sys.stderr)
        sys.exit(1)
