"""Build a fast, one-car narrated Short from researched facts and stills.

This is intentionally separate from ranking and startup-battle modes. It
uses exterior/engine/interior/detail photos, a 55-60 second script, the committed
narrator sprites, word-sized captions, and gentle motion so still images do
not feel frozen.
"""
import argparse
import copy
import difflib
import json
import re
import time
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import requests

from background_removal import remove_background
from market_value import spoken_price, summarise_comps, value_from_input, value_from_sale
from thumbnail import build_thumbnail
from youtube_metadata import build_metadata as build_youtube_metadata, plain_dashes
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
from generate_sample import ROOT, CANVAS
from narrator_script import (_extract_wav, build_mouth_timeline, narration_settings,
                             synthesize_narration)
from narrator_video import (render_narrator_video, _scene_time_boundaries,
                            _media_zone_geometry)
from openai_retry import with_openai_retry
from openai_models import text_model, with_text_model
from plate_blur import blur_license_plates
from photo_story import SLOTS, SLOT_LABELS, photo_metadata, collect_photo_sections

load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
OUTPUT_ROOT = ROOT / "cars" / "single-car-shorts"
FAST_TTS_SPEED = 1.35
# Under a minute, deliberately. A Short is judged on how much of it people
# watch, and asking a stranger for a full minute is the hard part.
TARGET_DURATION_SECONDS = 55.0
# How fast the narrator actually talks, measured off a real gpt-audio read
# rather than assumed: 138 words in 61.6 seconds. This is the number that
# sets the word budget, and it belongs to the engine -- gpt-audio has no
# speed control, so its pace is fixed and a script either fits or gets
# compressed afterwards. 138 words in 56.8 seconds, on the delivery
# instruction that measured fastest of the three tried.
NARRATION_WORDS_PER_SECOND = 138 / 56.8
# The old cap was 175, written for text-to-speech generated at 1.35x. At
# the conversational model's own pace that is a 78-second read, which would
# need a 1.42x squeeze to reach 55 -- reintroducing exactly the compression
# that made the old voice sound like a machine. The cap is what the
# narrator can say in the target without being hurried.
# How much the finished take is squeezed to fit the target, and therefore
# how many more words fit in the same 55 seconds. gpt-audio has no speed
# control and will not be talked into hurrying -- "brisk" measured 2.17
# words a second, "deliberately talking fast" 2.43, "as fast as you can"
# 2.34 -- so the only lever is compressing afterwards, which moves tempo
# and leaves pitch alone.
#
# 1.15 was chosen by ear against a real build, not by arithmetic: the
# dashboard plays any narration at 1.0, 1.09, 1.2 and 1.3, and 1.09 and 1.3
# both passed, so this sits between them. For reference the squeeze that
# made the old text-to-speech voice sound like a machine was 1.35.
SPOKEN_TEMPO = 1.15
WORD_CAP = round(TARGET_DURATION_SECONDS * NARRATION_WORDS_PER_SECOND * SPOKEN_TEMPO)
TARGET_WORD_CENTER = WORD_CAP
TARGET_WORD_FLEX = 5
# Kept for the text-to-speech path, which is still reachable with
# NARRATION_ENGINE=tts and does generate speech at FAST_TTS_SPEED.
_BASE_WORDS_PER_SECOND = (182.5 / 58.0) / 1.12


def _hard_word_range(words_per_second=NARRATION_WORDS_PER_SECOND,
                     target_seconds=TARGET_DURATION_SECONDS, min_tempo=0.5, max_tempo=2.0):
    """Where a script stops being safe to ship -- derived from
    normalize_audio_duration's own atempo clamp rather than guessed. Inside
    this range a script still reaches ~target_seconds with a tempo
    correction that does not sound broken; outside it, the correction would
    have to exceed what atempo can do."""
    min_words = words_per_second * (min_tempo * target_seconds)
    max_words = words_per_second * (max_tempo * target_seconds)
    return (round(min_words), round(max_words))


# The cap is the top of the range, never its midpoint.
TARGET_WORDS = (WORD_CAP - 2 * TARGET_WORD_FLEX, WORD_CAP)
# Used to decide whether to retry the model with corrective feedback, not
# as the failure gate. A fixed margin rather than a percentage, so a script
# that overshoots by a dozen words still triggers a retry.
ACCEPTABLE_WORDS = (TARGET_WORDS[0] - 10, WORD_CAP)
# The real failure gate.
HARD_WORD_RANGE = _hard_word_range()


# A second way of writing the same video, opt-in and off by default.
#
# Four of Driving Lab's Shorts, read off their own transcripts, come out
# remarkably alike: 192-204 words, 14-19 sentences, sentences averaging
# twelve words with two- and three-word punches dropped between them. Ours
# run 139 words in seven beats of about twenty, which reads as a tour of a
# car rather than an argument about one.
#
# The density cannot be copied outright. 200 words in 55 seconds is 3.6 a
# second; gpt-audio delivers about 2.5 and has no speed control, so that
# needs a 1.45x squeeze and 1.35 is where the old voice started sounding
# like a machine. Running to 63 seconds instead asks for 1.15x, which is
# what the current build already uses -- and 63s sits inside their own
# range, their E36 video being 67.
#
# Left alone deliberately: the tuning payoff every one of their scripts
# ends on (fit these parts, get it mapped, you are at 400hp). It is the
# most copyable thing they do and the channel does not want to sound like
# a tuning shop.
SCRIPT_STYLES = {
    "classic": {
        "beats": 7,
        "seconds": TARGET_DURATION_SECONDS,
        "words": TARGET_WORDS,
        "window": (52.0, 58.0),
        "sentences": "ONE OR TWO complete sentences",
        # The photo gate lived here -- "the higher end only when you have
        # several pasted photos" -- which told a build with no photos to use
        # five or six scenes. At roughly one 18-word sentence a scene that
        # is 108 words against a 145-154 target. Pasted photos raise the
        # ceiling on scenes; they were never meant to set the floor.
        "scene_count": ("7-{max_scenes} scenes -- eight when photos were pasted and each needs its "
                        "own. Fewer than seven cannot carry the word count at this sentence "
                        "length, however well the sentences are written"),
        "scene_note": ("EIGHT OR NINE sentences across the whole script, averaging about "
                       "eighteen words -- count them. Prefer fewer, fuller scenes over many thin "
                       "one-liners, which read choppy when spoken back to back"),
        "block": "",
    },
    "dense": {
        "beats": 7,
        "seconds": 63.0,
        "words": (155, 165),
        "window": (58.0, 68.0),
        "sentences": "TWO OR THREE short sentences",
        # The base clause told it the opposite -- prefer fewer, fuller scenes --
        # and two builds landed at two sentences a scene and 136-144 words
        # because of it. A general instruction does not beat a specific one.
        # Scene count is what actually sets the word count: scenes x sentences
        # x sentence length. Three builds sat at 136-144 words while each of
        # the other two terms moved, because the clause above gated the scene
        # count on pasted photos -- none were pasted, so it used five, and
        # 5 x 3 x 9 is 135. Here the target sets it instead.
        "scene_count": ("7-{max_scenes} scenes -- use the top of that range. The scene count is "
                        "what makes the word count here, so fewer scenes means a script that "
                        "ends short no matter how the sentences are written. How many photos "
                        "were pasted does NOT limit it; a scene can share a photo type with "
                        "another"),
        # "At least THREE in every scene" was here, which is 21+ sentences
        # across seven scenes -- about 210 words against a 155-165 target.
        # It contradicted both the per-scene range and the total below, so
        # the model picked one and the word count came out wherever that
        # landed: 110 to 177 across ten builds. The number is the same in
        # all three places now.
        "scene_note": ("SIXTEEN OR SEVENTEEN sentences across the whole script -- count them. A "
                       "scene carrying one sentence is under-written for this style and the "
                       "script lands short. Thin, punchy one-liners are wanted here, not avoided"),
        "block": """

HOW THIS ONE IS WRITTEN -- this overrides the pacing guidance above.

Short sentences, and a lot of them: SIXTEEN OR SEVENTEEN of them, averaging about ten words,
which is the 160 this format is built around. Not eight sentences of twenty-five. The
arithmetic is seven scenes carrying two or three each -- five scenes of three and two of two
is nineteen, five of two and two of three is sixteen. Seven scenes of two is fourteen, and
fourteen lands short. Shorter sentences are not the whole job --
there have to be MORE of them, carrying more material, or the script simply ends
early. Vary them hard: a long sentence, then a short one, then
a two- or three-word line on its own for emphasis ("Two years." "Nothing at all."). That
rhythm is the point -- an even stream of same-length sentences is the failure mode here.

ONE argument, prosecuted the whole way through. Pick the single most interesting true thing
about this car -- what was taken out of it, why it was killed, what it beat that it should
not have, who was brought in to fix it -- and make every beat serve that. Do NOT tour the
car. A sentence about the interior, the wheels or the badge that does not serve the argument
is a sentence wasted; cut it and spend the words on the argument instead.

Specifications arrive when the argument has earned them, not before. The opening third is
setup: the claim, the car, and why it matters. Horsepower and torque belong in the middle,
as evidence for the argument, not as an opening fact.

Conversational and spoken, not written. Contractions throughout. Plain words over formal
ones. It should read like somebody telling you something they find genuinely interesting,
not like a brochure being read aloud.

End on a question that makes the viewer pick a side, not one they can simply answer. Give
them two camps and ask which they are in -- this car against the obvious thing somebody
would buy instead, cheap against fast, old against new, one country's cars against
another's. "Would you daily a fast Volvo, or does it have to be German?" is the shape:
a real choice with an argument inside it. "Would you buy one?" and "Is it worth it?" are
the failure -- there is nothing to disagree with, so nobody does."""
    },
    "medium": {
        "beats": 5,
        "seconds": 45.0,
        "words": (110, 125),
        "window": (40.0, 50.0),
        "sentences": "TWO OR THREE short sentences",
        "scene_count": ("5-6 scenes -- no more. Six beats is what fits at this length, and a "
                        "seventh makes them all too thin to say anything"),
        "scene_note": ("TWELVE OR THIRTEEN sentences across the whole script, averaging about ten "
                       "words -- count them. Every one earning its place"),
        "block": """

HOW THIS ONE IS WRITTEN -- this overrides the pacing guidance above.

This is a 45-second video. 110-125 words, about 12-13 sentences, five or six beats. That is
three quarters of the usual length: there is room for the argument AND one supporting beat,
but not for a tour of the car.

ONE argument, plus at most one beat that is not strictly part of it -- a real history fact, or
the one feature genuinely worth seeing. Everything else goes. No interior beat, no wheels beat
and no styling beat unless that is the argument itself.

Specifications where the argument needs them, not as a list. One number stated well beats four
recited.

Short sentences, plainly spoken, with contractions. Open on the claim, close on a question that
makes the viewer pick a side.""",
    },
    "short": {
        "beats": 4,
        # Average view duration sits at about nineteen seconds across eleven
        # uploads, whatever the runtime -- 0:19 on a 0:49 video and 0:19 on a
        # 1:04 one. Completion is then arithmetic: the same nineteen seconds
        # is 39% of a 49-second video and 30% of a 64-second one, and the
        # feed rewards the ratio. At 35 seconds it would be 54%.
        #
        # 90 words at gpt-audio's own ~2.5 a second is 36 seconds, and the
        # whole 80-100 range lands inside the 30-40 window, so nothing is
        # compressed here either.
        "seconds": 35.0,
        "words": (80, 100),
        "window": (30.0, 40.0),
        "sentences": "TWO OR THREE short sentences",
        "scene_count": ("4-5 scenes -- no more. There is only room for four or five beats at this "
                        "length, and a sixth makes every one of them too thin to say anything"),
        "scene_note": ("EIGHT TO TEN sentences across the whole script, averaging about ten "
                       "words -- count them. Short and punchy, and every one earning its place: "
                       "at this length there is no room for a beat that is merely true"),
        "block": """

HOW THIS ONE IS WRITTEN -- this overrides the pacing guidance above.

This is a 35-second video, not a minute. 80-100 words, about 7-9 sentences, four or five beats.
That is a third of the usual length, so most of what would normally go in has to be left out --
and the cutting is the job, not an afterthought.

ONE argument and nothing else. Pick the single most interesting true thing about this car and
spend the whole video on it. There is no room for a tour: no interior beat, no wheels beat, no
styling beat, unless that is the argument. A sentence that does not serve the one argument is
the sentence to cut, every time.

Specifications only where the argument needs them. One number stated well beats four listed.

Short sentences, plainly spoken, with contractions. Open on the claim and close on a question
that makes the viewer pick a side. Nothing in between that you would not say to someone who
asked you about this car in a pub."""
    },
}


def style_of(name):
    """The named style, falling back to the one everything shipped with."""
    return SCRIPT_STYLES.get((name or "classic").strip().lower(), SCRIPT_STYLES["classic"])
ALLOWED_MEDIA_TYPES = {"exterior", "engine", "interior", "detail", "wheel"}

PACKAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "youtube_title", "key_specs", "history", "scenes", "sources",
                 "start_year", "end_year"],
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
        # One researched historical fact, held apart from the narration for
        # the same reason key_specs is. The prompt has asked for a history
        # beat "early, right after the hook" for a long time and it keeps
        # not arriving: three builds running came back with the specs and
        # then four beats about how the car looks. Naming it as a field
        # makes it a thing the model has to go and find, and gives the
        # repair pass something to put in a scene that says nothing --
        # which, having no web search of its own, it otherwise cannot do.
        "history": {
            "type": "object", "additionalProperties": False,
            "required": ["year", "fact"],
            "properties": {
                # When it happened. A single year for a dated event, null
                # for a fact about the whole production run -- roughly 200
                # R63s were built across 2006-2013, and requiring a year
                # turned that into "in 2007, Mercedes produced approximately
                # 200 units", which is both awkward and wrong.
                "year": {"type": ["integer", "null"]},
                "fact": {"type": "string"},
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


# Said beside every price the writer is given. Prices are the one figure in
# these scripts that arrives with more precision than the format can pay for:
# "$172,233" takes nine spoken words, and the seven after "a hundred and
# seventy" buy nothing a viewer of a 40-second video will act on.
# A redline is interesting above this and unremarkable below it. Ordinary
# road cars, diesels included, sit in the sixes and low sevens; past eight
# is where it is the reason somebody watches.
NOTABLE_REDLINE_RPM = 8000


_REDLINE_RULE = f""" Mention the redline ONLY if it is {NOTABLE_REDLINE_RPM:,} rpm or higher.
Below that it is unremarkable -- ordinary road cars and even diesels sit in the sixes and low
sevens -- so it reads as a spec dump instead of a reason to watch, and the words are better
spent elsewhere. And never say a figure is not listed, published or available: one build said
"this engine doesn't have a redline listed, but it roars", which tells the viewer nothing
except that the research came up short. If you do not have a number, say something else."""


_ORIGINAL_PRICE_RULE = """ If you give the original price, it must be the manufacturer's list
price when the car was new, verified -- never an asking price from a classified, a dealer
listing or a used-car ad, which is what the car costs NOW and is usually far higher on
anything collectible. Getting this backwards inverts the whole beat: every E63 S Final
Edition build said "$170,000 new, about $137,000 today" and called it depreciation, when the
car listed near $113,000 and has gone UP. If you cannot verify the list price when new, leave
it out and give today's figure alone. And never state a car has lost or held value unless
both figures are verified and the arithmetic actually says so."""

_ROUND_PRICE_RULE = """ Round every price you say to the nearest ten thousand above a hundred
thousand, and to the nearest thousand below it, and say it the short way a person would --
"about a hundred and seventy grand", "about $140,000", "about $12,000". NEVER speak an exact
figure like "$172,233" or "$136,550": every digit past the first two is read out loud at the
cost of a fact worth hearing. This applies to the original price too, not just today's."""


def _market_block(market):
    """Recent comparable sales, as the number the value beat should use.

    This outranks both the listing and web search for "what does it cost":
    the listing prices one example, and search returns the model's general
    reputation without holding the trim -- which is how a 400hp 993 Turbo
    was once quoted at a base Carrera's $70,000.
    """
    if not market:
        return ""
    if market.get("source") == "sale":
        return f"""

WHAT THIS CAR IS WORTH -- about {spoken_price(market['median'])} today. That is what this exact car
changed hands for, in a completed sale, rounded. Use it as the current-value half of the price beat, as
one plain figure -- "they go for about {spoken_price(market['median'])} today".{_ROUND_PRICE_RULE}{_ORIGINAL_PRICE_RULE}{_REDLINE_RULE} Never
mention the auction, the listing, a bid, a sale, or where the number came from. The viewer is being told
what the car costs, not how we know."""
    if market.get("source") == "stated":
        return f"""

WHAT THIS CAR IS WORTH -- about {spoken_price(market['median'])} today. This figure was given by the
person making the video, who knows the car; the value is not up for debate and not to be adjusted,
averaged or hedged, though it is rounded for speaking. Use it as the current-value half of the price
beat, as one plain figure -- "they go for about {spoken_price(market['median'])} today".
{_ROUND_PRICE_RULE}{_ORIGINAL_PRICE_RULE}{_REDLINE_RULE} Never mention the auction, the listing, a bid, or where the number came from."""
    examples = "; ".join(
        f"{item['title']} sold for about {spoken_price(item['price'])}"
        + (f" ({item['ended']})" if item.get("ended") else "")
        for item in market.get("examples") or []
    )
    return f"""

WHAT THIS MODEL ACTUALLY SELLS FOR -- {market['count']} completed sales of the same variant, from the
auction site's own results: they run about {spoken_price(market['low'])} to
{spoken_price(market['high'])}, with the middle around {spoken_price(market['median'])}. Examples:
{examples}.

These are sales of THIS variant; never blend in a cheaper or dearer version of the same model.

Say it as one plain figure -- "they go for about {spoken_price(market['median'])} today" -- as the current-value
half of the price beat.{_ROUND_PRICE_RULE}{_ORIGINAL_PRICE_RULE}{_REDLINE_RULE} Never mention the auction, the listing, a bid, a sale,
or any single example. The viewer is being told what this car costs, not where the number came
from: no "this one sold for", no "currently bid to", no "one recently went for". Just the going
rate."""


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
    return f"""

NO VERIFIED CURRENT VALUE IS AVAILABLE for this car. State the original price if you know it, and stop
there. Do not say what it trades for, sells for, goes for, is worth or fetches today, and do not give a
percentage of appreciation or depreciation -- there is no number behind any of it, and a guess reads as
a fact to the viewer. "Originally around $110,000, and the good ones have not been cheap since" is
fine. "Originally around $110,000, now about $70,000" is not, because the second figure is
invented.{_ROUND_PRICE_RULE}{_ORIGINAL_PRICE_RULE}{_REDLINE_RULE}"""


_REDLINE_FIGURE_RE = re.compile(
    r"\bredlin\w*[^.]{0,30}?(\d{1,2}[,.]?\d{3})\s*(?:rpm)?"
    r"|(\d{1,2}[,.]?\d{3})\s*(?:rpm)?[^.]{0,20}?\bredlin\w*", re.I)
# The model saying out loud that it could not find a number.
_MISSING_FIGURE_RE = re.compile(
    r"\b(?:do(?:es)?n.t|did\s+not|does\s+not|no|isn.t|is\s+not|aren.t)\s+"
    r"(?:\w+\s+){0,3}?(?:listed|stated|published|specified|available|disclosed|documented)\b"
    r"|\bnot?\s+(?:\w+\s+){0,2}?(?:figure|number|spec)\s+(?:is\s+)?(?:listed|available|published)\b",
    re.I)


# Pictographs and dingbats. Deliberately not the whole Unicode emoji space:
# this only has to catch what a title carries.
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF\U00002B00-\U00002BFF\uFE0F]"
)


# A price with more than two significant figures: $172,233, $136,550,
# $12,250. Two-figure prices ($140,000, $12,000) and anything under a
# thousand are left alone. Years are not prices, and carry no $.
_EXACT_PRICE_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d+)?"
    # The spoken shorthand: $148K, $1.2M. Scaled to the real figure so one
    # rule judges both notations the same way.
    r"|\$\s?(\d{1,3}(?:\.\d+)?)\s?([KkMm])\b")


def _price_value(match):
    """The dollar figure a match stands for, whichever notation it used."""
    if match.group(1):
        try:
            return int(float(match.group(1).replace(",", "")))
        except ValueError:
            return None
    try:
        scaled = float(match.group(2)) * (1_000_000 if match.group(3).lower() == "m" else 1_000)
    except (TypeError, ValueError):
        return None
    return int(scaled)


def _price_is_round(value):
    """True when a price is already as short as it needs to be spoken."""
    if value >= 1_000_000:
        return value % 50_000 == 0
    if value >= 100_000:
        return value % 10_000 == 0
    if value >= 10_000:
        return value % 1_000 == 0
    # Below ten thousand these are option prices rather than car prices.
    # "$3,500 for the adaptive suspension" is already short spoken, and
    # calling it unrounded would push a real figure to $4,000 for nothing.
    return value % 500 == 0


def _round_price_text(text):
    """Every exact price in a blob, rewritten as the short spoken form."""
    def swap(match):
        value = _price_value(match)
        if value is None:
            return match.group(0)
        return match.group(0) if _price_is_round(value) else spoken_price(value)
    return _EXACT_PRICE_RE.sub(swap, str(text or "")) if text else ""


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
        # Rounded on the way in as well: handed "$172,233" verbatim, the
        # writer repeats it verbatim, and the rule below then has to fight
        # the evidence it was given.
        rounded = _round_price_text(listing_facts["price_text"])
        lines.append(f"{label}: {rounded or listing_facts['price_text']}")
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


BULLET_MARKER_RE = re.compile(r"^\s*(?:[-*\u2022\u00b7\u2013\u2014]+|\d+[.)])\s+")


def _split_angles(angles):
    """The typed notes, as bullets, in the order they were typed.

    Pasted notes wrap. The SLR run was handed three bullets in a box that
    held five lines, because two of them ran over -- and splitting on
    newlines made "sold ~1,400 by end of 2007." a bullet of its own, ranked
    second, ahead of the gearbox note it had nothing to do with. Ranking
    only means something if the bullets are the ones the person wrote.

    A line continues the one above when it starts lower-case, carries no
    marker of its own, and no blank line separates them. Nobody opens a new point in lower case and every
    wrapped line does, which is the whole of the rule -- it needs no
    trailing full stop to work, since notes are rarely punctuated that
    carefully. An explicit marker always starts a bullet, so a deliberately
    lower-case one can still be written.
    """
    bullets, broken = [], True
    for raw in str(angles or "").splitlines():
        line = raw.strip()
        if not line:
            # A line left blank between two notes separates them whatever
            # the next one starts with -- it is the clearest thing a person
            # can type to mean "new point", and wrapping never produces it.
            broken = True
            continue
        marked = bool(BULLET_MARKER_RE.match(line))
        line = BULLET_MARKER_RE.sub("", line, count=1).strip()
        # A marker with nothing after it is not a note. The regex wants
        # whitespace after the dash so "-40kg" keeps its sign, which leaves
        # a lone "-" looking like a bullet that says "-".
        if not re.search(r"[0-9A-Za-z]", line):
            continue
        if bullets and not broken and not marked and line[:1].islower():
            bullets[-1] = f"{bullets[-1]} {line}"
        else:
            bullets.append(line)
        broken = False
    return bullets


# How many notes the script is held to. The list is allowed to degrade --
# the tail gets dropped whole when the words run out -- so only the top of
# it can be enforced. Two at full length, where there is a history beat and
# a price beat a note can take; one at 35 seconds, where the block itself
# says only three beats are irreducible and a second note has nowhere to go.
NOTES_ENFORCED = 2
NOTES_ENFORCED_SHORT = 1

# An engine layout is not evidence a note was used. Both the California T
# notes said "V8" and so did the script, which would have passed note 2 --
# the one about the 296 GTB, the 819 hp and the electric Luce, none of
# which the script mentioned -- on a token that appears in practically
# every script about a Ferrari.
_GENERIC_ANCHOR_RE = re.compile(r"[VIWBLR]\d{1,2}|HP|BHP|PS|NM|MPH|KPH|RPM|AWD|RWD|FWD", re.I)


def _note_anchors(note, make, model):
    """The specifics in a note that prove the script actually used it.

    A note is covered when its particulars survive into the narration, not
    when its topic does. "Ferrari is embracing hybrid technology" keeps the
    shape of "the mid-engine car is now a plug-in hybrid V6 - the 296 GTB,
    819 hp" and discards everything that made it worth saying, and no
    keyword check on the topic can tell those two apart.

    So the anchors are the figures and the proper names that are not this
    car's own -- 296, 819, GTB, Luce, Dino, F40 -- which is the same test
    the history beat is already held to, for the same reason. A note with
    no anchors in it cannot be checked this way and is not checked.
    """
    text = str(note or "")
    own = {token.lower() for token in _name_tokens(make, model)}
    # A single digit is not an anchor -- "2+2" contributed a "2" that any
    # script mentioning two of anything would have satisfied.
    figures = {n for n in re.findall(r"\b\d[\d,.]*\b", text) if len(n) > 1}
    names = {word for word in re.findall(r"\b[A-Z][A-Za-z0-9'\-]+", text)
             if word.lower() not in own and len(word) > 1
             and not _GENERIC_ANCHOR_RE.fullmatch(word)}
    # Sentence-opening capitals are not names, and nor is the leading word
    # of the note itself.
    for sentence in _SENTENCE_SPLIT_RE.split(text.strip()):
        head = re.match(r"\b[A-Z][A-Za-z0-9'\-]+", sentence.strip())
        if head:
            names.discard(head.group(0))
    return figures | names


def _angles_block(angles, style="classic"):
    """What the person asked the script to cover, in their own words.

    They know things about these cars that research will not surface on its
    own -- that the SLR launched alongside the Enzo and the Carrera GT at
    roughly the same money, and is the only one of the three that did not
    go up. Asked for, that is a beat. Not asked for, the script is exactly
    what it was.

    The order is the instruction. Every note used to be mandatory, which
    meant five of them bought five thin beats and squeezed the rest of the
    script -- one build finished on "But what do you think?" because four
    angles had eaten the words. Ranked instead, a long list degrades: the
    top ones get written properly and the tail is dropped whole, rather
    than all of them getting a clause each.

    Their claim is checked rather than repeated. Wrong is corrected; a
    widely-held enthusiast view is said as one, because "the complaint that
    sticks is the gearbox" is true about what people say even when it is
    not a fact about the car.
    """
    wanted = _split_angles(angles)
    if not wanted:
        return ""
    # At 35 seconds there are four or five scenes in total, so protecting
    # five beats leaves a note nowhere to land. Only three are irreducible
    # there; the history and the price are what a note is allowed to take.
    keeps = (
        "the hook, the engine figures and the closing question all stay -- and at this length "
        "that is nearly the whole video, so a note REPLACES the history beat or the price beat. "
        "Take one of those out rather than squeezing a note in beside it, and never say the "
        "same figure in two scenes to make room.\n\nIf a note is about a DIFFERENT car or brand "
        "-- a rival's sales, what a competitor stopped building, how the market is moving -- keep "
        "it to one short clause inside a beat about THIS car, and never make it the opening. "
        "Context needs setting up before it means anything, and there are no words here for the "
        "setup: one build spent its hook on a rival's order books and came out titled after the "
        "rival, in a video about this car."
        if (style or "").strip().lower() == "short"
        else "the hook, the history, the engine figures, the price and the closing question all stay."
    )
    listed = "\n".join(f"  {i}. {line}" for i, line in enumerate(wanted, 1))
    return f"""

TOPICS THE PERSON COMMISSIONING THIS VIDEO SUGGESTED, IN THEIR ORDER OF PREFERENCE -- 1 first:
{listed}

These were written by somebody who knows this car and they are the SPINE OF THE SCRIPT, not
suggestions. Work down the list and cover each one properly until the words run out. What you
research yourself fills in around them: the figures, the photo beats, the things a note does
not mention.

Where a note contains specifics -- a model name, a figure, a year, another car -- SAY THOSE
SPECIFICS. A note saying "the mid-engine car is now a plug-in hybrid V6, the 296 GTB, 819 hp"
is not covered by "Ferrari is embracing hybrid technology": the names and the numbers are the
whole reason the point lands, and a script that keeps only the shape of them has said nothing.
One build was handed that exact note and never mentioned the 296, the horsepower or the
electric car, then closed by asking whether this car "offers the best of both worlds" -- a
question its own script had given the viewer no way to answer.

Where a note explains WHY something was true -- why a car was criticised, why it is thought of
differently now -- the script has to carry the reasoning, not the verdict. "Criticised for
straying from tradition, now hailed as a perfect blend of innovation and performance" is the
verdict with the argument removed. The argument is what the note gave you: it was mocked for
being turbocharged when rivals were naturally aspirated, and it reads differently now that the
alternative is a hybrid V6. Say that.

What you may leave out is the TAIL. If only the first two fit, write those two properly and
drop the rest whole -- that is the list working as intended. What you may not do is mention
all of them in passing, reorder them, or skip note 1 because note 3 was easier to write.

When you take one it REPLACES a beat you would otherwise have written -- the video does not
get longer, and the word count is unchanged. Drop a beat about how the car looks first; {keeps}

NONE OF THESE NOTES IS THE OPENING. The first sentence is still the strongest claim YOUR OWN
research found, chosen to make a viewer stay -- not note 1.
Note 1 is the most important thing to COVER, not the thing to open with, and it goes in
the body like the rest. Two builds opened
on "they trade for about $130,000 today" because the price was note 1, which buried the reason
anybody would watch. Priority orders the beats; it does not reorder the script.

Stop when there is no room left, and leave the rest out ENTIRELY. A note covered properly is
worth more than three mentioned in passing: do not compress them all into one sentence each to
fit them in, and never shorten or drop a higher-numbered note to make room for a lower one. If
only the first two fit, write those two well and ignore the others completely -- that is the
list working as intended, not a failure. Say nothing about the ones you leave out, and never
mention that topics were suggested at all.

Check each claim you use with web search before you say it, and say it at the level it
deserves:
  - Verifiable: state it as fact.
  - A theory enthusiasts widely hold, not established fact (the SLR's automatic gearbox, the
    LFA's single-clutch): say it as what people say -- "the complaint that sticks is...",
    "most owners point at...". That is honest and it is the interesting version.
  - Unheard of, or contradicted by what you find: do NOT repeat it. Say what actually happened
    instead, and keep the beat -- the topic was chosen for a reason even when the detail was
    wrong.
  - Naming another car or marque: if you take a note that names one, name it in full. Three
    ZR1 builds turned "more power than a Ferrari SF90 or a Lamborghini Revuelto" into
    "hypercars costing three times as much", which is a vaguer claim about nothing in
    particular -- and the naming was the whole point of the note. Leaving the note out
    entirely is fine; softening it into an unnamed rival is not.
Never present a guess as a fact, and never say the person asked for it."""


# Beats that are spoken for before anything else asks for one: the hook,
# the horsepower-and-torque beat and the closing question are all required
# and all checked.
RESERVED_BEATS = 3


def _budget_block(style, photo_hints, angles):
    """What has asked for a beat, against how many there are.

    A photo hint and a note are both requests for a beat, and nothing ever
    told the model how many it had. The Ford GT build had nineteen photos
    hinted and five notes against seven beats, and resolved it by spending
    three on a rear wing, a gearshift and an air intake while the reason
    Ford built the car at all went unsaid.
    """
    wanted = _split_angles(angles)
    hints = list(photo_hints or [])
    spare = max(0, style["beats"] - RESERVED_BEATS)
    asked = len(wanted) + len(hints)
    if asked <= spare:
        return ""
    return f"""

MORE HAS BEEN ASKED FOR THAN FITS. This script has about {style['beats']} beats. The hook, the
horsepower-and-torque beat and the closing question take three of them, which leaves roughly
{spare}. You have been given {len(wanted)} note(s) and {len(hints)} photo(s) to talk about -- {asked} things for {spare} beats.

Do not try to fit them all. Squeezing them in is how a beat becomes a clause that says nothing.
Choose in this order and stop when the beats are gone:
  1. The notes, in the order they are numbered. They were typed by a person who knows this car.
  2. The photos, in the order listed. A photo is already on screen whether or not you mention
     it, so a photo beat is only worth spending when the photo shows something the script would
     want to say anyway.
Say nothing at all about whatever does not fit. A photo you leave out still appears in the
video; it simply is not narrated, which is what it is there for."""


VOICE_BLOCK = """

HOW IT SHOULD SOUND -- this applies whatever the length.

"Conversational, not written" has been the instruction all along and every script has
ignored it, because an instruction with no mechanism in it is a preference. Three habits
are what make a script read as written, and all three are avoidable:

1. NEVER TRAIL OFF INTO A COMMA CLAUSE. "It makes 553 horsepower, delivering thrilling
   performance." "They sell for $100,000, reflecting significant depreciation." "The
   calipers are carbon ceramic, showcasing its racing DNA." A quarter of every sentence
   written for this channel has ended like that -- one in four, across every script --
   and not one of them is speech. Put a full stop where the comma is, or cut the clause
   entirely: what follows the comma is almost always a comment on what came before rather
   than new information. "It makes 553 horsepower." Then stop. This holds even when the
   clause carries a real figure -- "it is a 3.9 V8, making 553 horsepower" is still not
   something anybody says out loud.

2. VARY THE LENGTH. Half the sentences written for this channel are 13 to 18 words and
   only one in twelve is under eight, which is why the delivery is flat even when the
   facts are good. Land a short one after a long one. "Ferrari had not turbocharged a
   road car since the F40. Thirty years. Then this." Three sentences, one of them two
   words. That is rhythm, and it costs nothing against the word count.

3. SAY IT WITH A VERB, NOT AN ABSTRACT NOUN. "The return of turbo technology marks the
   evolution in road cars" has no person and no action in it. "Ferrari brought turbos
   back" has both, and is six words shorter. If the subject of a sentence is a concept
   rather than a car, a company or a person, rewrite it.

The test for all three: would you say this out loud to a friend who likes cars? If it
would sound odd coming out of your mouth it is wrong here, however correct it is."""


def _research_script_prompt(label, year_scope, retry_feedback="", photo_hints=None, forced_rival=None, disable_comparison=False, max_scenes=8, listing_facts=None, market=None, angles=None, style="classic", engine_clip=False):
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
    angles_block = _angles_block(angles, style)
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
        # The checkbox turns off the drag-race segment: the rival photo, the
        # horsepower bars, the race on screen. It was written as "anywhere in
        # the script", which also gagged the notes box -- twice the SLR was
        # asked for "why it sat still while the Enzo and Carrera GT climbed"
        # and twice neither car was nameable, so the point came back as
        # "didn't quite rival contemporaries", which is a different claim.
        # A car the person typed themselves outranks a checkbox about a scene.
        notes_exception = """

A car named in the commissioning notes is asked for by name, so name it as they wrote it.
Keep their comparison as the comparison they wrote; do not soften it into "rivals",
"contemporaries" or "supercars costing three times as much", which says nothing.""" \
            if _split_angles(angles) else ""
        no_comparison_block = f"""

HARD REQUIREMENT: the user has turned off the rival-comparison SEGMENT for this video -- the rival photo, the
horsepower bars and the drag race on screen. That means: do NOT set rival_make/rival_model/main_horsepower/
rival_horsepower/main_quarter_mile_seconds/rival_quarter_mile_seconds on ANY scene. Leave every one of those
null, on every scene, even if the narration does name another car -- those fields are what put the race on
screen, and they are the only thing being switched off here.

What is NOT switched off is the words. You may still name another car in the narration where it earns its
place -- as value, history or context ("more power than a Ferrari that costs three times as much", "the one
the Enzo and the Carrera GT left behind"). What you must not write is the head-to-head beat itself: no scene
built as a spec-for-spec duel, no "420 hp versus its 455", no narrating a race. Spend that beat on an
ownership/value insight, a character observation or another history/mechanical point instead, so the script
still hits its word target and beat variety.{notes_exception}"""
    chosen = style_of(style)
    low, high = chosen["words"]
    return f"""Write a narration of exactly {low}-{high} words total -- count as you go. This word count is a hard requirement, not a suggestion. If you land under {low}, the fix is never to pad sentences or slow down -- it's to research and add another genuinely interesting beat, either historical or mechanical: who designed it, a notable race win/record/motorsport pedigree, a bit of production history (why it exists, what it replaced, a notable limited run or special edition), a fact about its reputation/legacy, or a specific engineering/mechanical detail (how the suspension or rear axle is set up, the steering system, chassis/platform sharing, a notable engineering trade-off) that's genuinely well-documented for this car. This format is meant to be packed with real, well-researched detail people want to listen to, not stretched -- a short, thin script is a failure to research deeply enough, not an acceptable outcome.{retry_feedback}{_market_block(market)}{_no_market_block(market)}{_listing_facts_block(listing_facts)}{photo_hints_block}{angles_block}{_budget_block(chosen, photo_hints, angles)}{VOICE_BLOCK}{_engine_clip_block() if engine_clip else ""}{forced_rival_block}{no_comparison_block}{chosen['block']}

Research and write one original vertical car-video package about {label}, scoped to {year_scope}. Use web search and verify every technical comparison and historical claim. Write a quick, conversational narration split across {chosen['scene_count'].format(max_scenes=max_scenes)} in speaking order, each scene being {chosen['sentences']} -- {chosen['scene_note']} -- each scene's "narration" is the exact words spoken during that beat, and all of them concatenated in order form the entire script, so each one must read naturally both alone and flowing into the next (no "scene 1, scene 2" choppiness). Start with a strong value/performance hook, name the exact car early, then the history/design-legacy beat (a motorsport win or record, why this generation/model exists, a notable special edition -- whatever is genuinely well-documented for this car, verified with web search, not invented) comes next, early, right after the hook -- not saved for near the end -- then cover engine/turbo (state both horsepower AND torque as real numbers in this beat, not horsepower alone), drivetrain, a direct head-to-head comparison against one real, well-known cross-shop rival -- this beat is REQUIRED, and that scene must carry rival_make, rival_model, main_horsepower and rival_horsepower as real verified numbers, because a comparison scene with those four fields filled is what puts the head-to-head drag race on screen. Run #176 dropped the comparison altogether and lost that whole segment. Only skip it, using an ownership/value insight instead, if you genuinely cannot name a fair rival for this car, tuning potential only when supportable, and finish with a direct viewer-choice question -- spread across the scenes in that order. That closing question is a HARD REQUIREMENT, not an optional flourish: the final scene must end on a real question aimed at the viewer that calls back to the hook's claim ("so would you daily a five-hundred-horsepower minivan, or is that a step too far?"). A closing scene that summarises what you just said, or restates what the car is about, is a failed ending -- rewrite it as a question. Use short spoken sentences and natural contractions. Do not imitate or quote any creator.

The hook must be the very first words, no throat-clearing lead-in like "Check out the..." or "Let's talk about...". The FIRST SENTENCE must contain a real number or a hard superlative, and must NOT contain the car's name -- the name is the payoff, so it lands in the second sentence ("...that's the R63 AMG"). "A hand-built seven-seater that hits sixty in four-point-four, and almost nobody knows it exists" is the shape: a concrete claim that makes the viewer want the name. "The Mercedes-Benz R63 AMG might surprise you" is the failure mode -- it names the car, promises interest instead of delivering any, and could be said about any car ever made. Never open by asserting that something is surprising, interesting, special or underrated; state the fact that makes it so and let the viewer conclude it. The claim has to be genuinely verifiable, not just punchy.

Never name a specific individual as the designer unless that person is a real, easily verifiable, widely publicized credit for this exact car (the kind of name that shows up across multiple independent, reputable sources, not just one page) -- a wrong or invented name is worse than no name at all. When you can't verify a specific person, either skip the designer credit and use a different history/legacy fact instead (a motorsport result, a production milestone, why the generation exists), or credit the design studio/brand's design language generally without inventing a person.

Every sentence has to earn its place with a specific, concrete fact -- a real number, a named comparison, a verifiable detail -- not a vague enthusiast-copy adjective doing the work instead. Cut lines like "adding to its sporty agility" or "making every drive engaging and dynamic" or "celebrated for its precise steering" that describe a *feeling* about the car without any fact backing it up -- if you can't attach a real number, a named comparison, or a specific verifiable detail to a claim, cut the claim and replace it with one you can verify, don't soften it into vague praise. This applies to every beat, not just the hook.

Write like an excited, knowledgeable friend talking fast about a car they love, not a brochure. These exact constructions are banned outright, because they read as generated copy: "a testament to", "design ethos", "blending luxury with practicality", "adding a touch of exclusivity", "catering to family needs", "creating a motorsport feel", "highlighting its performance lineage", and any sentence built on "isn't just for looks", on "blending X with Y", or on "provides a sporty feel". Run #176 still slipped through with "the brushed aluminum pedals and bolstered seats provide a sporty feel, blending luxury with performance intentions" -- three clauses saying nothing. Name what the pedal material is actually for, or which seat it is, or cut the sentence. Also banned is the appearance beat that carries no fact: "the red brake calipers highlight its
performance aura", "the full-width taillight emphasizes its sleek look", "the circular air vents
elevate the aesthetics". Three builds running spent four of their eight scenes this way and had
no room left for anything the viewer did not already get from the photo. Describing a part is
only worth a scene if you say something about it the picture cannot -- what it is for, what it
replaced, what it costs, why it is unusual. Also banned is stat-shaped padding -- a number that sounds like data but tells the viewer nothing they can use, like "reflecting an annual depreciation of about 8%". Give the two prices and let them do the subtraction. Favor punchy, stacked, specific claims over smooth marketing prose -- "that's more horsepower per liter than the [famous engine], and it's only got three cylinders" reads as genuinely engaging; "it delivers a dynamic and engaging driving experience" reads as filler no matter how true it is. A strong hook is a bold, specific, verifiable superlative or comparison (most powerful, quickest, cheapest, rarest -- something with a real number and a real point of comparison attached), not a generic "this car blends performance and luxury" opener. Every superlative must name the group it actually wins: "the most powerful 911 ever built", "the most expensive Spyder Porsche has sold", "the quickest minivan ever made". Never aim one at cars in general -- "the most luxurious car ever produced" about a $217,545 718 Spyder is not a bold claim, it is a false one, and the true version was available. Casual contractions and informal phrasing are good here -- this should sound spoken, not written.

Also write "youtube_title" -- the video's title on the channel, under 100 characters, ending in a
fire emoji. It is the only thing a viewer reads before deciding to watch, so two things about it
are not negotiable.

FIRST: it must NOT contain the make or the model. Not the make on its own, not the model on its
own, not the year with them. The name is what the viewer is watching to find out, and the script
gives it away in the second beat anyway. "This one was built for a single year. \U0001F525" works;
"2024 BMW M3 CS: Track Weapon \U0001F525" does not. The description and the hashtags carry the name
for search, so nothing is lost by keeping it out of the title.

SECOND: the title must promise exactly what the FIRST SENTENCE of the narration delivers, in the
same words. A title promising one thing over an opening about another is how a video loses nine
viewers in ten: they swipe in for what was promised, hear something else in the first two seconds,
and leave. If the title says a single model year, the first sentence is about that single model
year. If the first sentence is about the weight, the title is about the weight. Write the hook
first, then write the title from it -- not the other way round, and never independently.

Within those two, pick whichever the car actually earns: a question about the argument people have
over it, a price move when both figures are verified, or the single strangest true fact. Never
claim something the script does not support, and never promise a reveal the video does not
contain.

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

"history" is one specific, well-documented thing about this car's background, found with web
search, written as a complete spoken sentence that carries its own date -- it is used verbatim.
Scope the date to the fact: a one-off event gets its year ("won its class at Le Mans in 1989"),
but a fact about a production run says so, and naming the span beats a bare number: "only 1,270
were built across the 2004-2007 run" tells the viewer how rare it is AND how long it was on sale.
"year" is null for those. Verify the span belongs to THIS car and not to the model line it sits
in -- roughly 200 R63 AMGs were sold in the US in a single model year, while the R-Class around
it ran from 2006 to 2013, and attaching the line's span to the AMG's number is a false sentence
built from two true ones. About 200 R63s exist in total, not in 2007, and pinning that to a
single model year is both awkward and wrong -- a race result, how many were built and why that number, what it
replaced, who engineered which part of it, a special edition and what made it different, an
engineering decision and the reason for it. What makes it specific is something to hold on to: a
figure, or a name that is not the car's own. "Only 2,157 were built" has one. "The tub was built
by McLaren in Woking while the V8 was assembled at Affalterbach" has the other. "A
limited-production supercar blending luxury with racing pedigree" has neither, and neither does
"revered by enthusiasts" or "became iconic". When the car arrived is part of its story and worth
saying -- it is just not, on its own, the beat. That fact has to be SPOKEN in the history beat,
not merely recorded in the field -- the field exists so it has been researched before the
narration needs it.

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
    # with_text_model here and text_model() at the other six call sites:
    # this is the first model call of any build, and a model that cannot
    # take this request is downgraded for the rest of the process, so one
    # wrap in the right place covers the repairs too.
    response = with_text_model(lambda chosen: with_openai_retry(lambda: OpenAI().responses.create(
        model=chosen,
        input=prompt,
        tools=[{"type": "web_search_preview"}],
        text={
            "format": {
                "type": "json_schema", "name": "single_car_short", "strict": True,
                "schema": _schema_with_scene_cap(max_scenes),
            },
        },
    )))
    package = json.loads(response.output_text.strip())
    for scene in package["scenes"]:
        scene["narration"] = _strip_citations(scene["narration"])
    package["script"] = " ".join(scene["narration"] for scene in package["scenes"])
    specs = package.get("key_specs") or {}
    if specs.get("price"):
        # The table is read, not spoken, but it is the same figure and a
        # viewer seeing "$224K new" under a script that says "about $220,000"
        # is looking at the build disagreeing with itself.
        specs["price"] = _round_price_text(specs["price"]) or specs["price"]
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
# A rate of depreciation is stat-shaped padding: it sounds like data and
# tells the viewer nothing they can act on, because the two prices it was
# computed from are already in the sentence. The prompt has banned it in
# prose since run #176; the Mazdaspeed3 build still shipped "depreciating
# roughly 4% per year" because the ban was a list of literal phrases and
# this is a shape, not a phrase.
# The verbs an empty sentence reaches for. Each one asserts that a part
# matters without saying anything about it: "the calipers highlight its
# performance aura", "the taillight emphasizes its sleek look", "the
# plaque highlights the bespoke craftsmanship". Banned only in a sentence
# with no number in it -- "the diffuser cuts lift by 15%" uses the same
# verb and is a real claim.
EMPTY_CLAIM_RE = re.compile(
    r"\b(?:emphasi[sz]\w*|highlight\w*|underscor\w*|elevat\w*|accentuat\w*"
    r"|exemplif\w*|epitomi[sz]\w*|showcas\w*|enhanc\w*|define[sd]?|promis\w*"
    r"|stands? out|hint\w* at|speak\w* to)\b",
    re.I,
)

# The one construction that makes a script read as written rather than
# said: a comma, then a present participle commenting on the sentence it
# is hanging off.
#
#   "...carbon ceramic brakes, showcasing its racing DNA."
#   "...557 lb-ft of torque, delivering thrilling performance."
#   "...sell for about $100,000, reflecting significant depreciation."
#
# Measured across 39 shipped scripts: 104 of 409 sentences end this way,
# one in four, in 35 of the 39. That is a grammatical habit, not a phrase
# to blacklist, and it is why banning phrases one at a time was never
# going to fix the voice. The commonest participles are the emptiest --
# enhancing, reflecting, emphasizing, delivering, boasting, ensuring,
# showcasing -- but the construction is the problem even when the clause
# carries a fact: nobody says "it is a 3.9 V8, making 553 horsepower" out
# loud. They say "it is a 3.9 V8. It makes 553 horsepower."
#
# So the fix is always the same and always available: full stop instead of
# the comma, or cut the clause.
#
# -ing words that are not verbs here, which would otherwise match: a
# stoplist is cheaper than parsing, and a wrong match costs one retry.
_NOT_A_PARTICIPLE = frozenset("""
during nothing anything something everything morning evening spring string thing things
king being wing swing ceiling sibling offspring racing tuning styling handling braking
engineering weighting upbringing
""".split())
TRAILING_PARTICIPLE_RE = re.compile(r",\s+([A-Za-z]+ing)\b")

# The sentence that sounds like a verdict and contains none.
#
# Every one of these came out of a single Ferrari California T build, which
# is what made the pattern obvious: "the return of turbo technology marks
# the evolution in road cars", "showcasing its racing DNA", "delivering
# thrilling performance with each acceleration", "a perfect blend of
# innovation and performance", "highlight the fusion of performance and
# luxury", and a closing question asking whether it "offers the best of
# both worlds".
#
# They share a shape: two abstract nouns asserted to combine, or a feeling
# asserted to be delivered. What makes them worth banning rather than
# merely disliking is that none of them is false -- they are unfalsifiable,
# so they survive every fact check in here while saying nothing. They are
# also what a viewer hears as an advert, which is the opposite of the
# reason anybody watches one of these.
#
# EMPTY_CLAIM_RE catches the verb; this catches the clause the verb is
# reaching for, including where there is no such verb at all ("marks",
# "delivering").
MARKETING_FILLER_RE = re.compile(
    r"\b(?:perfect\s+|ultimate\s+|seamless\s+)?"
    r"(?:blend|fusion|marriage|melding|combination)\s+of\b"
    r"|\bbest\s+of\s+both\s+worlds\b"
    r"|\b(?:racing|motorsport|track|performance)\s+DNA\b"
    r"|\b(?:thrilling|exhilarating|breathtaking|heart-pounding|spine-tingling"
    r"|intoxicating|visceral|effortless|sublime)\s+"
    r"(?:performance|acceleration|power|speed|drive|driving)\b"
    r"|\bwith\s+(?:each|every)\s+"
    r"(?:acceleration|downshift|upshift|rev|corner|gear\s+change|press\s+of)\b"
    r"|\bmarks?\s+(?:a|the)\s+(?:evolution|new\s+era|turning\s+point|milestone)\b"
    r"|\b(?:pure|raw)\s+(?:driving\s+)?(?:emotion|theatre|theater|adrenaline)\b",
    re.I,
)

# Either direction and either order. A rate of appreciation is the same
# padding as a rate of depreciation -- both are arithmetic on two prices
# the sentence already gave -- and "appreciating annually around 5%" walked
# past a rule that only knew one word and one word order.
# The E63 builds got past this twice. "That's nearly a 2% annual drop" missed
# because the rule wanted the literal word depreciation beside "annual", and
# "reflecting a subtle depreciation over time" missed because it carries no
# number at all -- which makes it worse, not better: it is a value claim with
# nothing behind it, in the beat that already gave both prices.
_TREND_WORD = r"(?:de|ap)preciat\w*|drop|dip|fall|decline|rise|gain|climb|growth|increase|jump"
DEPRECIATION_PADDING_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*%[^.]{0,40}?\b(?:per\s+year|a\s+year|annually|yearly|per\s+annum)"
    r"|\b(?:annual|yearly)\s+(?:" + _TREND_WORD + r")"
    r"|\b(?:" + _TREND_WORD + r")[^.]{0,40}?\b\d+(?:\.\d+)?\s*%"
    r"|\b\d+(?:\.\d+)?\s*%[^.]{0,25}?\b(?:" + _TREND_WORD + r")"
    r"|\b(?:per\s+year|a\s+year|annually|yearly|per\s+annum)[^.]{0,25}?\d+(?:\.\d+)?\s*%"
    # A value trend with no figure behind it at all.
    # "over the years" is the same sentence as "over time", and the rule
    # knew only the second: the California T shipped "reflecting significant
    # depreciation over the years" in the beat that had just given both
    # prices -- the third time this rule has been beaten by a synonym.
    r"|\b(?:de|ap)preciation\b[^.]{0,30}?\b(?:over\s+(?:time|the\s+years?"
    r"|the\s+decades?|its\s+life)|since|so\s+far|in\s+value)"
    r"|\b(?:hold|holds|holding|held|keep|keeps|keeping|kept|retain|retains|retaining|retained)"
    r"\s+(?:its\s+)?(?:significant\s+)?value\b"
    r"|\b(?:hasn.t|has\s+not|never)\s+(?:really\s+|truly\s+)?(?:de|ap)preciat\w*)",
    re.I,
)


# Horsepower has to arrive inside the opening beats, not merely somewhere in
# the script. Three scenes of an eleven-scene, sixty-second cut is roughly the
# first fifteen seconds -- the window in which a viewer decides to stay.
EARLY_TECH_SCENES = 3
# The hook withholds the name on purpose; this is where it has to arrive.
INTRODUCE_BY_SCENE = 3
# A closing that could end any video ever made. Length is not the test --
# "so would you daily it?" is five words and proposes something, while "but
# what do you think?" is five words and proposes nothing. What separates
# them is whether anything in the question is about this car.
EMPTY_CLOSING_RE = re.compile(
    r"^(?:so|but|now|well)?[\s,]*(?:what|how)?\s*(?:do|are|about)?\s*you\s*(?:think|decide|"
    r"reckon|feel)?\s*\??$|^\s*(?:thoughts|your call|you decide|let me know)\s*\??$",
    re.I,
)
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


# Words too common to prove a title and its hook are about the same thing.
TITLE_FILLER = frozenset("""
a an the this that these those there here is are was were be been being it its of for to in on at
by with from and or but not no nor so if then than as too very can could will would should may
might must do does did done have has had you your yours we our they their he she his her what
which who whom whose how why when where while all any both each few more most other some such own
same still just only really actually even about after before over under again once new now one
""".split())


def _title_terms(text):
    """The words in a title that could carry its promise."""
    words = re.findall(r"[A-Za-z0-9]+", str(text or "").lower())
    return {w for w in words if len(w) > 2 and w not in TITLE_FILLER}


# A number carrying one of these is a specification; one without is usually a
# name. "cars" is deliberately absent -- "this one 993 car" matched it, and a
# chassis code is exactly what must not be flagged.
SPEC_UNIT_RE = (r"(?:hp|bhp|horsepower|lb-?ft|pound-?feet|nm|newton-?met\w*|torque|units?|"
                r"examples?|built|made|produced|kg|pounds|lbs|mph|km/h|seconds?|litre|liter|rpm)")


def _repeated_specs(script, make, model):
    """Hard numbers stated more than once, which at 95 words costs a third of
    the video. "Only about 800 were made" twice in one short script; "227
    horsepower" three times in another.

    A name is not a repeat: the car's own numbers, a year, and anything that
    never appears next to a unit are all left alone, so 993 and 722 pass.
    """
    from collections import Counter

    name_numbers = set(re.findall(r"\d[\d.]*", f"{make} {model}"))
    tokens = [t.replace(",", "") for t in re.findall(r"\d[\d,]*(?:\.\d+)?", script)]
    found = []
    for number, count in Counter(tokens).items():
        if count < 2 or len(number.replace(".", "")) < 2:
            continue
        if number in name_numbers or re.fullmatch(r"(19|20)\d\d", number):
            continue
        spellings = {number}
        if number.isdigit() and len(number) > 3:
            spellings.add(f"{int(number):,}")
        alternatives = "|".join(re.escape(form) for form in spellings)
        if not re.search(rf"\b(?:{alternatives})\b(?:\s+\w+){{0,2}}\s+{SPEC_UNIT_RE}\b",
                         script, re.I):
            continue
        found.append((number, count))
    return sorted(found, key=lambda item: -item[1])


def _script_violations(package, make, model, market=None, pasted_slots=()):
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
    elif EMPTY_CLOSING_RE.match(closing):
        # A question mark is not a closing. Squeezed for words by four
        # asked-for angles, one build finished on "But what do you think?"
        # -- which passes the rule above and could end any video ever made.
        violations.append(
            f'your last scene is "{closing}", which could end any video ever made. Ask something '
            "about THIS car that calls back to the hook's claim."
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
    # The history beat has been asked for in the prompt for a long time and
    # keeps not arriving -- three builds running came back with the specs
    # and then four beats about how the car looks. The researched fact is
    # in the package; whether the narration ever says it is checkable.
    history = (package.get("history") or {})
    year = history.get("year")
    fact = str(history.get("fact") or "")
    # A history is rejected for carrying no specifics, not for how it is
    # worded. The first rule keyed on "debuted" and threw out "debuted as a
    # limited-production supercar, blending Mercedes luxury with McLaren's
    # racing pedigree" -- which is thin, but thin is the reason, and the
    # year a car arrived is a real part of its story.
    #
    # What makes a fact specific is something to hold on to: a figure that
    # is not just its own year, or a name that is not the car's own. "Built
    # 75 for homologation" has one. "Won its class at Le Mans" has the
    # other. "A limited-production supercar blending luxury with racing
    # pedigree" has neither, and neither does "revered by enthusiasts".
    own = {token.lower() for token in _name_tokens(make, model)}
    own |= {"the", "a", "an", "it", "its", "this", "was", "in", "and"}
    figures = [n for n in re.findall(r"\b\d[\d,]*\b", fact)
               if not (year and n.replace(",", "") == str(int(year)))]
    # Digits included in the name, because car names have digits in them.
    # The pattern wanted letters after the capital, so F40, ZR1, GT3, M3
    # and SF90 were not names as far as this rule was concerned: the
    # California T's history, "first turbocharged Ferrari road car since
    # the F40", was rejected for having nothing specific in it when the
    # specific thing was right there.
    names = [word for word in re.findall(r"\b[A-Z][A-Za-z0-9'\-]*", fact)
             if word.lower() not in own and len(word) > 1]
    # The opening word is capitalised because it opens the sentence.
    if names and fact.strip().startswith(names[0]):
        names = names[1:]
    if fact and not figures and not names:
        violations.append(
            f'your history is "{fact[:120]}" -- there is nothing specific in it. Give a figure '
            "or a name: how many were built and why, a race and where it finished, who "
            "engineered it, what it replaced, what it beat."
        )
    elif year and not re.search(rf"\b{int(year)}\b", script):
        violations.append(
            f'you researched what happened in {year} ("{str(history.get("fact"))[:120]}") and '
            "never said it. That beat belongs right after the hook, in the narration, with the "
            "year in it."
        )
    # A sentence using one of these verbs with no number in it is asserting
    # that a part matters instead of saying anything about it. The Vantage
    # build spent eight of ten scenes this way -- grille, air vents, rear
    # haunches, character lines, inspection plaque, taillight -- with the
    # ban sitting in the prompt being ignored, because nothing read it.
    #
    # Measured per sentence, because per scene let every one of these
    # through. A scene is often two sentences, and one real figure anywhere
    # in it satisfied the "has a number" test for both: the California T
    # shipped "calipers house carbon ceramic brakes, showcasing its racing
    # DNA" and "steering wheels highlight the fusion of performance and
    # luxury" with this rule switched on, because each sat next to a
    # sentence that had a number in it.
    empty = [sentence for scene in scenes
             for sentence in _SENTENCE_SPLIT_RE.split((scene.get("narration") or "").strip())
             if EMPTY_CLAIM_RE.search(sentence) and not re.search(r"\d", sentence)]
    if empty:
        violations.append(
            f'{len(empty)} sentence(s) assert that something matters without saying anything about '
            f'it, starting with "{empty[0]}". Say what the part is for, what it replaced, what '
            "it costs, or why it is unusual -- or spend the sentence on a fact instead."
        )
    # The filler clause itself, with or without one of those verbs in front
    # of it, and regardless of whether the sentence carries a figure: "553
    # hp and 557 lb-ft, delivering thrilling performance with each
    # acceleration" has two real numbers and still ends in nothing.
    # The trailing participle, which is the voice rule that matters: it is
    # one construction rather than a list of phrases, and it accounts for a
    # quarter of every sentence this pipeline has ever written.
    hanging = [match.group(0).strip() for match in TRAILING_PARTICIPLE_RE.finditer(script)
               if match.group(1).lower() not in _NOT_A_PARTICIPLE]
    if hanging:
        violations.append(
            f'{len(hanging)} sentence(s) trail off into a comment clause -- "{hanging[0]}". '
            "That is writing, not speech: nobody says \"it is a 3.9 V8, making 553 horsepower\" "
            "out loud. Put a full stop where the comma is and start a new sentence, or cut the "
            "clause. Every one of them, not just the first."
        )
    # Rhythm. Half of every script this pipeline writes sits between 13 and
    # 18 words a sentence and only 8% come in under 8, which is what makes
    # the delivery flat however good the facts are -- a person telling you
    # something lands a short one after a long one.
    lengths = [len(sentence.split()) for scene in scenes
               for sentence in _SENTENCE_SPLIT_RE.split((scene.get("narration") or "").strip())
               if sentence.strip()]
    if len(lengths) >= 8 and sum(1 for n in lengths if n < 8) < 2:
        violations.append(
            f"every sentence is about the same length ({min(lengths)}-{max(lengths)} words) and "
            "it reads flat. Break at least two of them into something short -- four or five "
            "words, landing after a long one. This is rhythm, not word count: the total is "
            "unchanged."
        )
    for match in MARKETING_FILLER_RE.finditer(script):
        violations.append(
            f'"{match.group(0).strip()}" is advert copy, not a claim -- it is true of every car '
            "ever marketed, so it tells the viewer nothing and it sounds written rather than "
            "said. Cut the clause, or replace it with what actually happens: what the part does, "
            "what it costs, what it beat, what it gave up."
        )
        break
    # A note the script was given and did not use.
    #
    # This is the rule that was missing. The instruction to cover the notes
    # lived only in the prompt, and the California T build ignored two of
    # three: it was handed the 296 GTB, its 819 hp and the electric Luce,
    # said none of them, and closed by asking whether the car "offers the
    # best of both worlds" -- a question nothing in its own script answered.
    # Nothing checked, so nothing retried.
    #
    # Only the top of the list is enforced, because the list is meant to
    # degrade: the tail is dropped whole when the words run out.
    wanted = _split_angles(package.get("_angles"))
    if wanted:
        limit = (NOTES_ENFORCED_SHORT
                 if (package.get("_style") or "").strip().lower() == "short"
                 else NOTES_ENFORCED)
        spoken = script.lower()
        for position, note in enumerate(wanted[:limit], 1):
            anchors = _note_anchors(note, make, model)
            if not anchors:
                continue
            if not any(anchor.lower() in spoken for anchor in anchors):
                violations.append(
                    f'note {position} is not in the script. It asked for '
                    f'"{note[:140]}" and the narration says none of {sorted(anchors)[:6]}. '
                    "Write that note as a beat, with its own figures and names in it -- the "
                    "specifics are the point, and the topic without them says nothing."
                )
    # A pasted photo the script never gives a beat to is simply not in the
    # video. The prompt has said "you MUST write one scene for each" the
    # whole time and it is not enough: with four asked-for angles also
    # wanting beats, the R63 build wrote seven scenes and none of them was
    # typed engine, so the engine photo was downloaded, described, handed
    # over and dropped. Only the slots with their own media_type can be
    # checked this way -- front, side and rear are all "exterior" and the
    # picker shares those out.
    _article = lambda word: "an" if word[0] in "aeiou" else "a"
    typed = {scene.get("media_type") for scene in scenes}
    for slot in ("engine", "interior"):
        if slot in (pasted_slots or ()) and slot not in typed:
            violations.append(
                f"you were given {_article(slot)} {slot} photo and no scene is about the {slot} -- set one "
                f'scene\'s media_type to "{slot}" and give it a beat, or that photo is not in '
                "the video at all."
            )
    padding = DEPRECIATION_PADDING_RE.search(script)
    if padding:
        violations.append(
            f'"{padding.group(0).strip()}" is stat-shaped padding -- you already gave both prices, '
            "so the rate tells the viewer nothing. Drop it and spend the words on a fact about the car."
        )
    # An emoji in the spoken words is always a leak: the title carries one by
    # convention and the narration never should, because TTS either reads it
    # as a word or stumbles over it.
    emoji = _EMOJI_RE.search(script)
    if emoji:
        violations.append(
            f'the narration contains "{emoji.group(0)}" -- that belongs in the title, not in the '
            "words being spoken. Take the headline text out of the scene and leave only what is said."
        )
    for match in _REDLINE_FIGURE_RE.finditer(script):
        raw = match.group(1) or match.group(2) or ""
        try:
            rpm = int(raw.replace(",", "").replace(".", ""))
        except ValueError:
            continue
        if rpm < NOTABLE_REDLINE_RPM:
            violations.append(
                f"you mention a {rpm:,} rpm redline, which is not worth the words -- ordinary road "
                f"cars sit there. Only call out a redline at {NOTABLE_REDLINE_RPM:,} or above, where "
                "it is a reason to keep watching. Cut it and say something specific instead."
            )
            break
    missing = _MISSING_FIGURE_RE.search(script)
    if missing:
        violations.append(
            f'you say a figure is not available -- "{missing.group(0).strip()}". Never narrate what '
            "you could not find: a viewer does not know a number was missing until you tell them. "
            "Cut the sentence and put a fact you do have in its place."
        )
    # A marque named in the notes but absent from the script used to be a
    # violation. It cannot be one any more: the notes are suggestions now,
    # so a script that passes over one is doing what it was told. The
    # instruction that a note WORTH taking must name its car in full lives
    # in the prompt, where the model can weigh it against everything else
    # competing for the same beat. Checking it here only re-imposed the
    # requirement the prompt had just lifted.
    exact = []
    for match in _EXACT_PRICE_RE.finditer(script):
        value = _price_value(match)
        if value is None:
            continue
        if not _price_is_round(value):
            exact.append((match.group(0).strip(), spoken_price(value)))
    if exact:
        # Checked rather than only asked for: the prompt has said to round
        # for a while and builds still shipped "$172,233", which is nine
        # spoken words where "about a hundred and seventy grand" is six and
        # tells the viewer the same thing.
        listed = ", ".join(f'{said} (say about {short})' for said, short in exact[:3])
        violations.append(
            f"you speak an exact price -- {listed}. Round it: every digit past the first two is "
            "read out loud and buys the viewer nothing in a video this short."
        )
    repeated = _repeated_specs(script, make, model)
    if repeated:
        worst = ", ".join(f'"{number}" {count} times' for number, count in repeated)
        violations.append(
            f"you state the same number more than once -- {worst}. Say each figure once and "
            "spend the words you get back on something you have not said yet."
        )
    # The title is the only thing read before the decision to watch, so it
    # gets the same two rules the hook does.
    title = str(package.get("youtube_title") or "").strip()
    scenes_for_title = package.get("scenes") or []
    if title and scenes_for_title:
        named = [token for token in _name_tokens(make, model)
                 if re.search(rf"\b{re.escape(token)}\b", title, re.I)]
        if named:
            # Naming the car is what the viewer is watching to find out, and
            # it is what the second beat exists to deliver. The description
            # and hashtags still carry it for search.
            violations.append(
                f'your title names the car ("{", ".join(named)}") -- it was "{title}". The name is '
                "the payoff, so it belongs in the second beat and not above the video. The "
                "description and hashtags carry it for search."
            )
        opening = _SENTENCE_SPLIT_RE.split(
            (scenes_for_title[0].get("narration") or "").strip())[0]
        if opening and not (_title_terms(title) & _title_terms(opening)):
            # A title promising one thing over an opening about another lost
            # nine viewers in ten: swiped in for "built for a single year",
            # heard "543 horsepower" instead, left inside two seconds.
            violations.append(
                f'your title and your first sentence are about different things -- the title is '
                f'"{title}" and the video opens "{opening}". Whoever swiped in for the title hears '
                "something else within two seconds and leaves. Rewrite the title so it promises "
                "what that first sentence actually delivers, in the same words."
            )
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


_DETERMINERS = {"the", "a", "an", "this", "that", "these", "those",
                "its", "his", "her", "their", "our", "your", "my"}
# Walking back from the name stops here: a preposition starts a new phrase,
# so a determiner on its far side belongs to a different noun.
_PHRASE_BOUNDARY = {"of", "for", "from", "with", "by", "to", "in", "on", "at",
                    "than", "like", "about", "into", "over", "under", "and", "or"}


def _phrase_has_determiner(before):
    """Whether the noun phrase the name sits in already has an article.

    Walks back over the adjectives in front of the name. A possessive
    ("Porsche's most powerful 911") counts as the article; a preposition
    ("the touch of AMG") ends the phrase before one is found.
    """
    for token in reversed(re.findall(r"[\w\u2019'\-]+", before)):
        lowered = token.lower()
        if lowered in _DETERMINERS or re.search(r"(?:'s|\u2019s)$", lowered):
            return True
        if lowered in _PHRASE_BOUNDARY:
            return False
    return False


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
    seen = {"count": 0, "abort": False}

    def replace(match):
        text = match.group(0)
        # Only a run carrying a real name word is the car's name; "the" or a
        # bare year on its own is just a sentence doing its job.
        if not any(re.search(rf"\b{re.escape(token)}\b", text, re.I) for token in long_tokens):
            return text
        seen["count"] += 1
        before = sentence[:match.start()]
        # A name revealed at the end after a dash or colon is an appositive:
        # the sentence has built up to naming the car. Take the name out and
        # the build-up points at nothing -- run #231 shipped "thanks to the
        # touch of car - this one." There is no stand-in that repairs that,
        # so the hook is left naming the car instead.
        if re.search(r"[\u2014\u2013:-]\s*$", before) and match.end() >= len(sentence.rstrip(" .!?")):
            seen["abort"] = True
            return text
        # A reveal exists to say the name. "...400 horsepower -- this is the
        # Porsche 911 Turbo" became "this is this one", a sentence that
        # introduces nothing. Whatever stands in, the construction is
        # already pointing at it, so there is nothing to repair.
        if re.search(r"\b(?:this|that|it|here|there)\s+(?:is|'s|was)\s*$", before, re.I) \
                or re.search(r"\b(?:meet|say hello to|introducing|welcome)\s*$", before, re.I):
            seen["abort"] = True
            return text
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
        if heads_its_own_phrase:
            return " this one "
        # A bare noun only works where the phrase already supplied an
        # article: "the most powerful road-legal 911" has one, "the touch of
        # AMG" does not, and became "the touch of car". Without one the name
        # is not the phrase's head noun at all -- it is usually the maker,
        # standing where no stand-in belongs -- so the hook is left alone.
        if not _phrase_has_determiner(before):
            seen["abort"] = True
            return text
        return " car "

    repaired = pattern.sub(replace, sentence)
    if seen["abort"] or not seen["count"]:
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


def _rewrite_closing_as_question(hook, closing, label):
    """Ask for one sentence: the closing turned into a viewer question.

    The whole-script retry asks for this among six other constraints and
    loses it -- the Mazdaspeed3 build failed the rule on all four attempts.
    On its own, with the hook to call back to and nothing else to satisfy,
    it is a much easier request.
    """
    prompt = (
        "You are fixing the last line of a short car video's narration.\n\n"
        f"The video opens: \"{hook}\"\n"
        f"It currently closes: \"{closing}\"\n\n"
        f"Rewrite that closing line as ONE spoken question aimed straight at the viewer about the "
        f"{label}, calling back to the claim in the opening. Keep it under 20 words, conversational, "
        "with contractions. It must end in a question mark. Do not add facts, do not summarise the "
        "video, and reply with the sentence alone -- no quotes, no preamble."
    )
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=text_model(), input=prompt,
    ))
    return _strip_citations(response.output_text.strip()).strip().strip('"\u201c\u201d')


def _repair_closing(package, label):
    """Make the last scene end on a question, or leave it exactly as it was.

    A rewrite that comes back without a question mark, empty, or long enough
    to blow the word budget is not an improvement, so it is discarded rather
    than shipped -- same rule the hook surgery follows.
    """
    scenes = package.get("scenes") or []
    if not scenes:
        return package
    closing = (scenes[-1].get("narration") or "").strip()
    if not closing or closing.endswith("?"):
        return package
    hook = _SENTENCE_SPLIT_RE.split((scenes[0].get("narration") or "").strip())[0]
    try:
        rewritten = _rewrite_closing_as_question(hook, closing, label)
    except Exception as error:  # noqa: BLE001 - a build is worth more than a question mark
        _note_repair(what="closing", outcome="call failed", detail=str(error)[:300])
        print(f"[single-car] Could not rewrite the closing ({error}); leaving it as written.")
        return package
    if not rewritten.endswith("?") or len(rewritten.split()) > 25:
        _note_repair(what="closing", outcome="unusable", returned=rewritten[:300])
        print(f'[single-car] The rewritten closing was not usable ("{rewritten}"); leaving it as written.')
        return package
    scenes[-1]["narration"] = rewritten
    package["script"] = " ".join(scene["narration"] for scene in scenes)
    package["word_count"] = _word_count(package["script"])
    _note_repair(what="closing", outcome="applied", returned=rewritten[:300])
    print(f'[single-car] Rewrote the closing as a question: "{rewritten}"')
    return package


# One targeted pass per broken rule, and a ceiling so a rule the model
# cannot satisfy costs a few calls rather than an unbounded run.
MAX_TARGETED_REPAIRS = 5
# Rounds of lengthening a short script. Three is two sources plus a retry:
# spec-sheet sentences, then a researched beat, then one more of either.
MAX_FLOOR_ROUNDS = 3
# Every repair attempt and what became of it, written into the build.
#
# The closing question has now failed twice in a row with two independent
# repairs both declining to fix it, and the reason each printed sits in a
# runner log that is unreachable by the time anyone asks. A build that
# cannot say why it shipped broken cannot be debugged without guessing.
_REPAIR_LOG = []


def _note_repair(**entry):
    _REPAIR_LOG.append(entry)


def _spec_sheet(package):
    """The verified figures the build already looked up, for the repair.

    Without these a repair that needs a number has nowhere to get one. The
    Mazdaspeed3 build knew its torque was 280 lb-ft -- it is in key_specs --
    and still shipped with the rule broken, because the repair was handed
    the scenes alone and told not to invent anything.
    """
    specs = package.get("key_specs") or {}
    known = [f"{name.replace('_', ' ')}: {value}" for name, value in specs.items() if value]
    # The researched history too: a scene that says nothing is usually a
    # scene with nothing to say, and this is the thing to put in it.
    history = package.get("history") or {}
    if history.get("fact"):
        year = f"{history['year']}: " if history.get("year") else ""
        known.append(f"history -- {year}{history['fact']}")
    return "\n".join(known)


# Not every broken rule costs the same. A repeated figure is a blemish on a
# video somebody is already watching; an opening that contradicts the title
# decides whether they watch at all. Counting them equally is what let the
# R8 ship: the repair that fixed its title/opening mismatch introduced a
# smaller rule, the count did not drop, and the fix was thrown away. It went
# out with the worst stay-to-watch of any build -- 17.3% against a 22.3%
# channel average -- while the people who did get past the opening watched
# longer than on almost any other video.
HOOK_VIOLATIONS = (
    "your title names the car",
    "your title and your first sentence are about different things",
    "you never say which car this is",
    "but not until after scene",
    "never states the car's horsepower",
)
# Worth fixing, but never at the price of the opening.
POLISH_VIOLATIONS = (
    "you state the same number more than once",
    "every sentence is about the same length",
    "which is not worth the words",
    "the rate tells the viewer nothing",
    "Take the headline text out of the scene",
)
HOOK_WEIGHT, CONTENT_WEIGHT, POLISH_WEIGHT = 3, 2, 1


def _violation_weight(rule):
    """What one broken rule costs, for weighing a repair against the script
    it would replace.

    Anything unrecognised counts as ordinary content: a rule nobody has
    classified should not be able to outrank the hook, nor be waved through
    as cosmetic.
    """
    text = str(rule or "")
    if any(marker in text for marker in HOOK_VIOLATIONS):
        return HOOK_WEIGHT
    if any(marker in text for marker in POLISH_VIOLATIONS):
        return POLISH_WEIGHT
    return CONTENT_WEIGHT


def _violation_cost(rules):
    return sum(_violation_weight(rule) for rule in rules)


# The rules about the title, which a scene rewrite cannot fix. Handing them
# to _rewrite_scene_for asked for a line of narration to be changed in order
# to fix text that is not narration, and on the R8 both failed for that
# reason -- one "did not help", one "traded for another".
TITLE_VIOLATION_MARKER = "your title"


# Which scene the start-up follows. Two, so it lands around twelve to
# eighteen seconds in on a 55-second build: late enough that the car has
# been named and a reason to care has been given, early enough to be
# inside the twenty seconds the average viewer actually watches.
ENGINE_CLIP_AFTER_SCENE = 2
# And the hard edge of that window. A boundary later than this is not
# worth using -- the clip would play to people who have already gone.
ENGINE_CLIP_LATEST_SECONDS = 20.0


def _engine_clip_block(after_scene=ENGINE_CLIP_AFTER_SCENE):
    """Tells the script a start-up clip interrupts it, and where."""
    return f"""

A CLIP OF THIS EXACT CAR STARTING UP PLAYS AFTER SCENE {after_scene}. Real video, with its
real engine note, from the listing's own footage. It is not something you describe -- the
viewer is about to hear it.

So scene {after_scene} ENDS BY HANDING OVER TO IT, in its last sentence, out loud: "listen to
it start", "here is what that sounds like", "wait for this". Something a person would say
before shutting up and letting the car talk. Do not say the word "clip" or "video" -- as far
as the viewer is concerned you are standing next to the car.

Scene {after_scene + 1} picks up AFTER the engine has run. Do not open it by reacting to the
sound as though you heard it with them -- no "incredible, right?" -- and do not re-describe
the noise. Carry on with the next thing you were going to say."""


def _rewrite_title_for(violation, title, opening, label):
    """One new YouTube title, fixing one title rule and nothing else.

    The title is the only thing read before the decision to watch, so it is
    repaired on its own terms rather than by rewriting the video under it.
    """
    prompt = (
        "You are fixing the title of a short vertical car video about the "
        + str(label) + ".\n\n"
        'The current title is: "' + str(title) + '"\n'
        'The video opens with this exact sentence: "' + str(opening) + '"\n\n'
        "One rule is broken: " + str(violation) + "\n\n"
        "Write ONE replacement title. It has to promise exactly what that opening sentence "
        "delivers, in words a viewer would recognise from it when they hear it two seconds "
        "later, and it must NOT name the car -- the name is the payoff the video itself "
        "delivers, and the description carries it for search. Keep it under about sixty "
        "characters, make it a claim or a question somebody would stop scrolling for, and do "
        "not invent a single fact the opening does not already contain.\n"
        'Reply as JSON: {"title": "<the new title>"}'
    )
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=text_model(), input=prompt,
        text={"format": {"type": "json_object"}},
    ))
    answer = json.loads(response.output_text.strip())
    return _strip_citations(str(answer["title"])).strip()


def _rewrite_scene_for(violation, numbered_scenes, label, specs=""):
    """Ask for one scene rewritten to fix one rule, and nothing else.

    The retry loop's only move is to rewrite the whole script, which throws
    away six good beats to fix a seventh -- and often loses a different rule
    on the way back. This asks the smaller question.
    """
    spec_block = (
        f"\n\nVerified figures for this car, already researched -- use these and no others:\n{specs}"
        if specs else ""
    )
    # The empty-scene rule fires on a verb with no number beside it, and a
    # rewrite that keeps both fails again: "the quad exhaust tips not only
    # underscore its AMG sporting credentials but also enhance the engine's
    # deep growl" was offered for exactly that violation. What satisfies it
    # is worth saying outright rather than leaving to be inferred.
    how = ""
    if "without saying anything about it" in violation:
        how = ("\n\nThis rule is satisfied two ways and only these two: put a real number in "
               "the sentence, or cut the claim and say the concrete thing instead. Do not keep "
               "the verb and add another clause -- emphasises, highlights, underscores, "
               "elevates, showcases, enhances, defines and promises all fail this rule on "
               "their own.")
    prompt = (
        f"You are fixing one line of a short car video's narration about the {label}.\n\n"
        f"The scenes, in spoken order:\n{numbered_scenes}{spec_block}\n\n"
        f"One rule is broken: {violation}{how}\n\n"
        "Rewrite the ONE scene that fixes it, changing as little as possible and leaving every "
        "other scene alone. Keep it to roughly the same length, keep it spoken and conversational "
        "with contractions, and do not invent facts -- every number must come from the figures "
        "above or from the script itself. Reply as JSON: "
        '{"index": <the scene number you rewrote>, "narration": "<the new line>"}'
    )
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=text_model(), input=prompt,
        text={"format": {"type": "json_object"}},
    ))
    answer = json.loads(response.output_text.strip())
    return int(answer["index"]), _strip_citations(str(answer["narration"])).strip()


def _loose_json(text):
    """The first JSON object in a reply, however it is wrapped.

    Without JSON mode the answer can arrive fenced in a code block or with
    a sentence in front of it, and losing a researched fact to a stray
    backtick would be a poor trade.
    """
    body = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", body, re.S)
    if fenced:
        body = fenced.group(1).strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in the reply: {body[:120]}")
    return json.loads(body[start:end + 1])


def _research_replacement_history(label, rejected, year_scope=""):
    """Go and find a different historical fact, with web search.

    A rule that needs a fact the build does not have cannot be repaired by
    rewriting a sentence. Asked to fix its history twice, the model returned
    "the SLR debuted as a limited-production supercar" and then "in 2008,
    the SLR Roadster debuted as a limited-production model" -- the same fact
    in new words, because rewording is all the scene rewrite can do.
    """
    prompt = (
        f"Find one specific, well-documented thing about the {label}"
        f"{f' ({year_scope})' if year_scope else ''}. Use web search and verify it.\n\n"
        "Write it as one complete spoken sentence that carries its own date, because it is used "
        "verbatim. Scope the date to the fact: a one-off event gets its year, a fact about the "
        "production run names the span -- 'only 1,270 were built across the 2004-2007 run'. Check the "
        "span belongs to this exact model and not to the wider line it sits in. Use null for "
        "\"year\" when the fact spans a run.\n\n"
        f"It must NOT be this, which was already rejected: \"{rejected}\"\n\n"
        "It has to carry something to hold on to: a figure, or a name that is not the car's "
        "own. \"Only 2,157 were built\" has one. \"The tub was built by McLaren in Woking\" has "
        "the other. \"A limited-production supercar blending luxury with racing pedigree\" has "
        "neither, and neither does \"revered by enthusiasts\".\n\n"
        'Reply as JSON: {"year": <integer>, "fact": "<one sentence, spoken, verified>"}'
    )
    # No text format here: the API refuses web search together with JSON
    # mode -- "Web Search cannot be used with JSON mode", a 400 that made
    # every history repair fail before it started. Research is the whole
    # point of this call, so the shape gives way and the answer is parsed.
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=text_model(), input=prompt,
        tools=[{"type": "web_search_preview"}],
    ))
    answer = _loose_json(response.output_text)
    return {"year": answer.get("year"), "fact": _strip_citations(str(answer.get("fact") or "")).strip()}


def _repair_history(package, make, model, market, label):
    """Replace a rejected history with a researched one, or leave it."""
    rejected = str((package.get("history") or {}).get("fact") or "")
    try:
        found = _research_replacement_history(
            label, rejected,
            year_scope=" ".join(str(y) for y in
                                (package.get("start_year"), package.get("end_year")) if y))
    except Exception as error:  # noqa: BLE001 - a build is worth more than a fact
        _note_repair(what="history", outcome="call failed", detail=str(error)[:300])
        return None
    # A null year is a real answer: a fact about the whole production run
    # does not have one. Only a missing fact makes the reply unusable.
    if not found.get("fact"):
        _note_repair(what="history", outcome="unusable", returned=json.dumps(found)[:300])
        return None

    # The year is metadata; the sentence is the thing. If the fact does not
    # say the year, the year field is dropped rather than the fact -- a
    # answer whose date is only in the metadata would otherwise be thrown
    # away by the rule that the narration must say it.
    if found.get("year") and not re.search(rf"\b{int(found['year'])}\b", found["fact"]):
        found = dict(found, year=None)

    candidate = copy.deepcopy(package)
    candidate["history"] = found
    # The narration has to say it, so the beat that carried the old one is
    # where the new one goes -- the second scene, where the prompt puts it.
    scenes = candidate.get("scenes") or []
    if len(scenes) < 2:
        return None
    # Spoken as written. Prepending the year produced "In 2007, in 2007,
    # Mercedes-Benz produced approximately 200 units" when the fact already
    # carried it, and pinned a whole-production-run figure to one model year
    # when it did not. The fact is asked for as a complete sentence, so it
    # is used as one.
    fact = found["fact"].strip()
    scenes[1]["narration"] = fact[0].upper() + fact[1:]
    candidate["script"] = " ".join(scene["narration"] for scene in scenes)
    candidate["word_count"] = _word_count(candidate["script"])
    if any("nothing specific in it" in rule or "never said it" in rule
           for rule in _script_violations(candidate, make, model, market)):
        _note_repair(what="history", outcome="did not help", returned=found["fact"][:300])
        return None
    _note_repair(what="history", outcome="applied", scene=2, returned=scenes[1]["narration"][:300])
    print(f'[single-car] Researched a new history: "{scenes[1]["narration"]}"')
    return candidate


def _plain_dashes_everywhere(package):
    """Em and en dashes out of every string this build will show or speak.

    The title and description are handled in youtube_metadata, which is
    where they are built; this is the narration, the headlines typed across
    the top, and the spoken script they are joined into.
    """
    for scene in package.get("scenes") or []:
        for field in ("narration", "headline", "stat_label", "stat_value",
                      "stat_label_2", "stat_value_2"):
            if isinstance(scene.get(field), str):
                scene[field] = plain_dashes(scene[field])
    if isinstance(package.get("script"), str):
        package["script"] = " ".join(
            str(s.get("narration") or "") for s in (package.get("scenes") or [])
        ).strip() or plain_dashes(package["script"])
    for field in ("title", "youtube_title"):
        if isinstance(package.get(field), str):
            package[field] = plain_dashes(package[field])
    return package


def _repair_title(package, violation, make, model, market, label, pasted_slots=()):
    """A new title for one title rule, kept only if it is actually better.

    Same bargain as a scene repair: the rule it targeted has to be gone and
    the script has to be cheaper overall, or the original title stands.
    """
    title = str(package.get("youtube_title") or "").strip()
    scenes = package.get("scenes") or []
    opening = _SENTENCE_SPLIT_RE.split((scenes[0].get("narration") or "").strip())[0] if scenes else ""
    if not title or not opening:
        return package
    try:
        rewritten = _rewrite_title_for(violation, title, opening, label)
    except Exception as error:  # noqa: BLE001 - a build is worth more than a title
        _note_repair(what=violation[:120], outcome="call failed", detail=str(error)[:300])
        print(f"[single-car] Could not rewrite the title ({error}); leaving it.")
        return package
    if not rewritten:
        _note_repair(what=violation[:120], outcome="unusable", returned=str(rewritten)[:300])
        return package

    candidate = copy.deepcopy(package)
    candidate["youtube_title"] = rewritten
    after = _script_violations(candidate, make, model, market, pasted_slots)
    before_cost = _violation_cost(_script_violations(package, make, model, market, pasted_slots))
    if violation in after or _violation_cost(after) >= before_cost:
        _note_repair(what=violation[:120],
                     outcome="did not help" if violation in after else "traded for another",
                     returned=rewritten[:300],
                     still=[rule[:120] for rule in after])
        print(f'[single-car] The new title "{rewritten}" did not help; keeping the original.')
        return package
    _note_repair(what=violation[:120], outcome="applied", returned=rewritten[:300])
    print(f'[single-car] Retitled: "{rewritten}"')
    return candidate


def _repair_violations(package, make, model, market, label, limit=MAX_TARGETED_REPAIRS, pasted_slots=()):
    """Fix what the retries left broken, one rule at a time.

    Every edit is kept only if it actually helps: the rule it targeted is
    gone and no new one appeared. That check is the whole reason this is
    safe to do -- the same function that found the problem decides whether
    the fix was one, so a rewrite that trades one violation for another is
    thrown away instead of shipped.
    """
    # Rules it has already failed at. A rule nothing can fix must not block
    # the ones that can: the SLR build left three violations because its
    # history could not be repaired and the loop stopped there, with the
    # padding and the empty scene never even attempted.
    refused = set()
    for _ in range(limit):
        violations = [rule for rule in _script_violations(package, make, model, market, pasted_slots)
                      if rule not in refused]
        if not violations:
            break
        violation = violations[0]
        # A rule that needs a fact the build does not have cannot be fixed
        # by rewriting a sentence around the fact it already rejected.
        if "nothing specific in it" in violation or "never said it" in violation:
            replaced = _repair_history(package, make, model, market, label)
            if replaced is None:
                refused.add(violation)
                continue
            package = replaced
            continue
        scenes = package.get("scenes") or []
        # A title rule is not fixable by rewriting narration, so it gets the
        # title rewritten instead. Everything after this point edits a scene.
        if violation.startswith(TITLE_VIOLATION_MARKER):
            package = _repair_title(package, violation, make, model, market, label, pasted_slots)
            if violation in _script_violations(package, make, model, market, pasted_slots):
                refused.add(violation)
            continue
        numbered = "\n".join(f"{i}. {s.get('narration') or ''}" for i, s in enumerate(scenes, 1))
        try:
            index, narration = _rewrite_scene_for(violation, numbered, label,
                                                  _spec_sheet(package))
        except Exception as error:  # noqa: BLE001 - a build is worth more than a rule
            _note_repair(what=violation[:120], outcome="call failed", detail=str(error)[:300])
            print(f"[single-car] Could not repair \"{violation[:60]}...\" ({error}); leaving it.")
            refused.add(violation)
            continue
        if not (1 <= index <= len(scenes)) or not narration:
            _note_repair(what=violation[:120], outcome="bad scene number", returned=str(index))
            print(f"[single-car] The repair named scene {index}, which does not exist; leaving it.")
            refused.add(violation)
            continue

        candidate = copy.deepcopy(package)
        candidate["scenes"][index - 1]["narration"] = narration
        candidate["script"] = " ".join(s["narration"] for s in candidate["scenes"])
        candidate["word_count"] = _word_count(candidate["script"])
        after = _script_violations(candidate, make, model, market, pasted_slots)
        # Weighed the same way on both sides -- `violations` is filtered by
        # what has already been refused, so comparing it against the full
        # list would call every repair a regression. Weight rather than
        # count, so trading a broken hook for a repeated figure is a fix and
        # not a wash.
        before_cost = _violation_cost(
            _script_violations(package, make, model, market, pasted_slots))
        if violation in after or _violation_cost(after) >= before_cost:
            _note_repair(what=violation[:120],
                         outcome="did not help" if violation in after else "traded for another",
                         returned=narration[:300],
                         still=[rule[:120] for rule in after])
            print(f"[single-car] The repair for \"{violation[:60]}...\" did not help; keeping the original.")
            refused.add(violation)
            continue
        package = candidate
        _note_repair(what=violation[:120], outcome="applied", scene=index, returned=narration[:300])
        print(f'[single-car] Repaired scene {index}: "{narration}"')
    return package


def _more_sentences_for(numbered_scenes, label, shortfall, specs="", avoid=""):
    """Ask for whole new sentences to add to named scenes, and nothing else.

    The retry loop's only move is to rewrite the whole script, and across
    29 builds that did not close the gap: dense averaged 145 words against
    a 155-165 target. This asks the smaller, countable question -- add this
    many words, as new sentences, to these scenes -- so the answer can be
    measured instead of trusted.
    """
    spec_block = (
        f"\n\nVerified figures for this car, already researched -- use these and no others:\n{specs}"
        if specs else ""
    )
    avoid_block = (
        f"\n\nA previous attempt added these and they were rejected for repeating something the "
        f"script already said. Do not offer them again:\n{avoid}" if avoid else ""
    )
    sentences = max(2, round(shortfall / 10))
    prompt = (
        f"You are lengthening a short car video's narration about the {label}.\n\n"
        f"The scenes, in spoken order:\n{numbered_scenes}{spec_block}{avoid_block}\n\n"
        f"The script is {shortfall} words SHORT. Add about {shortfall} words as roughly "
        f"{sentences} NEW complete sentences, spread across the middle scenes.\n\n"
        "Rules, all of them hard:\n"
        "- Add whole new sentences. Do NOT lengthen or reword a sentence that is already there.\n"
        "- Do NOT touch scene 1 or the last scene: the hook and the closing question are shaped.\n"
        "- Every new sentence carries a real fact -- a figure from the list above, a named "
        "comparison, or a specific verifiable detail. No adjectives doing the work.\n"
        "- Do NOT restate a number or a fact the script already gives. Say something new.\n"
        "- Spoken and conversational, with contractions. Keep each one about ten words.\n\n"
        'Reply as JSON: {"additions": [{"index": <scene number>, "sentences": "<the new '
        'sentence(s) to append to that scene>"}]}'
    )
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=text_model(), input=prompt,
        text={"format": {"type": "json_object"}},
    ))
    answer = json.loads(response.output_text.strip())
    out = []
    for item in answer.get("additions") or []:
        try:
            index = int(item["index"])
        except (KeyError, TypeError, ValueError):
            continue
        text = _strip_citations(str(item.get("sentences") or "")).strip()
        if text:
            out.append((index, text))
    return out


def _research_extra_beat(label, already, year_scope=""):
    """Find one more real beat, with web search, for a script that is short.

    When the spec sheet is spent, adding sentences from it means repeating
    what the script already says -- which the floor check rejects, so it
    stalls below target. This is where new material comes from. Same shape
    as _research_replacement_history, and the same reason it cannot use
    JSON mode: the API refuses web search alongside it.
    """
    prompt = (
        f"Find one specific, well-documented thing about the {label}"
        f"{f' ({year_scope})' if year_scope else ''} that is NOT already covered below. "
        "Use web search and verify it.\n\n"
        f"Already covered, so do not repeat any of it:\n{already}\n\n"
        "Good material: a motorsport result, a production figure, who engineered it, what it "
        "replaced, a specific engineering choice (suspension, axle, chassis, steering), a "
        "notable option and what it cost. It has to carry a figure, or a name that is not the "
        "car's own.\n\n"
        "Write it as two or three complete spoken sentences, about twenty-five words in total, "
        "used verbatim in the middle of the video. Conversational, with contractions.\n\n"
        'Reply as JSON: {"beat": "<two or three spoken sentences, verified>"}'
    )
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model=text_model(), input=prompt,
        tools=[{"type": "web_search_preview"}],
    ))
    text = response.output_text.strip()
    match = re.search(r"\{.*\}", text, re.S)
    answer = json.loads(match.group(0) if match else text)
    return _strip_citations(str(answer.get("beat") or "")).strip()


def _extend_to_floor(package, floor, cap, label, make, model, market=None,
                     pasted_slots=(), rounds=MAX_FLOOR_ROUNDS):
    """Bring a short script up to the floor, measuring every step.

    The counterpart to _enforce_word_cap, and the reason it exists: that
    function has always fixed overshoot deterministically, while undershoot
    was only ever *asked* for in the prompt. Across 29 builds the asking
    worked 11 times -- classic averaged 137 against a 145-154 target and
    dense 145 against 155-165 -- because a model cannot count the words it
    is writing. So the count is measured here and closed here.

    Two sources, in order. First more sentences off the verified spec sheet,
    which is free and cannot invent anything. When that stalls -- and it
    stalls as soon as the spec sheet is spent, which is exactly what a thin
    car like the M5 CS runs into -- a researched new beat, which is where
    genuinely new material comes from.

    Every round is kept only if it helped: the count went up, nothing new
    broke, and the cap was not breached. A round that cannot be kept is
    dropped and the next source is tried.
    """
    def rescript(target):
        """Rebuild the script from the scenes and return its real length.

        Only called on a candidate, never on the package being kept: like
        _enforce_word_cap, this leaves a package it did not change exactly
        as the model returned it, including that package's own reported
        word_count. Writing a recomputed count over an untouched package
        would quietly disagree with the model about a build nothing was
        done to.
        """
        target["script"] = " ".join(scene["narration"] for scene in target.get("scenes") or [])
        target["word_count"] = _word_count(target["script"])
        return target["word_count"]

    # Measured off the script, not off the package's own word_count -- that
    # field is the model's claim, and a model miscounting its own output is
    # the reason this function exists.
    count = _word_count(package.get("script") or "")
    if count >= floor:
        return package

    before = count
    rejected = []
    for round_number in range(1, rounds + 1):
        shortfall = floor - count
        scenes = package.get("scenes") or []
        if len(scenes) < 3:
            break
        numbered = "\n".join(f"{i}. {s.get('narration') or ''}" for i, s in enumerate(scenes, 1))
        candidate = copy.deepcopy(package)
        # Sentences first; a researched beat once sentences stop landing.
        use_research = bool(rejected)
        try:
            if use_research:
                beat = _research_extra_beat(
                    label, package.get("script") or "",
                    year_scope=" ".join(str(y) for y in
                                        (package.get("start_year"), package.get("end_year")) if y))
                if not beat:
                    _note_repair(what=f"{shortfall} words short", outcome="unusable",
                                 detail="the research call returned no beat")
                    break
                # Into the scene before the closing question, which keeps the
                # hook and the ending the shape they were written in. Proposed
                # rather than applied: the loop below is what trims it to the
                # room left, and applying it here skipped that entirely.
                target = max(1, len(candidate["scenes"]) - 2)
                offered = [(target + 1, beat)]
            else:
                offered = _more_sentences_for(numbered, label, shortfall,
                                              _spec_sheet(package), avoid="\n".join(rejected))
                if not offered:
                    rejected.append("(nothing offered)")
                    continue

            # One place where anything is applied, sentence at a time, while
            # there is room under the cap.
            room = cap - _word_count(
                " ".join(x["narration"] for x in candidate.get("scenes") or []))
            taken = []
            for index, text in offered:
                # Never the hook or the closing question.
                if not 2 <= index <= len(candidate["scenes"]) - 1:
                    continue
                for sentence in _SENTENCE_SPLIT_RE.split(text.strip()):
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    cost = _word_count(sentence)
                    if cost > room:
                        continue
                    candidate["scenes"][index - 1]["narration"] = (
                        candidate["scenes"][index - 1]["narration"].rstrip() + " " + sentence)
                    room -= cost
                    taken.append((index, sentence))
            if not taken:
                _note_repair(what=f"{shortfall} words short", outcome="did not help",
                             returned=" ".join(t for _, t in offered)[:300],
                             detail="every sentence offered was longer than the room left")
                rejected.append(" ".join(t for _, t in offered))
                continue
            offered = taken
        except Exception as error:  # noqa: BLE001 - a build is worth more than a word count
            _note_repair(what=f"{shortfall} words short", outcome="call failed",
                         detail=str(error)[:300])
            print(f"[single-car] Could not lengthen the script ({error}); leaving it at {count}.")
            break

        grown = rescript(candidate)
        after = _script_violations(candidate, make, model, market, pasted_slots)
        before_count = len(_script_violations(package, make, model, market, pasted_slots))
        added = " ".join(text for _, text in offered)[:300]
        if grown <= count or grown > cap or len(after) > before_count:
            why = ("added nothing" if grown <= count
                   else f"overshot the {cap}-word cap" if grown > cap
                   else "broke a rule that was not broken before")
            _note_repair(what=f"{shortfall} words short", outcome="did not help",
                         returned=added, detail=why,
                         still=[rule[:120] for rule in after])
            print(f"[single-car] Round {round_number} of lengthening {why}; discarding it.")
            rejected.append(added)
            continue

        package = candidate
        count = grown
        _note_repair(what=f"{shortfall} words short", outcome="applied",
                     returned=added,
                     detail=f"research beat" if use_research else "spec-sheet sentences",
                     words=count)
        print(f"[single-car] Lengthened the script to {count} words "
              f"({'researched a new beat' if use_research else 'added sentences'}).")
        if count >= floor:
            break

    if count < floor:
        # Loud, because a script that quietly shipped short is the whole
        # reason this function exists.
        print(f"[single-car] WARNING: the script is {count} words after {rounds} round(s) of "
              f"lengthening, still under the {floor}-word floor (started at {before}).")
    return package


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


def script_package_from_build(build_id, repository=None, branch="cars-output"):
    """A finished build's scenes, to be spoken and rendered again.

    A build can be good and still ship broken -- run #37340574003 wrote a
    script worth keeping and lost its caption alignment. Writing the car a
    third time to get the same words back is a coin toss, because the script
    is researched fresh every run; this takes the words that already worked
    and does everything downstream of them again.

    Read over HTTP, like audition_engines does: the output branch is 2.5GB
    of rendered video and is deliberately never cloned into a build.

    The narration is re-recorded, so the take will differ -- gpt-audio is not
    deterministic and has measured anywhere from 2.1 to 3.7 words a second on
    identical text. The words are the thing being reused, not the voice.
    """
    import os
    import urllib.request

    repository = repository or os.environ.get("GITHUB_REPOSITORY") or "Lukaa98/AI-horror-stories"
    url = (f"https://raw.githubusercontent.com/{repository}/{branch}"
           f"/cars/single-car-shorts/{build_id}/result.json")
    with urllib.request.urlopen(url, timeout=60) as response:
        manifest = json.loads(response.read().decode("utf-8"))
    scenes = manifest.get("scenes") or []
    if not scenes:
        raise SystemExit(f"{build_id} has no scenes in its result.json.")
    package = {
        "title": manifest.get("title") or "",
        "youtube_title": manifest.get("youtube_title") or "",
        "scenes": scenes,
        "history": manifest.get("history"),
        "key_specs": manifest.get("key_specs"),
        "sources": manifest.get("sources") or [],
    }
    package["script"] = " ".join(scene.get("narration") or "" for scene in scenes).strip()
    package["word_count"] = _word_count(package["script"])
    print(f"[single-car] Reusing the script from {build_id}: "
          f"{package['word_count']} words across {len(scenes)} scenes.")
    return package


def research_script(make, model, trim="", start_year=None, end_year=None, max_attempts=4, photo_hints=None, forced_rival=None, disable_comparison=False, listing_facts=None, market=None, angles=None, pasted_slots=(), style="classic", engine_clip=False):
    label = " ".join(value for value in [make, model, trim] if value).strip()
    year_scope = (
        f"model years {start_year}-{end_year}" if start_year and end_year
        else f"model year {start_year or end_year}" if start_year or end_year else "the best-known generation"
    )
    max_scenes = _scene_cap_for_photo_hints(photo_hints)
    chosen = style_of(style)
    target_low, target_high = chosen["words"]
    # Same margins the classic constants use, derived from the style's own
    # range rather than hard-coded: retry a little outside, fail nowhere.
    accept_low, accept_high = target_low - 10, target_high
    hard_low, hard_high = _hard_word_range(target_seconds=chosen["seconds"])
    package = None
    for attempt in range(1, max_attempts + 1):
        previous_violations = _script_violations(package, make, model, market, pasted_slots) if package else []
        violation_feedback = (
            " Your previous attempt also broke these rules, which are not negotiable -- fix every one: "
            + " ".join(previous_violations) if previous_violations else ""
        )
        # Four rewrites of "outside the target" produced 136 and 144 words.
        # Saying how many words short, and that the fix is more sentences
        # rather than longer ones, is a concrete instruction instead of a
        # verdict.
        shortfall = (target_low - package["word_count"]) if package else 0
        short_note = (
            f" You were {shortfall} words SHORT. Add roughly {shortfall} words as "
            f"{max(2, round(shortfall / 11))} more short sentences spread across the scenes you "
            "already have -- do NOT make the existing sentences longer, and do not repeat a "
            "number or a fact you have already given."
            if shortfall > 0 else ""
        )
        retry_feedback = (
            f" Your previous attempt came back at {package['word_count']} words, outside the "
            f"{target_low}-{target_high} target -- rewrite from scratch.{short_note}{violation_feedback} If you were short, research "
            f"and add a genuinely new beat (history, design story, a race win or record, a special edition, "
            f"or a mechanical/engineering detail like the suspension or rear-axle setup, steering system, or "
            f"chassis platform) rather than padding existing sentences or repeating what you already said -- "
            f"there is almost always more real, well-documented material available if you look for it." if package else ""
        )
        package = _request_script_package(
            _research_script_prompt(label, year_scope, retry_feedback, photo_hints, forced_rival,
                                    disable_comparison, max_scenes, listing_facts, market, angles, style,
                                    engine_clip),
            max_scenes=max_scenes,
        )
        package["_angles"] = angles or ""
        # Carried for the same reason as the notes: the note check enforces
        # fewer of them at 35 seconds, where there is no beat spare.
        package["_style"] = style or ""
        count = package["word_count"]
        violations = _script_violations(package, make, model, market, pasted_slots)
        if accept_low <= count <= accept_high and not violations:
            break
        problems = []
        if not accept_low <= count <= accept_high:
            problems.append(f"{count} words, outside the preferred {accept_low}-{accept_high} range")
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
    if not hard_low <= count <= hard_high:
        print(
            f"[single-car] Proceeding with {count} words even though it's outside the "
            f"{hard_low}-{hard_high} range atempo can fully correct for -- "
            "the video's pacing may be noticeably off."
        )
    elif not target_low <= count <= target_high:
        # This style's range, not the classic constants: a dense build used
        # to print "outside the preferred 144-154 range" on the same line its
        # retries called 145-165, which made the log contradict itself.
        print(
            f"[single-car] Proceeding with {count} words outside the preferred "
            f"{target_low}-{target_high} range; the floor pass below will try to close the gap."
        )
    repaired = _repair_closing(_repair_script(package, make, model), label)
    # Whatever the retries could not fix, fixed one rule at a time rather
    # than by rewriting the whole script again.
    # The cap is the style's, not the classic constant. Hard-coded to 154 it
    # made the dense target of 170-185 unreachable by construction: the trim
    # runs last, drops whole sentences and then whole scenes, and overshoots
    # downward -- which is how three dense builds landed at 136-144 from
    # scripts that may well have been the right length when written.
    # Both directions. The cap has always been enforced here; the floor was
    # only ever asked for in the prompt, which is why 18 of 29 builds shipped
    # under target. Floor first would be undone by the trim, so the cap runs
    # first and the floor is given it as its ceiling.
    final = _extend_to_floor(
        _enforce_word_cap(
            _repair_violations(repaired, make, model, market, label, pasted_slots=pasted_slots),
            cap=target_high,
        ),
        floor=target_low, cap=target_high, label=label, make=make, model=model,
        market=market, pasted_slots=pasted_slots,
    )
    # Shipping after four failed attempts is deliberate -- a build is worth
    # more than a perfect script -- but it was silent, so a script that broke
    # the rules looked exactly like one that kept them. The Mazdaspeed3 build
    # went out with no torque figure and a closing statement instead of a
    # question, and nothing downstream said so.
    # The narration is public twice over: it is spoken, and it is printed in
    # the caption band a word at a time. An em dash is one of the few marks
    # almost nobody types on a phone, so a feed of them reads as
    # machine-written whatever the words say. Done here, at the end, so the
    # captions and the voice are reading the same characters -- normalising
    # one and not the other is how they drift apart.
    final = _plain_dashes_everywhere(final)
    if _REPAIR_LOG:
        final["repair_log"] = list(_REPAIR_LOG)
        _REPAIR_LOG.clear()
    remaining = _script_violations(final, make, model, market, pasted_slots)
    if remaining:
        final["rule_violations"] = remaining
        print(f"[single-car] Shipping with {len(remaining)} rule(s) still broken after "
              f"{max_attempts} attempts: " + "; ".join(remaining))
    return final


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
        # A close-up is in the video either way -- it is placed as an insert
        # inside its slot's chapter, and 452 of 455 pasted photos across 27
        # builds reached the screen. The tick decides only whether the SCRIPT
        # is told what is in it, and a hint is a demand for a beat: nineteen
        # photos on the Ford GT build meant three of its seven beats went to a
        # rear wing, a gearshift and an air intake, and Enzo walking out of the
        # Ford deal never got said. Skipping the hint skips the vision call too,
        # so a close-up nobody narrates costs nothing to describe.
        if item.get("describe") is not True:
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
    # engine_clip is the one value here that is not a URL string. All 25
    # workflow inputs are spoken for -- GitHub's limit -- so it travels in
    # this object, and a filter that keeps only strings would drop it
    # without a word, which is the failure this function exists to prevent.
    kept = {key: str(value).strip() for key, value in parsed.items()
            if isinstance(value, str) and value.strip()}
    if isinstance(parsed.get("engine_clip"), dict):
        kept["engine_clip"] = parsed["engine_clip"]
    return kept


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


# The clip is somebody's phone video and the narration is a studio voice,
# so they arrive at wildly different levels and the clip is normalised
# rather than trusted. Not to the voice's level, though: a few dB hotter,
# because the hit is the whole point of putting it there. Against a voice
# sitting near -18 this reads as noticeably louder without being the kind
# of jump that sends somebody to the volume control.
ENGINE_CLIP_LUFS = -14.0


def engine_clip_seam(manifest, after_scene=ENGINE_CLIP_AFTER_SCENE,
                     latest=ENGINE_CLIP_LATEST_SECONDS):
    """When the start-up should interrupt, in seconds, or None.

    The end of scene `after_scene`, because that is the scene the script
    was told to end by handing over to the sound. Taken from the same
    boundaries the renderer timed its own captions against, so the cut
    lands in the gap after a finished sentence rather than inside a word.

    None when there is no usable seam: no timeline to measure against, or
    the scene ends later than anybody is still watching.
    """
    scenes = manifest.get("scenes") or []
    timeline = manifest.get("word_timeline") or []
    duration = float(manifest.get("duration_seconds") or 0)
    if not scenes or not timeline or duration <= 0 or len(scenes) <= after_scene:
        return None
    boundaries = _scene_time_boundaries(scenes, timeline, duration)
    if len(boundaries) < after_scene:
        return None
    at = float(boundaries[after_scene - 1][1])
    if not 0 < at < duration:
        return None
    if at > latest:
        # The script ran long and the seam fell outside the window the
        # clip exists to land in. Better no clip than one that plays to
        # people who left ten seconds ago.
        print(f"[single-car] Scene {after_scene} ends at {at:.1f}s, past the "
              f"{latest:.0f}s mark; skipping the engine clip.")
        return None
    return at


def open_engine_gap(audio_path, word_timeline, at, length):
    """Cut `length` seconds of silence into the narration at `at`.

    The gap is opened before the mouth timeline is built, which reads the
    audio file rather than the words -- so the narrator's mouth closes for
    the engine without being told to. Every word after the seam is moved
    along by the same amount, which keeps the captions, the scene
    boundaries and the drag-race windows on the syllables they belong to.

    Returns the shifted timeline, or None if the gap could not be opened
    and the narration is unchanged.
    """
    audio_path = Path(audio_path)
    gapped = audio_path.with_name(f"{audio_path.stem}-gap{audio_path.suffix}")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path),
             "-filter_complex",
             # All three branches forced to one format before the concat.
             # It joins whatever the inputs negotiate between them, and a
             # mono 24kHz silence against a stereo narration is a coin
             # toss that can come down on the silence -- which would ship
             # the whole narration downsampled to mono for the sake of
             # five quiet seconds.
             f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
             f"asplit=2[head][tail];"
             f"[head]atrim=0:{at:.3f},asetpts=PTS-STARTPTS[a1];"
             f"[tail]atrim=start={at:.3f},asetpts=PTS-STARTPTS[a2];"
             f"anullsrc=channel_layout=stereo:sample_rate=48000,"
             f"atrim=0:{length:.3f}[sil];"
             f"[a1][sil][a2]concat=n=3:v=0:a=1[out]",
             "-map", "[out]", str(gapped)],
            check=True, capture_output=True, text=True, timeout=300,
        )
    except Exception as error:  # noqa: BLE001 - a hook is not worth the build
        detail = getattr(error, "stderr", "") or str(error)
        print(f"[single-car] Could not open the engine gap ({str(detail)[-300:]}); "
              "the narration is unchanged.")
        return None
    if not gapped.exists() or gapped.stat().st_size == 0:
        return None
    gapped.replace(audio_path)
    shifted = []
    for word in word_timeline:
        start = float(word.get("start") or 0)
        if start >= at:
            shifted.append({**word, "start": start + length,
                            "end": float(word.get("end") or 0) + length})
        else:
            shifted.append(dict(word))
    return shifted


def fetch_engine_clip(clip, dest):
    """Pull the chosen seconds of the listing video down to a local file.

    Done before anything else is committed to, because the gap in the
    narration is opened before the render and cannot be taken back: if the
    fetch fails there, the film ships with five silent seconds over a still
    photo. Failing here instead costs nothing -- the narration is never
    touched and the build is the one it would have been without a clip.

    Returns (path, length_seconds), or None if there is nothing usable.
    """
    url = str((clip or {}).get("url") or "").strip()
    try:
        start = float((clip or {}).get("start"))
        end = float((clip or {}).get("end"))
    except (TypeError, ValueError):
        return None
    if not url or not (end > start >= 0):
        return None
    length = end - start
    dest = Path(dest)
    # Two ways of asking, because the first one came back silent.
    #
    # Seeking before -i is the cheap way: on an HLS manifest it skips
    # fetching everything before the cut instead of downloading it to throw
    # away. But a master playlist keeps its audio in a separate rendition,
    # and a seek taken at open time can leave that rendition unopened -- the
    # file then has a picture and no sound at all, and the overlay that
    # wants [1:a] cannot be built. Seeking on the output reads from the
    # start and decodes past it, which is slower and does not lose the
    # audio, so it is worth one go when the fast way comes back mute.
    attempts = (
        ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", url],
        ["ffmpeg", "-y", "-i", url, "-ss", f"{start:.3f}", "-t", f"{length:.3f}"],
    )
    for index, head in enumerate(attempts):
        try:
            subprocess.run(
                head + [
                    # Re-encoded rather than copied: a stream copy starts at
                    # the nearest keyframe, which on a listing video can be
                    # a second or two off the revs the times were read for.
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", str(dest)],
                check=True, capture_output=True, text=True, timeout=900,
            )
        except Exception as error:  # noqa: BLE001 - a hook is not worth the build
            detail = getattr(error, "stderr", "") or str(error)
            print(f"[single-car] Could not read the engine clip ({str(detail)[-400:]}); "
                  "the build carries on without it.")
            return None
        if not dest.exists() or dest.stat().st_size == 0:
            print("[single-car] The engine clip came back empty; the build carries on "
                  "without it.")
            return None
        if _has_audio(dest):
            break
        if index == 0:
            print("[single-car] The clip came back without an audio track; reading it "
                  "again from the start of the video.")
    else:
        # Checked here rather than found out by the overlay, which is three
        # steps and one render later -- by which point the gap for the clip
        # is already cut into the narration and the film is built around
        # five seconds of nothing. The whole point of the clip is the
        # sound, so no sound is no clip.
        print("[single-car] That video has no sound -- nothing to play. Either the "
              "listing uploaded it muted, or its audio is in a rendition ffmpeg "
              "could not open. The build carries on without a clip.")
        return None
    actual = _media_duration(dest)
    if actual and actual < length - 0.5:
        # Asking for 12s-17s of a video that ends at 14s gets two seconds,
        # and the gap has to match what there is to play, not what was typed.
        print(f"[single-car] The engine clip is {actual:.1f}s, not the {length:.1f}s asked for; "
              "using what is there.")
        length = actual
    print(f"[single-car] Engine clip: {length:.1f}s from {start:.1f}s of the listing video.")
    return dest, length


def _has_audio(path):
    """Whether a media file has an audio stream at all.

    Separate from "is it loud enough": a listing video uploaded muted and
    one whose audio rendition never opened both arrive here as a picture
    with no sound, and either way there is nothing to play.
    """
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            check=True, capture_output=True, text=True, timeout=120,
        )
        return "audio" in probe.stdout
    except Exception:  # noqa: BLE001
        # An unanswerable probe is not evidence of silence. The overlay
        # fails cleanly on a file with no audio and the gap gets closed
        # again, so guessing wrong here is recoverable.
        return True


def _media_duration(path):
    """Length of a media file in seconds, or None if ffprobe cannot say."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            check=True, capture_output=True, text=True, timeout=120,
        )
        return float(probe.stdout.strip())
    except Exception:  # noqa: BLE001 - the caller has a fallback
        return None


def close_engine_gap(video_path, at, length, size=CANVAS, fps=24):
    """Take the room for the clip back out of the finished film.

    The gap has to be opened before the render, so by the time anything can
    go wrong with the clip the film is already built around five seconds of
    nothing -- no voice, no picture, no engine. That is what shipped on the
    first real build: the clip came down without an audio track, the
    overlay could not be built, and the hole went out in the video.

    Fetching the clip first stops a *fetch* from doing that, and checking it
    has sound stops the known cause, but neither covers whatever fails
    next. So the gap is reversible: the film either side of it is joined
    back together and nobody can tell it was ever there.

    Returns the seconds removed, or 0.0 if the film is unchanged.
    """
    video_path = Path(video_path)
    closed = video_path.with_name(f"{video_path.stem}-closed{video_path.suffix}")
    width, height = size
    resume = at + length
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path),
             "-filter_complex",
             f"[0:v]scale={width}:{height},setsar=1,fps={fps},format=yuv420p,"
             f"split=2[before][after];"
             f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
             f"asplit=2[beforea][aftera];"
             f"[before]trim=0:{at:.3f},setpts=PTS-STARTPTS[v1];"
             f"[beforea]atrim=0:{at:.3f},asetpts=PTS-STARTPTS[a1];"
             f"[after]trim=start={resume:.3f},setpts=PTS-STARTPTS[v2];"
             f"[aftera]atrim=start={resume:.3f},asetpts=PTS-STARTPTS[a2];"
             f"[v1][a1][v2][a2]concat=n=2:v=1:a=1[v][a]",
             "-map", "[v]", "-map", "[a]",
             # Pinned for the same reason the overlay pins them: joining two
             # segments whose timebases differ let a 24fps film drift to 25.
             "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "48000", str(closed)],
            check=True, capture_output=True, text=True, timeout=900,
        )
    except Exception as error:  # noqa: BLE001
        detail = getattr(error, "stderr", "") or str(error)
        print(f"[single-car] WARNING: could not close the engine gap "
              f"({str(detail)[-300:]}). The video has {length:.1f}s of silence at "
              f"{at:.1f}s with nothing playing over it -- do not upload it as it is.")
        return 0.0
    if not closed.exists() or closed.stat().st_size == 0:
        print(f"[single-car] WARNING: closing the engine gap came back empty. The video "
              f"has {length:.1f}s of silence at {at:.1f}s -- do not upload it as it is.")
        return 0.0
    closed.replace(video_path)
    print(f"[single-car] Took the {length:.1f}s engine gap back out at {at:.1f}s; "
          "the video is the one it would have been without a clip.")
    return length


def overlay_engine_clip(video_path, clip_path, at, length, size=CANVAS, fps=24):
    """Play the start-up inside the photo frame, not over the whole screen.

    The clip goes where the photos go -- the same inset box the renderer
    fits car pictures into -- so the headline, the spec table and the
    narrator stay on screen while the car revs. Cutting to the clip full
    frame hid all three, which is the one thing the layout exists to keep
    visible.

    It needs no room made for it: the gap was already opened in the
    narration before the render (see open_engine_gap), so the stretch this
    overlays is a stretch where the narrator has stopped talking and the
    box is holding a still photo. Nothing is said over the engine and
    nothing moves out of sync, because the film's own length never changes.

    Fitted whole inside the box rather than cropped to fill it: a centre
    crop of a vertical phone clip is as likely as not to cut off the
    exhaust the start-up was filmed for. What fills the rest of the box is
    a blurred, zoomed copy of the clip itself -- the Shorts treatment,
    which reads as deliberate where white bars read as a mistake, and
    which keeps the box as full as a photo leaves it.

    Returns True if the clip is in the film, False if the film is as it
    was rendered.
    """
    video_path = Path(video_path)
    joined = video_path.with_name(f"{video_path.stem}-engine{video_path.suffix}")
    _, media_box, _ = _media_zone_geometry(size)
    box_x, box_y, box_w, box_h = (int(round(v)) for v in media_box)
    until = at + length
    delay_ms = int(round(at * 1000))
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-i", str(clip_path),
             "-filter_complex",
             # Two copies of the clip: one zoomed past the box and cropped
             # to fill it, blurred hard enough to carry no detail, and the
             # whole frame fitted on top of that. Whatever shape the clip
             # was shot in, the box ends up full and nothing is cut.
             f"[1:v]split=2[back][front];"
             f"[back]scale={box_w}:{box_h}:force_original_aspect_ratio=increase,"
             f"crop={box_w}:{box_h},gblur=sigma=24[backdrop];"
             f"[front]scale={box_w}:{box_h}:force_original_aspect_ratio=decrease[fitted];"
             f"[backdrop][fitted]overlay=x=(W-w)/2:y=(H-h)/2,setsar=1,fps={fps},"
             # The clip's own timestamps start at zero. tpad holds frames
             # ahead of it so overlay always has something to composite --
             # without it the filter stalls waiting on a stream that does
             # not start until the seam. The padded frames are never drawn:
             # `enable` is false until `at`.
             f"tpad=start_duration={at:.3f}[clip];"
             f"[0:v]fps={fps}[film];"
             f"[film][clip]overlay=x={box_x}:y={box_y}:"
             f"enable='between(t,{at:.3f},{until:.3f})':eof_action=pass,"
             f"format=yuv420p[v];"
             # A phone recording of an engine and a studio voice arrive at
             # completely different levels, so both are brought to the same
             # loudness rather than either being trusted -- the clip a
             # little hotter than the voice, because the hit is the point.
             # The second aformat is not a duplicate: loudnorm resamples to
             # 192kHz internally and hands that on, so without it the mix
             # inherits 192k and the file comes out as 96kHz AAC.
             # Trimmed to the length the gap was opened for, not the length
             # the file happens to be. A seek into an HLS manifest does not
             # land exactly, so a five-second ask can come back as 5.2 --
             # and where the picture is bounded by `enable`, the sound is
             # not: the overrun would play on top of the narration starting
             # again.
             f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
             f"atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
             f"loudnorm=I={ENGINE_CLIP_LUFS}:TP=-2:LRA=11,"
             f"aformat=sample_rates=48000:channel_layouts=stereo,"
             f"adelay={delay_ms}|{delay_ms}[clipa];"
             f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo[filma];"
             # Summed, not averaged: normalize=1 would halve the narration
             # for the whole film to make room for five seconds of engine.
             # The narration is silent across the gap, so the sum there is
             # the clip alone.
             f"[filma][clipa]amix=inputs=2:duration=first:"
             f"dropout_transition=0:normalize=0[a]",
             "-map", "[v]", "-map", "[a]",
             "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "48000", str(joined)],
            check=True, capture_output=True, text=True, timeout=900,
        )
    except Exception as error:  # noqa: BLE001 - a hook is not worth the build
        detail = getattr(error, "stderr", "") or str(error)
        print(f"[single-car] Could not lay the engine clip into the frame "
              f"({str(detail)[-400:]}); the video goes out as it was rendered.")
        return False
    if not joined.exists() or joined.stat().st_size == 0:
        print("[single-car] The overlaid file came back empty; the video is unchanged.")
        return False
    joined.replace(video_path)
    print(f"[single-car] Engine clip laid into the photo box at {at:.1f}s for {length:.1f}s, "
          f"after scene {ENGINE_CLIP_AFTER_SCENE}.")
    return True


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


# Silence to leave after the last word is spoken. Measured across 25 shipped
# builds the gap was whatever gpt-audio happened to leave -- 0.16s to 0.83s,
# median 0.30 -- and the E63 build at 0.16s ends while the closing question's
# last syllable is still decaying, which is what "the ending isn't good"
# sounds like. A closing question needs the beat after it to land.
MIN_TAIL_SILENCE = 0.45


# Script left unspoken, in characters, before a build counts as cut off.
# Roughly five words. Below that the difference is whisper hearing
# "send-off" as "send off", not audio going missing.
TRUNCATION_CHARS = 25
# Reads of the same script before a short one is shipped anyway. Three,
# because the cut-off is non-deterministic and independent: at one in ten,
# a single retake leaves about one build in a hundred still short and two
# leaves one in a thousand.
MAX_NARRATION_TAKES = 3


def unspoken_tail(script, word_timeline):
    """How much of the script the narration never reached, in characters.

    Counting words does not work. Whisper splits hyphenated words and
    numbers, so it usually returns MORE entries than the script has words:
    across 49 builds the ratio ran from 0.95 to 1.12 with no relation to
    whether anything was missing, and the old "under 95% of the words"
    rule caught none of the real cases. The Viper ACR stopped after
    "...your style" and never said "or would you opt for electric
    assists", at a ratio of 1.00.

    So both texts are stripped to bare characters and aligned. Whatever is
    left of the script after the last thing the audio matched is the part
    that was never said, and tokenisation stops mattering.
    """
    def bare(words):
        return "".join(re.sub(r"[^a-z0-9]", "", str(w).lower()) for w in words)

    written = bare(str(script or "").split())
    spoken = bare(w.get("word") or "" for w in (word_timeline or []))
    if not written or not spoken:
        return 0
    blocks = [b for b in difflib.SequenceMatcher(None, written, spoken, autojunk=False)
              .get_matching_blocks() if b.size > 8]
    if not blocks:
        return len(written)
    return max(0, len(written) - (blocks[-1].a + blocks[-1].size))


def pad_tail_silence(audio_path, last_word_end, minimum=MIN_TAIL_SILENCE):
    """Make sure the voice has room to finish, and return the new duration.

    This runs AFTER the tempo normalisation on purpose: atempo scales the
    trailing silence along with the speech, so padding first would have the
    pad squeezed by however much the script needed compressing. Padding
    after means the gap is the gap.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(audio_path)],
        check=True, capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    if not last_word_end or last_word_end <= 0:
        return duration
    shortfall = minimum - (duration - last_word_end)
    if shortfall <= 0.01:
        return duration
    padded = audio_path.with_name(f"{audio_path.stem}-padded{audio_path.suffix}")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path), "-af", f"apad=pad_dur={shortfall:.3f}",
         str(padded)],
        check=True, capture_output=True, text=True,
    )
    padded.replace(audio_path)
    print(f"[single-car] Padded {shortfall:.2f}s of silence after the last word, so the "
          f"closing question has room to land ({duration:.2f}s -> {duration + shortfall:.2f}s).")
    return duration + shortfall


def normalize_audio_duration(audio_path, target=TARGET_DURATION_SECONDS, minimum=52.0, maximum=58.0):
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


ALIGNMENT_ATTEMPTS = 3


def transcribe_word_timeline(audio_path):
    """Get real per-word timestamps for captions; rendering has a safe fallback.

    An empty alignment is a failure wearing a success's clothes. The call
    returns 200 with no per-word timings, nothing raises, and the renderer
    quietly falls back to splitting the runtime evenly across the scenes --
    which has no relation to how long each one takes to say, so captions,
    headlines and photo changes all drift against the voice. Run
    #37340574003 shipped exactly that way and looked perfectly healthy: 152
    words, no violations, nothing in the log, and a visibly out-of-sync
    video. with_openai_retry only retries rate limits, so an empty body is
    retried here instead, and a run of empties is raised rather than
    returned -- the caller's fallback should be a decision somebody can see
    in the log, not the default.
    """
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
        for attempt in range(1, ALIGNMENT_ATTEMPTS + 1):
            result = with_openai_retry(request_alignment)
            words = []
            for item in getattr(result, "words", None) or []:
                if isinstance(item, dict):
                    word, start, end = item.get("word"), item.get("start"), item.get("end")
                else:
                    word = getattr(item, "word", None)
                    start = getattr(item, "start", None)
                    end = getattr(item, "end", None)
                if word and start is not None and end is not None:
                    words.append({"word": str(word).strip(), "start": float(start), "end": float(end)})
            if words:
                return words
            print(f"[single-car] Alignment {attempt}/{ALIGNMENT_ATTEMPTS} came back with no word "
                  "timings; asking again.")
    raise ValueError(
        f"whisper returned no word timings in {ALIGNMENT_ATTEMPTS} attempts -- captions would be "
        "spaced evenly instead of matched to the voice"
    )


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
    # Read here rather than at the join, because the script has to know a
    # start-up interrupts it: scene 2 ends by handing over to the sound.
    engine_clip = photos.get("engine_clip") if isinstance(photos, dict) else None
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
    # Three sources, in order of how much they know about this car.
    #
    # A price the person typed outranks everything: they know the car, and
    # four characters in a form settle an argument that a scraper and a
    # search engine between them got wrong twice.
    #
    # Then a finished auction, which is one real dated transaction for the
    # exact car on screen. It can sit some way from the model's general
    # market -- but it is what somebody paid for this one.
    #
    # Then comparable sales, for a car still under the hammer, where no
    # price exists yet and the model's own results are the best available
    # answer to "what does it cost".
    market = value_from_input(args.current_price)
    if market:
        print(f"[single-car] Market: ${market['median']:,}, as stated on the build form.")
    if market is None and listing_facts:
        market = value_from_sale(listing_facts.get("price_text"),
                                 listing_facts.get("auction_state"))
        if market:
            print(f"[single-car] Market: ${market['median']:,}, what this car sold for.")
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
    package = script_package_from_build(args.reuse_script) if args.reuse_script else research_script(
        args.make, args.model, args.trim, args.start_year, args.end_year,
        photo_hints=photo_hints, forced_rival=forced_rival, disable_comparison=args.disable_comparison,
        listing_facts=listing_facts,
        market=market,
        angles=args.angles,
        pasted_slots=tuple(slot for slot in MANUAL_PHOTO_FIELDS if manual_photo_urls.get(slot)),
        style=args.style,
        engine_clip=bool(engine_clip),
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
    # The dense style runs longer on purpose: 180 words at gpt-audio's ~2.5
    # a second is 72 seconds raw, and squeezing that into 55 would need
    # 1.3x. Normalising to 63 asks for 1.15x, which is what the classic
    # build already uses.
    chosen_style = style_of(args.style)
    normalized_duration = normalize_audio_duration(
        audio_path, target=chosen_style["seconds"],
        minimum=chosen_style["window"][0], maximum=chosen_style["window"][1])
    voice_auditions = (
        generate_voice_auditions(package["script"], output_dir, args.voice) if args.audition_voices else {}
    )
    try:
        word_timeline = transcribe_word_timeline(audio_path)
    except Exception as exc:
        print(f"[single-car] Word alignment failed; falling back to estimated caption timing: {exc}")
        word_timeline = []
    # Run #182 stopped at "...not just a muscle car", leaving "it's a
    # statement" unspoken, and the Viper ACR stopped at "...your style"
    # without asking the rest of its closing question. The transcript is the
    # only evidence of what was actually said, so it is aligned against what
    # was meant to be said -- see unspoken_tail, and note that counting
    # words cannot do this.
    #
    # gpt-audio cutting off is not deterministic: the same script read again
    # usually finishes. So a short take is re-read rather than shipped, which
    # is worth a call in a way that re-researching the script would not be --
    # it is about one build in ten, 5 of 50 stopped more than five words
    # short, and what gets lost is the closing question.
    unspoken = unspoken_tail(package["script"], word_timeline) if word_timeline else 0
    for take in range(2, MAX_NARRATION_TAKES + 1):
        if unspoken <= TRUNCATION_CHARS:
            break
        print(f"[single-car] The narration stopped about {unspoken} characters before the end of "
              f"the script; take {take} of {MAX_NARRATION_TAKES}.")
        try:
            synthesize_narration(package["script"], audio_path, preset=args.voice,
                                 speed=FAST_TTS_SPEED)
            add_narration_lead_in(audio_path)
            normalized_duration = normalize_audio_duration(
                audio_path, target=chosen_style["seconds"],
                minimum=chosen_style["window"][0], maximum=chosen_style["window"][1])
            word_timeline = transcribe_word_timeline(audio_path)
            unspoken = unspoken_tail(package["script"], word_timeline)
        except Exception as exc:  # noqa: BLE001 - a build is worth more than a retake
            print(f"[single-car] Take {take} failed ({exc}); keeping the one before it.")
            break
    if unspoken > TRUNCATION_CHARS:
        # Recorded, not just printed: a closing question that never gets
        # asked is what a viewer notices, and nothing else reports it.
        print(
            f"[single-car] WARNING: the narration still stops about {unspoken} characters short. "
            f"The last words spoken were "
            f"\"{' '.join(str(w.get('word')) for w in word_timeline[-6:])}\", against a script "
            f"ending \"{' '.join(package['script'].split()[-8:])}\"."
        )
        package["narration_unspoken_chars"] = unspoken
    # After the alignment, because the pad needs to know where the last word
    # actually ends, and before the mouth timeline, which is built off the
    # file and would otherwise stop short of the padded end.
    if word_timeline:
        normalized_duration = pad_tail_silence(
            audio_path, float(word_timeline[-1].get("end") or 0))
    # The room for the start-up, made before anything is rendered. The clip
    # plays inside the photo box rather than over the whole frame, so the
    # film has to be running underneath it -- which means the quiet it
    # needs has to be in the narration, not cut into the finished mp4.
    #
    # Fetched first: the gap cannot be taken back once the film is rendered
    # around it, and a clip that turns out to be unreadable would leave
    # five silent seconds over a still photo.
    engine_clip_path = None
    engine_clip_at = None
    engine_clip_length = 0.0
    if engine_clip:
        fetched = fetch_engine_clip(engine_clip, output_dir / "engine_clip.mp4")
        if fetched:
            engine_clip_path, engine_clip_length = fetched
            engine_clip_at = engine_clip_seam({
                "scenes": package["scenes"],
                "word_timeline": word_timeline,
                "duration_seconds": normalized_duration,
            })
        if engine_clip_at is not None:
            shifted = open_engine_gap(audio_path, word_timeline,
                                      engine_clip_at, engine_clip_length)
            if shifted is None:
                engine_clip_at = None
            else:
                word_timeline = shifted
        if engine_clip_at is None:
            engine_clip_path = None
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
            "script_style": args.style,
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
            # What the script was asked to cover, and what the car was said
            # to be worth. Both were left out, so the create form read them
            # back as empty however they were set -- and the build that
            # proved the angles feature worked could not say it had been
            # used: the trinity beat was in the narration while the manifest
            # read as though nobody had asked for it.
            "angles": args.angles or "",
            "current_price": args.current_price or "",
            "extra_photos": args.extra_photos or "",
            # The one value here that is not a string, and the one that was
            # missing: a build that used a start-up clip came back from the
            # "Start from a previous build" dropdown with the box unticked,
            # the video unchosen and the times blank, so rebuilding it
            # silently dropped the clip the original had.
            "engine_clip": engine_clip or None,
        },
        **{k: v for k, v in package.items() if k not in ("_angles", "_style")},
        "voice_preset": args.voice,
        "tts_speed": FAST_TTS_SPEED if narration_settings()["engine"] == "tts" else None,
        "narration": narration_settings(),
        "target_duration_seconds": chosen_style["seconds"],
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
    # The start-up, laid into the photo box over the gap the narration
    # already has. Last, and on the rendered file, so the film underneath
    # it is exactly what the renderer timed -- nothing shifts, and the
    # headline, the spec table and the narrator stay on screen through it.
    if engine_clip_path and engine_clip_at is not None:
        if overlay_engine_clip(video_path, engine_clip_path, engine_clip_at,
                               engine_clip_length):
            # Recorded so a build that shipped with a clip can be told apart
            # from one that asked for a clip and did not get it -- the
            # overlay is best-effort and otherwise says so only in a log
            # nobody reads later.
            manifest["engine_clip"] = {**engine_clip,
                                       "at_seconds": round(engine_clip_at, 3),
                                       "played_seconds": round(engine_clip_length, 3)}
        else:
            # The room was made before the render, so a failure here leaves
            # the film holding five seconds of nothing. Taking it back out
            # is the difference between a build without a clip and a build
            # with a hole in it -- which is what went out the first time.
            removed = close_engine_gap(video_path, engine_clip_at, engine_clip_length)
            manifest["duration_seconds"] = round(
                float(manifest.get("duration_seconds") or 0) - removed, 3)
            manifest["engine_clip_failed"] = {
                **engine_clip, "at_seconds": round(engine_clip_at, 3),
                "gap_closed": bool(removed)}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        engine_clip_path.unlink(missing_ok=True)
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
        "--angles", default=None,
        help="Things to cover, in your own words, one per line. Each becomes a beat and "
             "replaces one the model would otherwise have written -- the video does not get "
             "longer. A claim that is wrong is corrected rather than repeated.",
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
    parser.add_argument(
        "--style", default="classic", choices=sorted(SCRIPT_STYLES),
        help="How the script is written. \"classic\" is everything shipped so far. \"dense\" is "
             "shorter sentences, more of them, one argument rather than a tour -- about 180 "
             "words over 63 seconds.")
    parser.add_argument(
        "--reuse-script", default=None,
        help="A previous build id. Takes that build's scenes verbatim instead of researching a "
             "new script, and does everything after them again -- narration, alignment, render. "
             "The take will differ; the words will not.")
    parser.add_argument("--no-audition-voices", dest="audition_voices", action="store_false")
    args = parser.parse_args()
    print(json.dumps(build_short(args), indent=2))


if __name__ == "__main__":
    main()
