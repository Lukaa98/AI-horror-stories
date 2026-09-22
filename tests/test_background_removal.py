import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars" / "automation"))

import background_removal  # noqa: E402


def test_remove_background_returns_new_path_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(background_removal, "VENV_DIR", tmp_path / "venv")
    image_path = tmp_path / "front-01.jpg"
    image_path.write_bytes(b"fake-jpeg-bytes")

    def fake_ensure_venv():
        return Path("/fake/python")

    def fake_run(cmd, **kwargs):
        # Simulate the worker subprocess actually producing the cutout.
        output_path = Path(cmd[cmd.index("--output") + 1])
        output_path.write_bytes(b"fake-png-bytes")

    monkeypatch.setattr(background_removal, "_ensure_venv", fake_ensure_venv)
    monkeypatch.setattr(background_removal.subprocess, "run", fake_run)

    result = background_removal.remove_background(image_path)

    assert result == image_path.with_name("front-01-nobg.png")
    assert result.exists()


def test_remove_background_fails_open_on_any_error(tmp_path, monkeypatch):
    image_path = tmp_path / "front-01.jpg"
    image_path.write_bytes(b"fake-jpeg-bytes")

    def raise_error():
        raise RuntimeError("no network")

    monkeypatch.setattr(background_removal, "_ensure_venv", raise_error)

    result = background_removal.remove_background(image_path)

    assert result == image_path


def test_remove_background_fails_open_when_output_missing(tmp_path, monkeypatch):
    image_path = tmp_path / "front-01.jpg"
    image_path.write_bytes(b"fake-jpeg-bytes")

    monkeypatch.setattr(background_removal, "_ensure_venv", lambda: Path("/fake/python"))
    monkeypatch.setattr(background_removal.subprocess, "run", lambda cmd, **kwargs: None)

    result = background_removal.remove_background(image_path)

    assert result == image_path


def test_ensure_venv_skips_setup_when_already_built(tmp_path, monkeypatch):
    monkeypatch.setattr(background_removal, "VENV_DIR", tmp_path / "venv")
    python_path = background_removal._venv_python()
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\n")

    calls = []
    monkeypatch.setattr(background_removal.subprocess, "run", lambda *a, **k: calls.append(a))

    result = background_removal._ensure_venv()

    assert result == python_path
    assert calls == []
