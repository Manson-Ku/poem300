#!/usr/bin/env python3
"""Validate pronunciation QA registries against poems.csv.

This is a data/contract QA step. It does not inspect audio acoustically.
It verifies:
- bopomofo IVS override source positions
- project-font IVS extensions
- raster-safe PUA aliases for every bopomofo override
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

            prototype_codepoint = parse_uplus(
                str(item["prototype_codepoint"])
            )

            best_cmap = font.getBestCmap() or {}
            prototype_name = best_cmap.get(
                prototype_codepoint
            )
            if not prototype_name:
                errors.append(
                    f"prototype glyph missing for "
                    f"{item['prototype_character']}"
                )
                continue

            prototype_glyph = font["glyf"][
                prototype_name
            ]
            if not prototype_glyph.isComposite():
                errors.append(
                    f"prototype glyph is not composite: "
                    f"{prototype_name!r}"
                )
                continue

            expected_phonetic = [
                component.glyphName
                for component in prototype_glyph.components
                if (
                    component.glyphName.startswith("zy")
                    or component.glyphName.startswith("tone")
                )
            ]

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

            actual_phonetic = [
                component.glyphName
                for component in glyph.components
                if (
                    component.glyphName.startswith("zy")
                    or component.glyphName.startswith("tone")
                )
            ]

            if actual_phonetic != expected_phonetic:
                errors.append(
                    f"font extension wrong pronunciation layout: "
                    f"{char} expected={expected_phonetic} "
                    f"actual={actual_phonetic}"
                )
                continue

            verified += 1

    finally:
        font.close()

    return verified, errors


def validate_render_aliases(
    *,
    font_path: Path,
    bop: dict[str, Any],
) -> tuple[int, list[str]]:
    if not font_path.exists():
        # validate_font_extensions reports the missing project font.
        return 0, []

    errors: list[str] = []
    aliases: dict[tuple[str, str], int] = {}
    pua_owners: dict[int, tuple[str, str]] = {}

    for item in bop.get("items", []):
        char = str(item["character"])
        selector_text = str(item["selector"]).upper()
        render_codepoint = item.get("render_codepoint")

        if not render_codepoint:
            errors.append(
                f"bopomofo override missing render_codepoint: "
                f"{char} {selector_text}"
            )
            continue

        try:
            pua = parse_uplus(str(render_codepoint))
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if not (0xE000 <= pua <= 0xF8FF):
            errors.append(
                f"{char} {selector_text}: "
                f"{render_codepoint} is outside BMP PUA"
            )
            continue

        key = (char, selector_text)
        existing = aliases.get(key)
        if existing is not None and existing != pua:
            errors.append(
                f"{key}: inconsistent render aliases "
                f"U+{existing:04X} vs U+{pua:04X}"
            )
            continue
        aliases[key] = pua

        owner = pua_owners.get(pua)
        if owner is not None and owner != key:
            errors.append(
                f"U+{pua:04X} assigned to both "
                f"{owner} and {key}"
            )
            continue
        pua_owners[pua] = key

    font = TTFont(str(font_path))
    verified = 0

    try:
        uvs = find_uvs_table(font)
        if uvs is None:
            return 0, errors + [
                "project font has no cmap format 14 IVS table"
            ]

        best_cmap = font.getBestCmap() or {}

        for (char, selector_text), pua in sorted(
            aliases.items(),
            key=lambda pair: pair[1],
        ):
            selector = parse_uplus(selector_text)
            expected_glyph = None

            for uv, glyph_name in uvs.uvsDict.get(
                selector,
                [],
            ):
                if uv == ord(char):
                    expected_glyph = glyph_name
                    break

            if not expected_glyph:
                errors.append(
                    f"{char} {selector_text}: "
                    "selected IVS glyph missing from project font"
                )
                continue

            actual_glyph = best_cmap.get(pua)
            if actual_glyph != expected_glyph:
                errors.append(
                    f"U+{pua:04X}: expected alias to "
                    f"{expected_glyph!r}, got {actual_glyph!r}"
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

    verified_aliases, alias_errors = validate_render_aliases(
        font_path=Path(args.font_path),
        bop=bop,
    )
    errors.extend(alias_errors)

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

        synthesis_text = item.get("synthesis_text")
        if synthesis_text is not None:
            synthesis_text = str(synthesis_text)
            if not synthesis_text.strip():
                errors.append(
                    f"TTS override {key}: synthesis_text is empty"
                )
            if len(synthesis_text) != len(actual):
                errors.append(
                    f"TTS override {key}: synthesis_text length "
                    f"{len(synthesis_text)} != source_text length "
                    f"{len(actual)}"
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
    print(
        "tts_synthesis_proxy_assets="
        + str(
            sum(
                bool(item.get("synthesis_text"))
                for item in tts.get("items", [])
            )
        )
    )
    print(f"font_extensions_required={extension_count}")
    print(f"font_extensions_verified={verified_extensions}")
    print(
        "render_aliases_verified="
        + str(verified_aliases)
    )
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
