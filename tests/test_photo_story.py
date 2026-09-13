import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars" / "automation"))

from photo_story import (MAX_CLOSEUPS, SLOTS, collect_photo_sections, photo_metadata,
                         photo_story_timeline, slot_of)
from narrator_motion import build_motion_plan
from photo_story_video import build_photo_tracks
from photo_story import collage_rows, tile_centers
from single_car_short import order_media_for_scenes, gather_extra_media, gather_photo_script_hints


def main_photo(slot, category, index):
    return {"path": f"{index}.png", "type": "exterior", "category": category}


def closeup(slot, index, label="Exhaust tip", identity="same-id"):
    return {"path": f"{index}.png", "type": "detail", "category": "other_detail",
            **photo_metadata({"id": identity, "slot": slot, "label": label}, index)}


def fixture():
    """Front main photo + two close-ups, interior main photo + one close-up."""
    photos = [main_photo("front", "exterior_front", 0), closeup("front", 1), closeup("front", 2, "Splitter"),
              main_photo("interior", "interior", 3), closeup("interior", 4, "Gauges")]
    photos[3]["type"] = "interior"
    scenes = [{"media_type": p["type"], "photo_label": p.get("cue_label"), "text": "A detail."} for p in photos]
    manifest = {"scenes": scenes, "media": order_media_for_scenes(scenes, photos),
                "photo_sections": collect_photo_sections(photos),
                "mouth_timeline": [{"start": 0, "end": 20, "mouth": "oh"}]}
    return manifest, [(i * 4.0, i * 4.0 + 4.0) for i in range(len(scenes))]


def test_slots_are_the_five_pipeline_fields_and_ids_disambiguate_duplicates():
    assert SLOTS == ("front", "side", "rear", "engine", "interior")
    # Same id and label in two input positions must still address two photos.
    assert closeup("front", 1)["photo_id"] != closeup("front", 2)["photo_id"]
    # A bare extra with no slot stays ungrouped, exactly as before.
    assert photo_metadata({"id": "x", "label": "Gauges"}, 0) == {}
    # v11.17 entries are mapped onto a slot rather than dropped.
    assert photo_metadata({"id": "x", "section": "engine", "role": "hero"}, 0)["slot"] == "engine"


def test_sections_need_a_closeup_so_ordinary_builds_keep_the_old_layout():
    plain = [main_photo("front", "exterior_front", 0), main_photo("interior", "interior", 1)]
    assert collect_photo_sections(plain) == []
    sections = collect_photo_sections(fixture()[0]["media"])
    assert [s["id"] for s in sections] == ["front", "interior"]
    assert len(sections[0]["photos"]) == 2
    assert slot_of(sections[0]["hero"]) == "front"


def test_closeups_never_replace_the_main_photo_and_only_an_exact_match_highlights():
    manifest, bounds = fixture()
    cues = photo_story_timeline(manifest, bounds)
    front = [c for c in cues if c["slot"] == "front"]
    assert front, "front chapter must produce cues"
    # Every front cue shows the front main photo, even the scenes whose own
    # selected media is one of the close-ups.
    assert {c["hero"] for c in front} == {"0.png"}
    # All of that slot's close-ups ride along for the whole chapter.
    assert all(len(c["closeups"]) == 2 for c in front)
    assert sum(c["active"] is not None for c in cues) == 3
    # A scene with no photo_label highlights nothing.
    manifest["scenes"][1]["photo_label"] = None
    assert photo_story_timeline(manifest, bounds)[1]["active"] is None


def test_rival_and_missing_section_fall_back_to_the_plain_selected_photo():
    manifest, bounds = fixture()
    manifest["scenes"][0] = {"media_type": "exterior", "rival_make": "Porsche", "rival_model": "Cayman"}
    cue = photo_story_timeline(manifest, bounds)[0]
    assert cue["slot"] is None and cue["closeups"] == [] and cue["hero"] == manifest["media"][0]["path"]


def test_motion_keeps_its_framing_cycle_and_points_at_the_active_tile():
    manifest, bounds = fixture()
    manifest["word_timeline"] = [{"start": 0, "end": 20}]
    media_box = (16, 176, 1047, 614)
    plan = build_motion_plan(manifest, 20.0, bounds, (1080, 1920), fps=24, media_box=media_box)
    # Nothing floats over the lower half any more, so the framing cycle is
    # free again -- it must not collapse to a single pinned layout.
    assert len({s["layout"] for s in plan["shots"]}) > 1
    # Scenes about one specific close-up get a hand raised toward it.
    assert any(g["pose"].startswith("present") for g in plan["gestures"])
    # Never two arm poses fighting over the same instant.
    starts = [g["start"] for g in plan["gestures"]]
    assert starts == sorted(starts) and len(starts) == len(set(starts))
    assert [m["mouth"] for m in plan["mouth_timeline"]] == ["oh"]


def test_gaze_aims_up_at_the_photos_and_across_at_the_right_tile():
    manifest, bounds = fixture()
    manifest["word_timeline"] = [{"start": 0, "end": 20}]
    plan = build_motion_plan(manifest, 20.0, bounds, (1080, 1920), fps=24,
                             media_box=(16, 176, 1047, 614))
    aims = [e["aim"] for e in plan["expressions"]]
    assert aims, "every scene gets a look"
    # The photos are above the character, so the vertical aim is negative --
    # the old point-through-CTM version clamped every one of these the wrong
    # way regardless of where the target was.
    assert all(-1.0 <= a[0] <= 1.0 and -1.0 <= a[1] <= 1.0 for a in aims)
    assert all(a[1] < 0 for a in aims)
    # A tile on the far side from the character pulls the eyes that way.
    pointing = [e for e in plan["expressions"] if e["brows"] and e["end"] - e["start"] > 1.0]
    assert pointing


def test_tile_centers_track_the_rendered_rows():
    assert tile_centers(0) == []
    # A lone tile is centred horizontally, not stretched across the row.
    assert tile_centers(1)[0][0] == 0.5
    two = tile_centers(2)
    assert two[0][0] < 0.5 < two[1][0]
    assert len(tile_centers(4)) == 4
    # Four close-ups means two rows, so the last two sit lower than the first.
    assert tile_centers(4)[3][1] > tile_centers(4)[0][1]
    # Tiles always sit below the main photo.
    assert all(y > 0.5 for _, y in tile_centers(3))


def test_collage_rows_match_the_approved_layout():
    assert collage_rows(0) == []
    assert collage_rows(1) == [1]
    assert collage_rows(2) == [2]
    assert collage_rows(3) == [3]
    assert collage_rows(MAX_CLOSEUPS) == [2, 2]
    assert collage_rows(9) == [2, 2]


def test_track_merges_chapters_and_survives_a_dead_closeup(tmp_path):
    from PIL import Image
    manifest, bounds = fixture()
    for item in manifest["media"]:
        Image.new("RGB", (300, 200), (50, 120, 60)).save(tmp_path / item["path"])
    cues = photo_story_timeline(manifest, bounds)
    media, cards, diagnostics = build_photo_tracks(
        cues, [], tmp_path, (0, 0, 540, 300), (540, 960), 20, tmp_path / "frames")
    # The floating detail cards are gone; the collage absorbed them.
    assert cards == []
    # One chapter picture per highlight change, not one per scene.
    assert len(media.clips) == len(diagnostics) == len(cues)
    assert media.get_frame(19.9).shape == (300, 540, 3)
    assert diagnostics[0]["slot"] == "front" and len(diagnostics[0]["closeups"]) == 2
    (tmp_path / "1.png").unlink()
    _, _, diagnostics = build_photo_tracks(
        cues, [], tmp_path, (0, 0, 540, 300), (540, 960), 20, tmp_path / "frames")
    # The dead close-up drops out of its chapter; the chapter still renders.
    assert len(diagnostics[0]["closeups"]) == 1
    media.close()


def test_download_and_hints_use_identical_photo_identity(tmp_path, monkeypatch):
    import single_car_short as module
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    path = images_dir / "car.png"
    path.touch()
    monkeypatch.setattr(module, "_download_car_photo", lambda *a: path)
    monkeypatch.setattr(module, "blur_license_plates", lambda *a: None)
    monkeypatch.setattr(module, "_describe_photo_for_script", lambda *a: "Visible dials.")
    items = [{"id": "gauges", "slot": "interior", "label": "Gauges", "url": "https://example.com/photo.jpg"}]
    media = gather_extra_media(items, images_dir, {})
    hints = gather_photo_script_hints({}, items, images_dir, "Lotus")
    assert media[0]["type"] == "detail", "a nested close-up is never an overview"
    assert f'photo_label: "{media[0]["cue_label"]}"' in hints[0]
    assert hints[0].startswith("CLOSE-UP (nested under the Interior main photo)")
