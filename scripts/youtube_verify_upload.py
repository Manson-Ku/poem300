#!/usr/bin/env python3
"""Verify whether a YouTube poem upload is complete and processable.

This is a read-only diagnostic. It compares the authenticated channel's
video resource with the local MP4 and reports upload/processing state.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from googleapiclient.discovery import build

from youtube_upload import (
    DEFAULT_CONFIG,
    DEFAULT_POEMS,
    DEFAULT_TOKEN,
    build_metadata,
    default_video_path,
    find_channel_video_matches,
    load_credentials,
    read_json,
    read_poem,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify one uploaded poem video against local MP4."
    )
    parser.add_argument("--poem-id", type=int, required=True)
    parser.add_argument("--style", default="B")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--poems", default=str(DEFAULT_POEMS))
    parser.add_argument("--token", default=str(DEFAULT_TOKEN))
    args = parser.parse_args()

    config_path = Path(args.config)
    poems_path = Path(args.poems)
    token_path = Path(args.token)

    config = read_json(config_path)
    poem = read_poem(poems_path, args.poem_id)
    local_path = default_video_path(
        poem_id=args.poem_id,
        style=args.style,
    )

    if not local_path.exists():
        raise FileNotFoundError(
            f"local video not found: {local_path.as_posix()}"
        )

    metadata = build_metadata(
        poem=poem,
        config=config,
        privacy="private",
    )
    expected_title = str(metadata["snippet"]["title"])
    tracking_url = str(metadata.get("_tracking_url", ""))

    scopes = [str(value) for value in config["scopes"]]
    credentials = load_credentials(token_path, scopes)
    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    matches = find_channel_video_matches(
        youtube=youtube,
        expected_title=expected_title,
        tracking_url=tracking_url,
    )

    if len(matches) != 1:
        print(f"matches={len(matches)}")
        if not matches:
            print("FAIL: no deterministic channel match")
        else:
            print("FAIL: multiple deterministic channel matches")
            for item in matches:
                print("  video_id=" + str(item.get("id", "")))
        return 1

    video_id = str(matches[0].get("id", ""))
    response = youtube.videos().list(
        part="id,snippet,status,processingDetails,fileDetails,suggestions",
        id=video_id,
        maxResults=1,
    ).execute()

    items = response.get("items", [])
    if not items:
        print("FAIL: matched video disappeared")
        return 1

    item = items[0]
    status = item.get("status", {})
    processing = item.get("processingDetails", {})
    file_details = item.get("fileDetails", {})
    suggestions = item.get("suggestions", {})

    local_size = local_path.stat().st_size
    remote_size_raw = file_details.get("fileSize")
    remote_size = (
        int(remote_size_raw)
        if remote_size_raw not in (None, "")
        else None
    )

    upload_status = str(status.get("uploadStatus", ""))
    processing_status = str(processing.get("processingStatus", ""))
    failure_reason = str(status.get("failureReason", ""))
    rejection_reason = str(status.get("rejectionReason", ""))
    processing_failure = str(
        processing.get("processingFailureReason", "")
    )
    file_availability = str(
        processing.get("fileDetailsAvailability", "")
    )
    processing_errors = suggestions.get("processingErrors", []) or []

    print(f"video_id={video_id}")
    print(f"title={item.get('snippet', {}).get('title', '')}")
    print(f"local_file={local_path.as_posix()}")
    print(f"local_size={local_size}")
    print(f"upload_status={upload_status}")
    print(f"processing_status={processing_status}")
    print(f"file_details_availability={file_availability}")
    print(
        "remote_size="
        + (str(remote_size) if remote_size is not None else "unavailable")
    )
    print(
        "size_match="
        + (
            str(remote_size == local_size).lower()
            if remote_size is not None
            else "unknown"
        )
    )
    if failure_reason:
        print(f"failure_reason={failure_reason}")
    if rejection_reason:
        print(f"rejection_reason={rejection_reason}")
    if processing_failure:
        print(f"processing_failure_reason={processing_failure}")
    if processing_errors:
        print(
            "processing_errors="
            + ",".join(str(value) for value in processing_errors)
        )

    hard_fail = (
        upload_status in {"failed", "rejected", "deleted"}
        or processing_status == "failed"
        or bool(processing_errors)
        or (
            remote_size is not None
            and remote_size != local_size
        )
    )
    if hard_fail:
        print("FAIL: remote upload is not safe for publication")
        return 1

    if remote_size is not None and remote_size == local_size:
        if processing_status == "succeeded":
            print("PASS: upload complete and YouTube processing succeeded")
        else:
            print(
                "PASS_UPLOAD: remote file size matches local; "
                "YouTube processing is still pending/in progress"
            )
        return 0

    if upload_status in {"uploaded", "processed"}:
        print(
            "PASS_UPLOAD_STATUS: YouTube reports the file uploaded; "
            "remote file size is not available yet"
        )
        return 0

    print(
        "WAIT: video resource exists but upload completeness is "
        "not yet provable from current API fields"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
