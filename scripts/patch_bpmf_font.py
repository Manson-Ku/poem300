#!/usr/bin/env python3
"""Patch a local Bpmf Huninn font with poem300-only IVS readings.

The source and derived TTF binaries stay local and are ignored by Git.
This script clones the existing annotated Han glyph, swaps only its
bopomofo component, then maps Han + IVS selector to the new glyph.

Requires:
    fonttools>=4.0
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

try:
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
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

    table = CmapSubtable.newSubtable(14)
    table.platformID = 0
    table.platEncID = 5
    table.language = 0xFF
    table.cmap = {}
    table.uvsDict = {}
    table.data = b""
    font["cmap"].tables.append(table)
    return table


def set_derivative_names(font: TTFont, spec: dict[str, Any]) -> None:
    if "name" not in font:
        return

    output = spec["output_font"]
    family = str(output["family_name"])
    full = str(output["full_name"])
    postscript = str(output["postscript_name"])

    name_table = font["name"]

    # Windows Unicode English
    for name_id, value in (
        (1, family),
        (2, "Regular"),
        (4, full),
        (6, postscript),
        (16, family),
        (17, "Regular"),
    ):
        name_table.setName(value, name_id, 3, 1, 0x0409)

    # Macintosh Roman English, where representable.
    for name_id, value in (
        (1, family),
        (2, "Regular"),
        (4, full),
        (6, postscript),
    ):
        name_table.setName(value, name_id, 1, 0, 0)


def add_extension(
    font: TTFont,
    uvs_table: Any,
    item: dict[str, Any],
) -> dict[str, Any]:
    char = str(item["character"])
    codepoint = parse_uplus(str(item["codepoint"]))
    selector = parse_uplus(str(item["selector"]))
    component_glyph = str(item["component_glyph"])
    suffix = str(item["glyph_suffix"])

    if ord(char) != codepoint:
        raise ValueError(
            f"{char}: codepoint mismatch; "
            f"spec={item['codepoint']} actual=U+{ord(char):04X}"
        )

    best_cmap = font.getBestCmap() or {}
    source_name = best_cmap.get(codepoint)
    if not source_name:
        raise ValueError(
            f"{char}: base glyph not found in Unicode cmap"
        )

    if "glyf" not in font:
        raise ValueError(
            "Only TrueType glyf-based Bpmf Huninn fonts are supported"
        )

    glyf = font["glyf"]
    if component_glyph not in glyf:
        raise ValueError(
            f"{char}: pronunciation component {component_glyph!r} "
            "not found in the source font"
        )

    source = glyf[source_name]
    if not source.isComposite():
        raise ValueError(
            f"{char}: annotated source glyph {source_name!r} "
            "is not composite"
        )

    bopomofo_components = [
        component
        for component in source.components
        if component.glyphName.startswith("z_")
    ]
    if len(bopomofo_components) != 1:
        names = [
            component.glyphName
            for component in source.components
        ]
        raise ValueError(
            f"{char}: expected exactly one z_* component in "
            f"{source_name!r}; got {names}"
        )

    new_name = (
        f"{source_name}.poem300.{suffix}"
    )

    # Re-running against an already-patched input should be deterministic.
    if new_name not in glyf:
        new_glyph = copy.deepcopy(source)
        replaced = 0
        for component in new_glyph.components:
            if component.glyphName.startswith("z_"):
                component.glyphName = component_glyph
                replaced += 1
        if replaced != 1:
            raise AssertionError(
                f"{char}: internal component replacement failed"
            )

        glyf[new_name] = new_glyph

        if "hmtx" in font:
            font["hmtx"].metrics[new_name] = (
                font["hmtx"].metrics[source_name]
            )
        if "vmtx" in font and source_name in font["vmtx"].metrics:
            font["vmtx"].metrics[new_name] = (
                font["vmtx"].metrics[source_name]
            )

    entries = list(uvs_table.uvsDict.get(selector, []))
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

    # FontTools keeps original raw format-14 bytes after decompile.
    # Clear them so the edited uvsDict is actually recompiled.
    uvs_table.data = b""

    return {
        "character": char,
        "codepoint": item["codepoint"],
        "selector": item["selector"],
        "reading": item["added_reading"],
        "source_glyph": source_name,
        "component_glyph": component_glyph,
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
            expected_component = str(item["component_glyph"])

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

            glyph = font["glyf"][glyph_name]
            components = [
                component.glyphName
                for component in glyph.components
            ]
            if expected_component not in components:
                raise ValueError(
                    f"{char}: output glyph {glyph_name!r} does not "
                    f"reference {expected_component!r}"
                )

            results.append(
                {
                    "character": char,
                    "selector": item["selector"],
                    "reading": item["added_reading"],
                    "glyph": glyph_name,
                    "component": expected_component,
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
                f"component={item['component_glyph']}"
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

        output_path.parent.mkdir(parents=True, exist_ok=True)
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
            f"({item['component_glyph']})"
        )

    print("\nVerified")
    for item in verified:
        print(
            "  "
            f"{item['character']} "
            f"{item['selector']} -> {item['reading']} "
            f"glyph={item['glyph']}"
        )

    print(f"\nPASS output={output_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
