"""Remove the background from exterior car photos so they composite onto
the format's white canvas as a cutout instead of carrying their original
backdrop -- interior/engine/detail shots are left alone since they're
meant to read as a real photo, not a subject cutout.

rembg (the actual segmentation library) needs pillow>=12, which conflicts
with this repo's own Pillow>=10.4,<11 pin, so it's installed into and run
from its own isolated virtualenv via a subprocess instead of the shared
environment -- see bg_removal_worker.py for the actual removal call. This
fails open (leaves the original image untouched) on any error, matching
plate_blur.py's established pattern: a missed cutout is a smaller problem
than blocking the whole render.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV_DIR = ROOT / ".cache" / "bg-removal-venv"
WORKER_SCRIPT = Path(__file__).resolve().with_name("bg_removal_worker.py")


def _venv_python():
    return VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _ensure_venv():
    python_path = _venv_python()
    if python_path.exists():
        return python_path
    VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True, capture_output=True)
    subprocess.run(
        [str(python_path), "-m", "pip", "install", "--quiet", "rembg", "onnxruntime"],
        check=True, capture_output=True, timeout=600,
    )
    return python_path


def remove_background(image_path):
    """Replace image_path's exterior photo with a transparent-background
    PNG cutout, in place-ish: writes a new "<stem>-nobg.png" file next to
    the original and returns its path, or returns image_path unchanged if
    removal wasn't possible for any reason."""
    image_path = Path(image_path)
    output_path = image_path.with_name(f"{image_path.stem}-nobg.png")
    try:
        python_path = _ensure_venv()
        subprocess.run(
            [str(python_path), str(WORKER_SCRIPT), "--input", str(image_path), "--output", str(output_path)],
            check=True, capture_output=True, timeout=120,
        )
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path
    except Exception:
        pass
    return image_path
