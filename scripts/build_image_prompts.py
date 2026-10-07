#!/usr/bin/env python3
"""Build style-specific prompt manifests for independent poem Scenes.

The semantic source is data/poems.csv + data/scenes.csv.
Each physical poem line is an independent image Scene. The poem-level world
is shared soft context; the current line remains the semantic priority.
Style A/B may change rendering language only.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

FIELDS = [
    "scene_id",
    "poem_id",
    "title",
    "author",
    "recommended_age",
    "scene_no",
    "original_line",
    "child_explanation_line",
    "visual_plan_version",
    "visual_plan_status",
    "style_id",
    "aspect_ratio",
    "output_image",
    "scene_plan_json",
    "image_prompt",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_style(selector: str, registry_path: Path) -> dict[str, Any]:
    registry = read_json(registry_path)
    normalized = selector.strip()

    if normalized.upper() in registry["styles"]:
        item = registry["styles"][normalized.upper()]
        return read_json(Path(item["config"]))

    for item in registry["styles"].values():
        if item["style_id"] == normalized:
            return read_json(Path(item["config"]))

    raise ValueError(
        f"Unknown style {selector!r}. Use A, B, or a registered style_id."
    )


def find_scene_plan(
    visual_plan: dict[str, Any],
    scene_id: str,
) -> dict[str, Any]:
    for scene in visual_plan.get("scenes", []):
        if scene.get("id") == scene_id or scene.get("scene_id") == scene_id:
            return scene
    raise ValueError(f"{scene_id}: missing from visual_plan_json")


def entity_details(
    visual_plan: dict[str, Any],
    scene_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {
        item["id"]: item
        for item in visual_plan.get("entities", [])
        if item.get("id")
    }

    refs = (
        scene_plan.get("entities")
        or scene_plan.get("entity_states")
        or []
    )

    ids: list[str] = []
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, str):
                ids.append(ref)
            elif isinstance(ref, dict) and ref.get("id"):
                ids.append(ref["id"])

    return [by_id[item_id] for item_id in ids if item_id in by_id]


def style_description(style: dict[str, Any]) -> str:
    parts: list[str] = []

    label = style.get("label_zh_tw")
    if label:
        parts.append(label)

    for key in ("medium", "visual_character"):
        values = style.get(key) or []
        if values:
            parts.append("、".join(values))

    color = style.get("color_language") or {}
    if color.get("palette"):
        parts.append(f"色彩：{color['palette']}")

    shapes = style.get("shape_language") or {}
    if shapes.get("silhouette"):
        parts.append(f"造型：{shapes['silhouette']}")
    if shapes.get("detail_level"):
        parts.append(f"細節量：{shapes['detail_level']}")

    return "；".join(parts)


def build_prompt(
    poem: dict[str, str],
    visual_plan: dict[str, Any],
    scene_plan: dict[str, Any],
    source_scene: dict[str, str],
    style: dict[str, Any],
) -> str:
    """Build one independent Scene prompt from the shared poem world."""
    world = (
        (poem.get("visual_world") or "").strip()
        or str(visual_plan.get("world") or "").strip()
    )
    entities = entity_details(visual_plan, scene_plan)
    exclusions = style.get("global_exclusions") or []

    entity_lines = []
    for item in entities:
        detail = item.get("detail")
        text = f"- {item.get('label', item.get('id'))}"
        if detail:
            text += f"：{detail}"
        entity_lines.append(text)

    prompt_parts = [
        "請生成一張 16:9、1K、完整滿版的唐詩兒童繪本插畫。",
        "",
        "核心原則：",
        "- 這是一張獨立 Scene 圖片。",
        "- data/scenes.csv 的一個實際換行就是一個 Scene；只表達本句，不主動補齊前一句或下一句。",
        "- 整首詩的世界觀只提供共同時代、空間與氣氛；本張圖片永遠以目前這一句詩為最高優先。",
        "- 兒童解釋只用來幫助理解原詩，不得凌駕或擴寫原詩。",
        "- 不需要延續上一張圖片的鏡位、姿勢或物件位置；可以自由選擇最能說清楚本句的構圖。",
        "",
        "整首詩共同世界觀：",
        world or "依本詩 visual plan 的世界設定自然建立。",
        "",
        f"本 Scene：{source_scene['scene_id']}",
        f"本句原詩：{source_scene['original_line']}",
        f"兒童解釋：{source_scene['child_explanation_line']}",
    ]

    for key, label in (
        ("setting", "本幕場景"),
        ("time", "本幕時間"),
        ("weather", "本幕天候"),
        ("mood", "本幕情緒"),
        ("focus", "本幕視覺核心"),
        ("visual_focus", "本幕視覺核心"),
        ("action", "本幕動作"),
    ):
        value = scene_plan.get(key)
        if value:
            if isinstance(value, list):
                value = "、".join(str(x) for x in value)
            prompt_parts.append(f"{label}：{value}")

    actions = scene_plan.get("actions")
    if actions:
        prompt_parts.append(
            "本幕動作：" + "、".join(str(x) for x in actions)
        )

    note = scene_plan.get("note")
    if note:
        prompt_parts.append(f"本幕特別注意：{note}")

    if entity_lines:
        prompt_parts += [
            "",
            "本幕可使用的人物／場景／物件：",
            *entity_lines,
        ]

    prompt_parts += [
        "",
        f"畫風：{style_description(style)}",
        "",
        "生成要求：",
        "- 第一眼要能幫幼童理解目前這一句詩。",
        "- 世界觀一致即可，不追求跨 Scene 的角色姿勢、房間細節、鏡位或構圖完全一致。",
        "- 不要因為知道整首詩而提前加入其他句子的主要事件。",
        "- 圖片自然延伸到四邊，不替後續字幕或注音預留區域。",
        "- 聲音與情緒使用人物姿態、表情、環境與光線表現，不使用符號化圖示。",
        "",
        "禁止出現：",
        "- 任何文字、題字、書法",
        "- 字幕、注音、Logo、UI",
        "- 對話框、思想泡泡、音符、漫畫聲效符號",
        "- 白色字幕條、刻意留白區、圓角卡片式畫框",
        "- 多格漫畫、拼貼、分割畫面",
    ]

    if exclusions:
        prompt_parts += [
            *[
                f"- {item}"
                for item in exclusions
                if str(item).strip()
            ]
        ]

    return "\n".join(prompt_parts)

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build image prompt manifest from poems.csv visual_plan_json. "
            "No image API is called."
        )
    )
    parser.add_argument(
        "--age",
        type=int,
        nargs="+",
        choices=(6, 7, 8, 9),
        default=[6],
    )
    parser.add_argument("--poem-id", type=int)
    parser.add_argument("--start-poem-id", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument("--scenes", default="data/scenes.csv")
    parser.add_argument(
        "--registry",
        default="config/image_styles_age6.json",
    )
    parser.add_argument(
        "--style",
        default="A",
        help="A, B, or a registered style_id.",
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    poems = read_csv(Path(args.poems))
    scenes = read_csv(Path(args.scenes))
    style = resolve_style(args.style, Path(args.registry))
    style_id = style["style_id"]

    ages = set(args.age)
    selected = [
        row for row in poems
        if int(row["recommended_age"]) in ages
    ]

    if args.poem_id is not None:
        selected = [
            row for row in selected
            if int(row["poem_id"]) == args.poem_id
        ]

    if args.start_poem_id is not None:
        selected = [
            row for row in selected
            if int(row["poem_id"]) >= args.start_poem_id
        ]

    selected.sort(key=lambda row: int(row["poem_id"]))

    if args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit must be > 0")
        selected = selected[: args.limit]

    selected_by_id = {row["poem_id"]: row for row in selected}
    source_by_scene = {
        row["scene_id"]: row for row in scenes
        if row["poem_id"] in selected_by_id
    }

    output_rows: list[dict[str, Any]] = []

    for poem in selected:
        raw_plan = (poem.get("visual_plan_json") or "").strip()
        if not raw_plan:
            raise ValueError(
                f"poem_id={poem['poem_id']} has no visual_plan_json"
            )

        visual_plan = json.loads(raw_plan)
        expected_ids = [
            row["scene_id"]
            for row in scenes
            if row["poem_id"] == poem["poem_id"]
        ]
        actual_ids = [
            item.get("id") or item.get("scene_id")
            for item in visual_plan.get("scenes", [])
        ]
        if actual_ids != expected_ids:
            raise ValueError(
                f"poem_id={poem['poem_id']}: visual plan Scene IDs "
                "do not match scenes.csv"
            )

        for scene_id in expected_ids:
            source_scene = source_by_scene[scene_id]
            scene_plan = find_scene_plan(visual_plan, scene_id)
            pid = int(poem["poem_id"])
            scene_no = int(source_scene["scene_no"])
            output_image = (
                f"assets/p{pid:03d}/s{scene_no:02d}/image/"
                f"{style_id}/background.webp"
            )

            output_rows.append(
                {
                    "scene_id": scene_id,
                    "poem_id": pid,
                    "title": poem["title"],
                    "author": poem["author"],
                    "recommended_age": int(poem["recommended_age"]),
                    "scene_no": scene_no,
                    "original_line": source_scene["original_line"],
                    "child_explanation_line": source_scene[
                        "child_explanation_line"
                    ],
                    "visual_plan_version": poem[
                        "visual_plan_version"
                    ],
                    "visual_plan_status": poem[
                        "visual_plan_status"
                    ],
                    "style_id": style_id,
                    "aspect_ratio": "16:9",
                    "output_image": output_image,
                    "scene_plan_json": json.dumps(
                        scene_plan,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "image_prompt": build_prompt(
                        poem,
                        visual_plan,
                        scene_plan,
                        source_scene,
                        style,
                    ),
                }
            )

    if args.output:
        output_path = Path(args.output)
    else:
        age_label = "_".join(str(x) for x in sorted(ages))
        slot = (style.get("ab_slot") or style_id).lower()
        output_path = Path(
            f"data/image_prompts_age{age_label}_{slot}.csv"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(
        f"poems={len(selected)} scenes={len(output_rows)} "
        f"style={style_id}"
    )
    print(f"output={output_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
