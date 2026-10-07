#!/usr/bin/env python3
"""Minimal multi-turn image generator for poem300.

One style = one Interaction chain:
  scene 1 -> scene 2 -> scene 3 ...

Scene 1 establishes the visual anchor. Later scenes use
previous_interaction_id so Gemini can preserve the same visual context.

Usage/cost is appended to data/image_usage.csv.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from PIL import Image


DEFAULT_MODEL = "gemini-3.1-flash-image"

PRICING = {
    "pricing_as_of": "2026-10-07",
    "pricing_basis": "Gemini Developer API Standard paid tier",
    "input_per_million": 0.50,
    "output_non_image_per_million": 3.00,
    "output_image_per_million": 60.00,
}

LEDGER_FIELDS = [
    "usage_id",
    "run_id",
    "created_at_utc",
    "scene_id",
    "scene_no",
    "poem_id",
    "title",
    "model",
    "style_key",
    "style_id",
    "chain_index",
    "prompt_sha256",
    "interaction_id",
    "previous_interaction_id",
    "status",
    "latency_ms",
    "input_text_tokens",
    "input_image_tokens",
    "total_input_tokens",
    "output_image_tokens",
    "output_non_image_tokens",
    "total_output_tokens",
    "total_tokens",
    "estimated_input_cost_usd",
    "estimated_output_image_cost_usd",
    "estimated_output_non_image_cost_usd",
    "estimated_total_cost_usd",
    "pricing_as_of",
    "pricing_basis",
    "image_width",
    "image_height",
    "image_size_bytes",
    "output_image",
    "meta_json",
    "usage_raw_json",
    "error_message",
]


def obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    if hasattr(value, "__dict__"):
        return {
            k: jsonable(v)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    return str(value)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def modality_tokens(usage: Any, field: str, modality: str) -> int:
    items = obj_get(usage, field, []) or []
    for item in items:
        if str(obj_get(item, "modality", "")).lower() == modality.lower():
            return int(obj_get(item, "tokens", 0) or 0)
    return 0


def ensure_ledger(path: Path) -> None:
    """Create/migrate the ledger without losing older rows."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, fieldnames=LEDGER_FIELDS).writeheader()
        return

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        old_fields = reader.fieldnames or []
        old_rows = list(reader)

    if old_fields == LEDGER_FIELDS:
        return

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        for row in old_rows:
            writer.writerow(
                {field: row.get(field, "") for field in LEDGER_FIELDS}
            )


def append_ledger(path: Path, row: dict[str, Any]) -> None:
    ensure_ledger(path)
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=LEDGER_FIELDS,
            extrasaction="ignore",
        )
        writer.writerow(
            {field: row.get(field, "") for field in LEDGER_FIELDS}
        )


def resolve_style(
    style_key: str,
    registry_path: Path,
) -> dict[str, Any]:
    registry = read_json(registry_path)
    key = style_key.strip().upper()

    if key not in registry["styles"]:
        raise ValueError(
            f"Unknown style {style_key!r}; available: "
            + ", ".join(registry["styles"].keys())
        )

    entry = registry["styles"][key]
    style = read_json(Path(entry["config"]))
    style["style_key"] = key
    return style


def style_text(style: dict[str, Any]) -> str:
    parts = [
        style.get("label_zh_tw", ""),
        "、".join(style.get("medium") or []),
        "、".join(style.get("visual_character") or []),
    ]

    color = style.get("color_language") or {}
    if color.get("palette"):
        parts.append("色彩：" + color["palette"])

    rendering = style.get("environment_rendering") or {}
    if rendering.get("texture"):
        parts.append("質感：" + rendering["texture"])

    return "；".join(part for part in parts if part)


def visual_world(poem: dict[str, str], plan: dict[str, Any]) -> str:
    value = (poem.get("visual_world") or "").strip()
    if value:
        return value

    if plan.get("world"):
        return str(plan["world"])

    summary = plan.get("visual_summary") or {}
    parts = []
    if summary.get("primary_setting"):
        parts.append("主要場景：" + str(summary["primary_setting"]))
    if summary.get("main_mood"):
        parts.append("主要情緒：" + str(summary["main_mood"]))
    return "；".join(parts)


def visual_objects(poem: dict[str, str], plan: dict[str, Any]) -> str:
    value = (poem.get("visual_object_text") or "").strip()
    if value:
        return value

    entities = plan.get("entities") or []
    if entities:
        lines = []
        for entity in entities:
            label = entity.get("label") or entity.get("id") or "元素"
            detail = entity.get("detail")
            line = f"- {label}"
            if detail:
                line += f"：{detail}"
            lines.append(line)
        return "\n".join(lines)

    summary = plan.get("visual_summary") or {}
    lines = []
    for key, label in (
        ("recurring_characters", "人物"),
        ("recurring_animals", "動物"),
        ("recurring_plants", "植物"),
        ("recurring_objects", "物件"),
    ):
        values = summary.get(key) or []
        if values:
            lines.append(f"{label}：" + "、".join(values))
    return "\n".join(lines)


def visual_continuity(
    poem: dict[str, str],
    plan: dict[str, Any],
) -> str:
    value = (poem.get("visual_continuity_text") or "").strip()
    if value:
        return value

    rules = plan.get("continuity_rules") or []
    if rules:
        return "\n".join(str(rule) for rule in rules)

    continuity = plan.get("continuity") or {}
    lines = []
    if continuity.get("reuse_entities_across_scenes"):
        lines.append("跨 Scene 重複出現的人物、場景與物件維持一致。")
    if continuity.get("keep_time_weather_if_consistent"):
        lines.append("詩意連續時，維持時間、季節與天候連續。")
    return "\n".join(lines)


def find_scene_plan(
    plan: dict[str, Any],
    scene_id: str,
) -> dict[str, Any]:
    for scene in plan.get("scenes", []):
        current = scene.get("id") or scene.get("scene_id")
        if current == scene_id:
            return scene
    return {}


def scene_plan_text(
    plan: dict[str, Any],
    scene_id: str,
) -> str:
    scene = find_scene_plan(plan, scene_id)
    if not scene:
        return ""

    parts = []
    for key, label in (
        ("setting", "場景"),
        ("time", "時間"),
        ("weather", "天候"),
        ("mood", "情緒"),
        ("focus", "畫面核心"),
        ("visual_focus", "畫面核心"),
        ("action", "動作"),
    ):
        value = scene.get(key)
        if value:
            if isinstance(value, list):
                value = "、".join(str(x) for x in value)
            parts.append(f"{label}：{value}")

    actions = scene.get("actions")
    if actions:
        parts.append("動作：" + "、".join(str(x) for x in actions))

    note = scene.get("note")
    if note:
        parts.append("備註：" + str(note))

    return "\n".join(parts)


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "、".join(
            part for part in (_text_value(item) for item in value)
            if part
        )
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = _text_value(item)
            if text:
                parts.append(f"{key}：{text}")
        return "；".join(parts)
    return str(value).strip()


def _directive_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for item in value:
            lines.extend(_directive_lines(item))
        return lines
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (list, tuple, dict)):
                for text in _directive_lines(item):
                    lines.append(f"{key}：{text}")
            else:
                text = _text_value(item)
                if text:
                    lines.append(f"{key}：{text}")
        return lines
    text = _text_value(value)
    return [text] if text else []


def _unique_lines(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _scene_entity_ids(scene_plan: dict[str, Any]) -> list[str]:
    refs = (
        scene_plan.get("entities")
        or scene_plan.get("entity_states")
        or []
    )
    result: list[str] = []
    if not isinstance(refs, list):
        return result
    for ref in refs:
        if isinstance(ref, str) and ref.strip():
            result.append(ref.strip())
        elif isinstance(ref, dict) and ref.get("id"):
            result.append(str(ref["id"]).strip())
    return result


def _entity_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["id"]): item
        for item in plan.get("entities", [])
        if isinstance(item, dict) and item.get("id")
    }


def _entity_text(
    plan: dict[str, Any],
    entity_id: str,
) -> str:
    item = _entity_map(plan).get(entity_id)
    if not item:
        return entity_id

    label = item.get("label") or entity_id
    detail = item.get("detail")
    if detail:
        return f"{label}：{detail}"
    return str(label)


def recurring_visual_objects(plan: dict[str, Any]) -> str:
    """Only expose cross-Scene anchors, never future-only entities."""
    counts: dict[str, int] = {}
    for scene_plan in plan.get("scenes", []):
        if not isinstance(scene_plan, dict):
            continue
        for entity_id in set(_scene_entity_ids(scene_plan)):
            counts[entity_id] = counts.get(entity_id, 0) + 1

    continuity = plan.get("continuity") or {}
    explicit = continuity.get("recurring_entities") or []
    recurring_ids = {
        str(item)
        for item in explicit
        if str(item).strip()
    }
    recurring_ids.update(
        entity_id
        for entity_id, count in counts.items()
        if count > 1
    )

    entities = _entity_map(plan)
    ordered_ids = [
        str(item.get("id"))
        for item in plan.get("entities", [])
        if isinstance(item, dict)
        and item.get("id")
        and str(item.get("id")) in recurring_ids
    ]

    lines = []
    for entity_id in ordered_ids:
        item = entities[entity_id]
        label = item.get("label") or entity_id
        detail = item.get("detail")
        line = f"- {label}"
        if detail:
            line += f"：{detail}"
        lines.append(line)

    return "\n".join(lines)


def fallback_must_show(
    plan: dict[str, Any],
    scene_plan: dict[str, Any],
) -> list[str]:
    items: list[str] = []
    for key, label in (
        ("setting", "場景"),
        ("time", "時間"),
        ("weather", "天候"),
        ("mood", "情緒"),
        ("focus", "畫面核心"),
        ("visual_focus", "畫面核心"),
        ("action", "動作"),
        ("actions", "動作"),
    ):
        text = _text_value(scene_plan.get(key))
        if text:
            items.append(f"{label}：{text}")

    for entity_id in _scene_entity_ids(scene_plan):
        items.append(
            "本幕既定元素：" + _entity_text(plan, entity_id)
        )

    return _unique_lines(items)


def fallback_scene_delta(
    plan: dict[str, Any],
    scene_plan: dict[str, Any],
    previous_scene_plan: dict[str, Any] | None,
) -> list[str]:
    if previous_scene_plan is None:
        return []

    items: list[str] = []
    for key, label in (
        ("setting", "場景"),
        ("time", "時間"),
        ("weather", "天候"),
        ("mood", "情緒"),
        ("focus", "畫面核心"),
        ("visual_focus", "畫面核心"),
        ("action", "動作"),
        ("actions", "動作"),
    ):
        current = _text_value(scene_plan.get(key))
        previous = _text_value(previous_scene_plan.get(key))
        if current and current != previous:
            items.append(f"{label}：{current}")

    previous_ids = set(_scene_entity_ids(previous_scene_plan))
    for entity_id in _scene_entity_ids(scene_plan):
        if entity_id not in previous_ids:
            items.append(
                "本幕新增既定元素："
                + _entity_text(plan, entity_id)
            )

    note = _text_value(scene_plan.get("note"))
    if note:
        items.append("本幕備註：" + note)

    return _unique_lines(items)


def fallback_must_not_show(
    plan: dict[str, Any],
    scene_index: int,
) -> list[str]:
    scenes = [
        item
        for item in plan.get("scenes", [])
        if isinstance(item, dict)
    ]
    if scene_index < 0 or scene_index >= len(scenes):
        return []

    current = scenes[scene_index]
    current_ids = set(_scene_entity_ids(current))
    items: list[str] = []

    note = _text_value(current.get("note"))
    if note:
        items.append("不得違反本幕備註：" + note)

    seen_future_entities: set[str] = set()
    for future in scenes[scene_index + 1:]:
        future_id = (
            future.get("id")
            or future.get("scene_id")
            or "後續 Scene"
        )
        focus = _text_value(
            future.get("focus")
            or future.get("visual_focus")
        )
        if focus:
            items.append(
                f"{future_id} 才發生的畫面核心：{focus}"
            )

        action = _text_value(
            future.get("action")
            or future.get("actions")
        )
        if action:
            items.append(
                f"{future_id} 才發生的動作：{action}"
            )

        future_note = _text_value(future.get("note"))
        if future_note:
            items.append(
                f"{future_id} 才適用的備註：{future_note}"
            )

        for entity_id in _scene_entity_ids(future):
            if (
                entity_id not in current_ids
                and entity_id not in seen_future_entities
            ):
                seen_future_entities.add(entity_id)
                items.append(
                    f"{future_id} 才出現的元素："
                    + _entity_text(plan, entity_id)
                )

    return _unique_lines(items)


def resolve_scene_isolation(
    plan: dict[str, Any],
    scene_id: str,
) -> dict[str, Any]:
    scenes = [
        item
        for item in plan.get("scenes", [])
        if isinstance(item, dict)
    ]

    scene_index = -1
    scene_plan: dict[str, Any] = {}
    for index, item in enumerate(scenes):
        current_id = item.get("id") or item.get("scene_id")
        if current_id == scene_id:
            scene_index = index
            scene_plan = item
            break

    previous_scene_plan = (
        scenes[scene_index - 1]
        if scene_index > 0
        else None
    )

    if "must_show" in scene_plan:
        must_show = _directive_lines(scene_plan.get("must_show"))
        must_show_source = "visual_plan_json"
    else:
        must_show = fallback_must_show(plan, scene_plan)
        must_show_source = "deterministic_fallback"

    if "must_not_show" in scene_plan:
        must_not_show = _directive_lines(
            scene_plan.get("must_not_show")
        )
        must_not_show_source = "visual_plan_json"
    else:
        must_not_show = fallback_must_not_show(
            plan,
            scene_index,
        )
        must_not_show_source = "deterministic_fallback"

    if "scene_delta" in scene_plan:
        scene_delta = _directive_lines(
            scene_plan.get("scene_delta")
        )
        scene_delta_source = "visual_plan_json"
    else:
        scene_delta = fallback_scene_delta(
            plan,
            scene_plan,
            previous_scene_plan,
        )
        scene_delta_source = "deterministic_fallback"

    return {
        "scene_plan": scene_plan,
        "scene_index": scene_index,
        "must_show": _unique_lines(must_show),
        "must_not_show": _unique_lines(must_not_show),
        "scene_delta": _unique_lines(scene_delta),
        "must_show_source": must_show_source,
        "must_not_show_source": must_not_show_source,
        "scene_delta_source": scene_delta_source,
    }


def _append_list_section(
    blocks: list[str],
    title: str,
    items: list[str],
    empty_text: str,
) -> None:
    blocks += ["", title]
    if items:
        blocks.extend(f"- {item}" for item in items)
    else:
        blocks.append(f"- {empty_text}")


GLOBAL_VISUAL_EXCLUSIONS = [
    "音符",
    "對話框",
    "漫畫符號或漫畫聲效符號",
    "任何文字",
    "題字",
    "書法",
    "字幕",
    "注音",
    "Logo",
    "UI",
    "白色字幕條",
    "刻意空白區",
    "圓角卡片式畫框",
]


def continuity_lock_items(
    plan: dict[str, Any],
    scene_index: int,
) -> list[str]:
    """Return only entities that persist from the previous Scene."""
    scenes = [
        item
        for item in plan.get("scenes", [])
        if isinstance(item, dict)
    ]
    if scene_index <= 0 or scene_index >= len(scenes):
        return []

    previous_ids = set(_scene_entity_ids(scenes[scene_index - 1]))
    current_ids = _scene_entity_ids(scenes[scene_index])

    return _unique_lines(
        [
            _entity_text(plan, entity_id)
            for entity_id in current_ids
            if entity_id in previous_ids
        ]
    )


def build_anchor_prompt(
    scene: dict[str, str],
    isolation: dict[str, Any],
    style: dict[str, Any],
    exclusions: list[str],
) -> str:
    blocks = [
        "ANCHOR SCENE — 只畫這一幕。",
        "生成一張 16:9、1K、完整滿版的兒童繪本背景插畫。",
        "這張圖要建立後續 Scene 可延續的人物與場景外觀，但不得提前畫後續事件。",
        "",
        f"本幕詩句：{scene['original_line']}",
        f"本幕意思：{scene['child_explanation_line']}",
    ]

    _append_list_section(
        blocks,
        "必須清楚呈現：",
        isolation["must_show"],
        "依本幕詩句與兒童理解自然呈現。",
    )
    _append_list_section(
        blocks,
        "絕對不要提前出現：",
        isolation["must_not_show"],
        "任何後續 Scene 的事件、結果或專屬元素。",
    )

    blocks += [
        "",
        "畫風：",
        style_text(style),
        "",
        "構圖原則：",
        "- 只服務本幕詩意，不替下一幕做預告。",
        "- 建立清楚的人物外觀、服裝、房間／場景與主要物件，供下一幕延續 identity。",
        "- 畫面自然延伸到四邊；不要字幕安全區、白色橫條、刻意留白或卡片框。",
        "- 聲音以姿態與環境表現，不使用音符、對話框或漫畫聲效符號。",
    ]

    if exclusions:
        blocks += [
            "",
            "禁止出現：",
            *[f"- {item}" for item in exclusions],
        ]

    return "\n".join(blocks)


def build_transition_prompt(
    scene: dict[str, str],
    isolation: dict[str, Any],
    exclusions: list[str],
    continuity_locks: list[str],
) -> str:
    blocks = [
        "NEXT SCENE — 本幕詩意優先。",
        "把上一輪影像當成『人物／場景 identity 與畫風參考』，不是構圖模板。",
        "目標是讓觀者第一眼就看懂現在這一句詩，而不是把上一張只做小幅修圖。",
        "",
        f"現在要表現的詩句：{scene['original_line']}",
        f"現在這一幕的意思：{scene['child_explanation_line']}",
    ]

    _append_list_section(
        blocks,
        "這一幕相對上一幕必須發生的變化：",
        isolation["scene_delta"],
        "依現在這句詩重新安排畫面重心。",
    )
    _append_list_section(
        blocks,
        "這一幕必須清楚呈現：",
        isolation["must_show"],
        "依現在這句詩與兒童理解自然呈現。",
    )
    _append_list_section(
        blocks,
        "這一幕絕對不能出現：",
        isolation["must_not_show"],
        "本幕未要求的事件或錯誤時序。",
    )
    _append_list_section(
        blocks,
        "只鎖定以下 continuity：",
        continuity_locks,
        "沒有需要硬鎖定的上一幕實體。",
    )

    blocks += [
        "",
        "重構規則：",
        "- 保留上列 continuity 的 identity；其餘上一幕細節不必保留。",
        "- 可以主動改變人物姿勢、視線、鏡頭距離、取景與畫面重心，只要人物與固定場景仍辨識為同一個。",
        "- 不要只是沿用上一張構圖再加幾個雨滴、花瓣、雪、煙或其他小物件；本幕必須有明確的新敘事重心。",
        "- 上一幕曾經是焦點、但本幕沒有要求的元素，不得搶走現在這句詩的主題。",
        "",
        "時序硬規則：",
        "- 只畫本幕『現在』正在發生或現在看得到的狀態。",
        "- 若詩句／兒童理解提到『昨夜、之前、回想、曾經、結果』，過去事件本身不得被畫成現在仍在發生；只能表現它留在現在的結果，除非本幕明確說事件此刻仍在發生。",
        "",
        "空間硬規則：",
        "- 雨水、花瓣、雪、煙、泥濘等狀態只能出現在本幕語意指定的位置，不要擴散到無關的室內表面或物件。",
        "",
        "畫風鎖定：",
        "- 沿用上一輪完全相同的插畫風格、媒材語言與角色設計，不重新選畫風。",
        "",
        "輸出規則：",
        "- 生成一張新的 16:9、1K、完整滿版插畫。",
        "- 不要多格、拼貼、漫畫分鏡、字幕安全區、白色橫條、刻意留白或卡片框。",
        "- 聲音以姿態與環境表現，不使用音符、對話框或漫畫聲效符號。",
    ]

    if exclusions:
        blocks += [
            "",
            "禁止出現：",
            *[f"- {item}" for item in exclusions],
        ]

    return "\n".join(blocks)


def build_prompt(
    poem: dict[str, str],
    scene: dict[str, str],
    plan: dict[str, Any],
    style: dict[str, Any],
    chain_index: int,
) -> str:
    # Intentionally do not inject poem-level world/full poem/continuity prose.
    # Multi-turn context carries visual history; the runtime prompt should
    # stay narrowly scoped to the current physical poem line.
    del poem

    isolation = resolve_scene_isolation(
        plan,
        scene["scene_id"],
    )

    style_exclusions = style.get("global_exclusions") or []
    exclusions = _unique_lines(
        [str(item) for item in style_exclusions]
        + GLOBAL_VISUAL_EXCLUSIONS
    )

    if chain_index == 1:
        return build_anchor_prompt(
            scene,
            isolation,
            style,
            exclusions,
        )

    continuity_locks = continuity_lock_items(
        plan,
        isolation["scene_index"],
    )
    return build_transition_prompt(
        scene,
        isolation,
        exclusions,
        continuity_locks,
    )

def calc_cost(
    total_input_tokens: int,
    output_image_tokens: int,
    output_non_image_tokens: int,
) -> tuple[float, float, float, float]:
    input_cost = (
        total_input_tokens
        * PRICING["input_per_million"]
        / 1_000_000
    )
    image_cost = (
        output_image_tokens
        * PRICING["output_image_per_million"]
        / 1_000_000
    )
    non_image_cost = (
        output_non_image_tokens
        * PRICING["output_non_image_per_million"]
        / 1_000_000
    )
    return (
        input_cost,
        image_cost,
        non_image_cost,
        input_cost + image_cost + non_image_cost,
    )


def save_webp(
    image_bytes: bytes,
    output_path: Path,
) -> tuple[int, int, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(image_bytes)) as image:
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        width, height = image.size
        image.save(
            output_path,
            format="WEBP",
            quality=92,
            method=6,
        )
    return width, height, output_path.stat().st_size


def output_paths(
    poem_id: int,
    scene_no: int,
    style_id: str,
) -> tuple[Path, Path]:
    directory = (
        Path("assets")
        / f"p{poem_id:03d}"
        / f"s{scene_no:02d}"
        / "image"
        / style_id
    )
    return (
        directory / "background.webp",
        directory / "meta.json",
    )



def poc_output_path(
    poem_id: int,
    scene_no: int,
    style_key: str,
) -> Path:
    return (
        Path("assets")
        / "poc"
        / f"{poem_id:03d}_{style_key.upper()}_s{scene_no:02d}.webp"
    )


def copy_to_poc(
    source_path: Path,
    *,
    poem_id: int,
    scene_no: int,
    style_key: str,
) -> Path:
    target = poc_output_path(
        poem_id,
        scene_no,
        style_key,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)
    return target

def read_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_meta(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def call_image_model(
    client: genai.Client,
    *,
    model: str,
    prompt: str,
    previous_interaction_id: str | None,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "response_format": {
            "type": "image",
            "mime_type": "image/jpeg",
            "aspect_ratio": "16:9",
            "image_size": "1K",
        },
    }

    if previous_interaction_id:
        kwargs["previous_interaction_id"] = previous_interaction_id

    return client.interactions.create(**kwargs)


def generate_scene(
    client: genai.Client,
    *,
    poem: dict[str, str],
    scene: dict[str, str],
    plan: dict[str, Any],
    style: dict[str, Any],
    model: str,
    ledger: Path,
    run_id: str,
    chain_index: int,
    previous_interaction_id: str | None,
    force: bool,
) -> tuple[str | None, str, float]:
    poem_id = int(poem["poem_id"])
    scene_no = int(scene["scene_no"])
    style_key = style["style_key"]
    style_id = style["style_id"]

    output_image, meta_json = output_paths(
        poem_id,
        scene_no,
        style_id,
    )

    if output_image.exists() and meta_json.exists() and not force:
        meta = read_meta(meta_json) or {}
        interaction_id = meta.get("interaction_id")
        if not interaction_id:
            raise RuntimeError(
                f"{meta_json.as_posix()} has no interaction_id; "
                "use --force to rebuild the chain."
            )
        print(
            f"{scene['scene_id']} {style_key}: SKIP existing -> "
            f"{output_image.as_posix()}"
        )
        return interaction_id, "skipped", 0.0

    if output_image.exists() and not meta_json.exists() and not force:
        raise RuntimeError(
            f"{output_image.as_posix()} exists but meta.json is missing; "
            "use --force to rebuild the chain."
        )

    prompt = build_prompt(
        poem,
        scene,
        plan,
        style,
        chain_index,
    )

    base = {
        "usage_id": str(uuid.uuid4()),
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scene_id": scene["scene_id"],
        "scene_no": scene_no,
        "poem_id": poem_id,
        "title": poem["title"],
        "model": model,
        "style_key": style_key,
        "style_id": style_id,
        "chain_index": chain_index,
        "prompt_sha256": hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest(),
        "previous_interaction_id": previous_interaction_id or "",
        "pricing_as_of": PRICING["pricing_as_of"],
        "pricing_basis": PRICING["pricing_basis"],
        "output_image": output_image.as_posix(),
        "meta_json": meta_json.as_posix(),
    }

    started = time.perf_counter()

    try:
        interaction = call_image_model(
            client,
            model=model,
            prompt=prompt,
            previous_interaction_id=previous_interaction_id,
        )
        latency_ms = round(
            (time.perf_counter() - started) * 1000
        )

        image_block = obj_get(interaction, "output_image")
        image_b64 = obj_get(image_block, "data")

        if not image_b64:
            raise RuntimeError(
                "API response completed without output_image.data"
            )

        width, height, size_bytes = save_webp(
            base64.b64decode(image_b64),
            output_image,
        )
        poc_image = copy_to_poc(
            output_image,
            poem_id=poem_id,
            scene_no=scene_no,
            style_key=style_key,
        )

        usage = obj_get(interaction, "usage")

        input_text_tokens = modality_tokens(
            usage,
            "input_tokens_by_modality",
            "text",
        )
        input_image_tokens = modality_tokens(
            usage,
            "input_tokens_by_modality",
            "image",
        )
        output_image_tokens = modality_tokens(
            usage,
            "output_tokens_by_modality",
            "image",
        )

        total_input_tokens = int(
            obj_get(usage, "total_input_tokens", 0) or 0
        )
        total_output_tokens = int(
            obj_get(usage, "total_output_tokens", 0) or 0
        )
        total_tokens = int(
            obj_get(usage, "total_tokens", 0) or 0
        )

        if total_input_tokens == 0:
            total_input_tokens = (
                input_text_tokens + input_image_tokens
            )

        output_non_image_tokens = max(
            total_output_tokens - output_image_tokens,
            0,
        )

        (
            input_cost,
            image_cost,
            non_image_cost,
            total_cost,
        ) = calc_cost(
            total_input_tokens,
            output_image_tokens,
            output_non_image_tokens,
        )

        interaction_id = str(
            obj_get(interaction, "id", "") or ""
        )

        if not interaction_id:
            raise RuntimeError(
                "API response has no interaction id; "
                "cannot continue the multi-turn chain."
            )

        meta = {
            "poem_id": poem_id,
            "title": poem["title"],
            "scene_id": scene["scene_id"],
            "scene_no": scene_no,
            "style_key": style_key,
            "style_id": style_id,
            "chain_index": chain_index,
            "model": model,
            "interaction_id": interaction_id,
            "previous_interaction_id": (
                previous_interaction_id
            ),
            "prompt_sha256": base["prompt_sha256"],
            "output_image": output_image.as_posix(),
            "poc_image": poc_image.as_posix(),
            "generated_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
        }
        write_meta(meta_json, meta)

        result = {
            **base,
            "interaction_id": interaction_id,
            "status": (
                obj_get(interaction, "status", "completed")
                or "completed"
            ),
            "latency_ms": latency_ms,
            "input_text_tokens": input_text_tokens,
            "input_image_tokens": input_image_tokens,
            "total_input_tokens": total_input_tokens,
            "output_image_tokens": output_image_tokens,
            "output_non_image_tokens": output_non_image_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "estimated_input_cost_usd": f"{input_cost:.9f}",
            "estimated_output_image_cost_usd": (
                f"{image_cost:.9f}"
            ),
            "estimated_output_non_image_cost_usd": (
                f"{non_image_cost:.9f}"
            ),
            "estimated_total_cost_usd": f"{total_cost:.9f}",
            "image_width": width,
            "image_height": height,
            "image_size_bytes": size_bytes,
            "usage_raw_json": json.dumps(
                jsonable(usage),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "error_message": "",
        }

        append_ledger(ledger, result)

        print(
            f"{scene['scene_id']} {style_key}: OK "
            f"{width}x{height} "
            f"input={total_input_tokens} "
            f"image_tokens={output_image_tokens} "
            f"latency={latency_ms}ms "
            f"est_usd={total_cost:.9f} "
            f"poc={poc_image.as_posix()}"
        )

        return interaction_id, "generated", total_cost

    except Exception as exc:
        latency_ms = round(
            (time.perf_counter() - started) * 1000
        )
        append_ledger(
            ledger,
            {
                **base,
                "status": "failed",
                "latency_ms": latency_ms,
                "error_message": (
                    f"{type(exc).__name__}: {exc}"
                ),
            },
        )
        raise


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Generate one poem as sequential multi-turn image chains."
        )
    )
    parser.add_argument("--poem-id", type=int, required=True)
    parser.add_argument(
        "--styles",
        default="A,B",
        help="Comma-separated style keys. Default: A,B",
    )
    parser.add_argument(
        "--scene-ids",
        default="",
        help=(
            "Optional comma-separated full scene IDs, "
            "e.g. p225_s01,p225_s02"
        ),
    )
    parser.add_argument(
        "--poems",
        default="data/poems.csv",
    )
    parser.add_argument(
        "--scenes",
        default="data/scenes.csv",
    )
    parser.add_argument(
        "--registry",
        default="config/image_styles_age6.json",
    )
    parser.add_argument(
        "--ledger",
        default="data/image_usage.csv",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Regenerate selected chains from scene 1. "
            "Use when an existing chain should be replaced."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )
    args = parser.parse_args()

    poems = read_csv(Path(args.poems))
    scenes = read_csv(Path(args.scenes))

    poem = next(
        (
            row for row in poems
            if int(row["poem_id"]) == args.poem_id
        ),
        None,
    )

    if poem is None:
        print(
            f"ERROR: poem_id={args.poem_id} not found",
            file=sys.stderr,
        )
        return 2

    plan_raw = (poem.get("visual_plan_json") or "").strip()
    if not plan_raw:
        print(
            "ERROR: selected poem has no visual_plan_json",
            file=sys.stderr,
        )
        return 2

    plan = json.loads(plan_raw)

    poem_scenes = [
        row for row in scenes
        if int(row["poem_id"]) == args.poem_id
    ]
    poem_scenes.sort(key=lambda row: int(row["scene_no"]))

    requested_scene_ids = {
        item.strip()
        for item in args.scene_ids.split(",")
        if item.strip()
    }

    if requested_scene_ids:
        # A chain cannot safely begin in the middle without prior state.
        first_requested_no = min(
            int(row["scene_no"])
            for row in poem_scenes
            if row["scene_id"] in requested_scene_ids
        )
        if first_requested_no != 1:
            print(
                "ERROR: multi-turn test must include scene 1. "
                "Start from the anchor scene.",
                file=sys.stderr,
            )
            return 2

        poem_scenes = [
            row for row in poem_scenes
            if row["scene_id"] in requested_scene_ids
        ]

    if not poem_scenes:
        print("ERROR: no scenes selected", file=sys.stderr)
        return 2

    style_keys = [
        item.strip().upper()
        for item in args.styles.split(",")
        if item.strip()
    ]
    styles = [
        resolve_style(
            key,
            Path(args.registry),
        )
        for key in style_keys
    ]

    print(f"model={args.model}")
    print(
        f"poem_id={poem['poem_id']} "
        f"title={poem['title']}"
    )
    print(
        "styles="
        + ",".join(
            f"{style['style_key']}:{style['style_id']}"
            for style in styles
        )
    )
    print(
        "scenes="
        + ",".join(row["scene_id"] for row in poem_scenes)
    )
    print(f"force={args.force} dry_run={args.dry_run}")

    if args.dry_run:
        print("\n=== Scene Isolation semantics (shared by A/B) ===")
        print("prompt_strategy=anchor_then_minimal_transition")
        print("whole_poem_text_in_prompt=no")
        print("poem_world_in_prompt=no")
        print("poem_continuity_prose_in_transition=no")
        for index, scene in enumerate(poem_scenes, start=1):
            isolation = resolve_scene_isolation(
                plan,
                scene["scene_id"],
            )
            print(f"\n{index}. {scene['scene_id']}")
            print(
                "  must_show "
                f"[{isolation['must_show_source']}]"
            )
            for item in isolation["must_show"]:
                print(f"    - {item}")
            if not isolation["must_show"]:
                print("    - (none)")

            print(
                "  scene_delta "
                f"[{isolation['scene_delta_source']}]"
            )
            for item in isolation["scene_delta"]:
                print(f"    - {item}")
            if not isolation["scene_delta"]:
                print("    - (anchor scene; no prior delta)")

            print(
                "  must_not_show "
                f"[{isolation['must_not_show_source']}]"
            )
            for item in isolation["must_not_show"]:
                print(f"    - {item}")
            if not isolation["must_not_show"]:
                print("    - (no additional scene-specific exclusions)")

        print("\n=== Output plan ===")
        for style in styles:
            print(
                f"\n=== Style {style['style_key']} / "
                f"{style['style_id']} ==="
            )
            for index, scene in enumerate(poem_scenes, start=1):
                image_path, meta_path = output_paths(
                    int(poem["poem_id"]),
                    int(scene["scene_no"]),
                    style["style_id"],
                )
                poc_path = poc_output_path(
                    int(poem["poem_id"]),
                    int(scene["scene_no"]),
                    style["style_key"],
                )
                if image_path.exists() and meta_path.exists():
                    action = (
                        "OVERWRITE" if args.force else "SKIP"
                    )
                else:
                    action = "GENERATE"
                prompt = build_prompt(
                    poem,
                    scene,
                    plan,
                    style,
                    index,
                )
                prompt_hash = hashlib.sha256(
                    prompt.encode("utf-8")
                ).hexdigest()[:12]
                prompt_mode = (
                    "ANCHOR" if index == 1 else "TRANSITION"
                )
                print(
                    f"{index}. {scene['scene_id']}: "
                    f"{prompt_mode} {action} -> "
                    f"{image_path.as_posix()} "
                    f"poc={poc_path.as_posix()} "
                    f"prompt={prompt_hash}"
                )
        return 0

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: GEMINI_API_KEY is missing.",
            file=sys.stderr,
        )
        return 2

    ledger = Path(args.ledger)
    ensure_ledger(ledger)

    client = genai.Client(api_key=api_key)

    run_id = (
        datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        + "_"
        + uuid.uuid4().hex[:8]
    )

    generated = 0
    skipped = 0
    failed = 0
    total_cost = 0.0

    for style in styles:
        print(
            f"\n=== Style {style['style_key']} / "
            f"{style['style_id']} ==="
        )

        previous_interaction_id: str | None = None

        for chain_index, scene in enumerate(
            poem_scenes,
            start=1,
        ):
            try:
                (
                    interaction_id,
                    status,
                    cost,
                ) = generate_scene(
                    client,
                    poem=poem,
                    scene=scene,
                    plan=plan,
                    style=style,
                    model=args.model,
                    ledger=ledger,
                    run_id=run_id,
                    chain_index=chain_index,
                    previous_interaction_id=(
                        previous_interaction_id
                    ),
                    force=args.force,
                )

                previous_interaction_id = (
                    interaction_id
                    or previous_interaction_id
                )
                total_cost += cost

                if status == "generated":
                    generated += 1
                else:
                    skipped += 1

            except Exception as exc:
                failed += 1
                print(
                    f"{scene['scene_id']} "
                    f"{style['style_key']}: FAILED "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                # Stop only this style chain. The next style can still run.
                break

    print("\nSummary")
    print(f"generated={generated}")
    print(f"skipped_existing={skipped}")
    print(f"failed={failed}")
    print(f"estimated_cost_usd={total_cost:.9f}")
    print(f"ledger={ledger.as_posix()}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
