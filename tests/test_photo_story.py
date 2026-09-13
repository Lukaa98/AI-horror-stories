import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars" / "automation"))

from photo_story import photo_metadata, collect_photo_sections, photo_story_timeline
from narrator_motion import build_motion_plan
from photo_story_video import build_photo_tracks, detail_box
from single_car_short import order_media_for_scenes, gather_extra_media, gather_photo_script_hints


def photo(section, role, index, label="Wheel"):
    return {"path": f"{index}.png", "type": section if role != "detail" else "detail",
            **photo_metadata({"id": "same-id", "section": section, "role": role, "label": label}, index)}


def fixture():
    photos = [photo("exterior", "hero", 0), photo("exterior", "detail", 1),
              photo("exterior", "detail", 2), photo("interior", "hero", 3),
              photo("interior", "detail", 4)]
    scenes = [{"media_type": p["type"], "photo_label": p["cue_label"], "text": "A detail."} for p in photos]
    manifest = {"scenes": scenes, "media": order_media_for_scenes(scenes, photos),
                "photo_sections": collect_photo_sections(photos),
                "mouth_timeline": [{"start": 0, "end": 20, "mouth": "oh"}]}
    boundaries = [(i * 4, (i + 1) * 4) for i in range(5)]
    return manifest, boundaries


def test_legacy_is_opt_out_and_ids_disambiguate_duplicate_names():
    assert photo_metadata({"label": "Wheel"}, 0) == {}
    assert photo_story_timeline({}, [(0, 4)]) == []
    assert photo("exterior", "detail", 1)["photo_id"] != photo("exterior", "detail", 2)["photo_id"]


def test_hero_is_persistent_details_exact_and_chapter_switch_is_measured():
    manifest, bounds = fixture()
    cues = photo_story_timeline(manifest, bounds)
    assert [c["hero"] for c in cues] == ["0.png", "0.png", "0.png", "3.png", "3.png"]
    assert [c["side"] for c in cues] == ["right"] * 3 + ["left"] * 2
    assert cues[0]["detail"] is None
    assert cues[1]["detail"]["photo_id"] == manifest["media"][1]["photo_id"]
    manifest["scenes"][1]["photo_label"] = None
    assert photo_story_timeline(manifest, bounds)[1]["detail"] is None


def test_motion_uses_card_side_gaze_and_preserves_mouth_track():
    manifest, bounds = fixture()
    plan = build_motion_plan(manifest, 20, bounds)
    assert plan == build_motion_plan(manifest, 20, bounds)
    assert plan["shots"] == [{"start": 0, "layout": "bottom-right", "framing": "half"},
                             {"start": 12, "layout": "bottom-left", "framing": "half"}]
    assert {g["pose"] for g in plan["gestures"]} >= {"presentLeft", "presentRight", "rest"}
    assert any(e["look_at"] == [138, 640] for e in plan["expressions"])
    assert any(e["look_at"] == [402, 640] for e in plan["expressions"])
    assert plan["mouth_timeline"][0]["mouth"] == "oh"


def test_rival_and_missing_hero_are_safe_fallbacks():
    manifest, bounds = fixture()
    manifest["scenes"][1]["rival_make"] = "Porsche"
    manifest["media"][1] = {"path": "rival.png", "type": "exterior"}
    cue = photo_story_timeline(manifest, bounds)[1]
    assert cue["hero"] == "rival.png" and cue["detail"] is None
    manifest["photo_sections"][1]["hero"] = None
    assert photo_story_timeline(manifest, bounds)[4]["detail"] is None


def test_generic_scene_cannot_steal_grouped_detail():
    hero, detail = photo("interior", "hero", 0), photo("interior", "detail", 1)
    assert order_media_for_scenes([{"media_type": "detail"}], [detail, hero]) == [hero]


def test_track_merges_hero_and_handles_broken_detail(tmp_path):
    from PIL import Image
    manifest, bounds = fixture()
    for item in manifest["media"]:
        Image.new("RGB", (300, 200), (50, 120, 60)).save(tmp_path / item["path"])
    cues = photo_story_timeline(manifest, bounds)
    hero, cards, diagnostics = build_photo_tracks(cues, [], tmp_path, (0, 0, 540, 300), (540, 960), 20, tmp_path / "frames")
    assert len(hero.clips) == 2
    assert len(cards) == len(diagnostics) == 3
    assert hero.get_frame(19.9).shape == (300, 540, 3)
    for side in ("left", "right"):
        x, y, w, h = detail_box((540, 960), side)
        assert 0 <= x < x + w <= 540 and 480 < y < y + h < 960
    (tmp_path / "1.png").unlink()
    _, cards, diagnostics = build_photo_tracks(cues, [], tmp_path, (0, 0, 540, 300), (540, 960), 20, tmp_path / "frames")
    assert len(cards) == 2
    hero.close()


def test_download_and_hints_use_identical_photo_identity(tmp_path, monkeypatch):
    import single_car_short as module
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    path = images_dir / "car.png"
    path.touch()
    monkeypatch.setattr(module, "_download_car_photo", lambda *a: path)
    monkeypatch.setattr(module, "blur_license_plates", lambda *a: None)
    monkeypatch.setattr(module, "_describe_photo_for_script", lambda *a: "Visible dials.")
    items = [{"section": "interior", "role": "detail", "label": "Gauges", "url": "https://example.com/photo.jpg"}]
    media = gather_extra_media(items, images_dir, {})
    hints = gather_photo_script_hints({}, items, images_dir, "Lotus")
    assert hints[0].startswith(media[0]["cue_label"] + " photo:")
    assert "Section: interior" in hints[0]
