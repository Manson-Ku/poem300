#!/usr/bin/env python3
"""Submit and collect Gemini Batch API image generation jobs.

This is the lower-cost asynchronous production path for poem300 images.
It reuses the exact poem-world + independent-scene prompt compiler from
scripts/generate_images.py, submits only missing assets by default, and
writes collected images back to the same formal asset paths.

Runtime batch files are kept under output/image_batches/ and are ignored
by Git.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from generate_images import (
    DEFAULT_MODEL,
    append_ledger,
    build_prompt,
    copy_to_poc,
    ensure_ledger,
    jsonable,
    output_paths,
    poc_output_path,
    read_csv,
    resolve_style,
    save_webp,
    visual_world,
    write_meta,
)


BATCH_PRICING = {
    "pricing_as_of": "2026-10-08",
    "pricing_basis": "Gemini Developer API Batch paid tier",
    "input_text_per_million": 0.125,
    "output_image_per_million": 15.00,
}

DEFAULT_RUNTIME_ROOT = Path("output/image_batches")
DEFAULT_LEDGER = Path("data/image_usage.csv")
IMAGE_TOKENS_1K = 1120
TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid.uuid4().hex[:8]
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def state_name(batch_job: Any) -> str:
    state = getattr(batch_job, "state", None)
    if state is None:
        return ""
    return str(getattr(state, "name", state))


def load_api_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing")
    return genai.Client(api_key=api_key)


def select_items(args: argparse.Namespace) -> tuple[list[dict[str, Any]], int, int]:
    poems = read_csv(Path(args.poems))
    scenes = read_csv(Path(args.scenes))

    if args.poem_id is not None:
        selected_poems = [
            row for row in poems
            if int(row["poem_id"]) == args.poem_id
        ]
        if not selected_poems:
            raise ValueError(f"poem_id={args.poem_id} not found")
    else:
        selected_poems = [
            row for row in poems
            if int(row["recommended_age"]) == args.age
        ]
        if args.approved_only:
            selected_poems = [
                row for row in selected_poems
                if (row.get("visual_plan_status") or "").strip().lower()
                == "approved"
            ]

    selected_poems.sort(key=lambda row: int(row["poem_id"]))
    if not selected_poems:
        raise ValueError("no poems matched selection")

    selected_ages = {
        int(row["recommended_age"])
        for row in selected_poems
    }
    if len(selected_ages) != 1:
        raise ValueError("selected poems must belong to exactly one age group")
    target_age = next(iter(selected_ages))

    style_keys = [
        item.strip().upper()
        for item in args.styles.split(",")
        if item.strip()
    ]
    if not style_keys:
        raise ValueError("--styles must contain at least one style key")

    styles = [
        resolve_style(
            key,
            Path(args.registry),
            target_age=target_age,
        )
        for key in style_keys
    ]

    requested_scene_ids = {
        item.strip()
        for item in args.scene_ids.split(",")
        if item.strip()
    }
    all_source_scene_ids: set[str] = set()
    prepared: list[dict[str, Any]] = []
    skipped_existing = 0
    selected_scene_count = 0

    for poem in selected_poems:
        raw_plan = (poem.get("visual_plan_json") or "").strip()
        if not raw_plan:
            raise ValueError(
                f"poem_id={poem['poem_id']} has no visual_plan_json"
            )
        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"poem_id={poem['poem_id']} invalid visual_plan_json: {exc}"
            ) from exc

        poem_scenes = [
            row for row in scenes
            if row["poem_id"] == poem["poem_id"]
        ]
        poem_scenes.sort(key=lambda row: int(row["scene_no"]))

        plan_scene_ids = [
            item.get("id") or item.get("scene_id")
            for item in plan.get("scenes", [])
            if isinstance(item, dict)
        ]
        source_scene_ids = [row["scene_id"] for row in poem_scenes]
        if plan_scene_ids != source_scene_ids:
            raise ValueError(
                "visual_plan_json Scene IDs/order do not match "
                f"data/scenes.csv for poem_id={poem['poem_id']}"
            )

        all_source_scene_ids.update(source_scene_ids)
        if requested_scene_ids:
            poem_scenes = [
                row for row in poem_scenes
                if row["scene_id"] in requested_scene_ids
            ]

        selected_scene_count += len(poem_scenes)

        for style in styles:
            for scene in poem_scenes:
                poem_id = int(poem["poem_id"])
                scene_no = int(scene["scene_no"])
                output_image, meta_json = output_paths(
                    poem_id,
                    scene_no,
                    style["style_id"],
                )
                poc_image = poc_output_path(
                    poem_id,
                    scene_no,
                    style["style_key"],
                )
                if output_image.exists() and not args.force:
                    if not poc_image.exists():
                        copy_to_poc(
                            output_image,
                            poem_id=poem_id,
                            scene_no=scene_no,
                            style_key=style["style_key"],
                        )
                    skipped_existing += 1
                    continue

                prompt = build_prompt(poem, scene, plan, style)
                key = f"{scene['scene_id']}_{style['style_key']}"
                prepared.append(
                    {
                        "key": key,
                        "poem_id": poem_id,
                        "title": poem["title"],
                        "scene_id": scene["scene_id"],
                        "scene_no": scene_no,
                        "style_key": style["style_key"],
                        "style_id": style["style_id"],
                        "prompt": prompt,
                        "prompt_sha256": hashlib.sha256(
                            prompt.encode("utf-8")
                        ).hexdigest(),
                        "output_image": output_image.as_posix(),
                        "poc_image": poc_image.as_posix(),
                        "meta_json": meta_json.as_posix(),
                    }
                )

    if requested_s²È="24€€€€€€€€É•ÍÁ½¹Í”¹•Ð ‰É•ÍÁ½¹Í•%ˆ¤(€€€€€€€€€€€€€€€½ÈÉ•ÍÁ½¹Í”¹•Ð ‰É•ÍÁ½¹Í•}¥ˆ¤(€€€€€€€€€€€€€€€½È€ˆˆ(€€€€€€€€€€€€¤((€€€€€€€€€€€µ•Ñ„€ôì(€€€€€€€€€€€€€€€€‰…É¡¥Ñ•ÑÕÉ”ˆè€‰Á½•µ}Ý½É±‘}¥¹‘•Á•¹‘•¹Ñ}Í•¹•}ØÄˆ°(€€€€€€€€€€€€€€€€‰‘•±¥Ù•Éå}µ½‘”ˆè€‰•µ¥¹¥}‰…Ñ¡}…Á¥}ØÄˆ°(€€€€€€€€€€€€€€€€‰‰…Ñ¡}©½‰}¹…µ”ˆè©½‰}‘½l‰©½‰}¹…µ”‰t°(€€€€€€€€€€€€€€€€‰‰…Ñ¡}É•ÅÕ•ÍÑ}­•äˆè­•ä°(€€€€€€€€€€€€€€€€‰Á½•µ}¥ˆè¥Ñ•µl‰Á½•µ}¥‰t°(€€€€€€€€€€€€€€€€‰Ñ¥Ñ±”ˆè¥Ñ•µl‰Ñ¥Ñ±”‰t°(€€€€€€€€€€€€€€€€‰Í•¹•}¥ˆè¥Ñ•µl‰Í•¹•}¥‰t°(€€€€€€€€€€€€€€€€‰Í•¹•}¹¼ˆè¥Ñ•µl‰Í•¹•}¹¼‰t°(€€€€€€€€€€€€€€€€‰ÍÑå±•}­•äˆè¥Ñ•µl‰ÍÑå±•}­•ä‰t°(€€€€€€€€€€€€€€€€‰ÍÑå±•}¥ˆè¥Ñ•µl‰ÍÑå±•}¥‰t°(€€€€€€€€€€€€€€€€‰µ½‘•°ˆè©½‰}‘½l‰µ½‘•°‰t°(€€€€€€€€€€€€€€€€‰É•ÍÁ½¹Í•}¥ˆèÉ•ÍÁ½¹Í•}¥°(€€€€€€€€€€€€€€€€‰ÁÉ•Ù¥½ÕÍ}¥¹Ñ•É…Ñ¥½¹}¥ˆè9½¹”°(€€€€€€€€€€€€€€€€‰ÁÉ½µÁÑ}Í¡„ÈÔØˆè¥Ñ•µl‰ÁÉ½µÁÑ}Í¡„ÈÔØ‰t°(€€€€€€€€€€€€€€€€‰µ¥µ•}ÑåÁ”ˆèµ¥µ•}ÑåÁ”°(€€€€€€€€€€€€€€€€‰½ÕÑÁÕÑ}¥µ…”ˆè½ÕÑÁÕÑ}¥µ…”¹…Í}Á½Í¥à ¤°(€€€€€€€€€€€€€€€€‰Á½}¥µ…”ˆèÁ½}¥µ…”¹…Í}Á½Í¥à ¤°(€€€€€€€€€€€€€€€€‰•¹•É…Ñ•‘}…Ñ}ÕÑŒˆè¹½Ý}ÕÑŒ ¤°(€€€€€€€€€€€ô(€€€€€€€€€€€ÝÉ¥Ñ•}µ•Ñ„¡A…Ñ ¡ÍÑÈ¡¥Ñ•µl‰µ•Ñ…}©Í½¸‰t¤¤°µ•Ñ„¤((€€€€€€€€€€€…ÁÁ•¹‘}±•‘•È (€€€€€€€€€€€€€€€±•‘•È°(€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€‰ÕÍ…•}¥ˆèÍÑÈ¡ÕÕ¥¹ÕÕ¥Ð ¤¤°(€€€€€€€€€€€€€€€€€€€€‰ÉÕ¹}¥ˆè©½‰}‘½l‰ÉÕ¹}¥‰t°(€€€€€€€€€€€€€€€€€€€€‰É•…Ñ•‘}…Ñ}ÕÑŒˆè¹½Ý}ÕÑŒ ¤°(€€€€€€€€€€€€€€€€€€€€‰Í•¹•}¥ˆè¥Ñ•µl‰Í•¹•}¥‰t°(€€€€€€€€€€€€€€€€€€€€‰Í•¹•}¹¼ˆè¥Ñ•µl‰Í•¹•}¹¼‰t°(€€€€€€€€€€€€€€€€€€€€‰Á½•µ}¥ˆè¥Ñ•µl‰Á½•µ}¥‰t°(€€€€€€€€€€€€€€€€€€€€‰Ñ¥Ñ±”ˆè¥Ñ•µl‰Ñ¥Ñ±”‰t°(€€€€€€€€€€€€€€€€€€€€‰µ½‘•°ˆè©½‰}‘½l‰µ½‘•°‰t°(€€€€€€€€€€€€€€€€€€€€‰ÍÑå±•}­•äˆè¥Ñ•µl‰ÍÑå±•}­•ä‰t°(€€€€€€€€€€€€€€€€€€€€‰ÍÑå±•}¥ˆè¥Ñ•µl‰ÍÑå±•}¥‰t°(€€€€€€€€€€€€€€€€€€€€‰ÁÉ½µÁÑ}Í¡„ÈÔØˆè¥Ñ•µl‰ÁÉ½µÁÑ}Í¡„ÈÔØ‰t°(€€€€€€€€€€€€€€€€€€€€‰¥¹Ñ•É…Ñ¥½¹}¥ˆèÉ•ÍÁ½¹Í•}¥°(€€€€€€€€€€€€€€€€€€€€‰ÁÉ•Ù¥½ÕÍ}¥¹Ñ•É…Ñ¥½¹}¥ˆè€ˆˆ°(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰½µÁ±•Ñ•ˆ°(€€€€€€€€€€€€€€€€€€€€‰±…Ñ•¹å}µÌˆè€ˆˆ°(€€€€€€€€€€€€€€€€€€€€‰¥¹ÁÕÑ}Ñ•áÑ}Ñ½­•¹Ìˆè¥¹ÁÕÑ}Ñ•áÑ}Ñ½­•¹Ì°(€€€€€€€€€€€€€€€€€€€€‰¥¹ÁÕÑ}¥µ…•}Ñ½­•¹Ìˆè€À°(€€€€€€€€€€€€€€€€€€€€‰Ñ½Ñ…±}¥¹ÁÕÑ}Ñ½­•¹ÌˆèÑ½Ñ…±}¥¹ÁÕÑ}Ñ½­•¹Ì°(€€€€€€€€€€€€€€€€€€€€‰½ÕÑÁÕÑ}¥µ…•}Ñ½­•¹Ìˆè½ÕÑÁÕÑ}¥µ…•}Ñ½­•¹Ì°(€€€€€€€€€€€€€€€€€€€€‰½ÕÑÁÕÑ}¹½¹}¥µ…•}Ñ½­•¹Ìˆè½ÕÑÁÕÑ}¹½¹}¥µ…•}Ñ½­•¹Ì°(€€€€€€€€€€€€€€€€€€€€‰Ñ½Ñ…±}½ÕÑÁÕÑ}Ñ½­•¹ÌˆèÑ½Ñ…±}½ÕÑÁÕÑ}Ñ½­•¹Ì°(€€€€€€€€€€€€€€€€€€€€‰Ñ½Ñ…±}Ñ½­•¹ÌˆèÑ½Ñ…±}Ñ½­•¹Ì°(€€€€€€€€€€€€€€€€€€€€‰•ÍÑ¥µ…Ñ•‘}¥¹ÁÕÑ}½ÍÑ}ÕÍˆè˜‰í¥¹ÁÕÑ}½ÍÐè¸å™ôˆ°(€€€€€€€€€€€€€€€€€€€€‰•ÍÑ¥µ…Ñ•‘}½ÕÑÁÕÑ}¥µ…•}½ÍÑ}ÕÍˆè˜‰í¥µ…•}½ÍÐè¸å™ôˆ°(€€€€€€€€€€€€€€€€€€€€‰•ÍÑ¥µ…Ñ•‘}½ÕÑÁÕÑ}¹½¹}¥µ…•}½ÍÑ}ÕÍˆè€ˆÀ¸ÀÀÀÀÀÀÀÀÀˆ°(€€€€€€€€€€€€€€€€€€€€‰•ÍÑ¥µ…Ñ•‘}Ñ½Ñ…±}½ÍÑ}ÕÍˆè˜‰íÉ•ÅÕ•ÍÑ}½ÍÐè¸å™ôˆ°(€€€€€€€€€€€€€€€€€€€€‰ÁÉ¥¥¹}…Í}½˜ˆè	Q!}AI%%9l‰ÁÉ¥¥¹}…Í}½˜‰t°(€€€€€€€€€€€€€€€€€€€€‰ÁÉ¥¥¹}‰…Í¥Ìˆè	Q!}AI%%9l‰ÁÉ¥¥¹}‰…Í¥Ì‰t°(€€€€€€€€€€€€€€€€€€€€‰¥µ…•}Ý¥‘Ñ ˆèÝ¥‘Ñ °(€€€€€€€€€€€€€€€€€€€€‰¥µ…•}¡•¥¡Ðˆè¡•¥¡Ð°(€€€€€€€€€€€€€€€€€€€€‰¥µ…•}Í¥é•}‰åÑ•ÌˆèÍ¥é•}‰åÑ•Ì°(€€€€€€€€€€€€€€€€€€€€‰½ÕÑÁÕÑ}¥µ…”ˆè½ÕÑÁÕÑ}¥µ…”¹…Í}Á½Í¥à ¤°(€€€€€€€€€€€€€€€€€€€€‰Á½}¥µ…”ˆèÁ½}¥µ…”¹…Í}Á½Í¥à ¤°(€€€€€€€€€€€€€€€€€€€€‰µ•Ñ…}©Í½¸ˆè¥Ñ•µl‰µ•Ñ…}©Í½¸‰t°(€€€€€€€€€€€€€€€€€€€€‰ÕÍ…•}É…Ý}©Í½¸ˆè©Í½¸¹‘ÕµÁÌ (€€€€€€€€€€€€€€€€€€€€€€€ÕÍ…”°(€€€€€€€€€€€€€€€€€€€€€€€•¹ÍÕÉ•}…Í¥¤õ…±Í”°(€€€€€€€€€€€€€€€€€€€€€€€Í•Á…É…Ñ½ÉÌô ˆ°ˆ°€ˆèˆ¤°(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€‰•ÉÉ½É}µ•ÍÍ…”ˆè€ˆˆ°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€¤((€€€€€€€€€€€½±±•Ñ•¹…‘¡­•ä¤(€€€€€€€€€€€©½‰}‘½l‰½±±•Ñ•‘}­•åÌ‰t€ôÍ½ÉÑ•¡½±±•Ñ•¤(€€€€€€€€€€€©½‰}‘½l‰™…¥±•‘}­•åÌ‰t€ôÍ½ÉÑ•¡™…¥±•‘}­•åÌ¤(€€€€€€€€€€€©½‰}‘½l‰½±±•Ñ•‘}…Ñ}ÕÑŒ‰t€ô¹½Ý}ÕÑŒ ¤(€€€€€€€€€€€ÝÉ¥Ñ•}©Í½¸¡©½‰}Á…Ñ °©½‰}‘½Œ¤(€€€€€€€€€€€•¹•É…Ñ•€¬ô€Ä(€€€€€€€€€€€ÁÉ¥¹Ð (€€€€€€€€€€€€€€€˜‰í­•åôè=11QíÝ¥‘Ñ¡õáí¡•¥¡Ñô€ˆ(€€€€€€€€€€€€€€€˜‰¥µ…•}Ñ½­•¹Ìõí½ÕÑÁÕÑ}¥µ…•}Ñ½­•¹Íô€ˆ(€€€€€€€€€€€€€€€˜‰•ÍÑ}ÕÍõíÉ•ÅÕ•ÍÑ}½ÍÐè¸å™ô€ˆ(€€€€€€€€€€€€€€€˜ˆ´øí½ÕÑÁÕÑ}¥µ…”¹…Í}Á½Í¥à ¥ôˆ(€€€€€€€€€€€€¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€…ÁÁ•¹‘}™…¥±•‘}±•‘•È (€€€€€€€€€€€€€€€±•‘•È°(€€€€€€€€€€€€€€€©½‰}‘½Œ°(€€€€€€€€€€€€€€€¥Ñ•´°(€€€€€€€€€€€€€€€ì‰µ•ÍÍ…”ˆè˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áô‰ô°(€€€€€€€€€€€€¤(€€€€€€€€€€€™…¥±•‘}­•åÌ¹…‘¡­•ä¤(€€€€€€€€€€€©½‰}‘½l‰™…¥±•‘}­•åÌ‰t€ôÍ½ÉÑ•¡™…¥±•‘}­•åÌ¤(€€€€€€€€€€€ÝÉ¥Ñ•}©Í½¸¡©½‰}Á…Ñ °©½‰}‘½Œ¤(€€€€€€€€€€€ÁÉ¥¹Ð (€€€€€€€€€€€€€€€˜‰í­•åôè%1íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áôˆ°(€€€€€€€€€€€€€€€™¥±”õÍåÌ¹ÍÑ‘•ÉÈ°(€€€€€€€€€€€€¤(€€€€€€€€€€€™…¥±•€¬ô€Ä((€€€µ¥ÍÍ¥¹}É•ÍÕ±Ñ}­•åÌ€ôÍ•Ð¡µ…¹¥™•ÍÑ}‰å}­•ä¤€´Í••¹}É•ÍÕ±Ñ}­•åÌ(€€€¥˜µ¥ÍÍ¥¹}É•ÍÕ±Ñ}­•åÌè(€€€€€€€ÁÉ¥¹Ð (€€€€€€€€€€€€‰II=Hµ¥ÍÍ¥¹œÉ•ÍÕ±Ð­•åÌè€ˆ(€€€€€€€€€€€€¬€ˆ°ˆ¹©½¥¸¡Í½ÉÑ•¡µ¥ÍÍ¥¹}É•ÍÕ±Ñ}­•åÌ¤¤°(€€€€€€€€€€€™¥±”õÍåÌ¹ÍÑ‘•ÉÈ°(€€€€€€€€¤(€€€€€€€™…¥±•€¬ô±•¸¡µ¥ÍÍ¥¹}É•ÍÕ±Ñ}­•åÌ¤((€€€©½‰}‘½l‰½±±•Ñ•‘}­•åÌ‰t€ôÍ½ÉÑ•¡½±±•Ñ•¤(€€€©½‰}‘½l‰™…¥±•‘}­•åÌ‰t€ôÍ½ÉÑ•¡™…¥±•‘}­•åÌ¤(€€€©½‰}‘½l‰É•ÍÕ±Ñ}™¥±•}¹…µ”‰t€ôÍÑÈ¡É•ÍÕ±Ñ}™¥±•}¹…µ”¤(€€€©½‰}‘½l‰É•ÍÕ±Ñ}©Í½¹°‰t€ôÉ•ÍÕ±Ñ}Á…Ñ ¹…Í}Á½Í¥à ¤(€€€©½‰}‘½l‰±…ÍÑ}½±±•Ñ}…Ñ}ÕÑŒ‰t€ô¹½Ý}ÕÑŒ ¤(€€€ÝÉ¥Ñ•}©Í½¸¡©½‰}Á…Ñ °©½‰}‘½Œ¤((€€€ÁÉ¥¹Ð ‰q¹MÕµµ…Éäˆ¤(€€€ÁÉ¥¹Ð¡˜‰‰…Ñ¡}É•ÅÕ•ÍÑÌõí©½‰}‘½lÉ•ÅÕ•ÍÑ}½Õ¹Ðuôˆ¤(€€€ÁÉ¥¹Ð¡˜‰•¹•É…Ñ•‘}¹½Üõí•¹•É…Ñ•‘ôˆ¤(€€€ÁÉ¥¹Ð¡˜‰…±É•…‘å}ÁÉ½•ÍÍ•õí…±É•…‘å}ÁÉ½•ÍÍ•‘ôˆ¤(€€€ÁÉ¥¹Ð¡˜‰½±±•Ñ•‘}Ñ½Ñ…°õí±•¸¡½±±•Ñ•¥ôˆ¤(€€€ÁÉ¥¹Ð¡˜‰™…¥±•‘}Ñ½Ñ…°õí±•¸¡™…¥±•‘}­•åÌ¥ôˆ¤(€€€ÁÉ¥¹Ð¡˜‰•ÍÑ¥µ…Ñ•‘}½ÍÑ}ÕÍ‘}¹½ÜõíÑ½Ñ…±}½ÍÐè¸å™ôˆ¤(€€€ÁÉ¥¹Ð¡˜‰±•‘•Èõí±•‘•È¹…Í}Á½Í¥à ¥ôˆ¤(€€€É•ÑÕÉ¸€Ä¥˜™…¥±•½È™…¥±•‘}­•åÌ•±Í”€À(()‘•˜…‘‘}Í•±•Ñ¥½¹}…ÉÌ¡Á…ÉÍ•Èè…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•È¤€´ø9½¹”è(€€€Í•±•Ñ½È€ôÁ…ÉÍ•È¹…‘‘}µÕÑÕ…±±å}•á±ÕÍ¥Ù•}É½ÕÀ¡É•ÅÕ¥É•õQÉÕ”¤(€€€Í•±•Ñ½È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁ½•´µ¥ˆ°ÑåÁ”õ¥¹Ð¤(€€€Í•±•Ñ½È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ…”ˆ°ÑåÁ”õ¥¹Ð°¡½¥•Ìô Ø°€Ü°€à°€ä¤¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ…ÁÁÉ½Ù•µ½¹±äˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÍÑå±•Ìˆ°‘•™…Õ±Ðô‰ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÍ•¹”µ¥‘Ìˆ°‘•™…Õ±Ðôˆˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁ½•µÌˆ°‘•™…Õ±Ðô‰‘…Ñ„½Á½•µÌ¹ÍØˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÍ•¹•Ìˆ°‘•™…Õ±Ðô‰‘…Ñ„½Í•¹•Ì¹ÍØˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð (€€€€€€€€ˆ´µÉ•¥ÍÑÉäˆ°(€€€€€€€‘•™…Õ±Ðô‰½¹™¥œ½¥µ…•}ÍÑå±•Í}ÁÉ½‘ÕÑ¥½¹}ØÄ¹©Í½¸ˆ°(€€€€¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µµ½‘•°ˆ°‘•™…Õ±ÐõU1Q}5=0¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ™½É”ˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‘ÉäµÉÕ¸ˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€±½…‘}‘½Ñ•¹Ø ¤(€€€Á…ÉÍ•È€ô…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•È (€€€€€€€‘•ÍÉ¥ÁÑ¥½¸ô‰•µ¥¹¤	…Ñ A$¥µ…”ÁÉ½‘ÕÑ¥½¸™½ÈÁ½•´ÌÀÀ¸ˆ(€€€€¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð (€€€€€€€€ˆ´µÉÕ¹Ñ¥µ”µÉ½½Ðˆ°(€€€€€€€‘•™…Õ±ÐõU1Q}IU9Q%5}I==P¹…Í}Á½Í¥à ¤°(€€€€¤(€€€ÍÕˆ€ôÁ…ÉÍ•È¹…‘‘}ÍÕ‰Á…ÉÍ•ÉÌ¡‘•ÍÐô‰½µµ…¹ˆ°É•ÅÕ¥É•õQÉÕ”¤((€€€ÍÕ‰µ¥Ñ}Á…ÉÍ•È€ôÍÕˆ¹…‘‘}Á…ÉÍ•È (€€€€€€€€‰ÍÕ‰µ¥Ðˆ°(€€€€€€€¡•±Àô‰	Õ¥±„)M=90™¥±”°ÕÁ±½…¥Ð°…¹É•…Ñ”½¹”	…Ñ A$©½ˆ¸ˆ°(€€€€¤(€€€…‘‘}Í•±•Ñ¥½¹}…ÉÌ¡ÍÕ‰µ¥Ñ}Á…ÉÍ•È¤((€€€ÍÑ…ÑÕÍ}Á…ÉÍ•È€ôÍÕˆ¹…‘‘}Á…ÉÍ•È (€€€€€€€€‰ÍÑ…ÑÕÌˆ°(€€€€€€€¡•±Àô‰¡•¬„ÍÕ‰µ¥ÑÑ•	…Ñ A$©½ˆ¸ˆ°(€€€€¤(€€€ÍÑ…ÑÕÍ}Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ©½ˆˆ¤((€€€½±±•Ñ}Á…ÉÍ•È€ôÍÕˆ¹…‘‘}Á…ÉÍ•È (€€€€€€€€‰½±±•Ðˆ°(€€€€€€€¡•±Àô‰½Ý¹±½…„½µÁ±•Ñ•‰…Ñ …¹ÝÉ¥Ñ”¥µ…•ÌÑ¼…ÍÍ•ÐÁ…Ñ¡Ì¸ˆ°(€€€€¤(€€€½±±•Ñ}Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ©½ˆˆ¤(€€€½±±•Ñ}Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð (€€€€€€€€ˆ´µ±•‘•Èˆ°(€€€€€€€‘•™…Õ±ÐõU1Q}1H¹…Í}Á½Í¥à ¤°(€€€€¤((€€€…ÉÌ€ôÁ…ÉÍ•È¹Á…ÉÍ•}…ÉÌ ¤(€€€ÑÉäè(€€€€€€€¥˜…ÉÌ¹½µµ…¹€ôô€‰ÍÕ‰µ¥Ðˆè(€€€€€€€€€€€É•ÑÕÉ¸ÍÕ‰µ¥Ð¡…ÉÌ¤(€€€€€€€¥˜…ÉÌ¹½µµ…¹€ôô€‰ÍÑ…ÑÕÌˆè(€€€€€€€€€€€É•ÑÕÉ¸ÍÑ…ÑÕÌ¡…ÉÌ¤(€€€€€€€¥˜…ÉÌ¹½µµ…¹€ôô€‰½±±•Ðˆè(€€€€€€€€€€€É•ÑÕÉ¸½±±•Ð¡…ÉÌ¤(€€€•á•ÁÐ€¡Y…±Õ•ÉÉ½È°¥±•9½Ñ½Õ¹‘ÉÉ½È°IÕ¹Ñ¥µ•ÉÉ½È¤…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰II=Hèí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€É•ÑÕÉ¸€È(€€€É•ÑÕÉ¸€È(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤(