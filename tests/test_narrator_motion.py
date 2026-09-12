import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
from narrator_motion import MIN_SHOT_SECONDS, build_motion_plan


def test_motion_continues_during_unbroken_speech_and_visits_both_sides():
    manifest = {"scenes": [{"headline": "Fact"}] * 6,
                "mouth_timeline": [{"start": 0, "end": 30, "mouth": "wide"}]}
    plan = build_motion_plan(manifest, 30, [(i, i + 5) for i in range(0, 30, 5)])
    layouts = {s["layout"] for s in plan["shots"]}
    assert {"bottom-left", "bottom-right", "bottom-center", "close-left", "close-right"} <= layouts
    assert len(plan["gestures"]) >= 10
    assert plan["mouth_timeline"] == manifest["mouth_timeline"]
    assert all(p["pose"] == "rest" for p in plan["gestures"][1::2])


def test_fast_scene_cuts_do_not_whip_the_narrator_around():
    plan = build_motion_plan({"scenes": [{}] * 20}, 10, [(i / 2, (i + 1) / 2) for i in range(20)])
    assert all(b["start"] - a["start"] >= MIN_SHOT_SECONDS for a, b in zip(plan["shots"], plan["shots"][1:]))


def test_car_details_stay_wide_and_stats_reserve_space():
    scenes = [{"headline": "Engine", "media_type": "engine"},
              {"headline": "Power", "stat_label": "Power", "stat_value": "760 hp"},
              {"headline": "Inside", "media_type": "interior"}]
    plan = build_motion_plan({"scenes": scenes}, 15, [(0, 5), (5, 10), (10, 15)])
    assert [s["framing"] for s in plan["shots"]] == ["half", "bust", "half"]
    assert plan["safe_top"] > build_motion_plan({}, 15, [])['safe_top']


def test_speech_fallback_uses_word_times_and_leaves_silence_empty():
    plan = build_motion_plan({"word_timeline": [{"word": "Hello", "start": 1, "end": 2}]}, 4, [])
    assert plan["mouth_timeline"] == [{"start": 1, "end": 2, "mouth": "small"}]
    assert all(0 <= g["start"] < 4 for g in plan["gestures"])


def test_plan_is_repeatable_and_long_scene_has_some_camera_variety():
    first = build_motion_plan({}, 20, [(0, 20)])
    assert first == build_motion_plan({}, 20, [(0, 20)])
    assert len(first["shots"]) >= 3


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_rejects_invalid_duration(duration):
    with pytest.raises(ValueError):
        build_motion_plan({}, duration, [])
