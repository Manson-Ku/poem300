#!/usr/bin/env python3
"""Audit one recommended-age cohort before production API calls.

This gate is API-free. It validates the SSOT relationship between
data/poems.csv, data/scenes.csv and visual_plan_json, then reports
whether the cohort is structurally valid and whether it is ready for
production generation.

Structural PASS does not imply semantic approval. Production readiness
requires approved visual plans plus a substantive poem-world context.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


PLACEHOLDER_WORLDS = {
    "",
    "依各 Scene 視覺計畫",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def physical_lines(value: str) -> list[str]:
    return (
        (value or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )


def scene_rows(
    rows: list[dict[str, str]],
    poem_id: int,
) -> list[dict[str, str]]:
    selected = [
        row for row in rows
        if int(row["poem_id"]) == poem_id
    ]
    selected.sort(key=lambda row: int(row["scene_no"]))
    return selected


def plan_scene_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("scene_id") or "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit an age cohort before resource production."
    )
    parser.add_argument(
        "--age",
        type=int,
        choices=(6, 7, 8, 9),
        required=True,
    )
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument("--scenes", default="data/scenes.csv")
    parser.add_argument(
        "--production-ready",
        action="store_true",
        help=(
            "Return non-zero unless every selected visual plan is "
            "approved and has a substantive poem world."
        ),
    )
    args = parser.parse_args()

    poems = [
        row for row in read_csv(Path(args.poems))
        if int(row["recommended_age"]) == args.age
    ]
    poems.sort(key=lambda row: int(row["poem_id"]))
    scenes = read_csv(Path(args.scenes))

    if not poems:
        print(f"ERROR: no poems found for age {args.age}")
        return 2

    structural_errors: list[str] = []
    production_blockers: list[str] = []
    warnings: list[str] = []

    status_counts: Counter[str] = Counter()
    version_counts: Counter[str] = Counter()
    scene_distribution: Counter[int] = Counter()

    age_scene_count = 0
    nonblank_scene_count = 0
    blank_scene_count = 0
    placeholder_world_count = 0
    plan_world_count = 0
    empty_object_text_count = 0
    empty_continuity_text_count = 0
    empty_focus_scene_count = 0
    empty_entity_scene_count = 0
    all_semantics_empty_scene_count = 0

    for poem in poems:
        pid = int(poem["poem_id"])
        title = poem["title"]
        poem_lines = physical_lines(poem.get("content", ""))
        explanation_lines = physical_lines(
            poem.get("child_explanation_6_8", "")
        )
        poem_scenes = scene_rows(scenes, pid)

        age_scene_count += len(poem_scenes)
        scene_distribution[len(poem_scenes)] += 1
        nonblank_scene_count += sum(
            bool(row.get("original_line", "").strip())
            for row in poem_scenes
        )
        blank_scene_count += sum(
            not row.get("original_line", "").strip()
            for row in poem_scenes
        )

        if len(poem_scenes) != len(poem_lines):
            structural_errors.append(
                f"p{pid:03d} {title}: scenes={len(poem_scenes)} "
                f"content_lines={len(poem_lines)}"
            )
        if len(explanation_lines) != len(poem_lines):
            structural_errors.append(
                f"p{pid:03d} {title}: explanation_lines="
                f"{len(explanation_lines)} content_lines={len(poem_lines)}"
            )

        for index, row in enumerate(poem_scenes):
            expected_no = index + 1
            expected_id = f"p{pid:03d}_s{expected_no:02d}"
            if int(row["scene_no"]) != expected_no:
                structural_errors.append(
                    f"p{pid:03d}: scene_no {row['scene_no']} "
                    f"expected {expected_no}"
                )
            if row.get("scene_id") != expected_id:
                structural_errors.append(
                    f"p{pid:03d}: scene_id {row.get('scene_id')!r} "
                    f"expected {expected_id!r}"
                )
            if index < len(poem_lines):
                if row.get("original_line", "") != poem_lines[index]:
                    structural_errors.append(
                        f"{expected_id}: original_line mismatch"
                    )
            if index < len(explanation_lines):
                if (
                    row.get("child_explanation_line", "")
                    != explanation_lines[index]
                ):
                    structural_errors.append(
                        f"{expected_id}: explanation mismatch"
                    )

        status = (
            poem.get("visual_plan_status", "").strip().lower()
            or "<blank>"
        )
        version = (
            poem.get("visual_plan_version", "").strip()
            or "<blank>"
        )
        status_counts[status] += 1
        version_counts[version] += 1

        raw_plan = poem.get("visual_plan_json", "").strip()
        if not raw_plan:
            structural_errors.append(
                f"p{pid:03d} {title}: missing visual_plan_json"
            )
            continue

        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError as exc:
            structural_errors.append(
                f"p{pid:03d} {title}: invalid visual_plan_json: {exc}"
            )
            continue

        entities = plan.get("entities") or []
        if not isinstance(entities, list):
            structural_errors.append(
                f"p{pid:03d} {title}: entities is not a list"
            )
            entities = []

        entity_ids: list[str] = []
        for item in entities:
            if not isinstance(item, dict):
                structural_errors.append(
                    f"p{pid:03d} {title}: non-object entity"
                )
                continue
            entity_id = str(item.get("id") or "")
            if not entity_id:
                structural_errors.append(
                    f"p{pid:03d} {title}: entity missing id"
                )
                continue
            entity_ids.append(entity_id)

        if len(entity_ids) != len(set(entity_ids)):
            structural_errors.append(
                f"p{pid:03d} {title}: duplicate entity id"
            )
        entity_id_set = set(entity_ids)

        plan_scenes = plan.get("scenes")
        if not isinstance(plan_scenes, list):
            structural_errors.append(
                f"p{pid:03d} {title}: plan.scenes is not a list"
            )
            plan_scenes = []

        expected_scene_ids = [
            row["scene_id"] for row in poem_scenes
        ]
        actual_scene_ids = [
            plan_scene_id(item)
            for item in plan_scenes
            if isinstance(item, dict)
        ]
        if actual_scene_ids != expected_scene_ids:
            structural_errors.append(
                f"p{pid:03d} {title}: visual plan Scene IDs/order "
                "do not match scenes.csv"
            )

        for item in plan_scenes:
            if not isinstance(item, dict):
                continue
            scene_id = plan_scene_id(item)
            refs = (
                item.get("entities")
                or item.get("entity_states")
                or []
            )
            if not isinstance(refs, list):
                structural_errors.append(
                    f"{scene_id}: entities/entity_states is not a list"
                )
                refs = []

            ref_ids: list[str] = []
            for ref in refs:
                if isinstance(ref, str):
                    ref_ids.append(ref)
                elif isinstance(ref, dict) and ref.get("id"):
                    ref_ids.append(str(ref["id"]))
                else:
                    structural_errors.append(
                        f"{scene_id}: invalid entity reference"
                    )

            unknown = sorted(
                ref for ref in ref_ids
                if ref not in entity_id_set
            )
            if unknown:
                structural_errors.append(
                    f"{scene_id}: unknown entity refs {unknown}"
                )

            focus = item.get("focus")
            if focus is None:
                focus = item.get("visual_focus")
            actions = item.get("actions")
            if actions is None:
                actions = item.get("action")

            focus_empty = (
                focus is None
                or focus == ""
                or focus == []
            )
            entities_empty = len(ref_ids) == 0
            actions_empty = (
                actions is None
                or actions == ""
                or actions == []
            )
            empty_focus_scene_count += int(focus_empty)
            empty_entity_scene_count += int(entities_empty)
            all_semantics_empty_scene_count += int(
                focus_empty and entities_empty and actions_empty
            )

        visual_world = poem.get("visual_world", "").strip()
        plan_world = str(plan.get("world") or "").strip()
        if visual_world in PLACEHOLDER_WORLDS:
            placeholder_world_count += 1
        if plan_world:
            plan_world_count += 1

        substantive_world = (
            visual_world not in PLACEHOLDER_WORLDS
            or bool(plan_world)
        )
        if status != "approved":
            production_blockers.append(
                f"p{pid:03d} {title}: visual_plan_status={status}"
            )
        if version == "visual_plan_v1":
            production_blockers.append(
                f"p{pid:03d} {title}: visual_plan_version={version}"
            )
        if not substantive_world:
            production_blockers.append(
                f"p{pid:03d} {title}: no substantive poem world"
            )

        if not poem.get("visual_object_text", "").strip():
            empty_object_text_count += 1
        if not poem.get("visual_continuity_text", "").strip():
            empty_continuity_text_count += 1

    if all_semantics_empty_scene_count:
        warnings.append(
            f"{all_semantics_empty_scene_count} visual-plan scenes have "
            "no entities, focus, or actions; review semantics before approval"
        )
    if empty_object_text_count:
        warnings.append(
            f"{empty_object_text_count} poems have blank visual_object_text"
        )
    if empty_continuity_text_count:
        warnings.append(
            f"{empty_continuity_text_count} poems have blank "
            "visual_continuity_text; this may be legitimate"
        )

    print(f"Age {args.age} production inventory")
    print(f"poems={len(poems)}")
    print(f"scenes={age_scene_count}")
    print(f"nonblank_scenes={nonblank_scene_count}")
    print(f"blank_scenes={blank_scene_count}")
    print(
        "scene_distribution="
        + ",".join(
            f"{count}_scenes:{poem_count}"
            for count, poem_count
            in sorted(scene_distribution.items())
        )
    )
    print(
        "visual_plan_status="
        + ",".join(
            f"{key}:{value}"
            for key, value in sorted(status_counts.items())
        )
    )
    print(
        "visual_plan_version="
        + ",".join(
            f"{key}:{value}"
            for key, value in sorted(version_counts.items())
        )
    )
    print(f"placeholder_visual_world={placeholder_world_count}")
    print(f"plan_world_present={plan_world_count}")
    print(f"empty_entity_scenes={empty_entity_scene_count}")
    print(f"empty_focus_scenes={empty_focus_scene_count}")
    print(
        "all_semantics_empty_scenes="
        f"{all_semantics_empty_scene_count}"
    )
    print(f"structural_errors={len(structural_errors)}")

    if structural_errors:
        print("\nStructural errors")
        for item in structural_errors:
            print(f"  {item}")

    if warnings:
        print("\nWarnings")
        for item in warnings:
            print(f"  {item}")

    if args.production_ready:
        print(
            "\nproduction_blockers="
            f"{len(production_blockers)}"
        )
        if production_blockers:
            for item in production_blockers:
                print(f"  {item}")

    if structural_errors:
        print("\nSTRUCTURAL FAIL")
        return 1
    print("\nSTRUCTURAL PASS")

    if args.production_ready and production_blockers:
        print("PRODUCTION BLOCKED")
        return 1
    if args.production_ready:
        print("PRODUCTION PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
