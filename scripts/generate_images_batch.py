#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemini Batch API image production for poem300.

This script reuses the production prompt compiler and asset contract from
scripts/generate_images.py. It submits only missing images by default, stores
runtime job files under output/image_batches/, and collects results back into
the normal production asset paths.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from generate_images import (
    DEFAULT_MODEL,
    append_ledger,
    build_prompt,
    copy_to_poc,
    ensure_ledger,
    output_paths,
    poc_output_path,
    read_csv,
    resolve_style,
    save_webp,
    write_meta,
)


BATCH_PRICING = {
    "pricing_as_of": "2026-10-08",
    "pricing_basis": "Gemini Developer API Batch paid tier",
    "input_text_per_million": 0.125,
    "output_image_per_million": 15.00,
}
DEFAULT_RUNTIME_ROOT = Path("output/image_batches")
DEFAULT_LEDGER = Path("data/image_usage.csv")
IMAGE_TOKENS_1K = 1120
TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid.uuid4().hex[:8]
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def state_name(job: Any) -> str:
    state = getattr(job, "state", None)
    if state is None:
        return ""
    return str(getattr(state, "name", state))


def api_client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing")
    return genai.Client(api_key=key)


def normalize_key(scene_id: str, style_key: str) -> str:
    return f"{scene_id}_{style_key.upper()}"


def select_items(args: argparse.Namespace) -> tuple[list[dict[str, Any]], int, int]:
    poems = read_csv(Path(args.poems))
    scenes = read_csv(Path(args.scenes))

    if args.poem_id is not None:
        selected_poems = [
            row for row in poems if int(row["poem_id"]) == args.poem_id
        ]
        if not selected_poems:
            raise ValueError(f"poem_id={args.poem_id} not found")
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

    selected_poems.sort(key=lambda row: int(row["poem_id"]))
    if args.limit is not None:
        selected_poems = selected_poems[: args.limit]
    if not selected_poems:
        raise ValueError("no poems matched selection")

    ages = {int(row["recommended_age"]) for row in selected_poems}
    if len(ages) != 1:
        raise ValueError("selected poems must belong to one age group")
    target_age = next(iter(ages))

    style_keys = [
        item.strip().upper()
        for item in args.styles.split(",")
        if item.strip()
    ]
    if not style_keys:
        raise ValueError("--styles must contain at least one style key")
    styles = [
        resolve_style(
            key,
            Path(args.registry),
            target_age=target_age,
        )
        for key in style_keys
    ]

    requested_scene_ids = {
        item.strip() for item in args.scene_ids.split(",") if item.strip()
    }
    all_scene_ids: set[str] = set()
    selected_scene_count = 0
    skipped_existing = 0
    items: list[dict[str, Any]] = []

    for poem in selected_poems:
        raw_plan = (poem.get("visual_plan_json") or "").strip()
        if not raw_plan:
            raise ValueError(
                f"poem_id={poem['poem_id']} has no visual_plan_json"
            )
        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"poem_id={poem['poem_id']} invalid visual_plan_json: {exc}"
            ) from exc

        poem_scenes = [
            row for row in scenes if row["poem_id"] == poem["poem_id"]
        ]
        poem_scenes.sort(key=lambda row: int(row["scene_no"]))

        plan_scene_ids = [
            entry.get("id") or entry.get("scene_id")
            for entry in plan.get("scenes", [])
            if isinstance(entry, dict)
        ]
        source_scene_ids = [row["scene_id"] for row in poem_scenes]
        if plan_scene_ids != source_scene_ids:
            raise ValueError(
                "visual_plan_json Scene IDs/order do not match "
                f"data/scenes.csv for poem_id={poem['poem_id']}"
            )

        all_scene_ids.update(source_scene_ids)
        if requested_scene_ids:
            poem_scenes = [
                row for row in poem_scenes
                if row["scene_id"] in requested_scene_ids
            ]

        selected_scene_count += len(poem_scenes)

        for style in styles:
            for scene in poem_scenes:
                poem_id = int(poem["poem_id"])
                scene_no = int(scene["scene_no"])
                output_image, meta_json = output_paths(
                    poem_id,
                    scene_no,
                    style["style_id"],
                )
                poc_image = poc_output_path(
                    poem_id,
                    scene_no,
                    style["style_key"],
                )

                if output_image.exists() and not args.force:
                    if not poc_image.exists():
                        copy_to_poc(
                            output_image,
                            poem_id=poem_id,
                            scene_no=scene_no,
                            style_key=style["style_key"],
                        )
                    skipped_existing += 1
                    continue

                prompt = build_prompt(poem, scene, plan, style)
                items.append(
                    {
                        "key": normalize_key(
                            scene["scene_id"], style["style_key"]
                        ),
                        "poem_id": poem_id,
                        "title": poem["title"],
                        "scene_id": scene["scene_id"],
                        "scene_no": scene_no,
                        "style_key": style["style_key"],
                        "style_id": style["style_id"],
                        "prompt": prompt,
                        "prompt_sha256": hashlib.sha256(
                            prompt.encode("utf-8")
                        ).hexdigest(),
                        "output_image": output_image.as_posix(),
                        "poc_image": poc_image.as_posix(),
                        "meta_json": meta_json.as_posix(),
                    }
                )

    if requested_scene_ids:
        unknown = requested_scene_ids - all_scene_ids
        if unknown:
            raise ValueError(
                "unknown scene IDs: " + ",".join(sorted(unknown))
            )

    return items, selected_scene_count, skipped_existing


def request_json(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": item["key"],
        "request": {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": item["prompt"]}],
                }
            ],
            "generation_config": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {
                    "aspectRatio": "16:9",
                    "imageSize": "1K",
                },
            },
        },
    }


def batch_output_cost(request_count: int) -> float:
    return (
        request_count
        * IMAGE_TOKENS_1K
        * BATCH_PRICING["output_image_per_million"]
        / 1_000_000
    )


def latest_job_path(runtime_root: Path) -> Path:
    latest = runtime_root / "latest.json"
    if not latest.exists():
        raise FileNotFoundError(
            f"No latest batch pointer found: {latest.as_posix()}"
        )
    pointer = read_json(latest)
    path = pointer.get("job_state")
    if not path:
        raise ValueError(f"Invalid latest pointer: {latest.as_posix()}")
    return Path(path)


def resolve_job_path(runtime_root: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return latest_job_path(runtime_root)


def submit(args: argparse.Namespace) -> int:
    items, selected_scenes, skipped_existing = select_items(args)
    request_count = len(items)

    print("delivery_mode=gemini_batch_api")
    print(f"model={args.model}")
    print(f"selected_scenes={selected_scenes}")
    print(f"skipped_existing={skipped_existing}")
    print(f"batch_requests={request_count}")
    print(
        "estimated_image_output_cost_usd="
        f"{batch_output_cost(request_count):.9f}"
    )
    print(f"force={args.force} dry_run={args.dry_run}")

    if not items:
        print("Nothing to submit; all selected image assets already exist.")
        return 0

    if args.dry_run:
        for item in items:
            print(
                f"{item['key']}: SUBMIT -> {item['output_image']} "
                f"prompt={item['prompt_sha256'][:12]}"
            )
        print("dry_run=true; no upload or Batch API job was created.")
        return 0

    client = api_client()
    run = new_run_id()
    runtime_root = Path(args.runtime_root)
    run_dir = runtime_root / run
    run_dir.mkdir(parents=True, exist_ok=True)
    request_path = run_dir / "requests.jsonl"
    manifest_path = run_dir / "manifest.json"
    job_path = run_dir / "job.json"

    with request_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(
                json.dumps(
                    request_json(item),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    manifest = {
        "run_id": run,
        "created_at_utc": utc_now(),
        "model": args.model,
        "request_count": request_count,
        "selected_scenes": selected_scenes,
        "skipped_existing": skipped_existing,
        "items": items,
    }
    write_json(manifest_path, manifest)

    uploaded = client.files.upload(
        file=str(request_path),
        config=types.UploadFileConfig(
            display_name=f"poem300-image-batch-{run}",
            mime_type="jsonl",
        ),
    )
    uploaded_name = str(getattr(uploaded, "name", "") or "")
    if not uploaded_name:
        raise RuntimeError("File API upload completed without a file name")

    job = client.batches.create(
        model=args.model,
        src=uploaded_name,
        config={"display_name": f"poem300-image-batch-{run}"},
    )
    job_name = str(getattr(job, "name", "") or "")
    if not job_name:
        raise RuntimeError("Batch API create completed without a job name")

    job_doc = {
        "run_id": run,
        "created_at_utc": utc_now(),
        "model": args.model,
        "job_name": job_name,
        "state": state_name(job),
        "input_file_name": uploaded_name,
        "request_jsonl": request_path.as_posix(),
        "manifest": manifest_path.as_posix(),
        "request_count": request_count,
        "selected_scenes": selected_scenes,
        "skipped_existing": skipped_existing,
        "collected_keys": [],
        "failed_keys": [],
    }
    write_json(job_path, job_doc)
    write_json(
        runtime_root / "latest.json",
        {
            "run_id": run,
            "job_state": job_path.as_posix(),
            "job_name": job_name,
        },
    )

    print(f"request_jsonl={request_path.as_posix()}")
    print(f"manifest={manifest_path.as_posix()}")
    print(f"input_file_name={uploaded_name}")
    print(f"job_name={job_name}")
    print(f"state={state_name(job)}")
    print(f"job_state={job_path.as_posix()}")
    print("SUBMITTED")
    return 0


def status(args: argparse.Namespace) -> int:
    runtime_root = Path(args.runtime_root)
    job_path = resolve_job_path(runtime_root, args.job)
    job_doc = read_json(job_path)
    client = api_client()
    job = client.batches.get(name=job_doc["job_name"])
    state = state_name(job)
    job_doc["state"] = state
    job_doc["last_checked_at_utc"] = utc_now()
    if getattr(job, "error", None):
        job_doc["job_error"] = str(job.error)
    write_json(job_path, job_doc)

    print(f"run_id={job_doc['run_id']}")
    print(f"job_name={job_doc['job_name']}")
    print(f"state={state}")
    print(f"request_count={job_doc['request_count']}")
    print(f"collected={len(job_doc.get('collected_keys', []))}")
    print(f"failed={len(job_doc.get('failed_keys', []))}")
    print(f"collect_ready={str(state == 'JOB_STATE_SUCCEEDED').lower()}")
    if getattr(job, "error", None):
        print(f"job_error={job.error}")
    return 1 if state in (TERMINAL_STATES - {"JOB_STATE_SUCCEEDED"}) else 0


def modality_tokens(details: Any, modality: str) -> int:
    if not isinstance(details, list):
        return 0
    for item in details:
        if not isinstance(item, dict):
            continue
        if str(item.get("modality", "")).upper() != modality.upper():
            continue
        return int(item.get("tokenCount") or item.get("tokens") or 0)
    return 0


def usage_numbers(response: dict[str, Any]) -> tuple[int, int, int, int, int]:
    usage = response.get("usageMetadata") or response.get("usage_metadata") or {}
    input_total = int(
        usage.get("promptTokenCount")
        or usage.get("prompt_token_count")
        or 0
    )
    output_total = int(
        usage.get("candidatesTokenCount")
        or usage.get("candidates_token_count")
        or 0
    )
    total = int(
        usage.get("totalTokenCount")
        or usage.get("total_token_count")
        or (input_total + output_total)
    )
    input_text = modality_tokens(
        usage.get("promptTokensDetails")
        or usage.get("prompt_tokens_details")
        or [],
        "TEXT",
    )
    output_image = modality_tokens(
        usage.get("candidatesTokensDetails")
        or usage.get("candidates_tokens_details")
        or [],
        "IMAGE",
    )
    if input_text == 0:
        input_text = input_total
    if output_image == 0:
        output_image = output_total or IMAGE_TOKENS_1K
    return input_text, input_total, output_image, output_total, total


def image_from_response(response: dict[str, Any]) -> tuple[bytes, str]:
    candidates = response.get("candidates") or []
    if not candidates:
        raise ValueError("response has no candidates")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if not inline:
            continue
        data = inline.get("data")
        if not data:
            continue
        mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
        return base64.b64decode(data), mime
    raise ValueError("response has no inline image data")


def append_failed_ledger(
    ledger: Path,
    job_doc: dict[str, Any],
    item: dict[str, Any],
    error: Any,
) -> None:
    append_ledger(
        ledger,
        {
            "usage_id": str(uuid.uuid4()),
            "run_id": job_doc["run_id"],
            "created_at_utc": utc_now(),
            "scene_id": item["scene_id"],
            "scene_no": item["scene_no"],
            "poem_id": item["poem_id"],
            "title": item["title"],
            "model": job_doc["model"],
            "style_key": item["style_key"],
            "style_id": item["style_id"],
            "prompt_sha256": item["prompt_sha256"],
            "status": "batch_failed",
            "pricing_as_of": BATCH_PRICING["pricing_as_of"],
            "pricing_basis": BATCH_PRICING["pricing_basis"],
            "output_image": item["output_image"],
            "poc_image": item["poc_image"],
            "meta_json": item["meta_json"],
            "error_message": json.dumps(error, ensure_ascii=False),
        },
    )


def collect(args: argparse.Namespace) -> int:
    runtime_root = Path(args.runtime_root)
    job_path = resolve_job_path(runtime_root, args.job)
    job_doc = read_json(job_path)
    manifest = read_json(Path(job_doc["manifest"]))
    items = manifest["items"]
    items_by_key = {item["key"]: item for item in items}

    client = api_client()
    job = client.batches.get(name=job_doc["job_name"])
    state = state_name(job)
    job_doc["state"] = state
    job_doc["last_checked_at_utc"] = utc_now()
    write_json(job_path, job_doc)
    if state != "JOB_STATE_SUCCEEDED":
        print(f"ERROR: job is not ready for collection: {state}", file=sys.stderr)
        return 2

    dest = getattr(job, "dest", None)
    result_file_name = str(getattr(dest, "file_name", "") or "")
    if not result_file_name:
        raise RuntimeError("Succeeded batch job has no dest.file_name")

    content = client.files.download(file=result_file_name)
    result_path = job_path.parent / "results.jsonl"
    result_path.write_bytes(content)

    ledger = Path(args.ledger)
    ensure_ledger(ledger)
    collected = set(job_doc.get("collected_keys", []))
    failed_keys = set(job_doc.get("failed_keys", []))
    seen_keys: set[str] = set()
    generated_now = 0
    failed_now = 0
    already_processed = 0
    total_cost = 0.0

    lines = [
        line for line in content.decode("utf-8").splitlines() if line.strip()
    ]

    for index, line in enumerate(lines):
        parsed = json.loads(line)
        key = str(parsed.get("key") or "")
        if not key and index < len(items):
            key = items[index]["key"]
        if key not in items_by_key:
            print(f"ERROR: unknown result key {key!r}", file=sys.stderr)
            failed_now += 1
            continue
        seen_keys.add(key)
        item = items_by_key[key]

        if key in collected or key in failed_keys:
            already_processed += 1
            continue

        if parsed.get("error"):
            append_failed_ledger(ledger, job_doc, item, parsed["error"])
            failed_keys.add(key)
            failed_now += 1
            print(f"{key}: FAILED {parsed['error']}", file=sys.stderr)
            job_doc["failed_keys"] = sorted(failed_keys)
            write_json(job_path, job_doc)
            continue

        response = parsed.get("response")
        if not isinstance(response, dict):
            error = {"message": "result has no response object"}
            append_failed_ledger(ledger, job_doc, item, error)
            failed_keys.add(key)
            failed_now += 1
            job_doc["failed_keys"] = sorted(failed_keys)
            write_json(job_path, job_doc)
            continue

        try:
            image_bytes, mime_type = image_from_response(response)
            output_image = Path(item["output_image"])
            width, height, size_bytes = save_webp(image_bytes, output_image)
            poc_image = copy_to_poc(
                output_image,
                poem_id=int(item["poem_id"]),
                scene_no=int(item["scene_no"]),
                style_key=item["style_key"],
            )

            input_text, input_total, output_image_tokens, output_total, total = (
                usage_numbers(response)
            )
            output_non_image = max(output_total - output_image_tokens, 0)
            input_cost = (
                input_text
                * BATCH_PRICING["input_text_per_million"]
                / 1_000_000
            )
            image_cost = (
                output_image_tokens
                * BATCH_PRICING["output_image_per_million"]
                / 1_000_000
            )
            request_cost = input_cost + image_cost
            total_cost += request_cost
            response_id = str(
                response.get("responseId")
                or response.get("response_id")
                or ""
            )
            usage_raw = response.get("usageMetadata") or response.get(
                "usage_metadata"
            ) or {}

            write_meta(
                Path(item["meta_json"]),
                {
                    "architecture": "poem_world_independent_scene_v1",
                    "delivery_mode": "gemini_batch_api_v1",
                    "batch_job_name": job_doc["job_name"],
                    "batch_request_key": key,
                    "poem_id": item["poem_id"],
                    "title": item["title"],
                    "scene_id": item["scene_id"],
                    "scene_no": item["scene_no"],
                    "style_key": item["style_key"],
                    "style_id": item["style_id"],
                    "model": job_doc["model"],
                    "response_id": response_id,
                    "previous_interaction_id": None,
                    "prompt_sha256": item["prompt_sha256"],
                    "mime_type": mime_type,
                    "output_image": output_image.as_posix(),
                    "poc_image": poc_image.as_posix(),
                    "generated_at_utc": utc_now(),
                },
            )

            append_ledger(
                ledger,
                {
                    "usage_id": str(uuid.uuid4()),
                    "run_id": job_doc["run_id"],
                    "created_at_utc": utc_now(),
                    "scene_id": item["scene_id"],
                    "scene_no": item["scene_no"],
                    "poem_id": item["poem_id"],
                    "title": item["title"],
                    "model": job_doc["model"],
                    "style_key": item["style_key"],
                    "style_id": item["style_id"],
                    "prompt_sha256": item["prompt_sha256"],
                    "interaction_id": response_id,
                    "previous_interaction_id": "",
                    "status": "completed",
                    "latency_ms": "",
                    "input_text_tokens": input_text,
                    "input_image_tokens": 0,
                    "total_input_tokens": input_total,
                    "output_image_tokens": output_image_tokens,
                    "output_non_image_tokens": output_non_image,
                    "total_output_tokens": output_total,
                    "total_tokens": total,
                    "estimated_input_cost_usd": f"{input_cost:.9f}",
                    "estimated_output_image_cost_usd": f"{image_cost:.9f}",
                    "estimated_output_non_image_cost_usd": "0.000000000",
                    "estimated_total_cost_usd": f"{request_cost:.9f}",
                    "pricing_as_of": BATCH_PRICING["pricing_as_of"],
                    "pricing_basis": BATCH_PRICING["pricing_basis"],
                    "image_width": width,
                    "image_height": height,
                    "image_size_bytes": size_bytes,
                    "output_image": output_image.as_posix(),
                    "poc_image": poc_image.as_posix(),
                    "meta_json": item["meta_json"],
                    "usage_raw_json": json.dumps(
                        usage_raw,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "error_message": "",
                },
            )

            collected.add(key)
            generated_now += 1
            job_doc["collected_keys"] = sorted(collected)
            write_json(job_path, job_doc)
            print(
                f"{key}: COLLECTED {width}x{height} "
                f"image_tokens={output_image_tokens} "
                f"est_usd={request_cost:.9f} -> {output_image.as_posix()}"
            )
        except Exception as exc:
            error = {"message": f"{type(exc).__name__}: {exc}"}
            append_failed_ledger(ledger, job_doc, item, error)
            failed_keys.add(key)
            failed_now += 1
            job_doc["failed_keys"] = sorted(failed_keys)
            write_json(job_path, job_doc)
            print(f"{key}: FAILED {error['message']}", file=sys.stderr)

    missing_result_keys = set(items_by_key) - seen_keys
    if missing_result_keys:
        print(
            "ERROR: result file is missing keys: "
            + ",".join(sorted(missing_result_keys)),
            file=sys.stderr,
        )
        failed_now += len(missing_result_keys)

    job_doc["result_file_name"] = result_file_name
    job_doc["result_jsonl"] = result_path.as_posix()
    job_doc["collected_keys"] = sorted(collected)
    job_doc["failed_keys"] = sorted(failed_keys)
    job_doc["last_collect_at_utc"] = utc_now()
    write_json(job_path, job_doc)

    print("\nSummary")
    print(f"batch_requests={len(items)}")
    print(f"generated_now={generated_now}")
    print(f"already_processed={already_processed}")
    print(f"collected_total={len(collected)}")
    print(f"failed_total={len(failed_keys)}")
    print(f"estimated_cost_usd_now={total_cost:.9f}")
    print(f"result_jsonl={result_path.as_posix()}")
    print(f"ledger={ledger.as_posix()}")
    return 1 if failed_now or failed_keys or missing_result_keys else 0


def add_selection_args(parser: argparse.ArgumentParser) -> None:
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--poem-id", type=int)
    selector.add_argument("--age", type=int, choices=(6, 7, 8, 9))
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--styles", default="B")
    parser.add_argument("--scene-ids", default="")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument("--scenes", default="data/scenes.csv")
    parser.add_argument(
        "--registry",
        default="config/image_styles_production_v1.json",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Gemini Batch API image production for poem300."
    )
    parser.add_argument(
        "--runtime-root",
        default=DEFAULT_RUNTIME_ROOT.as_posix(),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    submit_parser = sub.add_parser(
        "submit",
        help="Build JSONL, upload it, and create one Batch API job.",
    )
    add_selection_args(submit_parser)

    status_parser = sub.add_parser(
        "status",
        help="Check the latest or specified Batch API job.",
    )
    status_parser.add_argument("--job")

    collect_parser = sub.add_parser(
        "collect",
        help="Download a completed result JSONL and write image assets.",
    )
    collect_parser.add_argument("--job")
    collect_parser.add_argument(
        "--ledger",
        default=DEFAULT_LEDGER.as_posix(),
    )

    args = parser.parse_args()
    if args.command == "submit" and args.limit is not None and args.limit <= 0:
        parser.error("--limit must be > 0")

    try:
        if args.command == "submit":
            return submit(args)
        if args.command == "status":
            return status(args)
        if args.command == "collect":
            return collect(args)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
