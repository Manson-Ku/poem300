#!/usr/bin/env python3
"""List local BGM tracks and manage poem-level BGM selections."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


DEFAULT_BGM_DIR = Path("bgm")
DEFAULT_MAP = Path("data/poem_bgm.csv")
SUPPORTED = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
}


def discover_bgm(directory: Path) -> list[Path]:
    if not directory.exists():
        return []

    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in SUPPORTED
        ),
        key=lambda path: path.name.casefold(),
    )


def resolve_track(
    directory: Path,
    value: str,
) -> Path:
    requested = Path(value)

    direct = (
        requested
        if requested.is_absolute()
        else directory / requested
    )
    if direct.exists() and direct.is_file():
        return direct

    tracks = discover_bgm(directory)
    key = value.casefold()

    exact_name = [
        path
        for path in tracks
        if path.name.casefold() == key
    ]
    if len(exact_name) == 1:
        return exact_name[0]

    exact_stem = [
        path
        for path in tracks
        if path.stem.casefold() == key
    ]
    if len(exact_stem) == 1:
        return exact_stem[0]

    if len(exact_stem) > 1:
        names = ", ".join(
            path.name
            for path in exact_stem
        )
        raise ValueError(
            f"ambiguous BGM stem {value!r}: {names}"
        )

    raise FileNotFoundError(
        f"BGM not found under {directory.as_posix()}: {value}"
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))


def write_rows(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    rows.sort(
        key=lambda row: int(row["poem_id"])
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "poem_id",
                "bgm_file",
                "gain_db",
                "notes",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List bgm/ tracks or set one poem's BGM selection."
        )
    )
    parser.add_argument(
        "--bgm-dir",
        default=str(DEFAULT_BGM_DIR),
    )
    parser.add_argument(
        "--map",
        default=str(DEFAULT_MAP),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List local selectable BGM files.",
    )
    parser.add_argument(
        "--poem-id",
        type=int,
    )

    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--set",
        dest="set_bgm",
        help="BGM filename or unique stem under bgm/.",
    )
    action.add_argument(
        "--clear",
        action="store_true",
        help="Remove this poem's persistent BGM selection.",
    )

    parser.add_argument(
        "--gain-db",
        type=float,
        default=0.0,
        help=(
            "Per-poem trim applied after BGM loudness normalization. "
            "0 dB is the normal starting point."
        ),
    )
    parser.add_argument(
        "--notes",
        default="",
    )
    args = parser.parse_args()

    bgm_dir = Path(args.bgm_dir)
    map_path = Path(args.map)

    if args.list:
        tracks = discover_bgm(bgm_dir)
        print(
            f"bgm_dir={bgm_dir.as_posix()} "
            f"tracks={len(tracks)}"
        )
        for index, path in enumerate(
            tracks,
            start=1,
        ):
            print(f"{index:02d}  {path.name}")

        if not args.poem_id and not args.set_bgm and not args.clear:
            return 0

    if args.poem_id is None:
        if args.set_bgm or args.clear:
            parser.error(
                "--poem-id is required with --set/--clear"
            )
        if not args.list:
            parser.error(
                "use --list or provide --poem-id with --set/--clear"
            )
        return 0

    rows = read_rows(map_path)
    by_poem = {
        int(row["poem_id"]): row
        for row in rows
        if row.get("poem_id", "").strip()
    }

    pid = int(args.poem_id)

    if args.clear:
        by_poem.pop(pid, None)
        write_rows(
            map_path,
            list(by_poem.values()),
        )
        print(
            f"p{pid:03d}: BGM selection cleared "
            f"-> {map_path.as_posix()}"
        )
        return 0

    if args.set_bgm:
        track = resolve_track(
            bgm_dir,
            args.set_bgm,
        )
        by_poem[pid] = {
            "poem_id": str(pid),
            "bgm_file": track.name,
            "gain_db": f"{args.gain_db:g}",
            "notes": args.notes,
        }
        write_rows(
            map_path,
            list(by_poem.values()),
        )
        print(
            f"p{pid:03d}: bgm={track.name} "
            f"gain_db={args.gain_db:g} "
            f"-> {map_path.as_posix()}"
        )
        return 0

    current = by_poem.get(pid)
    if current:
        print(
            f"p{pid:03d}: "
            f"bgm={current['bgm_file']} "
            f"gain_db={current.get('gain_db', '0')}"
        )
    else:
        print(f"p{pid:03d}: bgm=none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
