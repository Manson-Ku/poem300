#!/usr/bin/env python3
"""Generate the fixed 4-scene age-6 image POC with Gemini Flash-Lite Image."""

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

MODEL = "gemini-3.1-flash-lite-image"
POC_SCENES = ("p225_s01", "p226_s03", "p217_s02", "p242_s01")
PRICING = {
    "pricing_as_of": "2026-10-06",
    "pricing_basis": "paid_standard_list_price",
    "input_text_per_million": 0.25,
    "output_image_per_million": 30.00,
}
LEDGER_FIELDS = [
    "usage_id","run_id","created_at_utc","scene_id","poem_id","title",
    "model","style_id","prompt_sha256","interaction_id","status","latency_ms",
    "input_text_tokens","output_image_tokens","total_input_tokens",
    "total_output_tokens","total_tokens","estimated_input_cost_usd",
    "estimated_output_cost_usd","estimated_total_cost_usd","pricing_as_of",
    "pricing_basis","image_width","image_height","image_size_bytes",
    "output_image","usage_raw_json","error_message",
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
        return {k: jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def modality_tokens(usage: Any, field: str, modality: str) -> int:
    items = obj_get(usage, field, []) or []
    for item in items:
        if str(obj_get(item, "modality", "")).lower() == modality.lower():
            return int(obj_get(item, "tokens", 0) or 0)
    return 0


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def ensure_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, fieldnames=LEDGER_FIELDS).writeheader()


def append_ledger(path: Path, row: dict[str, Any]) -> None:
    ensure_ledger(path)
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        writer.writerow({field: row.get(field, "") for field in LEDGER_FIELDS})


def calc_cost(input_text_tokens: int, output_image_tokens: int) -> tuple[float, float, float]:
    input_cost = input_text_tokens * PRICING["input_text_per_million"] / 1_000_000
    output_cost = output_image_tokens * PRICING["output_image_per_million"] / 1_000_000
    return input_cost, output_cost, input_cost + output_cost


def save_webp(image_bytes: bytes, output_path: Path) -> tuple[int, int, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(image_bytes)) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        width, height = img.size
        img.save(output_path, format="WEBP", quality=92, method=6)
    return width, height, output_path.stat().st_size


def generate_one(
    client: genai.Client,
    *,
    row: dict[str, str],
    run_id: str,
    ledger: Path,
    force: bool,
) -> dict[str, Any] | None:
    output_path = Path(row["output_image"])
    if output_path.exists() and not force:
        print(f"{row['scene_id']}: SKIP existing -> {output_path.as_posix()}")
        return None

    prompt = row["image_prompt"]
    base = {
        "usage_id": str(uuid.uuid4()),
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scene_id": row["scene_id"],
        "poem_id": row["poem_id"],
        "title": row["title"],
        "model": MODEL,
        "style_id": row["style_id"],
        "prompt_sha256": sha256_text(prompt),
        "pricing_as_of": PRICING["pricing_as_of"],
        "pricing_basis": PRICING["pricing_basis"],
        "output_image": output_path.as_posix(),
    }

    started = time.perf_counter()
    try:
        interaction = client.interactions.create(
            model=MODEL,
            input=prompt,
            response_format={
                "type": "image",
                "mime_type": "image/jpeg",
                "aspect_ratio": "16:9",
                "image_size": "1K",
            },
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        output_image = obj_get(interaction, "output_image")
        image_b64 = obj_get(output_image, "data")
        if not image_b64:
            raise RuntimeError("API response completed without output_image.data")

        raw_bytes = base64.b64decode(image_b64)
        width, height, size_bytes = save_webp(raw_bytes, output_path)

        usage = obj_get(interaction, "usage")
        input_text_tokens = modality_tokens(usage, "input_tokens_by_modality", "text")
        output_image_tokens = modality_tokens(usage, "output_tokens_by_modality", "image")
        total_input_tokens = int(obj_get(usage, "total_input_tokens", 0) or 0)
        total_output_tokens = int(obj_get(usage, "total_output_tokens", 0) or 0)
        total_tokens = int(obj_get(usage, "total_tokens", 0) or 0)

        if input_text_tokens == 0:
            input_text_tokens = total_input_tokens
        if output_image_tokens == 0:
            output_image_tokens = total_output_tokens

        input_cost, output_cost, total_cost = calc_cost(
            input_text_tokens,
            output_image_tokens,
        )

        result = {
            **base,
            "interaction_id": obj_get(interaction, "id", ""),
            "status": obj_get(interaction, "status", "completed") or "completed",
            "latency_ms": latency_ms,
            "input_text_tokens": input_text_tokens,
            "output_image_tokens": output_image_tokens,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "estimated_input_cost_usd": f"{input_cost:.9f}",
            "estimated_output_cost_usd": f"{output_cost:.9f}",
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
        return result

    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000)
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
        description="Generate four fixed age-6 POC background images."
    )
    parser.add_argument(
        "--manifest",
        default="data/image_prompts_age6.csv",
    )
    parser.add_argument(
        "--ledger",
        default="data/image_usage.csv",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing POC images.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show selected POC scenes without API calls.",
    )
    args = parser.parse_args()

    rows = read_manifest(Path(args.manifest))
    by_scene = {row["scene_id"]: row for row in rows}

    missing = [scene_id for scene_id in POC_SCENES if scene_id not in by_scene]
    if missing:
        print(
            "ERROR: POC scenes missing from manifest: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    selected = [by_scene[scene_id] for scene_id in POC_SCENES]

    print(f"model={MODEL}")
    print(f"poc_scenes={len(selected)}")
    print("scenes=" + ",".join(POC_SCENES))
    print(f"force={args.force} dry_run={args.dry_run}")

    if args.dry_run:
        for row in selected:
            output_path = Path(row["output_image"])
            action = "OVERWRITE" if output_path.exists() and args.force else (
                "SKIP" if output_path.exists() else "GENERATE"
            )
            print(
                f"{row['scene_id']} {row['title']}: {action} -> "
                f"{output_path.as_posix()}"
            )
        return 0

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: GEMINI_API_KEY is missing. Put it in .env or environment.",
            file=sys.stderr,
        )
        return 2

    client = genai.Client(api_key=api_key)
    ledger = Path(args.ledger)
    ensure_ledger(ledger)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid.uuid4().hex[:8]
    )

    generated = 0
    skipped = 0
    failed = 0
    estimated_cost = 0.0

    for row in selected:
        try:
            result = generate_one(
                client,
                row=row,
                run_id=run_id,
                ledger=ledger,
                force=args.force,
            )
            if result is None:
                skipped += 1
                continue
            generated += 1
            estimated_cost += float(result["estimated_total_cost_usd"])
            print(
                f"{row['scene_id']}: OK "
                f"{result['image_width']}x{result['image_height']} "
                f"tokens={result['output_image_tokens']} "
                f"latency={result['latency_ms']}ms "
                f"est_usd={result['estimated_total_cost_usd']}"
            )
        except Exception as exc:
            failed += 1
            print(
                f"{row['scene_id']}: FAILED {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    print("\nSummary")
    print(f"generated={generated}")
    print(f"skipped_existing={skipped}")
    print(f"failed={failed}")
    print(f"estimated_cost_usd={estimated_cost:.9f}")
    print(f"ledger={ledger.as_posix()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
