#!/usr/bin/env python3
"""Build reviewable image prompt manifests from poem300 scene SSOT."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

PROMPT_FIELDS = [
    "scene_id",
    "poem_id",
    "title",
    "author",
    "recommended_age",
    "scene_no",
    "original_line",
    "child_explanation_line",
    "style_id",
    "aspect_ratio",
    "character_consistency_key",
    "output_image",
    "image_prompt",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_style(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_prompt(
    poem: dict[str, str],
    scene: dict[str, str],
    style: dict[str, Any],
) -> str:
    visual_style = ", ".join(style["visual_style"])
    negative_rules = ", ".join(style["negative_rules"])

    return (
        "Create one 16:9 children's picture-book background illustration "
        "for a Tang-poetry learning video.\n\n"
        f"Poem: {poem['title']}\n"
        f"Author: {poem['author']}\n"
        f"Scene: {scene['scene_no']}\n"
        f"Original poem line: {scene['original_line']}\n"
        "Child-friendly meaning: "
        f"{scene['child_explanation_line']}\n\n"
        "Visualize the meaning of this whole physical poem line as ONE "
        "coherent scene. Do not split the image into panels or separate "
        "shots based on commas or punctuation.\n\n"
        f"Style: {visual_style}.\n\n"
        "Composition: place the important subject and narrative action "
        "mainly in the upper and middle area. Keep the lower 25 percent "
        "visually calm and relatively low-detail as a safe area for a "
        "later bopomofo Chinese text overlay. Do not place important "
        "faces, hands, animals, key props, or narrative action in that "
        "lower text-safe area.\n\n"
        "Continuity: all scenes with the same character consistency key "
        "must look like pages from the same picture book. Keep recurring "
        "character appearance, clothing, hairstyle, architecture, "
        "season, palette, and time-of-day logic consistent.\n\n"
        "Forbidden: "
        f"{negative_rules}."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build image prompt manifest without calling an image API."
    )
    parser.add_argument(
        "--age",
        type=int,
        nargs="+",
        choices=(6, 7, 8, 9),
        default=[6],
        help="Exact recommended-age groups. Default: 6.",
    )
    parser.add_argument("--poem-id", type=int)
    parser.add_argument("--start-poem-id", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument("--scenes", default="data/scenes.csv")
    parser.add_argument(
        "--style",
        default="config/image_style_age6_v1.json",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV. Default derives from age filter.",
    )
    args = parser.parse_args()

    poems = read_csv(Path(args.poems))
    scenes = read_csv(Path(args.scenes))
    style = read_style(Path(args.style))

    age_set = set(args.age)
    selected_poems = [
        row for row in poems
        if int(row["recommended_age"]) in age_set
    ]

    if args.poem_id is not None:
        selected_poems = [
            row for row in selected_poems
            if int(row["poem_id"]) == args.poem_id
        ]

    if args.start_poem_id is not None:
        selected_poems = [
            row for row in selected_poems
            if int(row["poem_id"]) >= args.start_poem_id
        ]

    selected_poems.sort(key=lambda row: int(row["poem_id"]))

    if args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit must be > 0")
        selected_poems = selected_poems[: args.limit]

    poem_by_id = {
        row["poem_id"]: row
        for row in selected_poems
    }

    output_rows: list[dict[str, Any]] = []

    for scene in scenes:
        poem = poem_by_id.get(scene["poem_id"])
        if poem is None:
            continue

        pid = int(poem["poem_id"])
        scene_no = int(scene["scene_no"])
        output_image = (
            f"assets/p{pid:03d}/s{scene_no:02d}/image/background.webp"
        )

        output_rows.append(
            {
                "scene_id": scene["scene_id"],
                "poem_id": pid,
                "title": poem["title"],
                "author": poem["author"],
                "recommended_age": int(poem["recommended_age"]),
                "scene_no": scene_no,
                "original_line": scene["original_line"],
                "child_explanation_line": scene[
                    "child_explanation_line"
                ],
                "style_id": style["style_id"],
                "aspect_ratio": style["aspect_ratio"],
                "character_consistency_key": scene[
                    "character_consistency_key"
                ],
                "output_image": output_image,
                "image_prompt": build_prompt(poem, scene, style),
            }
        )

    if args.output:
        output_path = Path(args.output)
    elif len(age_set) == 1:
        age = next(iter(age_set))
        output_path = Path(f"data/image_prompts_age{age}.csv")
    else:
        ages = "_".join(str(age) for age in sorted(age_set))
        output_path = Path(f"data/image_prompts_age{ages}.csv")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PROMPT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    poem_count = len(
        {row["poem_id"] for row in output_rows}
    )
    print(
        f"poems={poem_count} scenes={len(output_rows)} "
        f"style={style['style_id']}"
    )
    print(f"output={output_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
