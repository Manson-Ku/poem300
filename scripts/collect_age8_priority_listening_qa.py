#!/usr/bin/env python3
"""Collect Age 8 priority pronunciation listening-QA WAV files.

Default behavior:
- copies Tier 1 + Tier 2 priority assets
- keeps source WAV files untouched
- writes copies under review/age8_pronunciation_priority/
- writes review.csv with the expected target readings
- exits non-zero when any expected source WAV is missing

Examples:
    py scripts\collect_age8_priority_listening_qa.py
    py scripts\collect_age8_priority_listening_qa.py --tier 1
    py scripts\collect_age8_priority_listening_qa.py --tier 2
    py scripts\collect_age8_priority_listening_qa.py --clean
"""

from __future__ import annotations

import argparse
import csv
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReviewAsset:
    tier: int
    poem_id: int
    audio_type: str
    scene_no: int
    expected: str
    note: str

    @property
    def source_relative_path(self) -> Path:
        poem_dir = Path(f"p{self.poem_id:03d}")
        if self.audio_type in {"title", "author"}:
            return poem_dir / "audio" / f"{self.audio_type}.wav"
        return (
            poem_dir
            / f"s{self.scene_no:02d}"
            / "audio"
            / f"{self.audio_type}.wav"
        )

    @property
    def short_id(self) -> str:
        if self.audio_type in {"title", "author"}:
            return f"p{self.poem_id:03d}_{self.audio_type}"
        return f"p{self.poem_id:03d}_s{self.scene_no:02d}_{self.audio_type}"


PRIORITY_ASSETS: tuple[ReviewAsset, ...] = (
    # Tier 1: highest-risk polyphones / historical variants / prior failure patterns.
    ReviewAsset(1, 28, "poem", 2, "爲 ㄨㄟˋ", "何爲來"),
    ReviewAsset(1, 34, "title", 0, "塞 ㄙㄞˋ", "塞上曲"),
    ReviewAsset(1, 34, "poem", 2, "塞 ㄙㄞˋ；塞 ㄙㄞˋ", "出塞／入塞"),
    ReviewAsset(1, 77, "poem", 3, "塞 ㄙㄜˋ；行 ㄏㄤˊ", "冰塞川／太行"),
    ReviewAsset(1, 92, "author", 0, "參 ㄕㄣ", "岑參"),
    ReviewAsset(1, 96, "poem", 2, "爲 ㄨㄟˋ", "爲我"),
    ReviewAsset(1, 100, "poem", 4, "數 ㄕㄨㄛˋ", "數問"),
    ReviewAsset(1, 151, "poem", 2, "參 ㄘㄣ；差 ㄘ", "參差"),
    ReviewAsset(1, 152, "poem", 4, "占 ㄓㄢ；數 ㄕㄨㄛˋ", "占夢數"),
    ReviewAsset(1, 251, "poem", 1, "單 ㄔㄢˊ", "單于"),
    ReviewAsset(1, 251, "poem", 2, "騎 ㄐㄧˋ", "輕騎"),
    ReviewAsset(
        1,
        294,
        "poem",
        1,
        "興 ㄒㄧㄥˋ；重 ㄔㄨㄥˊ；省 ㄒㄧㄥˇ",
        "乘興／九重／誰省",
    ),
    ReviewAsset(1, 312, "poem", 2, "觧 ㄐㄧㄝˇ；沈 ㄔㄣˊ", "觧釋／沈香亭"),

    # Tier 2: uncommon place/person names and rare characters.
    ReviewAsset(2, 29, "title", 0, "盱 ㄒㄩ；眙 ㄧˊ", "盱眙"),
    ReviewAsset(2, 99, "poem", 1, "鄜 ㄈㄨ", "鄜州"),
    ReviewAsset(2, 126, "author", 0, "長 ㄔㄤˊ", "劉長卿"),
    ReviewAsset(2, 127, "author", 0, "長 ㄔㄤˊ", "劉長卿"),
    ReviewAsset(2, 128, "author", 0, "長 ㄔㄤˊ", "劉長卿"),
    ReviewAsset(2, 129, "author", 0, "長 ㄔㄤˊ", "劉長卿"),
    ReviewAsset(2, 130, "author", 0, "長 ㄔㄤˊ", "劉長卿"),
    ReviewAsset(2, 138, "author", 0, "綸 ㄌㄨㄣˊ", "盧綸"),
    ReviewAsset(2, 163, "author", 0, "顥 ㄏㄠˋ", "崔顥"),
    ReviewAsset(2, 249, "author", 0, "綸 ㄌㄨㄣˊ", "盧綸"),
    ReviewAsset(2, 250, "author", 0, "綸 ㄌㄨㄣˊ", "盧綸"),
    ReviewAsset(2, 251, "author", 0, "綸 ㄌㄨㄣˊ", "盧綸"),
    ReviewAsset(2, 252, "author", 0, "綸 ㄌㄨㄣˊ", "盧綸"),
    ReviewAsset(2, 278, "author", 0, "祜 ㄏㄨˋ", "張祜"),
    ReviewAsset(2, 279, "author", 0, "祜 ㄏㄨˋ", "張祜"),
    ReviewAsset(2, 299, "author", 0, "畋 ㄊㄧㄢˊ", "鄭畋"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy Age 8 priority pronunciation QA WAV files."
    )
    parser.add_argument(
        "--assets-root",
        default="assets",
        help="Source asset root. Default: assets",
    )
    parser.add_argument(
        "--output-dir",
        default="review/age8_pronunciation_priority",
        help=(
            "Output directory. "
            "Default: review/age8_pronunciation_priority"
        ),
    )
    parser.add_argument(
        "--tier",
        choices=("1", "2", "all"),
        default="all",
        help="Copy Tier 1, Tier 2, or both. Default: all",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the output directory before copying.",
    )
    return parser.parse_args()


def selected_assets(tier: str) -> list[ReviewAsset]:
    if tier == "all":
        return list(PRIORITY_ASSETS)
    wanted = int(tier)
    return [item for item in PRIORITY_ASSETS if item.tier == wanted]


def main() -> int:
    args = parse_args()
    assets_root = Path(args.assets_root)
    output_dir = Path(args.output_dir)

    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)

    chosen = selected_assets(args.tier)
    missing: list[Path] = []
    rows: list[dict[str, str | int]] = []

    counters = {1: 0, 2: 0}

    for item in chosen:
        source = assets_root / item.source_relative_path
        if not source.exists():
            missing.append(source)
            continue

        counters[item.tier] += 1
        tier_dir = output_dir / f"tier{item.tier}"
        tier_dir.mkdir(parents=True, exist_ok=True)

        destination = tier_dir / (
            f"{counters[item.tier]:02d}_{item.short_id}.wav"
        )
        shutil.copy2(source, destination)

        rows.append(
            {
                "tier": item.tier,
                "order": counters[item.tier],
                "asset_id": item.short_id,
                "poem_id": item.poem_id,
                "audio_type": item.audio_type,
                "scene_no": item.scene_no,
                "expected_reading": item.expected,
                "review_note": item.note,
                "source_wav": source.as_posix(),
                "review_wav": destination.as_posix(),
                "listening_result": "",
                "observed_reading": "",
                "reviewer_note": "",
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "review.csv"
    fieldnames = [
        "tier",
        "order",
        "asset_id",
        "poem_id",
        "audio_type",
        "scene_no",
        "expected_reading",
        "review_note",
        "source_wav",
        "review_wav",
        "listening_result",
        "observed_reading",
        "reviewer_note",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("Age 8 priority listening QA collection")
    print(f"tier={args.tier}")
    print(f"selected={len(chosen)}")
    print(f"copied={len(rows)}")
    print(f"missing={len(missing)}")
    print(f"output={output_dir.as_posix()}")
    print(f"manifest={csv_path.as_posix()}")

    if missing:
        print("\nMissing source WAV files:")
        for path in missing:
            print(f"  {path.as_posix()}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
