#!/usr/bin/env python3
"""Render one poem MP4 from an existing timeline.json.

Composer v3 adds run-level background motion while preserving the exact
audio-driven timeline:
- each consecutive background run is rendered once as one motion clip
- crop-window zoom alternates 100% -> 80%, then 80% -> 100%
- FFmpeg perspective/cubic performs subpixel-centered sampling; zoompan is not used
- 0.30s scene crossfade when background_image changes
- fade-in for each newly revealed content line
- fade-in/out for explanation overlays
- title / author remain fixed in display space
- content accumulation and page/session reset rules remain unchanged

The motion layer never changes event durations or audio start times.
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
DEFAULT_MOTION_CONFIG = Path("config/video_motion_v1.json")

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
            "ffmpeg.exe was not found in PATH. "
            "The Python package installed by 'pip install FFmpeg' "
            "does not include the FFmpeg executable. "
            "On Windows install the binary, for example: "
            "winget install --id Gyan.FFmpeg -e ; "
            "then reopen PowerShell and verify with: ffmpeg -version. "
            "Alternatively pass --ffmpeg C:\\path\\to\\ffmpeg.exe."
        )
    return path


def display_asset(
    path: Path,
    raster_scale: int,
) -> Image.Image:
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


def zone_center(
    zone: dict[str, Any],
) -> tuple[int, int]:
    return (
        int(zone["x"]) + int(zone["width"]) // 2,
        int(zone["y"]) + int(zone["height"]) // 2,
    )


def transparent_canvas(
    width: int,
    height: int,
) -> Image.Image:
    return Image.new(
        "RGBA",
        (width, height),
        (0, 0, 0, 0),
    )


class OverlayState:
    """Stateful text-overlay planner for timeline events."""

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

    def _paste_poem_identity(
        self,
        canvas: Image.Image,
        *,
        session: str,
    ) -> None:
        zones = self.config["zones"]

        tx, ty = zone_center(zones["title"])
        paste_centered(
            canvas,
            self.cached_overlay(self.title),
            center_x=tx,
            center_y=ty,
        )

        ax, ay = zone_center(zones["author"])
        paste_centered(
            canvas,
            self.cached_overlay(self.author),
            center_x=ax,
            center_y=ay,
        )

        if session in POEM_TYPE_SESSIONS:
            px, py = zone_center(zones["poem_type"])
            paste_centered(
                canvas,
                self.cached_overlay(self.poem_type),
                center_x=px,
                center_y=py,
            )

    def _paste_content(
        self,
        canvas: Image.Image,
        content_by_slot: dict[int, Path],
    ) -> None:
        if not content_by_slot:
            return

        zone = self.config["zones"]["content"]
        center_x = int(zone["x"]) + int(zone["width"]) // 2
        slots = {
            int(item["slot"]): int(item["center_y"])
            for item in zone["slots"]
        }

        for slot in sorted(content_by_slot):
            path = content_by_slot[slot]
            require_file(path, label="content overlay")
            paste_centered(
                canvas,
                self.cached_overlay(path),
                center_x=center_x,
                center_y=slots[slot],
            )

    def build_layers(
        self,
        event: dict[str, Any],
        *,
        width: int,
        height: int,
    ) -> tuple[Image.Image, Image.Image | None, str | None]:
        """Return static layer, optional fade layer, and fade kind.

        The state is advanced to the visual state that should be visible
        after the fade-in has completed.
        """

        session = str(event["session"])

        if session != self.current_session:
            self.current_session = session
            self.current_page = None
            self.content_by_slot = {}

        static_layer = transparent_canvas(width, height)
        fade_layer: Image.Image | None = None
        fade_kind: str | None = None

        self._paste_poem_identity(
            static_layer,
            session=session,
        )

        if session not in CONTENT_SESSIONS:
            self.current_page = None
            self.content_by_slot = {}
            return static_layer, None, None

        if event["kind"] == "content_page_turn":
            self.current_page = None
            self.content_by_slot = {}
            return static_layer, None, None

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

            # Existing content remains fully visible.
            previous_content = dict(self.content_by_slot)
            self._paste_content(
                static_layer,
                previous_content,
            )

            new_path = repo_path(
                str(event["content_image"])
            )
            require_file(new_path, label="content overlay")

            fade_layer = transparent_canvas(
                width,
                height,
            )
            zone = self.config["zones"]["content"]
            center_x = (
                int(zone["x"])
                + int(zone["width"]) // 2
            )
            slots = {
                int(item["slot"]): int(item["center_y"])
                for item in zone["slots"]
            }
            paste_centered(
                fade_layer,
                self.cached_overlay(new_path),
                center_x=center_x,
                center_y=slots[slot],
            )
            fade_kind = "content"

            self.current_page = page
            self.content_by_slot[slot] = new_path
            return static_layer, fade_layer, fade_kind

        # Non-poem events in content-bearing sessions retain all currently
        # revealed lines.
        self._paste_content(
            static_layer,
            self.content_by_slot,
        )

        if (
            event.get("kind") == "audio"
            and event.get("audio_type") == "explanation"
            and event.get("explanation_image")
        ):
            explanation_path = repo_path(
                str(event["explanation_image"])
            )
            require_file(
                explanation_path,
                label="explanation overlay",
            )

            fade_layer = transparent_canvas(
                width,
                height,
            )
            ex, ey = zone_center(
                self.config["zones"]["explanation"]
            )
            paste_centered(
                fade_layer,
                self.cached_overlay(explanation_path),
                center_x=ex,
                center_y=ey,
            )
            fade_kind = "explanation"

        return static_layer, fade_layer, fade_kind


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


def plan_background_runs(
    planned: list[dict[str, Any]],
    motion: dict[str, Any],
) -> list[dict[str, Any]]:
    """Plan one continuous zoom clip per consecutive background run.

    This is deliberately different from event-level zoom. Timeline events
    may split one scene into poem/explanation/pause pieces, but the
    background motion is rendered once across the full consecutive run.
    Event segments later slice that already-rendered motion clip.
    """

    background_motion = motion["background_motion"]
    crop_a = float(
        background_motion["crop_window_a_pct"]
    )
    crop_b = float(
        background_motion["crop_window_b_pct"]
    )

    for value, label in (
        (crop_a, "crop_window_a_pct"),
        (crop_b, "crop_window_b_pct"),
    ):
        if value <= 0 or value > 100:
            raise ValueError(
                f"{label} must be > 0 and <= 100; got {value}"
            )

    runs: list[dict[str, Any]] = []
    index = 0

    while index < len(planned):
        background = planned[index]["background_image"]
        end = index + 1

        while (
            end < len(planned)
            and planned[end]["background_image"] == background
        ):
            end += 1

        group = planned[index:end]
        run_no = len(runs) + 1
        total_frames = sum(
            int(item["frame_count"])
            for item in group
        )
        total_frames = max(1, total_frames)

        # "100% -> 80%" means the centered crop window shrinks from
        # full-frame to 80% of the frame. In zoom-factor terms this is
        # 1.00 -> 1.25. The next background run reverses it.
        if run_no % 2 == 1:
            crop_start = crop_a
            crop_end = crop_b
        else:
            crop_start = crop_b
            crop_end = crop_a

        zoom_start = 100.0 / crop_start
        zoom_end = 100.0 / crop_end

        consumed = 0
        for item in group:
            count = int(item["frame_count"])
            item["background_run_no"] = run_no
            item["background_run_start_frame"] = consumed
            item["background_run_end_frame"] = (
                consumed + count
            )
            item["background_run_total_frames"] = total_frames
            item["background_run_crop_start_pct"] = crop_start
            item["background_run_crop_end_pct"] = crop_end
            item["background_run_zoom_start"] = zoom_start
            item["background_run_zoom_end"] = zoom_end
            consumed += count

        runs.append(
            {
                "run_no": run_no,
                "background_image": background,
                "total_frames": total_frames,
                "duration_sec": total_frames
                / int(planned[0]["fps"]),
                "crop_start_pct": crop_start,
                "crop_end_pct": crop_end,
                "zoom_start": zoom_start,
                "zoom_end": zoom_end,
            }
        )

        index = end

    return runs


def clamp_effect(
    requested: float,
    *,
    duration: float,
    fps: int,
) -> float:
    if requested <= 0:
        return 0.0

    one_frame = 1.0 / fps
    maximum = max(
        0.0,
        duration - one_frame,
    )
    return min(requested, maximum)


def background_filter(
    *,
    input_label: str,
    output_label: str,
    width: int,
    height: int,
    fps: int,
    frame_count: int,
    crop_start_pct: float,
    crop_end_pct: float,
) -> str:
    """Build one continuous centered zoom with subpixel sampling.

    Do not use FFmpeg zoompan here. zoompan quantizes its crop origin to
    integer source pixels, which produces visible frame-to-frame center
    jitter during a slow centered zoom.

    perspective with eval=frame accepts floating-point source corner
    coordinates. That lets the centered crop window move continuously
    from crop_start_pct to crop_end_pct without left/right or up/down
    integer-pixel hopping.
    """

    denominator = max(1, frame_count - 1)

    start_fraction = crop_start_pct / 100.0
    end_fraction = crop_end_pct / 100.0

    start_margin = (1.0 - start_fraction) / 2.0
    end_margin = (1.0 - end_fraction) / 2.0
    delta_margin = end_margin - start_margin

    margin_expr = (
        f"({start_margin:.12f}"
        f"+({delta_margin:.12f})*in/{denominator})"
    )
    mx = f"W*{margin_expr}"
    my = f"H*{margin_expr}"

    return (
        f"[{input_label}:v]"
        f"scale={width}:{height}:"
        "force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        "setsar=1,"
        "perspective="
        f"x0='{mx}':y0='{my}':"
        f"x1='W-{mx}':y1='{my}':"
        f"x2='{mx}':y2='H-{my}':"
        f"x3='W-{mx}':y3='H-{my}':"
        "sense=source:"
        "interpolation=cubic:"
        "eval=frame,"
        f"fps={fps},"
        f"trim=end_frame={frame_count},"
        "setpts=PTS-STARTPTS,"
        "format=yuv420p"
        f"[{output_label}]"
    )


def render_background_run(
    *,
    ffmpeg: str,
    background_image: Path,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    frame_count: int,
    crop_start_pct: float,
    crop_end_pct: float,
    verbose: bool,
) -> None:
    """Render a background run once, before timeline-event slicing."""

    require_file(
        background_image,
        label="background",
    )

    filters = background_filter(
        input_label="0",
        output_label="v",
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        crop_start_pct=crop_start_pct,
        crop_end_pct=crop_end_pct,
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning" if verbose else "error",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(background_image),
        "-filter_complex",
        filters,
        "-map",
        "[v]",
        "-frames:v",
        str(frame_count),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "0",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]

    run_command(
        command,
        verbose=verbose,
    )


def render_segment(
    *,
    ffmpeg: str,
    background_run_file: Path,
    background_start_frame: int,
    background_end_frame: int,
    previous_background_run_file: Path | None,
    previous_background_run_frames: int | None,
    static_overlay: Path,
    fade_overlay: Path | None,
    fade_kind: str | None,
    segment_path: Path,
    event: dict[str, Any],
    duration_sec: float,
    frame_count: int,
    fps: int,
    motion: dict[str, Any],
    verbose: bool,
) -> None:
    require_file(
        background_run_file,
        label="background run clip",
    )
    require_file(
        static_overlay,
        label="static overlay",
    )

    command: list[str] = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning" if verbose else "error",
        "-y",
        "-i",
        str(background_run_file),
    ]

    current_index = 0
    next_index = 1

    transition_requested = float(
        motion["scene_transition"]["duration_sec"]
    )
    transition_sec = 0.0
    previous_index: int | None = None

    if previous_background_run_file is not None:
        require_file(
            previous_background_run_file,
            label="previous background run clip",
        )
        if not previous_background_run_frames:
            raise ValueError(
                "previous_background_run_frames is required "
                "when previous_background_run_file is provided"
            )

        previous_index = next_index
        next_index += 1
        command += [
            "-i",
            str(previous_background_run_file),
        ]
        transition_sec = clamp_effect(
            transition_requested,
            duration=duration_sec,
            fps=fps,
        )

    static_index = next_index
    next_index += 1
    command += [
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(static_overlay),
    ]

    fade_index: int | None = None
    if fade_overlay is not None:
        require_file(
            fade_overlay,
            label="fade overlay",
        )
        fade_index = next_index
        next_index += 1
        command += [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(fade_overlay),
        ]

    audio_index = next_index
    audio_file = event.get("audio_file")

    if audio_file:
        audio_path = repo_path(str(audio_file))
        require_file(audio_path, label="audio")
        command += [
            "-i",
            str(audio_path),
        ]
        audio_delay_sec = float(
            event.get("pre_roll_sec", 0.0)
        )
    else:
        command += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
        audio_delay_sec = 0.0

    filters: list[str] = []

    # Slice the already-rendered run. No zoom is recalculated here, so
    # poem/explanation/pause event boundaries cannot reset the camera.
    filters.append(
        f"[{current_index}:v]"
        f"trim=start_frame={background_start_frame}:"
        f"end_frame={background_end_frame},"
        "setpts=PTS-STARTPTS"
        "[bgcur]"
    )

    background_label = "bgcur"

    if (
        previous_index is not None
        and transition_sec > 0
    ):
        last_frame = int(
            previous_background_run_frames
        ) - 1

        # Freeze the exact last frame of the outgoing run through the
        # short crossfade. This preserves its true final zoom position.
        filters.append(
            f"[{previous_index}:v]"
            f"trim=start_frame={last_frame}:"
            f"end_frame={last_frame + 1},"
            "setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:"
            f"stop_duration={duration_sec:.6f}"
            "[bgprev]"
        )
        filters.append(
            "[bgprev][bgcur]"
            "xfade=transition=fade:"
            f"duration={transition_sec:.6f}:offset=0"
            "[bg]"
        )
        background_label = "bg"

    filters.append(
        f"[{static_index}:v]"
        "format=rgba,"
        f"fps={fps},"
        f"trim=duration={duration_sec:.6f},"
        "setpts=PTS-STARTPTS"
        "[static]"
    )
    filters.append(
        f"[{background_label}][static]"
        "overlay=0:0:format=auto:shortest=1"
        "[vbase]"
    )

    final_video_label = "vbase"

    if (
        fade_index is not None
        and fade_kind is not None
    ):
        if fade_kind == "content":
            requested_in = float(
                motion["content_overlay"][
                    "fade_in_sec"
                ]
            )
            fade_in = clamp_effect(
                requested_in,
                duration=duration_sec,
                fps=fps,
            )
            fade_filter = (
                f"[{fade_index}:v]"
                "format=rgba,"
                f"fps={fps},"
                f"trim=duration={duration_sec:.6f},"
                "setpts=PTS-STARTPTS"
            )
            if fade_in > 0:
                fade_filter += (
                    f",fade=t=in:st=0:"
                    f"d={fade_in:.6f}:alpha=1"
                )
            fade_filter += "[fade]"
        elif fade_kind == "explanation":
            requested_in = float(
                motion["explanation_overlay"][
                    "fade_in_sec"
                ]
            )
            requested_out = float(
                motion["explanation_overlay"][
                    "fade_out_sec"
                ]
            )

            fade_in = clamp_effect(
                requested_in,
                duration=duration_sec,
                fps=fps,
            )
            fade_out = clamp_effect(
                requested_out,
                duration=duration_sec,
                fps=fps,
            )

            maximum_pair = max(
                0.0,
                duration_sec - (1.0 / fps),
            )
            if fade_in + fade_out > maximum_pair:
                scale = (
                    maximum_pair
                    / max(fade_in + fade_out, 1e-9)
                )
                fade_in *= scale
                fade_out *= scale

            fade_filter = (
                f"[{fade_index}:v]"
                "format=rgba,"
                f"fps={fps},"
                f"trim=duration={duration_sec:.6f},"
                "setpts=PTS-STARTPTS"
            )
            if fade_in > 0:
                fade_filter += (
                    f",fade=t=in:st=0:"
                    f"d={fade_in:.6f}:alpha=1"
                )
            if fade_out > 0:
                start_out = max(
                    0.0,
                    duration_sec - fade_out,
                )
                fade_filter += (
                    f",fade=t=out:"
                    f"st={start_out:.6f}:"
                    f"d={fade_out:.6f}:alpha=1"
                )
            fade_filter += "[fade]"
        else:
            raise ValueError(
                f"unsupported fade kind: {fade_kind}"
            )

        filters.append(fade_filter)
        filters.append(
            "[vbase][fade]"
            "overlay=0:0:format=auto:shortest=1"
            "[vout]"
        )
        final_video_label = "vout"

    delay_ms = round(audio_delay_sec * 1000)

    filters.append(
        f"[{audio_index}:a]"
        f"adelay={delay_ms}:all=1,"
        "apad,"
        f"atrim=duration={duration_sec:.6f},"
        "aresample=48000,"
        "aformat=channel_layouts=stereo"
        "[a]"
    )

    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{final_video_label}]",
        "-map",
        "[a]",
        "-frames:v",
        str(frame_count),
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

    run_command(
        command,
        verbose=verbose,
    )


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

    run_command(
        command,
        verbose=verbose,
    )


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
        help=(
            "Output filename label only; timeline owns actual style_id."
        ),
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
        "--motion-config",
        default=str(DEFAULT_MOTION_CONFIG),
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
        help=(
            "Keep temporary overlay frames and event segments "
            "for debugging."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate timeline/assets and overlay state without "
            "invoking FFmpeg."
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
    motion_config_path = Path(args.motion_config)

    for path, label in (
        (timeline_path, "timeline"),
        (text_manifest_path, "text manifest"),
        (text_config_path, "text config"),
        (motion_config_path, "motion config"),
    ):
        require_file(path, label=label)

    timeline = read_json(timeline_path)
    text_manifest = read_json(text_manifest_path)
    text_config = read_json(text_config_path)
    motion = read_json(motion_config_path)

    if motion.get("version") != "video_motion_v1":
        raise ValueError(
            "composer currently requires video_motion_v1"
        )

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
            f"ERROR: output already exists: "
            f"{output_path.as_posix()}\n"
            "Use --force to overwrite it.",
            file=sys.stderr,
        )
        return 2

    events = timeline.get("events", [])
    if not events:
        raise ValueError("timeline contains no events")

    previous_end_frame = 0
    planned: list[dict[str, Any]] = []

    for event in events:
        frame_count, target_end_frame = event_frame_count(
            event_end_sec=float(event["end_sec"]),
            fps=fps,
            previous_end_frame=previous_end_frame,
        )
        duration = frame_count / fps

        planned.append(
            {
                "event": event,
                "frame_count": frame_count,
                "duration_sec": duration,
                "fps": fps,
                "background_image": str(
                    event["background_image"]
                ),
            }
        )
        previous_end_frame = target_end_frame

    background_runs = plan_background_runs(
        planned,
        motion,
    )

    rendered_duration = previous_end_frame / fps

    print(
        f"p{pid:03d} {timeline['title']} "
        f"events={len(events)} "
        f"timeline={timeline['total_duration_sec']:.3f}s "
        f"{width}x{height}@{fps}"
    )
    print(
        f"frame_snapped_duration={rendered_duration:.3f}s "
        f"frames={previous_end_frame}"
    )
    print(
        "motion="
        f"crossfade {motion['scene_transition']['duration_sec']:.2f}s, "
        f"content fade {motion['content_overlay']['fade_in_sec']:.2f}s, "
        f"explanation "
        f"{motion['explanation_overlay']['fade_in_sec']:.2f}/"
        f"{motion['explanation_overlay']['fade_out_sec']:.2f}s, "
        f"background-run crop "
        f"{motion['background_motion']['crop_window_a_pct']:.0f}%"
        "<->"
        f"{motion['background_motion']['crop_window_b_pct']:.0f}% "
        f"runs={len(background_runs)}"
    )

    # Always validate overlay state and all referenced visual assets.
    validate_state = OverlayState(
        poem_id=pid,
        text_config=text_config,
        text_manifest=text_manifest,
    )

    for item in planned:
        static_layer, fade_layer, _fade_kind = (
            validate_state.build_layers(
                item["event"],
                width=width,
                height=height,
            )
        )
        static_layer.close()
        if fade_layer is not None:
            fade_layer.close()

        require_file(
            repo_path(item["background_image"]),
            label="background",
        )
        audio_file = item["event"].get("audio_file")
        if audio_file:
            require_file(
                repo_path(str(audio_file)),
                label="audio",
            )

    if args.dry_run:
        print(
            "PASS dry_run=true; "
            "motion/overlay state validated, FFmpeg not invoked."
        )
        return 0

    ffmpeg = ffmpeg_path(args.ffmpeg)
    print(f"ffmpeg={ffmpeg}")

    work_parent = poem_dir / "video"
    work_parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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

    background_run_files: dict[int, Path] = {}

    print(
        f"background_runs={len(background_runs)} "
        "(render once per consecutive background segment)"
    )

    for run in background_runs:
        run_no = int(run["run_no"])
        run_file = (
            work_dir
            / f"background_run_{run_no:03d}.mp4"
        )
        print(
            f"  run {run_no:02d}: "
            f"{run['crop_start_pct']:.0f}%"
            "->"
            f"{run['crop_end_pct']:.0f}% "
            f"frames={run['total_frames']} "
            f"bg={run['background_image']}"
        )
        render_background_run(
            ffmpeg=ffmpeg,
            background_image=repo_path(
                str(run["background_image"])
            ),
            output_path=run_file,
            width=width,
            height=height,
            fps=fps,
            frame_count=int(run["total_frames"]),
            crop_start_pct=float(run["crop_start_pct"]),
            crop_end_pct=float(run["crop_end_pct"]),
            verbose=args.verbose,
        )
        background_run_files[run_no] = run_file

    state = OverlayState(
        poem_id=pid,
        text_config=text_config,
        text_manifest=text_manifest,
    )

    try:
        segment_paths: list[Path] = []
        previous_run_no: int | None = None

        for index, item in enumerate(planned, start=1):
            event = item["event"]
            run_no = int(item["background_run_no"])
            current_background = repo_path(
                item["background_image"]
            )

            static_layer, fade_layer, fade_kind = (
                state.build_layers(
                    event,
                    width=width,
                    height=height,
                )
            )

            static_path = (
                work_dir
                / f"event_{index:03d}_static.png"
            )
            static_layer.save(
                static_path,
                "PNG",
                optimize=True,
            )
            static_layer.close()

            fade_path: Path | None = None
            if fade_layer is not None:
                fade_path = (
                    work_dir
                    / f"event_{index:03d}_fade.png"
                )
                fade_layer.save(
                    fade_path,
                    "PNG",
                    optimize=True,
                )
                fade_layer.close()

            segment_path = (
                work_dir / f"segment_{index:03d}.mp4"
            )

            has_scene_change = (
                previous_run_no is not None
                and previous_run_no != run_no
            )

            effect_parts = []
            if has_scene_change:
                effect_parts.append("xfade")
            if fade_kind:
                effect_parts.append(
                    f"{fade_kind}-fade"
                )
            effect_parts.append(
                "run-zoom-in"
                if float(
                    item["background_run_crop_end_pct"]
                )
                < float(
                    item["background_run_crop_start_pct"]
                )
                else "run-zoom-out"
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
                + f" {item['duration_sec']:.3f}s "
                + ",".join(effect_parts)
            )

            previous_run_file: Path | None = None
            previous_run_frames: int | None = None

            if has_scene_change:
                previous_run_file = background_run_files[
                    int(previous_run_no)
                ]
                previous_run_frames = int(
                    background_runs[
                        int(previous_run_no) - 1
                    ]["total_frames"]
                )

            render_segment(
                ffmpeg=ffmpeg,
                background_run_file=background_run_files[
                    run_no
                ],
                background_start_frame=int(
                    item["background_run_start_frame"]
                ),
                background_end_frame=int(
                    item["background_run_end_frame"]
                ),
                previous_background_run_file=(
                    previous_run_file
                ),
                previous_background_run_frames=(
                    previous_run_frames
                ),
                static_overlay=static_path,
                fade_overlay=fade_path,
                fade_kind=fade_kind,
                segment_path=segment_path,
                event=event,
                duration_sec=float(
                    item["duration_sec"]
                ),
                frame_count=int(
                    item["frame_count"]
                ),
                fps=fps,
                motion=motion,
                verbose=args.verbose,
            )
            segment_paths.append(segment_path)
            previous_run_no = run_no

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
        "composer_mode=motion_v3 "
        "(run-level 100%/80% perspective zoom "
        "+ crossfade + content/explanation fades)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
