#!/usr/bin/env python3
"""Validate pronunciation QA registries against poems.csv.

This is a data/contract QA step. It does not inspect audio acoustically.
It verifies:
- bopomofo IVS override source positions
- reviewed font-gap source positions
- TTS pronunciation override source text
- no duplicate override keys

Exit code is non-zero when unresolved font gaps remain unless
--allow-font-gaps is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def read_poems(path: Path) -> dict[int, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return {int(row["poem_id"]): row for row in rows}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_text(
    poem: dict[str, str],
    *,
    field: str,
    line_no: int,
) -> str:
    if field == "title":
        if line_no != 0:
            raise ValueError("title line_no must be 0")
        return poem["title"]

    if field != "content":
        raise ValueError(f"unsupported field: {field}")

    lines = (
        (poem["content"] or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )
    if line_no < 1 or line_no > len(lines):
        raise ValueError(
            f"content line_no={line_no} out of range"
        )
    return lines[line_no - 1]


def validate_char_item(
    *,
    poems: dict[int, dict[str, str]],
    item: dict[str, Any],
    label: str,
) -> str | None:
    pid = int(item["poem_id"])
    poem = poems.get(pid)
    if poem is None:
        return f"{label}: poem_id={pid} not found"

    field = str(item["field"])
    line_no = int(item["line_no"])
    text = source_text(
        poem,
        field=field,
        line_no=line_no,
    )
    index = int(item["char_index"])
    character = str(item["character"])

    chars = list(text)
    if index < 0 or index >= len(chars):
        return (
            f"{label}: p{pid:03d} {field} line={line_no} "
            f"char_index={index} out of range"
        )

    if chars[index] != character:
        return (
            f"{label}: p{pid:03d} {field} line={line_no} "
            f"index={index} expected={character!r} "
            f"actual={chars[index]!r}"
        )

    return None


def expected_tts_text(
    poem: dict[str, str],
    *,
    audio_type: str,
    scene_no: int,
) -> str:
    if audio_type == "title":
        return poem["title"]
    if audio_type == "author":
        return poem["author"]
    if audio_type != "poem":
        raise ValueError(
            f"unsupported QA audio_type: {audio_type}"
        )

    lines = (
        (poem["content"] or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )
    if scene_no < 1 or scene_no > len(lines):
        raise ValueError(
            f"scene_no={scene_no} out of range"
        )
    return lines[scene_no - 1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate pronunciation QA data."
    )
    parser.add_argument(
        "--poems",
        default="data/poems.csv",
    )
    parser.add_argument(
        "--bopomofo-overrides",
        default="data/bopomofo_overrides.json",
    )
    parser.add_argument(
        "--qa",
        default="data/pronunciation_qa_age6.json",
    )
    parser.add_argument(
        "--tts-overrides",
        default="data/tts_pronunciation_overrides.json",
    )
    parser.add_argument(
        "--allow-font-gaps",
        action="store_true",
    )
    args = parser.parse_args()

    poems = read_poems(Path(args.poems))
    bop = read_json(Path(args.bopomofo_overrides))
    qa = read_json(Path(args.qa))
    tts = read_json(Path(args.tts_overrides))

    errors: list[str] = []

    seen_bop: set[tuple[int, str, int, int]] = set()
    for item in bop.get("items", []):
        key = (
            int(item["poem_id"]),
            str(item["field"]),
            int(item["line_no"]),
            int(item["char_index"]),
        )
        if key in seen_bop:
            errors.append(
                f"duplicate bopomofo override: {key}"
            )
        seen_bop.add(key)

        error = validate_char_item(
            poems=poems,
            item=item,
            label="bopomofo",
        )
        if error:
            errors.append(error)

    font_gaps = qa.get("font_gaps", [])
    for item in font_gaps:
        error = validate_char_item(
            poems=poems,
            item=item,
            label="font_gap",
        )
        if error:
            errors.append(error)

    seen_tts: set[tuple[int, str, int]] = set()
    for item in tts.get("items", []):
        pid = int(item["poem_id"])
        audio_type = str(item["audio_type"])
        scene_no = int(item.get("scene_no", 0))
        key = (pid, audio_type, scene_no)

        if key in seen_tts:
            errors.append(
                f"duplicate TTS pronunciation override: {key}"
            )
        seen_tts.add(key)

        poem = poems.get(pid)
        if poem is None:
            errors.append(
                f"TTS override poem_id={pid} not found"
            )
            continue

        try:
            expected = expected_tts_text(
                poem,
                audio_type=audio_type,
                scene_no=scene_no,
            )
        except ValueError as exc:
            errors.append(
                f"TTS override {key}: {exc}"
            )
            continue

        actual = str(item.get("source_text", ""))
        if actual != expected:
            errors.append(
                f"TTS override {key} source mismatch: "
                f"expected={expected!r} actual={actual!r}"
            )

        for pronunciation in item.get(
            "pronunciations",
            [],
        ):
            character = str(
                pronunciation["character"]
            )
            if character not in actual:
                errors.append(
                    f"TTS override {key}: "
                    f"character={character!r} "
                    "not present in source_text"
                )

    print("Pronunciation QA")
    print(
        f"bopomofo_overrides={len(bop.get('items', []))}"
    )
    print(f"tts_override_assets={len(tts.get('items', []))}")
    print(f"font_gaps={len(font_gaps)}")
    print(f"errors={len(errors)}")

    if font_gaps:
        print("\nUnresolved font gaps")
        for item in font_gaps:
            print(
                "  "
                f"p{int(item['poem_id']):03d} "
                f"{item['field']} "
                f"line={item['line_no']} "
                f"{item['character']} -> "
                f"{item['expected_reading']} "
                f"({item['note']})"
            )

    if errors:
        print("\nErrors")
        for error in errors:
            print(f"  {error}")

    if errors:
        return 1

    if font_gaps and not args.allow_font_gaps:
        print(
            "\nBLOCKED: unresolved font gaps remain. "
            "Use --allow-font-gaps only for diagnostics."
        )
        return 1

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
