#!/usr/bin/env python3
"""Generate independent poem-scene illustrations from one shared poem world.

Architecture:
- One poem defines one shared visual world.
- One physical poem line = one independent Scene = one image request.
- Each Scene prioritizes its own poem line; child explanation is secondary help.
- No previous_interaction_id and no sequential image-edit chain.
- Style A/B share the same semantics and model; only the style preset changes.

Successful outputs are written to the formal asset path and copied to
assets/poc/{poem_id}_{style_key}_sXX.webp for quick review.
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


DEFAULT_MODEL = "gemini-3.1-flash-lite-image"

PRICING = {
    "pricing_as_of": "2026-10-07",
    "pricing_basis": "Gemini Developer API Standard paid tier",
    "input_text_per_million": 0.25,
    "output_image_per_million": 30.00,
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
    "poc_image",
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
    """Create or migrate the ledger without dropping previous rows."""
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
            migrated = {
                field: row.get(field, "")
                for field in LEDGER_FIELDS
            }
            if (
                not migrated["estimated_output_image_cost_usd"]
                and row.get("estimated_output_cost_usd")
            ):
                migrated["estimated_output_image_cost_usd"] = row[
                    "estimated_output_cost_usd"
                ]
            style_id = migrated.get("style_id", "")
            if not migrated.get("style_key"):
                if "_a_" in style_id.lower():
                    migrated["style_key"] = "A"
                elif "_b_" in style_id.lower():
                    migrated["style_key"] = "B"
            writer.writerow(migrated)


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
    *,
    target_age: int,
) -> dict[str, Any]:
    registry = read_json(registry_path)
    key = style_key.strip().upper()

    supported_ages = registry.get("supported_ages")
    if supported_ages is not None and target_age not in {
        int(value) for value in supported_ages
    }:
        raise ValueError(
            f"registry {registry_path} does not support age {target_age}"
        )

    if key not in registry["styles"]:
        raise ValueError(
            f"Unknown style {style_key!r}; available: "
            + ", ".join(registry["styles"].keys())
        )

    entry = registry["styles"][key]
    config_by_age = entry.get("config_by_age") or {}
    config_path = (
        config_by_age.get(str(target_age))
        or entry.get("config")
    )
    if not config_path:
        raise ValueError(
            f"style {key!r} has no config for age {target_age}"
        )

    style = read_json(Path(config_path))
    age_map = entry.get("asset_style_id_by_age") or {}
    asset_style_id = (
        age_map.get(str(target_age))
        or entry.get("asset_style_id")
        or entry.get("style_id")
        or style.get("style_id")
    )
    if not asset_style_id:
        raise ValueError(
            f"style {key!r} has no asset style id"
        )

    style["preset_id"] = (
        entry.get("preset_id")
        or style.get("preset_id")
        or style.get("style_id")
    )
    style["style_id"] = str(asset_style_id)
    style["style_key"] = key
    style["target_age"] = target_age
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
    if rendering.get("priority"):
        parts.append("場景：" + rendering["priority"])
    if rendering.get("texture"):
        parts.append("質感：" + rendering["texture"])

    character = style.get("character_rendering") or {}
    if character.get("proportion"):
        parts.append("人物：" + character["proportion"])
    if character.get("face"):
        parts.append("表情：" + character["face"])

    lighting = style.get("lighting_language") or {}
    if isinstance(lighting, dict) and lighting.get("description"):
        parts.append("光線：" + lighting["description"])
    elif isinstance(lighting, str) and lighting.strip():
        parts.append("光線：" + lighting.strip())

    camera = style.get("camera_language") or {}
    if isinstance(camera, dict) and camera.get("description"):
        parts.append("鏡頭：" + camera["description"])
    elif isinstance(camera, str) and camera.strip():
        parts.append("鏡頭：" + camera.strip())

    return "；".join(part for part in parts if part)


def find_scene_plan(
    plan: dict[str, Any],
    scene_id: str,
) -> dict[str, Any]:
    for scene in plan.get("scenes", []):
        current = scene.get("id") or scene.get("scene_id")
        if current == scene_id:
            return scene
    raise ValueError(f"{scene_id}: missing from visual_plan_json")


def visual_world(poem: dict[str, str], plan: dict[str, Any]) -> str:
    value = (poem.get("visual_world") or "").strip()
    if value:
        return value
    return str(plan.get("world") or "").strip()


def scene_entity_lines(
    plan: dict[str, Any],
    scene_plan: dict[str, Any],
) -> list[str]:
    by_id = {
        str(item["id"]): item
        for item in plan.get("entities", [])
        if isinstance(item, dict) and item.get("id")
    }

    refs = (
        scene_plan.get("entities")
        or scene_plan.get("entity_states")
        or []
    )

    lines: list[str] = []
    if not isinstance(refs, list):
        return lines

    for ref in refs:
        entity_id = ""
        if isinstance(ref, str):
            entity_id = ref
        elif isinstance(ref, dict) and ref.get("id"):
            entity_id = str(ref["id"])

        item = by_id.get(entity_id)
        if not item:
            continue

        label = item.get("label") or entity_id
        detail = item.get("detail")
        line = f"- {label}"
        if detail:
            line += f"：{detail}"
        lines.append(line)

    return lines


GLOBAL_EXCLUSIONS = [
    "任何文字、題字、書法",
    "字幕、注音、Logo、UI",
    "對話框、思想泡泡、音符、漫畫聲效符號",
    "白色字幕條、刻意留白區、圓角卡片式畫框",
    "多格漫畫、拼貼、分割畫面",
]


def build_prompt(
    poem: dict[str, str],
    scene: dict[str, str],
    plan: dict[str, Any],
    style: dict[str, Any],
) -> str:
    """Compile one independent Scene prompt.

    The poem world is soft context. The current physical poem line is the
    semantic priority. No previous/future Scene semantics are injected.
    """
    scene_plan = find_scene_plan(plan, scene["scene_id"])
    world = visual_world(poem, plan)
    entities = scene_entity_lines(plan, scene_plan)

    blocks = [
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
        f"本 Scene：{scene['scene_id']}",
        f"本句原詩：{scene['original_line']}",
        f"兒童解釋：{scene['child_explanation_line']}",
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
            blocks.append(f"{label}：{value}")

    actions = scene_plan.get("actions")
    if actions:
        blocks.append(
            "本幕動作：" + "、".join(str(x) for x in actions)
        )

    note = scene_plan.get("note")
    if note:
        blocks.append(f"本幕特別注意：{note}")

    if entities:
        blocks += [
            "",
            "本幕可使用的人物／場景／物件：",
            *entities,
        ]

    blocks += [
        "",
        "畫風：",
        style_text(style),
        "",
        "生成要求：",
        "- 第一眼要能幫幼童理解目前這一句詩。",
        "- 世界觀一致即可，不追求跨 Scene 的角色姿勢、房間細節、鏡位或構圖完全一致。",
        "- 不要因為知道整首詩而提前加入其他句子的主要事件。",
        "- 圖片自然延伸到四邊，不替後續字幕或注音預留區域。",
        "- 聲音與情緒使用人物姿態、表情、環境與光線表現，不使用符號化圖示。",
    ]

    exclusions = [
        str(item)
        for item in (style.get("global_exclusions") or [])
    ] + GLOBAL_EXCLUSIONS

    if exclusions:
        seen: set[str] = set()
        clean = []
        for item in exclusions:
            if item not in seen:
                seen.add(item)
                clean.append(item)
        blocks += ["", "禁止出現："] + [
            f"- {item}" for item in clean
        ]

    return "\n".join(blocks)


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
    return directory / "background.webp", directory / "meta.json"


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
    target = poc_output_path(poem_id, scene_no, style_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)
    return target


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


def write_meta(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def calc_cost(
    input_text_tokens: int,
    output_image_tokens: int,
) -> tuple[float, float, float]:
    input_cost = (
        input_text_tokens
        * PRICING["input_text_per_million"]
        / 1_000_000
    )
    image_cost = (
        output_image_tokens
        * PRICING["output_image_per_million"]
        / 1_000_000
    )
    return input_cost, image_cost, input_cost + image_cost


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
    force: bool,
) -> tuple[str, float]:
    poem_id = int(poem["poem_id"])
    scene_no = int(scene["scene_no"])
    style_key = style["style_key"]
    style_id = style["style_id"]

    output_image, meta_json = output_paths(
        poem_id,
        scene_no,
        style_id,
    )
    poc_image = poc_output_path(poem_id, scene_no, style_key)

    if output_image.exists() and not force:
        if not poc_image.exists():
            copy_to_poc(
                output_image,
                poem_id=poem_id,
                scene_no=scene_no,
                style_key=style_key,
            )
        print(
            f"{scene['scene_id']} {style_key}: SKIP existing -> "
            f"{output_image.as_posix()}"
        )
        return "skipped", 0.0

    prompt = build_prompt(poem, scene, plan, style)

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
        "prompt_sha256": hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest(),
        "previous_interaction_id": "",
        "pricing_as_of": PRICING["pricing_as_of"],
        "pricing_basis": PRICING["pricing_basis"],
        "output_image": output_image.as_posix(),
        "poc_image": poc_image.as_posix(),
        "meta_json": meta_json.as_posix(),
    }

    started = time.perf_counter()

    try:
        interaction = client.interactions.create(
            model=model,
            input=prompt,
            response_format={
                "type": "image",
                "mime_type": "image/jpeg",
                "aspect_ratio": "16:9",
                "image_size": "1K",
            },
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

        if input_text_tokens == 0:
            input_text_tokens = max(
                total_input_tokens - input_image_tokens,
                0,
            )
        if output_image_tokens == 0:
            output_image_tokens = total_output_tokens

        output_non_image_tokens = max(
            total_output_tokens - output_image_tokens,
            0,
        )

        input_cost, image_cost, total_cost = calc_cost(
            input_text_tokens,
            output_image_tokens,
        )

        interaction_id = str(
            obj_get(interaction, "id", "") or ""
        )

        meta = {
            "architecture": "poem_world_independent_scene_v1",
            "poem_id": poem_id,
            "title": poem["title"],
            "scene_id": scene["scene_id"],
            "scene_no": scene_no,
            "style_key": style_key,
            "style_id": style_id,
            "model": model,
            "interaction_id": interaction_id,
            "previous_interaction_id": None,
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
            "estimated_output_image_cost_usd": f"{image_cost:.9f}",
            "estimated_output_non_image_cost_usd": "0.000000000",
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
            f"input={input_text_tokens} "
            f"image_tokens={output_image_tokens} "
            f"latency={latency_ms}ms "
            f"est_usd={total_cost:.9f} "
            f"poc={poc_image.as_posix()}"
        )
        return "generated", total_cost

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
                "error_message": f"{type(exc).__name__}: {exc}",
            },
        )
        raise


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Generate independent Scene illustrations from one shared poem world."
        )
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--poem-id",
        type=int,
        help="Generate one poem only.",
    )
    selector.add_argument(
        "--age",
        type=int,
        choices=(6, 7, 8, 9),
        help="Batch-select poems by recommended_age.",
    )
    parser.add_argument(
        "--approved-only",
        action="store_true",
        help=(
            "When batch-selecting by --age, include only poems whose "
            "visual_plan_status is approved."
        ),
    )
    parser.add_argument(
        "--start-poem-id",
        type=int,
        help="Optional lower poem_id bound for batch selection.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of poems after filtering.",
    )
    parser.add_argument(
        "--styles",
        default="B",
        help="Comma-separated style keys. Default: B",
    )
    parser.add_argument(
        "--scene-ids",
        default="",
        help=(
            "Optional comma-separated full scene IDs. Intended mainly "
            "for single-poem tests."
        ),
    )
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument("--scenes", default="data/scenes.csv")
    parser.add_argument(
        "--registry",
        default="config/image_styles_production_v1.json",
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
        help="Regenerate existing selected images.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the independent Scene plan without API calls.",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be > 0")

    poems = read_csv(Path(args.poems))
    scenes = read_csv(Path(args.scenes))

    if args.poem_id is not None:
        selected_poems = [
            row for row in poems
            if int(row["poem_id"]) == args.poem_id
        ]
        if not selected_poems:
            print(
                f"ERROR: poem_id={args.poem_id} not found",
                file=sys.stderr,
            )
            return 2
    else:
        selected_poems = [
            row for row in poems
            if int(row["recommended_age"]) == args.age
        ]
        if args.approved_only:
            selected_poems = [
                row for row in selected_poems
                if (row.get("visual_plan_status") or "").strip().lower()
                == "approved"
            ]
        if args.start_poem_id is not None:
            selected_poems = [
                row for row in selected_poems
                if int(row["poem_id"]) >= args.start_poem_id
            ]

    selected_poems.sort(key=lambda row: int(row["poem_id"]))

    if args.limit is not None:
        selected_poems = selected_poems[: args.limit]

    if not selected_poems:
        print("ERROR: no poems matched selection", file=sys.stderr)
        return 2

    requested_scene_ids = {
        item.strip()
        for item in args.scene_ids.split(",")
        if item.strip()
    }

    style_keys = [
        item.strip().upper()
        for item in args.styles.split(",")
        if item.strip()
    ]
    if not style_keys:
        parser.error("--styles must contain at least one style key")

    selected_ages = {
        int(row["recommended_age"])
        for row in selected_poems
    }
    if len(selected_ages) != 1:
        print(
            "ERROR: selected poems must belong to exactly one age group",
            file=sys.stderr,
        )
        return 2
    target_age = next(iter(selected_ages))

    styles = [
        resolve_style(
            key,
            Path(args.registry),
            target_age=target_age,
        )
        for key in style_keys
    ]

    work_items: list[
        tuple[dict[str, str], dict[str, Any], list[dict[str, str]]]
    ] = []
    all_source_scene_ids: set[str] = set()

    # Validate every selected poem before any API request is allowed.
    for poem in selected_poems:
        raw_plan = (poem.get("visual_plan_json") or "").strip()
        if not raw_plan:
            print(
                f"ERROR: poem_id={poem['poem_id']} "
                "has no visual_plan_json",
                file=sys.stderr,
            )
            return 2

        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError as exc:
            print(
                f"ERROR: poem_id={poem['poem_id']} "
                f"invalid visual_plan_json: {exc}",
                file=sys.stderr,
            )
            return 2

        poem_scenes = [
            row for row in scenes
            if row["poem_id"] == poem["poem_id"]
        ]
        poem_scenes.sort(key=lambda row: int(row["scene_no"]))

        plan_scene_ids = [
            item.get("id") or item.get("scene_id")
            for item in plan.get("scenes", [])
            if isinstance(item, dict)
        ]
        source_scene_ids = [
            row["scene_id"] for row in poem_scenes
        ]

        if plan_scene_ids != source_scene_ids:
            print(
                "ERROR: visual_plan_json Scene IDs/order do not match "
                "data/scenes.csv physical-line Scenes.",
                file=sys.stderr,
            )
            print(
                f"poem_id={poem['poem_id']} "
                f"title={poem['title']}",
                file=sys.stderr,
            )
            print(f"plan={plan_scene_ids}", file=sys.stderr)
            print(f"source={source_scene_ids}", file=sys.stderr)
            return 2

        all_source_scene_ids.update(source_scene_ids)
        work_items.append((poem, plan, poem_scenes))

    if requested_scene_ids:
        unknown = requested_scene_ids - all_source_scene_ids
        if unknown:
            print(
                "ERROR: unknown scene IDs: "
                + ",".join(sorted(unknown)),
                file=sys.stderr,
            )
            return 2

        filtered_items = []
        for poem, plan, poem_scenes in work_items:
            selected_scenes = [
                row for row in poem_scenes
                if row["scene_id"] in requested_scene_ids
            ]
            if selected_scenes:
                filtered_items.append(
                    (poem, plan, selected_scenes)
                )
        work_items = filtered_items

    selected_scene_count = sum(
        len(poem_scenes)
        for _, _, poem_scenes in work_items
    )
    planned_images = selected_scene_count * len(styles)

    print("architecture=poem_world_independent_scene_v1")
    print(f"model={args.model}")
    if args.poem_id is not None:
        print(f"selection=poem_id:{args.poem_id}")
    else:
        status_text = "approved" if args.approved_only else "any"
        print(
            f"selection=age:{args.age} "
            f"visual_plan_status:{status_text}"
        )
    print(f"poem_count={len(work_items)}")
    print(f"scene_count={selected_scene_count}")
    print(
        f"planned_images={planned_images} "
        f"({selected_scene_count} scenes x {len(styles)} styles)"
    )
    print(
        "poem_ids="
        + ",".join(
            poem["poem_id"]
            for poem, _, _ in work_items
        )
    )
    print(
        "styles="
        + ",".join(
            f"{style['style_key']}:{style['style_id']}"
            for style in styles
        )
    )
    print("previous_interaction_id=disabled")
    print(f"force={args.force} dry_run={args.dry_run}")

    if args.dry_run:
        for poem, plan, poem_scenes in work_items:
            print(
                f"\n=== Poem {poem['poem_id']} "
                f"{poem['title']} ==="
            )
            print(f"world={visual_world(poem, plan)}")
            for style in styles:
                print(
                    f"-- Style {style['style_key']} / "
                    f"{style['style_id']} --"
                )
                for scene in poem_scenes:
                    scene_no = int(scene["scene_no"])
                    image_path, _ = output_paths(
                        int(poem["poem_id"]),
                        scene_no,
                        style["style_id"],
                    )
                    poc_path = poc_output_path(
                        int(poem["poem_id"]),
                        scene_no,
                        style["style_key"],
                    )
                    scene_plan = find_scene_plan(
                        plan,
                        scene["scene_id"],
                    )
                    if image_path.exists():
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
                    )
                    print(
                        f"{scene['scene_id']} "
                        f"{style['style_key']}: "
                        f"{action} "
                        f"line={scene['original_line']} "
                        f"focus="
                        f"{scene_plan.get('focus') or scene_plan.get('visual_focus', '')} "
                        f"prompt="
                        f"{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:12]} "
                        f"output={image_path.as_posix()} "
                        f"poc={poc_path.as_posix()}"
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
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid.uuid4().hex[:8]
    )

    generated = 0
    skipped = 0
    failed = 0
    total_cost = 0.0

    for poem, plan, poem_scenes in work_items:
        print(
            f"\n=== Poem {poem['poem_id']} "
            f"{poem['title']} ==="
        )
        for style in styles:
            print(
                f"-- Style {style['style_key']} / "
                f"{style['style_id']} --"
            )
            for scene in poem_scenes:
                try:
                    status, cost = generate_scene(
                        client,
                        poem=poem,
                        scene=scene,
                        plan=plan,
                        style=style,
                        model=args.model,
                        ledger=ledger,
                        run_id=run_id,
                        force=args.force,
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

    print("\nSummary")
    print(f"poems={len(work_items)}")
    print(f"scenes={selected_scene_count}")
    print(f"generated={generated}")
    print(f"skipped_existing={skipped}")
    print(f"failed={failed}")
    print(f"estimated_cost_usd={total_cost:.9f}")
    print(f"ledger={ledger.as_posix()}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
