import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

from voice_audition_request import DEFAULT_SCRIPT, build_voice_auditions  # noqa: E402


def test_build_voice_auditions_writes_result_json_with_the_default_script(tmp_path, monkeypatch):
    import single_car_short
    import voice_audition_request

    monkeypatch.setattr(voice_audition_request, "OUTPUT_ROOT", tmp_path)
    synthesized = []
    monkeypatch.setattr(
        single_car_short, "synthesize_narration",
        lambda script, path, preset=None, speed=None: (synthesized.append(preset), Path(path).write_bytes(b"x")),
    )

    result = build_voice_auditions("aud-1")

    assert result["text"] == DEFAULT_SCRIPT
    assert result["chosen_preset"] == "onyx"
    assert "onyx" in result["files"]
    result_path = tmp_path / "aud-1" / "result.json"
    assert result_path.exists()
    assert json.loads(result_path.read_text(encoding="utf-8")) == result
    for relative in result["files"].values():
        assert (tmp_path / relative).exists()


def test_build_voice_auditions_accepts_custom_text_and_preset(tmp_path, monkeypatch):
    import single_car_short
    import voice_audition_request

    monkeypatch.setattr(voice_audition_request, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(
        single_car_short, "synthesize_narration",
        lambda script, path, preset=None, speed=None: Path(path).write_bytes(b"x"),
    )

    result = build_voice_auditions("aud-2", text="Custom sample text.", chosen_preset="british_dry_wit")

    assert result["text"] == "Custom sample text."
    assert result["chosen_preset"] == "british_dry_wit"
    assert "british_dry_wit" in result["files"]
