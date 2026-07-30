"""Suggest 3-5 rival cars for a startup-sound battle, given one base car.

Lets the Battle UI ask "what should I compare this against?" instead of the
user having to already know good rivals. Writes
cars/rival-suggestions/<suggestion-id>/suggestions.json.
"""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from openai_retry import with_openai_retry

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
SUGGESTIONS_ROOT = ROOT / "cars" / "rival-suggestions"

RIVALS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rivals"],
    "properties": {
        "rivals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["make", "model", "trim", "year", "reason"],
                "properties": {
                    "make": {"type": "string"},
                    "model": {"type": "string"},
                    "trim": {"type": "string"},
                    "year": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}


def suggest_rivals(make, model, trim, year, count):
    base_label = f"{year} {make} {model} {trim}".strip()
    prompt = (
        f"Suggest {count} rival/competitor cars for a head-to-head cold-start-sound comparison video "
        f"against the {base_label}. Pick cars from roughly the same era (within a few model years), a "
        "similar price bracket, and a similar performance/segment -- genuine rivals a car enthusiast "
        "would cross-shop or compare, not random unrelated cars. Prefer cars with well-known, distinct "
        "exhaust notes or cold-start sounds, and avoid suggesting the same make and model as the base "
        "car. For each rival return make, model, trim (the best-known trim/variant for that "
        "price/performance tier, empty string if not applicable), year (a specific model year, not a "
        "range), and a one-sentence reason it is a fair rival to the base car."
    )
    from openai import OpenAI

    response = with_openai_retry(lambda: OpenAI().responses.create(
        model="gpt-4o-mini",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "rival_suggestions",
                "strict": True,
                "schema": RIVALS_SCHEMA,
            }
        },
    ))
    data = json.loads(response.output_text.strip())
    return (data.get("rivals") or [])[:count]


def main():
    parser = argparse.ArgumentParser(description="Suggest rival cars for a startup-sound battle.")
    parser.add_argument("--make", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--trim", default="")
    parser.add_argument("--year", required=True)
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--suggestion-id", required=True)
    args = parser.parse_args()

    count = max(1, min(4, args.count))
    rivals = suggest_rivals(args.make, args.model, args.trim, args.year, count)

    out_dir = SUGGESTIONS_ROOT / args.suggestion_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output = {
        "suggestion_id": args.suggestion_id,
        "base_car": {"make": args.make, "model": args.model, "trim": args.trim, "year": args.year},
        "rivals": rivals,
    }
    (out_dir / "suggestions.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"[suggest-rivals] Wrote {out_dir / 'suggestions.json'} ({len(rivals)} rivals)")


if __name__ == "__main__":
    main()
