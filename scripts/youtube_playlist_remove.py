#!/usr/bin/env python3
"""Remove one specific video from one owned YouTube playlist.

This repair is idempotent and does not delete or modify the video itself.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from googleapiclient.discovery import build

from youtube_upload import (
    DEFAULT_CONFIG,
    DEFAULT_TOKEN,
    find_owned_playlist,
    load_credentials,
    read_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove one video from one owned YouTube playlist."
    )
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--playlist-title", required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--token", default=str(DEFAULT_TOKEN))
    args = parser.parse_args()

    config_path = Path(args.config)
    token_path = Path(args.token)
    config = read_json(config_path)

    scopes = [str(value) for value in config["scopes"]]
    credentials = load_credentials(token_path, scopes)
    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    playlist = find_owned_playlist(
        youtube=youtube,
        title=args.playlist_title,
    )
    if playlist is None:
        print(
            "PASS: playlist not found; nothing to remove "
            f"title={args.playlist_title}"
        )
        return 0

    playlist_id = str(playlist.get("id", ""))
    page_token: str | None = None
    item_ids: list[str] = []

    while True:
        response = youtube.playlistItems().list(
            part="id,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in response.get("items", []):
            video_id = str(
                item.get("contentDetails", {}).get("videoId", "")
            )
            if video_id == args.video_id:
                item_id = str(item.get("id", ""))
                if item_id:
                    item_ids.append(item_id)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if not item_ids:
        print(
            "PASS: video is not in playlist; nothing to remove "
            f"video_id={args.video_id} "
            f"playlist={args.playlist_title}"
        )
        return 0

    for item_id in item_ids:
        youtube.playlistItems().delete(
            id=item_id,
        ).execute()
        print(f"removed_playlist_item={item_id}")

    print(
        "PASS "
        f"video_id={args.video_id} "
        f"playlist={args.playlist_title} "
        f"removed={len(item_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
