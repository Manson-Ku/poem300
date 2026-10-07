#!/usr/bin/env python3
"""Production Gemini TTS batch generator for poem300.

Production defaults:
- model: gemini-3.8-flash-lite-tts
- voice: Kore
- one physical poem line == one Scene
- each Scene owns poem.wav + explanation.wav

Existing WAV files are skipped by default. Use --force to regenerate them.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import sys
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai

MODEL = "gemini-3.8-flash-lite-tts"
DEFAULT_VOICE = "Kore"
DEFAULT_STYLE = (
    "Taiwan Mandarin. Warm, clear, gentle children's audiobook narration for ages 6 to 9. "
    "Recite exactly as written. Do not add, omit, paraphrase, explain, or repeat any words. "
    "Use natural pauses at Chinese punctuation and a calm educational pace."
)

DEFAULT_TYPES = ("poem", "explanation")
ALL_TYPES = ("title", "author", "poem", "explanation")

PRICING = {
    "pricing_as_of": "2026-10-06",
    "pricing_basis": "paid_standard_list_price",
    "input_text_per_million": 0.50,
    "output_audio_per_million": 6.00,
}

LEDGER_FIELDS = [
    "usage_id",
    "run_id",
    "created_at_utc",
    "poem_id",
    "title",
    "scene_id",
    "scene_no",
    "audio_type",
    "model",
    "voice",
    "style_sha256",
    "text_sha256",
    "text_chars",
    "interaction_id",
    "status",
    "latency_ms",
    "input_text_tokens",
    "output_audio_tokens",
    "total_input_tokens",
    "total_output_tokens",
    "total_tokens",
    "estimated_input_cost_usd",
    "estimated_output_cost_usd",
    "estimated_total_cost_usd",
    "pricing_as_of",
    "pricing_basis",
    "audio_duration_sec",
    "audio_size_bytes",
    "audio_file",
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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_newlines(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def read_poems(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda row: int(row["poem_id"]))
    return rows


def select_poems(
    poems: list[dict[str, str]],
    *,
    ages: set[int] | None,
    poem_id: int | None,
    start_poem_id: int | None,
    limit: int | None,
) -> list[dict[str, str]]:
    selected = poems

    if ages:
        selected = [
            row
            for row in selected
            if int(row["recommended_age"]) in ages
        ]

    if poem_id is not None:
        selected = [
            row for row in selected if int(row["poem_id"]) == poem_id
        ]

    if start_poem_id is not None:
        selected = [
            row
            for row in selected
            if int(row["poem_id"]) >= start_poem_id
        ]

    if limit is not None:
        selected = selected[:limit]

    return selected


def build_scenes(poem: dict[str, str]) -> list[dict[str, Any]]:
    originals = normalize_newlines(poem["content"]).split("\n")
    explanations = normalize_newlines(
        poem["child_explanation_6_8"]
    ).split("\n")

    if len(originals) != len(explanations):
        raise ValueError(
            f"poem_id={poem['poem_id']}: "
            f"content lines={len(originals)} != "
            f"explanation lines={len(explanations)}"
        )

    pid = int(poem["poem_id"])
    scenes = []
    for scene_no, (original, explanation) in enumerate(
        zip(originals, explanations),
        start=1,
    ):
        scenes.append(
            {
                "scene_id": f"p{pid:03d}_s{scene_no:02d}",
                "scene_no": scene_no,
                "original_line": original,
                "child_explanation_line": explanation,
            }
        )
    return scenes


def ensure_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, fieldnames=LEDGER_FIELDS).writeheader()


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


def modality_tokens(usage: Any, field: str, modality: str) -> int:
    items = obj_get(usage, field, []) or []
    for item in items:
        if str(obj_get(item, "modality", "")).lower() == modality.lower():
            return int(obj_get(item, "tokens", 0) or 0)
    return 0


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        frames = wav.getnframes()
        return round(frames / rate, 3) if rate else 0.0


def calculate_cost(
    input_text_tokens: int,
    output_audio_tokens: int,
) -> tuple[float, float, float]:
    input_cost = (
        input_text_tokens
        * PRICING["input_text_per_million"]
        / 1_000_000
    )
    output_cost = (
        output_audio_tokens
        * PRICING["output_audio_per_million"]
        / 1_000_000
    )
    return input_cost, output_cost, input_cost + output_cost


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_pronunciation_overrides(
    path: Path,
) -> dict[tuple[int, str, int], dict[str, Any]]:
    if not path.exists():
        return {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    index: dict[tuple[int, str, int], dict[str, Any]] = {}

    for item in payload.get("items", []):
        key = (
            int(item["poem_id"]),
            str(item["audio_type"]),
            int(item.get("scene_no", 0)),
        )
        if key in index:
            raise ValueError(
                f"duplicate TTS pronunciation override: {key}"
            )
        index[key] = item

    return index


def style_with_pronunciation(
    base_style: str,
    *,
    text: str,
    override: dict[str, Any] | None,
) -> str:
    if not override:
        return base_style

    expected_text = str(override.get("source_text", ""))
    if expected_text != text:
        raise ValueError(
            "TTS pronunciation override source_text mismatch: "
            f"expected={expected_text!r} actual={text!r}"
        )

    rules = []
    for item in override.get("pronunciations", []):
        character = str(item["character"])
        reading = str(item["reading"])
        context = str(item.get("context", "")).strip()
        if character not in text:
            raise ValueError(
                "TTS pronunciation override character missing "
                f"from source text: {character!r}"
            )
        if context:
            rules.append(
                f"{character} in {context} must be pronounced {reading}"
            )
        else:
            rules.append(
                f"{character} must be pronounced {reading}"
            )

    if not rules:
        return base_style

    return (
        base_style.rstrip()
        + " Pronunciation requirements for this exact asset: "
        + "; ".join(rules)
        + ". Speak only the original source text. "
        + "Do not say these pronunciation instructions aloud."
    )


def is_retryable(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".upper()
    retry_markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "RESOURCE_EXHAUSTED",
        "UNAVAILABLE",
        "DEADLINE_EXCEEDED",
        "INTERNAL",
    )
    return any(marker in message for marker in retry_markers)


def generate_audio(
    client: genai.Client,
    *,
    run_id: str,
    poem: dict[str, str],
    scene: dict[str, Any],
    audio_type: str,
    text: str,
    voice: str,
    style: str,
    output_path: Path,
    ledger_path: Path,
    max_retries: int,
    retry_base_seconds: float,
) -> dict[str, Any]:
    usage_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    relative_audio_path = output_path.as_posix()

    base_row: dict[str, Any] = {
        "usage_id": usage_id,
        "run_id": run_id,
        "created_at_utc": created_at,
        "poem_id": poem["poem_id"],
        "title": poem["title"],
        "scene_id": scene["scene_id"],
        "scene_no": scene["scene_no"],
        "audio_type": audio_type,
        "model": MODEL,
        "voice": voice,
        "style_sha256": sha256_text(style),
        "text_sha256": sha256_text(text),
        "text_chars": len(text),
        "pricing_as_of": PRICING["pricing_as_of"],
        "pricing_basis": PRICING["pricing_basis"],
        "audio_file": relative_audio_path,
    }

    last_exc: Exception | None = None
    total_started = time.perf_counter()

    for attempt in range(max_retries + 1):
        try:
            interaction = client.interactions.create(
                model=MODEL,
                input=[
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                                "annotations": [
                                    {
                                        "type": "speech_metadata",
                                        "style": style,
                                    }
                                ],
                            }
                        ],
                    }
                ],
                response_format={
                    "type": "audio",
                    "mime_type": "audio/wav",
                    "sample_rate": 24000,
                },
                generation_config={
                    "speech_config": [{"voice": voice}]
                },
            )

            latency_ms = round(
                (time.perf_counter() - total_started) * 1000
            )

            output_audio = obj_get(interaction, "output_audio")
            audio_b64 = obj_get(output_audio, "data")
            if not audio_b64:
                raise RuntimeError(
                    "API response completed without output_audio.data"
                )

            audio_bytes = base64.b64decode(audio_b64)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(audio_bytes)

            usage = obj_get(interaction, "usage")
            input_text_tokens = modality_tokens(
                usage,
                "input_tokens_by_modality",
                "text",
            )
            output_audio_tokens = modality_tokens(
                usage,
                "output_tokens_by_modality",
                "audio",
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
                input_text_tokens = total_input_tokens
            if output_audio_tokens == 0:
                output_audio_tokens = total_output_tokens

            input_cost, output_cost, total_cost = calculate_cost(
                input_text_tokens,
                output_audio_tokens,
            )

            row = {
                **base_row,
                "interaction_id": obj_get(interaction, "id", ""),
                "status": (
                    obj_get(interaction, "status", "completed")
                    or "completed"
                ),
                "latency_ms": latency_ms,
                "input_text_tokens": input_text_tokens,
                "output_audio_tokens": output_audio_tokens,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
                "estimated_input_cost_usd": f"{input_cost:.9f}",
                "estimated_output_cost_usd": f"{output_cost:.9f}",
                "estimated_total_cost_usd": f"{total_cost:.9f}",
                "audio_duration_sec": wav_duration_seconds(output_path),
                "audio_size_bytes": len(audio_bytes),
                "usage_raw_json": json.dumps(
                    jsonable(usage),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "error_message": "",
            }
            append_ledger(ledger_path, row)
            return row

        except Exception as exc:
            last_exc = exc
            if attempt < max_retries and is_retryable(exc):
                delay = retry_base_seconds * (2 ** attempt)
                print(
                    f"    retry {attempt + 1}/{max_retries} "
                    f"in {delay:.1f}s: {type(exc).__name__}: {exc}"
                )
                time.sleep(delay)
                continue
            break

    latency_ms = round(
        (time.perf_counter() - total_started) * 1000
    )
    error_text = (
        f"{type(last_exc).__name__}: {last_exc}"
        if last_exc is not None
        else "Unknown TTS generation error"
    )
    append_ledger(
        ledger_path,
        {
            **base_row,
            "status": "failed",
            "latency_ms": latency_ms,
            "error_message": error_text,
        },
    )
    raise RuntimeError(error_text)


def scene_manifest(
    *,
    poem: dict[str, str],
    scene: dict[str, Any],
    voice: str,
    style: str,
    poem_audio: Path,
    explanation_audio: Path,
) -> dict[str, Any]:
    return {
        "scene_id": scene["scene_id"],
        "poem_id": int(poem["poem_id"]),
        "scene_no": scene["scene_no"],
        "original_line": scene["original_line"],
        "child_explanation_line": scene["child_explanation_line"],
        "tts": {
            "model": MODEL,
            "voice": voice,
            "style_sha256": sha256_text(style),
            "audio": {
                "poem": {
                    "path": poem_audio.as_posix(),
                    "exists": poem_audio.exists(),
                },
                "explanation": {
                    "path": explanation_audio.as_posix(),
                    "exists": explanation_audio.exists(),
                },
            },
        },
        "image": {
            "background": (
                poem_audio.parent.parent / "image" / "background.webp"
            ).as_posix(),
        },
        "text": {
            "poem_bpmf": (
                poem_audio.parent.parent / "text" / "poem_bpmf.png"
            ).as_posix(),
        },
    }


def poem_manifest(
    *,
    poem: dict[str, str],
    scenes: list[dict[str, Any]],
    assets_root: Path,
) -> dict[str, Any]:
    pid = int(poem["poem_id"])
    poem_dir = assets_root / f"p{pid:03d}"
    title_audio = poem_dir / "audio" / "title.wav"
    author_audio = poem_dir / "audio" / "author.wav"

    scene_entries = []
    scene_tts_complete = True

    for scene in scenes:
        scene_dir = poem_dir / f"s{scene['scene_no']:02d}"
        poem_audio = scene_dir / "audio" / "poem.wav"
        explanation_audio = scene_dir / "audio" / "explanation.wav"

        # Blank physical lines are separators and do not require TTS.
        if (
            not scene["original_line"].strip()
            and not scene["child_explanation_line"].strip()
        ):
            complete = True
        else:
            complete = (
                poem_audio.exists()
                and explanation_audio.exists()
            )

        if not complete:
            scene_tts_complete = False

        scene_entries.append(
            {
                "scene_id": scene["scene_id"],
                "scene_no": scene["scene_no"],
                "manifest": (
                    scene_dir / "scene.json"
                ).as_posix(),
                "tts_complete": complete,
            }
        )

    poem_level_tts_complete = (
        title_audio.exists() and author_audio.exists()
    )

    return {
        "poem_id": pid,
        "title": poem["title"],
        "author": poem["author"],
        "poem_type": poem["poem_type"],
        "recommended_age": int(poem["recommended_age"]),
        "popularity_level": int(poem["popularity_level"]),
        "scene_count": len(scenes),
        "tts_complete": (
            poem_level_tts_complete and scene_tts_complete
        ),
        "tts": {
            "model": MODEL,
            "audio": {
                "title": {
                    "path": title_audio.as_posix(),
                    "exists": title_audio.exists(),
                },
                "author": {
                    "path": author_audio.as_posix(),
                    "exists": author_audio.exists(),
                },
            },
            "poem_level_complete": poem_level_tts_complete,
            "scene_level_complete": scene_tts_complete,
        },
        "scenes": scene_entries,
    }

def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Generate production TTS assets for poem300."
    )
    parser.add_argument(
        "--age",
        type=int,
        nargs="+",
        choices=(6, 7, 8, 9),
        help="Generate only exact recommended-age groups, e.g. --age 6 or --age 6 7.",
    )
    parser.add_argument(
        "--poem-id",
        type=int,
        help="Generate only one poem_id.",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        choices=ALL_TYPES,
        default=list(DEFAULT_TYPES),
        help=(
            "Audio asset types to generate. "
            "Default: poem explanation. "
            "Example: --types title author"
        ),
    )
    parser.add_argument(
        "--start-poem-id",
        type=int,
        help="Only process poem_id >= this value.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit selected poems after filters.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate and overwrite existing WAV files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned work without API calls or file writes.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop the batch on the first failed asset.",
    )
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
    )
    parser.add_argument(
        "--style",
        default=DEFAULT_STYLE,
    )
    parser.add_argument(
        "--poems",
        default="data/poems.csv",
    )
    parser.add_argument(
        "--assets-root",
        default="assets",
    )
    parser.add_argument(
        "--ledger",
        default="data/tts_usage.csv",
    )
    parser.add_argument(
        "--pronunciation-overrides",
        default="data/tts_pronunciation_overrides.json",
        help=(
            "JSON pronunciation hints for title/author/poem assets."
        ),
    )
    parser.add_argument(
        "--pronunciation-qa-only",
        action="store_true",
        help=(
            "Process only assets listed in the pronunciation override "
            "registry. Use with --force to regenerate reviewed audio."
        ),
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=2.0,
    )
    parser.add_argument(
        "--request-delay-ms",
        type=int,
        default=0,
        help="Optional delay after each successful API request.",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be > 0")
    if args.max_retries < 0:
        parser.error("--max-retries must be >= 0")
    if args.retry_base_seconds < 0:
        parser.error("--retry-base-seconds must be >= 0")
    if args.request_delay_ms < 0:
        parser.error("--request-delay-ms must be >= 0")

    requested_types = set(args.types)
    pronunciation_overrides = load_pronunciation_overrides(
        Path(args.pronunciation_overrides)
    )

    poems = read_poems(Path(args.poems))
    ages = set(args.age) if args.age else None
    selected = select_poems(
        poems,
        ages=ages,
        poem_id=args.poem_id,
        start_poem_id=args.start_poem_id,
        limit=args.limit,
    )

    if not selected:
        print("No poems matched the requested filters.")
        return 0

    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid.uuid4().hex[:8]
    )
    assets_root = Path(args.assets_root)
    ledger_path = Path(args.ledger)

    total_scenes = sum(
        len(build_scenes(poem))
        for poem in selected
    )
    poem_level_assets_per_poem = sum(
        audio_type in requested_types
        for audio_type in ("title", "author")
    )
    scene_assets_per_scene = sum(
        audio_type in requested_types
        for audio_type in ("poem", "explanation")
    )
    total_assets = (
        len(selected) * poem_level_assets_per_poem
        + total_scenes * scene_assets_per_scene
    )

    print(f"run_id={run_id}")
    print(f"model={MODEL} voice={args.voice}")
    print(
        "types="
        + ",".join(
            audio_type
            for audio_type in ALL_TYPES
            if audio_type in requested_types
        )
    )
    print(
        f"selected_poems={len(selected)} "
        f"scenes={total_scenes} "
        f"audio_assets={total_assets}"
    )
    print(
        "ages="
        + (
            ",".join(str(age) for age in sorted(ages))
            if ages
            else "ALL"
        )
    )
    print(f"force={args.force} dry_run={args.dry_run}")

    if not args.dry_run:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            print(
                "ERROR: GEMINI_API_KEY is missing. "
                "Put it in .env or the environment.",
                file=sys.stderr,
            )
            return 2
        client = genai.Client(api_key=api_key)
        ensure_ledger(ledger_path)
    else:
        client = None

    generated = 0
    skipped = 0
    failed = 0
    planned = 0
    new_audio_seconds = 0.0
    new_estimated_cost = 0.0

    for poem_index, poem in enumerate(selected, start=1):
        pid = int(poem["poem_id"])
        scenes = build_scenes(poem)
        poem_dir = assets_root / f"p{pid:03d}"

        print(
            f"\n[{poem_index}/{len(selected)}] "
            f"p{pid:03d} {poem['title']} "
            f"age={poem['recommended_age']} "
            f"scenes={len(scenes)}"
        )

        if not args.dry_run:
            poem_dir.mkdir(parents=True, exist_ok=True)

        poem_failed = False
        poem_audio_dir = poem_dir / "audio"

        if not args.dry_run:
            poem_audio_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

        poem_level_jobs = []
        if "title" in requested_types:
            poem_level_jobs.append(
                (
                    "title",
                    poem["title"],
                    poem_audio_dir / "title.wav",
                )
            )
        if "author" in requested_types:
            poem_level_jobs.append(
                (
                    "author",
                    poem["author"],
                    poem_audio_dir / "author.wav",
                )
            )

        for audio_type, text, output_path in poem_level_jobs:
            override = pronunciation_overrides.get(
                (pid, audio_type, 0)
            )
            if args.pronunciation_qa_only and not override:
                continue

            effective_style = style_with_pronunciation(
                args.style,
                text=text,
                override=override,
            )

            if output_path.exists() and not args.force:
                skipped += 1
                print(
                    f"  poem {audio_type}: SKIP existing"
                )
                continue

            planned += 1

            if args.dry_run:
                action = (
                    "OVERWRITE"
                    if output_path.exists()
                    else "GENERATE"
                )
                print(
                    f"  poem {audio_type}: {action} "
                    f"-> {output_path.as_posix()}"
                )
                continue

            print(
                f"  poem {audio_type}: GENERATE "
                f"-> {output_path.as_posix()}"
            )

            meta_scene = {
                "scene_id": (
                    f"p{pid:03d}_{audio_type}"
                ),
                "scene_no": 0,
            }

            try:
                row = generate_audio(
                    client,
                    run_id=run_id,
                    poem=poem,
                    scene=meta_scene,
                    audio_type=audio_type,
                    text=text,
                    voice=args.voice,
                    style=effective_style,
                    output_path=output_path,
                    ledger_path=ledger_path,
                    max_retries=args.max_retries,
                    retry_base_seconds=(
                        args.retry_base_seconds
                    ),
                )
                generated += 1
                new_audio_seconds += float(
                    row["audio_duration_sec"]
                )
                new_estimated_cost += float(
                    row["estimated_total_cost_usd"]
                )
                print(
                    "    OK "
                    f"{row['audio_duration_sec']}s "
                    f"tokens={row['output_audio_tokens']} "
                    f"est_usd="
                    f"{row['estimated_total_cost_usd']}"
                )

                if args.request_delay_ms:
                    time.sleep(
                        args.request_delay_ms / 1000
                    )

            except Exception as exc:
                failed += 1
                poem_failed = True
                print(
                    f"    FAILED: {exc}",
                    file=sys.stderr,
                )
                if args.fail_fast:
                    return 1

        for scene in scenes:
            scene_dir = poem_dir / f"s{scene['scene_no']:02d}"
            audio_dir = scene_dir / "audio"
            image_dir = scene_dir / "image"
            text_dir = scene_dir / "text"

            if not args.dry_run:
                audio_dir.mkdir(parents=True, exist_ok=True)
                image_dir.mkdir(parents=True, exist_ok=True)
                text_dir.mkdir(parents=True, exist_ok=True)

            jobs = []
            if (
                "poem" in requested_types
                and scene["original_line"].strip()
            ):
                jobs.append(
                    (
                        "poem",
                        scene["original_line"],
                        audio_dir / "poem.wav",
                    )
                )
            if (
                "explanation" in requested_types
                and scene["child_explanation_line"].strip()
            ):
                jobs.append(
                    (
                        "explanation",
                        scene["child_explanation_line"],
                        audio_dir / "explanation.wav",
                    )
                )

            for audio_type, text, output_path in jobs:
                override = pronunciation_overrides.get(
                    (
                        pid,
                        audio_type,
                        int(scene["scene_no"]),
                    )
                )
                if args.pronunciation_qa_only and not override:
                    continue

                effective_style = style_with_pronunciation(
                    args.style,
                    text=text,
                    override=override,
                )

                if output_path.exists() and not args.force:
                    skipped += 1
                    print(
                        f"  {scene['scene_id']} {audio_type}: "
                        "SKIP existing"
                    )
                    continue

                planned += 1

                if args.dry_run:
                    action = "OVERWRITE" if output_path.exists() else "GENERATE"
                    print(
                        f"  {scene['scene_id']} {audio_type}: "
                        f"{action} -> {output_path.as_posix()}"
                    )
                    continue

                print(
                    f"  {scene['scene_id']} {audio_type}: "
                    f"GENERATE -> {output_path.as_posix()}"
                )

                try:
                    row = generate_audio(
                        client,
                        run_id=run_id,
                        poem=poem,
                        scene=scene,
                        audio_type=audio_type,
                        text=text,
                        voice=args.voice,
                        style=effective_style,
                        output_path=output_path,
                        ledger_path=ledger_path,
                        max_retries=args.max_retries,
                        retry_base_seconds=args.retry_base_seconds,
                    )
                    generated += 1
                    new_audio_seconds += float(
                        row["audio_duration_sec"]
                    )
                    new_estimated_cost += float(
                        row["estimated_total_cost_usd"]
                    )
                    print(
                        "    OK "
                        f"{row['audio_duration_sec']}s "
                        f"tokens={row['output_audio_tokens']} "
                        f"est_usd={row['estimated_total_cost_usd']}"
                    )

                    if args.request_delay_ms:
                        time.sleep(args.request_delay_ms / 1000)

                except Exception as exc:
                    failed += 1
                    poem_failed = True
                    print(f"    FAILED: {exc}", file=sys.stderr)
                    if args.fail_fast:
                        return 1

            if not args.dry_run:
                write_json(
                    scene_dir / "scene.json",
                    scene_manifest(
                        poem=poem,
                        scene=scene,
                        voice=args.voice,
                        style=args.style,
                        poem_audio=audio_dir / "poem.wav",
                        explanation_audio=(
                            audio_dir / "explanation.wav"
                        ),
                    ),
                )

        if not args.dry_run:
            write_json(
                poem_dir / "poem.json",
                poem_manifest(
                    poem=poem,
                    scenes=scenes,
                    assets_root=assets_root,
                ),
            )

        if poem_failed:
            print("  poem_status=PARTIAL_OR_FAILED")
        elif not args.dry_run:
            print("  poem_status=OK")

    print("\nSummary")
    print(f"selected_poems={len(selected)}")
    print(f"scenes={total_scenes}")
    print(f"audio_assets_total={total_assets}")
    print(f"planned_api_requests={planned}")
    print(f"generated={generated}")
    print(f"skipped_existing={skipped}")
    print(f"failed={failed}")
    print(f"new_audio_seconds={new_audio_seconds:.3f}")
    print(f"new_estimated_cost_usd={new_estimated_cost:.9f}")
    print(f"ledger={ledger_path.as_posix()}")

    if args.dry_run:
        print("dry_run=true; no API calls or file writes were made.")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
