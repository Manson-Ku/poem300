#!/usr/bin/env python3
"""Build the local poem300 Bpmf Huninn derivative font.

Two related problems are solved here:

1. Upstream Bpmf Huninn lacks a few literary readings needed by poem300.
   Those readings are added as normal IVS variants by copying the phonetic
   layout from an existing prototype character with the same reading.

2. Canonical poems contain a small number of historical/variant Han
   codepoints not present in the selected upstream base font. The local
   derivative maps those codepoints to reviewed annotated compatibility
   glyphs without changing canonical poem text.

3. Pillow/FreeType rasterization does not reliably consume the IVS
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
    from fontTools.ttLib.tables._g_l_y_f import (
        Glyph,
        GlyphComponent,
    )
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
            "glyph_source_character": str(
                item.get("glyph_source_character", "")
            ),
            "glyph_source_selector": str(
                item.get("glyph_source_selector", "")
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


def base_glyph_name_for_codepoint(
    font: TTFont,
    codepoint: int,
) -> str:
    annotated_name = annotated_glyph_for_codepoint(
        font,
        codepoint,
    )
    _phonetic, bases = split_components(
        font,
        annotated_name,
    )
    if len(bases) != 1:
        raise ValueError(
            f"U+{codepoint:04X}: expected one base Han component, "
            f"got {len(bases)}"
        )
    return str(bases[0].glyphName)


def make_component(
    *,
    glyph_name: str,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    x_units: int = 0,
    y_units: int = 0,
) -> Any:
    """Create a positioned/scaled component for synthesized Han glyphs.

    The older Age 7/8 specs only needed left/right composition. Age 9
    introduces rare characters with top/bottom and mixed layouts, so the
    synthesis contract now supports an explicit component list while keeping
    the legacy left/right fields fully compatible.
    """
    component = GlyphComponent()
    component.glyphName = glyph_name
    component.x = int(x_units)
    component.y = int(y_units)
    component.flags = 0
    component.transform = [
        [float(scale_x), 0.0],
        [0.0, float(scale_y)],
    ]
    return component


def make_lr_component(
    *,
    glyph_name: str,
    scale_x: float,
    x_units: int,
) -> Any:
    return make_component(
        glyph_name=glyph_name,
        scale_x=scale_x,
        x_units=x_units,
    )


def add_synthesized_glyphs(
    font: TTFont,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    results = []
    glyf = font["glyf"]
    best_cmap = font.getBestCmap() or {}
    units_per_em = int(font["head"].unitsPerEm)

    for item in spec.get("synthesized_glyphs", []):
        char = str(item["character"])
        codepoint = parse_uplus(str(item["codepoint"]))
        prototype_char = str(item["prototype_character"])
        prototype_codepoint = parse_uplus(
            str(item["prototype_codepoint"])
        )

        if ord(char) != codepoint:
            raise ValueError(
                f"target {char}: codepoint mismatch U+{codepoint:04X}"
            )
        if ord(prototype_char) != prototype_codepoint:
            raise ValueError(
                f"prototype {prototype_char}: codepoint mismatch "
                f"U+{prototype_codepoint:04X}"
            )

        existing = best_cmap.get(codepoint)
        if existing:
            results.append(
                {
                    **item,
                    "glyph": existing,
                    "status": "native",
                }
            )
            continue

        component_specs = item.get("components")
        if component_specs:
            normalized_components: list[dict[str, Any]] = []
            for index, raw in enumerate(component_specs):
                component_char = str(raw["character"])
                component_codepoint = parse_uplus(
                    str(
                        raw.get("codepoint")
                        or f"U+{ord(component_char):04X}"
                    )
                )
                if ord(component_char) != component_codepoint:
                    raise ValueError(
                        f"{char}: component {index} "
                        f"{component_char} codepoint mismatch"
                    )
                normalized_components.append(
                    {
                        "character": component_char,
                        "codepoint": component_codepoint,
                        "scale_x": float(raw.get("scale_x", 1.0)),
                        "scale_y": float(raw.get("scale_y", 1.0)),
                        "x_em": float(raw.get("x_em", 0.0)),
                        "y_em": float(raw.get("y_em", 0.0)),
                    }
                )
        else:
            # Backward-compatible Age 7/8 left-right synthesis contract.
            left_char = str(item["left_character"])
            right_char = str(item["right_character"])
            left_codepoint = parse_uplus(str(item["left_codepoint"]))
            right_codepoint = parse_uplus(str(item["right_codepoint"]))
            if ord(left_char) != left_codepoint:
                raise ValueError(
                    f"{char}: left component codepoint mismatch"
                )
            if ord(right_char) != right_codepoint:
                raise ValueError(
                    f"{char}: right component codepoint mismatch"
                )
            layout = item["layout"]
            normalized_components = [
                {
                    "character": left_char,
                    "codepoint": left_codepoint,
                    "scale_x": float(layout["left_scale_x"]),
                    "scale_y": 1.0,
                    "x_em": 0.0,
                    "y_em": 0.0,
                },
                {
                    "character": right_char,
                    "codepoint": right_codepoint,
                    "scale_x": float(layout["right_scale_x"]),
                    "scale_y": 1.0,
                    "x_em": float(layout["right_x_em"]),
                    "y_em": 0.0,
                },
            ]

        prototype_name = annotated_glyph_for_codepoint(
            font,
            prototype_codepoint,
        )
        prototype_phonetic, prototype_base = split_components(
            font,
            prototype_name,
        )
        if len(prototype_base) != 1:
            raise ValueError(
                f"{prototype_char}: expected one base component, "
                f"got {len(prototype_base)}"
            )

        base_components = []
        for component_spec in normalized_components:
            base_name = base_glyph_name_for_codepoint(
                font,
                int(component_spec["codepoint"]),
            )
            base_components.append(
                make_component(
                    glyph_name=base_name,
                    scale_x=float(component_spec["scale_x"]),
                    scale_y=float(component_spec["scale_y"]),
                    x_units=int(
                        round(
                            units_per_em
                            * float(component_spec["x_em"])
                        )
                    ),
                    y_units=int(
                        round(
                            units_per_em
                            * float(component_spec["y_em"])
                        )
                    ),
                )
            )

        base_name = f"poem300.base.{codepoint:04X}"
        annotated_name = f"poem300.annotated.{codepoint:04X}"

        if base_name not in glyf:
            base_glyph = Glyph()
            base_glyph.numberOfContours = -1
            base_glyph.components = base_components
            glyf[base_name] = base_glyph
            ensure_glyph_order(font, base_name)

            if "hmtx" in font:
                font["hmtx"].metrics[base_name] = (
                    font["hmtx"].metrics[prototype_name]
                )
            if (
                "vmtx" in font
                and prototype_name in font["vmtx"].metrics
            ):
                font["vmtx"].metrics[base_name] = (
                    font["vmtx"].metrics[prototype_name]
                )

        if annotated_name not in glyf:
            annotated_glyph = copy.deepcopy(
                glyf[prototype_name]
            )
            target_base = copy.deepcopy(prototype_base[0])
            target_base.glyphName = base_name
            annotated_glyph.components = (
                prototype_phonetic + [target_base]
            )
            glyf[annotated_name] = annotated_glyph
            ensure_glyph_order(font, annotated_name)

            if "hmtx" in font:
                font["hmtx"].metrics[annotated_name] = (
                    font["hmtx"].metrics[prototype_name]
                )
            if (
                "vmtx" in font
                and prototype_name in font["vmtx"].metrics
            ):
                font["vmtx"].metrics[annotated_name] = (
                    font["vmtx"].metrics[prototype_name]
                )

        mapped = add_unicode_alias(
            font,
            codepoint=codepoint,
            glyph_name=annotated_name,
        )
        best_cmap[codepoint] = annotated_name

        results.append(
            {
                **item,
                "glyph": annotated_name,
                "base_glyph": base_name,
                "component_count": len(base_components),
                "mapped_cmap_tables": mapped,
                "status": "synthesized",
            }
        )

    return results


def add_compatibility_aliases(
    font: TTFont,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    results = []
    best_cmap = font.getBestCmap() or {}

    for item in spec.get("compatibility_aliases", []):
        char = str(item["character"])
        source_char = str(item["source_character"])
        codepoint = parse_uplus(str(item["codepoint"]))
        source_codepoint = parse_uplus(
            str(item["source_codepoint"])
        )

        if ord(char) != codepoint:
            raise ValueError(
                f"{char}: compatibility codepoint mismatch"
            )
        if ord(source_char) != source_codepoint:
            raise ValueError(
                f"{source_char}: source codepoint mismatch"
            )

        existing = best_cmap.get(codepoint)
        if existing:
            results.append(
                {
                    **item,
                    "glyph": existing,
                    "status": "native",
                }
            )
            continue

        source_glyph = best_cmap.get(source_codepoint)
        if not source_glyph:
            raise ValueError(
                f"{source_char} {item['source_codepoint']}: "
                "source compatibility glyph missing"
            )

        mapped = add_unicode_alias(
            font,
            codepoint=codepoint,
            glyph_name=source_glyph,
        )
        best_cmap[codepoint] = source_glyph
        results.append(
            {
                **item,
                "glyph": source_glyph,
                "mapped_cmap_tables": mapped,
                "status": "compatibility_alias",
            }
        )

    return results


def add_render_aliases(
    font: TTFont,
    uvs_table: Any,
    overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    results = []

    for item in unique_render_aliases(overrides):
        char = item["character"]
        source_char = (
            item.get("glyph_source_character")
            or char
        )
        source_selector_text = (
            item.get("glyph_source_selector")
            or item["selector"]
        )
        selector = parse_uplus(source_selector_text)
        pua = parse_uplus(item["render_codepoint"])

        glyph_name = uvs_glyph(
            uvs_table,
            codepoint=ord(source_char),
            selector=selector,
        )
        if not glyph_name:
            raise ValueError(
                f"{char}: no IVS glyph exists for raster source "
                f"{source_char} {source_selector_text}"
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

        best_cmap = font.getBestCmap() or {}
        synthesized_results = []

        for item in spec.get("synthesized_glyphs", []):
            target = parse_uplus(str(item["codepoint"]))
            glyph_name = best_cmap.get(target)
            if not glyph_name:
                raise ValueError(
                    f"{item['character']}: synthesized glyph missing "
                    "from output font"
                )
            glyph = font["glyf"][glyph_name]
            if not glyph.isComposite():
                raise ValueError(
                    f"{item['character']}: synthesized annotated "
                    "glyph is not composite"
                )
            actual_phonetic, _ = split_components(
                font,
                glyph_name,
            )

            prototype_name = annotated_glyph_for_codepoint(
                font,
                parse_uplus(
                    str(item["prototype_codepoint"])
                ),
            )
            expected_phonetic, _ = split_components(
                font,
                prototype_name,
            )
            actual_names = [
                component.glyphName
                for component in actual_phonetic
            ]
            expected_names = [
                component.glyphName
                for component in expected_phonetic
            ]
            if actual_names != expected_names:
                raise ValueError(
                    f"{item['character']}: synthesized pronunciation "
                    f"layout differs from prototype "
                    f"{item['prototype_character']}"
                )

            synthesized_results.append(
                {
                    **item,
                    "glyph": glyph_name,
                }
            )

        compatibility_results = []

        for item in spec.get("compatibility_aliases", []):
            target = parse_uplus(str(item["codepoint"]))
            actual_glyph = best_cmap.get(target)
            if not actual_glyph:
                raise ValueError(
                    f"{item['character']}: compatibility glyph missing "
                    "from output font"
                )
            compatibility_results.append(
                {
                    **item,
                    "glyph": actual_glyph,
                }
            )

        alias_results = []

        for item in unique_render_aliases(overrides):
            char = item["character"]
            source_char = (
                item.get("glyph_source_character")
                or char
            )
            source_selector_text = (
                item.get("glyph_source_selector")
                or item["selector"]
            )
            selector = parse_uplus(source_selector_text)
            pua = parse_uplus(item["render_codepoint"])
            expected_glyph = uvs_glyph(
                uvs_table,
                codepoint=ord(source_char),
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
            "synthesized_glyphs": synthesized_results,
            "compatibility_aliases": compatibility_results,
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
    print(
        "synthesized_glyphs="
        + str(len(spec.get("synthesized_glyphs", [])))
    )
    print(
        "compatibility_aliases="
        + str(len(spec.get("compatibility_aliases", [])))
    )
    print(f"extensions={len(spec.get('extensions', []))}")
    print(f"render_aliases={len(aliases)}")

    if args.dry_run:
        print("\nSynthesized glyphs")
        for item in spec.get("synthesized_glyphs", []):
            if item.get("components"):
                formula = " + ".join(
                    str(component["character"])
                    for component in item["components"]
                )
            else:
                formula = (
                    f"{item['left_character']} + "
                    f"{item['right_character']}"
                )
            print(
                "  "
                f"{item['character']} = {formula} "
                f"reading={item['reading']} "
                f"prototype={item['prototype_character']}"
            )

        print("\nCompatibility aliases")
        for item in spec.get("compatibility_aliases", []):
            print(
                "  "
                f"{item['character']} -> "
                f"{item['source_character']} "
                f"reading={item['reading']}"
            )

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

        patched_synthesized = add_synthesized_glyphs(
            font,
            spec,
        )
        patched_compatibility = add_compatibility_aliases(
            font,
            spec,
        )
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

    print("\nPatched synthesized glyphs")
    for item in patched_synthesized:
        if item.get("components"):
            formula = " + ".join(
                str(component["character"])
                for component in item["components"]
            )
        else:
            formula = (
                f"{item['left_character']} + "
                f"{item['right_character']}"
            )
        print(
            "  "
            f"{item['character']} = {formula} "
            f"reading={item['reading']} "
            f"status={item['status']}"
        )

    print("\nPatched compatibility aliases")
    for item in patched_compatibility:
        print(
            "  "
            f"{item['character']} -> "
            f"{item['source_character']} "
            f"reading={item['reading']} "
            f"status={item['status']}"
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
        "  synthesized_glyphs="
        + str(len(verified["synthesized_glyphs"]))
    )
    print(
        "  compatibility_aliases="
        + str(len(verified["compatibility_aliases"]))
    )
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
