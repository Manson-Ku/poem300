#!/usr/bin/env python3
"""Validate pronunciation QA registries against poems.csv.

This is a data/contract QA step. It does not inspect audio acoustically.
It verifies:
- bopomofo IVS override source positions
- project-font IVS extensions
- TTS pronunciation override source text
- no duplicate override keys
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

try:
    from fontTools.ttLib import TTFont
except ImportError as exc:
    raise SystemExit(
        "fonttools is required. Run: "
        "py -m pip install -r requirements.txt"
    ) from exc


def read_poems(path: Path) -> dict[int, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return {int(row["poem_id"]): row for row in rows}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_uplus(value: str) -> int:
    text = value.strip().upper()
    if not text.startswith("U+"):
        raise ValueError(f"expected U+XXXX value, got {value!r}")
    return int(text[2:], 16)


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


def find_uvs_table(font: TTFont) -> Any | None:
    for table in font["cmap"].tables:
        if table.format == 14:
            table.ensureDecompiled()
            return table
    return None


def validate_font_extensions(
    *,
    font_path: Path,
    spec: dict[str, Any],
) -> tuple[int, list[str]]:
    errors: list[str] = []

    if not font_path.exists():
        return 0, [
            f"project font not found: {font_path.as_posix()}; "
            "run py scripts\\patch_bpmf_font.py"
        ]

    font = TTFont(str(font_path))
    verified = 0

    try:
        uvs = find_uvs_table(font)
        if uvs is None:
            return 0, [
                f"project font has no cmap format 14 IVS table: "
                f"{font_path.as_posix()}"
            ]

        for item in spec.get("extensions", []):
            char = str(item["character"])
            codepoint = parse_uplus(str(item["codepoint"]))
            selector = parse_uplus(str(item["selector"]))
            expected_component = str(item["component_glyph"])

            mappings = {
                uv: glyph_name
                for uv, glyph_name in uvs.uvsDict.get(
                    selector,
                    [],
                )
            }
            glyph_name = mappings.get(codepoint)

            if not glyph_name:
                errors.append(
                    f"font extension missing: {char} "
                    f"{item['selector']} -> "
                    f"{item['added_reading']}"
                )
                continue

            if glyph_name not in font["glyf"]:
                errors.append(
                    f"font extension glyph not found: "
                    f"{glyph_name!r}"
                )
                continue

            glyph = font["glyf"][glyph_name]
            if not glyph.isComposite():
                errors.append(
                    f"font extension glyph is not composite: "
                    f"{glyph_name!r}"
                )
                continue

            components = [
                component.glyphName
                for component in glyph.components
            ]
            if expected_component not in components:
                errors.append(
                    f"font extension wrong pronunciation component: "
                    f"{char} expected={expected_component!r} "
                    f"actual={components}"
                )
                continue

            verified += 1

    finally:
        font.close()

    return verified, errors


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
        "--font-extensions",
        default="data/bpmf_font_extensions.json",
    )
    parser.add_argument(
        "--font-path",
        default="fonts/BpmfHuninn-Poem300-Regular.ttf",
    )
    # Retained so old command lines do not break. It no longer turns
    # unresolved project-font extensions into PASS.
    parser.add_argument(
        "--allow-font-gaps",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    poems = read_poems(Path(args.poems))
    bop = read_json(Path(args.bopomofo_overrides))
    qa = read_json(Path(args.qa))
    tts = read_json(Path(args.tts_overrides))
    font_spec = read_json(Path(args.font_extensions))

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

    required_extensions = qa.get(
        "font_extensions_required",
        [],
    )
    for item in required_extensions:
        error = validate_char_item(
            poems=poems,
            item=item,
            label="font_extension",
        )
        if error:
            errors.append(error)

    extension_count = len(
        font_spec.get("extensions", [])
    )
    verified_extensions, font_errors = (
        validate_font_extensions(
            font_path=Path(args.font_path),
            spec=font_spec,
        )
    )
    errors.extend(font_errors)

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

    legacy_font_gaps = qa.get("font_gaps", [])
    if legacy_font_gaps:
        errors.append(
            "legacy unresolved font_gaps remain in QA data"
        )

    print("Pronunciation QA")
    print(
        f"bopomofo_overrides={len(bop.get('items', []))}"
    )
    print(f"tts_override_assets={len(tts.get('items', []))}")
    print(f"font_extensions_required={extension_count}")
    print(f"font_extensions_verified={verified_extensions}")
    print(f"font_gaps={len(legacy_font_gaps)}")
    print(f"errors={len(errors)}")

    if errors:
        print("\nErrors")
        for error in errors:
            print(f"  {error}")
        print("\nBLOCKED")
        return 1

    print(
        "\nPASS "
        f"font={Path(args.font_path).as_posix()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
