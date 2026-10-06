#!/usr/bin/env python3
"""One-poem Gemini TTS comparison POC."""

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

MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")
PRICING_SNAPSHOT = {
    "pricing_as_of": "2026-10-06",
    "pricing_basis": "paid_standard_list_price",
    "gemini-3.8-flash-tts": {"input_text_per_million": 0.50, "output_audio_per_million": 9.00},
    "gemini-3.8-flash-lite-tts": {"input_text_per_million": 0.50, "output_audio_per_million": 6.00},
}
DEFAULT_VOICE = "Kore"
DEFAULT_STYLE = (
    "Taiwan Mandarin. Warm, clear, gentle children's audiobook narration for ages 6 to 9. "
    "Recite exactly as written. Do not add, omit, paraphrase, explain, or repeat any words. "
    "Use natural pauses at Chinese punctuation and a calm educational pace."
)
LEDGER_FIELDS = [
    "usage_id","run_id","created_at_utc","poem_id","title","scene_id","scene_no",
    "audio_type","model","voice","style_sha256","text_sha256","text_chars",
    "interaction_id","status","latency_ms","input_text_tokens","output_audio_tokens",
    "total_input_tokens","total_output_tokens","total_tokens","estimated_input_cost_usd",
    "estimated_output_cost_usd","estimated_total_cost_usd","pricing_as_of","pricing_basis",
    "audio_duration_sec","audio_size_bytes","audio_file","usage_raw_json","error_message",
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

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def normalize_newlines(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")

def read_poem(poems_csv: Path, title: str | None, poem_id: int | None) -> dict[str, str]:
    with poems_csv.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    matches = []
    for row in rows:
        if poem_id is not None and int(row["poem_id"]) == poem_id:
            matches.append(row)
        elif poem_id is None and title is not None and row["title"].strip() == title.strip():
            matches.append(row)
    if len(matches) != 1:
        selector = f"poem_id={poem_id}" if poem_id is not None else f"title={title!r}"
        raise ValueError(f"Expected exactly one poem for {selector}, found {len(matches)}")
    return matches[0]

def poem_scenes(poem: dict[str, str]) -> list[dict[str, Any]]:
    originals = normalize_newlines(poem["content"]).split("\n")
    explanations = normalize_newlines(poem["child_explanation_6_8"]).split("\n")
    if len(originals) != len(explanations):
        raise ValueError(
            f"poem_id={poem['poem_id']}: content lines={len(originals)} != explanation lines={len(explanations)}"
        )
    pid = int(poem["poem_id"])
    return [
        {
            "scene_id": f"p{pid:03d}_s{scene_no:02d}",
            "scene_no": scene_no,
            "poem": original,
            "explanation": explanation,
        }
        for scene_no, (original, explanation) in enumerate(zip(originals, explanations), start=1)
    ]

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

def calculate_cost(model: str, input_text_tokens: int, output_audio_tokens: int) -> tuple[float, float, float]:
    pricing = PRICING_SNAPSHOT[model]
    input_cost = input_text_tokens * pricing["input_text_per_million"] / 1_000_000
    output_cost = output_audio_tokens * pricing["output_audio_per_million"] / 1_000_000
    return input_cost, output_cost, input_cost + output_cost

def generate_one(
    client: genai.Client, *, run_id: str, poem: dict[str, str], scene: dict[str, Any],
    audio_type: str, text: str, model: str, voice: str, style: str,
    output_dir: Path, ledger: Path,
) -> dict[str, Any]:
    usage_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    safe_model = model.replace("gemini-", "").replace("/", "-")
    filename = f"{scene['scene_id']}_{audio_type}_{safe_model}.wav"
    audio_path = output_dir / filename
    started = time.perf_counter()
    base_row: dict[str, Any] = {
        "usage_id": usage_id,
        "run_id": run_id,
        "created_at_utc": created_at,
        "poem_id": poem["poem_id"],
        "title": poem["title"],
        "scene_id": scene["scene_id"],
        "scene_no": scene["scene_no"],
        "audio_type": audio_type,
        "model": model,
        "voice": voice,
        "style_sha256": sha256_text(style),
        "text_sha256": sha256_text(text),
        "text_chars": len(text),
        "pricing_as_of": PRICING_SNAPSHOT["pricing_as_of"],
        "pricing_basis": PRICING_SNAPSHOT["pricing_basis"],
        "audio_file": str(audio_path).replace("\\", "/"),
    }
    try:
        interaction = client.interactions.create(
            model=model,
            input=[{
                "type": "user_input",
                "content": [{
                    "type": "text",
                    "text": text,
                    "annotations": [{"type": "speech_metadata", "style": style}],
                }],
            }],
            response_format={"type": "audio", "mime_type": "audio/wav", "sample_rate": 24000},
            generation_config={"speech_config": [{"voice": voice}]},
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        output_audio = obj_get(interaction, "output_audio")
        audio_b64 = obj_get(output_audio, "data")
        if not audio_b64:
            raise RuntimeError("API response completed without output_audio.data")
        audio_bytes = base64.b64decode(audio_b64)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(audio_bytes)

        usage = obj_get(interaction, "usage")
        input_text_tokens = modality_tokens(usage, "input_tokens_by_modality", "text")
        output_audio_tokens = modality_tokens(usage, "output_tokens_by_modality", "audio")
        total_input_tokens = int(obj_get(usage, "total_input_tokens", 0) or 0)
        total_output_tokens = int(obj_get(usage, "total_output_tokens", 0) or 0)
        total_tokens = int(obj_get(usage, "total_tokens", 0) or 0)
        if input_text_tokens == 0:
            input_text_tokens = total_input_tokens
        if output_audio_tokens == 0:
            output_audio_tokens = total_output_tokens

        in_cost, out_cost, total_cost = calculate_cost(model, input_text_tokens, output_audio_tokens)
        row = {
            **base_row,
            "interaction_id": obj_get(interaction, "id", ""),
            "status": obj_get(interaction, "status", "completed") or "completed",
            "latency_ms": latency_ms,
            "input_text_tokens": input_text_tokens,
            "output_audio_tokens": output_audio_tokens,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "estimated_input_cost_usd": f"{in_cost:.9f}",
            "estimated_output_cost_usd": f"{out_cost:.9f}",
            "estimated_total_cost_usd": f"{total_cost:.9f}",
            "audio_duration_sec": wav_duration_seconds(audio_path),
            "audio_size_bytes": len(audio_bytes),
            "usage_raw_json": json.dumps(jsonable(usage), ensure_ascii=False, separators=(",", ":")),
            "error_message": "",
        }
        append_ledger(ledger, row)
        return row
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000)
        row = {**base_row, "status": "failed", "latency_ms": latency_ms, "error_message": f"{type(exc).__name__}: {exc}"}
        append_ledger(ledger, row)
        raise

def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Compare two Gemini TTS models on one poem.")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--title", default="春曉", help="Poem title. Default: 春曉")
    selector.add_argument("--poem-id", type=int, help="Poem ID; overrides title lookup.")
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument("--ledger", default="data/tts_usage.csv")
    parser.add_argument("--output-root", default="output/tts_poc")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--style", default=DEFAULT_STYLE)
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GEMINI_API_KEY is missing. Put it in .env or the environment.", file=sys.stderr)
        return 2

    poem = read_poem(Path(args.poems), None if args.poem_id is not None else args.title, args.poem_id)
    scenes = poem_scenes(poem)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    output_dir = Path(args.output_root) / run_id
    ledger = Path(args.ledger)
    client = genai.Client(api_key=api_key)

    print(f"run_id={run_id}")
    print(f"poem_id={poem['poem_id']} title={poem['title']} scenes={len(scenes)}")
    print(f"models={','.join(MODELS)} voice={args.voice}")
    print(f"requests={len(scenes) * 2 * len(MODELS)}")

    success_rows: list[dict[str, Any]] = []
    for model in MODELS:
        for scene in scenes:
            for audio_type, text in (("poem", scene["poem"]), ("explanation", scene["explanation"])):
                print(f"[{model}] {scene['scene_id']} {audio_type}: {text}")
                row = generate_one(
                    client, run_id=run_id, poem=poem, scene=scene, audio_type=audio_type,
                    text=text, model=model, voice=args.voice, style=args.style,
                    output_dir=output_dir, ledger=ledger,
                )
                success_rows.append(row)
                print(
                    "  -> "
                    f"{row['audio_duration_sec']}s, "
                    f"audio_tokens={row['output_audio_tokens']}, "
                    f"latency={row['latency_ms']}ms, "
                    f"est_usd={row['estimated_total_cost_usd']}"
                )

    print("\nSummary")
    for model in MODELS:
        rows = [r for r in success_rows if r["model"] == model]
        audio_sec = sum(float(r["audio_duration_sec"]) for r in rows)
        tokens = sum(int(r["output_audio_tokens"]) for r in rows)
        cost = sum(float(r["estimated_total_cost_usd"]) for r in rows)
        latency = sum(int(r["latency_ms"]) for r in rows)
        print(
            f"{model}: requests={len(rows)}, audio={audio_sec:.3f}s, "
            f"audio_tokens={tokens}, latency_total={latency}ms, est_cost_usd={cost:.9f}"
        )
    print(f"audio_dir={output_dir}")
    print(f"ledger={ledger}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
