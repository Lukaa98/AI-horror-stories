import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

import compare_short as compare  # noqa: E402


def _cars(count=3, photos=True):
    out = []
    for index in range(count):
        out.append({
            "label": f"Car {index + 1}",
            "url": f"https://carsandbids.com/auctions/x{index}/car-{index}",
            "photos": {"front": f"https://example.com/{index}-front.jpg"} if photos else {},
        })
    return out


def _scene(car_index, narration="Words about the car here.", headline="A REAL CLAIM",
           media_type="exterior"):
    return {"car_index": car_index, "media_type": media_type, "headline": headline,
            "narration": narration, "photo_label": None, "stat_label": None,
            "stat_value": None, "stat_label_2": None, "stat_value_2": None,
            "rival_make": None, "rival_model": None, "main_horsepower": None,
            "rival_horsepower": None, "main_quarter_mile_seconds": None,
            "rival_quarter_mile_seconds": None}


def _package(cars, words=None):
    """A countdown that breaks none of the rules, as a starting point."""
    low, high = compare.COMPARE_STYLES[len(cars)]["words"]
    target = words if words is not None else (low + high) // 2
    scenes = [_scene(None, "Here is the theme and a reason to stay to the end.")]
    for index in range(len(cars)):
        scenes.append(_scene(index, f"Number thing, the car {index}, with an engine and a price."))
    scenes.append(_scene(len(cars) - 1, "Now the catch, which costs real money to fix."))
    # Pad the first body scene to hit the word target exactly.
    script_words = sum(len(s["narration"].split()) for s in scenes)
    if script_words < target:
        scenes[1]["narration"] += " filler" * (target - script_words)
    package = {"theme": "three cheap V8s", "youtube_title": "THREE CHEAP V8s",
               "ranking": list(range(len(cars))), "scenes": scenes}
    package["script"] = " ".join(s["narration"] for s in scenes)
    return package


def test_parse_cars_takes_two_or_three():
    assert len(compare.parse_cars(json.dumps(_cars(2)))) == 2
    assert len(compare.parse_cars(json.dumps(_cars(3)))) == 3


@pytest.mark.parametrize("count", [1, 4])
def test_parse_cars_refuses_any_other_count(count):
    """A countdown of one is not a countdown, and a countdown of four is a
    different video from the one that was asked for -- neither is a thing
    to discover after it has been rendered."""
    with pytest.raises(SystemExit) as error:
        compare.parse_cars(json.dumps(_cars(count)))
    assert str(count) in str(error.value)


def test_parse_cars_refuses_a_car_with_no_url_and_no_name():
    cars = _cars(3)
    cars[1]["url"] = ""
    cars[1]["label"] = ""
    with pytest.raises(SystemExit) as error:
        compare.parse_cars(json.dumps(cars))
    assert "Car 2" in str(error.value)


def test_parse_cars_keeps_the_plate_boxes_apart_from_the_urls():
    """The exact trap the single-car build fell into: a blur box smuggled
    into the photo dict is dropped when that dict is rebuilt, and the plate
    ships readable."""
    cars = _cars(3)
    cars[0]["photos"]["blur"] = {"front": {"x": 0.1, "y": 0.2, "w": 0.1, "h": 0.1}}
    parsed = compare.parse_cars(json.dumps(cars))
    assert parsed[0]["blur"] == {"front": {"x": 0.1, "y": 0.2, "w": 0.1, "h": 0.1}}
    assert "blur" not in parsed[0]["photos"]


def test_a_clean_countdown_has_no_violations():
    cars = compare.parse_cars(json.dumps(_cars(3)))
    assert compare.compare_violations(_package(cars), cars) == []


def test_a_car_with_no_beat_of_its_own_is_caught():
    """Three cars were asked for and one of them is never mentioned -- the
    video is a two-car countdown wearing a three-car title."""
    cars = compare.parse_cars(json.dumps(_cars(3)))
    package = _package(cars)
    package["scenes"] = [s for s in package["scenes"] if s["car_index"] != 1]
    package["script"] = " ".join(s["narration"] for s in package["scenes"])
    problems = compare.compare_violations(package, cars)
    assert any("car 2 never gets a beat" in p for p in problems), problems


def test_a_one_word_headline_is_caught():
    """AP2 and $50K, as a rule. The opening headline is the only thing on
    screen while somebody decides whether to keep watching, and a fragment
    means nothing to anybody who does not already know the car."""
    cars = compare.parse_cars(json.dumps(_cars(3)))
    package = _package(cars)
    package["scenes"][1]["headline"] = "AP2"
    problems = compare.compare_violations(package, cars)
    assert any("AP2" in p and "one word" in p for p in problems), problems


def test_an_ending_that_summarises_instead_of_turning_is_caught():
    cars = compare.parse_cars(json.dumps(_cars(3)))
    package = _package(cars)
    package["scenes"][-1]["narration"] = "So those are the three cheapest V8s you can buy."
    package["script"] = " ".join(s["narration"] for s in package["scenes"])
    problems = compare.compare_violations(package, cars)
    assert any("catch" in p for p in problems), problems


def test_an_opening_about_one_car_is_caught():
    cars = compare.parse_cars(json.dumps(_cars(3)))
    package = _package(cars)
    package["scenes"][0]["car_index"] = 0
    problems = compare.compare_violations(package, cars)
    assert any("opening scene" in p for p in problems), problems


def test_a_scene_pointing_at_a_car_that_was_not_given_is_caught():
    cars = compare.parse_cars(json.dumps(_cars(2)))
    package = _package(cars)
    package["scenes"][1]["car_index"] = 7
    problems = compare.compare_violations(package, cars)
    assert any("car 7" in p for p in problems), problems


@pytest.mark.parametrize("ranking,ok", [
    ([0, 1, 2], True), ([2, 0, 1], True),
    ([0, 0, 1], False), ([0, 1], False), ([0, 1, 3], False), ("nope", False), (None, False),
])
def test_only_a_real_permutation_counts_as_a_ranking(ranking, ok):
    """The ranking decides which car is the climax and which car the catch
    is spoken over. A repeated index would put one car at two positions and
    silently drop another from the countdown."""
    assert compare._valid_ranking(ranking, 3) is ok


def test_each_scene_shows_the_car_that_scene_is_about():
    """The whole point of carrying car_index. The single-car ordering
    matches on photo type alone, which here would happily put car 1's
    engine bay under a sentence about car 2's engine."""
    media = [
        {"path": "images/car0/manual/front.jpg", "type": "exterior", "category": "front",
         "car_index": 0},
        {"path": "images/car1/manual/front.jpg", "type": "exterior", "category": "front",
         "car_index": 1},
        {"path": "images/car1/manual/engine.jpg", "type": "engine", "category": "engine",
         "car_index": 1},
        {"path": "images/car2/manual/front.jpg", "type": "exterior", "category": "front",
         "car_index": 2},
    ]
    scenes = [
        dict(_scene(None), _fallback_car=2),
        dict(_scene(0), _fallback_car=2),
        dict(_scene(1, media_type="engine"), _fallback_car=2),
        dict(_scene(2), _fallback_car=2),
    ]
    ordered = compare.order_media_for_countdown(scenes, media)
    assert len(ordered) == len(scenes)
    # The hook has no car of its own, so it opens on the car the countdown
    # ends with -- the one the opening line just promised something about.
    assert ordered[0]["car_index"] == 2
    assert ordered[1]["car_index"] == 0
    # Car 1's engine beat gets car 1's engine bay, not whoever's was free.
    assert ordered[2]["car_index"] == 1 and ordered[2]["type"] == "engine"
    assert ordered[3]["car_index"] == 2


def test_a_scene_never_takes_another_cars_photo_when_its_own_is_used_up():
    """Two beats about the same car, one photo between them. The second
    must reuse that car's picture rather than reach into another car's."""
    media = [
        {"path": "a.jpg", "type": "exterior", "category": "front", "car_index": 0},
        {"path": "b.jpg", "type": "exterior", "category": "front", "car_index": 1},
    ]
    scenes = [dict(_scene(0), _fallback_car=1), dict(_scene(0), _fallback_car=1)]
    ordered = compare.order_media_for_countdown(scenes, media)
    assert [item["car_index"] for item in ordered] == [0, 0]


def test_the_word_floor_and_cap_are_enforced_per_car_count():
    """A three-car countdown and a two-car one are different lengths, and
    a script written to the wrong one is either padded or truncated by the
    tempo correction rather than rewritten."""
    for count in (2, 3):
        cars = compare.parse_cars(json.dumps(_cars(count)))
        low, high = compare.COMPARE_STYLES[count]["words"]
        assert compare.compare_violations(_package(cars, words=low - 20), cars)
        assert compare.compare_violations(_package(cars, words=high + 20), cars)
        assert compare.compare_violations(_package(cars), cars) == []


def test_every_car_gets_its_own_image_directory(tmp_path, monkeypatch):
    """Three cars sharing one directory would leave one front.jpg between
    them, and the countdown would be built from the last car's photos under
    every car's narration."""
    seen = []

    def fake_gather(photo_urls, images_dir, entry, plate_boxes=None):
        seen.append(Path(images_dir))
        return [{"path": "manual/front.jpg", "type": "exterior", "category": "front",
                 "facing_direction": "left"}]

    monkeypatch.setattr(compare, "gather_manual_media", fake_gather)
    cars = compare.parse_cars(json.dumps(_cars(3)))
    media = compare._media_for_cars(cars, tmp_path / "images")
    assert len({str(path) for path in seen}) == 3, seen
    # And the manifest paths are rooted at the build, not at the car folder.
    assert [item["path"] for item in media] == [
        "images/car0/manual/front.jpg",
        "images/car1/manual/front.jpg",
        "images/car2/manual/front.jpg",
    ]
    assert [item["car_index"] for item in media] == [0, 1, 2]


def test_a_car_with_no_photos_stops_the_build(tmp_path):
    cars = compare.parse_cars(json.dumps(_cars(3, photos=False)))
    with pytest.raises(SystemExit) as error:
        compare._media_for_cars(cars, tmp_path / "images")
    assert "no photos" in str(error.value)


def test_the_prompt_names_every_car_and_its_notes():
    cars = compare.parse_cars(json.dumps(_cars(3)))
    cars[1]["notes"] = "the supercharger pulley swap"
    cars[2]["worth"] = "8k"
    prompt = compare.compare_prompt(cars, compare.COMPARE_STYLES[3], "cheap V8 AMGs")
    for index in range(3):
        assert f"CAR {index}:" in prompt
    assert "the supercharger pulley swap" in prompt
    assert "8k" in prompt
    assert "cheap V8 AMGs" in prompt
    # The structure the format depends on.
    assert "catch" in prompt.lower()
    assert "car_index" in prompt
