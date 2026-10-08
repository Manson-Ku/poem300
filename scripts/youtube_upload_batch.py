#!/usr/bin/env python3
"""Upload rendered poem videos that are not yet present on the channel.

The YouTube channel is the runtime SSOT for "already uploaded" state.
This orchestrator inventories the authenticated channel first, compares
deterministic poem metadata, and calls youtube_upload.py only for poems
that have a ready local MP4 and no matching channel video.

Default behavior:
- approved poems only
- one recommended_age group at a time
- exact generated title OR canonical tracking URL counts as uploaded
- existing videos are never re-uploaded
- existing matches are passed through idempotent post-upload repair so
  age-playlist routing is complete without uploading the MP4 again
- missing local MP4s are reported and skipped
- upload failures stop the batch by default
- each actual upload delegates to youtube_upload.py, which owns public
  status, made-for-kids, playlist routing, thumbnail policy, and retries
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import unicodedata
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from youtube_upload import (
    DEFAULT_CONFIG,
    DEFAULT_POEMS,
    DEFAULT_TOKEN,
    build_metadata,
    default_video_path,
    load_credentials,
    read_json,
)


UPLOAD_SCRIPT = Path("scripts/youtube_upload.py")
DEFAULT_SCHEDULE_DIR = Path("output/youtube_schedules")


def parse_hhmm(value: str) -> dt_time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"invalid schedule time {value!r}; expected HH:MM") from exc


def deterministic_minute(
    *,
    seed: str,
    age: int,
    style: str,
    poem_id: int,
    target_date: date,
    slot_name: str,
    start: dt_time,
    end: dt_time,
) -> int:
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    span = end_minutes - start_minutes
    if span <= 0:
        raise ValueError(
            f"schedule window {slot_name!r} must end after it starts"
        )
    material = (
        f"{seed}|age={age}|style={style.upper()}|poem={poem_id}|"
        f"date={target_date.isoformat()}|slot={slot_name}"
    ).encode("utf-8")
    value = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    return start_minutes + (value % span)


def schedule_plan_path(
    *,
    config: dict[str, Any],
    age: int,
    style: str,
) -> Path:
    schedule = config.get("schedule", {})
    root = Path(
        str(schedule.get("plan_dir", DEFAULT_SCHEDULE_DIR.as_posix()))
    )
    return root / f"age{age}_{style.upper()}.json"


def read_schedule_plan(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_schedule_plan(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_schedule_plan(
    *,
    ready: list[tuple[dict[str, str], Path]],
    config: dict[str, Any],
    age: int,
    style: str,
    start_date: date,
) -> dict[str, Any]:
    schedule = config.get("schedule", {})
    timezone_name = str(schedule.get("timezone", "Asia/Taipei"))
    tz = ZoneInfo(timezone_name)
    seed = str(schedule.get("random_seed", "poem300-youtube-schedule-v1"))
    windows = schedule.get("windows") or [
        {"name": "morning", "start": "06:00", "end": "09:00"},
        {"name": "evening", "start": "17:00", "end": "20:00"},
    ]
    if len(windows) != 2:
        raise ValueError("youtube schedule requires exactly two daily windows")

    assignments: list[dict[str, Any]] = []
    for index, (poem, _video_path) in enumerate(ready):
        window = windows[index % len(windows)]
        day_offset = index // len(windows)
        target_date = start_date + timedelta(days=day_offset)
        start = parse_hhmm(str(window["start"]))
        end = parse_hhmm(str(window["end"]))
        minute_of_day = deterministic_minute(
            seed=seed,
            age=age,
            style=style,
            poem_id=int(poem["poem_id"]),
            target_date=target_date,
            slot_name=str(window["name"]),
            start=start,
            end=end,
        )
        local_dt = datetime.combine(
            target_date,
            dt_time(hour=minute_of_day // 60, minute=minute_of_day % 60),
            tzinfo=tz,
        )
        utc_dt = local_dt.astimezone(timezone.utc)
        assignments.append(
            {
                "poem_id": int(poem["poem_id"]),
                "title": poem["title"],
                "slot_index": index,
                "window": str(window["name"]),
                "publish_at_local": local_dt.isoformat(),
                "publish_at_utc": utc_dt.isoformat().replace("+00:00", "Z"),
            }
        )

    return {
        "version": "youtube_schedule_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "age": age,
        "style": style.upper(),
        "timezone": timezone_name,
        "start_date": start_date.isoformat(),
        "windows": windows,
        "random_seed": seed,
        "assignments": assignments,
    }


def extend_schedule_plan(
    *,
    plan: dict[str, Any],
    ready: list[tuple[dict[str, str], Path]],
    age: int,
    style: str,
) -> int:
    assignments = plan.setdefault("assignments", [])
    existing_ids = {
        int(item["poem_id"])
        for item in assignments
    }
    missing = [
        poem
        for poem, _video_path in ready
        if int(poem["poem_id"]) not in existing_ids
    ]
    if not missing:
        return 0

    windows = plan.get("windows") or []
    if not windows:
        raise ValueError("existing schedule plan has no windows")

    timezone_name = str(
        plan.get("timezone", "Asia/Taipei")
    )
    tz = ZoneInfo(timezone_name)
    seed = str(
        plan.get(
            "random_seed",
            "poem300-youtube-schedule-v1",
        )
    )
    start_date = date.fromisoformat(
        str(plan["start_date"])
    )
    next_index = (
        max(
            (
                int(item.get("slot_index", index))
                for index, item in enumerate(assignments)
            ),
            default=-1,
        )
        + 1
    )

    for offset, poem in enumerate(missing):
        index = next_index + offset
        window = windows[index % len(windows)]
        target_date = start_date + timedelta(
            days=index // len(windows)
        )
        start = parse_hhmm(str(window["start"]))
        end = parse_hhmm(str(window["end"]))
        pid = int(poem["poem_id"])
        minute_of_day = deterministic_minute(
            seed=seed,
            age=age,
            style=style,
            poem_id=pid,
            target_date=target_date,
            slot_name=str(window["name"]),
            start=start,
            end=end,
        )
        local_dt = datetime.combine(
            target_date,
            dt_time(
                hour=minute_of_day // 60,
                minute=minute_of_day % 60,
            ),
            tzinfo=tz,
        )
        utc_dt = local_dt.astimezone(timezone.utc)
        assignments.append(
            {
                "poem_id": pid,
                "title": poem["title"],
                "slot_index": index,
                "window": str(window["name"]),
                "publish_at_local": local_dt.isoformat(),
                "publish_at_utc": (
                    utc_dt.isoformat().replace("+00:00", "Z")
                ),
            }
        )

    return len(missing)


def resolve_schedule_plan(
    *,
    ready: list[tuple[dict[str, str], Path]],
    config: dict[str, Any],
    age: int,
    style: str,
    schedule_start: str | None,
    persist: bool,
) -> tuple[dict[int, str], Path | None, dict[str, Any] | None]:
    schedule = config.get("schedule", {})
    enabled = bool(schedule.get("enabled", False))
    if not enabled:
        return {}, None, None

    plan_path = schedule_plan_path(
        config=config,
        age=age,
        style=style,
    )

    if plan_path.exists():
        plan = read_schedule_plan(plan_path)
        if int(plan.get("age", -1)) != age:
            raise ValueError(f"schedule plan age mismatch: {plan_path}")
        if str(plan.get("style", "")).upper() != style.upper():
            raise ValueError(f"schedule plan style mismatch: {plan_path}")
        if schedule_start and str(plan.get("start_date")) != schedule_start:
            raise ValueError(
                "existing schedule plan start_date differs from "
                f"--schedule-start: {plan_path}"
            )

        extended = extend_schedule_plan(
            plan=plan,
            ready=ready,
            age=age,
            style=style,
        )
        if extended:
            print(f"schedule_plan_extended={extended}")
            if persist:
                write_schedule_plan(plan_path, plan)
    else:
        timezone_name = str(schedule.get("timezone", "Asia/Taipei"))
        tz = ZoneInfo(timezone_name)
        if schedule_start:
            try:
                start_date = date.fromisoformat(schedule_start)
            except ValueError as exc:
                raise ValueError(
                    f"invalid --schedule-start date: {schedule_start!r}"
                ) from exc
        else:
            start_date = datetime.now(tz).date() + timedelta(days=1)

        plan = build_schedule_plan(
            ready=ready,
            config=config,
            age=age,
            style=style,
            start_date=start_date,
        )
        if persist:
            write_schedule_plan(plan_path, plan)

    mapping = {
        int(item["poem_id"]): str(item["publish_at_utc"])
        for item in plan.get("assignments", [])
    }

    missing = [
        int(poem["poem_id"])
        for poem, _video_path in ready
        if int(poem["poem_id"]) not in mapping
    ]
    if missing:
        raise ValueError(
            "existing schedule plan does not cover ready poems: "
            + ",".join(f"p{pid:03d}" for pid in missing)
        )

    return mapping, plan_path, plan


def read_poems(path: Path) -> list[dict[str, str]]:
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))


def select_poems(
    rows: list[dict[str, str]],
    *,
    age: int,
    include_unapproved: bool,
) -> list[dict[str, str]]:
    selected = [
        row
        for row in rows
        if int(row["recommended_age"]) == age
        and (
            include_unapproved
            or row.get(
                "visual_plan_status",
                "",
            ).strip().casefold()
            == "approved"
        )
    ]
    selected.sort(
        key=lambda row: int(row["poem_id"])
    )
    return selected


def normalize_text(value: str) -> str:
    return unicodedata.normalize(
        "NFC",
        value.strip(),
    )


def channel_upload_playlist_id(
    youtube: Any,
) -> str:
    response = youtube.channels().list(
        part="contentDetails",
        mine=True,
        maxResults=1,
    ).execute()

    items = response.get("items", [])
    if not items:
        raise RuntimeError(
            "No authenticated YouTube channel was returned."
        )

    playlist_id = str(
        items[0]
        .get(
            "contentDetails",
            {},
        )
        .get(
            "relatedPlaylists",
            {},
        )
        .get(
            "uploads",
            "",
        )
    ).strip()

    if not playlist_id:
        raise RuntimeError(
            "Authenticated channel returned no uploads playlist ID."
        )

    return playlist_id


def all_uploaded_video_ids(
    youtube: Any,
    uploads_playlist_id: str,
) -> list[str]:
    video_ids: list[str] = []
    page_token: str | None = None

    while True:
        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in response.get("items", []):
            video_id = str(
                item.get(
                    "contentDetails",
                    {},
                ).get(
                    "videoId",
                    "",
                )
            ).strip()
            if video_id:
                video_ids.append(video_id)

        page_token = response.get(
            "nextPageToken"
        )
        if not page_token:
            break

    return video_ids


def chunks(
    values: list[str],
    size: int,
) -> list[list[str]]:
    return [
        values[index:index + size]
        for index in range(
            0,
            len(values),
            size,
        )
    ]


def channel_inventory(
    youtube: Any,
) -> list[dict[str, str]]:
    uploads_playlist_id = (
        channel_upload_playlist_id(
            youtube
        )
    )
    video_ids = all_uploaded_video_ids(
        youtube,
        uploads_playlist_id,
    )

    records: list[dict[str, str]] = []

    for group in chunks(
        video_ids,
        50,
    ):
        response = youtube.videos().list(
            part="snippet,status",
            id=",".join(group),
            maxResults=50,
        ).execute()

        for item in response.get("items", []):
            snippet = item.get(
                "snippet",
                {},
            )
            status = item.get(
                "status",
                {},
            )

            records.append(
                {
                    "video_id": str(
                        item.get(
                            "id",
                            "",
                        )
                    ),
                    "title": str(
                        snippet.get(
                            "title",
                            "",
                        )
                    ),
                    "description": str(
                        snippet.get(
                            "description",
                            "",
                        )
                    ),
                    "privacy": str(
                        status.get(
                            "privacyStatus",
                            "",
                        )
                    ),
                }
            )

    return records


def find_channel_matches(
    *,
    inventory: list[dict[str, str]],
    expected_title: str,
    tracking_url: str,
) -> list[dict[str, str]]:
    title_key = normalize_text(
        expected_title
    )
    tracking_key = tracking_url.strip()

    matches: dict[str, dict[str, str]] = {}

    for item in inventory:
        title_match = (
            normalize_text(
                item["title"]
            )
            == title_key
        )
        tracking_match = (
            bool(tracking_key)
            and tracking_key
            in item["description"]
        )

        if title_match or tracking_match:
            matches[item["video_id"]] = item

    return list(matches.values())


def upload_command(
    *,
    poem_id: int,
    style: str,
    config: Path,
    poems: Path,
    token: Path,
    existing_video_id: str | None = None,
    publish_at: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(UPLOAD_SCRIPT),
        "--poem-id",
        str(poem_id),
        "--style",
        style,
        "--config",
        str(config),
        "--poems",
        str(poems),
        "--token",
        str(token),
    ]

    if existing_video_id:
        command.extend(
            [
                "--existing-video-id",
                existing_video_id,
            ]
        )

    if publish_at:
        command.extend(
            [
                "--publish-at",
                publish_at,
            ]
        )

    return command


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Upload local poem videos that are not yet on the "
            "authenticated YouTube channel."
        )
    )
    parser.add_argument(
        "--age",
        type=int,
        choices=(6, 7, 8, 9),
        required=True,
    )
    parser.add_argument(
        "--style",
        default="B",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
    )
    parser.add_argument(
        "--poems",
        default=str(DEFAULT_POEMS),
    )
    parser.add_argument(
        "--token",
        default=str(DEFAULT_TOKEN),
    )
    parser.add_argument(
        "--include-unapproved",
        action="store_true",
        help=(
            "Include poems whose visual_plan_status is not approved."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Inventory channel/local state and print the upload plan "
            "without uploading."
        ),
    )
    parser.add_argument(
        "--skip-existing-postcheck",
        action="store_true",
        help=(
            "Do not run idempotent playlist repair for videos already "
            "found on the channel."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Upload at most N currently-unuploaded ready videos. "
            "Useful for staged rollout tests."
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "Continue to later poems after one upload fails. "
            "Default is fail-fast."
        ),
    )
    parser.add_argument(
        "--schedule-start",
        help=(
            "First local publication date in YYYY-MM-DD. "
            "Default for a new schedule plan is tomorrow in the "
            "configured timezone."
        ),
    )
    parser.add_argument(
        "--no-schedule",
        action="store_true",
        help=(
            "Disable scheduled publication for this run even when "
            "config schedule.enabled=true."
        ),
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")

    config_path = Path(args.config)
    poems_path = Path(args.poems)
    token_path = Path(args.token)

    config = read_json(
        config_path
    )
    if config.get("version") != "youtube_v1":
        raise RuntimeError(
            "youtube_upload_batch.py requires youtube_v1"
        )

    scopes = [
        str(value)
        for value in config["scopes"]
    ]
    credentials = load_credentials(
        token_path,
        scopes,
    )
    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    poems = select_poems(
        read_poems(
            poems_path
        ),
        age=args.age,
        include_unapproved=args.include_unapproved,
    )

    if not poems:
        print(
            "ERROR: no poems matched age/status selection"
        )
        return 2

    print(
        f"channel={config.get('expected_handle', '')} "
        f"age={args.age} "
        f"style={args.style.upper()} "
        f"selected={len(poems)}"
    )
    print("channel_inventory_start=true")
    inventory = channel_inventory(
        youtube
    )
    print(
        f"channel_inventory_videos={len(inventory)}"
    )

    already_uploaded: list[
        tuple[dict[str, str], dict[str, str]]
    ] = []
    ready_to_upload: list[
        tuple[dict[str, str], Path]
    ] = []
    missing_local: list[
        tuple[dict[str, str], Path]
    ] = []
    duplicate_matches: list[
        tuple[dict[str, str], list[dict[str, str]]]
    ] = []

    for poem in poems:
        pid = int(poem["poem_id"])
        metadata = build_metadata(
            poem=poem,
            config=config,
            privacy=str(
                config["upload"].get(
                    "default_privacy",
                    "public",
                )
            ),
        )

        expected_title = str(
            metadata["snippet"]["title"]
        )
        tracking_url = str(
            metadata.get(
                "_tracking_url",
                "",
            )
        )

        matches = find_channel_matches(
            inventory=inventory,
            expected_title=expected_title,
            tracking_url=tracking_url,
        )

        if len(matches) > 1:
            duplicate_matches.append(
                (
                    poem,
                    matches,
                )
            )
            print(
                f"CONFLICT p{pid:03d} {poem['title']} "
                f"channel_matches={len(matches)}"
            )
            for match in matches:
                print(
                    "  "
                    f"{match['video_id']} "
                    f"privacy={match['privacy']} "
                    f"title={match['title']}"
                )
            continue

        if len(matches) == 1:
            match = matches[0]
            already_uploaded.append(
                (
                    poem,
                    match,
                )
            )
            print(
                f"SKIP_UPLOADED p{pid:03d} {poem['title']} "
                f"video_id={match['video_id']} "
                f"privacy={match['privacy']}"
            )
            continue

        video_path = default_video_path(
            poem_id=pid,
            style=args.style,
        )

        if not video_path.exists():
            missing_local.append(
                (
                    poem,
                    video_path,
                )
            )
            print(
                f"WAIT_LOCAL p{pid:03d} {poem['title']} "
                f"{video_path.as_posix()}"
            )
            continue

        ready_to_upload.append(
            (
                poem,
                video_path,
            )
        )
        print(
            f"READY p{pid:03d} {poem['title']} "
            f"{video_path.as_posix()}"
        )

    print("")
    print("=== PLAN ===")
    print(f"selected={len(poems)}")
    print(
        f"already_uploaded={len(already_uploaded)}"
    )
    print(
        f"ready_to_upload={len(ready_to_upload)}"
    )
    print(
        f"missing_local={len(missing_local)}"
    )
    print(
        f"duplicate_conflicts={len(duplicate_matches)}"
    )

    schedule_enabled = bool(
        config.get("schedule", {}).get("enabled", False)
    ) and not args.no_schedule
    schedule_map: dict[int, str] = {}
    schedule_path: Path | None = None
    schedule_plan: dict[str, Any] | None = None

    if schedule_enabled:
        schedule_map, schedule_path, schedule_plan = resolve_schedule_plan(
            ready=ready_to_upload,
            config=config,
            age=args.age,
            style=args.style,
            schedule_start=args.schedule_start,
            persist=not args.dry_run,
        )
        print("")
        print("=== SCHEDULE ===")
        print("schedule_enabled=true")
        print(
            "timezone="
            + str(schedule_plan.get("timezone", ""))
        )
        print(
            "schedule_start="
            + str(schedule_plan.get("start_date", ""))
        )
        print(
            f"schedule_plan={schedule_path.as_posix() if schedule_path else ''}"
        )
        print(
            f"scheduled_new_uploads={len(ready_to_upload)}"
        )
        for poem, _video_path in ready_to_upload:
            pid = int(poem["poem_id"])
            assignment = next(
                item
                for item in schedule_plan["assignments"]
                if int(item["poem_id"]) == pid
            )
            print(
                f"SCHEDULE p{pid:03d} {poem['title']} "
                f"window={assignment['window']} "
                f"local={assignment['publish_at_local']} "
                f"utc={assignment['publish_at_utc']}"
            )
    else:
        print("")
        print("schedule_enabled=false")

    if duplicate_matches:
        print(
            "STOP: duplicate channel matches require review. "
            "No uploads were started."
        )
        return 1

    if args.dry_run:
        print("DRY RUN: no videos uploaded.")
        return 0

    existing_postchecked: list[int] = []
    existing_post_failed: list[
        tuple[int, int]
    ] = []

    if (
        already_uploaded
        and not args.skip_existing_postcheck
    ):
        print("")
        print(
            "=== EXISTING VIDEO POST-UPLOAD CHECK "
            f"count={len(already_uploaded)} ==="
        )

        for index, (
            poem,
            match,
        ) in enumerate(
            already_uploaded,
            start=1,
        ):
            pid = int(poem["poem_id"])
            video_id = str(
                match["video_id"]
            )
            print("")
            print(
                f"[postcheck {index:02d}/{len(already_uploaded):02d}] "
                f"p{pid:03d} {poem['title']} "
                f"video_id={video_id}"
            )

            completed = subprocess.run(
                upload_command(
                    poem_id=pid,
                    style=args.style,
                    config=config_path,
                    poems=poems_path,
                    token=token_path,
                    existing_video_id=video_id,
                ),
                check=False,
            )

            if completed.returncode == 0:
                existing_postchecked.append(
                    pid
                )
                continue

            existing_post_failed.append(
                (
                    pid,
                    int(completed.returncode),
                )
            )
            print(
                f"POSTCHECK_FAIL p{pid:03d} "
                f"rc={completed.returncode}"
            )

            if not args.continue_on_error:
                print(
                    "STOP: fail-fast is enabled. "
                    "No new video upload was started."
                )
                return 1

    queue = ready_to_upload
    if args.limit is not None:
        queue = queue[
            :args.limit
        ]

    if not queue:
        print("PASS: nothing ready requires upload.")
        if existing_post_failed:
            return 1
        return 0

    print("")
    print(
        f"=== UPLOAD LOOP count={len(queue)} ==="
    )

    succeeded: list[int] = []
    failed: list[
        tuple[int, int]
    ] = []

    for index, (
        poem,
        video_path,
    ) in enumerate(
        queue,
        start=1,
    ):
        pid = int(poem["poem_id"])
        print("")
        print(
            f"[upload {index:02d}/{len(queue):02d}] "
            f"p{pid:03d} {poem['title']}"
        )
        print(
            f"file={video_path.as_posix()}"
        )

        publish_at = schedule_map.get(pid)
        if publish_at:
            normalized = publish_at.replace("Z", "+00:00")
            publish_dt = datetime.fromisoformat(normalized)
            if publish_dt.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                print(
                    f"UPLOAD_FAIL p{pid:03d} scheduled time is no longer "
                    f"in the future: {publish_at}"
                )
                failed.append((pid, 2))
                break
            print(f"publish_at={publish_at}")

        completed = subprocess.run(
            upload_command(
                poem_id=pid,
                style=args.style,
                config=config_path,
                poems=poems_path,
                token=token_path,
                publish_at=publish_at,
            ),
            check=False,
        )

        if completed.returncode == 0:
            succeeded.append(pid)
            continue

        failed.append(
            (
                pid,
                int(completed.returncode),
            )
        )
        print(
            f"UPLOAD_FAIL p{pid:03d} "
            f"rc={completed.returncode}"
        )

        if not args.continue_on_error:
            print(
                "STOP: fail-fast is enabled. "
                "Fix the issue and rerun the same batch command; "
                "the channel inventory will skip successful uploads."
            )
            break

    print("")
    print("=== SUMMARY ===")
    print(
        f"already_uploaded={len(already_uploaded)}"
    )
    print(
        f"existing_postchecked={len(existing_postchecked)}"
    )
    print(
        f"existing_post_failed={len(existing_post_failed)}"
    )
    print(
        f"uploaded_now={len(succeeded)}"
    )
    print(
        f"failed={len(failed)}"
    )
    print(
        f"waiting_local={len(missing_local)}"
    )

    if existing_post_failed:
        for pid, rc in existing_post_failed:
            print(
                f"  POSTCHECK_FAIL p{pid:03d} rc={rc}"
            )

    if failed:
        for pid, rc in failed:
            print(
                f"  FAIL p{pid:03d} rc={rc}"
            )

    if existing_post_failed or failed:
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
