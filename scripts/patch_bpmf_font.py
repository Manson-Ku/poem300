#!/usr/bin/env python3
"""Patch a local Bpmf Huninn font with poem300-only IVS readings.

The released Bpmf Huninn font does not necessarily retain the upstream
intermediate z_<reading> composite glyphs. Instead, annotated Han glyphs
may directly reference primitive bopomofo components such as zyzh, zyai,
tone4, plus the base Han glyph.

To stay compatible with the released font, this patcher copies the
phonetic component layout from an existing prototype Han character that
already has the desired reading, then combines it with the target Han
glyph and assigns the requested IVS selector.

The source and derived TTF binaries stay local and are ignored by Git.

Requires:
    fonttools>=4.0
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from fontTools.ttLib import TTFont
except ImportError as exc:
    raise SystemExit(
        "fonttools is required. Run: "
        "py -m pip install -r requirements.txt"
    ) from exc


DEFAULT_INPUT = Path("fonts/BpmfHuninn-Regular.ttf")
DEFAULT_OUTPUT = Path("fonts/BpmfHuninn-Poem300-Regular.ttf")
DEFAULT_SPEC = Path("data/bpmf_font_extensions.json")


def parse_uplus(value: str) -> int:
    text = value.strip().upper()
    if not text.startswith("U+"):
        raise ValueError(f"expected U+XXXX value, got {value!r}")
    return int(text[2:], 16)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_uvs_table(font: TTFont) -> Any:
    for table in font["cmap"].tables:
        if table.format == 14:
            table.ensureDecompiled()
            return table

    raise ValueError(
        "source font does not contain cmap format 14 IVS data"
    )


def set_derivative_names(font: TTFont, spec: dict[str, Any]) -> None:
    if "name" not in font:
        return

    output = spec["output_font"]
    family = str(output["family_name"])
    full = str(output["full_name"])
    postscript = str(output["postscript_name"])

    name_table = font["name"]

    for name_id, value in (
        (1, family),
        (2, "Regular"),
        (4, full),
        (6, postscript),
        (16, family),
        (17, "Regular"),
    ):
        name_table.setName(value, name_id, 3, 1, 0x0409)

    for name_id, value in (
        (1, family),
        (2, "Regular"),
        (4, full),
        (6, postscript),
    ):
        name_table.setName(value, name_id, 1, 0, 0)


def is_phonetic_component(glyph_name: str) -> bool:
    return (
        glyph_name.startswith("zy")
        or glyph_name.startswith("tone")
    )


def describe_components(font: TTFont, glyph_name: str) -> list[str]:
    glyph = font["glyf"][glyph_name]
    if not glyph.isComposite():
        return []
    return [
        component.glyphName
        for component in glyph.components
    ]


def annotated_glyph_for_codepoint(
    font: TTFont,
    codepoint: int,
) -> str:
    best_cmap = font.getBestCmap() or {}
    glyph_name = best_cmap.get(codepoint)
    if not glyph_name:
        raise ValueError(
            f"U+{codepoint:04X}: glyph not found in Unicode cmap"
        )
    if glyph_name not in font["glyf"]:
        raise ValueError(
            f"U+{codepoint:04X}: glyph {glyph_name!r} missing from glyf"
        )
    glyph = font["glyf"][glyph_name]
    if not glyph.isComposite():
        raise ValueError(
            f"U+{codepoint:04X}: annotated glyph {glyph_name!r} "
            "is not composite"
        )
    return glyph_name


def split_components(
    font: TTFont,
    glyph_name: str,
) -> tuple[list[Any], list[Any]]:
    glyph = font["glyf"][glyph_name]
    phonetic = []
    non_phonetic = []

    for component in glyph.components:
        if is_phonetic_component(component.glyphName):
            phonetic.append(copy.deepcopy(component))
        else:
            non_phonetic.append(copy.deepcopy(component))

    if not phonetic:
        raise ValueError(
            f"{glyph_name!r}: no zy*/tone* phonetic components found; "
            f"components={describe_components(font, glyph_name)}"
        )
    if not non_phonetic:
        raise ValueError(
            f"{glyph_name!r}: no base Han component found; "
            f"components={describe_components(font, glyph_name)}"
        )

    return phonetic, non_phonetic


def safe_glyph_suffix(reading: str) -> str:
    # Only used in an internal glyph name. Avoid relying on Unicode there.
    encoded = "_".join(
        f"{ord(ch):04X}"
        for ch in reading
    )
    return encoded.lower()


def add_extension(
    font: TTFont,
    uvs_table: Any,
    item: dict[str, Any],
) -> dict[str, Any]:
    char = str(item["character"])
    codepoint = parse_uplus(str(item["codepoint"]))
    selector = parse_uplus(str(item["selector"]))

    prototype_char = str(item["prototype_character"])
    prototype_codepoint = parse_uplus(
        str(item["prototype_codepoint"])
    )
    added_reading = str(item["added_reading"])

    if ord(char) != codepoint:
        raise ValueError(
            f"{char}: codepoint mismatch; "
            f"spec={item['codepoint']} actual=U+{ord(char):04X}"
        )
    if ord(prototype_char) != prototype_codepoint:
        raise ValueError(
            f"{prototype_char}: prototype codepoint mismatch; "
            f"spec={item['prototype_codepoint']} "
            f"actual=U+{ord(prototype_char):04X}"
        )

    target_name = annotated_glyph_for_codepoint(
        font,
        codepoint,
    )
    prototype_name = annotated_glyph_for_codepoint(
        font,
        prototype_codepoint,
    )

    _target_phonetic, target_base = split_components(
        font,
        target_name,
    )
    prototype_phonetic, _prototype_base = split_components(
        font,
        prototype_name,
    )

    suffix = safe_glyph_suffix(added_reading)
    new_name = f"{target_name}.poem300.{suffix}"

    glyf = font["glyf"]

    if new_name not in glyf:
        new_glyph = copy.deepcopy(glyf[target_name])

        # Keep the target Han component(s) but use the complete phonetic
        # layout from the prototype. This is important because different
        # readings can contain a different number of bopomofo symbols,
        # so copying positions one-for-one from the target is not valid.
        new_glyph.components = (
            prototype_phonetic + target_base
        )
        glyf[new_name] = new_glyph

        if "hmtx" in font:
            font["hmtx"].metrics[new_name] = (
                font["hmtx"].metrics[target_name]
            )
        if (
            "vmtx" in font
            and target_name in font["vmtx"].metrics
        ):
            font["vmtx"].metrics[new_name] = (
                font["vmtx"].metrics[target_name]
            )

    entries = list(
        uvs_table.uvsDict.get(selector, [])
    )
    same_cp = [
        glyph_name
        for uv, glyph_name in entries
        if uv == codepoint
    ]

    if same_cp and same_cp != [new_name]:
        raise ValueError(
            f"{char}: selector {item['selector']} is already mapped "
            f"to {same_cp}; refusing to overwrite"
        )

    if not same_cp:
        entries.append((codepoint, new_name))
        uvs_table.uvsDict[selector] = entries

    # Force FontTools to compile the modified format-14 table rather than
    # reusing the original raw bytes.
    if hasattr(uvs_table, "data"):
        uvs_table.data = b""

    return {
        "character": char,
        "codepoint": item["codepoint"],
        "selector": item["selector"],
        "reading": added_reading,
        "target_glyph": target_name,
        "prototype_character": prototype_char,
        "prototype_glyph": prototype_name,
        "prototype_components": [
            component.glyphName
            for component in prototype_phonetic
        ],
        "new_glyph": new_name,
    }


def verify_output(
    path: Path,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    font = TTFont(str(path))
    try:
        uvs_table = find_uvs_table(font)
        results = []

        for item in spec["extensions"]:
            char = str(item["character"])
            codepoint = parse_uplus(str(item["codepoint"]))
            selector = parse_uplus(str(item["selector"]))

            prototype_codepoint = parse_uplus(
                str(item["prototype_codepoint"])
            )
            prototype_name = annotated_glyph_for_codepoint(
                font,
                prototype_codepoint,
            )
            prototype_phonetic, _ = split_components(
                font,
                prototype_name,
            )
            expected_components = [
                component.glyphName
                for component in prototype_phonetic
            ]

            mappings = {
                uv: glyph_name
                for uv, glyph_name in (
                    uvs_table.uvsDict.get(selector, [])
                )
            }
            glyph_name = mappings.get(codepoint)
            if not glyph_name:
                raise ValueError(
                    f"{char}: output font is missing "
                    f"{item['codepoint']} + {item['selector']}"
                )

            actual_phonetic, _ = split_components(
                font,
                glyph_name,
            )
            actual_components = [
                component.glyphName
                for component in actual_phonetic
            ]

            if actual_components != expected_components:
                raise ValueError(
                    f"{char}: output pronunciation components differ "
                    f"from prototype {item['prototype_character']}: "
                    f"expected={expected_components} "
                    f"actual={actual_components}"
                )

            results.append(
                {
                    "character": char,
                    "selector": item["selector"],
                    "reading": item["added_reading"],
                    "glyph": glyph_name,
                    "prototype_character": (
                        item["prototype_character"]
                    ),
                    "phonetic_components": actual_components,
                }
            )

        return results
    finally:
        font.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a local poem300 derivative of Bpmf Huninn "
            "with literary IVS readings."
        )
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
    )
    parser.add_argument(
        "--spec",
        default=str(DEFAULT_SPEC),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing derived font.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    spec_path = Path(args.spec)

    if not input_path.exists():
        print(
            f"ERROR: source font not found: {input_path}",
            file=sys.stderr,
        )
        return 2
    if not spec_path.exists():
        print(
            f"ERROR: extension spec not found: {spec_path}",
            file=sys.stderr,
        )
        return 2
    if output_path.exists() and not args.force:
        print(
            f"ERROR: output already exists: {output_path}\n"
            "Use --force to rebuild it.",
            file=sys.stderr,
        )
        return 2

    spec = read_json(spec_path)

    print(f"input={input_path.as_posix()}")
    print(f"output={output_path.as_posix()}")
    print(f"extensions={len(spec.get('extensions', []))}")

    if args.dry_run:
        for item in spec.get("extensions", []):
            print(
                "  "
                f"{item['character']} "
                f"{item['selector']} -> "
                f"{item['added_reading']} "
                f"prototype={item['prototype_character']}"
            )
        print("dry_run=true; no font was written.")
        return 0

    font = TTFont(str(input_path))
    try:
        uvs_table = find_uvs_table(font)
        patched = [
            add_extension(font, uvs_table, item)
            for item in spec["extensions"]
        ]

        set_derivative_names(font, spec)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        font.save(str(output_path))
    finally:
        font.close()

    verified = verify_output(output_path, spec)

    print("\nPatched")
    for item in patched:
        print(
            "  "
            f"{item['character']} "
            f"{item['selector']} -> {item['reading']} "
            f"prototype={item['prototype_character']} "
            f"components={item['prototype_components']}"
        )

    print("\nVerified")
    for item in verified:
        print(
            "  "
            f"{item['character']} "
            f"{item['selector']} -> {item['reading']} "
            f"prototype={item['prototype_character']} "
            f"glyph={item['glyph']}"
        )

    print(
        f"\nPASS output={output_path.as_posix()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
