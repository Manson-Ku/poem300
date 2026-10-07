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


def build_prompt(
    poem: dict[str, str],
    scene: dict[str, str],
    plan: dict[str, Any],
    style: dict[str, Any],
    chain_index: int,
) -> str:
    exclusions = style.get("global_exclusions") or []

    if chain_index == 1:
        continuity_instruction = (
            "這是這首詩的第一張圖，也是後續 Scene 的視覺錨點。"
            "請建立可延續的角色外貌、服裝、場景空間、主要物件、"
            "光線、色彩與整體繪本語言。"
        )
    else:
        continuity_instruction = (
            "這是同一首詩的下一個 Scene。"
            "你已經看過上一輪產生的圖。"
            "請延續既有角色外貌、服裝、場景空間、主要物件、"
            "畫風、光線邏輯與色彩系統；只修改本 Scene 必須改變的內容。"
            "除非詩意明確要求，不要重新設計人物或地點。"
        )

    blocks = [
        "請以繁體中文理解內容，生成一張 16:9、完整滿版的兒童繪本插畫。",
        continuity_instruction,
        "",
        f"詩名：{poem['title']}",
        f"作者：{poem['author']}",
        f"整首詩：\n{poem['content']}",
        "",
        "整首詩共用的視覺世界：",
        visual_world(poem, plan) or "依詩意自然建立。",
        "",
        "整首詩共用的人物／場景／物件：",
        visual_objects(poem, plan) or "依詩意自然建立。",
        "",
        "跨 Scene 連貫規則：",
        visual_continuity(poem, plan) or "重複元素保持一致。",
        "",
        f"本 Scene：{scene['scene_id']}",
        f"原詩：{scene['original_line']}",
        f"兒童解釋：{scene['child_explanation_line']}",
    ]

    planned = scene_plan_text(plan, scene["scene_id"])
    if planned:
        blocks += [
            "",
            "本 Scene 已定義的視覺內容：",
            planned,
        ]

    blocks += [
        "",
        "畫風：",
        style_text(style),
        "",
        "生成規則：",
        "- 這個 Scene 是單一完整畫面，不依逗號或句號拆成多格。",
        "- 圖片本身只負責詩意與故事畫面，不替字幕、注音或排版預留區域。",
        "- 不要加入沒有必要的新角色。",
        "- 畫面需自然延續前一 Scene，而不是重新抽一張相似題材的插畫。",
    ]

    if exclusions:
        blocks += [
            "",
            "禁止出現：",
            *[f"- {item}" for item in exclusions],
        ]

    return "\n".join(blocks)


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
            f"est_usd={total_cost:.9f}"
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
                if image_path.exists() and meta_path.exists():
                    action = (
                        "OVERWRITE" if args.force else "SKIP"
                    )
                else:
                    action = "GENERATE"
                print(
                    f"{index}. {scene['scene_id']}: "
                    f"{action} -> {image_path.as_posix()}"
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
