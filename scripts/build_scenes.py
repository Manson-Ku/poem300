#!/usr/bin/env python3
"""Build scene-level CSV from poem-level SSOT.

Rule:
    one physical line in content == one scene
    content line count must equal child_explanation_6_8 line count
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

SCENE_FIELDS = [
    "scene_id",
    "poem_id",
    "scene_no",
    "original_line",
    "child_explanation_line",
    "image_prompt_zh",
    "style_group",
    "character_consistency_key",
    "aspect_ratio",
    "duration_sec",
    "motion_hint",
    "background_image",
    "text_overlay_image",
    "tts_audio",
    "image_status",
]


def normalize_newlines(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def build(input_csv: Path, output_csv: Path) -> tuple[int, int]:
    with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
        poems = list(csv.DictReader(f))

    scenes: list[dict[str, object]] = []

    for poem in poems:
        poem_id = int(poem["poem_id"])
        original_lines = normalize_newlines(poem["content"]).split("\n")
        explanation_lines = normalize_newlines(
            poem["child_explanation_6_8"]
        ).split("\n")

        if len(original_lines) != len(explanation_lines):
            raise ValueError(
                f"poem_id={poem_id}: content has {len(original_lines)} lines, "
                f"but child explanation has {len(explanation_lines)} lines"
            )

        for scene_no, (original, explanation) in enumerate(
            zip(original_lines, explanation_lines), start=1
        ):
            scenes.append(
                {
                    "scene_id": f"p{poem_id:03d}_s{scene_no:02d}",
                    "poem_id": poem_id,
                    "scene_no": scene_no,
                    "original_line": original,
                    "child_explanation_line": explanation,
                    "image_prompt_zh": "",
                    "style_group": "storybook_cn_v1",
                    "character_consistency_key": f"p{poem_id:03d}",
                    "aspect_ratio": "16:9",
                    "duration_sec": "",
                    "motion_hint": "",
                    "background_image": "",
                    "text_overlay_image": "",
                    "tts_audio": "",
                    "image_status": "pending",
                }
            )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCENE_FIELDS)
        writer.writeheader()
        writer.writerows(scenes)

    return len(poems), len(scenes)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/poems.csv")
    parser.add_argument("--output", default="data/scenes.csv")
    args = parser.parse_args()

    poem_count, scene_count = build(Path(args.input), Path(args.output))
    print(f"poems={poem_count} scenes={scene_count}")


if __name__ == "__main__":
    main()
