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

import requests

from background_removal import remove_background
from cars_and_bids import enrich_entry_from_manifest, scrape_auction_images, scrape_entry_images
from research_request import (
    _auction_provenance_matches_entry,
    _image_data_url,
    _review_image_with_ai,
    IMAGE_REVIEW_MODEL,
    review_and_rename_entry_images,
)
from generate_sample import ROOT
from narrator_script import _extract_wav, build_mouth_timeline, synthesize_narration
from narrator_video import render_narrator_video
from openai_retry import with_openai_retry
from plate_blur import blur_license_plates

load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
OUTPUT_ROOT = ROOT / "cars" / "single-car-shorts"
FAST_TTS_SPEED = 1.35
TARGET_DURATION_SECONDS = 58.0
# Calibrated from the original working numbers -- 175-190 words (center
# 182.5) hit the 55-60s target at the old 1.12x speed, giving a baseline
# spoken pace independent of playback speed. TARGET_WORDS/ACCEPTABLE_WORDS
# are derived from that pace at the *current* FAST_TTS_SPEED instead of a
# hardcoded tuple, specifically so bumping the speed can't silently
# desync the word target from it again -- that's exactly what happened
# when FAST_TTS_SPEED went 1.12 -> 1.25 without TARGET_WORDS moving with
# it: the same ~180 words spoken faster produced meaningfully less raw
# audio, so normalize_audio_duration's atempo correction had to slow it
# back down to hit 58s, mostly cancelling out the speed increase (a
# one-minute video effectively still took close to a minute).
_BASE_WORDS_PER_SECOND = (182.5 / 58.0) / 1.12


def _target_word_range(speed=FAST_TTS_SPEED, target_seconds=TARGET_DURATION_SECONDS):
    center = _BASE_WORDS_PER_SECOND * speed * target_seconds
    return (round(center - 12), round(center + 12))


def _acceptable_word_range(speed=FAST_TTS_SPEED, target_seconds=TARGET_DURATION_SECONDS):
    center = _BASE_WORDS_PER_SECOND * speed * target_seconds
    return (round(center * 0.75), round(center * 1.25))


def _hard_word_range(speed=FAST_TTS_SPEED, target_seconds=TARGET_DURATION_SECONDS, min_tempo=0.5, max_tempo=2.0):
    """The word count actually stops being safe to ship -- derived from
    normalize_audio_duration's own atempo clamp (0.5-2.0), not an arbitrary
    guess. A script this short/long still gets its runtime corrected to
    ~target_seconds by that atempo stretch; only outside this range does
    the correction have to exceed what atempo can do without sounding
    broken. ACCEPTABLE_WORDS used to be the actual pass/fail gate at a much
    tighter +-25% band, rejecting scripts (e.g. 146 words, when the target
    center is ~220) that normalize_audio_duration would have handled fine
    with a ~0.66x slowdown -- comfortably inside the 0.5-2.0 clamp -- so a
    build failed over nothing actually broken."""
    words_per_second = _BASE_WORDS_PER_SECOND * speed
    min_words = words_per_second * (min_tempo * target_seconds)
    max_words = words_per_second * (max_tempo * target_seconds)
    return (round(min_words), round(max_words))


TARGET_WORDS = _target_word_range()
# The prompt targets the tight range above, and this wider band is used to
# decide whether to retry the model with corrective feedback (see
# research_script) -- neither one is the actual failure gate anymore.
ACCEPTABLE_WORDS = _acceptable_word_range()
# The real failure gate: only a script this far outside the atempo-safe
# range gets rejected, since anything inside it still reaches ~target
# runtime with an audio-quality-preserving tempo correction.
HARD_WORD_RANGE = _hard_word_range()
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
            "type": "array", "minItems": 5, "maxItems": 8,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": [
                    "media_type", "headline", "narration", "rival_make", "rival_model",
                    "main_horsepower", "rival_horsepower",
                    "main_quarter_mile_seconds", "rival_quarter_mile_seconds",
                ],
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
                    # The same two horsepower figures the narration already
                    # has to state for a rival-comparison beat, but as real
                    # numbers instead of embedded in prose -- narrator_video's
                    # drag-race doodle needs a verified winner, not a regex
                    # guess at whatever number happens to appear in the
                    # sentence. null/null outside a rival-comparison scene.
                    "main_horsepower": {"type": ["integer", "null"]},
                    "rival_horsepower": {"type": ["integer", "null"]},
                    # Verified quarter-mile times (seconds, e.g. 11.5) for
                    # both cars on the same rival-comparison scene -- the
                    # drag-race doodle uses the real ratio between these two
                    # so the faster car actually finishes in less time
                    # instead of just "winning" arbitrarily. null/null when
                    # a reliable published figure isn't available for both
                    # cars (falls back to the horsepower comparison then).
                    "main_quarter_mile_seconds": {"type": ["number", "null"]},
                    "rival_quarter_mile_seconds": {"type": ["number", "null"]},
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


def _research_script_prompt(label, year_scope, retry_feedback="", photo_hints=None, forced_rival=None, disable_comparison=False):
    photo_hints_block = ""
    if photo_hints:
        bullet_list = "\n".join(f"- {hint}" for hint in photo_hints)
        photo_hints_block = f"""

HARD REQUIREMENT, same as the word count above: the user has specifically pasted these photos for this
video, each with a genuine, concrete detail already identified from the image itself:
{bullet_list}
You MUST write one scene's narration specifically about each one -- in your own words, describing/reacting
to that exact detail (not just reusing the sentence verbatim), so the script actually talks about what's on
screen instead of narrating something unrelated over it. Use that scene's headline and media_type to match
(e.g. an interior/gauge detail gets media_type "interior" or "detail" as appropriate). These beats count
toward the word target and beat variety like any other -- they don't replace the history/mechanical/
comparison beats below, they're additional specific material that must be folded in alongside them. Do not
substitute a different, unrelated "detail" beat of your own invention for one of these -- every photo listed
above needs its own scene, genuinely about what's in it."""
    forced_rival_block = ""
    if forced_rival:
        forced_rival_block = f"""

HARD REQUIREMENT: the user has already chosen {forced_rival} as this car's rival for the comparison scene
below (they pasted a photo of it) -- you MUST include that rival-comparison scene, and its rival_make/
rival_model MUST be {forced_rival}'s make and model exactly, not a different car you'd otherwise pick. Verify
both cars' real horsepower and (if published) quarter-mile times with web search as usual; if you genuinely
cannot verify {forced_rival} is a fair, real comparison for this car, still name it as the rival and focus the
scene on whatever real, verifiable comparison you can make (value, character, a spec difference) rather than
dropping it."""
    no_comparison_block = ""
    if disable_comparison:
        no_comparison_block = """

HARD REQUIREMENT: the user has explicitly turned off the rival-comparison scene for this video -- do NOT name
any specific competitor car anywhere in the script, and do NOT set rival_make/rival_model/main_horsepower/
rival_horsepower/main_quarter_mile_seconds/rival_quarter_mile_seconds on ANY scene (leave every one of those
null). Replace that beat with a different one instead -- an ownership/value insight, a character/driving-feel
observation, or another history/mechanical beat -- so the script still hits its word target and beat variety
without any head-to-head."""
    return f"""Write a narration of exactly {TARGET_WORDS[0]}-{TARGET_WORDS[1]} words total -- count as you go. This word count is a hard requirement, not a suggestion. If you land under {TARGET_WORDS[0]}, the fix is never to pad sentences or slow down -- it's to research and add another genuinely interesting beat, either historical or mechanical: who designed it, a notable race win/record/motorsport pedigree, a bit of production history (why it exists, what it replaced, a notable limited run or special edition), a fact about its reputation/legacy, or a specific engineering/mechanical detail (how the suspension or rear axle is set up, the steering system, chassis/platform sharing, a notable engineering trade-off) that's genuinely well-documented for this car. This format is meant to be packed with real, well-researched detail people want to listen to, not stretched -- a short, thin script is a failure to research deeply enough, not an acceptable outcome.{retry_feedback}{photo_hints_block}{forced_rival_block}{no_comparison_block}

Research and write one original vertical car-video package about {label}, scoped to {year_scope}. Use web search and verify every technical comparison and historical claim. Write a quick, conversational narration split across 5-8 scenes in speaking order so faster TTS lands near 55-60 seconds -- each scene's "narration" is the exact words spoken during that beat, and all of them concatenated in order form the entire script, so each one must read naturally both alone and flowing into the next (no "scene 1, scene 2" choppiness). Start with a strong value/performance hook, name the exact car early, then the history/design-legacy beat (the designer, a motorsport win or record, why this generation/model exists, a notable special edition -- whatever is genuinely well-documented for this car, verified with web search, not invented) comes next, early, right after the hook -- not saved for near the end -- then cover engine/turbo, drivetrain, a direct head-to-head comparison against one real, well-known cross-shop rival (nearly every car has one -- only skip this and use an ownership/value insight instead if you genuinely cannot name a fair rival), tuning potential only when supportable, and finish with a direct viewer-choice question -- spread across the scenes in that order. Use short spoken sentences and natural contractions. Do not imitate or quote any creator.

Every sentence has to earn its place with a specific, concrete fact -- a real number, a named comparison, a verifiable detail -- not a vague enthusiast-copy adjective doing the work instead. Cut lines like "adding to its sporty agility" or "making every drive engaging and dynamic" or "celebrated for its precise steering" that describe a *feeling* about the car without any fact backing it up -- if you can't attach a real number, a named comparison, or a specific verifiable detail to a claim, cut the claim and replace it with one you can verify, don't soften it into vague praise. This applies to every beat, not just the hook.

Write like an excited, knowledgeable friend talking fast about a car they love, not a brochure. Favor punchy, stacked, specific claims over smooth marketing prose -- "that's more horsepower per liter than the [famous engine], and it's only got three cylinders" reads as genuinely engaging; "it delivers a dynamic and engaging driving experience" reads as filler no matter how true it is. A strong hook is a bold, specific, verifiable superlative or comparison (most powerful, quickest, cheapest, rarest -- something with a real number and a real point of comparison attached), not a generic "this car blends performance and luxury" opener. Casual contractions and informal phrasing are good here -- this should sound spoken, not written.

Every scene's "narration" is read aloud as-is -- it must contain ONLY the spoken words. Never include citations, footnotes, markdown links, URLs, domain names (e.g. wikipedia.org), or phrases like "according to" a named site. If a claim needs a source, put that source's URL in the separate "sources" array instead, not inline in the narration.

Headlines are only for important facts and must be 1-4 words (examples: model/chassis, engine code, AWD, horsepower, price gap); use an empty string for ordinary beats. Use exterior media for the hook/close, engine for powertrain, wheel for drivetrain when useful, detail for modification/technical beats, and interior only when the script specifically discusses the cabin, seats, controls, or practicality -- most scripts should lean on exterior shots with only a couple of interior beats, not the other way around. Sources must be direct URLs supporting the claims.

When this car's original MSRP when new and a rough current used/market price are both verifiable, work one beat around that comparison -- especially call it out when it's notable: a luxury or exotic car that has depreciated hard off its window sticker, or one (often a limited-run or enthusiast favorite) that has held or even gained value. Give both numbers as approximate round figures (e.g. "started around $85K new, trades for about $40K today"), and make that scene's headline the price figures themselves (e.g. "$85K -> $40K" or "Holds Its Value"), still 1-4 words/tokens. Skip this beat entirely when solid pricing can't be verified with web search -- never guess at numbers.

When a scene's "narration" directly names one specific competitor car (e.g. "beats the Camaro in handling"), set that scene's rival_make/rival_model to that competitor (e.g. "Chevrolet"/"Camaro") so a real photo of it can be shown exactly during that scene; otherwise set both to null. Only set these when the narration truly names one specific rival car in THAT scene, not a vague "its rivals" or a whole segment/class. That scene's narration must include a concrete horsepower figure for both cars (e.g. "420 hp vs. the Camaro SS's 455 hp"), not just a vague handling or value claim -- verify both numbers with web search. Also set that same scene's main_horsepower/rival_horsepower to those same two verified figures as plain integers (e.g. 420 and 455). This narration should stay about the cars themselves (specs, character, verdict) -- never narrate or describe an animation, race, or visual; nothing on screen needs a spoken introduction. Set both horsepower fields to null on every other scene.

On that same rival-comparison scene, also look up each car's published quarter-mile time in seconds (e.g. 11.5) and set main_quarter_mile_seconds/rival_quarter_mile_seconds to those two verified figures -- these (not the horsepower numbers) drive a silent visual drag-race animation between the two cars that plays behind the narration, so the faster car needs to actually be the one with the shorter time. Leave both null if you can't verify a real published time for both cars; do not estimate or guess. Set both to null on every other scene.

Also return "start_year" and "end_year": the exact model-year range of the generation your script actually describes (the same year, twice, if it's a single model year). This must reflect what you actually researched and wrote about, even when the scope above was "the best-known generation" and you had to pick one yourself -- the photos shown alongside the narration are gathered using these years, so they need to match the generation you're describing."""


def _request_script_package(prompt):
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
    package["word_count"] = _word_count(package["script"])
    return package


def research_script(make, model, trim="", start_year=None, end_year=None, max_attempts=4, photo_hints=None, forced_rival=None, disable_comparison=False):
    label = " ".join(value for value in [make, model, trim] if value).strip()
    year_scope = (
        f"model years {start_year}-{end_year}" if start_year and end_year
        else f"model year {start_year or end_year}" if start_year or end_year else "the best-known generation"
    )
    package = None
    for attempt in range(1, max_attempts + 1):
        retry_feedback = (
            f" Your previous attempt came back at {package['word_count']} words, outside the "
            f"{TARGET_WORDS[0]}-{TARGET_WORDS[1]} target -- rewrite from scratch. If you were short, research "
            f"and add a genuinely new beat (history, design story, a race win or record, a special edition, "
            f"or a mechanical/engineering detail like the suspension or rear-axle setup, steering system, or "
            f"chassis platform) rather than padding existing sentences or repeating what you already said -- "
            f"there is almost always more real, well-documented material available if you look for it." if package else ""
        )
        package = _request_script_package(
            _research_script_prompt(label, year_scope, retry_feedback, photo_hints, forced_rival, disable_comparison)
        )
        count = package["word_count"]
        if ACCEPTABLE_WORDS[0] <= count <= ACCEPTABLE_WORDS[1]:
            break
        print(
            f"[single-car] Attempt {attempt}/{max_attempts} returned {count} words, outside the preferred "
            f"{ACCEPTABLE_WORDS[0]}-{ACCEPTABLE_WORDS[1]} range."
            + (" Retrying with corrective feedback..." if attempt < max_attempts else "")
        )
    count = package["word_count"]
    # No hard failure here, on request -- a build dying over a word count
    # was the actual complaint (the retries above already gave the model
    # several honest shots at landing closer). Outside HARD_WORD_RANGE,
    # normalize_audio_duration's atempo clamp (0.5-2.0) can't fully correct
    # the runtime any more -- the video plays noticeably off pace -- but
    # that's a quality tradeoff to proceed with, not a reason to throw the
    # whole build away.
    if not HARD_WORD_RANGE[0] <= count <= HARD_WORD_RANGE[1]:
        print(
            f"[single-car] Proceeding with {count} words even though it's outside the "
            f"{HARD_WORD_RANGE[0]}-{HARD_WORD_RANGE[1]} range atempo can fully correct for -- "
            "the video's pacing may be noticeably off."
        )
    elif not TARGET_WORDS[0] <= count <= TARGET_WORDS[1]:
        print(
            f"[single-car] Proceeding with {count} words outside the preferred "
            f"{TARGET_WORDS[0]}-{TARGET_WORDS[1]} range; audio timing will normalize the final runtime."
        )
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
        words.append("interior dashboard steering wheel cabin")
    if "engine" in media_types:
        words.append("engine bay turbo horsepower")
    if "wheel" in media_types or "detail" in media_types:
        words.append("wheel brake exhaust tailpipe detail")
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


# Which manual photo field maps to which review category -- trusted over
# any AI guess, since the user is telling us directly what the photo is.
MANUAL_PHOTO_FIELDS = {
    "front": "exterior_front",
    "side": "exterior_side",
    "rear": "exterior_rear",
    "engine": "engine_bay",
    "interior": "interior",
}


def _download_car_photo(url, dest_dir, filename_stem):
    """Download one user-pasted photo URL to dest_dir. Returns the local
    Path, or None on any failure -- a dead link, a non-image response, or
    (the actual bug this guards against) the user pasting a *page* URL
    (e.g. a carsandbids.com/auctions/... listing link) instead of a direct
    image link: that request succeeds and returns real bytes, just HTML
    instead of a photo, which silently corrupted the override into a
    broken image with no visible error -- the manual photo just quietly
    never "took". Both the response's content-type and a real decode
    check guard against that, so a bad link fails cleanly (falls back to
    whatever the scrape already found) instead of corrupting the slot."""
    try:
        response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except Exception:
        return None
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        return None
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(response.content)) as image:
            image.verify()
    except Exception:
        return None
    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    else:
        url_ext = Path(url.split("?")[0]).suffix.lower()
        if url_ext in (".jpg", ".jpeg", ".png", ".webp"):
            ext = url_ext
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{filename_stem}{ext}"
    path.write_bytes(response.content)
    return path


def _describe_photo_for_script(path, label_hint, car_label):
    """One AI vision call that turns a user-pasted photo into a concrete,
    specific detail the script can actually talk about -- not just "there's
    a photo of the interior" but the kind of thing a reviewer would call
    out by name (an era-specific gauge design, an unusual material, a
    visible modification). Best-effort: returns None on any failure so a
    bad photo just means no forced beat for it, not a crashed build."""
    try:
        client = OpenAI()
        hint_line = f'The user labeled it "{label_hint}". ' if label_hint else ""
        prompt = (
            f"This is a real photo of a {car_label} that the user specifically chose to include in their "
            f"car-review video script. {hint_line}Look closely and describe, in one concise sentence, the "
            f"single most notable and concrete visual detail actually visible in this photo -- something a "
            f"knowledgeable car reviewer would call out by name (a specific design element, an era-typical "
            f"styling choice, a functional or unusual characteristic, a visible modification). Be specific "
            f"to what's really in the frame, not a generic description of the photo's subject. Return ONLY "
            f"that one sentence -- no preamble, no quotes."
        )
        response = with_openai_retry(lambda: client.responses.create(
            model=IMAGE_REVIEW_MODEL,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": _image_data_url(path)},
                ],
            }],
        ))
        text = response.output_text.strip()
        return text or None
    except Exception as exc:
        print(f"[single-car] Photo-detail description failed for a pasted photo, skipping that hint: {exc}")
        return None


def _identify_car_in_photo(path):
    """One AI vision call to name the specific car in a photo -- used so a
    pasted comparison-car photo can be handed to the script writer as a
    forced rival (make + model). Without this, the pasted photo only ever
    got used *if* the AI happened to independently decide to write a
    rival-comparison scene, and only for whatever car it happened to pick
    on its own -- routinely not the car in the photo, or no rival scene at
    all (no drag race, no comparison, and the pasted photo silently
    unused). Best-effort: returns None on any failure or low-confidence
    identification, which just means no forced rival, not a crashed
    build."""
    try:
        client = OpenAI()
        prompt = (
            "Identify the specific make and model of the car shown in this photo, as precisely as you can "
            'tell from the pixels (e.g. "Acura NSX", "Chevrolet Camaro SS", "Porsche 911 GT3"). Return ONLY '
            'the make and model, nothing else -- no year, no extra commentary. If you cannot confidently '
            'identify a specific car, return exactly "unknown".'
        )
        response = with_openai_retry(lambda: client.responses.create(
            model=IMAGE_REVIEW_MODEL,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": _image_data_url(path)},
                ],
            }],
        ))
        text = response.output_text.strip()
        if not text or text.lower() == "unknown":
            return None
        return text
    except Exception as exc:
        print(f"[single-car] Could not identify the car in the pasted comparison photo: {exc}")
        return None


def gather_photo_script_hints(manual_photo_urls, extra_photos, images_dir, car_label):
    """Downloads and describes every manually-pasted photo (the fixed
    front/side/rear/engine/interior fields plus any free-typed extras) so
    research_script can be told what's actually in them and write a beat
    that specifically references each one -- the point of pasting a photo
    is that the script ends up being about it, not just illustrating an
    unrelated line of narration. Best-effort per photo: a description
    failure just drops that one hint, not the whole build."""
    dest_dir = images_dir / "script-hints"
    hints = []
    for field, category in MANUAL_PHOTO_FIELDS.items():
        url = (manual_photo_urls or {}).get(field)
        if not url:
            continue
        path = _download_car_photo(url, dest_dir, f"hint-{field}")
        if not path:
            continue
        description = _describe_photo_for_script(path, category.replace("_", " "), car_label)
        if description:
            hints.append(f"{category.replace('_', ' ')} photo: {description}")
    for index, item in enumerate(extra_photos or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        label = str(item.get("label") or "").strip()
        if not url:
            continue
        path = _download_car_photo(url, dest_dir, f"hint-extra-{index}-{_slugify(label)}")
        if not path:
            continue
        description = _describe_photo_for_script(path, label, car_label)
        if description:
            hints.append(f"{label or 'featured'} photo: {description}")
    return hints


def _facing_direction_for_photo(path, entry):
    """A single AI vision call just for facing_direction -- the drag-race/
    doodle animations need it to orient the cutout, and a user pasting a
    raw photo link has no way to specify it themselves. Best-effort: a
    failed call leaves the photo usable, just unflippable."""
    try:
        review = _review_image_with_ai(path, entry)
        return review.get("facing_direction", "unclear")
    except Exception:
        return "unclear"


def gather_manual_media(photo_urls, images_dir, entry):
    """Build the media list straight from user-pasted photo URLs instead of
    searching/scraping Cars & Bids -- the whole point is to skip the slow
    Puppeteer search+review pass when the user already has the exact
    photos they want, for much faster iteration. Category comes from
    which field the user put the link in, not an AI guess."""
    dest_dir = images_dir / "manual"
    media = []
    for field, category in MANUAL_PHOTO_FIELDS.items():
        url = (photo_urls or {}).get(field)
        if not url:
            continue
        path = _download_car_photo(url, dest_dir, field)
        if not path:
            print(
                f"[single-car] Could not use the pasted {field} photo URL -- it didn't download as a real "
                f"image (make sure it's a direct image link, e.g. right-click the photo in the listing's "
                f"gallery and \"Copy image address\", not the listing page URL itself). Falling back to "
                f"whatever the scrape found for {field}: {url}"
            )
            continue
        shot_type = _SHOT_TYPE_BY_CATEGORY.get(category, "exterior")
        facing_direction = _facing_direction_for_photo(path, entry) if shot_type == "exterior" else "unclear"
        blur_license_plates(path)
        if shot_type == "exterior":
            path = remove_background(path)
        relative = str(path.relative_to(images_dir.parent)).replace("\\", "/")
        media.append({"path": relative, "type": shot_type, "category": category, "facing_direction": facing_direction})
    return media


def _slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or "photo"


def gather_extra_media(extra_photos, images_dir, entry):
    """Any number of arbitrarily-named extra photos (e.g. "Gauge Cluster",
    "Rear Diffuser") -- always additions to whatever media the scrape or
    the fixed manual fields already produced, never an override of an
    existing category, since a free-typed label has no fixed category to
    replace. Filed as "other_detail"/type "detail", same bucket the
    format's normal detail beats already draw from. Best-effort: a
    malformed entry or a dead link just means one fewer photo, not a
    crashed build."""
    dest_dir = images_dir / "manual-extra"
    media = []
    for index, item in enumerate(extra_photos or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        label = str(item.get("label") or "").strip()
        if not url:
            continue
        path = _download_car_photo(url, dest_dir, f"{index}-{_slugify(label)}")
        if not path:
            print(
                f"[single-car] Could not use the pasted \"{label or 'extra'}\" photo URL -- it didn't "
                f"download as a real image (needs to be a direct image link, not a listing page URL): {url}"
            )
            continue
        blur_license_plates(path)
        relative = str(path.relative_to(images_dir.parent)).replace("\\", "/")
        media.append({"path": relative, "type": "detail", "category": "other_detail", "facing_direction": "unclear", "label": label or None})
    return media


def _apply_manual_photo_overrides(media, manual_media):
    """Layer manual photos on top of a scraped media pool -- each manual
    photo replaces the scraped one(s) in its own category, leaving every
    other category from the scrape untouched. So pasting just a side
    photo, say, overrides only the side shot while front/rear/engine/
    interior still come from the listing itself."""
    if not manual_media:
        return media
    manual_categories = {item["category"] for item in manual_media}
    kept = [item for item in media if item.get("category") not in manual_categories]
    return kept + manual_media


def gather_manual_rival_photo(url, images_dir, rival_make, rival_model):
    """Like gather_rival_photo, but from a user-pasted photo link instead
    of a search -- returns (path, facing_direction), or (None, "unclear")
    on any failure, matching gather_rival_photo's fail-open contract so a
    bad link costs one optional beat's photo, not the whole build."""
    entry = {"name": f"{rival_make} {rival_model}".strip(), "years": ""}
    path = _download_car_photo(url, images_dir / "manual-rival", "rival")
    if not path:
        print(
            f"[single-car] Could not use the pasted comparison-car photo URL -- it didn't download as a "
            f"real image (needs to be a direct image link, not a listing page URL). Falling back to a "
            f"normal rival photo search: {url}"
        )
        return None, "unclear"
    facing_direction = _facing_direction_for_photo(path, entry)
    blur_license_plates(path)
    path = remove_background(path)
    return str(path.relative_to(images_dir.parent)).replace("\\", "/"), facing_direction


def gather_media(make, model, trim, start_year, end_year, images_dir, scenes=None, auction_url=None, manual_photo_urls=None, extra_photos=None):
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
    # A manually pasted photo link overrides whichever category it's for
    # (front/side/rear/engine/interior) with that exact photo. Without a
    # specific listing to fall back to for the rest, manual links skip the
    # search/scrape entirely -- that whole Puppeteer search+download+
    # first-pass-review pass is the slow part of a build, and running it
    # just to throw most of it away would defeat the point. There's no
    # scraped listing in this case, so selected_auction comes back empty.
    manual_urls = {key: value for key, value in (manual_photo_urls or {}).items() if value}
    if manual_urls and not auction_url:
        media = gather_manual_media(manual_urls, images_dir, entry)
        media.extend(gather_extra_media(extra_photos, images_dir, entry))
        if not media:
            raise RuntimeError("None of the provided photo URLs could be downloaded.")
        return media, {}

    # This format needs real variety across five distinct media_types
    # (exterior, engine, wheel, detail, interior) plus a dedicated
    # side-profile shot -- the default limit=6 (tuned for the ranking/
    # battle pipelines, which mostly just need one hero shot per car) was
    # capping the pool before engine/wheel/detail photos ever got a chance
    # to survive the second review pass, even when the gallery had them.
    # A pasted auction_url skips the make/model search entirely and scrapes
    # that exact listing instead -- the escape hatch for a car whose Cars &
    # Bids search page doesn't turn up results (or turns up the wrong one),
    # since the user can find the right listing themselves in a browser.
    if auction_url:
        selected, manifest = scrape_auction_images(SCRAPER_DIR, images_dir, entry, auction_url, limit=10)
    else:
        selected, manifest = scrape_entry_images(SCRAPER_DIR, images_dir, entry, limit=10)
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
        media.append({
            "path": relative, "type": shot_type, "category": review.get("category"),
            "facing_direction": review.get("facing_direction", "unclear"),
        })
    if manual_urls:
        # A manual link alongside a listing overrides just that one
        # category -- the rest of the listing's own gallery still fills
        # in whatever wasn't manually given.
        media = _apply_manual_photo_overrides(media, gather_manual_media(manual_urls, images_dir, entry))
    media.extend(gather_extra_media(extra_photos, images_dir, entry))
    if not media:
        raise RuntimeError("No approved exterior, engine, detail, or wheel images were found.")
    return media, manifest.get("selected_auction") or {}


def _select_side_profile_media(media):
    """Pick one exterior photo to reuse for the decorative mini-car
    animations (drift doodle, drag race) -- a true side profile reads far
    better doing a spin or a left-to-right "race" than a front/rear crop,
    so this prefers exterior_side, falls back to exterior_full (still shows
    the whole car), and only then any exterior photo at all rather than
    skipping the decorative features outright. Returns {"path", "facing_direction"}
    (or None) -- facing_direction lets the race animation flip the cutout so
    the car's nose actually points the way it's "driving" instead of
    sometimes appearing to race backwards."""
    exterior = [item for item in media if item["type"] == "exterior"]
    match = None
    for category in ("exterior_side", "exterior_full"):
        match = next((item for item in exterior if item.get("category") == category), None)
        if match:
            break
    else:
        match = exterior[0] if exterior else None
    if not match:
        return None
    return {"path": match["path"], "facing_direction": match.get("facing_direction", "unclear")}


def apply_rival_photos(scenes, media, start_year, end_year, images_dir, manual_rival_url=None):
    """Swap in a real photo of the named competitor for any scene that
    directly compares to one, instead of showing the main car's own photo
    again there. The rival car itself is decided by the AI script (it's
    whichever car that scene's narration actually names), so a manually
    pasted rival photo can't be matched to a make/model ahead of time --
    instead it's used directly for whichever scene turns out to be the
    comparison, on the assumption the user pasted it because they already
    know roughly who the rival will be. Without a manual link, one scrape
    per distinct rival (a video rarely names more than one), best-effort --
    a rival lookup failure just leaves that scene's original media
    untouched."""
    rival_cache = {}
    manual_rival_photo = None
    for index, scene in enumerate(scenes):
        rival_make = scene.get("rival_make")
        rival_model = scene.get("rival_model")
        if not rival_make and not rival_model:
            continue
        if manual_rival_url:
            if manual_rival_photo is None:
                manual_rival_photo = gather_manual_rival_photo(manual_rival_url, images_dir, rival_make, rival_model)
            rival_path, rival_facing = manual_rival_photo
        else:
            cache_key = (rival_make, rival_model)
            if cache_key not in rival_cache:
                rival_cache[cache_key] = gather_rival_photo(rival_make, rival_model, start_year, end_year, images_dir)
            rival_path, rival_facing = rival_cache[cache_key]
        if rival_path and index < len(media):
            media[index] = {"path": rival_path, "type": "exterior", "facing_direction": rival_facing}
    return media


def gather_rival_photo(rival_make, rival_model, start_year, end_year, images_dir):
    """One real exterior photo of a named competitor car, same era as the
    main car -- for the single scene that directly compares to it, instead
    of showing the main car's own photo again there. Best-effort: returns
    (None, "unclear") on any failure (no results, review rejects
    everything, etc.) rather than failing the whole build over one
    optional beat. The facing_direction lets the drag-race animation flip
    the cutout so it doesn't sometimes appear to race backwards."""
    search_hint = " ".join(value for value in [rival_make, rival_model] if value).strip()
    if not search_hint:
        return None, "unclear"
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
        return None, "unclear"
    reviews_by_path = {review.get("path"): review for review in entry.get("image_reviews", [])}
    exterior_categories = {"exterior_front", "exterior_rear", "exterior_side", "exterior_full"}
    candidates = [
        relative for relative in entry.get("images", [])
        if reviews_by_path.get(relative, {}).get("category") in exterior_categories
    ]
    # A side profile (or, failing that, a full-car angle) reads far better
    # than a front/rear crop for the drag-race mini-car animation this
    # photo doubles as -- prefer those categories over whatever the scraper
    # happened to list first.
    category_rank = {"exterior_side": 0, "exterior_full": 1, "exterior_front": 2, "exterior_rear": 2}
    for relative in sorted(candidates, key=lambda item: category_rank.get(reviews_by_path.get(item, {}).get("category"), 3)):
        path = images_dir.parent / relative
        if not path.exists():
            continue
        blur_license_plates(path)
        path = remove_background(path)
        facing = reviews_by_path.get(relative, {}).get("facing_direction", "unclear")
        return str(path.relative_to(images_dir.parent)).replace("\\", "/"), facing
    return None, "unclear"


# Extra takes of the same script in a few other voices, purely for the
# creator to listen to and compare against the chosen voice -- not used in
# the rendered video itself. British presets added on request; the
# currently-chosen preset (plain "onyx" by default) is included too so
# there's a like-for-like comparison instead of only ever hearing the
# alternatives. british_deep_narrator pairs onyx's own deep register with a
# British accent, on request for "the same deep voice, more British".
AUDITION_PRESETS = ["british_narrator", "british_dry_wit", "british_energetic", "british_deep_narrator"]


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
    # Slowing speech down to stretch a short script out to ~target was the
    # actual complaint ("had to watch at 1.5x speed") -- a big atempo
    # slowdown undoes the whole point of FAST_TTS_SPEED, since it stacks on
    # top of speech that was already recorded fast. Capping the floor much
    # closer to 1.0 means a too-short script just produces a shorter final
    # video instead of artificially dragged-out speech -- a real tradeoff,
    # but the requested one: pace matters more than hitting exactly ~58s.
    # The ceiling stays generous (speeding TTS up reads fine, unlike
    # slowing it down) for a script that runs long instead.
    tempo = max(0.92, min(2.0, tempo))
    adjusted = audio_path.with_name(f"{audio_path.stem}-timed{audio_path.suffix}")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path), "-filter:a", f"atempo={tempo:.5f}", str(adjusted)],
        check=True, capture_output=True, text=True,
    )
    adjusted.replace(audio_path)
    # Report what the file actually ends up at, not the target -- honest
    # even when the clamp above saturated and couldn't fully correct it.
    return duration / tempo


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
    manual_photo_urls = {
        "front": args.photo_front, "side": args.photo_side, "rear": args.photo_rear,
        "engine": args.photo_engine, "interior": args.photo_interior,
    }
    try:
        extra_photos = json.loads(args.extra_photos) if args.extra_photos else []
        if not isinstance(extra_photos, list):
            extra_photos = []
    except (json.JSONDecodeError, TypeError):
        extra_photos = []
    # Any pasted photo (fixed field or free-typed extra) is analyzed up
    # front so the script itself can be written about what's actually in
    # it -- e.g. a pasted gauge-cluster photo should get the script
    # actually talking about that gauge cluster, not just showing it under
    # unrelated narration.
    car_label = " ".join(value for value in [args.make, args.model, args.trim] if value).strip()
    photo_hints = gather_photo_script_hints(manual_photo_urls, extra_photos, images_dir, car_label)
    # A pasted comparison-car photo needs to be identified *before*
    # research so the script can be told to actually name that car as the
    # rival -- otherwise the script decides on its own whether to include
    # a rival scene at all, and the pasted photo only ever gets used if it
    # happens to agree, which routinely means no rival scene (no drag
    # race) and the pasted photo going completely unused. Skipped entirely
    # when comparison is disabled -- an explicit "no comparison" beats
    # whatever URL happens to be sitting in that field.
    forced_rival = None
    if args.photo_rival and not args.disable_comparison:
        rival_id_path = _download_car_photo(args.photo_rival, images_dir / "manual-rival-id", "rival")
        if rival_id_path:
            forced_rival = _identify_car_in_photo(rival_id_path)
    package = research_script(
        args.make, args.model, args.trim, args.start_year, args.end_year,
        photo_hints=photo_hints, forced_rival=forced_rival, disable_comparison=args.disable_comparison,
    )
    if args.disable_comparison:
        # Belt-and-suspenders: the prompt already tells the model never to
        # set these, but a script it wrote before that instruction was
        # added (or one that just doesn't comply) shouldn't be able to
        # sneak a rival scene/drag race past an explicit "off" -- strip
        # the fields outright rather than trusting compliance alone.
        for scene in package["scenes"]:
            for field in (
                "rival_make", "rival_model", "main_horsepower", "rival_horsepower",
                "main_quarter_mile_seconds", "rival_quarter_mile_seconds",
            ):
                scene[field] = None
    # Prefer the caller's explicit year range when given; otherwise fall
    # back to whatever generation the script actually settled on, so photo
    # gathering searches the same generation the narration describes
    # instead of an unconstrained "Audi TT" that can land on any year.
    media_start_year = args.start_year or package.get("start_year")
    media_end_year = args.end_year or package.get("end_year")
    media, selected_auction = gather_media(
        args.make, args.model, args.trim, media_start_year, media_end_year, images_dir, package["scenes"],
        auction_url=args.auction_url, manual_photo_urls=manual_photo_urls, extra_photos=extra_photos,
    )
    # Captured before order_media_for_scenes/apply_rival_photos reshuffle
    # `media` into one pick per scene -- this needs the whole gathered pool
    # to find the single best side-profile shot.
    side_profile_media = _select_side_profile_media(media)
    media = order_media_for_scenes(package["scenes"], media)
    media = apply_rival_photos(
        package["scenes"], media, media_start_year, media_end_year, images_dir,
        manual_rival_url=None if args.disable_comparison else args.photo_rival,
    )
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
        "side_profile_media_path": str(output_dir / side_profile_media["path"]) if side_profile_media else None,
        "side_profile_facing_direction": side_profile_media["facing_direction"] if side_profile_media else "unclear",
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
        "--auction-url", default=None,
        help="A specific carsandbids.com/auctions/... listing to pull photos from instead of "
             "searching by make/model -- for a car whose search page doesn't turn up results.",
    )
    parser.add_argument("--photo-front", default=None, help="Direct URL for the main car's front exterior photo.")
    parser.add_argument("--photo-side", default=None, help="Direct URL for the main car's side exterior photo.")
    parser.add_argument("--photo-rear", default=None, help="Direct URL for the main car's rear exterior photo.")
    parser.add_argument("--photo-engine", default=None, help="Direct URL for the main car's engine-bay photo.")
    parser.add_argument("--photo-interior", default=None, help="Direct URL for the main car's interior photo.")
    parser.add_argument(
        "--photo-rival", default=None,
        help="Direct URL for the comparison car's photo. If omitted, the comparison car (decided by "
             "the AI script) is found with the normal search instead.",
    )
    parser.add_argument(
        "--disable-comparison", action="store_true", default=False,
        help="Skip the rival-comparison scene (and its drag-race animation) entirely, regardless of "
             "--photo-rival or what the AI script would otherwise decide.",
    )
    parser.add_argument(
        "--extra-photos", default=None,
        help='JSON array of extra, arbitrarily-named photos to add on top of the fixed slots, e.g. '
             '\'[{"label": "Gauge Cluster", "url": "https://..."}]\'. Always additions, filed as detail shots.',
    )
    parser.add_argument(
        "--audition-voices", dest="audition_voices", action="store_true", default=True,
        help="Also synthesize the script in a few other voice presets (British included) to compare. On by default.",
    )
    parser.add_argument("--no-audition-voices", dest="audition_voices", action="store_false")
    args = parser.parse_args()
    print(json.dumps(build_short(args), indent=2))


if __name__ == "__main__":
    main()
