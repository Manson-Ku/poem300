#!/usr/bin/env python3
"""Upload a rendered poem video to YouTube using poem300 metadata rules.

Default behavior is intentionally safe for the first production test:
- privacyStatus=private
- notifySubscribers=False
- selfDeclaredMadeForKids=True

Metadata is generated from data/poems.csv and config/youtube_v1.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import random
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


DEFAULT_CONFIG = Path("config/youtube_v1.json")
DEFAULT_POEMS = Path("data/poems.csv")
DEFAULT_TOKEN = Path("credentials/youtube_token.json")

RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
MAX_RETRIES = 10


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_poem(
    path: Path,
    poem_id: int,
) -> dict[str, str]:
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        for row in csv.DictReader(handle):
            if int(row["poem_id"]) == poem_id:
                return row

    raise ValueError(
        f"poem_id={poem_id} not found in {path.as_posix()}"
    )


def load_credentials(
    token_path: Path,
    scopes: list[str],
) -> Credentials:
    if not token_path.exists():
        raise FileNotFoundError(
            f"YouTube OAuth token not found: {token_path.as_posix()}\n"
            "Run: py scripts\\youtube_auth.py"
        )

    credentials = Credentials.from_authorized_user_file(
        str(token_path),
        scopes,
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_path.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

    if not credentials.valid:
        raise RuntimeError(
            "Saved YouTube OAuth credentials are not valid. "
            "Run: py scripts\\youtube_auth.py --force-reauth"
        )

    return credentials


def build_metadata(
    *,
    poem: dict[str, str],
    config: dict[str, Any],
    privacy: str,
) -> dict[str, Any]:
    title = poem["title"].strip()
    author = poem["author"].strip()
    poem_type = poem["poem_type"].strip()
    content = poem["content"].strip()

    template = str(
        config["upload"]["title_template"]
    )
    video_title = template.format(
        title=title,
        author=author,
        poem_type=poem_type,
    )

    campaign = title
    tracking_url = (
        str(config["upload"]["website_url"])
        + "?"
        + urlencode(
            {
                "utm_source": str(
                    config["upload"]["utm_source"]
                ),
                "utm_medium": str(
                    config["upload"]["utm_medium"]
                ),
                "utm_campaign": campaign,
            }
        )
    )

    description_header = str(
        config["upload"].get(
            "description_header_template",
            "{title}-{author}-{poem_type}",
        )
    ).format(
        title=title,
        author=author,
        poem_type=poem_type,
    )

    description = (
        f"{description_header}\n"
        f"{content}\n\n"
        f"{tracking_url}"
    )

    if not video_title:
        raise ValueError("YouTube title cannot be empty")
    if len(video_title) > 100:
        raise ValueError(
            f"YouTube title is too long ({len(video_title)} > 100): "
            f"{video_title}"
        )
    if len(description) > 5000:
        raise ValueError(
            "YouTube description exceeds 5000 characters"
        )

    tags = [
        str(value)
        for value in config["upload"]["tags"]
        if str(value).strip()
    ]

    snippet: dict[str, Any] = {
        "title": video_title,
        "description": description,
        "tags": tags,
        "defaultLanguage": str(
            config["upload"].get(
                "default_language",
                "zh-TW",
            )
        ),
    }

    category_id = str(
        config["upload"].get(
            "category_id",
            "",
        )
    ).strip()
    if category_id:
        snippet["categoryId"] = category_id

    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": bool(
            config["upload"].get(
                "self_declared_made_for_kids",
                True,
            )
        ),
    }

    return {
        "snippet": snippet,
        "status": status,
        "_tracking_url": tracking_url,
    }


def default_video_path(
    *,
    poem_id: int,
    style: str,
) -> Path:
    return (
        Path("assets")
        / f"p{poem_id:03d}"
        / "video"
        / f"p{poem_id:03d}_{style.upper()}_1080p.mp4"
    )


def print_preview(
    *,
    poem_id: int,
    video_path: Path,
    body: dict[str, Any],
    notify_subscribers: bool,
) -> None:
    print(f"poem_id={poem_id}")
    print(f"video={video_path.as_posix()}")
    print(f"title={body['snippet']['title']}")
    print("description:")
    print(body["snippet"]["description"])
    print(
        "tags="
        + "，".join(body["snippet"].get("tags", []))
    )
    print(
        "privacy="
        + str(body["status"]["privacyStatus"])
    )
    print(
        "made_for_kids="
        + str(
            body["status"]["selfDeclaredMadeForKids"]
        ).lower()
    )
    print(
        "notify_subscribers="
        + str(notify_subscribers).lower()
    )


def resumable_upload(
    request: Any,
) -> dict[str, Any]:
    response = None
    retry = 0

    while response is None:
        try:
            status, response = request.next_chunk()

            if status is not None:
                pct = int(status.progress() * 100)
                print(
                    f"upload_progress={pct}% "
                    f"bytes={status.resumable_progress}"
                )

        except HttpError as exc:
            if exc.resp.status not in RETRIABLE_STATUS_CODES:
                raise

            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError(
                    "YouTube upload exceeded retry limit"
                ) from exc

            delay = random.random() * (2 ** retry)
            print(
                f"retriable_http_error={exc.resp.status} "
                f"retry={retry} sleep={delay:.1f}s"
            )
            time.sleep(delay)

        except (
            httplib2.HttpLib2Error,
            IOError,
            socket.timeout,
            ConnectionError,
        ) as exc:
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError(
                    "YouTube upload exceeded retry limit"
                ) from exc

            delay = random.random() * (2 ** retry)
            print(
                f"retriable_transport_error={type(exc).__name__} "
                f"retry={retry} sleep={delay:.1f}s"
            )
            time.sleep(delay)

    return response


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Upload one rendered poem video to YouTube."
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
    )
    parser.add_argument(
        "--file",
        help=(
            "Override MP4 path. Default: "
            "assets/pXXX/video/pXXX_<style>_1080p.mp4"
        ),
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
        "--privacy",
        choices=("private", "unlisted", "public"),
        help=(
            "Override privacy status. Default comes from config and is "
            "private for the initial test workflow."
        ),
    )
    parser.add_argument(
        "--notify-subscribers",
        action="store_true",
        help=(
            "Notify subscribers. Off by default for test/batch uploads."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print resolved video metadata without calling YouTube."
        ),
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    poems_path = Path(args.poems)
    token_path = Path(args.token)

    config = read_json(config_path)
    if config.get("version") != "youtube_v1":
        raise RuntimeError(
            "youtube_upload.py currently requires youtube_v1"
        )

    poem = read_poem(
        poems_path,
        args.poem_id,
    )

    privacy = (
        args.privacy
        if args.privacy
        else str(
            config["upload"].get(
                "default_privacy",
                "private",
            )
        )
    )

    video_path = (
        Path(args.file)
        if args.file
        else default_video_path(
            poem_id=args.poem_id,
            style=args.style,
        )
    )

    body = build_metadata(
        poem=poem,
        config=config,
        privacy=privacy,
    )

    print_preview(
        poem_id=args.poem_id,
        video_path=video_path,
        body=body,
        notify_subscribers=args.notify_subscribers,
    )

    if args.dry_run:
        print("DRY RUN: no YouTube API request was made.")
        return 0

    if not video_path.exists():
        raise FileNotFoundError(
            f"video not found: {video_path.as_posix()}"
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

    mime_type = (
        mimetypes.guess_type(
            video_path.name
        )[0]
        or "video/mp4"
    )

    media = MediaFileUpload(
        str(video_path),
        mimetype=mime_type,
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )

    api_body = {
        "snippet": body["snippet"],
        "status": body["status"],
    }

    request = youtube.videos().insert(
        part="snippet,status",
        body=api_body,
        media_body=media,
        notifySubscribers=args.notify_subscribers,
    )

    print("upload_start=true")
    response = resumable_upload(request)

    video_id = str(response.get("id", ""))
    if not video_id:
        raise RuntimeError(
            "YouTube upload returned no video ID"
        )

    print("YouTube upload PASS")
    print(f"video_id={video_id}")
    print(
        f"url=https://www.youtube.com/watch?v={video_id}"
    )
    print(
        "privacy="
        + str(
            response.get(
                "status",
                {},
            ).get(
                "privacyStatus",
                privacy,
            )
        )
    )
    print("made_for_kids=true")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
