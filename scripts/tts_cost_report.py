#!/usr/bin/env python3
"""Report per-poem Gemini TTS cost from data/tts_usage.csv."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

MODEL = "gemini-3.8-flash-lite-tts"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def as_float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def as_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def is_production_asset(row: dict[str, str]) -> bool:
    path = (row.get("audio_file") or "").replace("\\", "/")
    return path.startswith("assets/")


def current_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    latest: dict[tuple[str, str, str], dict[str, str]] = {}

    for row in rows:
        key = (
            row.get("poem_id", ""),
            row.get("scene_id", ""),
            row.get("audio_type", ""),
        )
        previous = latest.get(key)
        if previous is None:
            latest[key] = row
            continue

        if (row.get("created_at_utc") or "") >= (
            previous.get("created_at_utc") or ""
        ):
            latest[key] = row

    existing = []
    for row in latest.values():
        audio_file = row.get("audio_file") or ""
        if audio_file and Path(audio_file).exists():
            existing.append(row)

    return existing


def aggregate(
    rows: list[dict[str, str]],
    poems: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        grouped[row["poem_id"]].append(row)

    result = []
    for poem_id, poem_rows in grouped.items():
        poem = poems.get(poem_id, {})
        poem_audio = [
            row for row in poem_rows
            if row.get("audio_type") == "poem"
        ]
        explanation_audio = [
            row for row in poem_rows
            if row.get("audio_type") == "explanation"
        ]

        result.append(
            {
                "poem_id": as_int(poem_id),
                "title": poem.get(
                    "title",
                    poem_rows[0].get("title", ""),
                ),
                "age": as_int(poem.get("recommended_age")),
                "scenes": len(
                    {
                        row.get("scene_id", "")
                        for row in poem_rows
                        if row.get("scene_id")
                    }
                ),
                "requests": len(poem_rows),
                "poem_sec": sum(
                    as_float(row.get("audio_duration_sec"))
                    for row in poem_audio
                ),
                "explanation_sec": sum(
                    as_float(row.get("audio_duration_sec"))
                    for row in explanation_audio
                ),
                "total_sec": sum(
                    as_float(row.get("audio_duration_sec"))
                    for row in poem_rows
                ),
                "cost_usd": sum(
                    as_float(row.get("estimated_total_cost_usd"))
                    for row in poem_rows
                ),
            }
        )

    result.sort(key=lambda row: row["poem_id"])
    return result


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No matching TTS usage rows.")
        return

    print(
        f"{'poem_id':>7}  "
        f"{'age':>3}  "
        f"{'title':<16}  "
        f"{'scenes':>6}  "
        f"{'req':>4}  "
        f"{'poem_s':>8}  "
        f"{'explain_s':>9}  "
        f"{'total_s':>8}  "
        f"{'cost_usd':>10}"
    )
    print("-" * 92)

    for row in rows:
        title = str(row["title"])
        if len(title) > 16:
            title = title[:15] + "…"

        print(
            f"{row['poem_id']:>7}  "
            f"{row['age']:>3}  "
            f"{title:<16}  "
            f"{row['scenes']:>6}  "
            f"{row['requests']:>4}  "
            f"{row['poem_sec']:>8.2f}  "
            f"{row['explanation_sec']:>9.2f}  "
            f"{row['total_sec']:>8.2f}  "
            f"{row['cost_usd']:>10.6f}"
        )

    total_requests = sum(row["requests"] for row in rows)
    total_seconds = sum(row["total_sec"] for row in rows)
    total_cost = sum(row["cost_usd"] for row in rows)

    print("-" * 92)
    print(
        f"TOTAL poems={len(rows)} "
        f"requests={total_requests} "
        f"audio_sec={total_seconds:.2f} "
        f"cost_usd={total_cost:.6f}"
    )


def write_report_csv(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "poem_id",
        "title",
        "age",
        "scenes",
        "requests",
        "poem_sec",
        "explanation_sec",
        "total_sec",
        "cost_usd",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "poem_sec": f"{row['poem_sec']:.3f}",
                    "explanation_sec": (
                        f"{row['explanation_sec']:.3f}"
                    ),
                    "total_sec": f"{row['total_sec']:.3f}",
                    "cost_usd": f"{row['cost_usd']:.9f}",
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report per-poem production TTS cost."
    )
    parser.add_argument(
        "--ledger",
        default="data/tts_usage.csv",
    )
    parser.add_argument(
        "--poems",
        default="data/poems.csv",
    )
    parser.add_argument(
        "--mode",
        choices=("current", "spend"),
        default="current",
        help=(
            "current: latest completed row per Scene/audio type "
            "and only files currently present; "
            "spend: all completed requests."
        ),
    )
    parser.add_argument(
        "--age",
        type=int,
        nargs="+",
        choices=(6, 7, 8, 9),
        help="Filter exact recommended-age groups.",
    )
    parser.add_argument(
        "--poem-id",
        type=int,
        help="Filter one poem.",
    )
    parser.add_argument(
        "--include-poc",
        action="store_true",
        help="Include non-assets/ POC requests.",
    )
    parser.add_argument(
        "--csv",
        help="Optional output CSV path.",
    )
    args = parser.parse_args()

    ledger_rows = read_csv(Path(args.ledger))
    poem_rows = read_csv(Path(args.poems))
    poems = {row["poem_id"]: row for row in poem_rows}

    rows = [
        row
        for row in ledger_rows
        if row.get("model") == MODEL
        and row.get("status") not in ("", "failed")
    ]

    if not args.include_poc:
        rows = [row for row in rows if is_production_asset(row)]

    if args.poem_id is not None:
        rows = [
            row
            for row in rows
            if as_int(row.get("poem_id")) == args.poem_id
        ]

    if args.age:
        ages = set(args.age)
        rows = [
            row
            for row in rows
            if as_int(
                poems.get(
                    row.get("poem_id", ""),
                    {},
                ).get("recommended_age")
            )
            in ages
        ]

    if args.mode == "current":
        rows = current_rows(rows)

    report = aggregate(rows, poems)
    print_table(report)

    if args.csv:
        output_path = Path(args.csv)
        write_report_csv(output_path, report)
        print(f"report_csv={output_path.as_posix()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
