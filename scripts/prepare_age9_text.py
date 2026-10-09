#!/usr/bin/env python3
"""Prepare the Age 9 text/data layer without calling external APIs.

This is a deterministic preprocessing step. It:
- validates the 138-poem / 1,050-Scene Age 9 cohort;
- preserves every physical line, including the six blank separator Scenes;
- upgrades Age 9 visual plans to source-grounded visual_plan_v2 in DRAFT state;
- writes a compact Age 9 text inventory for pronunciation / semantic review.

It never changes canonical title, author, poem content, child explanation, or
Scene boundaries.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

AGE = 9
EXPECTED_POEMS = 138
EXPECTED_SCENES = 1050
EXPECTED_BLANK_SCENES = 6

POEMS_PATH = Path("data/poems.csv")
SCENES_PATH = Path("data/scenes.csv")
INVENTORY_PATH = Path("data/age9_text_inventory.json")


def physical_lines(value: str) -> list[str]:
    return (
        (value or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(
    path: Path,
    headers: list[str],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=headers,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def scene_rows(
    scenes: list[dict[str, str]],
    poem_id: int,
) -> list[dict[str, str]]:
    rows = [
        row for row in scenes
        if int(row["poem_id"]) == poem_id
    ]
    rows.sort(key=lambda row: int(row["scene_no"]))
    return rows


def source_grounded_plan(
    poem: dict[str, str],
    poem_scenes: list[dict[str, str]],
) -> dict[str, Any]:
    world = (
        f"唐代〈{poem['title']}〉的共同詩意世界；保持古代中國時代感與全詩氣氛一致。"
        "具體人物、景物、地點與動作只依當前 Scene 的原詩和既有兒童解釋建立，"
        "不提前加入其他 Scene 事件，也不把比喻、典故、夢境或想像強行畫成字面事實。"
    )

    planned_scenes: list[dict[str, Any]] = []
    for scene in poem_scenes:
        original = scene["original_line"]
        explanation = scene["child_explanation_line"]
        blank = not original.strip()

        if blank:
            planned_scenes.append(
                {
                    "id": scene["scene_id"],
                    "setting": "blank separator",
                    "time": "not applicable",
                    "mood": "structural separator",
                    "entities": [],
                    "actions": [],
                    "visual_focus": "blank separator; no new semantic content",
                    "note": (
                        "這是原始資料中的空白分隔 Scene，必須保留 Scene ID 與順序；"
                        "不得從前後句補入新語意，也不得刪除此 Scene。"
                    ),
                }
            )
            continue

        planned_scenes.append(
            {
                "id": scene["scene_id"],
                "setting": "依當前原詩與兒童解釋直接建立",
                "time": "原詩有明示才具體化，未明示不額外推定",
                "mood": "依當前原詩與兒童解釋",
                "entities": [],
                "actions": [],
                "visual_focus": explanation,
                "note": (
                    "只呈現本句原詩與既有兒童解釋可支持的內容；"
                    "不提前加入其他 Scene 的主要事件。"
                    "戰爭、死亡、飲酒、成人情慾、鬼神、刑罰等內容若出現，"
                    "後續 semantic review 必須決定兒童安全的具體視覺處理。"
                ),
            }
        )

    return {
        "v": "visual_plan_v2",
        "semantic_mode": "source_grounded_scene_v2",
        "world": world,
        "entities": [],
        "continuity": {
            "mode": "soft_world_continuity",
            "recurring_entities": [],
            "note": (
                "同首詩共用時代感與整體氣氛；各 Scene 構圖獨立，"
                "不為 continuity 犧牲本句語意。"
            ),
        },
        "scenes": planned_scenes,
    }


def main() -> int:
    headers, poems = read_csv(POEMS_PATH)
    _, scenes = read_csv(SCENES_PATH)

    selected = [
        poem for poem in poems
        if int(poem["recommended_age"]) == AGE
    ]
    selected.sort(key=lambda poem: int(poem["poem_id"]))

    errors: list[str] = []
    inventory_poems: list[dict[str, Any]] = []
    total_scenes = 0
    blank_scenes: list[str] = []
    scene_distribution: Counter[int] = Counter()

    for poem in selected:
        pid = int(poem["poem_id"])
        content_lines = physical_lines(poem["content"])
        explanation_lines = physical_lines(
            poem["child_explanation_6_8"]
        )
        pscenes = scene_rows(scenes, pid)

        total_scenes += len(pscenes)
        scene_distribution[len(pscenes)] += 1

        if len(content_lines) != len(explanation_lines):
            errors.append(
                f"p{pid:03d}: content_lines={len(content_lines)} "
                f"explanation_lines={len(explanation_lines)}"
            )
        if len(pscenes) != len(content_lines):
            errors.append(
                f"p{pid:03d}: scenes={len(pscenes)} "
                f"content_lines={len(content_lines)}"
            )

        for index, scene in enumerate(pscenes):
            expected_no = index + 1
            expected_id = f"p{pid:03d}_s{expected_no:02d}"
            if scene["scene_id"] != expected_id:
                errors.append(
                    f"p{pid:03d}: scene_id={scene['scene_id']} "
                    f"expected={expected_id}"
                )
            if index < len(content_lines):
                if scene["original_line"] != content_lines[index]:
                    errors.append(
                        f"{expected_id}: original_line mismatch"
                    )
            if index < len(explanation_lines):
                if (
                    scene["child_explanation_line"]
                    != explanation_lines[index]
                ):
                    errors.append(
                        f"{expected_id}: explanation mismatch"
                    )
            if not scene["original_line"].strip():
                blank_scenes.append(scene["scene_id"])

        plan = source_grounded_plan(poem, pscenes)
        world = str(plan["world"])

        poem["visual_plan_version"] = "visual_plan_v2"
        poem["visual_plan_status"] = "draft"
        poem["visual_plan_json"] = json.dumps(
            plan,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        poem["visual_world"] = world
        poem["visual_scene_text"] = "\n".join(
            (
                f"{scene['id']}｜場景：{scene['setting']}｜"
                f"畫面核心：{scene['visual_focus']}"
            )
            for scene in plan["scenes"]
        )
        poem["visual_object_text"] = (
            "source-grounded v2：不使用規則式關鍵字抽取硬猜 entity；"
            "人物、景物與動作由各 Scene 原詩與兒童解釋直接驅動。"
        )
        poem["visual_continuity_text"] = (
            "同首詩共用時代感與整體氣氛；各 Scene 構圖獨立；"
            "blank separator Scene 保留但不增加新語意。"
        )

        inventory_poems.append(
            {
                "poem_id": pid,
                "title": poem["title"],
                "author": poem["author"],
                "poem_type": poem["poem_type"],
                "content_lines": content_lines,
                "child_explanation_lines": explanation_lines,
                "scene_ids": [row["scene_id"] for row in pscenes],
                "blank_scene_ids": [
                    row["scene_id"]
                    for row in pscenes
                    if not row["original_line"].strip()
                ],
            }
        )

    if len(selected) != EXPECTED_POEMS:
        errors.append(
            f"age9 poems={len(selected)} expected={EXPECTED_POEMS}"
        )
    if total_scenes != EXPECTED_SCENES:
        errors.append(
            f"age9 scenes={total_scenes} expected={EXPECTED_SCENES}"
        )
    if len(blank_scenes) != EXPECTED_BLANK_SCENES:
        errors.append(
            f"blank scenes={len(blank_scenes)} "
            f"expected={EXPECTED_BLANK_SCENES}"
        )

    inventory = {
        "version": "age9_text_inventory_v1",
        "scope": {
            "recommended_age": AGE,
            "poems": len(selected),
            "scenes": total_scenes,
            "blank_scenes": len(blank_scenes),
        },
        "scene_distribution": {
            str(key): scene_distribution[key]
            for key in sorted(scene_distribution)
        },
        "blank_scene_ids": blank_scenes,
        "errors": errors,
        "poems": inventory_poems,
    }

    INVENTORY_PATH.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Age 9 text preprocessing")
    print(f"poems={len(selected)}")
    print(f"scenes={total_scenes}")
    print(f"blank_scenes={len(blank_scenes)}")
    print(
        "scene_distribution="
        + ",".join(
            f"{key}:{scene_distribution[key]}"
            for key in sorted(scene_distribution)
        )
    )
    print(f"errors={len(errors)}")
    print(f"inventory={INVENTORY_PATH.as_posix()}")

    if errors:
        for error in errors:
            print("  " + error)
        print("BLOCKED")
        return 1

    write_csv(POEMS_PATH, headers, poems)
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
