"""Turn a free-text request ("ranking video of Corvettes", "Mustang generations
2015-2024") into a research.json draft: an AI research pass (OpenAI Responses
API with the hosted web_search tool, so facts are grounded/cited, not just
model-recalled) plus best-effort image sourcing from Cars & Bids first and
Wikimedia Commons as fallback. Factual research is deliberately independent
from image availability, so cars with weak US-auction coverage are not omitted.

Writes cars/drafts/<draft-id>/research.json and cars/drafts/<draft-id>/images/.
Does NOT render a video -- that's generate_from_research.py, a separate stage,
so a human can review facts/photos before committing to a render.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from PIL import Image
from cars_and_bids import (
    discover_entry_engine_videos,
    enrich_entry_from_manifest,
    infer_search_params,
    scrape_entry_images,
)
from engine_video import prepare_engine_clip
from openai_retry import with_openai_retry
from plate_blur import blur_license_plates

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
SCRAPER_DIR = ROOT / "scraper" / "car-source-scraper"
DRAFTS_ROOT = ROOT / "cars" / "drafts"

RESEARCH_PROMPT_TEMPLATE = """You are researching content for a short vertical "ranking" video about cars.

User request: "{request}"

Use web search to find up to 8 ranked candidate
entries based on the request scope. We will keep the highest-ranked candidates that
also have usable image coverage, so your ranking order matters. If the lineup only
naturally supports 4 strong candidates, returning 4 is acceptable.

The request may explicitly ask for one of two workflows:
1. best generations overall across the full production run of a model line
2. best versions within a specific year range, generation, or chassis focus
Honor that scope strictly when choosing the 4 entries.

If the request is overall / across the full production run / all generations:
- treat this as a GENERATION ranking, not a trim ranking
- choose 4 different generations or chassis families from across the model's history
- use exactly one representative version for each chosen generation
- do NOT let two entries come from the same generation unless the user explicitly asked for that
- example for Corvette: C1, C4, C6, C8 is valid; Stingray, Z06, ZR1, ZR1X all from C8 is NOT valid
- if the model line has fewer than 4 true generations, fall back to 4 era-defining versions across the full production run
- in that fallback case, maximize generation diversity first, then use major facelifts, flagship trims, or historically important versions
- if you use that fallback, make the order_rationale explicitly say that the model has fewer than 4 true generations

If the request is focused / in a range / generation-specific:
- treat this as a VARIANT ranking within that constrained scope
- all 4 entries may come from the same generation if that is what the request implies
- example for Corvette C8: Stingray, E-Ray, Z06, ZR1 is valid

Choose and rank candidates on factual merit within the user's requested scope.
Do NOT prefer or exclude a candidate merely because it appears (or does not appear)
on Cars & Bids, a US auction site, or Wikimedia Commons. European-market, Japanese-
market, homologation, low-volume, and older cars must receive equal consideration.
Image availability is evaluated later by a separate pipeline.

RANKING POLICY:
- "Best" does not mean newest, most powerful, most expensive, or chronological.
- Score every candidate from 0-10 in each category:
  enthusiast_desirability, driving_engagement, historical_significance,
  performance, collectibility, and value.
- Treat attributes such as a gated manual, naturally aspirated engine, low production,
  homologation significance, distinctive design, critical acclaim, and sustained
  enthusiast demand as meaningful evidence—not decorative trivia.
- Use this weighting: enthusiast desirability 25%, driving engagement 20%,
  historical significance 15%, performance 15%, collectibility 15%, value 10%.
- Recency has no independent score. A newer car wins only when its merits earn it.
- Do not rank chronologically unless the user explicitly requests chronological order.
- Use auction results as market evidence, not as the entire definition of "best."
- Sort the returned candidates from lowest weighted score to highest weighted score.
- The order_rationale must state the decisive enthusiast criteria and must never merely
  say "oldest to newest," "earliest to latest," or "by model year" for a best ranking.

RESEARCH SOURCE POLICY:
- Use broad web search; do not treat Cars & Bids as the default factual authority.
- For specifications, dates, trims, and original pricing, prefer primary sources:
  manufacturer press rooms, heritage archives, brochures, homologation documents,
  owner's manuals, and reputable auction-house documentation.
- For history, reputation, and market context, corroborate with established
  automotive sources such as Hagerty, marque clubs/registries, major automotive
  publications, and reputable auction results.
- Wikipedia may help discover terminology and references, but should not be the
  sole source for a factual claim when a primary or specialist source is available.
- Cars & Bids, Bring a Trailer, Collecting Cars, and similar listings may support
  condition-specific sale/value claims, but a single listing is not authoritative
  for production history or specifications.
- Cross-check important numbers. Use at least two independent source URLs per entry
  when possible, including at least one primary or specialist source.
- Never invent a URL, price, horsepower figure, production year, or citation.

IMPORTANT:
- each entry still needs a clearly photographable subject
- for overall generation rankings, prefer names that combine generation plus representative trim when needed,
  such as "C6 Z06", "C4 ZR-1", "First-Gen R8 V8", or "997 GT3 RS"
- for focused rankings, each entry must be a distinctly NAMED model/trim that a photographer would
  tag as its own subject and that has its own dedicated Wikimedia Commons category --
  e.g. "Stingray", "Z06", "ZR1", "E-Ray" are good; internal option-package codes like
  "1LT", "2LT", "3LZ", "1LZ" are BAD (nobody photographs "a 2LT", they photograph "a Z06")
- if the request doesn't obviously split into 4 named variants, pick the 4 most
  distinct/well-known ones rather than an internal trim-code breakdown
- do not stretch a model's history to force 4 distinct generations when only 1-2 true
  generations actually exist. A rare badge-engineered rebadge, an obscure gap-era
  import, or a name reused for an unrelated car (e.g. a captive import sold under the
  same nameplate during a production gap) will have essentially no real-world auction
  or enthusiast photo coverage even though it technically existed -- prefer widely
  recognized era-defining trims/years within the real generation(s) instead
- provide at least 6 candidates (aim for 8), not just 4 -- image sourcing later drops
  any candidate with too few verified photos, and without extra candidates beyond the
  final 4 there is no backup when one turns out to be poorly photographed

For each entry, give:
- name: short identifier (e.g. "NA", "Z06", "2018")
- years: production year range or single year as a string
- introduced_year: the year this exact generation/variant was introduced
- price_usd: its ORIGINAL starting MSRP in its introduction year, in USD as a number, or null only if unavailable
- horsepower: a representative horsepower number, or null if not applicable
- label: a short (2-4 word) factual or reputation-based descriptor, e.g. "MOST UNLOVED" or "ENTRY POINT"
- one_line_fact: energetic spoken narration of 16-24 words. It MUST naturally mention the introduction year,
  original starting price, horsepower, and one meaningful enthusiast detail. Keep it to one punchy sentence.
  Write like a knowledgeable car-club friend, not a brochure or AI summary. Use contractions and varied transitions.
  The first entry should open with an enthusiastic ranking hook; later entries should flow with phrases such as
  "Then," "Next," and "At number one." Do not use Markdown, emoji, headings, or stage directions because this text
  goes directly to text-to-speech.
- search_hint: a short phrase to search Wikimedia Commons for photos of this specific thing
  (e.g. "Ford Mustang III GT", "Chevrolet Corvette Z06 C8")
- chassis_code: the recognized platform/generation code when one exists, such as
  "Type 42", "Type 4S", "997", "C6", or "E46"; otherwise null
- commons_search_terms: 2-4 precise Wikimedia-oriented searches combining make,
  model, chassis code, generation, trim, body style, and facelift era as applicable
- visual_highlight: the most interesting model-specific visual detail to show, such as "quad exhaust",
  "interior dashboard", "engine bay", or "rear light design"
- visual_identifiers: 3-6 visible features that distinguish this exact generation/variant
  from the other ranked entries, such as headlight shape, side-blade design, exhaust
  layout, grille, badge, dashboard, engine cover, or facelift bodywork
- engine_nickname: the widely-recognized enthusiast or manufacturer nickname for this
  specific engine, if one genuinely exists, e.g. "Hemi", "Coyote", "LS7", "Boxer",
  "Flat-plane V8", "2JZ"; use null if this engine has no popular nickname -- do not invent one
- ranking_scores: an object containing 0-10 numeric scores for enthusiast_desirability,
  driving_engagement, historical_significance, performance, collectibility, and value
- ranking_case: one concise sentence explaining the evidence behind this candidate's
  score and what could make an enthusiast choose it over newer or faster alternatives
- research_sources: 2-5 pages that directly support this entry's facts. Each item must include:
  - url: the exact page URL returned by web search
  - title: concise page/source title
  - publisher: organization or site name
  - source_type: one of "manufacturer", "heritage_archive", "specialist",
    "automotive_publication", "auction_result", or "reference"
  - supports: a short list using only these values when applicable:
    "years", "introduced_year", "price_usd", "horsepower", "history",
    "reputation", "market_value"

Also give:
- title: a short ALL-CAPS-worthy video title, e.g. "RANKING EVERY CORVETTE GENERATION"
- highlight_word: the single word in the title that should be color-highlighted (usually the car model name)
- close_narration: an enthusiastic conversational choice question (max 10 words) naming the relevant lineup,
  e.g. "So, which C5 are you taking home: Coupe, Convertible, FRC, or Z06?"
- order_rationale: one sentence explaining why you ordered the 4 entries this way (worst-to-best, cheapest-to-priciest, etc)

Order the candidates from what you determine is position 8/7/etc. up to position 1, so the
best candidate is last. The final video will keep the highest-ranked 4 candidates that
have usable image coverage.

Return ONLY strict JSON, no markdown fences, no prose outside the JSON, matching:
{{
  "title": "string",
  "highlight_word": "string",
  "close_narration": "string",
  "order_rationale": "string",
  "entries": [
    {{"name": "string", "years": "string", "introduced_year": number, "price_usd": number_or_null, "horsepower": number_or_null,
      "label": "string", "one_line_fact": "string", "search_hint": "string",
      "chassis_code": "string_or_null", "commons_search_terms": ["string"],
      "visual_highlight": "string",
      "visual_identifiers": ["string"],
      "engine_nickname": "string_or_null",
      "ranking_scores": {{
        "enthusiast_desirability": number, "driving_engagement": number,
        "historical_significance": number, "performance": number,
        "collectibility": number, "value": number
      }},
      "ranking_case": "string",
      "research_sources": [
        {{"url": "https://...", "title": "string", "publisher": "string",
          "source_type": "manufacturer|heritage_archive|specialist|automotive_publication|auction_result|reference",
          "supports": ["years", "horsepower"]}}
      ]}}
  ]
}}
Return at least 6 candidates, ideally 8, so weakly-photographed candidates can be
skipped later in favor of better-covered ones."""

NARRATION_PROMPT_TEMPLATE = """Write the final spoken narration for a short car-ranking video.

User request:
{request}

Final ranked entries are supplied below in countdown order from #4 to #1:
{entries_json}

Write like a knowledgeable, opinionated car-club friend—not a specification sheet,
press release, or generic AI summary.

STRUCTURE AND VOICE:
- The first spoken words must immediately identify the video subject, for example
  "Let's rank every Porsche Boxster generation." Then flow naturally into
  "At number four..." Do not begin with a generic statement that could describe any car.
- #3 must advance the story with a varied transition such as "Then..." or
  "[Manufacturer] turned up the volume."
- #2 should explain the major evolution and may contrast what improved with what was lost.
- #1 must clearly explain why it wins using character, experience, or significance,
  rather than declaring it best only because it has the largest number.
- End with a conversational choice question naming recognizable versions from the lineup.
- Use contractions and natural connective language. Mild enthusiast opinion is encouraged
  when framed as opinion.

FACT AND PRICE RULES:
- Preserve the supplied years, horsepower, original MSRP, and current value exactly.
- Never invent or silently alter a number.
- Mention every entry's original price naturally.
- Mention a current value only when current_value_display is present.
- Connect price to meaning: entry point, expensive upgrade, appreciating collectible,
  depreciation, or value—not four repetitions of "Today, examples trade around..."
- Use at most three numerical facts in any one entry.
- Facts visible on screen do not all need to be spoken.

STYLE RULES:
- Give each entry roughly 28-42 spoken words, except #4 may be slightly longer for the hook.
- Keep the complete script around 140-180 words.
- Vary sentence openings and rhythm.
- Include at least one memorable mechanical, visual, historical, or driving detail per entry.
- Do not repeat "packed," "delivered," "boasting," or "Today, clean examples trade around."
- Do not use Markdown, headings, stage directions, quotation marks, or emoji.
- Divide each paragraph into 2-4 natural performance beats. Let meaning determine the
  delivery: reveals can be energetic, context can be conversational, contrasts can be
  intrigued, and verdicts can be confident. Do not make every beat energetic.
- Set pause_after to 0. The production narrator now reads one continuous script; performance
  beats exist for visual synchronization and must not divide grammatical sentences.
- Choose at most two emphasis_words per beat. These are words the narrator should stress
  or slightly sustain naturally, such as "legendary", "manual", or "rear-wheel drive".
  Never alter their spelling in the spoken text.
- Give every beat the closest visual_cue so its matching photo can appear while it is spoken.

Return ONLY strict JSON:
{{
  "entries": [
    {{
      "name": "exact supplied entry name",
      "narration": "the exact spoken paragraph formed by the beats",
      "performance_beats": [
        {{
          "text": "natural spoken phrase or sentence",
          "style": "energetic_reveal|conversational|intrigued|confident|reflective",
          "emphasis_words": ["zero to two words or short phrases copied from text"],
          "pause_after": 0.25,
          "visual_cue": "engine|wheel|interior|rear|front|side|exterior"
        }}
      ]
    }}
  ],
  "close_narration": "spoken closing question"
}}
"""

NARRATION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entries", "close_narration"],
    "properties": {
        "entries": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "narration", "performance_beats"],
                "properties": {
                    "name": {"type": "string"},
                    "narration": {"type": "string"},
                    "performance_beats": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "text", "style", "emphasis_words",
                                "pause_after", "visual_cue",
                            ],
                            "properties": {
                                "text": {"type": "string"},
                                "style": {
                                    "type": "string",
                                    "enum": [
                                        "energetic_reveal", "conversational",
                                        "intrigued", "confident", "reflective",
                                    ],
                                },
                                "emphasis_words": {
                                    "type": "array",
                                    "minItems": 0,
                                    "maxItems": 2,
                                    "items": {"type": "string"},
                                },
                                "pause_after": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 0,
                                },
                                "visual_cue": {
                                    "type": "string",
                                    "enum": [
                                        "engine", "wheel", "interior", "rear",
                                        "front", "side", "exterior",
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        },
        "close_narration": {"type": "string"},
    },
}


def slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "draft"


RANKING_WEIGHTS = {
    "enthusiast_desirability": 0.25,
    "driving_engagement": 0.20,
    "historical_significance": 0.15,
    "performance": 0.15,
    "collectibility": 0.15,
    "value": 0.10,
}

RESEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "highlight_word",
        "close_narration",
        "order_rationale",
        "entries",
    ],
    "properties": {
        "title": {"type": "string"},
        "highlight_word": {"type": "string"},
        "close_narration": {"type": "string"},
        "order_rationale": {"type": "string"},
        "entries": {
            "type": "array",
            "minItems": 6,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name", "years", "introduced_year", "price_usd", "horsepower",
                    "label", "one_line_fact", "search_hint", "chassis_code",
                    "commons_search_terms", "visual_highlight", "visual_identifiers",
                    "engine_nickname", "ranking_scores", "ranking_case", "research_sources",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "years": {"type": "string"},
                    "introduced_year": {"type": "number"},
                    "price_usd": {"type": ["number", "null"]},
                    "horsepower": {"type": ["number", "null"]},
                    "label": {"type": "string"},
                    "one_line_fact": {"type": "string"},
                    "search_hint": {"type": "string"},
                    "chassis_code": {"type": ["string", "null"]},
                    "commons_search_terms": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {"type": "string"},
                    },
                    "visual_highlight": {"type": "string"},
                    "visual_identifiers": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 6,
                        "items": {"type": "string"},
                    },
                    "engine_nickname": {"type": ["string", "null"]},
                    "ranking_scores": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(RANKING_WEIGHTS),
                        "properties": {
                            category: {"type": "number", "minimum": 0, "maximum": 10}
                            for category in RANKING_WEIGHTS
                        },
                    },
                    "ranking_case": {"type": "string"},
                    "research_sources": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["url", "title", "publisher", "source_type", "supports"],
                            "properties": {
                                "url": {"type": "string"},
                                "title": {"type": "string"},
                                "publisher": {"type": "string"},
                                "source_type": {
                                    "type": "string",
                                    "enum": [
                                        "manufacturer", "heritage_archive", "specialist",
                                        "automotive_publication", "auction_result", "reference",
                                    ],
                                },
                                "supports": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": [
                                            "years", "introduced_year", "price_usd",
                                            "horsepower", "history", "reputation", "market_value",
                                        ],
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def calculate_ranking_total(entry):
    scores = entry.get("ranking_scores") or {}
    total = 0.0
    for category, weight in RANKING_WEIGHTS.items():
        try:
            score = float(scores.get(category, 0))
        except (TypeError, ValueError):
            score = 0.0
        total += max(0.0, min(10.0, score)) * weight
    return round(total, 3)


def _research_response(client, prompt, max_output_tokens):
    return with_openai_retry(lambda: client.responses.create(
        model="gpt-4o",
        tools=[{"type": "web_search_preview"}],
        input=prompt,
        max_output_tokens=max_output_tokens,
        text={
            "format": {
                "type": "json_schema",
                "name": "car_ranking_research",
                "strict": True,
                "schema": RESEARCH_OUTPUT_SCHEMA,
            }
        },
    ))


def _parse_research_response(response):
    status = getattr(response, "status", "completed")
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) or "unknown reason"
        raise ValueError(f"OpenAI response was incomplete: {reason}")
    text = str(getattr(response, "output_text", "") or "").strip()
    if not text:
        raise ValueError("OpenAI response contained no structured research output")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"OpenAI returned invalid research JSON at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc


def run_research(request_text):
    from openai import OpenAI, RateLimitError

    client = OpenAI()
    prompt = RESEARCH_PROMPT_TEMPLATE.format(request=request_text)
    try:
        response = _research_response(client, prompt, max_output_tokens=12000)
        try:
            data = _parse_research_response(response)
        except ValueError as first_error:
            print(f"[research] First structured response failed: {first_error}")
            retry_prompt = (
                f"{prompt}\n\nRETRY MODE: Return exactly 4 candidates, not 8. "
                "Keep descriptions and source titles concise while preserving every "
                "required schema field. Complete the JSON response."
            )
            retry_response = _research_response(client, retry_prompt, max_output_tokens=10000)
            try:
                data = _parse_research_response(retry_response)
            except ValueError as retry_error:
                raise SystemExit(
                    "OpenAI could not produce complete structured research after two attempts. "
                    f"First attempt: {first_error}. Retry: {retry_error}."
                ) from retry_error
    except RateLimitError as exc:
        error_code = getattr(exc, "code", None)
        if error_code == "insufficient_quota" or "insufficient_quota" in str(exc):
            raise SystemExit(
                "OpenAI rejected the research request because the API key has no available quota. "
                "Verify that the GitHub OPENAI_API_KEY secret belongs to the intended OpenAI project, "
                "that API billing/credits are active, and that the project's monthly budget or usage "
                "limit has not been reached. ChatGPT subscriptions and unrelated projects do not fund "
                "this API key."
            ) from exc
        raise SystemExit(
            "OpenAI rate-limited the research request. Wait briefly and retry, or review the API "
            "project's rate limits."
        ) from exc
    entry_count = len(data.get("entries", []))
    if entry_count < 4:
        raise SystemExit(f"Expected at least 4 entries from research, got {entry_count}.")
    for entry in data["entries"]:
        sources = entry.get("research_sources")
        if not isinstance(sources, list):
            entry["research_sources"] = []
        entry["ranking_total"] = calculate_ranking_total(entry)
    data["entries"].sort(key=lambda entry: entry["ranking_total"])
    return data


def compose_final_narration(request_text, entries):
    """Turn four researched records into one connected, human-sounding countdown."""
    from openai import OpenAI

    narration_inputs = []
    for rank, entry in zip((4, 3, 2, 1), entries):
        narration_inputs.append({
            "rank": rank,
            "name": entry["name"],
            "years": entry.get("years"),
            "introduced_year": entry.get("introduced_year"),
            "price_usd": entry.get("price_usd"),
            "horsepower": entry.get("horsepower"),
            "current_value_display": entry.get("current_value_display"),
            "label": entry.get("label"),
            "visual_highlight": entry.get("visual_highlight"),
            "visual_identifiers": entry.get("visual_identifiers", []),
            "researched_fact": entry.get("one_line_fact"),
        })
    prompt = NARRATION_PROMPT_TEMPLATE.format(
        request=request_text,
        entries_json=json.dumps(narration_inputs, indent=2),
    )
    response = with_openai_retry(lambda: OpenAI().responses.create(
        model="gpt-4o",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "car_narration_performance",
                "strict": True,
                "schema": NARRATION_OUTPUT_SCHEMA,
            }
        },
    ))
    text = response.output_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    result = json.loads(text)
    paragraphs = result.get("entries", [])
    by_name = {
        item.get("name"): item
        for item in paragraphs
        if isinstance(item, dict)
    }
    missing = [
        entry["name"] for entry in entries
        if not str(by_name.get(entry["name"], {}).get("narration", "")).strip()
    ]
    if missing:
        raise SystemExit(f"Final narration omitted ranked entries: {', '.join(missing)}")
    for entry in entries:
        narration_result = by_name[entry["name"]]
        beats = narration_result.get("performance_beats", [])
        beat_narration = " ".join(
            str(beat.get("text", "")).strip()
            for beat in beats
            if isinstance(beat, dict) and str(beat.get("text", "")).strip()
        )
        entry["narration"] = beat_narration or narration_result["narration"].strip()
        entry["performance_beats"] = beats
        entry["one_line_fact"] = entry["narration"]
    close = str(result.get("close_narration") or "").strip()
    if not close:
        raise SystemExit("Final narration omitted close_narration.")
    return close


def format_stat(entry):
    parts = []
    if entry.get("price_usd"):
        parts.append(f"MSRP ${entry['price_usd']:,.0f}")
    if entry.get("horsepower"):
        parts.append(f"{entry['horsepower']:.0f} HP")
    if entry.get("current_value_display"):
        parts.append(f"Now ~{entry['current_value_display']}")
    return " - ".join(parts) if parts else "SPEC UNAVAILABLE"


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_REVIEW_MODEL = os.getenv("OPENAI_MEDIA_REVIEW_MODEL", "gpt-4o-mini")
TARGET_IMAGES_PER_ENTRY = 4
MIN_IMAGES_PER_ENTRY = 2
IMAGE_CATEGORIES = {
    "exterior_front",
    "exterior_rear",
    "exterior_side",
    "exterior_full",
    "interior",
    "engine_bay",
    "wheel_detail",
    "other_detail",
}


def valid_images(directory):
    """Return only complete images that Pillow can decode."""
    valid = []
    if not directory.exists():
        return valid
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            with Image.open(path) as image:
                image.verify()
            valid.append(path)
        except (OSError, ValueError):
            print(f"[images] Removing unreadable download: {path}")
            path.unlink(missing_ok=True)
    return valid


def _image_data_url(path):
    media_type = "jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else path.suffix.lower().lstrip(".")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{media_type};base64,{encoded}"


def _normalized_image_category(value):
    value = re.sub(r"[^a-z]+", "_", str(value or "").lower()).strip("_")
    aliases = {
        "front": "exterior_front",
        "rear": "exterior_rear",
        "side": "exterior_side",
        "exterior": "exterior_full",
        "full_body": "exterior_full",
        "three_quarter": "exterior_full",
        "engine": "engine_bay",
        "wheel": "wheel_detail",
        "detail": "other_detail",
    }
    value = aliases.get(value, value)
    return value if value in IMAGE_CATEGORIES else "other_detail"


def _image_fingerprints(path):
    """Return exact and perceptual fingerprints for duplicate detection."""
    exact = hashlib.sha256(path.read_bytes()).hexdigest()
    with Image.open(path) as image:
        grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(grayscale.getdata())
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | int(
                pixels[row * 9 + column] > pixels[row * 9 + column + 1]
            )
    return exact, bits


def _find_duplicate(path, seen_images, max_distance=5):
    exact, perceptual = _image_fingerprints(path)
    for seen in seen_images:
        distance = (perceptual ^ seen["perceptual_hash"]).bit_count()
        if exact == seen["sha256"] or distance <= max_distance:
            return seen, exact, perceptual, distance
    return None, exact, perceptual, None


def _auction_provenance_matches_entry(entry):
    """Return true when one auction title coherently identifies the generation."""
    source = entry.get("image_source") or {}
    if source.get("provider") != "cars_and_bids":
        return False
    title = str(source.get("auction_title") or "").lower()
    params = infer_search_params(entry.get("search_hint", "")) or {}
    make = str(params.get("make") or "").lower()
    model = str(params.get("model") or "").lower()
    if not title or not make or not model:
        return False
    if not re.search(rf"\b{re.escape(make)}\b", title):
        return False
    if not re.search(rf"\b{re.escape(model)}\b", title):
        return False
    start_year, end_year = None, None
    years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", str(entry.get("years", "")))]
    if years:
        start_year, end_year = min(years), max(years)
    title_year_match = re.search(r"\b(?:19|20)\d{2}\b", title)
    if title_year_match and start_year and not (start_year <= int(title_year_match.group()) <= end_year):
        return False
    return True


def _generation_commons_terms(entry):
    """Prefer broad generation/chassis discovery before sparse exact-trim searches."""
    params = infer_search_params(entry.get("search_hint", "")) or {}
    make = str(params.get("make") or "").strip()
    model = str(params.get("model") or "").strip()
    chassis = str(entry.get("chassis_code") or "").strip()
    terms = []
    if make and model and chassis:
        terms.append(f"{make} {model} {chassis}")
    if make and model:
        terms.append(f"{make} {model}")
    terms.extend(entry.get("commons_search_terms") or [])
    return list(dict.fromkeys(term for term in terms if str(term).strip()))


def _finalize_image_review(review, trusted_variant_provenance=False):
    review["category"] = _normalized_image_category(review.get("category"))
    review["confidence"] = max(0.0, min(1.0, float(review.get("confidence", 0))))
    review["is_expected_vehicle"] = bool(review.get("is_expected_vehicle"))
    review["exact_variant_visible"] = bool(review.get("exact_variant_visible"))
    review["has_visible_contradiction"] = bool(review.get("has_visible_contradiction", False))
    facing = str(review.get("facing_direction") or "").strip().lower()
    review["facing_direction"] = facing if facing in ("left", "right") else "unclear"
    review["image_quality_usable"] = bool(
        review.get("image_quality_usable", True if trusted_variant_provenance else review.get("usable"))
    )
    if trusted_variant_provenance:
        review["usable"] = (
            review["image_quality_usable"]
            and not review["has_visible_contradiction"]
        )
    else:
        review["usable"] = (
            review["is_expected_vehicle"]
            and review["image_quality_usable"]
            and not review["has_visible_contradiction"]
            and review["confidence"] >= 0.6
        )
    review["trusted_variant_provenance"] = trusted_variant_provenance
    return review


def _review_image_with_ai(
    path,
    entry,
    model=IMAGE_REVIEW_MODEL,
    trusted_variant_provenance=False,
):
    from openai import OpenAI

    client = OpenAI()
    identifiers = ", ".join(entry.get("visual_identifiers") or [])
    prompt = f"""Inspect this downloaded car image using the pixels, not its filename.
Expected subject: {entry['name']} ({entry.get('years', 'year unknown')}).
Expected distinguishing visual identifiers: {identifiers or 'not provided'}.
Trusted exact-variant gallery provenance: {trusted_variant_provenance}.
Return ONLY strict JSON with:
- is_expected_vehicle: boolean (false for a clearly different model/generation or no useful car)
- exact_variant_visible: boolean
- has_visible_contradiction: boolean (true only when pixels positively show the wrong vehicle)
- image_quality_usable: boolean (judge blur, framing, page UI, collage, and resolution only)
- category: exactly one of exterior_front, exterior_rear, exterior_side, exterior_full,
  interior, engine_bay, wheel_detail, other_detail
- facing_direction: for any exterior category, which side of the frame the car's own front
  bumper/nose points toward -- "left", "right", or "unclear" (a straight front/rear shot, or a
  wheel/interior/engine/detail photo where the whole car isn't in frame). This is used to orient
  the car correctly in a left-to-right animation, so judge it from the pixels, not the filename.
- view_description: a precise phrase such as "front-left three-quarter exterior"
- visible_match_evidence: array of short descriptions of generation/variant-specific details
- confidence: number from 0 to 1
- usable: boolean
- rejection_reason: string or null
Mark usable false for page UI, severe blur, tiny vehicles, collages, watermarks dominating
the frame, or an obvious subject mismatch. A visible full car is exterior_full even if
the search or filename says engine, interior, wheel, or detail. Confirm the expected general
model/generation when reasonably visible, but do not reject merely because a trim, package,
badge, or engine designation cannot be proven from this angle. exact_variant_visible is useful
metadata, not an approval requirement. When provenance is true, the image came from one exact
auction gallery and remains usable unless the pixels contradict it.
Only use category "interior" when the dashboard, steering wheel, or center console is the
prominent subject filling a meaningful part of the frame -- not just visible at a small sliver
along an edge. A seat-only, trunk/cargo, headrest, or door-panel close-up (even one with a corner
of dashboard peeking in) should be "other_detail" instead; this category exists specifically for
the driver's-eye cabin view, not any interior-adjacent shot. Also use "other_detail" (never
"interior") for an extreme macro crop of a single isolated component -- a shifter knob, a single
vent, a stitching close-up, a lone button or dial -- shot tight enough that it lacks the
surrounding dashboard/wheel context to read clearly as "the car's interior" at a glance; those
crops often look like an ambiguous or awkward blob of shapes rather than a recognizable cabin
shot, which is exactly what "interior" must not be. A genuine "interior" photo shows enough of
the dash, wheel, and/or console together that a viewer instantly recognizes it as the cabin. Only
use "engine_bay" when actual engine-bay contents (block, intake, hoses, etc. with the hood open)
are visible -- a closed hood or an exterior badge is not engine_bay."""
    response = with_openai_retry(lambda: client.responses.create(
        model=model,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": _image_data_url(path)},
            ],
        }],
    ))
    text = response.output_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    review = json.loads(text)
    _finalize_image_review(review, trusted_variant_provenance)
    review["provider"] = "openai"
    review["model"] = model
    return review


def review_and_rename_entry_images(
    entry,
    images_dir,
    require_ai=False,
    model=IMAGE_REVIEW_MODEL,
    seen_images=None,
    trusted_variant_provenance=False,
):
    """Verify draft images from their pixels and give usable files truthful names."""
    if seen_images is None:
        seen_images = []
    reviewed = []
    approved = []
    category_counts = {}
    for relative_image in entry.get("images", []):
        path = images_dir.parent / relative_image
        original_relative = relative_image
        duplicate, sha256, perceptual_hash, duplicate_distance = _find_duplicate(path, seen_images)
        if duplicate:
            reviewed.append({
                "original_path": original_relative,
                "path": original_relative,
                "provider": "fingerprint",
                "usable": False,
                "is_expected_vehicle": False,
                "category": "other_detail",
                "view_description": "duplicate image",
                "confidence": 1.0,
                "rejection_reason": "duplicate_or_near_duplicate",
                "duplicate_of": duplicate["path"],
                "duplicate_of_entry": duplicate["entry"],
                "perceptual_distance": duplicate_distance,
            })
            continue
        try:
            review = _review_image_with_ai(
                path,
                entry,
                model=model,
                trusted_variant_provenance=trusted_variant_provenance,
            )
            _finalize_image_review(review, trusted_variant_provenance)
        except Exception as exc:
            if require_ai:
                raise SystemExit(f"AI image review failed for {path}: {exc}") from exc
            review = {
                "category": "other_detail",
                "view_description": "unverified image",
                "confidence": 0.0,
                "is_expected_vehicle": True,
                "usable": True,
                "rejection_reason": None,
                "provider": "unverified",
                "error": str(exc),
            }

        review["original_path"] = original_relative
        if review["usable"]:
            category = review["category"]
            category_counts[category] = category_counts.get(category, 0) + 1
            renamed = path.with_name(f"{category}-{category_counts[category]:02d}{path.suffix.lower()}")
            if renamed != path:
                path.rename(renamed)
            current_relative = f"images/{renamed.parent.name}/{renamed.name}"
            review["path"] = current_relative
            approved.append(current_relative)
            seen_images.append({
                "path": current_relative,
                "entry": entry["name"],
                "sha256": sha256,
                "perceptual_hash": perceptual_hash,
            })
        else:
            review["path"] = original_relative
        reviewed.append(review)

    entry["images"] = approved
    entry["image_reviews"] = reviewed
    entry["image_review_provider"] = (
        "openai" if reviewed and all(item.get("provider") == "openai" for item in reviewed)
        else "mixed_or_unverified"
    )
    return entry


def run_image_search(query, dest, prefix, limit=1, min_width=900):
    subprocess.run(
        [
            "node", "src/search-commons-media.js",
            f"--query={query}", f"--out-dir={dest}", f"--prefix={prefix}",
            f"--limit={limit}", f"--min-width={min_width}",
        ],
        cwd=SCRAPER_DIR,
        check=False,
    )
    return valid_images(dest)


def scrape_images(
    search_hint,
    topic_slug,
    draft_images_dir,
    visual_highlight="",
    commons_search_terms=None,
):
    """Build a varied, verified image set with thematic and general fallbacks."""
    dest = draft_images_dir / topic_slug
    existing_names = {path.name for path in valid_images(dest)}

    def new_images():
        return [path for path in valid_images(dest) if path.name not in existing_names]

    precise_terms = [
        str(term).strip()
        for term in (commons_search_terms or [])
        if str(term).strip()
    ]
    category_hint = precise_terms[0] if precise_terms else search_hint
    subprocess.run(
        [
            "node", "src/scrape-commons-category.js",
            f"--category={category_hint}",
            f"--topic=drafts-tmp/{topic_slug}",
            "--limit=4",
            "--pool-size=100",
            "--target-front=1",
            "--target-rear=1",
            "--target-side=0",
            "--target-interior=1",
            "--target-engine=1",
            "--target-wheel=0",
            "--download",
        ],
        cwd=SCRAPER_DIR,
        check=False,  # best effort -- a bad category name shouldn't kill the whole run
    )
    scraped_dir = ROOT / "cars" / "output" / "sources" / "drafts-tmp" / topic_slug / "images"
    if scraped_dir.exists():
        for path in valid_images(scraped_dir)[:4]:
            dest.mkdir(parents=True, exist_ok=True)
            out_path = dest / path.name
            out_path.write_bytes(path.read_bytes())
    dest.mkdir(parents=True, exist_ok=True)

    # Add deliberate visual variety instead of relying on whichever category
    # files sort first. Each query has a distinct prefix so results coexist.
    query_base = precise_terms[0] if precise_terms else search_hint
    themed_queries = [
        (f"{query_base} rear", "rear"),
        (f"{query_base} interior dashboard", "interior"),
    ]
    if visual_highlight:
        themed_queries.append((f"{query_base} {visual_highlight}", "highlight"))
    for index, term in enumerate(precise_terms[1:], start=2):
        themed_queries.append((term, f"precise-{index}"))
    for query, prefix in themed_queries:
        if len(new_images()) >= 6:
            break
        run_image_search(query, dest, prefix)

    # General model images are the safe fallback. Retry at a lower resolution
    # threshold when Commons has sparse coverage for an older model/year.
    if len(new_images()) < 2:
        print(f"[images] Adding general fallback images for {search_hint!r}")
        run_image_search(search_hint, dest, "general", limit=3)
    if not new_images():
        run_image_search(search_hint, dest, "fallback", limit=3, min_width=600)

    return [f"images/{topic_slug}/{path.name}" for path in valid_images(dest)[:12]]


def build_engine_clip_preview(entry, images_dir, topic_slug):
    """Extract and verify a real short engine-clip preview during research.

    Without this, the research review step could only show a static
    thumbnail linking out to the source listing - not the actual
    startup/rev clip a viewer would want to check before committing to a
    render. Cached under the draft's own directory (keyed by this entry's
    topic slug) so the later render step can reuse this exact clip instead
    of re-extracting and re-verifying it a second time.
    """
    if not entry.get("engine_videos"):
        return None
    preview_dir = images_dir.parent / "engine_preview"
    pseudo_entry = SimpleNamespace(
        rank=topic_slug,
        name=entry["name"],
        years=entry.get("years", ""),
        engine_videos=entry["engine_videos"],
    )
    try:
        # Match the standalone video-test tool's tolerance: it always tries
        # real audio on a candidate even when its static thumbnail looked
        # mediocre. Without allow_irrelevant here, this call could give up
        # purely on thumbnail-guessing, never checking audio on anything,
        # and miss a genuinely good exhaust/cold-start clip whose thumbnail
        # just happened to be a plain frame.
        result = prepare_engine_clip(pseudo_entry, preview_dir, allow_irrelevant=True)
    except Exception as exc:
        return {"approved": False, "error": str(exc)}
    if not result:
        return None
    preview = {
        "approved": bool(result.get("approved")),
        "detected_onset_seconds": result.get("detected_onset_seconds"),
        "engine_event_score": result.get("engine_event_score"),
        "scene_review": result.get("scene_review"),
        "review": result.get("review"),
        "source": result.get("source"),
        "error": result.get("error"),
    }
    if result.get("path"):
        preview["path"] = str(Path(result["path"]).relative_to(images_dir.parent)).replace("\\", "/")
    return preview


def source_entry_images(entry, images_dir, require_ai_image_review=False, seen_images=None):
    topic_slug = slugify(entry["name"])
    print(f"[images] {entry['name']} -> trying Cars & Bids for {entry['search_hint']!r}")
    cars_and_bids_images, cars_and_bids_manifest = scrape_entry_images(SCRAPER_DIR, images_dir, entry)
    entry["images"] = cars_and_bids_images
    enrich_entry_from_manifest(entry, cars_and_bids_manifest)
    # The photo scrape above only visits the top few price-sorted listings,
    # which is fine for photo quality but routinely misses engine videos
    # sitting in cheaper listings. Run the same wider, video-only search the
    # standalone video-test tool uses so engine clips are found just as
    # reliably here as they are there.
    print(f"[videos] {entry['name']} -> searching Cars & Bids for engine videos")
    broader_videos = discover_entry_engine_videos(SCRAPER_DIR, images_dir, entry)
    if broader_videos:
        entry["engine_videos"] = broader_videos
    print(f"[videos] {entry['name']} -> extracting and verifying an engine-clip preview")
    entry["engine_clip_preview"] = build_engine_clip_preview(entry, images_dir, topic_slug)
    initial_images = list(entry["images"])
    review_and_rename_entry_images(
        entry,
        images_dir,
        require_ai=require_ai_image_review,
        seen_images=seen_images,
        trusted_variant_provenance=_auction_provenance_matches_entry(entry),
    )
    if len(entry["images"]) < TARGET_IMAGES_PER_ENTRY:
        print(
            f"[images] Cars & Bids yielded only {len(entry['images'])} verified unique "
            f"images for {entry['name']} -- adding chassis-aware Commons results"
        )
        approved_images = list(entry["images"])
        initial_reviews = list(entry.get("image_reviews", []))
        commons_candidates = scrape_images(
            entry["search_hint"],
            topic_slug,
            images_dir,
            entry.get("visual_highlight", ""),
            _generation_commons_terms(entry),
        )
        already_considered = set(initial_images) | set(approved_images)
        already_considered.update(
            review.get("original_path") for review in initial_reviews if review.get("original_path")
        )
        fallback_entry = dict(entry)
        fallback_entry["images"] = [
            image for image in commons_candidates if image not in already_considered
        ]
        fallback_entry["image_reviews"] = []
        review_and_rename_entry_images(
            fallback_entry,
            images_dir,
            require_ai=require_ai_image_review,
            seen_images=seen_images,
            trusted_variant_provenance=False,
        )
        entry["images"] = [*approved_images, *fallback_entry["images"]][:6]
        entry["image_reviews"] = [*initial_reviews, *fallback_entry["image_reviews"]]
        entry["image_review_provider"] = (
            "openai"
            if entry["image_reviews"]
            and all(review.get("provider") == "openai" for review in entry["image_reviews"])
            else "mixed_or_unverified"
        )
        entry["image_fallback_used"] = "wikimedia_commons"
    else:
        entry["image_fallback_used"] = None
    entry["image_coverage"] = {
        "approved_count": len(entry["images"]),
        "target_count": TARGET_IMAGES_PER_ENTRY,
        "target_met": len(entry["images"]) >= TARGET_IMAGES_PER_ENTRY,
    }
    entry["stat"] = format_stat(entry)

    # Real listing photos carry real, current license plates. Blur any that
    # are visible in the final chosen images before they reach the video;
    # this only ever runs on the handful of images an entry actually keeps,
    # not every scraped candidate.
    draft_dir = images_dir.parent
    for relative_path in entry["images"]:
        blur_license_plates(draft_dir / relative_path)

    return entry


def main():
    parser = argparse.ArgumentParser(description="AI-research a free-text car ranking request into a draft JSON + images.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--draft-id", required=True)
    parser.add_argument(
        "--require-ai-image-review",
        action="store_true",
        help="Fail instead of publishing unverified image labels when vision review is unavailable.",
    )
    args = parser.parse_args()

    draft_dir = DRAFTS_ROOT / args.draft_id
    images_dir = draft_dir / "images"
    draft_dir.mkdir(parents=True, exist_ok=True)

    print(f"[research] Request: {args.request!r}")
    data = run_research(args.request)
    print(f"[research] Title: {data['title']}")

    selected_entries = []
    skipped_entries = []
    seen_images = []
    # Source the strongest candidates first. Once four have verified images,
    # restore ascending order so the UI/video reads #4 through #1.
    ranked_candidates = sorted(
        data["entries"],
        key=lambda entry: entry.get("ranking_total", 0),
        reverse=True,
    )
    for i, candidate in enumerate(ranked_candidates):
        if i > 0:
            time.sleep(5)  # let remote rate limiters cool down between entries
        entry = source_entry_images(
            candidate,
            images_dir,
            require_ai_image_review=args.require_ai_image_review,
            seen_images=seen_images,
        )
        if len(entry["images"]) >= MIN_IMAGES_PER_ENTRY:
            selected_entries.append(entry)
            print(f"[images] Selected {entry['name']} with {len(entry['images'])} image(s)")
        else:
            skipped_entries.append({
                "name": entry["name"],
                "years": entry.get("years", ""),
                "reason": f"fewer_than_{MIN_IMAGES_PER_ENTRY}_verified_unique_images",
            })
            print(
                f"[images] Skipping {entry['name']} because fewer than "
                f"{MIN_IMAGES_PER_ENTRY} verified "
                "unique images were found"
            )
        if len(selected_entries) == 4:
            break

    if len(selected_entries) < 4:
        raise SystemExit(
            f"Only found {len(selected_entries)} image-backed entries out of {len(data['entries'])} researched candidates. "
            "Try a broader request or improve source coverage."
        )

    selected_entries.sort(key=lambda entry: entry.get("ranking_total", 0))
    data["order_rationale"] = (
        "Ranked by weighted enthusiast desirability, driving engagement, historical "
        "significance, performance, collectibility, and value—not chronology."
    )
    print("[research] Composing connected final narration from the four selected entries")
    data["close_narration"] = compose_final_narration(args.request, selected_entries)

    output = {
        "request": args.request,
        "draft_id": args.draft_id,
        "title": data["title"],
        "highlight_word": data["highlight_word"],
        "close_narration": data["close_narration"],
        "order_rationale": data.get("order_rationale", ""),
        "entries": selected_entries,
        "skipped_entries": skipped_entries,
        "status": "researched",  # -> "video_generated" after stage 2
    }
    (draft_dir / "research.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"[research] Wrote {draft_dir / 'research.json'}")


if __name__ == "__main__":
    main()
