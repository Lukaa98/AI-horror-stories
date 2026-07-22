"""Stage 2: render a ranking video from an already-researched draft
(cars/drafts/<draft-id>/research.json, written by research_request.py).
Kept separate from research so a human can review facts/photos before
committing to a render.
"""
import argparse
import json
from pathlib import Path

from ranking_engine import ROOT, RankEntry, RankingConfig, render_ranking_video

DRAFTS_ROOT = ROOT / "cars" / "drafts"


def build_config(draft_dir, data):
    entries = data["entries"]
    if len(entries) != 4:
        raise SystemExit(f"research.json must have exactly 4 entries, found {len(entries)}")

    ranks = []
    for i, entry in enumerate(entries):
        rank_num = 4 - i
        images = [draft_dir / img for img in entry.get("images", [])]
        images = [p for p in images if p.exists()]
        if not images:
            raise SystemExit(f"No usable images for entry {entry['name']!r} -- cannot render.")
        ranks.append(RankEntry(
            rank=rank_num,
            name=entry["name"],
            years=entry.get("years", ""),
            images=images,
            label=entry.get("label", ""),
            stat=entry.get("stat", ""),
            narration=entry.get("one_line_fact", ""),
        ))

    return RankingConfig(
        slug=data["draft_id"],
        title=data["title"],
        title_highlight_words={data.get("highlight_word", "").upper()},
        theme=f"AI-researched draft for request: {data.get('request', '')}",
        close_narration=data.get("close_narration", "Would you rank these differently?"),
        ranks=ranks,
    )


def main():
    parser = argparse.ArgumentParser(description="Render a ranking video from a research.json draft.")
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--tts-provider", default="gtts", choices=["gtts", "openai", "tone", "silent"])
    parser.add_argument("--full-res", action="store_true")
    args = parser.parse_args()

    draft_dir = DRAFTS_ROOT / args.draft_id
    research_path = draft_dir / "research.json"
    if not research_path.exists():
        raise SystemExit(f"No research.json found at {research_path} -- run research_request.py first.")
    data = json.loads(research_path.read_text(encoding="utf-8"))

    config = build_config(draft_dir, data)
    run_dir = render_ranking_video(config, output_root=DRAFTS_ROOT, tts_provider=args.tts_provider, fast=not args.full_res)

    data["status"] = "video_generated"
    research_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(run_dir)


if __name__ == "__main__":
    main()
