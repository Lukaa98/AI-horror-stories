"""Build a fast, one-car narrated Short from researched facts and stills.

This is intentionally separate from ranking and startup-battle modes. It
uses exterior/engine/interior/detail photos, a 55-60 second script, the committed
narrator sprites, word-sized captions, and gentle motion so still images do
not feel frozen.
"""
import argparse
import copy
import json
import re
import time
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import requests

from background_removal import remove_background
from market_value import summarise_comps, value_from_input
from thumbnail import build_thumbnail
from youtube_metadata import build_metadata as build_youtube_metadata
from cars_and_bids import (enrich_entry_from_manifest, scrape_auction_comps, scrape_auction_facts,
                           scrape_auction_images,
                           scrape_entry_images)
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
from photo_story import SLOTS, SLOT_LABELS, photo_metadata, collect_photo_sections

load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
OUTPUT_ROOT = ROOT / "cars" / "single-car-shorts"
FAST_TTS_SPEED = 1.35
TARGET_DURATION_SECONDS = 58.0
# Calibrated from the original working numbers -- 175-190 words (center
# 182.5) hit the 55-60s target at the old 1.12x speed, giving a baseline
# spoken pace independent of playback speed. HARD_WORD_RANGE (the actual
# atempo-safety-net failure gate below) is still derived from that pace at
# the *current* FAST_TTS_SPEED so it can't silently desync from a future
# speed change. TARGET_WORDS itself, though, is a fixed ~180-word center
# on request -- letting it float up with speed (it drifted to ~220 at
# 1.35x) produced scripts that needed real atempo speed-up on top of the
# already-fast TTS to hit ~58s, which is exactly what read as rushed.
_BASE_WORDS_PER_SECOND = (182.5 / 58.0) / 1.12
# A hard ceiling, not a target to hover around. Above this the TTS has to be
# sped up on top of the already-fast playback to still land at ~58s, and the
# result is the rushed delivery this number exists to prevent: run #172
# shipped 257 words (4.4 words/sec) because nothing actually enforced it.
WORD_CAP = 175
TARGET_WORD_CENTER = WORD_CAP
TARGET_WORD_FLEX = 5


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


# The cap is the top of the range, never its midpoint.
TARGET_WORDS = (WORD_CAP - 2 * TARGET_WORD_FLEX, WORD_CAP)
# The prompt targets the tight range above, and this wider band is used to
# decide whether to retry the model with corrective feedback (see
# research_script) -- neither one is the actual failure gate anymore. A
# fixed +-10 margin around TARGET_WORDS (not a percentage of it) so a script
# that overshoots by, say, 14 words -- comfortably "acceptable" under the
# old +-25% band -- still triggers a retry instead of shipping noticeably
# over the stated hard target.
ACCEPTABLE_WORDS = (TARGET_WORDS[0] - 10, WORD_CAP)
# The real failure gate: only a script this far outside the atempo-safe
# range gets rejected, since anything inside it still reaches ~target
# runtime with an audio-quality-preserving tempo correction.
HARD_WORD_RANGE = _hard_word_range()
ALLOWED_MEDIA_TYPES = {"exterior", "engine", "interior", "detail", "wheel"}

PACKAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "youtube_title", "key_specs", "scenes", "sources", "start_year", "end_year"],
    "properties": {
        "title": {"type": "string"},
        # The YouTube title. Separate from "title", which is a scene headline
        # and reads as a fragment out of context ("Turbocharged Performance").
        "youtube_title": {"type": "string"},
        # The numbers a viewer came for, held apart from the narration so
        # they are guaranteed on screen. Run #184's script never said the
        # car's horsepower, never mentioned torque or 0-60 at all, and spent
        # four of its seven scenes describing what the photos looked like --
        # the spec table exists so that cannot cost the viewer the facts.
        "key_specs": {
            "type": "object", "additionalProperties": False,
            "required": ["horsepower", "torque", "zero_to_sixty", "redline", "engine", "price"],
            "properties": {
                "horsepower": {"type": "string"},
                "torque": {"type": "string"},
                "zero_to_sixty": {"type": "string"},
                "redline": {"type": "string"},
                "engine": {"type": "string"},
                "price": {"type": "string"},
            },
        },
        # Whichever generation the script actually settles on -- especially
        # important when the caller didn't pin a year range, since research
        # is free to pick "the best-known generation" on its own. Without
        # this, photo gathering had no idea which generation to search for
        # and could land on a totally different one (e.g. narrating the
        # first-gen 8N Audi TT while showing photos of a modern 8S).
        "start_year": {"type": ["integer", "null"]},
        "end_year": {"type": ["integer", "null"]},
        "scenes": {
            # maxItems here is the *default* cap (no/few pasted extra
            # photos) -- _schema_with_scene_cap raises it per-request when
            # there are enough pasted photos that they wouldn't all fit a
            # dedicated scene under this default alongside the canonical
            # hook/history/engine/comparison beats.
            "type": "array", "minItems": 5, "maxItems": 8,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": [
                    "media_type", "headline", "narration", "rival_make", "rival_model",
                    "main_horsepower", "rival_horsepower",
                    "main_quarter_mile_seconds", "rival_quarter_mile_seconds",
                    "stat_label", "stat_value", "stat_label_2", "stat_value_2", "photo_label",
                ],
                "properties": {
                    "media_type": {"type": "string", "enum": ["exterior", "engine", "interior", "detail", "wheel"]},
                    "headline": {"type": "string"},
                    # When this scene is specifically about one of the
                    # user's pasted photos (see the photo hints below), the
                    # exact label text for that photo, copied verbatim --
                    # this is how the actual matching picture gets shown
                    # for this scene instead of build_short falling back to
                    # "whichever same-type photo hasn't been used yet",
                    # which has no idea a "Gauge Cluster" scene should use
                    # the gauge-cluster photo specifically. null on every
                    # other scene.
                    "photo_label": {"type": ["string", "null"]},
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
                    # A short label/value pair for the on-screen stat tracker
                    # (e.g. "Horsepower"/"620 hp", "MSRP"/"$190K -> $150K") --
                    # only when this scene's narration actually states one
                    # hard, concrete number worth pinning to the tracker;
                    # null/null on beats that don't (the hook, the closing
                    # question, general character/legacy color). Keep
                    # stat_value short -- it renders in a narrow table cell,
                    # not a sentence. stat_label_2/stat_value_2 are the same
                    # thing for a SECOND distinct hard number in that same
                    # scene (e.g. the engine beat stating both horsepower and
                    # torque needs both numbers to make the tracker, not just
                    # whichever one gets picked) -- null/null when there
                    # isn't a second one.
                    "stat_label": {"type": ["string", "null"]},
                    "stat_value": {"type": ["string", "null"]},
                    "stat_label_2": {"type": ["string", "null"]},
                    "stat_value_2": {"type": ["string", "null"]},
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


def _market_block(market):
    """Recent comparable sales, as the number the value beat should use.

    This outranks both the listing and web search for "what does it cost":
    the listing prices one example, and search returns the model's general
    reputation without holding the trim -- which is how a 400hp 993 Turbo
    was once quoted at a base Carrera's $70,000.
    """
    if not market:
        return ""
    if market.get("source") == "stated":
        return f"""

WHAT THIS CAR IS WORTH -- ${market['median']:,} today. This figure was given by the person making the
video, who knows the car; it is not up for debate and not to be adjusted, averaged or hedged. Use it as
the current-value half of the price beat, as one plain figure -- "they go for about ${market['median']:,}
today". Never mention the auction, the listing, a bid, or where the number came from."""
    examples = "; ".join(
        f"{item['title']} sold for ${item['price']:,}"
        + (f" ({item['ended']})" if item.get("ended") else "")
        for item in market.get("examples") or []
    )
    return f"""

WHAT THIS MODEL ACTUALLY SELLS FOR -- {market['count']} completed sales of the same variant, from the
auction site's own results: they run ${market['low']:,} to ${market['high']:,}, with the middle around
${market['median']:,}. Examples: {examples}.

These are sales of THIS variant; never blend in a cheaper or dearer version of the same model.

Say it as one plain figure -- "they go for about ${market['median']:,} today" -- as the current-value
half of the price beat. Never mention the auction, the listing, a bid, a sale, or any single example.
The viewer is being told what this car costs, not where the number came from: no "this one sold for",
no "currently bid to", no "one recently went for". Just the going rate."""


def _no_market_block(market):
    """What to say about value when nothing verifies a current figure.

    The empty string used to be the answer here, which left the writer free
    to invent one -- and run #220 put "these models now trade for about
    $70,000" on a 993 Turbo worth nearly three times that, in the same
    sentence as "reflecting strong enthusiast demand". The original price is
    a fact; today's is not, unless comparable sales say so.
    """
    if market:
        return ""
    return """

NO VERIFIED CURRENT VALUE IS AVAILABLE for this car. State the original price if you know it, and stop
there. Do not say what it trades for, sells for, goes for, is worth or fetches today, and do not give a
percentage of appreciation or depreciation -- there is no number behind any of it, and a guess reads as
a fact to the viewer. "Originally around $110,000, and the good ones have not been cheap since" is
fine. "Originally around $110,000, now about $70,000" is not, because the second figure is invented."""


def _listing_facts_block(listing_facts):
    """The pasted listing's own text, handed to the writer as ground truth.

    Web search knows what the model makes; the listing knows what this car
    makes and what it actually sold for, on a dated page. That is the
    difference between "it costs about" and a number that is true. It
    supplements the research rather than replacing it -- same beats, same
    rules, better facts.
    """
    if not listing_facts:
        return ""
    lines = []
    if listing_facts.get("title"):
        lines.append(f"Listing title: {listing_facts['title']}")
    if listing_facts.get("price_text"):
        # Only a confirmed sale is described as one. "bidding" is a running
        # auction, and "unknown" means the page did not say -- neither is
        # evidence that money changed hands, and calling either a sale is
        # how run #218 reported a standing bid as the model's value.
        state = listing_facts.get("auction_state")
        label = {
            "sold": "What it actually sold for",
            "bidding": "Bidding is currently at (the auction is still running, so this is NOT a sale)",
        }.get(state, "A price shown on the listing (the page did not say whether it sold, so do not "
                     "call it a sale)")
        lines.append(f"{label}: {listing_facts['price_text']}")
    for key, value in (listing_facts.get("facts") or {}).items():
        lines.append(f"{key}: {value}")
    for key, value in (listing_facts.get("sections") or {}).items():
        lines.append(f"{key}: {value}")
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"""

VERIFIED SOURCE -- this is the actual listing for the exact car in the photos, so it outranks anything
web search tells you about this car's own configuration:
{body}

Use it this way. The engine, output, drivetrain and transmission above describe THIS car; where they
disagree with a figure you find by search, the listing wins and key_specs must match it.

The price above is for grounding, not for saying out loud as an event. Never narrate the auction, the
bidding or the sale -- the viewer is not being shown a listing. If a range of comparable sales was given
earlier, use that for the value beat. If it was not, and the price above is a completed sale, you may use
it as an approximate current value ("they go for about that today") without mentioning where it came
from. If the only figure is bidding still running, skip the current-value claim entirely: an unfinished
auction could land anywhere, and no number is better than a wrong one. And take the trim seriously when no listing price is given at
all: a search for "993 911" returns base Carrera money and this is a Turbo, so a value beat that quotes
the wrong variant is worse than no value beat -- run #217 told a viewer a 400hp Turbo trades for about
$70,000 while the actual car in the photos was bid to $267,000. Any history or generation background in the text above is a starting
point, not a quote: verify it and write it in your own words, and never read the listing aloud.

Ignore anything in there about this one used example's paperwork -- mileage, VIN, title status, service
records, flaws, ownership, location, seller. The video is about the car, not about one auction."""


def _research_script_prompt(label, year_scope, retry_feedback="", photo_hints=None, forced_rival=None, disable_comparison=False, max_scenes=8, listing_facts=None, market=None):
    photo_hints_block = ""
    if photo_hints:
        bullet_list = "\n".join(f"- {hint}" for hint in photo_hints)
        photo_hints_block = f"""

HARD REQUIREMENT, same as the word count above: the user has specifically pasted these photos for this
video, each with a genuine, concrete detail already identified from the image itself:
{bullet_list}
Lines marked MAIN PHOTO are the subjects of this video. You MUST write one scene's narration for each of
them. That scene's job is NOT to describe the photo -- the viewer can already see it. The photo picks the
*subject*; your sentence has to deliver a verifiable FACT about that subject that the picture cannot tell
them. Never narrate what is visible: no "you'll notice the aggressive splitter", no "the rear features quad
tips", no "the side profile reveals the wing", no describing shape, colour, stance, or where a badge sits.
Point the camera at the badge and say what the engine behind it is; point it at the wheels and say what
stops behind them. Use that scene's headline and media_type to match (e.g. an interior/gauge detail gets
media_type "interior" or "detail" as appropriate).

The facts worth spending a scene on are the ones an enthusiast would not already know from looking: the
engine's internal code, what other cars use that same engine, what this model has that its siblings do not,
how many were built, who physically assembles it, what it cost new against what it trades for now, a record
or race result. Those are what make the beat worth hearing over a picture the viewer is already looking at.

Lines marked CLOSE-UP are different: they are supporting detail shown on screen alongside the main photo
they are nested under, not subjects of their own. They stay visible for that whole chapter, so they do NOT
each need a scene. Touch them lightly, and only where there is something genuinely worth saying -- a spoiler,
a carbon-fibre panel, an exhaust tip, a headlight, how a trim piece is finished -- inside the scene that is
already about their main photo, in a clause or a short sentence. Never build a whole scene around one, never
force a mention of one you have nothing real to say about, and never let a close-up push out a
history/mechanical/comparison beat.

Every hint above contains `photo_label: "..."`. For whichever scene you write about that photo, copy the
text INSIDE THE QUOTES into that scene's photo_label field -- verbatim, character for character, the whole
thing and nothing else. Do not include the MAIN PHOTO / CLOSE-UP prefix, do not include the word "photo",
do not re-word it, do not drop the bracketed ID from a close-up's label. This is a literal string copy, and
an inexact one means the real picture cannot be matched back to that scene. If a scene spends a clause on
one specific CLOSE-UP, use THAT CLOSE-UP's quoted photo_label for the scene in preference to the main photo's
-- the main photo is on screen for that whole chapter either way, so the label's only job is to mark which
close-up tile to highlight while the scene runs. Do this on at least two scenes across the script: pick the
two close-ups you have the most concrete thing to say about, fold that into a scene you are already writing,
and label that scene with the close-up. A script where no scene ever carries a close-up's label leaves every
tile unmarked, which is what happened in runs #173 and #176. Set photo_label to null on every other scene, including the ordinary
hook/history/mechanical/comparison beats. These beats count toward the word target and beat variety like any
other -- they don't replace the history/mechanical/comparison beats below, they're additional specific
material that must be folded in alongside them. Do not substitute a different, unrelated "detail" beat of
your own invention for one of the MAIN PHOTOs -- every main photo listed above needs its own scene, genuinely
about what's in it. You have up to {max_scenes} scenes total specifically so all of these pasted photos fit
alongside the canonical hook/history/engine/drivetrain/comparison/closing beats below.

Keep each slot's scenes together and in the order the photos are listed, so the video reads front, side,
rear, engine bay, interior rather than jumping back and forth. User notes are topic suggestions, not verified
facts; verify factual claims with your research. Do not infer horsepower, modifications, or performance from
appearance alone."""
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
    return f"""Write a narration of exactly {TARGET_WORDS[0]}-{TARGET_WORDS[1]} words total -- count as you go. This word count is a hard requirement, not a suggestion. If you land under {TARGET_WORDS[0]}, the fix is never to pad sentences or slow down -- it's to research and add another genuinely interesting beat, either historical or mechanical: who designed it, a notable race win/record/motorsport pedigree, a bit of production history (why it exists, what it replaced, a notable limited run or special edition), a fact about its reputation/legacy, or a specific engineering/mechanical detail (how the suspension or rear axle is set up, the steering system, chassis/platform sharing, a notable engineering trade-off) that's genuinely well-documented for this car. This format is meant to be packed with real, well-researched detail people want to listen to, not stretched -- a short, thin script is a failure to research deeply enough, not an acceptable outcome.{retry_feedback}{_market_block(market)}{_no_market_block(market)}{_listing_facts_block(listing_facts)}{photo_hints_block}{forced_rival_block}{no_comparison_block}

Research and write one original vertical car-video package about {label}, scoped to {year_scope}. Use web search and verify every technical comparison and historical claim. Write a quick, conversational narration split across 5-{max_scenes} scenes (the higher end of that range only when you have several pasted photos each requiring their own scene, per above) in speaking order, each scene being ONE OR TWO complete sentences -- prefer fewer, fuller scenes over many thin one-liners, which read choppy when spoken back to back so faster TTS lands near 55-60 seconds -- each scene's "narration" is the exact words spoken during that beat, and all of them concatenated in order form the entire script, so each one must read naturally both alone and flowing into the next (no "scene 1, scene 2" choppiness). Start with a strong value/performance hook, name the exact car early, then the history/design-legacy beat (a motorsport win or record, why this generation/model exists, a notable special edition -- whatever is genuinely well-documented for this car, verified with web search, not invented) comes next, early, right after the hook -- not saved for near the end -- then cover engine/turbo (state both horsepower AND torque as real numbers in this beat, not horsepower alone), drivetrain, a direct head-to-head comparison against one real, well-known cross-shop rival -- this beat is REQUIRED, and that scene must carry rival_make, rival_model, main_horsepower and rival_horsepower as real verified numbers, because a comparison scene with those four fields filled is what puts the head-to-head drag race on screen. Run #176 dropped the comparison altogether and lost that whole segment. Only skip it, using an ownership/value insight instead, if you genuinely cannot name a fair rival for this car, tuning potential only when supportable, and finish with a direct viewer-choice question -- spread across the scenes in that order. That closing question is a HARD REQUIREMENT, not an optional flourish: the final scene must end on a real question aimed at the viewer that calls back to the hook's claim ("so would you daily a five-hundred-horsepower minivan, or is that a step too far?"). A closing scene that summarises what you just said, or restates what the car is about, is a failed ending -- rewrite it as a question. Use short spoken sentences and natural contractions. Do not imitate or quote any creator.

The hook must be the very first words, no throat-clearing lead-in like "Check out the..." or "Let's talk about...". The FIRST SENTENCE must contain a real number or a hard superlative, and must NOT contain the car's name -- the name is the payoff, so it lands in the second sentence ("...that's the R63 AMG"). "A hand-built seven-seater that hits sixty in four-point-four, and almost nobody knows it exists" is the shape: a concrete claim that makes the viewer want the name. "The Mercedes-Benz R63 AMG might surprise you" is the failure mode -- it names the car, promises interest instead of delivering any, and could be said about any car ever made. Never open by asserting that something is surprising, interesting, special or underrated; state the fact that makes it so and let the viewer conclude it. The claim has to be genuinely verifiable, not just punchy.

Never name a specific individual as the designer unless that person is a real, easily verifiable, widely publicized credit for this exact car (the kind of name that shows up across multiple independent, reputable sources, not just one page) -- a wrong or invented name is worse than no name at all. When you can't verify a specific person, either skip the designer credit and use a different history/legacy fact instead (a motorsport result, a production milestone, why the generation exists), or credit the design studio/brand's design language generally without inventing a person.

Every sentence has to earn its place with a specific, concrete fact -- a real number, a named comparison, a verifiable detail -- not a vague enthusiast-copy adjective doing the work instead. Cut lines like "adding to its sporty agility" or "making every drive engaging and dynamic" or "celebrated for its precise steering" that describe a *feeling* about the car without any fact backing it up -- if you can't attach a real number, a named comparison, or a specific verifiable detail to a claim, cut the claim and replace it with one you can verify, don't soften it into vague praise. This applies to every beat, not just the hook.

Write like an excited, knowledgeable friend talking fast about a car they love, not a brochure. These exact constructions are banned outright, because they read as generated copy: "a testament to", "design ethos", "blending luxury with practicality", "adding a touch of exclusivity", "catering to family needs", "creating a motorsport feel", "highlighting its performance lineage", and any sentence built on "isn't just for looks", on "blending X with Y", or on "provides a sporty feel". Run #176 still slipped through with "the brushed aluminum pedals and bolstered seats provide a sporty feel, blending luxury with performance intentions" -- three clauses saying nothing. Name what the pedal material is actually for, or which seat it is, or cut the sentence. Also banned is stat-shaped padding -- a number that sounds like data but tells the viewer nothing they can use, like "reflecting an annual depreciation of about 8%". Give the two prices and let them do the subtraction. Favor punchy, stacked, specific claims over smooth marketing prose -- "that's more horsepower per liter than the [famous engine], and it's only got three cylinders" reads as genuinely engaging; "it delivers a dynamic and engaging driving experience" reads as filler no matter how true it is. A strong hook is a bold, specific, verifiable superlative or comparison (most powerful, quickest, cheapest, rarest -- something with a real number and a real point of comparison attached), not a generic "this car blends performance and luxury" opener. Every superlative must name the group it actually wins: "the most powerful 911 ever built", "the most expensive Spyder Porsche has sold", "the quickest minivan ever made". Never aim one at cars in general -- "the most luxurious car ever produced" about a $217,545 718 Spyder is not a bold claim, it is a false one, and the true version was available. Casual contractions and informal phrasing are good here -- this should sound spoken, not written.

Also write "youtube_title" -- the video's title on the channel, under 100 characters, ending in a
fire emoji. It is the only thing a viewer reads before deciding to watch, so it carries the same
job as the hook. Pick whichever of these the car actually earns, in this order of preference:
a question about the argument people have over it ("Is the 488 Spider still a real Ferrari? \U0001F525"),
a price move when the move is genuinely notable and both figures are verified
("$250,000 new. $95,000 now. \U0001F525"), or the plain spec form as the fallback
("2018 Ferrari 488 Spider -- 661 hp of twin-turbo drama. \U0001F525"). The first two may leave the
name out, because the description says what the car is. Never claim something the script does not
support, and never promise a reveal the video does not contain.

Every scene's "narration" is read aloud as-is -- it must contain ONLY the spoken words. Never include citations, footnotes, markdown links, URLs, domain names (e.g. wikipedia.org), or phrases like "according to" a named site. If a claim needs a source, put that source's URL in the separate "sources" array instead, not inline in the narration.

Also fill in "key_specs" with this car's six headline numbers, verified by web search, each a
short value the way a spec sheet prints it and nothing else -- no sentences: horsepower ("415 hp"),
torque ("413 lb-ft"), zero_to_sixty ("3.9 sec"), redline ("9,000 rpm"), engine ("3.6L twin-turbo flat-six") and price
("$110K new, ~$70K today"). These are shown to the viewer in a table for the whole video, separate
from anything you say, so they must be right. Use "n/a" only when a figure genuinely does not exist
for this car, never as a shortcut for not having looked.

"redline" is where the tachometer's red arc starts -- the rev limit, not peak power RPM and not a
0-60 time. Give it as RPM ("9,000 rpm", "7,200 rpm"). It is the number this channel is named after,
so it has to be this car's published figure, not the engine family's. Use "n/a" only for a car that
genuinely has no rev limit to quote, which in practice means an electric.

Work in one REPUTATION beat wherever it fits -- what people actually argue about this car. Not a
feature, an opinion the audience already holds: what it lives in the shadow of, what it gets dismissed
for, what the badge snobs say, a criticism that is fair, or one that is not and the fact that settles
it. A 718 Spyder is written off for not being a 911, and then shares the 911's flat-six and outruns
most of them -- that is the beat. An R63 is a minivan nobody took seriously with a hand-built AMG V8
in it. This is what makes a viewer stay past the specs, and it still has to rest on something
verifiable: name the comparison, the criticism or the reputation, then answer it with a real figure or
a documented fact. Skip it only when this car genuinely has no such story.

Say which car this is by the third scene at the latest. The hook deliberately withholds the name, so
the beat right after it has to deliver it -- "...that's the R63 AMG" -- naming the model itself, not
just a performance division or the make hung on a part. Run #205's opening thirty seconds mentioned
"AMG" on an engine and "Mercedes'" on a differential and never said what the car was; the viewer spent
twenty-six seconds watching an unidentified minivan.

Your narration must still SAY the horsepower and the torque as real numbers -- the table does not
excuse leaving them out of the script; run #184 never spoke the car's own horsepower at all. The
horsepower figure has to land inside the FIRST {EARLY_TECH_SCENES} SCENES, not wherever the engine
beat happens to fall. It is the number that sells the car, and a viewer who hasn't heard one about
fifteen seconds in has already scrolled. If your photo order pushes the engine beat later than
that, put the figure in the hook or the history beat too rather than holding it back.

Beats about how the car looks are wanted, not a fault -- the stance, the wheels, a spoiler, how a
trim piece is finished are all fair subjects. What is banned is a beat that ONLY describes what the
viewer can already see. Every appearance beat still has to carry a fact the picture cannot give
them: what that vent actually cools, which other model shares that wheel, what the option cost new,
how many were built in that colour. Point at the thing, then say the part that isn't visible.

Headlines are only for important facts and must be 1-4 words (examples: model/chassis, engine code, AWD, horsepower, price gap); use an empty string for ordinary beats. Use exterior media for the hook/close, engine for powertrain, wheel for drivetrain when useful, detail for modification/technical beats, and interior only when the script specifically discusses the cabin, seats, controls, or practicality -- most scripts should lean on exterior shots with only a couple of interior beats, not the other way around. Sources must be direct URLs supporting the claims.

Each scene shows exactly one photo for its entire duration, so each scene's narration must stay on the ONE physical thing that photo actually shows -- never drift onto a second, different physical subject partway through the same scene. If you want to talk about two different things (e.g. the shift knob AND the seats, or one exterior detail AND a completely different exterior detail), that is two separate scenes, each with its own headline and media_type, not one scene covering both -- otherwise the photo on screen stops matching what's being said the moment the narration moves to the second thing. The reverse also applies: if you're still elaborating on the SAME subject a previous sentence already introduced (more detail on the same spec, the same price discussion, the same feature), keep that in the SAME scene rather than splitting it into a pointless extra scene that would just repeat the same photo. One scene = one subject = one photo, for exactly as long as that subject is being discussed.

When this car's original MSRP when new and a rough current used/market price are both verifiable, work one beat around that comparison -- especially call it out when it's notable: a luxury or exotic car that has depreciated hard off its window sticker, or one (often a limited-run or enthusiast favorite) that has held or even gained value. Give both numbers as approximate round figures (e.g. "started around $85K new, trades for about $40K today"), and make that scene's headline the price figures themselves (e.g. "$85K -> $40K" or "Holds Its Value"), still 1-4 words/tokens. Also work out the average annual depreciation (or appreciation) rate -- the percentage change divided by roughly how many years old the car is -- and state that rate too (e.g. "that's about 8% a year"); put all three figures (MSRP, current price, and the yearly rate) into that scene's stat_value together (e.g. "$85K -> $40K (-8%/yr)") under stat_label "Value". Skip this beat entirely when solid pricing can't be verified with web search -- never guess at numbers.

When a scene's "narration" directly names one specific competitor car (e.g. "beats the Camaro in handling"), set that scene's rival_make/rival_model to that competitor (e.g. "Chevrolet"/"Camaro") so a real photo of it can be shown exactly during that scene; otherwise set both to null. Only set these when the narration truly names one specific rival car in THAT scene, not a vague "its rivals" or a whole segment/class. That scene's narration must include a concrete horsepower figure for both cars (e.g. "420 hp vs. the Camaro SS's 455 hp"), not just a vague handling or value claim -- verify both numbers with web search. Also set that same scene's main_horsepower/rival_horsepower to those same two verified figures as plain integers (e.g. 420 and 455), AND set that scene's stat_label/stat_value to reflect the same head-to-head (e.g. "Horsepower"/"420 hp vs 455 hp") -- this comparison beat needs a stat row just as much as any other hard-number beat does, it's easy to forget since the number's already captured in main_horsepower/rival_horsepower. This narration should stay about the cars themselves (specs, character, verdict) -- never narrate or describe an animation, race, or visual; nothing on screen needs a spoken introduction. Set both horsepower fields to null on every other scene.

Say what the two cars are actually trading, not just who is ahead. A drag race settles one dimension
and the comparison beat is where the rest of it goes: muscle-car torque against track agility, a Plaid
that walks away down the strip and makes no noise doing it, all-wheel-drive launch against rear-drive
feel, cheap speed against a badge. Name the axis, give the loser the thing it actually wins, and keep
it factual -- the honest version of "it wins" is "it wins the quarter mile and gives up everything in
the corners". This matters most when the race disagrees with the horsepower you just quoted, which
happens often: more power is not a shorter elapsed time, and a viewer who is not told why reads it as
the video contradicting itself.

On that same rival-comparison scene, also look up each car's published quarter-mile time in seconds (e.g. 11.5) and set main_quarter_mile_seconds/rival_quarter_mile_seconds to those two verified figures -- these (not the horsepower numbers) drive a silent visual drag-race animation between the two cars that plays behind the narration, so the faster car needs to actually be the one with the shorter time. Leave both null if you can't verify a real published time for both cars; do not estimate or guess. Set both to null on every other scene.

Whenever a scene's narration states one hard, concrete number about THIS car (horsepower, torque, 0-60 time, quarter-mile time, top speed, MSRP/price, weight, production count -- not the rival's), also set that scene's stat_label/stat_value to a short label and that number, formatted for a narrow on-screen table cell (e.g. "Horsepower"/"620 hp", "0-60"/"2.9s", "MSRP"/"$190K -> $150K"). If that SAME scene also states a second, different hard number (the classic case: the engine beat gives both horsepower and torque in the same breath), put that second one in stat_label_2/stat_value_2 -- don't drop it just because stat_label/stat_value is already used. Reuse the same figures already stated in that scene's narration -- never introduce a new number here that isn't spoken. Leave stat_label/stat_value null on scenes that don't state a standalone hard number at all (the hook, the closing question, character/legacy/design color without a figure attached), and leave stat_label_2/stat_value_2 null whenever there's no second number.

Also return "start_year" and "end_year": the exact model-year range of the generation your script actually describes (the same year, twice, if it's a single model year). This must reflect what you actually researched and wrote about, even when the scope above was "the best-known generation" and you had to pick one yourself -- the photos shown alongside the narration are gathered using these years, so they need to match the generation you're describing."""


def _scene_cap_for_photo_hints(photo_hints):
    """The schema's default scenes.maxItems (8) assumes the canonical
    hook/history/engine/drivetrain/comparison/closing beats plus at most a
    couple of pasted-photo beats. Every pasted photo (fixed field or
    free-typed extra) needs its own dedicated scene, so with more than a
    couple of them the fixed cap forced the model to silently drop some --
    they'd get described in the photo hints but never actually assigned a
    scene, which is why a pasted photo (e.g. cup holders) could go
    completely unused even though it downloaded and was described fine.
    Six canonical beats plus one scene per pasted photo, deliberately
    uncapped on the high end -- pasting 10 photos is meant to just work,
    each getting a short couple-second beat of its own, without the total
    word count changing (more scenes just means shorter beats, not a
    longer video)."""
    return max(8, 6 + len(photo_hints or []))


def _schema_with_scene_cap(max_scenes):
    schema = copy.deepcopy(PACKAGE_SCHEMA)
    schema["properties"]["scenes"]["maxItems"] = max_scenes
    return schema


def _request_script_package(prompt, max_scenes=8):
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model="gpt-4o",
        input=prompt,
        tools=[{"type": "web_search_preview"}],
        text={
            "format": {
                "type": "json_schema", "name": "single_car_short", "strict": True,
                "schema": _schema_with_scene_cap(max_scenes),
            },
        },
    ))
    package = json.loads(response.output_text.strip())
    for scene in package["scenes"]:
        scene["narration"] = _strip_citations(scene["narration"])
    package["script"] = " ".join(scene["narration"] for scene in package["scenes"])
    package["word_count"] = _word_count(package["script"])
    return package


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _enforce_word_cap(package, cap=WORD_CAP):
    """Trim the script down to the cap, deterministically.

    The retry loop asks the model to land under the cap and it often will
    not -- runs #171 and #172 came back at 217 and 257 words after four
    attempts each, and were shipped anyway, so the TTS had to race to fit
    ~58 seconds. This is the backstop: drop whole trailing sentences from
    whichever scene is longest until the total fits.

    Sentences, not words, because a truncated sentence is worse than a
    missing one; and never the last sentence a scene has, because every
    scene has to keep narrating its own photo for the imagery to still line
    up with what is being said.
    """
    if _word_count(package.get("script") or "") <= cap:
        # Leave the package exactly as the model returned it, including its
        # own reported word_count, when there is nothing to trim.
        return package
    scenes = package.get("scenes") or []
    trimmed = dropped = 0

    def rescript():
        package["script"] = " ".join(scene["narration"] for scene in scenes)
        return _word_count(package["script"])

    # First pass: trailing sentences off whichever scene is longest, never a
    # scene's only sentence, so every scene keeps narrating its own photo.
    while rescript() > cap:
        trimmable = [
            (len(_SENTENCE_SPLIT_RE.split(scene["narration"].strip())), index)
            for index, scene in enumerate(scenes)
            if len(_SENTENCE_SPLIT_RE.split(scene["narration"].strip())) > 1
        ]
        if not trimmable:
            break
        _, index = max(trimmable)
        sentences = _SENTENCE_SPLIT_RE.split(scenes[index]["narration"].strip())
        scenes[index]["narration"] = " ".join(sentences[:-1])
        trimmed += 1

    # Second pass: whole scenes. Run #175 came back as eleven scenes of one
    # sentence each, which the first pass cannot touch at all -- it found
    # nothing trimmable and shipped 199 words. Dropping a scene costs a beat
    # and its photo, so it goes longest-first and never takes the hook or
    # the closing question, which carry the video.
    while rescript() > cap and len(scenes) > 2:
        index = max(range(1, len(scenes) - 1), key=lambda i: _word_count(scenes[i]["narration"]))
        scenes.pop(index)
        dropped += 1

    package["word_count"] = rescript()
    if trimmed or dropped:
        print(
            f"[single-car] Trimmed {trimmed} trailing sentence(s) and dropped {dropped} scene(s) to "
            f"bring the script to {package['word_count']} words, at or under the {cap}-word cap."
        )
    return package


# Shapes that read as generated copy. Substrings, so a plural or a tense
# change cannot walk past them the way "aren't just for looks" walked past a
# ban on "isn't just for looks" in run #182.
# A superlative aimed at "car" or "vehicle" with nothing narrowing it: the
# noun is the tell. "the fastest 911 ever built" scopes itself and is fine;
# "the most luxurious car ever produced" does not.
UNSCOPED_SUPERLATIVE_RE = re.compile(
    r"\b(?:the\s+)?(?:most\s+\w+|fastest|quickest|rarest|cheapest|priciest|finest|greatest|best)\s+"
    r"(?:car|vehicle|machine)\s+(?:ever|in\s+(?:the\s+)?world|of\s+all\s+time)\b",
    re.I,
)

BANNED_SHAPES = (
    "you'll notice", "the rear features", "side profile reveals", "a testament to",
    "design ethos", "adding a touch of", "just for looks", "catering to",
    "sporty feel", "blending luxury", "design language of",
)


# Horsepower has to arrive inside the opening beats, not merely somewhere in
# the script. Three scenes of an eleven-scene, sixty-second cut is roughly the
# first fifteen seconds -- the window in which a viewer decides to stay.
EARLY_TECH_SCENES = 3
# The hook withholds the name on purpose; this is where it has to arrive.
INTRODUCE_BY_SCENE = 3
# Words inside a model field that name a performance division, a trim or a
# body style rather than the model itself. Saying one of these does not tell
# a viewer what car they are looking at.
GENERIC_MODEL_WORDS = frozenset({
    "amg", "srt", "gts", "gti", "rs", "gt", "gtr", "sti", "type", "edition",
    "package", "coupe", "sedan", "wagon", "convertible", "cabriolet", "roadster",
    "spyder", "spider", "base", "premium", "sport", "plus", "pro", "performance",
})
HORSEPOWER_RE = r"\d[\d,.]*\s*-?\s*(hp\b|horsepower|bhp\b)"
TORQUE_RE = r"\d[\d,.]*\s*-?\s*(lb-ft|lb\.?\s?ft|pound-feet|nm\b)"


def _script_violations(package, make, model, market=None):
    """The house rules that can actually be checked, checked.

    Stating them in the prompt was not enough: audited across runs #180-#182
    the hook named the car every single time, and #182 also opened without a
    number, closed on a statement instead of a question, and used a banned
    shape. These are mechanical properties of the text, so the retry loop can
    verify them instead of hoping.
    """
    scenes = package.get("scenes") or []
    if not scenes:
        return []
    violations = []
    # With no comparable sales, the original price is the only money figure
    # there is evidence for. A second one is an invention, whatever words
    # surround it -- which is why this counts figures rather than hunting
    # for the phrasings that introduce them.
    if not market:
        script = " ".join(scene.get("narration") or "" for scene in scenes)
        figures = {match.group(0) for match in re.finditer(r"\$[\d,]+(?:\.\d+)?\s*(?:k|K|million)?", script)}
        if len(figures) > 1:
            violations.append(
                "you gave more than one price (" + ", ".join(sorted(figures)) + ") but nothing "
                "verifies what this car is worth today. Keep the original price and drop every "
                "other figure -- no current value, no appreciation percentage."
            )
    opening = _SENTENCE_SPLIT_RE.split((scenes[0].get("narration") or "").strip())[0]
    if not re.search(r"\d", opening):
        violations.append(
            f'your first sentence contains no number -- it was "{opening}". Open on a real '
            "figure or a hard superlative."
        )
    make_tokens = [t for t in re.split(r"[\s\-/]+", str(make)) if len(t) > 2]
    model_tokens = [t for t in re.split(r"[\s\-/]+", str(model)) if len(t) > 2]
    names = [part for part in re.split(r"\s+", f"{make} {model}".strip()) if len(part) > 2]
    named = [part for part in names if re.search(rf"\b{re.escape(part)}\b", opening, re.I)]
    if named:
        violations.append(
            f'your first sentence names the car ("{named[0]}") -- it was "{opening}". The name is '
            "the payoff and belongs in the second sentence; the first has to make the viewer want it."
        )
    # Keeping the name out of the first sentence only works if something
    # then says it. Nothing did: across 56 builds 18 never named the car at
    # all and run #205 left a viewer watching an unidentified minivan for 26
    # seconds, with "AMG" and "Mercedes'" passing by as adjectives on an
    # engine and a diff. A real introduction is the make and the model in one
    # sentence, and it belongs where the prompt already says it does -- right
    # after the hook.
    # Any word of the model that actually names the model. Requiring the make
    # too was too strict -- "the 911's aerodynamics" tells a viewer exactly
    # what they are watching without saying Porsche. Requiring the model's
    # leading word was also wrong: "the 991-based GT2 RS" identifies the car
    # without saying 911. What has to be excluded is the other direction, a
    # word naming a performance division or a body style rather than a model,
    # because those ride along on engines and trim: run #205's only mentions
    # were "AMG" on an engine and "Mercedes'" on a differential, and the
    # viewer watched an unidentified minivan for twenty-six seconds.
    identifying = [t for t in model_tokens if t.lower() not in GENERIC_MODEL_WORDS]
    early_text = " ".join(scene.get("narration") or "" for scene in scenes[:INTRODUCE_BY_SCENE])
    if identifying and not any(re.search(rf"\b{re.escape(t)}\b", early_text, re.I) for t in identifying):
        violations.append(
            f"you never say which car this is -- none of {identifying} appears in the first "
            f"{INTRODUCE_BY_SCENE} scenes. The hook withholds the name on purpose, so the next beat has "
            "to deliver it. A sub-brand or make hung on a part does not count."
        )
    closing = (scenes[-1].get("narration") or "").rstrip()
    if not closing.endswith("?"):
        violations.append(
            f'your last scene does not end on a question -- it was "{closing}". It must ask the '
            "viewer something that calls back to the hook."
        )
    script = " ".join(scene.get("narration") or "" for scene in scenes).lower()
    # Horsepower is the number that sells the car, so it has to land while the
    # viewer is still deciding whether to stay -- run #184 buried its only
    # technical beat past the halfway mark. Checking the whole script was not
    # enough; the check is positional.
    early = " ".join(
        scene.get("narration") or "" for scene in scenes[:EARLY_TECH_SCENES]
    ).lower()
    # "807-horsepower" is as common as "807 horsepower", so the separator
    # between the number and the unit may be a hyphen, a space, or nothing.
    if not re.search(HORSEPOWER_RE, early):
        if re.search(HORSEPOWER_RE, script):
            violations.append(
                f"you state the horsepower, but not until after scene {EARLY_TECH_SCENES}. Move that "
                f"figure into the first {EARLY_TECH_SCENES} scenes -- it is the number that keeps the "
                "viewer watching."
            )
        else:
            violations.append(
                f"your narration never states the car's horsepower as a number. Say it within the "
                f"first {EARLY_TECH_SCENES} scenes."
            )
    if not re.search(TORQUE_RE, script):
        violations.append("your narration never states the car's torque as a number. Say it in the engine beat.")
    # A superlative has to name the group it wins. "The most luxurious car
    # ever produced" is not a bold claim about a $217k 718 Spyder, it is a
    # false one -- and the true version was right there: the most expensive
    # Spyder, the most powerful naturally-aspirated Porsche. Scoping is what
    # separates a hook from a lie, and the hook rules ask for superlatives,
    # so this is the guard rail on that instruction.
    for match in re.finditer(UNSCOPED_SUPERLATIVE_RE, script):
        violations.append(
            f'"{match.group(0).strip()}" claims a superlative over every car ever made, which is not '
            "true and is not what you meant. Scope it to the group the car actually leads -- its own "
            "model, brand, era, body style or segment."
        )
        break
    for shape in BANNED_SHAPES:
        if shape in script:
            violations.append(f'you used the banned phrase "{shape}". Rewrite that sentence around a fact.')
    return violations


def _name_tokens(make, model):
    """Every word of the car's own name, for spotting it in a sentence."""
    return [t for t in re.split(r"[\s\-/]+", f"{make} {model}".strip()) if t]


def _name_span_re(make, model):
    """Matches a run of the car's name words, with its article and year.

    The run allows short words ("RS", "GT") so "the GT2 RS Weissach" comes
    out as one span instead of breaking at RS and leaving the rest of the
    name stranded mid-sentence. A trailing possessive is kept in the match
    so "Porsche's most powerful 911" can become "the most powerful 911"
    rather than losing its article.
    """
    tokens = _name_tokens(make, model)
    if not tokens:
        return None
    alt = "|".join(re.escape(token) for token in sorted(tokens, key=len, reverse=True))
    return re.compile(
        rf"\b(?:the|a|an|this)?\s*(?:\d{{4}}\s+)?(?:(?:{alt})\b[\s\-]*)+(?:'s|\u2019s)?",
        re.I,
    )


def _strip_car_name(sentence, make, model):
    """The first sentence with the car's name taken out of it, or None.

    The hook has to make the viewer want the car; the name is the payoff and
    belongs in the second sentence. Asking the model for that failed on 47 of
    56 builds and on 5 of today's 6 -- it is the one rule that never sticks,
    and it is also the only one that is pure text surgery rather than
    judgement, so it is done here instead of asked for.

    Returns None whenever the surgery would leave a worse sentence than it
    found: still naming the car, no number left to hook on, or too short to
    be a sentence. A hook that cannot be repaired cleanly is left alone and
    reported, never mangled.
    """
    pattern = _name_span_re(make, model)
    if pattern is None:
        return None
    long_tokens = [t for t in _name_tokens(make, model) if len(t) > 2]
    seen = {"count": 0}

    def replace(match):
        text = match.group(0)
        # Only a run carrying a real name word is the car's name; "the" or a
        # bare year on its own is just a sentence doing its job.
        if not any(re.search(rf"\b{re.escape(token)}\b", text, re.I) for token in long_tokens):
            return text
        seen["count"] += 1
        # Padded on both sides and the whitespace collapsed afterwards: the
        # match can swallow the space in front of it ("is Porsche's" -> "is"
        # + replacement), which silently welded two words together.
        if re.search(r"(?:'s|\u2019s)$", text):
            return " the "
        # What replaces the name depends on the job it is doing, not on
        # whether it came first. A name with its own determiner ("the R63
        # AMG", "The 2002 Porsche 911 Turbo") heads a noun phrase, so a
        # phrase stands in for it. A bare name inside someone else's phrase
        # ("the most powerful road-legal 911 ever built") is that phrase's
        # head noun, and needs a noun, not a phrase and not a hole: run #202
        # shipped "the most powerful road-legal this one has" because this
        # substituted the wrong thing, and deleting would have left "the most
        # powerful road-legal ever built", which is no better.
        heads_its_own_phrase = (
            match.start() == 0 or re.match(r"\s*(?:the|a|an|this)\b", text, re.I)
        )
        return " this one " if heads_its_own_phrase else " car "

    repaired = pattern.sub(replace, sentence)
    if not seen["count"]:
        return None
    repaired = re.sub(r"\s+", " ", repaired).strip()
    repaired = re.sub(r"\s+([,.;:!?])", r"\1", repaired)
    repaired = re.sub(r"\bthe the\b", "the", repaired, flags=re.I)
    if repaired:
        repaired = repaired[0].upper() + repaired[1:]
    if any(re.search(rf"\b{re.escape(token)}\b", repaired, re.I) for token in long_tokens):
        return None
    # The hook's number must survive the surgery -- but a hook that never
    # had one is a separate violation, not a reason to leave the name in.
    if re.search(r"\d", sentence) and not re.search(r"\d", repaired):
        return None
    if len(repaired.split()) < 6:
        return None
    return repaired


def _repair_script(package, make, model):
    """Deterministic fixes for violations the model would not fix itself.

    Runs after the retries, so the model gets its honest shots first and
    this only touches what survived them. Only the hook's name is repairable
    as text -- the rest (a missing figure, a closing question) is content
    that has to be written, not moved.
    """
    scenes = package.get("scenes") or []
    if not scenes:
        return package
    first = (scenes[0].get("narration") or "").strip()
    opening = _SENTENCE_SPLIT_RE.split(first)[0] if first else ""
    names = [t for t in _name_tokens(make, model) if len(t) > 2]
    if not opening or not any(re.search(rf"\b{re.escape(t)}\b", opening, re.I) for t in names):
        return package
    repaired = _strip_car_name(opening, make, model)
    if repaired is None:
        print("[single-car] The hook names the car and could not be repaired cleanly; leaving it as written.")
        return package
    scenes[0]["narration"] = first.replace(opening, repaired, 1)
    package["script"] = " ".join(scene["narration"] for scene in scenes)
    package["word_count"] = _word_count(package["script"])
    print(f'[single-car] Repaired the hook so it no longer names the car: "{repaired}"')
    return package


def research_script(make, model, trim="", start_year=None, end_year=None, max_attempts=4, photo_hints=None, forced_rival=None, disable_comparison=False, listing_facts=None, market=None):
    label = " ".join(value for value in [make, model, trim] if value).strip()
    year_scope = (
        f"model years {start_year}-{end_year}" if start_year and end_year
        else f"model year {start_year or end_year}" if start_year or end_year else "the best-known generation"
    )
    max_scenes = _scene_cap_for_photo_hints(photo_hints)
    package = None
    for attempt in range(1, max_attempts + 1):
        previous_violations = _script_violations(package, make, model, market) if package else []
        violation_feedback = (
            " Your previous attempt also broke these rules, which are not negotiable -- fix every one: "
            + " ".join(previous_violations) if previous_violations else ""
        )
        retry_feedback = (
            f" Your previous attempt came back at {package['word_count']} words, outside the "
            f"{TARGET_WORDS[0]}-{TARGET_WORDS[1]} target -- rewrite from scratch.{violation_feedback} If you were short, research "
            f"and add a genuinely new beat (history, design story, a race win or record, a special edition, "
            f"or a mechanical/engineering detail like the suspension or rear-axle setup, steering system, or "
            f"chassis platform) rather than padding existing sentences or repeating what you already said -- "
            f"there is almost always more real, well-documented material available if you look for it." if package else ""
        )
        package = _request_script_package(
            _research_script_prompt(label, year_scope, retry_feedback, photo_hints, forced_rival,
                                    disable_comparison, max_scenes, listing_facts, market),
            max_scenes=max_scenes,
        )
        count = package["word_count"]
        violations = _script_violations(package, make, model, market)
        if ACCEPTABLE_WORDS[0] <= count <= ACCEPTABLE_WORDS[1] and not violations:
            break
        problems = []
        if not ACCEPTABLE_WORDS[0] <= count <= ACCEPTABLE_WORDS[1]:
            problems.append(f"{count} words, outside the preferred {ACCEPTABLE_WORDS[0]}-{ACCEPTABLE_WORDS[1]} range")
        problems.extend(violations)
        print(
            f"[single-car] Attempt {attempt}/{max_attempts}: " + "; ".join(problems)
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
    return _enforce_word_cap(_repair_script(package, make, model))


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
# A pasted link is deliberate, so a blip should not cost it.
PHOTO_DOWNLOAD_ATTEMPTS = 3

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
    response = None
    for attempt in range(PHOTO_DOWNLOAD_ATTEMPTS):
        try:
            response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            break
        except Exception as exc:
            if attempt == PHOTO_DOWNLOAD_ATTEMPTS - 1:
                print(f"[single-car] Could not fetch {url}: {exc}")
                return None
            time.sleep(1.5 * (attempt + 1))
    # Decode, do not take the server's word for it. The content-type header
    # used to be a hard gate, which made this fail on any host that labels an
    # image as octet-stream -- and the build then quietly carried on without
    # the photo. Decoding is the authoritative test and catches the case the
    # gate was really there for: a pasted *page* URL returns real bytes that
    # are HTML, and HTML does not decode as an image.
    content_type = response.headers.get("content-type", "")
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(response.content)) as image:
            image.verify()
    except Exception:
        print(f"[single-car] {url} did not decode as an image (content-type {content_type!r}, "
              f"{len(response.content)} bytes). A listing page URL does this -- the link has to be "
              "the image itself.")
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


def _mirror_photo(path):
    """Flip a downloaded photo left-to-right, in place.

    The drag race runs left to right, so a car facing the wrong way looks
    like it is reversing. Facing is normally measured off the cutout's
    silhouette, which is right most of the time and occasionally is not --
    this is the manual override, applied to the pixels before anything else
    reads them, so every later step (the facing measurement included) sees
    the photo the user actually chose.
    """
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            ImageOps.mirror(image).save(path)
    except Exception as exc:
        print(f"[single-car] Could not mirror {path.name}, using it as-is: {exc}")
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
            # The photo_label the model has to echo is quoted inline rather
            # than described positionally ("everything before ' photo:'"),
            # which runs #170/#171 each parsed differently and neither got
            # right -- one kept the "MAIN PHOTO" prefix, the other kept the
            # trailing " photo".
            hints.append(f'MAIN PHOTO -- photo_label: "{category.replace("_", " ")}" -- {description}')
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
            metadata = photo_metadata(item, index)
            cue_label = metadata.get("cue_label", label or "featured")
            context = ""
            prefix = "EXTRA PHOTO"
            if metadata:
                prefix = f"CLOSE-UP (nested under the {SLOT_LABELS[metadata['slot']]} main photo)"
                if metadata.get("note"):
                    context = f" User topic suggestion (verify independently): {metadata['note']}"
            hints.append(f'{prefix} -- photo_label: "{cue_label}" -- {description}{context}')
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
    failed = []
    for field, category in MANUAL_PHOTO_FIELDS.items():
        url = (photo_urls or {}).get(field)
        if not url:
            continue
        path = _download_car_photo(url, dest_dir, field)
        if not path:
            failed.append((field, url))
            continue
        shot_type = _SHOT_TYPE_BY_CATEGORY.get(category, "exterior")
        facing_direction = _facing_direction_for_photo(path, entry) if shot_type == "exterior" else "unclear"
        blur_license_plates(path)
        if shot_type == "exterior":
            path = remove_background(path)
        relative = str(path.relative_to(images_dir.parent)).replace("\\", "/")
        media.append({"path": relative, "type": shot_type, "category": category, "facing_direction": facing_direction})
    # A pasted link is a decision, not a hint. Skipping one quietly produces a
    # video built from different photos than the ones asked for, and the
    # build's own log is the only trace: run #208 pasted five slots, four of
    # them failed to download, and it shipped a Corvette story made of two
    # photos with the drag race running the front shot because the side one
    # never arrived.
    if failed:
        listed = "\n".join(f"  - {field}: {url}" for field, url in failed)
        raise SystemExit(
            f"{len(failed)} pasted photo(s) could not be downloaded, so the video would be built from "
            f"photos you did not choose:\n{listed}\n"
            "Each link has to be the image itself -- right-click the photo in the listing gallery and "
            '"Copy image address" -- not the listing page. If a link looks right, the host may have '
            "refused the request; re-running usually clears a transient failure."
        )
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
        metadata = photo_metadata(item, index)
        # A nested close-up is always a detail: the slot's own Image URL is
        # the main photo, so a close-up never competes to be the overview.
        media.append({"path": relative, "type": "detail", "category": "other_detail",
                      "facing_direction": "unclear", "label": label or None, **metadata})
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


def gather_manual_rival_photo(url, images_dir, rival_make, rival_model, mirror=False):
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
    if mirror:
        _mirror_photo(path)
    facing_direction = _facing_direction_for_photo(path, entry)
    blur_license_plates(path)
    path = remove_background(path)
    return str(path.relative_to(images_dir.parent)).replace("\\", "/"), facing_direction


def _pasted_photos_are_enough(manual_urls, auction_url):
    """Whether the build can skip scraping a listing for photos.

    With no listing there is nothing to scrape, so any pasted photo is
    enough. With a listing, it is only enough once every slot is pasted --
    otherwise the gallery still has to fill the gaps.
    """
    if not auction_url:
        return True
    return all(manual_urls.get(field) for field in MANUAL_PHOTO_FIELDS)


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
    nested = any(isinstance(p, dict) and photo_metadata(p, i) for i, p in enumerate(extra_photos or []))
    # A listing URL used to force the scrape even when every slot was
    # already pasted in. The gallery was downloaded, AI-reviewed image by
    # image, plate-blurred and background-removed -- and then
    # _apply_manual_photo_overrides threw nearly all of it away. On a
    # ten-image gallery that is twenty vision calls, roughly 740k tokens,
    # spent on photos the video never shows, plus the scrape itself, which
    # is the slowest and least reliable part of a build (run #190 hung in
    # it for fifty minutes). When the pasted photos already cover every
    # slot there is nothing left for the listing to supply, so it is not
    # fetched.
    if (manual_urls or nested) and _pasted_photos_are_enough(manual_urls, auction_url):
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


def _pasted_race_media(url, images_dir, entry, mirror=False):
    """The exact photo of the main car to run in the drag race, if given.

    Without this the race car is whichever exterior shot _select_side_profile_media
    ranks highest, which is a reasonable guess and still only a guess -- run
    #201 raced the GT2 RS as a head-on front shot. A side profile is the one
    that reads as a car driving, and the user is the one who can see which
    photo that is. Best-effort: an unusable link falls back to the automatic
    pick rather than losing the race.
    """
    if not url:
        return None
    path = _download_car_photo(url, images_dir / "manual-race", "race")
    if not path:
        print("[single-car] The pasted drag-race photo did not download as an image; "
              "falling back to the best automatic side-profile pick.")
        return None
    if mirror:
        _mirror_photo(path)
    # Measured after any mirroring, so the direction describes the photo that
    # will actually be on screen.
    facing_direction = _facing_direction_for_photo(path, entry)
    # The same preparation the rival's own race photo gets: a plate blurred,
    # and the background cut away so the car races as a cutout on the white
    # canvas. Without this the pasted photo ran as a rectangular snapshot
    # with its sky and tarmac still attached, next to a clean rival cutout.
    blur_license_plates(path)
    path = remove_background(path)
    relative = str(path.relative_to(images_dir.parent)).replace("\\", "/")
    return {"path": relative, "facing_direction": facing_direction}


def _select_side_profile_media(media):
    """Pick one exterior photo to reuse for the decorative mini-car
    animations (drift doodle, drag race) -- a true side profile reads far
    better doing a spin or a left-to-right "race" than a front/rear crop,
    so this prefers exterior_side, falls back to exterior_full (still shows
    the whole car), then exterior_front (still reads as a real car "moving"
    even head-on), and only as a last resort exterior_rear -- a rear shot
    "driving" left-to-right visibly shows the car's backside the whole way,
    which is exactly the shot to avoid when every better option has run
    out, not a fallback tied evenly with front. Returns
    {"path", "facing_direction"} (or None) -- facing_direction lets the
    race animation flip the cutout so the car's nose actually points the
    way it's "driving" instead of sometimes appearing to race backwards."""
    exterior = [item for item in media if item["type"] == "exterior"]
    match = None
    for category in ("exterior_side", "exterior_full", "exterior_front", "exterior_rear"):
        match = next((item for item in exterior if item.get("category") == category), None)
        if match:
            break
    else:
        match = exterior[0] if exterior else None
    if not match:
        return None
    return {"path": match["path"], "facing_direction": match.get("facing_direction", "unclear")}


def _photos_argument(raw):
    """The --photos JSON object. Empty input gives {}; bad input raises.

    Ignoring a malformed value looks forgiving and is not: the photos were
    passed because they are the ones the video is supposed to show, so
    dropping them does not produce a slightly worse video, it produces a
    different one. Run #201 spent seventeen minutes building a GT2 RS story
    out of scraped gallery photos because the JSON arrived with its quotes
    stripped by the shell and this quietly returned {}. Failing here costs
    seconds instead.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SystemExit(
            f"--photos was not valid JSON ({exc}). Received: {str(raw)[:200]!r}. "
            "If this came from the workflow, the value must be passed through an "
            "environment variable -- interpolated straight into the shell command its "
            "double quotes are stripped and the JSON is destroyed."
        )
    if not isinstance(parsed, dict):
        raise SystemExit(f"--photos must be a JSON object of slot -> URL, got {type(parsed).__name__}.")
    return {key: str(value).strip() for key, value in parsed.items()
            if isinstance(value, str) and value.strip()}


def _year_in(text):
    """The four-digit model year in a chosen rival's name, if it has one."""
    match = re.search(r"\b(19|20)\d{2}\b", str(text or ""))
    return int(match.group(0)) if match else None


def apply_rival_photos(scenes, media, start_year, end_year, images_dir, manual_rival_url=None,
                       rival_year=None, mirror_rival=False):
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
                manual_rival_photo = gather_manual_rival_photo(
                    manual_rival_url, images_dir, rival_make, rival_model, mirror=mirror_rival)
            rival_path, rival_facing = manual_rival_photo
        else:
            cache_key = (rival_make, rival_model)
            if cache_key not in rival_cache:
                # The rival's own year when the user picked it, not this
                # car's: an R63 is a 2007, so searching "BMW X5 M" with the
                # R63's years asked for a 2007 X5 M, a car that did not
                # exist until 2010, and the search settled for a plain X5.
                rival_start = rival_year or start_year
                rival_end = rival_year or end_year
                rival_cache[cache_key] = gather_rival_photo(
                    rival_make, rival_model, rival_start, rival_end, images_dir)
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
    # happened to list first. Front beats rear as the last resort: a rear
    # shot "driving" left-to-right shows the car's backside the whole way,
    # which reads as racing butt-first rather than just an unglamorous
    # angle, so it's never picked while a front shot is available.
    category_rank = {"exterior_side": 0, "exterior_full": 1, "exterior_front": 2, "exterior_rear": 3}
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


# TTS starts speaking almost immediately -- measured on run #175, the first
# audible sample was at 0.060s and the opening "The" was still ramping up
# out of silence, so its consonant got clipped and the first word sounded
# swallowed. A short lead-in gives the encoder and the viewer's player
# somewhere to start.
NARRATION_LEAD_IN_SECONDS = 0.35


def add_narration_lead_in(audio_path, seconds=NARRATION_LEAD_IN_SECONDS):
    """Prepend silence to the narration, in place.

    Runs before duration normalisation and before transcription, so the word
    timeline -- and therefore captions, mouth shapes and every scene
    boundary -- is measured against the padded audio and stays in step.
    """
    audio_path = Path(audio_path)
    padded = audio_path.with_name(f"{audio_path.stem}-leadin{audio_path.suffix}")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path),
         "-af", f"adelay={int(seconds * 1000)}:all=1", str(padded)],
        check=True, capture_output=True, text=True,
    )
    padded.replace(audio_path)
    return audio_path


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


def _normalize_photo_label(text):
    """photo_label matching has to survive the model paraphrasing slightly
    even when told to copy the label verbatim -- in practice it reliably
    echoes back "<label> photo" (trailing the word "photo", taken from the
    "<label> photo: <description>" hint format it was shown) instead of the
    bare label, and a strict equality check against the bare label/category
    then never matches anything, silently falling back to the old
    pool-order behavior this was meant to fix. Stripping a trailing
    "photo"/"photograph"/"picture"/"pic" word (plus punctuation/case) makes
    the match survive that without requiring exact literal compliance."""
    text = (text or "").strip().lower()
    text = re.sub(r"[.:!]+$", "", text).strip()
    text = re.sub(r"\s+(photo|photograph|picture|pic)s?$", "", text).strip()
    return text


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

    Once same-type is fully used, this reuses an already-shown same-type
    photo BEFORE ever reaching for a different type (exterior fallback, or
    any other unused photo) -- e.g. a script with more exterior-topic
    scenes than distinct exterior photos must repeat an exterior shot, not
    "steal" the one interior photo reserved for the video's own interior
    beat. A repeated shot is a minor issue; a wrong-content-type photo
    under an unrelated topic (the actual complaint this fixes -- an
    interior photo showing up during a scene about the rear wing) is a
    real one. The "exterior" fallback (unused, then reused) only kicks in
    once same_type has no photos in the pool AT ALL, not merely once its
    photos are used up.

    Before any of that type-based matching, a scene carrying a photo_label
    (set by research_script when its narration is specifically about one
    pasted photo, e.g. "Gauge Cluster") is matched directly to the media
    item with that same label/category -- otherwise every "detail"-type
    scene just grabs whichever unused detail photo happens to be first in
    pool order, which is how a "Driver-Focused Gauges" scene could end up
    showing the rear-spoiler photo while the actual gauge-cluster photo
    goes to a different, unrelated scene.

    Labeled scenes are resolved in a first pass, over the whole scene list,
    before any type-based fallback runs -- not inline, scene by scene.
    Doing it inline let an earlier UNLABELED scene's generic same-type
    fallback grab a specific photo before the later scene that's actually
    supposed to show it got a turn (e.g. a plain "4MATIC" beat with no
    photo_label stealing the "Its all about rear seat luxury" extra via
    plain pool order, leaving the actual "Rear Seat Luxury" scene further
    down to fall back to a generic reused interior shot instead). Reserving
    every labeled match first means a later labeled scene's photo is never
    up for grabs to an earlier unlabeled one.
    """
    ordered = [None] * len(scenes)
    used_paths = set()

    for index, scene in enumerate(scenes):
        photo_label = _normalize_photo_label(scene.get("photo_label"))
        if not photo_label:
            continue
        match = next(
            (
                item for item in media
                if item["path"] not in used_paths
                and (
                    _normalize_photo_label(item.get("cue_label") or item.get("label")) == photo_label
                    or _normalize_photo_label((item.get("category") or "").replace("_", " ")) == photo_label
                )
            ),
            None,
        )
        if match:
            if match.get("cue_label"):
                scene["photo_label"] = match["cue_label"]
            ordered[index] = match
            used_paths.add(match["path"])

    for index, scene in enumerate(scenes):
        if ordered[index] is not None:
            continue
        requested = scene["media_type"]
        # Grouped details are reserved for exact narration cues, never random filler.
        fallback_media = [item for item in media if not (item.get("section") and item.get("role") == "detail")] or media
        same_type = [item for item in fallback_media if item["type"] == requested]
        exterior = [item for item in media if item["type"] == "exterior"]
        pick = (
            next((item for item in same_type if item["path"] not in used_paths), None)
            or next((item for item in same_type if item["path"] in used_paths), None)
            or next((item for item in exterior if item["path"] not in used_paths), None)
            or next((item for item in exterior if item["path"] in used_paths), None)
            or next((item for item in fallback_media if item["path"] not in used_paths), None)
            # Only reach here once every photo of every type has been used.
            or (same_type[0] if same_type else fallback_media[0])
        )
        used_paths.add(pick["path"])
        ordered[index] = pick
    return ordered


def build_short(args):
    output_dir = OUTPUT_ROOT / args.short_id
    images_dir = output_dir / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    photos = _photos_argument(args.photos)
    # An explicit --photo-* flag wins over the same slot in --photos, so the
    # individual flags keep behaving exactly as they always have.
    manual_photo_urls = {
        "front": args.photo_front or photos.get("front"),
        "side": args.photo_side or photos.get("side"),
        "rear": args.photo_rear or photos.get("rear"),
        "engine": args.photo_engine or photos.get("engine"),
        "interior": args.photo_interior or photos.get("interior"),
    }
    args.photo_rival = args.photo_rival or photos.get("rival") or None
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
    if not args.disable_comparison:
        # Naming the rival outright beats working it out from a photo: it is
        # what the user actually chose, it carries the rival's own model
        # years instead of inheriting this car's, and it skips a vision call
        # on a full-size image.
        forced_rival = (args.rival_car or "").strip() or None
        if forced_rival is None and args.photo_rival:
            rival_id_path = _download_car_photo(args.photo_rival, images_dir / "manual-rival-id", "rival")
            if rival_id_path:
                forced_rival = _identify_car_in_photo(rival_id_path)
    # Read before the script is written, so the writer has this car's real
    # engine, output and sale price in hand rather than reconstructing them
    # from search. One page, no photos, fails open.
    listing_facts = scrape_auction_facts(SCRAPER_DIR, args.auction_url, images_dir / "listing") \
        if args.auction_url else {}
    if listing_facts:
        print(f"[single-car] Listing facts: {len(listing_facts.get('facts') or {})} spec rows, "
              f"{len(listing_facts.get('sections') or {})} text sections, "
              f"price {listing_facts.get('price_text') or 'not stated'}.")
    # What the model sells for, rather than what this one example did. Read
    # from the results page the listing links to, so no slug has to be
    # guessed.
    # A price the person typed outranks everything. They know the car, and
    # four characters in a form settle an argument that a scraper and a
    # search engine between them got wrong twice -- $70,000 on a 993 Turbo
    # and $267,000 on the same car in the same week.
    market = value_from_input(args.current_price)
    if market:
        print(f"[single-car] Market: ${market['median']:,}, as stated on the build form.")
    if market is None and args.auction_url:
        comps = scrape_auction_comps(SCRAPER_DIR, args.auction_url, images_dir / "listing")
        market = summarise_comps(
            comps, listing_facts.get("title") or f"{args.make} {args.model}",
            make=args.make, today_year=time.localtime().tm_year)
        if market:
            print(f"[single-car] Market: {market['count']} comparable sales, "
                  f"median ${market['median']:,} (${market['low']:,}-${market['high']:,}).")
        elif comps:
            print(f"[single-car] {len(comps)} results on the model page, none comparable enough to use.")
    package = research_script(
        args.make, args.model, args.trim, args.start_year, args.end_year,
        photo_hints=photo_hints, forced_rival=forced_rival, disable_comparison=args.disable_comparison,
        listing_facts=listing_facts,
        market=market,
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
    else:
        # Belt-and-suspenders, same reasoning as above: the prompt asks for
        # a stat row on the comparison scene too, but main_horsepower/
        # rival_horsepower are already real verified numbers regardless of
        # whether the model remembered to also mirror them into
        # stat_label/stat_value -- so fill that row in directly from the
        # numbers already on the scene rather than depending on compliance
        # for the one stat viewers most expect to see.
        for scene in package["scenes"]:
            main_hp, rival_hp = scene.get("main_horsepower"), scene.get("rival_horsepower")
            if main_hp is not None and rival_hp is not None and not scene.get("stat_label"):
                scene["stat_label"] = "Horsepower"
                scene["stat_value"] = f"{main_hp} hp vs {rival_hp} hp"
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
    race_entry = {"name": car_label, "label": car_label, "years": "",
                  "search_hint": car_label, "visual_highlight": "", "generation_label": ""}
    side_profile_media = (_pasted_race_media(photos.get("race"), images_dir, race_entry,
                                             mirror=photos.get("race_flip") == "1")
                          or _select_side_profile_media(media))
    photo_sections = collect_photo_sections(media)
    media = order_media_for_scenes(package["scenes"], media)
    media = apply_rival_photos(
        package["scenes"], media, media_start_year, media_end_year, images_dir,
        manual_rival_url=None if args.disable_comparison else args.photo_rival,
        rival_year=_year_in(args.rival_car),
        mirror_rival=photos.get("rival_flip") == "1",
    )
    audio_path = output_dir / "narration.mp3"
    synthesize_narration(package["script"], audio_path, preset=args.voice, speed=FAST_TTS_SPEED)
    add_narration_lead_in(audio_path)
    normalized_duration = normalize_audio_duration(audio_path)
    voice_auditions = (
        generate_voice_auditions(package["script"], output_dir, args.voice) if args.audition_voices else {}
    )
    try:
        word_timeline = transcribe_word_timeline(audio_path)
    except Exception as exc:
        print(f"[single-car] Word alignment failed; falling back to estimated caption timing: {exc}")
        word_timeline = []
    # The narration in run #182 stopped at "...not just a muscle car", leaving
    # "it's a statement" unspoken -- the video simply ended mid-sentence and
    # nothing noticed. The transcript is the only evidence of what was
    # actually said, so compare it with what was meant to be said. Whisper
    # splits some tokens (hyphenated words, numbers) and so usually returns
    # *more* entries than the script has words; coming back materially short
    # means audio is missing.
    if word_timeline:
        spoken, written = len(word_timeline), _word_count(package["script"])
        if spoken < written * 0.95:
            print(
                f"[single-car] WARNING: narration audio looks truncated -- the script is {written} "
                f"words but only {spoken} were transcribed. The last words spoken were "
                f"\"{' '.join(str(w.get('word')) for w in word_timeline[-6:])}\", against a script "
                f"ending \"{' '.join(package['script'].split()[-6:])}\"."
            )
    wav_path = output_dir / "narration.wav"
    _extract_wav(audio_path, wav_path)
    timeline = build_mouth_timeline(wav_path)
    wav_path.unlink(missing_ok=True)
    manifest = {
        "car": {"make": args.make, "model": args.model, "trim": args.trim or None},
        # A verbatim snapshot of the raw build inputs -- not anything
        # derived/researched -- so the create form's "Start from a previous
        # build" dropdown can fill itself from this build (same photos, same
        # car, same comparison settings) without the user retyping every
        # link. Keys match dispatchWorkflow's single_car `inputs` shape 1:1
        # (see App.jsx's handleSingleCarShort), so the UI maps them straight
        # back onto its own form fields.
        "build_inputs": {
            "make": args.make,
            "model": args.model,
            "query": args.trim or "",
            "start_year": args.start_year if args.start_year is not None else "",
            "end_year": args.end_year if args.end_year is not None else "",
            "voice": args.voice,
            "auction_url": args.auction_url or "",
            # The resolved URLs, not the raw flags: a build dispatched with
            # --photos would otherwise record five empty slots here, and the
            # create form's "Start from a previous build" dropdown reads
            # exactly these keys to refill itself.
            "photo_front": manual_photo_urls.get("front") or "",
            "photo_side": manual_photo_urls.get("side") or "",
            "photo_rear": manual_photo_urls.get("rear") or "",
            "photo_engine": manual_photo_urls.get("engine") or "",
            "photo_interior": manual_photo_urls.get("interior") or "",
            "photo_rival": args.photo_rival or "",
            "rival_car": args.rival_car or "",
            "photo_race": photos.get("race") or "",
            "photo_race_flip": photos.get("race_flip") or "",
            "photo_rival_flip": photos.get("rival_flip") or "",
            "disable_comparison": "true" if args.disable_comparison else "false",
            "extra_photos": args.extra_photos or "",
        },
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
        "photo_sections": photo_sections,
        "selected_auction": selected_auction,
        "listing_facts": listing_facts,
        "market_value": market,
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

    # The listing, written now rather than at upload time. Everything it
    # needs is in the manifest, and having it on disk means the dashboard
    # can show the exact title and description before anything is published
    # -- and that the upload reads a reviewed file rather than regenerating
    # something nobody has seen.
    # The channel still, built from the same photos. Failing to draw one
    # must not lose the video that is already rendered, so it is caught.
    thumbnail_name = None
    try:
        thumbnail_name = build_thumbnail(manifest, output_dir, output_dir / "thumbnail.jpg").name
    except Exception as error:  # noqa: BLE001 - a thumbnail is not worth the build
        print(f"[single-car] Thumbnail failed ({error}); the video is unaffected.")

    upload_path = output_dir / "upload.json"
    upload_path.write_text(json.dumps({
        **build_youtube_metadata(manifest, credit_url=str(getattr(args, "auction_url", "") or "")),
        "video": video_path.name,
        "thumbnail": thumbnail_name,
        "privacy": "private",
        "status": "ready",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[single-car] Wrote {upload_path.name}"
          + (f" and {thumbnail_name}" if thumbnail_name else ""))
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
    parser.add_argument(
        "--current-price", default=None,
        help="What this car is worth today, e.g. 185k or $185,000. Given, it is used as-is and "
             "nothing is scraped or guessed; left empty, comparable sales are read from the "
             "auction site instead.",
    )
    parser.add_argument("--photo-front", default=None, help="Direct URL for the main car's front exterior photo.")
    parser.add_argument("--photo-side", default=None, help="Direct URL for the main car's side exterior photo.")
    parser.add_argument("--photo-rear", default=None, help="Direct URL for the main car's rear exterior photo.")
    parser.add_argument("--photo-engine", default=None, help="Direct URL for the main car's engine-bay photo.")
    parser.add_argument("--photo-interior", default=None, help="Direct URL for the main car's interior photo.")
    parser.add_argument(
        "--photos", default=None,
        help='JSON object of photo URLs by slot: {"front":"...","side":"...","rear":"...",'
             '"engine":"...","interior":"...","rival":"..."}. The individual --photo-* flags '
             "still work and win where both are given; this exists because workflow_dispatch "
             "allows only 25 inputs in total and six of them were photo URLs.",
    )
    parser.add_argument(
        "--rival-car", default=None,
        help="The comparison car by name (e.g. \"2010 BMW X5 M\"), as chosen from the suggested "
             "rivals. Used as the forced rival directly, so no vision call is needed to work out "
             "what car a pasted rival photo shows.",
    )
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
