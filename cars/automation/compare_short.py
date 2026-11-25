"""Build a countdown Short that ranks two or three cars against one theme.

A different shape of video from single_car_short's, and deliberately a
separate entry point rather than a flag on it: build_short is single-car to
its bones -- one make, one model, five photo slots, one market value, one
set of notes -- and threading a list through all of it would have meant
touching every one of those four hundred lines to serve a format none of
them were written for.

Almost nothing here is new work. The renderer takes a flat list of photo
paths and a manifest whose scenes are each "some narration, a headline, and
one picture", which is exactly what a countdown is; the narration, the word
alignment, the mouth timeline, the photo handling, the plate blurring and
the start-up clip are all imported and behave as they always have. What is
new is two things: a script that counts down instead of touring one car,
and knowing which car each beat is about so the right photo is on screen
while it is being talked about.

The format is read off a real video rather than invented: a theme with a
teaser ("the three cheapest ways into a V8 AMG, and the cheapest one makes
500 horsepower"), then number three, number two, number one, each with its
engine, its real power figure and what it costs today, and then a turn at
the end -- "now the catch" -- that takes something back. The catch is the
part that makes it an argument rather than a list, so it is required here
the same way the closing question is required in the single-car script.
"""
import argparse
import json
import re
from pathlib import Path

from openai import OpenAI

from narrator_video import render_narrator_video
from narrator_script import (_extract_wav, build_mouth_timeline, narration_settings,
                             synthesize_narration)
from openai_models import with_text_model
from openai_retry import credits_available, with_openai_retry
from photo_story import collect_photo_sections
from thumbnail import build_thumbnail
from youtube_metadata import (DESCRIPTION_LIMIT, FIXED_TAGS, TAGS_TOTAL_LIMIT, TITLE_LIMIT,
                              fix_name, photo_credit, plain_dashes)
from single_car_short import (FAST_TTS_SPEED, OUTPUT_ROOT, _plain_dashes_everywhere,
                              _strip_citations, add_narration_lead_in, fetch_engine_clip,
                              gather_manual_media, normalize_audio_duration, pad_tail_silence,
                              prepend_engine_clip, transcribe_word_timeline)


# How long a countdown runs, and how many words that is, by how many cars
# are in it.
#
# The reference video is three cars in about sixty seconds. That is not the
# same arithmetic as the single-car styles: a countdown spends words on
# structure ("number three", "now the catch") that a tour does not, and
# every car needs its own engine, its own number and its own price or the
# beat is not worth including. Two cars get proportionally less rather than
# half, because the hook and the catch are paid for once however many cars
# follow them.
COMPARE_STYLES = {
    2: {"seconds": 45.0, "window": (40.0, 50.0), "words": (112, 126), "scenes": (5, 7)},
    3: {"seconds": 58.0, "window": (53.0, 64.0), "words": (148, 166), "scenes": (7, 9)},
}

# Photos per car. The single-car build gathers up to ten because one car
# carries the whole video; here three cars share the same runtime, so each
# one is on screen for about fifteen seconds and a fourth photo would never
# be reached.
MAX_PHOTOS_PER_CAR = 3

# The five slots a car can have a photo in, same names and same meaning as
# the single-car form, so the dashboard's photo row is reusable as it is.
PHOTO_SLOTS = ("front", "side", "rear", "engine", "interior")


def parse_cars(raw):
    """The --cars payload, validated into a list of 2 or 3 car dicts.

    Raises SystemExit rather than returning something half-formed: a
    countdown built from two cars when three were asked for is a different
    video, not a degraded one, and it would not be noticed until it was
    watched.
    """
    try:
        cars = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as error:
        raise SystemExit(f"[compare] --cars is not valid JSON: {error}")
    if not isinstance(cars, list):
        raise SystemExit("[compare] --cars must be a list of cars.")
    if len(cars) not in COMPARE_STYLES:
        raise SystemExit(
            f"[compare] A countdown takes two or three cars; {len(cars)} were given. "
            f"Supported: {sorted(COMPARE_STYLES)}.")
    cleaned = []
    for index, car in enumerate(cars):
        if not isinstance(car, dict):
            raise SystemExit(f"[compare] Car {index + 1} is not an object.")
        photos = car.get("photos") if isinstance(car.get("photos"), dict) else {}
        entry = {
            "url": (car.get("url") or "").strip(),
            "label": (car.get("label") or "").strip(),
            "worth": (car.get("worth") or "").strip(),
            "notes": (car.get("notes") or "").strip(),
            "photos": {slot: (photos.get(slot) or "").strip() or None for slot in PHOTO_SLOTS},
            "blur": photos.get("blur") if isinstance(photos.get("blur"), dict) else None,
            "engine_clip": car.get("engine_clip") or None,
        }
        if not entry["url"] and not entry["label"]:
            raise SystemExit(
                f"[compare] Car {index + 1} has neither a listing URL nor a name. "
                "Every car in a countdown needs one or the other.")
        cleaned.append(entry)
    return cleaned


def _compare_schema(car_count, max_scenes):
    """The scene schema, plus the one field a countdown needs that a tour
    does not: which car each beat is about.

    car_index is null on the opening hook and on the closing catch, because
    those two are about the theme rather than about one car -- and a beat
    that claims to be about car 2 while the hook is still being spoken puts
    the wrong photo on screen for the only seconds anybody is still
    watching.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["theme", "youtube_title", "ranking", "scenes"],
        "properties": {
            # What the list is a list of, in the video's own words -- "the
            # three cheapest ways into a V8 AMG". Used for the title and
            # for the opening headline.
            "theme": {"type": "string"},
            "youtube_title": {"type": "string"},
            # The cars in the order they are counted down, worst-to-best,
            # as indexes into the --cars list. The model decides this from
            # the theme rather than the caller fixing it, because the theme
            # is what defines the ranking ("cheapest" ranks on price,
            # "fastest" on time) and a caller who has to work the order out
            # by hand will get it wrong on the one that matters.
            "ranking": {
                "type": "array",
                "minItems": car_count, "maxItems": car_count,
                "items": {"type": "integer"},
            },
            "scenes": {
                "type": "array", "minItems": 5, "maxItems": max_scenes,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["car_index", "media_type", "headline", "narration",
                                 "photo_label", "stat_label", "stat_value",
                                 "stat_label_2", "stat_value_2",
                                 "rival_make", "rival_model", "main_horsepower",
                                 "rival_horsepower", "main_quarter_mile_seconds",
                                 "rival_quarter_mile_seconds"],
                    "properties": {
                        "car_index": {"type": ["integer", "null"]},
                        "media_type": {"type": "string",
                                       "enum": ["exterior", "engine", "interior", "detail", "wheel"]},
                        "headline": {"type": "string"},
                        "narration": {"type": "string"},
                        "photo_label": {"type": ["string", "null"]},
                        "stat_label": {"type": ["string", "null"]},
                        "stat_value": {"type": ["string", "null"]},
                        "stat_label_2": {"type": ["string", "null"]},
                        "stat_value_2": {"type": ["string", "null"]},
                        # Carried and always null. The renderer's drag race
                        # reads these, and a countdown has no head-to-head
                        # beat to run one from -- but leaving the keys out
                        # entirely would mean every .get() downstream is
                        # the only thing standing between this and a
                        # KeyError, which is not a guarantee worth relying
                        # on across two modules.
                        "rival_make": {"type": ["string", "null"]},
                        "rival_model": {"type": ["string", "null"]},
                        "main_horsepower": {"type": ["integer", "null"]},
                        "rival_horsepower": {"type": ["integer", "null"]},
                        "main_quarter_mile_seconds": {"type": ["number", "null"]},
                        "rival_quarter_mile_seconds": {"type": ["number", "null"]},
                    },
                },
            },
        },
    }


def compare_prompt(cars, style, theme_hint=""):
    """The instruction for one countdown script."""
    low, high = style["words"]
    scene_low, scene_high = style["scenes"]
    count = len(cars)
    position_words = {2: "two", 3: "three"}[count]
    lines = []
    for index, car in enumerate(cars):
        bits = [f"CAR {index}: {car['label'] or car['url']}"]
        if car["url"]:
            bits.append(f"listing: {car['url']}")
        if car["worth"]:
            bits.append(f"what it is worth today: {car['worth']}")
        if car["notes"]:
            bits.append(f"things to cover, best first: {car['notes']}")
        lines.append("\n  ".join(bits))
    roster = "\n".join(lines)
    theme_line = (f"The theme is: {theme_hint}\n" if theme_hint else
                  "Work out what these cars have in common and make that the theme -- the thing "
                  "that makes them one list rather than three unrelated cars.\n")
    return f"""Write one vertical car-video script: a countdown of {position_words} cars.

{theme_line}
The cars, in no particular order:
{roster}

Use web search and verify every number you state. Never invent a figure.

STRUCTURE, in this order, and do not depart from it:
1. OPEN on the theme and give away one thing about the best entry so the
   viewer has a reason to stay to the end -- the reference for this format
   opens "the three cheapest ways into a V8 AMG right now, and the cheapest
   one makes 500 horsepower". This scene's car_index is null.
2. Then count DOWN. "Number {position_words}" first, finishing on "number one".
   Each car gets its engine (capacity and layout), its real horsepower as a
   number, what one costs today, and one concrete reason somebody wants it.
   Name each car properly -- make, model and the chassis code or generation
   if it is how people refer to it. These scenes carry the car_index of the
   car they are about.
3. END on the catch. A real, specific, verifiable drawback of the car you
   put at number one -- the reference ends on a hydraulic suspension rebuild
   costing as much as the car. This is NOT optional and it is NOT a summary:
   a closing scene that restates what you just said is a failed ending. Its
   car_index is the number one car's, because the picture should be on that
   car while you take it back.

The `ranking` array is the car indexes in the order you count them down:
worst or least impressive first, the best last.

Write {low}-{high} words in total across {scene_low}-{scene_high} scenes -- count as you
go, this is a hard requirement. Each scene's "narration" is the exact words
spoken over that beat, and all of them joined in order are the whole script,
so each must read naturally alone and flowing into the next.

Short spoken sentences, natural contractions, no em dashes. Do not imitate
or quote any creator. Give every scene a headline of two to five words that
is a complete claim a stranger can read and understand on its own -- "457
HP, NO TURBOS" works, "AP2" and "$50K" do not, because they mean nothing to
somebody who does not already know the car.

Set stat_label/stat_value (and the _2 pair when there is a second worth
showing) to the hard numbers for that beat, e.g. "POWER" / "457 hp",
"PRICE" / "under $20K". Leave rival_make, rival_model, main_horsepower,
rival_horsepower and both quarter-mile fields null on every scene.
"""


def _request_compare_package(prompt, car_count, max_scenes):
    """One countdown script from the model, with web search."""
    response = with_text_model(lambda chosen: with_openai_retry(lambda: OpenAI().responses.create(
        model=chosen,
        input=prompt,
        tools=[{"type": "web_search_preview"}],
        text={
            "format": {
                "type": "json_schema", "name": "compare_short", "strict": True,
                "schema": _compare_schema(car_count, max_scenes),
            },
        },
    )))
    package = json.loads(response.output_text.strip())
    for scene in package["scenes"]:
        scene["narration"] = _strip_citations(scene["narration"])
    package["script"] = " ".join(scene["narration"] for scene in package["scenes"])
    return package


def _valid_ranking(ranking, car_count):
    """Whether `ranking` is a real permutation of the cars given.

    Checked rather than trusted: the ranking decides which car is the
    climax and which photo the catch is spoken over, and a model that
    repeats an index or invents a fourth one would put the wrong car at
    number one without anything else noticing.
    """
    return (isinstance(ranking, list) and len(ranking) == car_count
            and sorted(ranking) == list(range(car_count)))


def research_countdown(cars, theme_hint="", max_attempts=3):
    """A verified countdown script for these cars."""
    style = COMPARE_STYLES[len(cars)]
    prompt = compare_prompt(cars, style, theme_hint)
    package = None
    for attempt in range(1, max_attempts + 1):
        package = _request_compare_package(prompt, len(cars), style["scenes"][1])
        problems = compare_violations(package, cars)
        if not problems:
            break
        print(f"[compare] Attempt {attempt}: {len(problems)} problem(s) -- " + "; ".join(problems))
        if attempt < max_attempts:
            prompt = (compare_prompt(cars, style, theme_hint)
                      + "\n\nYour previous attempt had these problems. Fix every one:\n"
                      + "\n".join(f"- {problem}" for problem in problems))
    problems = compare_violations(package, cars)
    if problems:
        # Shipped anyway, loudly, exactly as the single-car build does: a
        # video is worth more than a perfect script, but a script that
        # broke the rules must never look like one that kept them.
        package["rule_violations"] = problems
        print(f"[compare] Shipping with {len(problems)} rule(s) still broken: "
              + "; ".join(problems))
    if not _valid_ranking(package.get("ranking"), len(cars)):
        package["ranking"] = list(range(len(cars)))
        print("[compare] The ranking came back unusable; counting down in the order given.")
    return _plain_dashes_everywhere(package)


def compare_violations(package, cars):
    """Everything wrong with a countdown script, in plain words."""
    problems = []
    scenes = (package or {}).get("scenes") or []
    if not scenes:
        return ["there are no scenes at all"]
    style = COMPARE_STYLES[len(cars)]
    words = len((package.get("script") or "").split())
    low, high = style["words"]
    if words < low:
        problems.append(f"the script is {words} words, under the {low}-word floor")
    if words > high:
        problems.append(f"the script is {words} words, over the {high}-word cap")
    covered = {scene.get("car_index") for scene in scenes if scene.get("car_index") is not None}
    missing = [index for index in range(len(cars)) if index not in covered]
    if missing:
        named = ", ".join(str(index + 1) for index in missing)
        problems.append(f"car {named} never gets a beat of its own")
    for index, scene in enumerate(scenes):
        car_index = scene.get("car_index")
        if car_index is not None and not 0 <= car_index < len(cars):
            problems.append(f"scene {index + 1} points at car {car_index}, which was not given")
        headline = (scene.get("headline") or "").strip()
        if not headline:
            problems.append(f"scene {index + 1} has no headline")
        elif len(headline.split()) < 2:
            # The AP2/$50K failure, as a rule: a one-word headline is a
            # fragment, and the opening one is the only thing on screen
            # while somebody decides whether to keep watching.
            problems.append(f"scene {index + 1}'s headline \"{headline}\" is one word, not a claim")
    if scenes[0].get("car_index") is not None:
        problems.append("the opening scene is about one car; it should set up the theme")
    last = scenes[-1]
    if not re.search(r"\bcatch\b|\bbut\b|\bexcept\b|\bproblem\b|\bdownside\b",
                     (last.get("narration") or ""), re.I):
        problems.append("the last scene does not turn -- it needs the catch, not a summary")
    return problems


def _media_for_cars(cars, images_dir):
    """Every car's photos, gathered into one list, tagged with its car.

    Each car writes into its own directory. gather_manual_media names a
    downloaded file after the slot it came from, so three cars sharing one
    directory would have left one front.jpg between them and built the
    whole countdown from the last car's photos.
    """
    media = []
    for index, car in enumerate(cars):
        car_dir = images_dir / f"car{index}"
        slots = {slot: url for slot, url in (car["photos"] or {}).items() if url}
        if not slots:
            raise SystemExit(
                f"[compare] Car {index + 1} has no photos. A countdown shows each car while it "
                "is being talked about, so every car needs at least one.")
        if len(slots) > MAX_PHOTOS_PER_CAR:
            kept = dict(list(slots.items())[:MAX_PHOTOS_PER_CAR])
            print(f"[compare] Car {index + 1} has {len(slots)} photos; using the first "
                  f"{MAX_PHOTOS_PER_CAR}. Each car is on screen for about fifteen seconds.")
            slots = kept
        entry = {"make": car["label"], "model": "", "start_year": None, "end_year": None}
        car_media = gather_manual_media(slots, car_dir, entry, plate_boxes=car.get("blur"))
        for item in car_media:
            # gather_manual_media returns a path relative to the directory
            # it was handed, which here is one level further down than the
            # build's own images directory. The manifest's paths are read
            # against the build root, so the prefix goes back on.
            item["path"] = f"images/car{index}/{item['path']}"
            item["car_index"] = index
        media.extend(car_media)
    return media


def order_media_for_countdown(scenes, media):
    """One photo per scene, of the car that scene is about.

    The single-car ordering function matches on media_type alone, which is
    right when every photo is the same car and wrong here: an "engine" beat
    about car 2 would happily take car 1's engine bay. So the car comes
    first and the type is only a preference inside it.
    """
    by_car = {}
    for item in media:
        by_car.setdefault(item.get("car_index"), []).append(item)
    used = set()
    ordered = []
    for scene in scenes:
        car_index = scene.get("car_index")
        pool = by_car.get(car_index) or []
        if not pool:
            # The hook and the catch have no car of their own. The hook
            # opens on the car the countdown ends with, because that is the
            # one the opening line has just promised something about.
            pool = by_car.get(scene.get("_fallback_car")) or media
        wanted = scene.get("media_type")
        choice = next((item for item in pool
                       if item["type"] == wanted and id(item) not in used), None)
        if choice is None:
            choice = next((item for item in pool if id(item) not in used), None)
        if choice is None:
            choice = pool[0] if pool else media[0]
        used.add(id(choice))
        ordered.append(choice)
    return ordered


def compare_metadata(manifest, cars, ranking):
    """Title, description, tags and first comment for a countdown.

    Its own rather than youtube_metadata's, which reads one make and one
    model off the manifest and would have described a three-car countdown
    with two blanks and no list. The house shape is kept -- the written
    title, the spec line, subscribe, hashtags, the photo credit -- because
    that is what the channel's other descriptions look like and a countdown
    is not a reason to look like a different channel.
    """
    theme = (manifest.get("theme") or "").strip()
    title = (manifest.get("youtube_title") or theme or "Which one would you take?").strip()
    # Counted down the way the video says them: the climax is last here too,
    # so a reader's list matches the one they just heard.
    ordered = [cars[index] for index in ranking]
    lines = [title[:TITLE_LIMIT], ""]
    if theme:
        lines.append(f"Counting down {theme}. 💨")
        lines.append("")
    for position, car in enumerate(ordered):
        rank = len(ordered) - position
        label = fix_name(car["label"]) or car["url"]
        worth = f" - {car['worth']}" if car["worth"] else ""
        lines.append(f"{rank}. {label}{worth}")
    lines += ["", "Subscribe for more fire car content. 🔥", ""]
    hashtags = []
    for car in ordered:
        for word in re.split(r"[\s\-]+", car["label"] or ""):
            tag = re.sub(r"[^A-Za-z0-9]", "", word)
            if len(tag) > 1 and f"#{tag}" not in hashtags:
                hashtags.append(f"#{tag}")
    hashtags += ["#CarTok", "#Cars", "#performancecars"]
    lines.append(" ".join(hashtags))
    credit = photo_credit(next((car["url"] for car in cars if car["url"]), ""))
    if credit:
        lines += ["", f"Photos via {credit}"]

    candidates = []
    for car in ordered:
        label = fix_name(car["label"])
        if label:
            candidates.append(label)
            candidates += [w for w in re.split(r"[\s\-]+", label) if len(w) > 1]
    candidates += list(FIXED_TAGS)
    tags, used = [], 0
    for tag in dict.fromkeys(t.strip() for t in candidates if t and t.strip()):
        if used + len(tag) + 1 > TAGS_TOTAL_LIMIT:
            continue
        tags.append(tag)
        used += len(tag) + 1

    winner = fix_name(ordered[-1]["label"]) if ordered else ""
    comment = (f"Would you take the {winner}, or is one of the other two the smarter buy? 👇"
               if winner else "Which one would you actually buy? 👇")
    return {
        "title": plain_dashes(title[:TITLE_LIMIT]),
        "description": plain_dashes("\n".join(lines)[:DESCRIPTION_LIMIT]),
        "tags": [plain_dashes(tag) for tag in tags],
        "comment": plain_dashes(comment),
    }


def build_compare(args):
    """Research, narrate and render one countdown."""
    funded, reason = credits_available()
    print(f"[compare] OpenAI balance: {'answers a paid call' if funded else 'empty'}.")
    if not funded:
        raise SystemExit(
            "[compare] Stopping before any work: the OpenAI balance will not cover this build.\n"
            f"  {reason}\n  Nothing was fetched and nothing was written, so a re-run starts clean.")
    cars = parse_cars(args.cars)
    style = COMPARE_STYLES[len(cars)]
    output_dir = OUTPUT_ROOT / args.short_id
    images_dir = output_dir / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[compare] A {len(cars)}-car countdown: "
          + ", ".join(car["label"] or car["url"] for car in cars))
    package = research_countdown(cars, theme_hint=args.theme or "")
    scenes = package["scenes"]
    ranking = package["ranking"]
    winner = ranking[-1]
    for scene in scenes:
        scene["_fallback_car"] = winner

    media = _media_for_cars(cars, images_dir)
    photo_sections = collect_photo_sections(media)
    media = order_media_for_countdown(scenes, media)
    for scene in scenes:
        scene.pop("_fallback_car", None)

    audio_path = output_dir / "narration.mp3"
    synthesize_narration(package["script"], audio_path, preset=args.voice, speed=FAST_TTS_SPEED)
    add_narration_lead_in(audio_path)
    normalized_duration = normalize_audio_duration(
        audio_path, target=style["seconds"],
        minimum=style["window"][0], maximum=style["window"][1])

    alignment_error = None
    try:
        word_timeline = transcribe_word_timeline(audio_path)
        if not word_timeline:
            alignment_error = "the transcript came back empty"
    except Exception as exc:  # noqa: BLE001 - estimated timing beats no build
        alignment_error = str(exc)
        word_timeline = []
    if alignment_error:
        print(f"[compare] The voice could not be aligned, so captions and photo changes are on "
              f"estimated timing rather than the voice: {alignment_error}")
    if word_timeline:
        normalized_duration = pad_tail_silence(
            audio_path, float(word_timeline[-1].get("end") or 0))

    engine_clip = next((car["engine_clip"] for car in
                        [cars[winner]] + [car for car in cars if car is not cars[winner]]
                        if car["engine_clip"]), None)
    engine_clip_path, engine_clip_length = None, 0.0
    if engine_clip:
        fetched = fetch_engine_clip(engine_clip, output_dir / "engine_clip.mp4")
        if fetched:
            engine_clip_path, engine_clip_length = fetched

    wav_path = output_dir / "narration.wav"
    _extract_wav(audio_path, wav_path)
    timeline = build_mouth_timeline(wav_path)
    wav_path.unlink(missing_ok=True)

    manifest = {
        "compare": {
            "cars": [{"label": car["label"], "url": car["url"], "worth": car["worth"]}
                     for car in cars],
            "ranking": ranking,
            "theme": package.get("theme", ""),
        },
        "build_inputs": {"cars": args.cars, "theme": args.theme or "", "voice": args.voice},
        "title": package.get("theme", "Countdown"),
        "youtube_title": package.get("youtube_title", package.get("theme", "")),
        **{k: v for k, v in package.items() if k not in ("ranking",)},
        "voice_preset": args.voice,
        "tts_speed": FAST_TTS_SPEED if narration_settings()["engine"] == "tts" else None,
        "narration": narration_settings(),
        "target_duration_seconds": style["seconds"],
        "normalized_audio_duration_seconds": round(normalized_duration, 3),
        "audio_path": str(audio_path),
        "duration_seconds": timeline[-1]["end"] if timeline else 0,
        "mouth_timeline": timeline,
        "word_timeline": word_timeline,
        "word_alignment_failed": alignment_error,
        "media": media,
        "photo_sections": photo_sections,
    }
    manifest_path = output_dir / "result.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    media_paths = [output_dir / item["path"] for item in media]
    video_path = output_dir / "compare_short.mp4"
    render_narrator_video(media_paths, manifest, video_path)
    if engine_clip_path:
        played = prepend_engine_clip(video_path, engine_clip_path, engine_clip_length,
                                     crop=engine_clip.get("crop"), blur=engine_clip.get("blur"))
        if played:
            manifest["engine_clip"] = {**engine_clip, "played_seconds": round(played, 2)}
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # The channel still, from the same photos. A thumbnail that will not
    # draw must never lose a video that is already rendered.
    thumbnail_name = None
    try:
        thumbnail_name = build_thumbnail(manifest, output_dir, output_dir / "thumbnail.jpg").name
    except Exception as error:  # noqa: BLE001 - a thumbnail is not worth the build
        print(f"[compare] Thumbnail failed ({error}); the video is unaffected.")

    upload_path = output_dir / "upload.json"
    upload_path.write_text(json.dumps({
        **compare_metadata(manifest, cars, ranking),
        "video": video_path.name,
        "thumbnail": thumbnail_name,
        "privacy": "private",
        "status": "ready",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[compare] Wrote {video_path.name}, {upload_path.name}"
          + (f" and {thumbnail_name}" if thumbnail_name else ""))
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Build a two- or three-car countdown Short.")
    parser.add_argument("--cars", required=True,
                        help="JSON list of 2 or 3 cars: url, label, worth, notes, photos, engine_clip.")
    parser.add_argument("--short-id", required=True)
    parser.add_argument("--theme", default="",
                        help="What makes these one list. Left empty, the script works it out.")
    parser.add_argument("--voice", default="onyx")
    build_compare(parser.parse_args())


if __name__ == "__main__":
    main()
