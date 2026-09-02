"""Build a fast, one-car narrated Short from researched facts and stills.

This is intentionally separate from ranking and startup-battle modes. It
uses exterior/engine/interior/detail photos, a 55-60 second script, the committed
narrator sprites, word-sized captions, and gentle motion so still images do
not feel frozen.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from background_removal import remove_background
from cars_and_bids import enrich_entry_from_manifest, scrape_entry_images
from research_request import _auction_provenance_matches_entry, review_and_rename_entry_images
from generate_sample import ROOT
from narrator_script import _extract_wav, build_mouth_timeline, synthesize_narration
from narrator_video import render_narrator_video
from openai_retry import with_openai_retry
from plate_blur import blur_license_plates

load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
OUTPUT_ROOT = ROOT / "cars" / "single-car-shorts"
TARGET_WORDS = (175, 190)
# The prompt targets the tight range above, but word counts from a grounded
# structured response can land a little outside it. Runtime is normalized
# from the actual audio below, so only reject clearly broken short/long
# responses instead of throwing away an otherwise good 199-word script.
ACCEPTABLE_WORDS = (100, 220)
FAST_TTS_SPEED = 1.25
TARGET_DURATION_SECONDS = 58.0
ALLOWED_MEDIA_TYPES = {"exterior", "engine", "interior", "detail", "wheel"}

PACKAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "scenes", "sources", "start_year", "end_year"],
    "properties": {
        "title": {"type": "string"},
        # Whichever generation the script actually settles on -- especially
        # important when the caller didn't pin a year range, since research
        # is free to pick "the best-known generation" on its own. Without
        # this, photo gathering had no idea which generation to search for
        # and could land on a totally different one (e.g. narrating the
        # first-gen 8N Audi TT while showing photos of a modern 8S).
        "start_year": {"type": ["integer", "null"]},
        "end_year": {"type": ["integer", "null"]},
        "scenes": {
            "type": "array", "minItems": 5, "maxItems": 7,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["media_type", "headline", "narration", "rival_make", "rival_model"],
                "properties": {
                    "media_type": {"type": "string", "enum": ["exterior", "engine", "interior", "detail", "wheel"]},
                    "headline": {"type": "string"},
                    # The actual spoken narration for this one beat -- the
                    # full script is these joined in order (see
                    # research_script), not a separate freeform field, so
                    # each scene's photo/caption can be timed to exactly
                    # when its own words are actually being spoken instead
                    # of an even split across the whole clip that has no
                    # relation to how long each beat took to say (that
                    # mismatch is why a rival car's photo could show up at
                    # the end of the video instead of during the sentence
                    # that names it).
                    "narration": {"type": "string"},
                    # When this scene's narration directly names a specific
                    # competitor car (e.g. "beats the Camaro in handling"),
                    # these carry that competitor's make/model so build_short
                    # can show one real photo of it instead of the main
                    # car's own photo for this one beat -- null/null when
                    # the scene doesn't name a specific rival.
                    "rival_make": {"type": ["string", "null"]},
                    "rival_model": {"type": ["string", "null"]},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
    },
}


def _word_count(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


# The web-search tool makes the model attach inline citations to claims it
# just looked up, e.g. "...420 horsepower ([ru.wikipedia.org](https://...))"
# -- exactly the kind of thing that's fine in written text but gets read
# out loud (or at minimum sits visibly in the script/captions) if it isn't
# stripped before the text is used for narration. `sources` is the schema's
# actual place for citation URLs; this is a safety net in case the prompt
# instruction alone doesn't stop the model from also inlining them.
_CITATION_MARKDOWN_LINK = re.compile(r"\(\[[^\]]*\]\([^)]*\)\)")
_BARE_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://[^\s)]+")


def _strip_citations(text):
    text = _CITATION_MARKDOWN_LINK.sub("", text)
    text = _BARE_MARKDOWN_LINK.sub("", text)
    text = _BARE_URL.sub("", text)
    # A bare URL can also show up wrapped in a single paren rather than the
    # doubled "([text](url))" citation shape, e.g. "(https://site.com)" --
    # stripping just the URL leaves an empty, now-meaningless "()" behind.
    text = re.sub(r"\(\s*\)", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def research_script(make, model, trim="", start_year=None, end_year=None):
    label = " ".join(value for value in [make, model, trim] if value).strip()
    year_scope = (
        f"model years {start_year}-{end_year}" if start_year and end_year
        else f"model year {start_year or end_year}" if start_year or end_year else "the best-known generation"
    )
    prompt = f"""Research and write one original vertical car-video package about {label}, scoped to {year_scope}.
Use web search and verify every technical comparison. Write a quick, conversational narration of {TARGET_WORDS[0]}-{TARGET_WORDS[1]} words total so faster TTS lands near 55-60 seconds, split across 5-7 scenes in speaking order -- each scene's "narration" is the exact words spoken during that beat, and all of them concatenated in order form the entire script, so each one must read naturally both alone and flowing into the next (no "scene 1, scene 2" choppiness). Start with a strong value/performance hook, name the exact car early, then cover engine/turbo, drivetrain, one comparison or ownership insight, tuning potential only when supportable, and finish with a direct viewer-choice question -- spread across the scenes in that order. Use short spoken sentences and natural contractions. Do not imitate or quote any creator.

Every scene's "narration" is read aloud as-is -- it must contain ONLY the spoken words. Never include citations, footnotes, markdown links, URLs, domain names (e.g. wikipedia.org), or phrases like "according to" a named site. If a claim needs a source, put that source's URL in the separate "sources" array instead, not inline in the narration.

Headlines are only for important facts and must be 1-4 words (examples: model/chassis, engine code, AWD, horsepower, price gap); use an empty string for ordinary beats. Use exterior media for the hook/close, engine for powertrain, wheel for drivetrain when useful, detail for modification/technical beats, and interior only when the script specifically discusses the cabin, seats, controls, or practicality -- most scripts should lean on exterior shots with only a couple of interior beats, not the other way around. Sources must be direct URLs supporting the claims.

When a scene's "narration" directly names one specific competitor car (e.g. "beats the Camaro in handling"), set that scene's rival_make/rival_model to that competitor (e.g. "Chevrolet"/"Camaro") so a real photo of it can be shown exactly during that scene; otherwise set both to null. Only set these when the narration truly names one specific rival car in THAT scene, not a vague "its rivals" or a whole segment/class. That scene's narration must include a concrete horsepower figure for both cars (e.g. "420 hp vs. the Camaro SS's 455 hp"), not just a vague handling or value claim -- verify both numbers with web search.

Also return "start_year" and "end_year": the exact model-year range of the generation your script actually describes (the same year, twice, if it's a single model year). This must reflect what you actually researched and wrote about, even when the scope above was "the best-known generation" and you had to pick one yourself -- the photos shown alongside the narration are gathered using these years, so they need to match the generation you're describing."""
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model="gpt-4o",
        input=prompt,
        tools=[{"type": "web_search_preview"}],
        text={"format": {"type": "json_schema", "name": "single_car_short", "strict": True, "schema": PACKAGE_SCHEMA}},
    ))
    package = json.loads(response.output_text.strip())
    for scene in package["scenes"]:
        scene["narration"] = _strip_citations(scene["narration"])
    package["script"] = " ".join(scene["narration"] for scene in package["scenes"])
    count = _word_count(package["script"])
    if not ACCEPTABLE_WORDS[0] <= count <= ACCEPTABLE_WORDS[1]:
        raise RuntimeError(
            f"Single-car script is outside the safe {ACCEPTABLE_WORDS[0]}-{ACCEPTABLE_WORDS[1]} word range; "
            f"model returned {count}."
        )
    if not TARGET_WORDS[0] <= count <= TARGET_WORDS[1]:
        print(
            f"[single-car] Script returned {count} words outside the preferred "
            f"{TARGET_WORDS[0]}-{TARGET_WORDS[1]} range; audio timing will normalize the final runtime."
        )
    package["word_count"] = count
    return package


def _visual_highlight_for_scenes(scenes):
    """Build the entry's shot-type hint from what the script actually needs,
    instead of unconditionally naming every shot type.

    _desired_shot_types() (cars_and_bids.py) reads this text to decide which
    shot types to prioritize, and it used to always name exterior, engine,
    interior, wheel, *and* detail -- so every single-car short pushed
    interior/engine shots to the front of the priority order regardless of
    what the script's scenes actually called for, which is how a script with
    only exterior/engine/wheel/detail beats still ended up entirely composed
    of interior photos.
    """
    media_types = {scene.get("media_type") for scene in scenes}
    words = []
    if "interior" in media_types:
        words.append("interior dashboard seats")
    if "engine" in media_types:
        words.append("engine turbo horsepower")
    if "wheel" in media_types or "detail" in media_types:
        words.append("wheel brake detail")
    return " ".join(words)


# research_request.py's category vocabulary (from its own real per-image AI
# review, review_and_rename_entry_images) mapped down to the coarser types
# this format's scenes actually request.
_SHOT_TYPE_BY_CATEGORY = {
    "exterior_front": "exterior",
    "exterior_rear": "exterior",
    "exterior_side": "exterior",
    "exterior_full": "exterior",
    "interior": "interior",
    "engine_bay": "engine",
    "wheel_detail": "wheel",
    "other_detail": "detail",
}


def gather_media(make, model, trim, start_year, end_year, images_dir, scenes=None):
    search_hint = " ".join(value for value in [make, model, trim] if value).strip()
    if start_year or end_year:
        first, last = start_year or end_year, end_year or start_year
        generation = ""
    else:
        first, last, generation = "", "", ""
    entry = {
        "name": search_hint,
        "label": search_hint,
        "years": f"{first}-{last}" if first and first != last else str(first),
        "search_hint": search_hint,
        "visual_highlight": _visual_highlight_for_scenes(scenes or []),
        "generation_label": generation,
    }
    selected, manifest = scrape_entry_images(SCRAPER_DIR, images_dir, entry)
    entry["images"] = selected
    enrich_entry_from_manifest(entry, manifest)
    # scrape_entry_images already runs a first-pass review internally
    # (cars_and_bids.review_draft_images / choose_reviewed_images), but that
    # pass only does coarse shot-type keyword/heuristic scoring. The
    # ranking/battle pipeline's real strength is this second pass -- an
    # actual per-image AI vision review that checks the pixels against the
    # expected generation and gives usable files a truthful category name
    # (research_request.review_and_rename_entry_images) -- which is what
    # actually answers "is this really the front/side/interior of this car"
    # instead of trusting a keyword guess. Reusing it here instead of
    # reimplementing a thinner version is what should have been done from
    # the start.
    review_and_rename_entry_images(
        entry,
        images_dir,
        require_ai=False,
        seen_images=[],
        trusted_variant_provenance=_auction_provenance_matches_entry(entry),
    )
    reviews_by_path = {review.get("path"): review for review in entry.get("image_reviews", [])}
    media = []
    for relative in entry["images"]:
        review = reviews_by_path.get(relative, {})
        shot_type = _SHOT_TYPE_BY_CATEGORY.get(review.get("category"), "exterior")
        path = images_dir.parent / relative
        if not path.exists():
            continue
        blur_license_plates(path)
        # Exterior shots read better as a cutout on the format's white
        # canvas; interior/engine/wheel/detail shots are meant to look like
        # a real photo, so they're left with their original background.
        if shot_type == "exterior":
            path = remove_background(path)
            relative = str(path.relative_to(images_dir.parent)).replace("\\", "/")
        media.append({"path": relative, "type": shot_type})
    if not media:
        raise RuntimeError("No approved exterior, engine, detail, or wheel images were found.")
    return media, manifest.get("selected_auction") or {}


def apply_rival_photos(scenes, media, start_year, end_year, images_dir):
    """Swap in a real photo of the named competitor for any scene that
    directly compares to one, instead of showing the main car's own photo
    again there. One scrape per distinct rival (a video rarely names more
    than one), best-effort -- a rival lookup failure just leaves that
    scene's original media untouched."""
    rival_cache = {}
    for index, scene in enumerate(scenes):
        rival_make = scene.get("rival_make")
        rival_model = scene.get("rival_model")
        if not rival_make and not rival_model:
            continue
        cache_key = (rival_make, rival_model)
        if cache_key not in rival_cache:
            rival_cache[cache_key] = gather_rival_photo(rival_make, rival_model, start_year, end_year, images_dir)
        rival_path = rival_cache[cache_key]
        if rival_path and index < len(media):
            media[index] = {"path": rival_path, "type": "exterior"}
    return media


def gather_rival_photo(rival_make, rival_model, start_year, end_year, images_dir):
    """One real exterior photo of a named competitor car, same era as the
    main car -- for the single scene that directly compares to it, instead
    of showing the main car's own photo again there. Best-effort: returns
    None on any failure (no results, review rejects everything, etc.)
    rather than failing the whole build over one optional beat."""
    search_hint = " ".join(value for value in [rival_make, rival_model] if value).strip()
    if not search_hint:
        return None
    entry = {
        "name": search_hint,
        "label": search_hint,
        "years": f"{start_year}-{end_year}" if start_year and end_year and start_year != end_year else str(start_year or end_year or ""),
        "search_hint": search_hint,
        "visual_highlight": "",
        "generation_label": "",
    }
    try:
        selected, manifest = scrape_entry_images(SCRAPER_DIR, images_dir, entry)
        entry["images"] = selected
        enrich_entry_from_manifest(entry, manifest)
        review_and_rename_entry_images(
            entry, images_dir, require_ai=False, seen_images=[],
            trusted_variant_provenance=_auction_provenance_matches_entry(entry),
        )
    except Exception:
        return None
    reviews_by_path = {review.get("path"): review for review in entry.get("image_reviews", [])}
    exterior_categories = {"exterior_front", "exterior_rear", "exterior_side", "exterior_full"}
    for relative in entry.get("images", []):
        review = reviews_by_path.get(relative, {})
        if review.get("category") not in exterior_categories:
            continue
        path = images_dir.parent / relative
        if not path.exists():
            continue
        blur_license_plates(path)
        path = remove_background(path)
        return str(path.relative_to(images_dir.parent)).replace("\\", "/")
    return None


# Extra takes of the same script in a few other voices, purely for the
# creator to listen to and compare against the chosen voice -- not used in
# the rendered video itself. British presets added on request; the
# currently-chosen preset is included too so there's a like-for-like
# comparison instead of only ever hearing the alternatives.
AUDITION_PRESETS = ["british_narrator", "british_dry_wit", "british_energetic"]


def generate_voice_auditions(script, output_dir, chosen_preset):
    presets = list(dict.fromkeys([chosen_preset, *AUDITION_PRESETS]))
    audition_dir = output_dir / "voice_auditions"
    audition_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for preset in presets:
        try:
            path = audition_dir / f"{preset}.mp3"
            synthesize_narration(script, path, preset=preset, speed=FAST_TTS_SPEED)
            files[preset] = str(path.relative_to(output_dir.parent)).replace("\\", "/")
        except Exception as exc:
            print(f"[single-car] Voice audition failed for preset {preset!r}: {exc}")
    return files


def normalize_audio_duration(audio_path, target=TARGET_DURATION_SECONDS, minimum=55.0, maximum=60.0):
    """Keep the final voice close to one minute without asking TTS twice."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(audio_path)],
        check=True, capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    if minimum <= duration <= maximum:
        return duration
    tempo = duration / target
    # atempo accepts 0.5-2.0; this range is ample for a tightly constrained script.
    tempo = max(0.5, min(2.0, tempo))
    adjusted = audio_path.with_name(f"{audio_path.stem}-timed{audio_path.suffix}")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path), "-filter:a", f"atempo={tempo:.5f}", str(adjusted)],
        check=True, capture_output=True, text=True,
    )
    adjusted.replace(audio_path)
    return target


def transcribe_word_timeline(audio_path):
    """Get real per-word timestamps for captions; rendering has a safe fallback."""
    with Path(audio_path).open("rb") as audio_file:
        client = OpenAI()
        def request_alignment():
            audio_file.seek(0)
            return client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
        result = with_openai_retry(request_alignment)
    words = []
    for item in getattr(result, "words", None) or []:
        if isinstance(item, dict):
            word, start, end = item.get("word"), item.get("start"), item.get("end")
        else:
            word, start, end = getattr(item, "word", None), getattr(item, "start", None), getattr(item, "end", None)
        if word and start is not None and end is not None:
            words.append({"word": str(word).strip(), "start": float(start), "end": float(end)})
    return words


def order_media_for_scenes(scenes, media):
    """Put images in the same semantic order as the written scenes, but
    never repeat a photo while a different, still-unused one of a
    reasonable type is available.

    Always taking the first same-type (or "exterior" fallback) match
    regardless of what earlier scenes already used meant every scene
    requesting the same media_type -- or every scene falling back to
    "exterior" because its own type had no photos -- collapsed onto the
    exact same single image, which is why a whole video could end up stuck
    on one repeated interior shot even when several photos existed.
    """
    ordered = []
    used_paths = set()
    for scene in scenes:
        requested = scene["media_type"]
        same_type = [item for item in media if item["type"] == requested]
        exterior = [item for item in media if item["type"] == "exterior"]
        pick = (
            next((item for item in same_type if item["path"] not in used_paths), None)
            or next((item for item in exterior if item["path"] not in used_paths), None)
            or next((item for item in media if item["path"] not in used_paths), None)
            # Only reach here once every photo has been used at least once.
            or (same_type[0] if same_type else media[0])
        )
        used_paths.add(pick["path"])
        ordered.append(pick)
    return ordered


def build_short(args):
    output_dir = OUTPUT_ROOT / args.short_id
    images_dir = output_dir / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    package = research_script(args.make, args.model, args.trim, args.start_year, args.end_year)
    # Prefer the caller's explicit year range when given; otherwise fall
    # back to whatever generation the script actually settled on, so photo
    # gathering searches the same generation the narration describes
    # instead of an unconstrained "Audi TT" that can land on any year.
    media_start_year = args.start_year or package.get("start_year")
    media_end_year = args.end_year or package.get("end_year")
    media, selected_auction = gather_media(
        args.make, args.model, args.trim, media_start_year, media_end_year, images_dir, package["scenes"]
    )
    media = order_media_for_scenes(package["scenes"], media)
    media = apply_rival_photos(package["scenes"], media, media_start_year, media_end_year, images_dir)
    audio_path = output_dir / "narration.mp3"
    synthesize_narration(package["script"], audio_path, preset=args.voice, speed=FAST_TTS_SPEED)
    normalized_duration = normalize_audio_duration(audio_path)
    voice_auditions = (
        generate_voice_auditions(package["script"], output_dir, args.voice) if args.audition_voices else {}
    )
    try:
        word_timeline = transcribe_word_timeline(audio_path)
    except Exception as exc:
        print(f"[single-car] Word alignment failed; falling back to estimated caption timing: {exc}")
        word_timeline = []
    wav_path = output_dir / "narration.wav"
    _extract_wav(audio_path, wav_path)
    timeline = build_mouth_timeline(wav_path)
    wav_path.unlink(missing_ok=True)
    manifest = {
        "car": {"make": args.make, "model": args.model, "trim": args.trim or None},
        **package,
        "voice_preset": args.voice,
        "tts_speed": FAST_TTS_SPEED,
        "target_duration_seconds": TARGET_DURATION_SECONDS,
        "normalized_audio_duration_seconds": round(normalized_duration, 3),
        "audio_path": str(audio_path),
        "duration_seconds": timeline[-1]["end"] if timeline else 0,
        "mouth_timeline": timeline,
        "word_timeline": word_timeline,
        "media": media,
        "selected_auction": selected_auction,
        "voice_auditions": voice_auditions,
    }
    manifest_path = output_dir / "result.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    media_paths = [output_dir / item["path"] for item in media]
    video_path = output_dir / "single_car_short.mp4"
    render_narrator_video(media_paths, manifest, video_path)
    manifest["video"] = video_path.name
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Create one fast, narrated, stills-based car Short.")
    parser.add_argument("--make", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--trim", default="")
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--short-id", required=True)
    parser.add_argument("--voice", default="onyx")
    parser.add_argument(
        "--audition-voices", dest="audition_voices", action="store_true", default=True,
        help="Also synthesize the script in a few other voice presets (British included) to compare. On by default.",
    )
    parser.add_argument("--no-audition-voices", dest="audition_voices", action="store_false")
    args = parser.parse_args()
    print(json.dumps(build_short(args), indent=2))


if __name__ == "__main__":
    main()
