#!/usr/bin/env python3
"""Render transparent title/content/explanation PNG overlays for poem300.

Rules:
- title + content use the local poem300 Bpmf Huninn derivative.
- pronunciation overrides use raster-safe PUA aliases from the derived font;
  canonical IVS metadata remains in data/bopomofo_overrides.json.
- author / poem_type / explanation use the same font without IVS overrides.
- one physical content line == one Scene == one content PNG.
- blank Scenes remain in the manifest but do not render text PNGs.
- content pages use four fixed slots; line count never shrinks the whole poem.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


DEFAULT_CONFIG = Path("config/text_overlay_1080p_v2.json")
DEFAULT_OVERRIDES = Path("data/bopomofo_overrides.json")
DEFAULT_FONT = Path("fonts/BpmfHuninn-Poem300-Regular.ttf")


def normalize_newlines(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def select_poems(
    poems: list[dict[str, str]],
    *,
    poem_id: int | None,
    age: int | None,
    approved_only: bool,
) -> list[dict[str, str]]:
    selected = poems

    if poem_id is not None:
        selected = [
            row for row in selected
            if int(row["poem_id"]) == poem_id
        ]
    elif age is not None:
        selected = [
            row for row in selected
            if int(row["recommended_age"]) == age
        ]
        if approved_only:
            selected = [
                row for row in selected
                if (row.get("visual_plan_status") or "").strip().lower()
                == "approved"
            ]

    selected.sort(key=lambda row: int(row["poem_id"]))
    return selected


def scene_rows_for_poem(
    scenes: list[dict[str, str]],
    poem_id: int,
) -> list[dict[str, str]]:
    rows = [
        row for row in scenes
        if int(row["poem_id"]) == poem_id
    ]
    rows.sort(key=lambda row: int(row["scene_no"]))
    return rows


def codepoint_char(value: str) -> str:
    text = value.strip().upper()
    if text.startswith("U+"):
        return chr(int(text[2:], 16))
    if text.startswith("0X"):
        return chr(int(text[2:], 16))
    if len(text) == 1:
        return text
    raise ValueError(f"Unsupported codepoint value: {value}")


def selector_char(selector: str) -> str:
    return codepoint_char(selector)


def apply_overrides(
    *,
    text: str,
    poem_id: int,
    field: str,
    line_no: int,
    overrides: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    applicable = [
        item for item in overrides
        if int(item["poem_id"]) == poem_id
        and item["field"] == field
        and int(item["line_no"]) == line_no
    ]

    chars = list(text)
    applied: list[dict[str, Any]] = []

    for item in sorted(
        applicable,
        key=lambda x: int(x["char_index"]),
        reverse=True,
    ):
        index = int(item["char_index"])
        expected = str(item["character"])

        if index < 0 or index >= len(chars):
            raise ValueError(
                f"poem_id={poem_id} field={field} line={line_no}: "
                f"override char_index={index} out of range"
            )
        if chars[index] != expected:
            raise ValueError(
                f"poem_id={poem_id} field={field} line={line_no}: "
                f"override expected {expected!r} at char_index={index}, "
                f"found {chars[index]!r}"
            )

        render_codepoint = item.get("render_codepoint")
        if render_codepoint:
            # Pillow/FreeType does not reliably consume the bpmfvs
            # Han+IVS sequence during rasterization. The local derived
            # font exposes the exact selected annotated glyph through
            # a stable PUA alias, so replace only the render-time codepoint.
            chars[index] = codepoint_char(
                str(render_codepoint)
            )
        else:
            # Backward-compatible fallback for older override data.
            ivs = selector_char(str(item["selector"]))
            chars.insert(index + 1, ivs)

        applied.append(item)

    applied.reverse()
    return "".join(chars), applied


def text_bbox(
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    stroke_width: int,
    multiline_spacing: int = 0,
) -> tuple[int, int, int, int]:
    image = Image.new("L", (8, 8), 0)
    draw = ImageDraw.Draw(image)
    if "\n" in text:
        return draw.multiline_textbbox(
            (0, 0),
            text,
            font=font,
            stroke_width=stroke_width,
            spacing=multiline_spacing,
            align="center",
        )
    return draw.textbbox(
        (0, 0),
        text,
        font=font,
        stroke_width=stroke_width,
    )


def title_line_candidates(
    text: str,
    *,
    max_lines: int,
) -> list[str]:
    """Return natural long-title wrapping candidates.

    Prefer the canonical ASCII-space phrase boundaries used by the source
    dataset. If a title has no such separators, fall back to character
    boundaries. Canonical title text is never changed; newlines exist only in
    the renderer-facing title string.
    """
    if max_lines < 2:
        return []

    phrase_units = [part for part in text.split(" ") if part]
    use_phrase_units = len(phrase_units) >= 2
    units = phrase_units if use_phrase_units else list(text)
    if len(units) < 2:
        return []

    joiner = " " if use_phrase_units else ""
    results: list[str] = []

    def partitions(
        start: int,
        remaining_lines: int,
        parts: list[str],
    ) -> None:
        if remaining_lines == 1:
            tail = joiner.join(units[start:]).strip()
            if tail:
                results.append("\n".join(parts + [tail]))
            return

        max_end = len(units) - (remaining_lines - 1)
        for end in range(start + 1, max_end + 1):
            part = joiner.join(units[start:end]).strip()
            if not part:
                continue
            partitions(
                end,
                remaining_lines - 1,
                parts + [part],
            )

    for line_count in range(2, min(max_lines, len(units)) + 1):
        partitions(0, line_count, [])

    return results


def choose_title_layout(
    text: str,
    *,
    font_path: Path,
    config: dict[str, Any],
    max_width: int,
    max_height: int,
    raster_scale: int,
    stroke_width: int,
) -> tuple[int, str]:
    """Fit a title, using multiline rendering only as an overflow fallback."""
    title_style = config["typography"]["title"]
    try:
        size, _ = choose_font_size(
            [text],
            font_path=font_path,
            candidates=title_style["candidate_font_px"],
            max_width=max_width,
            raster_scale=raster_scale,
            stroke_width=stroke_width,
        )
        return size, text
    except ValueError:
        pass

    fallback = title_style.get("multiline_fallback") or {}
    if not fallback.get("enabled", False):
        raise ValueError(
            f"title overflow and multiline fallback disabled: {text!r}"
        )

    max_lines = int(fallback.get("max_lines", 2))
    line_spacing_px = int(fallback.get("line_spacing_px", 2))
    candidates = [
        int(value)
        for value in fallback.get(
            "candidate_font_px",
            title_style["candidate_font_px"],
        )
    ]
    layouts = title_line_candidates(
        text,
        max_lines=max_lines,
    )
    if not layouts:
        raise ValueError(
            f"title overflow with no valid multiline layout: {text!r}"
        )

    for display_px in candidates:
        font = ImageFont.truetype(
            str(font_path),
            display_px * raster_scale,
        )
        measured: list[
            tuple[int, int, int, str]
        ] = []
        for layout in layouts:
            bbox = text_bbox(
                layout,
                font=font,
                stroke_width=stroke_width * raster_scale,
                multiline_spacing=line_spacing_px * raster_scale,
            )
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            if (
                width <= max_width * raster_scale
                and height <= max_height * raster_scale
            ):
                line_widths = []
                for line in layout.split("\n"):
                    line_bbox = text_bbox(
                        line,
                        font=font,
                        stroke_width=stroke_width * raster_scale,
                    )
                    line_widths.append(
                        line_bbox[2] - line_bbox[0]
                    )
                imbalance = max(line_widths) - min(line_widths)
                measured.append(
                    (
                        len(layout.split("\n")),
                        imbalance,
                        width,
                        layout,
                    )
                )

        if measured:
            measured.sort(
                key=lambda item: (
                    item[0],
                    item[1],
                    item[2],
                )
            )
            return display_px, measured[0][3]

    raise ValueError(
        f"title overflow: cannot fit within "
        f"{max_width}x{max_height}px using <= {max_lines} lines"
    )


def choose_font_size(
    texts: list[str],
    *,
    font_path: Path,
    candidates: list[int],
    max_width: int,
    raster_scale: int,
    stroke_width: int,
) -> tuple[int, dict[str, tuple[int, int, int, int]]]:
    nonblank = [text for text in texts if text.strip()]
    if not nonblank:
        raise ValueError("No nonblank text to measure")

    for display_px in candidates:
        font = ImageFont.truetype(
            str(font_path),
            display_px * raster_scale,
        )
        bboxes = {
            text: text_bbox(
                text,
                font=font,
                stroke_width=stroke_width * raster_scale,
            )
            for text in nonblank
        }
        widest = max(
            bbox[2] - bbox[0]
            for bbox in bboxes.values()
        )
        if widest <= max_width * raster_scale:
            return display_px, bboxes

    minimum = candidates[-1]
    font = ImageFont.truetype(
        str(font_path),
        minimum * raster_scale,
    )
    bboxes = {
        text: text_bbox(
            text,
            font=font,
            stroke_width=stroke_width * raster_scale,
        )
        for text in nonblank
    }
    widest = max(
        bbox[2] - bbox[0]
        for bbox in bboxes.values()
    )
    raise ValueError(
        f"text overflow: width={widest / raster_scale:.1f}px "
        f"> max_width={max_width}px at minimum font={minimum}px"
    )


def parse_hex_color(value: str) -> tuple[int, int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"Unsupported color: {value}")
    return (
        int(raw[0:2], 16),
        int(raw[2:4], 16),
        int(raw[4:6], 16),
        255,
    )


def render_text_png(
    text: str,
    *,
    output_path: Path,
    font_path: Path,
    display_font_px: int,
    raster_scale: int,
    appearance: dict[str, Any],
    padding_px: int,
    force: bool,
) -> dict[str, Any]:
    if output_path.exists() and not force:
        with Image.open(output_path) as existing:
            width, height = existing.size
        return {
            "status": "skipped",
            "raster_width": width,
            "raster_height": height,
            "display_width": width / raster_scale,
            "display_height": height / raster_scale,
        }

    font = ImageFont.truetype(
        str(font_path),
        display_font_px * raster_scale,
    )

    stroke = int(appearance.get("stroke_px", 0)) * raster_scale
    shadow = appearance.get("shadow") or {}
    shadow_dx = int(shadow.get("dx", 0)) * raster_scale
    shadow_dy = int(shadow.get("dy", 0)) * raster_scale
    shadow_blur = int(shadow.get("blur_px", 0)) * raster_scale
    shadow_alpha = float(shadow.get("alpha", 0))
    padding = padding_px * raster_scale

    multiline_spacing = 0
    if "\n" in text:
        multiline_spacing = 2 * raster_scale
    bbox = text_bbox(
        text,
        font=font,
        stroke_width=stroke,
        multiline_spacing=multiline_spacing,
    )
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    extra_x = abs(shadow_dx) + shadow_blur * 2
    extra_y = abs(shadow_dy) + shadow_blur * 2

    width = text_w + padding * 2 + extra_x
    height = text_h + padding * 2 + extra_y

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    x = padding - bbox[0] + shadow_blur
    y = padding - bbox[1] + shadow_blur

    fill = parse_hex_color(appearance["fill"])
    stroke_fill = parse_hex_color(appearance["stroke"])

    if shadow_alpha > 0:
        shadow_layer = Image.new(
            "RGBA",
            image.size,
            (0, 0, 0, 0),
        )
        shadow_draw = ImageDraw.Draw(shadow_layer)
        draw_shadow = (
            shadow_draw.multiline_text
            if "\n" in text
            else shadow_draw.text
        )
        shadow_kwargs: dict[str, Any] = {}
        if "\n" in text:
            shadow_kwargs.update(
                spacing=multiline_spacing,
                align="center",
            )
        draw_shadow(
            (x + shadow_dx, y + shadow_dy),
            text,
            font=font,
            fill=(0, 0, 0, round(255 * shadow_alpha)),
            stroke_width=stroke,
            stroke_fill=(0, 0, 0, round(255 * shadow_alpha)),
            **shadow_kwargs,
        )
        if shadow_blur > 0:
            from PIL import ImageFilter
            shadow_layer = shadow_layer.filter(
                ImageFilter.GaussianBlur(shadow_blur)
            )
        image.alpha_composite(shadow_layer)

    draw = ImageDraw.Draw(image)
    draw_text = draw.multiline_text if "\n" in text else draw.text
    draw_kwargs: dict[str, Any] = {}
    if "\n" in text:
        draw_kwargs.update(
            spacing=multiline_spacing,
            align="center",
        )
    draw_text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke,
        stroke_fill=stroke_fill,
        **draw_kwargs,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")

    return {
        "status": "generated",
        "raster_width": width,
        "raster_height": height,
        "display_width": width / raster_scale,
        "display_height": height / raster_scale,
    }


def assign_pages(
    scenes: list[dict[str, str]],
    *,
    page_capacity: int,
) -> dict[str, dict[str, Any]]:
    page = 1
    slot = 0
    mapping: dict[str, dict[str, Any]] = {}

    for scene in scenes:
        scene_id = scene["scene_id"]
        line = scene["original_line"]

        if not line.strip():
            mapping[scene_id] = {
                "render": False,
                "kind": "blank_separator",
                "page": page,
                "slot": None,
            }
            if slot > 0:
                page += 1
                slot = 0
            continue

        if slot >= page_capacity:
            page += 1
            slot = 0

        slot += 1
        mapping[scene_id] = {
            "render": True,
            "kind": "content",
            "page": page,
            "slot": slot,
        }

    return mapping


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render transparent text overlay PNGs."
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--poem-id", type=int)
    selector.add_argument("--age", type=int, choices=(6, 7, 8, 9))
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument(
        "--font-path",
        default=os.environ.get(
            "BPMF_FONT_PATH",
            str(DEFAULT_FONT),
        ),
    )
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument("--scenes", default="data/scenes.csv")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
    )
    parser.add_argument(
        "--overrides",
        default=str(DEFAULT_OVERRIDES),
    )
    parser.add_argument("--assets-root", default="assets")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    font_path = Path(args.font_path)
    if not font_path.exists():
        print(
            f"ERROR: font not found: {font_path}",
            file=sys.stderr,
        )
        return 2

    config = read_json(Path(args.config))
    override_doc = read_json(Path(args.overrides))
    overrides = override_doc.get("items") or []

    poems = read_csv(Path(args.poems))
    scenes = read_csv(Path(args.scenes))
    selected = select_poems(
        poems,
        poem_id=args.poem_id,
        age=args.age,
        approved_only=args.approved_only,
    )

    if not selected:
        print("ERROR: no poems matched selection", file=sys.stderr)
        return 2

    raster_scale = int(config["raster_scale"])
    padding_px = int(config["output"]["padding_px"])
    assets_root = Path(args.assets_root)

    generated = 0
    skipped = 0
    failed = 0

    print(f"config={args.config}")
    print(f"font={font_path.as_posix()}")
    print(f"poems={len(selected)}")
    print(f"force={args.force} dry_run={args.dry_run}")

    for poem in selected:
        pid = int(poem["poem_id"])
        poem_scenes = scene_rows_for_poem(scenes, pid)

        content_lines = normalize_newlines(poem["content"]).split("\n")
        explanation_lines = normalize_newlines(
            poem["child_explanation_6_8"]
        ).split("\n")

        if len(content_lines) != len(explanation_lines):
            print(
                f"ERROR p{pid:03d}: content/explanation "
                f"line count mismatch",
                file=sys.stderr,
            )
            failed += 1
            continue

        if len(poem_scenes) != len(content_lines):
            print(
                f"ERROR p{pid:03d}: scenes={len(poem_scenes)} "
                f"content_lines={len(content_lines)}",
                file=sys.stderr,
            )
            failed += 1
            continue

        source_mismatch = False
        for index, scene in enumerate(poem_scenes):
            if scene["original_line"] != content_lines[index]:
                source_mismatch = True
                break
            if (
                scene["child_explanation_line"]
                != explanation_lines[index]
            ):
                source_mismatch = True
                break
        if source_mismatch:
            print(
                f"ERROR p{pid:03d}: scenes.csv does not match "
                "poems.csv physical lines",
                file=sys.stderr,
            )
            failed += 1
            continue

        page_map = assign_pages(
            poem_scenes,
            page_capacity=int(
                config["page_logic"]["content_page_capacity"]
            ),
        )

        title_text, title_overrides = apply_overrides(
            text=poem["title"],
            poem_id=pid,
            field="title",
            line_no=0,
            overrides=overrides,
        )

        prepared_content: dict[str, dict[str, Any]] = {}
        for scene in poem_scenes:
            scene_no = int(scene["scene_no"])
            if not scene["original_line"].strip():
                prepared_content[scene["scene_id"]] = {
                    "text": "",
                    "overrides": [],
                }
                continue
            rendered_text, applied = apply_overrides(
                text=scene["original_line"],
                poem_id=pid,
                field="content",
                line_no=scene_no,
                overrides=overrides,
            )
            prepared_content[scene["scene_id"]] = {
                "text": rendered_text,
                "overrides": applied,
            }

        content_pages: dict[int, list[str]] = {}
        for scene in poem_scenes:
            m = page_map[scene["scene_id"]]
            if not m["render"]:
                continue
            content_pages.setdefault(m["page"], []).append(
                prepared_content[scene["scene_id"]]["text"]
            )

        page_font_sizes: dict[int, int] = {}
        content_style = config["typography"]["content"]
        content_appearance = config["appearance"]["title_content"]
        content_zone = config["zones"]["content"]

        for page_no, texts in content_pages.items():
            size, _ = choose_font_size(
                texts,
                font_path=font_path,
                candidates=content_style["candidate_font_px"],
                max_width=int(content_zone["width"]),
                raster_scale=raster_scale,
                stroke_width=int(
                    content_appearance["stroke_px"]
                ),
            )
            page_font_sizes[page_no] = size

        title_size, title_render_text = choose_title_layout(
            title_text,
            font_path=font_path,
            config=config,
            max_width=int(config["zones"]["title"]["width"]),
            max_height=int(config["zones"]["title"]["height"]),
            raster_scale=raster_scale,
            stroke_width=int(
                config["appearance"]["title_content"]["stroke_px"]
            ),
        )

        author_size, _ = choose_font_size(
            [poem["author"]],
            font_path=font_path,
            candidates=config["typography"]["author"][
                "candidate_font_px"
            ],
            max_width=int(config["zones"]["author"]["width"]),
            raster_scale=raster_scale,
            stroke_width=int(
                config["appearance"]["meta_explanation"][
                    "stroke_px"
                ]
            ),
        )

        type_size, _ = choose_font_size(
            [poem["poem_type"]],
            font_path=font_path,
            candidates=config["typography"]["poem_type"][
                "candidate_font_px"
            ],
            max_width=int(config["zones"]["poem_type"]["width"]),
            raster_scale=raster_scale,
            stroke_width=int(
                config["appearance"]["meta_explanation"][
                    "stroke_px"
                ]
            ),
        )

        explanation_sizes: dict[str, int] = {}
        for scene in poem_scenes:
            text = scene["child_explanation_line"]
            if not text.strip():
                continue
            size, _ = choose_font_size(
                [text],
                font_path=font_path,
                candidates=config["typography"]["explanation"][
                    "candidate_font_px"
                ],
                max_width=int(
                    config["zones"]["explanation"]["width"]
                ),
                raster_scale=raster_scale,
                stroke_width=int(
                    config["appearance"]["meta_explanation"][
                        "stroke_px"
                    ]
                ),
            )
            explanation_sizes[scene["scene_id"]] = size

        poem_dir = assets_root / f"p{pid:03d}"
        poem_text_dir = poem_dir / "text"

        jobs: list[dict[str, Any]] = [
            {
                "kind": "title",
                "text": title_render_text,
                "output": poem_text_dir / "title_bpmf.png",
                "font_px": title_size,
                "appearance": config["appearance"]["title_content"],
            },
            {
                "kind": "author",
                "text": poem["author"],
                "output": poem_text_dir / "author.png",
                "font_px": author_size,
                "appearance": config["appearance"][
                    "meta_explanation"
                ],
            },
            {
                "kind": "poem_type",
                "text": poem["poem_type"],
                "output": poem_text_dir / "poem_type.png",
                "font_px": type_size,
                "appearance": config["appearance"][
                    "meta_explanation"
                ],
            },
        ]

        for scene in poem_scenes:
            mapping = page_map[scene["scene_id"]]
            if not mapping["render"]:
                continue

            scene_dir = (
                poem_dir
                / f"s{int(scene['scene_no']):02d}"
                / "text"
            )

            jobs.append(
                {
                    "kind": "content",
                    "scene_id": scene["scene_id"],
                    "text": prepared_content[
                        scene["scene_id"]
                    ]["text"],
                    "output": scene_dir / "content_bpmf.png",
                    "font_px": page_font_sizes[mapping["page"]],
                    "appearance": config["appearance"][
                        "title_content"
                    ],
                }
            )

            jobs.append(
                {
                    "kind": "explanation",
                    "scene_id": scene["scene_id"],
                    "text": scene["child_explanation_line"],
                    "output": scene_dir / "explanation.png",
                    "font_px": explanation_sizes[scene["scene_id"]],
                    "appearance": config["appearance"][
                        "meta_explanation"
                    ],
                }
            )

        print(
            f"\np{pid:03d} {poem['title']} "
            f"scenes={len(poem_scenes)} "
            f"content_pages={len(content_pages)}"
        )

        asset_results: dict[str, dict[str, Any]] = {}

        for job in jobs:
            output = job["output"]
            if args.dry_run:
                status = (
                    "OVERWRITE"
                    if output.exists() and args.force
                    else "SKIP"
                    if output.exists()
                    else "GENERATE"
                )
                print(
                    f"  {job['kind']}: {status} "
                    f"font={job['font_px']} "
                    f"-> {output.as_posix()}"
                )
                continue

            try:
                result = render_text_png(
                    job["text"],
                    output_path=output,
                    font_path=font_path,
                    display_font_px=int(job["font_px"]),
                    raster_scale=raster_scale,
                    appearance=job["appearance"],
                    padding_px=padding_px,
                    force=args.force,
                )
                key = (
                    f"{job.get('scene_id', 'poem')}:{job['kind']}"
                )
                asset_results[key] = result
                if result["status"] == "generated":
                    generated += 1
                else:
                    skipped += 1
                print(
                    f"  {job['kind']}: "
                    f"{result['status'].upper()} "
                    f"font={job['font_px']} "
                    f"-> {output.as_posix()}"
                )
            except Exception as exc:
                failed += 1
                print(
                    f"  {job['kind']}: FAILED {exc}",
                    file=sys.stderr,
                )

        if args.dry_run:
            continue

        manifest_scenes = []
        for scene in poem_scenes:
            mapping = page_map[scene["scene_id"]]
            scene_no = int(scene["scene_no"])
            item = {
                "scene_id": scene["scene_id"],
                "scene_no": scene_no,
                "original_line": scene["original_line"],
                "child_explanation_line": (
                    scene["child_explanation_line"]
                ),
                **mapping,
            }

            if mapping["render"]:
                slot = config["zones"]["content"]["slots"][
                    mapping["slot"] - 1
                ]
                item.update(
                    {
                        "content_font_px": page_font_sizes[
                            mapping["page"]
                        ],
                        "content_center_y": slot["center_y"],
                        "content_image": (
                            f"assets/p{pid:03d}/"
                            f"s{scene_no:02d}/text/"
                            "content_bpmf.png"
                        ),
                        "explanation_font_px": explanation_sizes[
                            scene["scene_id"]
                        ],
                        "explanation_image": (
                            f"assets/p{pid:03d}/"
                            f"s{scene_no:02d}/text/"
                            "explanation.png"
                        ),
                        "ivs_overrides": prepared_content[
                            scene["scene_id"]
                        ]["overrides"],
                    }
                )

            manifest_scenes.append(item)

        manifest = {
            "version": config["version"],
            "poem_id": pid,
            "title": poem["title"],
            "author": poem["author"],
            "poem_type": poem["poem_type"],
            "canvas": config["canvas"],
            "raster_scale": raster_scale,
            "font_path": font_path.as_posix(),
            "title": {
                "text": poem["title"],
                "render_text": title_render_text,
                "font_px": title_size,
                "line_count": title_render_text.count("\n") + 1,
                "image": f"assets/p{pid:03d}/text/title_bpmf.png",
                "ivs_overrides": title_overrides,
            },
            "author": {
                "text": poem["author"],
                "font_px": author_size,
                "image": f"assets/p{pid:03d}/text/author.png",
            },
            "poem_type": {
                "text": poem["poem_type"],
                "font_px": type_size,
                "image": f"assets/p{pid:03d}/text/poem_type.png",
            },
            "content_pages": [
                {
                    "page": page_no,
                    "font_px": page_font_sizes[page_no],
                }
                for page_no in sorted(page_font_sizes)
            ],
            "scenes": manifest_scenes,
        }

        write_json(poem_text_dir / "manifest.json", manifest)

    print("\nSummary")
    print(f"generated={generated}")
    print(f"skipped_existing={skipped}")
    print(f"failed={failed}")

    if args.dry_run:
        print("dry_run=true; no PNG or manifest files were written.")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
