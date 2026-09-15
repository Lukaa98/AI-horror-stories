import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))
from narrator_motion import MIN_SHOT_SECONDS, build_motion_plan


def test_motion_continues_during_unbroken_speech_and_keeps_off_the_table():
    """The spec table holds the lower-left for the whole video, so the
    framing cycle is right and centre only -- a left-anchored shot would put
    the character straight through it. It must still vary, though."""
    manifest = {"scenes": [{"headline": "Fact"}] * 6,
                "mouth_timeline": [{"start": 0, "end": 30, "mouth": "wide"}]}
    plan = build_motion_plan(manifest, 30, [(i, i + 5) for i in range(0, 30, 5)])
    layouts = {s["layout"] for s in plan["shots"]}
    assert not any(layout.endswith("left") for layout in layouts), layouts
    assert {"bottom-right", "bottom-center", "close-right"} <= layouts
    assert len(plan["gestures"]) >= 10
    assert plan["mouth_timeline"] == manifest["mouth_timeline"]
    assert all(p["pose"] == "rest" for p in plan["gestures"][1::2])


def test_fast_scene_cuts_do_not_whip_the_narrator_around():
    plan = build_motion_plan({"scenes": [{}] * 20}, 10, [(i / 2, (i + 1) / 2) for i in range(20)])
    assert all(b["start"] - a["start"] >= MIN_SHOT_SECONDS for a, b in zip(plan["shots"], plan["shots"][1:]))


def test_car_details_stay_wide_and_stats_no_longer_force_a_close_up():
    """A stat_label used to force a bust close-up. Once the script rules put
    a stat row on every hard-number beat it fired on nearly every scene, and
    since a shot is only recorded when it differs from the last one, run #189
    held a single close-right bust from 0.0s to 47.6s."""
    scenes = [{"headline": "Engine", "media_type": "engine"},
              {"headline": "Power", "stat_label": "Power", "stat_value": "760 hp"},
              {"headline": "Inside", "media_type": "interior"}]
    plan = build_motion_plan({"scenes": scenes}, 15, [(0, 5), (5, 10), (10, 15)])
    assert [s["framing"] for s in plan["shots"]] == ["half", "half", "half"]
    assert plan["safe_top"] > build_motion_plan({}, 15, [])['safe_top']


def test_a_stat_on_every_scene_still_gets_a_varied_camera():
    """The regression run #189 actually shipped: every scene carrying a stat
    resolved to the same shot, so the character never re-framed."""
    scenes = [{"stat_label": "Power", "stat_value": f"{i} hp"} for i in range(9)]
    boundaries = [(i * 6.0, (i + 1) * 6.0) for i in range(9)]
    plan = build_motion_plan({"scenes": scenes}, 54.0, boundaries)
    assert len(plan["shots"]) >= 5, plan["shots"]
    framings = [s["framing"] for s in plan["shots"]]
    # Every body camera the rig has gets used: the whole figure, the top
    # two-thirds, and a chest-up zoom.
    assert {"full", "half", "bust"} <= set(framings), framings
    # Two hands-visible framings per close-up, so the gestures are on screen
    # for most of the video rather than cropped at the chest.
    assert framings.count("half") + framings.count("full") > 2 * framings.count("bust")
    # The video opens on the whole character rather than a crop of it.
    assert plan["shots"][0]["framing"] == "full"


def test_no_single_shot_is_held_for_most_of_the_video():
    scenes = [{"stat_label": "Power", "stat_value": "760 hp"} for _ in range(9)]
    boundaries = [(i * 6.0, (i + 1) * 6.0) for i in range(9)]
    plan = build_motion_plan({"scenes": scenes}, 54.0, boundaries)
    spans = [b["start"] - a["start"] for a, b in zip(plan["shots"], plan["shots"][1:])]
    spans.append(54.0 - plan["shots"][-1]["start"])
    assert max(spans) <= 20.0, plan["shots"]


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
