#!/usr/bin/env python3
"""Render one poem MP4 from an existing timeline.json.

POC composer v1 intentionally focuses on contract correctness:
- 1920x1080 / 30 fps / H.264 / AAC
- measured timeline duration and audio pre/post rolls
- title + author persistent overlays
- intro/end poem_type
- progressive four-slot content accumulation
- explanation overlay only during explanation events
- content reset at each content-bearing session and page turn

Visual transitions are NOT yet blended in this first assembly test.
The timeline already reserves transition time, so later crossfade/motion
can be added without changing the audio/session contract.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image


DEFAULT_TEXT_CONFIG = Path("config/text_overlay_1080p_v2.json")
CONTENT_SESSIONS = {
    "session_content",
    "session_explain",
    "session_recap1",
    "session_recap2",
}
POEM_TYPE_SESSIONS = {
    "session_intro",
    "session_end",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def repo_path(value: str) -> Path:
    return Path(value)


def require_file(path: Path, *, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"{label} not found: {path.as_posix()}"
        )


def ffmpeg_path(explicit: str | None) -> str:
    if explicit:
        path = shutil.which(explicit)
        if path:
            return path
        candidate = Path(explicit)
        if candidate.exists():
            return str(candidate)
        raise FileNotFoundError(
            f"ffmpeg not found: {explicit}"
        )

    path = shutil.which("ffmpeg")
    if not path:
        raise FileNotFoundError(
            "ffmpeg was not found in PATH. Install FFmpeg "
            "and reopen the terminal before rendering video."
        )
    return path


def display_asset(path: Path, raster_scale: int) -> Image.Image:
    require_file(path, label="overlay")
    with Image.open(path) as source:
        image = source.convert("RGBA")

    if raster_scale <= 1:
        return image

    width = max(1, round(image.width / raster_scale))
    height = max(1, round(image.height / raster_scale))
    return image.resize(
        (width, height),
        Image.Resampling.LANCZOS,
    )


def cover_background(
    path: Path,
    *,
    width: int,
    height: int,
) -> Image.Image:
    require_file(path, label="background")
    with Image.open(path) as source:
        image = source.convert("RGB")

    scale = max(
        width / image.width,
        height / image.height,
    )
    resized = image.resize(
        (
            max(1, math.ceil(image.width * scale)),
            max(1, math.ceil(image.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )

    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)

    return resized.crop(
        (left, top, left + width, top + height)
    ).convert("RGBA")


def paste_centered(
    canvas: Image.Image,
    overlay: Image.Image,
    *,
    center_x: int,
    center_y: int,
) -> None:
    x = round(center_x - overlay.width / 2)
    y = round(center_y - overlay.height / 2)
    canvas.alpha_composite(overlay, (x, y))


def zone_center(zone: dict[str, Any]) -> tuple[int, int]:
    return (
        int(zone["x"]) + int(zone["width"]) // 2,
        int(zone["y"]) + int(zone["height"]) // 2,
    )


class VisualState:
    def __init__(
        self,
        *,
        poem_id: int,
        text_config: dict[str, Any],
        text_manifest: dict[str, Any],
    ) -> None:
        self.pid = poem_id
        self.config = text_config
        self.manifest = text_manifest
        self.raster_scale = int(
            text_manifest.get(
                "raster_scale",
                text_config.get("raster_scale", 1),
            )
        )
        self.current_session: str | None = None
        self.current_page: int | None = None
        self.content_by_slot: dict[int, Path] = {}

        poem_dir = Path("assets") / f"p{poem_id:03d}"
        self.title = poem_dir / "text" / "title_bpmf.png"
        self.author = poem_dir / "text" / "author.png"
        self.poem_type = poem_dir / "text" / "poem_type.png"

        for path, label in (
            (self.title, "title overlay"),
            (self.author, "author overlay"),
            (self.poem_type, "poem_type overlay"),
        ):
            require_file(path, label=label)

        self._cache: dict[str, Image.Image] = {}

    def cached_overlay(self, path: Path) -> Image.Image:
        key = path.as_posix()
        cached = self._cache.get(key)
        if cached is None:
            cached = display_asset(
                path,
                self.raster_scale,
            )
            self._cache[key] = cached
        return cached

    def begin_event(self, event: dict[str, Any]) -> None:
        session = str(event["session"])

        if session != self.current_session:
            self.current_session = session
            self.current_page = None
            self.content_by_slot = {}

        if session not in CONTENT_SESSIONS:
            self.current_page = None
            self.content_by_slot = {}
            return

        if event["kind"] == "content_page_turn":
            self.current_page = None
            self.content_by_slot = {}
            return

        if (
            event.get("kind") == "audio"
            and event.get("audio_type") == "poem"
            and event.get("content_image")
        ):
            page = int(event["content_page"])
            slot = int(event["content_slot"])

            if (
                self.current_page is not None
                and page != self.current_page
            ):
                self.content_by_slot = {}

            self.current_page = page
            self.content_by_slot[slot] = repo_path(
                str(event["content_image"])
            )

    def compose(
        self,
        event: dict[str, Any],
        *,
        width: int,
        height: int,
    ) -> Image.Image:
        self.begin_event(event)

        background = cover_background(
            repo_path(str(event["background_image"])),
            width=width,
            height=height,
        )

        zones = self.config["zones"]

        # Title and author are persistent through all six sessions.
        tx, ty = zone_center(zones["title"])
        paste_centered(
            background,
            self.cached_overlay(self.title),
            center_x=tx,
            center_y=ty,
        )

        ax, ay = zone_center(zones["author"])
        paste_centered(
            background,
            self.cached_overlay(self.author),
            center_x=ax,
            center_y=ay,
        )

        session = str(event["session"])
        if session in POEM_TYPE_SESSIONS:
            px, py = zone_center(zones["poem_type"])
            paste_centered(
                background,
                self.cached_overlay(self.poem_type),
                center_x=px,
                center_y=py,
            )

        if session in CONTENT_SESSIONS:
            content_zone = zones["content"]
            center_x = (
                int(content_zone["x"])
                + int(content_zone["width"]) // 2
            )
            slots = {
                int(item["slot"]): int(item["center_y"])
                for item in content_zone["slots"]
            }

            for slot in sorted(self.content_by_slot):
                path = self.content_by_slot[slot]
                paste_centered(
                    background,
                    self.cached_overlay(path),
                    center_x=center_x,
                    center_y=slots[slot],
                )

        if (
            event.get("kind") == "audio"
            and event.get("audio_type") == "explanation"
            and event.get("explanation_image")
        ):
            ex, ey = zone_center(zones["explanation"])
            explanation = repo_path(
                str(event["explanation_image"])
            )
            paste_centered(
                background,
                self.cached_overlay(explanation),
                center_x=ex,
                center_y=ey,
            )

        return background.convert("RGB")


def run_command(
    command: list[str],
    *,
    verbose: bool,
) -> None:
    if verbose:
        print("  $ " + " ".join(command))

    process = subprocess.run(
        command,
        stdout=None if verbose else subprocess.DEVNULL,
        stderr=None if verbose else subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        if not verbose and process.stderr:
            print(process.stderr, file=sys.stderr)
        raise RuntimeError(
            f"command failed with exit code {process.returncode}"
        )


def event_frame_count(
    *,
    event_end_sec: float,
    fps: int,
    previous_end_frame: int,
) -> tuple[int, int]:
    target_end = round(event_end_sec * fps)
    count = target_end - previous_end_frame
    if count < 1:
        count = 1
        target_end = previous_end_frame + 1
    return count, target_end


def render_segment(
    *,
    ffmpeg: str,
    frame_path: Path,
    segment_path: Path,
    event: dict[str, Any],
    duration_sec: float,
    fps: int,
    verbose: bool,
) -> None:
    common_video = [
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(frame_path),
    ]

    audio_file = event.get("audio_file")

    if audio_file:
        audio_path = repo_path(str(audio_file))
        require_file(audio_path, label="audio")
        delay_ms = round(
            float(event.get("pre_roll_sec", 0.0)) * 1000
        )
        filter_complex = (
            f"[1:a]adelay={delay_ms}:all=1,"
            f"apad,atrim=duration={duration_sec:.6f},"
            "aresample=48000[a]"
        )
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning" if verbose else "error",
            "-y",
            *common_video,
            "-i",
            str(audio_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[a]",
        ]
    else:
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning" if verbose else "error",
            "-y",
            *common_video,
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
        ]

    command += [
        "-t",
        f"{duration_sec:.6f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(segment_path),
    ]

    run_command(command, verbose=verbose)


def concat_segments(
    *,
    ffmpeg: str,
    segment_paths: list[Path],
    output_path: Path,
    work_dir: Path,
    verbose: bool,
) -> None:
    concat_path = work_dir / "concat.txt"

    def quote(path: Path) -> str:
        value = path.resolve().as_posix()
        return value.replace("'", "'\\''")

    concat_path.write_text(
        "".join(
            f"file '{quote(path)}'\n"
            for path in segment_paths
        ),
        encoding="utf-8",
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning" if verbose else "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    run_command(command, verbose=verbose)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render one poem MP4 from an existing timeline.json."
        )
    )
    parser.add_argument(
        "--poem-id",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--style",
        default="B",
        help="Output filename label only; timeline owns actual style_id.",
    )
    parser.add_argument(
        "--timeline",
        help=(
            "Override timeline path. Default: "
            "assets/pXXX/video/timeline.json"
        ),
    )
    parser.add_argument(
        "--text-config",
        default=str(DEFAULT_TEXT_CONFIG),
    )
    parser.add_argument(
        "--output",
        help=(
            "Override MP4 path. Default: "
            "assets/pXXX/video/pXXX_<style>_1080p.mp4"
        ),
    )
    parser.add_argument(
        "--ffmpeg",
        help="ffmpeg executable name or full path.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary event frames/segments for debugging.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate timeline/assets and compose event frames in memory "
            "without invoking FFmpeg."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pid = args.poem_id
    poem_dir = Path("assets") / f"p{pid:03d}"

    timeline_path = (
        Path(args.timeline)
        if args.timeline
        else poem_dir / "video" / "timeline.json"
    )
    text_manifest_path = (
        poem_dir / "text" / "manifest.json"
    )
    text_config_path = Path(args.text_config)

    for path, label in (
        (timeline_path, "timeline"),
        (text_manifest_path, "text manifest"),
        (text_config_path, "text config"),
    ):
        require_file(path, label=label)

    timeline = read_json(timeline_path)
    text_manifest = read_json(text_manifest_path)
    text_config = read_json(text_config_path)

    if int(timeline["poem_id"]) != pid:
        raise ValueError(
            f"timeline poem_id={timeline['poem_id']} "
            f"does not match --poem-id {pid}"
        )

    if timeline.get("preflight", {}).get("status") != "PASS":
        raise ValueError(
            "timeline preflight is not PASS; rebuild timeline first"
        )

    canvas = timeline["canvas"]
    width = int(canvas["width"])
    height = int(canvas["height"])
    fps = int(canvas["fps"])

    if (
        width != int(text_config["canvas"]["width"])
        or height != int(text_config["canvas"]["height"])
    ):
        raise ValueError(
            "timeline and text-overlay canvas sizes do not match"
        )

    output_path = (
        Path(args.output)
        if args.output
        else poem_dir
        / "video"
        / f"p{pid:03d}_{args.style.upper()}_1080p.mp4"
    )

    if output_path.exists() and not args.force:
        print(
            f"ERROR: output already exists: {output_path.as_posix()}\n"
            "Use --force to overwrite it.",
            file=sys.stderr,
        )
        return 2

    events = timeline.get("events", [])
    if not events:
        raise ValueError("timeline contains no events")

    state = VisualState(
        poem_id=pid,
        text_config=text_config,
        text_manifest=text_manifest,
    )

    previous_end_frame = 0
    planned: list[dict[str, Any]] = []

    print(
        f"p{pid:03d} {timeline['title']} "
        f"events={len(events)} "
        f"timeline={timeline['total_duration_sec']:.3f}s "
        f"{width}x{height}@{fps}"
    )

    for event in events:
        frame_count, target_end_frame = event_frame_count(
            event_end_sec=float(event["end_sec"]),
            fps=fps,
            previous_end_frame=previous_end_frame,
        )
        duration = frame_count / fps

        # This call validates all visual assets and state transitions
        # even during --dry-run.
        frame = state.compose(
            event,
            width=width,
            height=height,
        )

        planned.append(
            {
                "event": event,
                "frame": frame,
                "frame_count": frame_count,
                "duration_sec": duration,
            }
        )
        previous_end_frame = target_end_frame

    rendered_duration = previous_end_frame / fps
    print(
        f"frame_snapped_duration={rendered_duration:.3f}s "
        f"frames={previous_end_frame}"
    )

    if args.dry_run:
        print("PASS dry_run=true; FFmpeg was not invoked.")
        return 0

    ffmpeg = ffmpeg_path(args.ffmpeg)
    print(f"ffmpeg={ffmpeg}")

    work_parent = poem_dir / "video"
    work_parent.mkdir(parents=True, exist_ok=True)

    if args.keep_temp:
        work_dir = work_parent / "_render_tmp"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True)
        temp_context = None
    else:
        temp_context = tempfile.TemporaryDirectory(
            prefix=f"poem300_p{pid:03d}_"
        )
        work_dir = Path(temp_context.name)

    try:
        segment_paths: list[Path] = []

        for index, item in enumerate(planned, start=1):
            event = item["event"]
            frame_path = (
                work_dir / f"event_{index:03d}.png"
            )
            segment_path = (
                work_dir / f"segment_{index:03d}.mp4"
            )

            item["frame"].save(
                frame_path,
                "PNG",
                optimize=True,
            )

            print(
                f"[{index:02d}/{len(planned):02d}] "
                f"{event['session']} "
                f"{event['kind']}"
                + (
                    f"/{event.get('audio_type')}"
                    if event.get("audio_type")
                    else ""
                )
                + f" {item['duration_sec']:.3f}s"
            )

            render_segment(
                ffmpeg=ffmpeg,
                frame_path=frame_path,
                segment_path=segment_path,
                event=event,
                duration_sec=float(
                    item["duration_sec"]
                ),
                fps=fps,
                verbose=args.verbose,
            )
            segment_paths.append(segment_path)

        concat_segments(
            ffmpeg=ffmpeg,
            segment_paths=segment_paths,
            output_path=output_path,
            work_dir=work_dir,
            verbose=args.verbose,
        )

    finally:
        if temp_context is not None:
            temp_context.cleanup()

    print("\nPASS")
    print(f"output={output_path.as_posix()}")
    print(
        f"duration_target={rendered_duration:.3f}s"
    )
    print(
        "composer_mode=assembly_poc_v1 "
        "(hard cuts; transition time already reserved)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
