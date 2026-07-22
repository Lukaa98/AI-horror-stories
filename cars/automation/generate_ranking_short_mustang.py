import argparse
from pathlib import Path

from ranking_engine import ROOT, OUTPUT_ROOT, RankEntry, RankingConfig, render_ranking_video

SOURCE_ROOT = ROOT / "cars" / "output" / "sources"

CONFIG = RankingConfig(
    slug="ford-mustang-generation-ranking",
    title="RANKING MUSTANG GENERATIONS",
    title_highlight_words={"MUSTANG"},
    theme="Ford Mustang generation ranking by community sentiment + stats",
    close_narration="Would you rank these differently?",
    ranks=[
        RankEntry(
            rank=4,
            name="SN95",
            years="1994-2004",
            images=[
                SOURCE_ROOT / "ford-mustang-sn95" / "images" / "commons-001.jpg",
                SOURCE_ROOT / "ford-mustang-sn95" / "images" / "commons-006.jpg",
            ],
            label="MOST UNLOVED",
            stat="215 HP - LEAST FAVORITE ERA",
            narration="Ranking every Mustang generation. SN95 ranks last - the most unloved era.",
        ),
        RankEntry(
            rank=3,
            name="S197",
            years="2005-2014",
            images=[
                SOURCE_ROOT / "ford-mustang-s197" / "images" / "commons-002.jpg",
                SOURCE_ROOT / "ford-mustang-s197" / "images" / "commons-001.jpg",
            ],
            label="THE COYOTE ERA",
            stat="412 HP - RETRO REVIVAL",
            narration="S197 brought retro styling and the Coyote V8 - solid, but overshadowed.",
        ),
        RankEntry(
            rank=2,
            name="Fox Body",
            years="1979-1993",
            images=[
                SOURCE_ROOT / "ford-mustang-foxbody" / "images" / "commons-001.jpg",
                SOURCE_ROOT / "ford-mustang-foxbody" / "images" / "commons-004.jpg",
            ],
            label="STRONGEST CULT FOLLOWING",
            stat="225 HP - LIGHTWEIGHT ICON",
            narration="Fox Body is the purist favorite - strongest cult following of them all.",
        ),
        RankEntry(
            rank=1,
            name="S550",
            years="2015-2023",
            images=[
                SOURCE_ROOT / "ford-mustang-s550" / "images" / "commons-001.jpg",
                SOURCE_ROOT / "ford-mustang-s550" / "images" / "commons-004.jpg",
            ],
            label="BEST ALL-AROUND",
            stat="435 HP - MOST POWER EVER",
            narration="S550 takes number one - the most power any Mustang's ever had.",
        ),
    ],
)


def main():
    parser = argparse.ArgumentParser(description="Generate the Mustang generation ranking Short.")
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
