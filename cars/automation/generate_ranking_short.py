import argparse
from pathlib import Path

from ranking_engine import ROOT, OUTPUT_ROOT, RankEntry, RankingConfig, render_ranking_video

SOURCE_ROOT = ROOT / "cars" / "output" / "sources"

CONFIG = RankingConfig(
    slug="mazda-miata-generation-ranking",
    title="RANKING MIATA GENERATIONS",
    title_highlight_words={"MIATA"},
    theme="Mazda Miata generation ranking by community sentiment + stats",
    close_narration="Would you rank these differently?",
    ranks=[
        RankEntry(
            rank=4,
            name="NC",
            years="2006-2015",
            images=[
                SOURCE_ROOT / "mazda-miata-nc" / "images" / "commons-002.jpg",
                SOURCE_ROOT / "mazda-miata-nc" / "images" / "commons-004.jpg",
            ],
            label="THE BOAT",
            stat="2,498 LB - HEAVIEST EVER",
            narration="Ranking the best Mazda Miatas ever made. First up, NC ranks last - heaviest Miata ever, and fans never forgave it.",
        ),
        RankEntry(
            rank=3,
            name="NB",
            years="1998-2005",
            images=[
                SOURCE_ROOT / "mazda-miata-nb" / "images" / "commons-001.jpg",
                SOURCE_ROOT / "mazda-miata-nb" / "images" / "commons-004.jpg",
            ],
            label="THE FORGOTTEN ONE",
            stat="140 HP",
            narration="NB refined the formula, but nobody really talks about it.",
        ),
        RankEntry(
            rank=2,
            name="NA",
            years="1989-1997",
            images=[
                SOURCE_ROOT / "mazda-miata-na" / "images" / "commons-001.jpg",
                SOURCE_ROOT / "mazda-miata-na" / "images" / "commons-004.jpg",
            ],
            label="THE PURIST PICK",
            stat="2,116 LB - LIGHTEST EVER",
            narration="NA is the original. Lightest Miata ever, purists' favorite.",
        ),
        RankEntry(
            rank=1,
            name="ND",
            years="2016-present",
            images=[
                SOURCE_ROOT / "mazda-miata-nd" / "images" / "commons-001.jpg",
                SOURCE_ROOT / "mazda-miata-nd" / "images" / "commons-004.jpg",
            ],
            label="THE COMEBACK",
            stat="181 HP - BEST POWER-TO-WEIGHT",
            narration="ND takes number one. Most power, best power-to-weight ever.",
        ),
    ],
)


def main():
    parser = argparse.ArgumentParser(description="Generate the Miata generation ranking Short.")
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--tts-provider", choices=["gtts", "openai", "tone", "silent"], default="gtts")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    run_dir = render_ranking_video(
        CONFIG,
        output_root=args.output_root,
        render_video=not args.no_video,
        tts_provider=args.tts_provider,
    )
    print(run_dir)


if __name__ == "__main__":
    main()
