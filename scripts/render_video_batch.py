#!/usr/bin/env python3
"""Batch-build timelines and render poem videos for one age group.

Default behavior is resume-safe:
- select approved poems for the requested recommended_age
- skip MP4 files that already exist
- preflight every remaining poem before any expensive video render starts
- build/write timeline.json for every remaining poem
- render only if the whole remaining batch passes preflight
- completed MP4 files remain resumable across later runs

This script does not upload anything to YouTube.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_POEMS = Path("data/poems.csv")
TIMELINE_SCRIPT = Path("scripts/build_video_timeline.py")
RENDER_SCRIPT = Path("scripts/render_video.py")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))


def select_poems(
    rows: list[dict[str, str]],
    *,
    age: int,
    include_unapproved: bool,
) -> list[dict[str, str]]:
    selected = [
        row
        for row in rows
        if int(row["recommended_age"]) == age
        and (
            include_unapproved
            or row.get(
                "visual_plan_status",
                "",
            ).strip().casefold()
            == "approved"
        )
    ]
    selected.sort(
        key=lambda row: int(row["poem_id"])
    )
    return selected


def output_path(
    *,
    poem_id: int,
    style: str,
) -> Path:
    return (
        Path("assets")
        / f"p{poem_id:03d}"
        / "video"
        / f"p{poem_id:03d}_{style.upper()}_1080p.mp4"
    )


def run(
    command: list[str],
) -> int:
    print(
        "$ "
        + " ".join(command),
        flush=True,
    )
    completed = subprocess.run(
        command,
        check=False,
    )
    return int(completed.returncode)


def timeline_command(
    *,
    poem_id: int,
    style: str,
) -> list[str]:
    return [
        sys.executable,
        str(TIMELINE_SCRIPT),
        "--poem-id",
        str(poem_id),
        "--style",
        style,
    ]


def render_command(
    *,
    poem_id: int,
    style: str,
    dry_run: bool,
    force: bool,
    ffmpeg: str | None,
) -> list[str]:
    command = [
        sys.executable,
        str(RENDER_SCRIPT),
        "--poem-id",
        str(poem_id),
        "--style",
        style,
    ]

    if dry_run:
        command.append("--dry-run")

    if force:
        command.append("--force")

    if ffmpeg:
        command.extend(
            [
                "--ffmpeg",
                ffmpeg,
            ]
        )

    return command


def format_elapsed(seconds: float) -> str:
    total = max(
        0,
        round(seconds),
    )
    minutes, sec = divmod(
        total,
        60,
    )
    hours, minutes = divmod(
        minutes,
        60,
    )

    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight and render all approved poems for one age group."
        )
    )
    parser.add_argument(
        "--age",
        type=int,
        choices=(6, 7, 8, 9),
        default=6,
    )
    parser.add_argument(
        "--style",
        default="B",
    )
    parser.add_argument(
        "--poems",
        default=str(DEFAULT_POEMS),
    )
    parser.add_argument(
        "--include-unapproved",
        action="store_true",
        help=(
            "Include poems whose visual_plan_status is not approved. "
            "Default: approved only."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-render existing MP4 files too. Default is resume-safe "
            "and skips existing outputs."
        ),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Build timelines and run render dry-runs, but do not create MP4s."
        ),
    )
    parser.add_argument(
        "--ffmpeg",
        help=(
            "Optional ffmpeg executable/full path forwarded to "
            "render_video.py."
        ),
    )
    args = parser.parse_args()

    started = time.monotonic()

    poems = select_poems(
        read_csv(
            Path(args.poems)
        ),
        age=args.age,
        include_unapproved=args.include_unapproved,
    )

    if not poems:
        print(
            "ERROR: no poems matched the requested age/status selection"
        )
        return 2

    selected: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []

    print(
        f"age={args.age} "
        f"style={args.style.upper()} "
        f"selected={len(poems)} "
        "approved_only="
        + str(
            not args.include_unapproved
        ).lower()
    )

    for poem in poems:
        pid = int(poem["poem_id"])
        item = {
            "poem_id": pid,
            "title": poem["title"],
            "output": output_path(
                poem_id=pid,
                style=args.style,
            ),
        }

        if (
            item["output"].exists()
            and not args.force
        ):
            existing.append(item)
            print(
                f"SKIP p{pid:03d} {poem['title']} "
                f"existing={item['output'].as_posix()}"
            )
        else:
            selected.append(item)

    print("")
    print(
        "plan="
        f"existing_skip:{len(existing)} "
        f"preflight:{len(selected)} "
        f"render:{0 if args.preflight_only else len(selected)}"
    )

    if not selected:
        print("")
        print("PASS: no remaining videos to render.")
        print(
            f"elapsed={format_elapsed(time.monotonic() - started)}"
        )
        return 0

    # Phase 1: build timelines and validate every remaining poem before
    # starting any expensive MP4 rendering.
    print("")
    print("=== PHASE 1/2: timeline + composer preflight ===")

    preflight_passed: list[dict[str, Any]] = []
    preflight_failed: list[dict[str, Any]] = []

    for index, item in enumerate(
        selected,
        start=1,
    ):
        pid = int(item["poem_id"])
        title = str(item["title"])

        print("")
        print(
            f"[preflight {index:02d}/{len(selected):02d}] "
            f"p{pid:03d} {title}"
        )

        timeline_rc = run(
            timeline_command(
                poem_id=pid,
                style=args.style,
            )
        )
        if timeline_rc != 0:
            item["stage"] = "timeline"
            item["returncode"] = timeline_rc
            preflight_failed.append(item)
            continue

        render_rc = run(
            render_command(
                poem_id=pid,
                style=args.style,
                dry_run=True,
                force=args.force,
                ffmpeg=args.ffmpeg,
            )
        )
        if render_rc != 0:
            item["stage"] = "render_dry_run"
            item["returncode"] = render_rc
            preflight_failed.append(item)
            continue

        preflight_passed.append(item)

    print("")
    print(
        "preflight_summary="
        f"passed:{len(preflight_passed)} "
        f"failed:{len(preflight_failed)}"
    )

    if preflight_failed:
        print("")
        print(
            "STOP: at least one remaining poem failed preflight. "
            "No new MP4 rendering was started."
        )
        for item in preflight_failed:
            print(
                "  FAIL "
                f"p{int(item['poem_id']):03d} "
                f"{item['title']} "
                f"stage={item['stage']} "
                f"rc={item['returncode']}"
            )
        print(
            f"elapsed={format_elapsed(time.monotonic() - started)}"
        )
        return 1

    if args.preflight_only:
        print("")
        print("PASS preflight_only=true")
        print(
            f"ready_to_render={len(preflight_passed)}"
        )
        print(
            f"existing_skipped={len(existing)}"
        )
        print(
            f"elapsed={format_elapsed(time.monotonic() - started)}"
        )
        return 0

    # Phase 2: now that all remaining poems have passed, render them.
    print("")
    print("=== PHASE 2/2: render MP4 ===")

    rendered: list[dict[str, Any]] = []
    render_failed: list[dict[str, Any]] = []

    for index, item in enumerate(
        preflight_passed,
        start=1,
    ):
        pid = int(item["poem_id"])
        title = str(item["title"])

        print("")
        print(
            f"[render {index:02d}/{len(preflight_passed):02d}] "
            f"p{pid:03d} {title}"
        )

        rc = run(
            render_command(
                poem_id=pid,
                style=args.style,
                dry_run=False,
                force=args.force,
                ffmpeg=args.ffmpeg,
            )
        )

        if rc == 0:
            rendered.append(item)
        else:
            item["stage"] = "render"
            item["returncode"] = rc
            render_failed.append(item)

    print("")
    print("=== SUMMARY ===")
    print(f"age={args.age}")
    print(f"selected_total={len(poems)}")
    print(f"existing_skipped={len(existing)}")
    print(f"rendered={len(rendered)}")
    print(f"render_failed={len(render_failed)}")
    print(
        f"elapsed={format_elapsed(time.monotonic() - started)}"
    )

    if render_failed:
        for item in render_failed:
            print(
                "  FAIL "
                f"p{int(item['poem_id']):03d} "
                f"{item['title']} "
                f"rc={item['returncode']}"
            )
        print(
            "Resume after fixing by running the same command again; "
            "completed MP4 files will be skipped."
        )
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
