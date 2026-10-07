#!/usr/bin/env python3
"""Build the local poem300 Bpmf Huninn derivative font.

Two related problems are solved here:

1. Upstream Bpmf Huninn lacks a few literary readings needed by poem300.
   Those readings are added as normal IVS variants by copying the phonetic
   layout from an existing prototype character with the same reading.

2. Pillow/FreeType rasterization does not reliably consume the IVS
   selector sequence used by bpmfvs. For deterministic PNG rendering,
   every pronunciation override used by poem300 also receives a stable
   Private Use Area (PUA) alias that points directly at the intended
   annotated glyph.

Canonical poem text is never changed. The PUA aliases exist only inside
the local derived font and render_text_overlays.py.

Source and derived font binaries stay local and are ignored by Git.

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
except ImportError as exc:
    raise SystemExit(
        "fonttools is required. Run: "
        "py -m pip install -r requirements.txt"
    ) from exc


DEFAULT_INPUT = Path("fonts/BpmfHuninn-Regular.ttf")
DEFAULT_OUTPUT = Path("fonts/BpmfHuninn-Poem300-Regular.ttf")
DEFAULT_SPEC = Path("data/bpmf_font_extensions.json")
DEFAULT_OVERRIDES = Path("data/bopomofo_overrides.json")


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
    return "_".join(
        f"{ord(ch):04X}"
        for ch in reading
    ).lower()


def uvs_glyph(
    uvs_table: Any,
    *,
    codepoint: int,
    selector: int,
) -> str | None:
    for uv, glyph_name in uvs_table.uvsDict.get(selector, []):
        if uv == codepoint:
            return glyph_name
    return None


def ensure_glyph_order(font: TTFont, glyph_name: str) -> None:
    order = font.getGlyphOrder()
    if glyph_name not in order:
        font.setGlyphOrder(order + [glyph_name])


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
        new_glyph.components = (
            prototype_phonetic + target_base
        )
        glyf[new_name] = new_glyph
        ensure_glyph_order(font, new_name)

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

    existing = uvs_glyph(
        uvs_table,
        codepoint=codepoint,
        selector=selector,
    )
    if existing and existing != new_name:
        raise ValueError(
            f"{char}: selector {item['selector']} is already mapped "
            f"to {existing!r}; refusing to overwrite"
        )

    if not existing:
        entries = list(
            uvs_table.uvsDict.get(selector, [])
        )
        entries.append((codepoint, new_name))
        uvs_table.uvsDict[selector] = entries

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


def unique_render_aliases(
    overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    aliases: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    pua_owners: dict[int, tuple[str, str]] = {}

    for item in overrides.get("items", []):
        render_codepoint = item.get("render_codepoint")
        if not render_codepoint:
            continue

        char = str(item["character"])
        selector = str(item["selector"]).upper()
        pua = parse_uplus(str(render_codepoint))
        key = (char, selector)

        if not (0xE000 <= pua <= 0xF8FF):
            raise ValueError(
                f"{char} {selector}: render_codepoint "
                f"{render_codepoint} is outside BMP PUA"
            )

        owner = pua_owners.get(pua)
        if owner and owner != key:
            raise ValueError(
                f"{render_codepoint} assigned to both "
                f"{owner} and {key}"
            )
        pua_owners[pua] = key

        existing = aliases.get(key)
        if existing:
            if (
                str(existing["render_codepoint"]).upper()
                != str(render_codepoint).upper()
            ):
                raise ValueError(
                    f"{key}: inconsistent render_codepoint values"
                )
            continue

        aliases[key] = {
            "character": char,
            "selector": selector,
            "render_codepoint": str(render_codepoint).upper(),
            "expected_reading": str(
                item.get("expected_reading", "")
            ),
        }

    return sorted(
        aliases.values(),
        key=lambda item: parse_uplus(
            item["render_codepoint"]
        ),
    )


def add_unicode_alias(
    font: TTFont,
    *,
    codepoint: int,
    glyph_name: str,
) -> int:
    mapped_tables = 0

    for table in font["cmap"].tables:
        if not table.isUnicode():
            continue
        if table.format not in (4, 12):
            continue

        table.ensureDecompiled()
        existing = table.cmap.get(codepoint)
        if existing and existing != glyph_name:
            raise ValueError(
                f"U+{codepoint:04X} already maps to "
                f"{existing!r}, cannot map to {glyph_name!r}"
            )

        table.cmap[codepoint] = glyph_name
        mapped_tables += 1

    if mapped_tables == 0:
        raise ValueError(
            "font has no Unicode cmap format 4/12 table "
            "for PUA aliases"
        )
    return mapped_tables


def add_render_aliases(
    font: TTFont,
    uvs_table: Any,
    overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    results = []

    for item in unique_render_aliases(overrides):
        char = item["character"]
        codepoint = ord(char)
        selector = parse_uplus(item["selector"])
        pua = parse_uplus(item["render_codepoint"])

        glyph_name = uvs_glyph(
            uvs_table,
            codepoint=codepoint,
            selector=selector,
        )
        if not glyph_name:
            raise ValueError(
                f"{char} {item['selector']}: no IVS glyph exists "
                "after applying project extensions"
            )

        mapped_tables = add_unicode_alias(
            font,
            codepoint=pua,
            glyph_name=glyph_name,
        )

        results.append(
            {
                **item,
                "glyph": glyph_name,
                "mapped_cmap_tables": mapped_tables,
            }
        )

    return results


def verify_output(
    path: Path,
    spec: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    font = TTFont(str(path))
    try:
        uvs_table = find_uvs_table(font)
        extension_results = []

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

            glyph_name = uvs_glyph(
                uvs_table,
                codepoint=codepoint,
                selector=selector,
            )
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

            extension_results.append(
                {
                    "character": char,
                    "selector": item["selector"],
                    "reading": item["added_reading"],
                    "glyph": glyph_name,
                    "prototype_character": (
                        item["prototype_character"]
                    ),
                }
            )

        alias_results = []
        best_cmap = font.getBestCmap() or {}

        for item in unique_render_aliases(overrides):
            char = item["character"]
            selector = parse_uplus(item["selector"])
            pua = parse_uplus(item["render_codepoint"])
            expected_glyph = uvs_glyph(
                uvs_table,
                codepoint=ord(char),
                selector=selector,
            )
            actual_glyph = best_cmap.get(pua)

            if not expected_glyph:
                raise ValueError(
                    f"{char} {item['selector']}: IVS glyph missing "
                    "during alias verification"
                )
            if actual_glyph != expected_glyph:
                raise ValueError(
                    f"{item['render_codepoint']}: expected "
                    f"{expected_glyph!r}, got {actual_glyph!r}"
                )

            alias_results.append(
                {
                    **item,
                    "glyph": actual_glyph,
                }
            )

        return {
            "extensions": extension_results,
            "aliases": alias_results,
        }
    finally:
        font.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create the local poem300 Bpmf Huninn derivative "
            "with literary IVS readings and raster-safe PUA aliases."
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
        "--overrides",
        default=str(DEFAULT_OVERRIDES),
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
    overrides_path = Path(args.overrides)

    for path, label in (
        (input_path, "source font"),
        (spec_path, "extension spec"),
        (overrides_path, "bopomofo overrides"),
    ):
        if not path.exists():
            print(
                f"ERROR: {label} not found: {path}",
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
    overrides = read_json(overrides_path)
    aliases = unique_render_aliases(overrides)

    print(f"input={input_path.as_posix()}")
    print(f"output={output_path.as_posix()}")
    print(f"extensions={len(spec.get('extensions', []))}")
    print(f"render_aliases={len(aliases)}")

    if args.dry_run:
        print("\nExtensions")
        for item in spec.get("extensions", []):
            print(
                "  "
                f"{item['character']} "
                f"{item['selector']} -> "
                f"{item['added_reading']} "
                f"prototype={item['prototype_character']}"
            )

        print("\nRaster aliases")
        for item in aliases:
            print(
                "  "
                f"{item['character']} "
                f"{item['selector']} -> "
                f"{item['render_codepoint']} "
                f"reading={item['expected_reading']}"
            )

        print("\ndry_run=true; no font was written.")
        return 0

    font = TTFont(str(input_path))
    try:
        uvs_table = find_uvs_table(font)

        patched_extensions = [
            add_extension(font, uvs_table, item)
            for item in spec["extensions"]
        ]
        patched_aliases = add_render_aliases(
            font,
            uvs_table,
            overrides,
        )

        set_derivative_names(font, spec)
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        font.save(str(output_path))
    finally:
        font.close()

    verified = verify_output(
        output_path,
        spec,
        overrides,
    )

    print("\nPatched extensions")
    for item in patched_extensions:
        print(
            "  "
            f"{item['character']} "
            f"{item['selector']} -> {item['reading']} "
            f"prototype={item['prototype_character']}"
        )

    print("\nPatched raster aliases")
    for item in patched_aliases:
        print(
            "  "
            f"{item['character']} "
            f"{item['selector']} -> "
            f"{item['render_codepoint']} "
            f"glyph={item['glyph']}"
        )

    print("\nVerified")
    print(
        f"  extensions={len(verified['extensions'])}"
    )
    print(
        f"  render_aliases={len(verified['aliases'])}"
    )
    print(
        f"\nPASS output={output_path.as_posix()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
