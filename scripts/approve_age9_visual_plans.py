#!/usr/bin/env python3
"""Apply deterministic Age 9 visual semantic/safety review.

The canonical poem text, child explanation, and Scene boundaries are immutable.
This script only:
- attaches category-level safety notes to Scene visual plans;
- applies curated scene-specific overrides for high-risk/ambiguous material;
- records safety_tags for traceability;
- promotes Age 9 visual plans from draft to approved after structural checks;
- writes an API-free review report.

No image or TTS API calls are made.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

AGE = 9
EXPECTED_POEMS = 138
EXPECTED_SCENES = 1050
EXPECTED_BLANK_SCENES = 6

DEFAULT_POEMS = Path("data/poems.csv")
DEFAULT_SCENES = Path("data/scenes.csv")
DEFAULT_POLICY = Path("data/age9_visual_safety_policy.json")
DEFAULT_REPORT = Path("data/age9_visual_safety_report.json")


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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scene_rows(
    scenes: list[dict[str, str]],
    poem_id: int,
) -> list[dict[str, str]]:
    selected = [
        row for row in scenes
        if int(row["poem_id"]) == poem_id
    ]
    selected.sort(key=lambda row: int(row["scene_no"]))
    return selected


def physical_lines(value: str) -> list[str]:
    return (
        (value or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )


def detect_categories(
    text: str,
    categories: dict[str, dict[str, Any]],
) -> list[str]:
    matched: list[str] = []
    for category, spec in categories.items():
        triggers = [
            str(value)
            for value in spec.get("triggers", [])
            if str(value)
        ]
        if any(trigger in text for trigger in triggers):
            matched.append(category)
    return matched


def merge_note(
    *,
    default_note: str,
    category_names: list[str],
    categories: dict[str, dict[str, Any]],
    override: str | None,
) -> str:
    notes = [default_note]
    for category in category_names:
        note = str(categories[category].get("note") or "").strip()
        if note and note not in notes:
            notes.append(note)
    if override:
        override = override.strip()
        if override and override not in notes:
            notes.append(override)
    return " ".join(notes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poems", default=str(DEFAULT_POEMS))
    parser.add_argument("--scenes", default=str(DEFAULT_SCENES))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    poems_path = Path(args.poems)
    scenes_path = Path(args.scenes)
    policy_path = Path(args.policy)
    report_path = Path(args.report)

    headers, poems = read_csv(poems_path)
    _, scenes = read_csv(scenes_path)
    policy = load_json(policy_path)

    default_note = str(policy["default_note"])
    categories = dict(policy.get("categories") or {})
    overrides = {
        str(key): str(value)
        for key, value in (policy.get("scene_overrides") or {}).items()
    }

    selected = [
        poem for poem in poems
        if int(poem["recommended_age"]) == AGE
    ]
    selected.sort(key=lambda poem: int(poem["poem_id"]))

    errors: list[str] = []
    category_counts: Counter[str] = Counter()
    overridden_scene_count = 0
    tagged_scene_count = 0
    blank_scene_count = 0
    total_scenes = 0
    reviewed_scene_ids: set[str] = set()

    for poem in selected:
        pid = int(poem["poem_id"])
        title = poem["title"]
        poem_lines = physical_lines(poem["content"])
        explanation_lines = physical_lines(
            poem["child_explanation_6_8"]
        )
        pscenes = scene_rows(scenes, pid)

        if len(pscenes) != len(poem_lines):
            errors.append(
                f"p{pid:03d}: scenes={len(pscenes)} "
                f"content_lines={len(poem_lines)}"
            )
            continue
        if len(explanation_lines) != len(poem_lines):
            errors.append(
                f"p{pid:03d}: explanation_lines="
                f"{len(explanation_lines)} content_lines={len(poem_lines)}"
            )
            continue

        try:
            plan = json.loads(poem["visual_plan_json"])
        except Exception as exc:
            errors.append(
                f"p{pid:03d}: invalid visual_plan_json: {exc}"
            )
            continue

        if str(plan.get("v")) != "visual_plan_v2":
            errors.append(
                f"p{pid:03d}: visual plan version={plan.get('v')!r}"
            )
        if (
            str(plan.get("semantic_mode"))
            != "source_grounded_scene_v2"
        ):
            errors.append(
                f"p{pid:03d}: semantic_mode="
                f"{plan.get('semantic_mode')!r}"
            )

        plan_scenes = plan.get("scenes")
        if not isinstance(plan_scenes, list):
            errors.append(f"p{pid:03d}: plan.scenes is not a list")
            continue
        if len(plan_scenes) != len(pscenes):
            errors.append(
                f"p{pid:03d}: plan_scenes={len(plan_scenes)} "
                f"scenes={len(pscenes)}"
            )
            continue

        for index, scene in enumerate(pscenes):
            plan_scene = plan_scenes[index]
            expected_id = scene["scene_id"]
            actual_id = str(
                plan_scene.get("id")
                or plan_scene.get("scene_id")
                or ""
            )
            if actual_id != expected_id:
                errors.append(
                    f"p{pid:03d}: plan scene id {actual_id!r} "
                    f"expected {expected_id!r}"
                )
                continue
            if scene["original_line"] != poem_lines[index]:
                errors.append(
                    f"{expected_id}: original_line mismatch"
                )
            if (
                scene["child_explanation_line"]
                != explanation_lines[index]
            ):
                errors.append(
                    f"{expected_id}: explanation mismatch"
                )

            total_scenes += 1
            original = scene["original_line"]
            explanation = scene["child_explanation_line"]

            if not original.strip():
                blank_scene_count += 1
                plan_scene["safety_tags"] = ["blank_separator"]
                plan_scene["note"] = (
                    "這是原始資料中的空白分隔 Scene，必須保留 Scene ID "
                    "與順序；不得從前後句補入新語意，也不得新增敏感內容。"
                )
                reviewed_scene_ids.add(expected_id)
                continue

            combined = original + "\n" + explanation
            category_names = detect_categories(
                combined,
                categories,
            )
            for category in category_names:
                category_counts[category] += 1
            if category_names:
                tagged_scene_count += 1

            override = overrides.get(expected_id)
            if override:
                overridden_scene_count += 1

            plan_scene["safety_tags"] = (
                category_names
                + (["scene_override"] if override else [])
            )
            plan_scene["note"] = merge_note(
                default_note=default_note,
                category_names=category_names,
                categories=categories,
                override=override,
            )
            reviewed_scene_ids.add(expected_id)

        plan["safety_policy"] = str(policy["version"])
        plan["safety_review"] = {
            "status": "approved",
            "mode": "deterministic_scene_semantic_review",
            "policy": str(policy["version"]),
        }

        poem["visual_plan_status"] = "approved"
        poem["visual_plan_version"] = "visual_plan_v2"
        poem["visual_plan_json"] = json.dumps(
            plan,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        poem["visual_object_text"] = (
            "source-grounded v2；人物、景物與動作由當前 Scene "
            "原詩與兒童解釋直接驅動；Age 9 safety policy 已套用。"
        )
        poem["visual_continuity_text"] = (
            "同首詩共用時代感與整體氣氛；各 Scene 構圖獨立。"
            "敏感內容依 scene safety_tags 與 note 做兒童友善轉譯。"
        )

    unknown_overrides = sorted(
        set(overrides) - reviewed_scene_ids
    )
    if unknown_overrides:
        errors.append(
            "policy scene_overrides not found in Age 9 cohort: "
            + ", ".join(unknown_overrides)
        )

    if len(selected) != EXPECTED_POEMS:
        errors.append(
            f"poems={len(selected)} expected={EXPECTED_POEMS}"
        )
    if total_scenes != EXPECTED_SCENES:
        errors.append(
            f"scenes={total_scenes} expected={EXPECTED_SCENES}"
        )
    if blank_scene_count != EXPECTED_BLANK_SCENES:
        errors.append(
            f"blank_scenes={blank_scene_count} "
            f"expected={EXPECTED_BLANK_SCENES}"
        )

    report = {
        "version": "age9_visual_safety_report_v1",
        "policy": str(policy["version"]),
        "recommended_age": AGE,
        "poems": len(selected),
        "scenes": total_scenes,
        "blank_scenes": blank_scene_count,
        "tagged_scenes": tagged_scene_count,
        "scene_overrides": overridden_scene_count,
        "category_counts": dict(sorted(category_counts.items())),
        "status": "PASS" if not errors else "BLOCKED",
        "errors": errors,
    }

    print("Age 9 visual semantic/safety review")
    for key in (
        "poems",
        "scenes",
        "blank_scenes",
        "tagged_scenes",
        "scene_overrides",
    ):
        print(f"{key}={report[key]}")
    print(
        "category_counts="
        + ",".join(
            f"{key}:{value}"
            for key, value in sorted(category_counts.items())
        )
    )
    print(f"errors={len(errors)}")

    if errors:
        for error in errors:
            print("  " + error)
        print("BLOCKED")
        return 1

    if args.check:
        print("CHECK PASS")
        return 0

    write_csv(poems_path, headers, poems)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"report={report_path.as_posix()}")
    print("APPLY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
