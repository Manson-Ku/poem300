#!/usr/bin/env python3
"""Build audio-driven video timeline manifests without rendering video.

This is the timing/preflight stage between generated assets and the
future FFmpeg composer. It measures actual WAV durations, applies
config/video_timing_v1.json padding, validates required assets, and
writes one timeline.json per poem.

No MP4 is produced here.
"""

from __future__ import annotations

import argparse
import csv
import json
import wave
from pathlib import Path
from typing import Any


DEFAULT_TIMING = Path("config/video_timing_v1.json")
DEFAULT_SESSIONS = Path("config/video_sessions_v1.json")
DEFAULT_STYLE_REGISTRY = Path("config/image_styles_age6.json")


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


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.getnframes()
    if not rate:
        raise ValueError(f"invalid WAV sample rate: {path}")
    return frames / rate


def round_ms(value: float) -> float:
    return round(value + 1e-9, 3)


def select_poems(
    poems: list[dict[str, str]],
    *,
    poem_id: int | None,
    age: int | None,
) -> list[dict[str, str]]:
    if poem_id is not None:
        selected = [
            row for row in poems
            if int(row["poem_id"]) == poem_id
        ]
    else:
        selected = [
            row for row in poems
            if int(row["recommended_age"]) == age
        ]
    selected.sort(key=lambda row: int(row["poem_id"]))
    return selected


def poem_scenes(
    scenes: list[dict[str, str]],
    poem_id: int,
) -> list[dict[str, str]]:
    selected = [
        row for row in scenes
        if int(row["poem_id"]) == poem_id
    ]
    selected.sort(key=lambda row: int(row["scene_no"]))
    return selected


def style_id_for_key(
    registry: dict[str, Any],
    key: str,
) -> str:
    try:
        return str(registry["styles"][key]["style_id"])
    except KeyError as exc:
        raise ValueError(
            f"style key {key!r} not found in registry"
        ) from exc


def path_str(path: Path) -> str:
    return path.as_posix()


class TimelineBuilder:
    def __init__(
        self,
        *,
        poem: dict[str, str],
        scenes: list[dict[str, str]],
        text_manifest: dict[str, Any],
        timing: dict[str, Any],
        style_id: str,
        assets_root: Path,
    ) -> None:
        self.poem = poem
        self.scenes = scenes
        self.text_manifest = text_manifest
        self.timing = timing
        self.style_id = style_id
        self.assets_root = assets_root
        self.pid = int(poem["poem_id"])
        self.poem_dir = assets_root / f"p{self.pid:03d}"
        self.cursor = 0.0
        self.events: list[dict[str, Any]] = []
        self.missing: list[str] = []

        self.scene_text = {
            item["scene_id"]: item
            for item in text_manifest.get("scenes", [])
        }

        self.nonblank_scenes = [
            scene for scene in scenes
            if scene["original_line"].strip()
        ]
        if not self.nonblank_scenes:
            raise ValueError(
                f"p{self.pid:03d}: no nonblank scenes"
            )

    def check(self, path: Path) -> bool:
        if path.exists():
            return True
        value = path_str(path)
        if value not in self.missing:
            self.missing.append(value)
        return False

    def background(self, scene: dict[str, str]) -> Path:
        return (
            self.poem_dir
            / f"s{int(scene['scene_no']):02d}"
            / "image"
            / self.style_id
            / "background.webp"
        )

    def content_png(self, scene: dict[str, str]) -> Path:
        return (
            self.poem_dir
            / f"s{int(scene['scene_no']):02d}"
            / "text"
            / "content_bpmf.png"
        )

    def explanation_png(self, scene: dict[str, str]) -> Path:
        return (
            self.poem_dir
            / f"s{int(scene['scene_no']):02d}"
            / "text"
            / "explanation.png"
        )

    def poem_wav(self, scene: dict[str, str]) -> Path:
        return (
            self.poem_dir
            / f"s{int(scene['scene_no']):02d}"
            / "audio"
            / "poem.wav"
        )

    def explanation_wav(self, scene: dict[str, str]) -> Path:
        return (
            self.poem_dir
            / f"s{int(scene['scene_no']):02d}"
            / "audio"
            / "explanation.wav"
        )

    def add_pause(
        self,
        *,
        session: str,
        kind: str,
        duration: float,
        background: Path,
        note: str = "",
    ) -> None:
        if duration <= 0:
            return
        start = self.cursor
        end = start + duration
        self.events.append(
            {
                "event_no": len(self.events) + 1,
                "session": session,
                "kind": kind,
                "start_sec": round_ms(start),
                "end_sec": round_ms(end),
                "duration_sec": round_ms(duration),
                "background_image": path_str(background),
                "audio_file": None,
                "note": note,
            }
        )
        self.cursor = end

    def add_audio_event(
        self,
        *,
        session: str,
        audio_type: str,
        audio_file: Path,
        background: Path,
        scene: dict[str, str] | None = None,
        page: int | None = None,
        slot: int | None = None,
        content_image: Path | None = None,
        explanation_image: Path | None = None,
        action: str | None = None,
    ) -> None:
        self.check(audio_file)
        if not audio_file.exists():
            return

        padding = self.timing["audio_padding"][audio_type]
        pre = float(padding["pre_roll_sec"])
        post = float(padding["post_roll_sec"])
        audio_sec = wav_duration(audio_file)

        start = self.cursor
        audio_start = start + pre
        audio_end = audio_start + audio_sec
        end = audio_end + post

        event: dict[str, Any] = {
            "event_no": len(self.events) + 1,
            "session": session,
            "kind": "audio",
            "audio_type": audio_type,
            "start_sec": round_ms(start),
            "audio_start_sec": round_ms(audio_start),
            "audio_end_sec": round_ms(audio_end),
            "end_sec": round_ms(end),
            "duration_sec": round_ms(end - start),
            "audio_duration_sec": round_ms(audio_sec),
            "pre_roll_sec": round_ms(pre),
            "post_roll_sec": round_ms(post),
            "audio_file": path_str(audio_file),
            "background_image": path_str(background),
            "persistent_overlays": [
                path_str(self.poem_dir / "text" / "title_bpmf.png"),
                path_str(self.poem_dir / "text" / "author.png"),
            ],
        }

        if scene is not None:
            event["scene_id"] = scene["scene_id"]
            event["scene_no"] = int(scene["scene_no"])
        if page is not None:
            event["content_page"] = page
        if slot is not None:
            event["content_slot"] = slot
        if content_image is not None:
            event["content_image"] = path_str(content_image)
        if explanation_image is not None:
            event["explanation_image"] = path_str(
                explanation_image
            )
        if action:
            event["visual_action"] = action

        self.events.append(event)
        self.cursor = end

    def scene_page_slot(
        self,
        scene: dict[str, str],
    ) -> tuple[int, int]:
        item = self.scene_text.get(scene["scene_id"])
        if not item:
            raise ValueError(
                f"missing text-manifest scene: {scene['scene_id']}"
            )
        if not item.get("render", False):
            raise ValueError(
                f"nonblank scene marked non-render: "
                f"{scene['scene_id']}"
            )
        return int(item["page"]), int(item["slot"])

    def validate_static_assets(self) -> None:
        for path in (
            self.poem_dir / "text" / "title_bpmf.png",
            self.poem_dir / "text" / "author.png",
            self.poem_dir / "text" / "poem_type.png",
            self.poem_dir / "audio" / "title.wav",
            self.poem_dir / "audio" / "author.wav",
        ):
            self.check(path)

        for scene in self.nonblank_scenes:
            for path in (
                self.background(scene),
                self.content_png(scene),
                self.explanation_png(scene),
                self.poem_wav(scene),
                self.explanation_wav(scene),
            ):
                self.check(path)

    def add_session_lead(
        self,
        session: str,
        background: Path,
    ) -> None:
        value = float(
            self.timing["session"]["lead_in_sec"][session]
        )
        self.add_pause(
            session=session,
            kind="session_lead_in",
            duration=value,
            background=background,
        )

    def add_session_tail(
        self,
        session: str,
        background: Path,
    ) -> None:
        value = float(
            self.timing["session"]["tail_hold_sec"][session]
        )
        self.add_pause(
            session=session,
            kind="session_tail_hold",
            duration=value,
            background=background,
        )

    def add_session_gap(
        self,
        session: str,
        background: Path,
    ) -> None:
        value = float(
            self.timing["session"]["between_sessions_sec"]
        )
        self.add_pause(
            session=session,
            kind="between_sessions",
            duration=value,
            background=background,
        )

    def build_intro(self) -> None:
        session = "session_intro"
        scene = self.nonblank_scenes[0]
        bg = self.background(scene)
        self.add_session_lead(session, bg)

        self.add_audio_event(
            session=session,
            audio_type="title",
            audio_file=self.poem_dir / "audio" / "title.wav",
            background=bg,
            action="show_title_author_poem_type",
        )
        self.add_audio_event(
            session=session,
            audio_type="author",
            audio_file=self.poem_dir / "audio" / "author.wav",
            background=bg,
            action="keep_title_author_poem_type",
        )
        self.add_session_tail(session, bg)
        self.add_session_gap(session, bg)

    def build_scene_session(
        self,
        *,
        session: str,
        with_explanation: bool,
    ) -> None:
        first_bg = self.background(self.nonblank_scenes[0])
        self.add_session_lead(session, first_bg)

        previous_page: int | None = None
        last_bg = first_bg

        for scene in self.nonblank_scenes:
            bg = self.background(scene)
            page, slot = self.scene_page_slot(scene)

            if (
                previous_page is not None
                and page != previous_page
            ):
                self.add_pause(
                    session=session,
                    kind="content_page_turn",
                    duration=float(
                        self.timing["transition"][
                            "page_turn_pause_sec"
                        ]
                    ),
                    background=bg,
                    note=(
                        "Clear content zone before revealing "
                        "new page slot 1."
                    ),
                )

            action = (
                "clear_content_then_reveal_line"
                if previous_page is not None
                and page != previous_page
                else "reveal_content_line"
            )

            self.add_audio_event(
                session=session,
                audio_type="poem",
                audio_file=self.poem_wav(scene),
                background=bg,
                scene=scene,
                page=page,
                slot=slot,
                content_image=self.content_png(scene),
                action=action,
            )

            if with_explanation:
                self.add_audio_event(
                    session=session,
                    audio_type="explanation",
                    audio_file=self.explanation_wav(scene),
                    background=bg,
                    scene=scene,
                    page=page,
                    slot=slot,
                    content_image=self.content_png(scene),
                    explanation_image=(
                        self.explanation_png(scene)
                    ),
                    action=(
                        "show_explanation_for_this_event_only"
                    ),
                )

            previous_page = page
            last_bg = bg

        self.add_session_tail(session, last_bg)
        self.add_session_gap(session, last_bg)

    def build_end(self) -> None:
        session = "session_end"
        scene = self.nonblank_scenes[-1]
        bg = self.background(scene)
        self.add_session_lead(session, bg)

        self.add_audio_event(
            session=session,
            audio_type="title",
            audio_file=self.poem_dir / "audio" / "title.wav",
            background=bg,
            action="show_title_author_poem_type",
        )
        self.add_audio_event(
            session=session,
            audio_type="author",
            audio_file=self.poem_dir / "audio" / "author.wav",
            background=bg,
            action="keep_title_author_poem_type",
        )
        self.add_session_tail(session, bg)

    def build(self) -> dict[str, Any]:
        self.validate_static_assets()

        self.build_intro()
        self.build_scene_session(
            session="session_content",
            with_explanation=False,
        )
        self.build_scene_session(
            session="session_explain",
            with_explanation=True,
        )
        self.build_scene_session(
            session="session_recap1",
            with_explanation=False,
        )
        self.build_scene_session(
            session="session_recap2",
            with_explanation=False,
        )
        self.build_end()

        by_session: dict[str, dict[str, Any]] = {}
        for event in self.events:
            session = event["session"]
            info = by_session.setdefault(
                session,
                {
                    "start_sec": event["start_sec"],
                    "end_sec": event["end_sec"],
                    "event_count": 0,
                },
            )
            info["end_sec"] = event["end_sec"]
            info["event_count"] += 1

        for info in by_session.values():
            info["duration_sec"] = round_ms(
                float(info["end_sec"])
                - float(info["start_sec"])
            )

        return {
            "version": self.timing["version"],
            "poem_id": self.pid,
            "title": self.poem["title"],
            "author": self.poem["author"],
            "style_id": self.style_id,
            "canvas": {
                "width": 1920,
                "height": 1080,
                "fps": int(
                    self.timing["constraints"]["frame_rate"]
                ),
            },
            "total_duration_sec": round_ms(self.cursor),
            "preflight": {
                "status": "PASS" if not self.missing else "FAIL",
                "missing_asset_count": len(self.missing),
                "missing_assets": self.missing,
            },
            "session_summary": by_session,
            "events": self.events,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build audio-driven timeline manifests; no video rendering."
        )
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--poem-id", type=int)
    selector.add_argument("--age", type=int, choices=(6, 7, 8, 9))
    parser.add_argument("--style", default="B")
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument("--scenes", default="data/scenes.csv")
    parser.add_argument(
        "--timing",
        default=str(DEFAULT_TIMING),
    )
    parser.add_argument(
        "--sessions",
        default=str(DEFAULT_SESSIONS),
    )
    parser.add_argument(
        "--style-registry",
        default=str(DEFAULT_STYLE_REGISTRY),
    )
    parser.add_argument("--assets-root", default="assets")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print summary without writing timeline.json.",
    )
    args = parser.parse_args()

    poems = read_csv(Path(args.poems))
    scenes = read_csv(Path(args.scenes))
    timing = read_json(Path(args.timing))
    sessions = read_json(Path(args.sessions))
    registry = read_json(Path(args.style_registry))

    if sessions.get("version") != "video_sessions_v1":
        raise ValueError(
            "timeline builder currently requires video_sessions_v1"
        )

    style_id = style_id_for_key(
        registry,
        args.style.upper(),
    )
    selected = select_poems(
        poems,
        poem_id=args.poem_id,
        age=args.age,
    )

    if not selected:
        print("ERROR: no poems matched selection")
        return 2

    failed = 0

    for poem in selected:
        pid = int(poem["poem_id"])
        poem_dir = Path(args.assets_root) / f"p{pid:03d}"
        text_manifest_path = (
            poem_dir / "text" / "manifest.json"
        )

        if not text_manifest_path.exists():
            print(
                f"p{pid:03d} FAIL missing "
                f"{text_manifest_path.as_posix()}"
            )
            failed += 1
            continue

        builder = TimelineBuilder(
            poem=poem,
            scenes=poem_scenes(scenes, pid),
            text_manifest=read_json(text_manifest_path),
            timing=timing,
            style_id=style_id,
            assets_root=Path(args.assets_root),
        )
        timeline = builder.build()

        status = timeline["preflight"]["status"]
        print(
            f"p{pid:03d} {poem['title']} "
            f"{status} duration="
            f"{timeline['total_duration_sec']:.3f}s "
            f"events={len(timeline['events'])} "
            f"missing="
            f"{timeline['preflight']['missing_asset_count']}"
        )

        if status != "PASS":
            failed += 1
            for path in timeline["preflight"][
                "missing_assets"
            ]:
                print(f"  MISSING {path}")

        if not args.dry_run:
            output = poem_dir / "video" / "timeline.json"
            write_json(output, timeline)
            print(f"  -> {output.as_posix()}")

    print("\nSummary")
    print(f"poems={len(selected)}")
    print(f"failed={failed}")
    print(
        "mode="
        + ("dry_run" if args.dry_run else "write_timeline")
    )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
