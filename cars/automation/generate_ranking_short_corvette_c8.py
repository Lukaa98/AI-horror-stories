import argparse
from pathlib import Path

from ranking_engine import ROOT, OUTPUT_ROOT, RankEntry, RankingConfig, render_ranking_video

SOURCE_ROOT = ROOT / "cars" / "output" / "sources"

# Specs verified via web search (2026 model year): Chevrolet.com / Edmunds / CarBuzz / GM Authority.
# Ranked by starting price, base -> flagship (not a sentiment ranking like the generation videos).
CONFIG = RankingConfig(
    slug="corvette-c8-trim-breakdown",
    title="RANKING C8 CORVETTE TRIMS",
    title_highlight_words={"CORVETTE"},
    theme="Chevrolet Corvette C8 trim breakdown by price and horsepower (2026 MY specs)",
    close_narration="Which C8 would you actually buy?",
    ranks=[
        RankEntry(
            rank=4,
            name="Stingray",
            years="2020-present",
            images=[
                SOURCE_ROOT / "corvette-c8-stingray" / "images" / "commons-002.jpg",
                SOURCE_ROOT / "corvette-c8-stingray" / "images" / "commons-004.jpg",
            ],
            label="ENTRY POINT",
            stat="$71,995 - 490 HP",
            narration="The Stingray starts it all. Seventy one thousand dollars, four ninety horsepower.",
        ),
        RankEntry(
            rank=3,
            name="E-Ray",
            years="2024-present",
            images=[
                SOURCE_ROOT / "corvette-c8-eray" / "images" / "commons-001.jpg",
                SOURCE_ROOT / "corvette-c8-eray" / "images" / "commons-002.jpg",
            ],
            label="HYBRID AWD",
            stat="$110,595 - 655 HP",
            narration="E-Ray adds a hybrid motor for all wheel drive - six fifty five horsepower.",
        ),
        RankEntry(
            rank=2,
            name="Z06",
            years="2023-present",
            images=[
                SOURCE_ROOT / "corvette-c8-z06" / "images" / "commons-006.jpg",
                SOURCE_ROOT / "corvette-c8-z06" / "images" / "commons-004.jpg",
            ],
            label="NATURALLY ASPIRATED FLAGSHIP",
            stat="$119,695 - 670 HP",
            narration="Z06 packs a naturally aspirated flat plane V8 - six seventy horsepower, zero turbos.",
        ),
        RankEntry(
            rank=1,
            name="ZR1",
            years="2025-present",
            images=[
                SOURCE_ROOT / "corvette-c8-zr1" / "images" / "commons-001.jpg",
                SOURCE_ROOT / "corvette-c8-zr1" / "images" / "commons-003.jpg",
            ],
            label="MOST POWERFUL EVER",
            stat="$182,395 - 1,064 HP",
            narration="ZR1 tops the lineup with twin turbos - one thousand sixty four horsepower.",
        ),
    ],
)


def main():
    parser = argparse.ArgumentParser(description="Generate the Corvette C8 trim breakdown Short.")
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--tts-provider", choices=["gtts", "openai", "tone", "silent"], default="gtts")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--full-res", action="store_true")
    args = parser.parse_args()
    run_dir = render_ranking_video(
        CONFIG,
        output_root=args.output_root,
        render_video=not args.no_video,
        tts_provider=args.tts_provider,
        fast=not args.full_res,
    )
    print(run_dir)


if __name__ == "__main__":
    main()
