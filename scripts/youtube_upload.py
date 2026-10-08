#!/usr/bin/env python3
"""Upload a rendered poem video to YouTube using poem300 metadata rules.

Default production behavior:
- normal one-off upload privacy comes from config
- scheduled upload uses privacyStatus=private + status.publishAt
- notifySubscribers=False
- selfDeclaredMadeForKids=True
- add the uploaded video to the recommended_age playlist
- leave thumbnail selection to YouTube
- support idempotent post-upload repair without re-uploading the MP4

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
from datetime import datetime, timezone
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
PLAYLIST_POST_MAX_RETRIES = 6
AMBIGUOUS_UPLOAD_RECONCILE_DELAYS = (0, 2, 4, 8, 16, 30)


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

    token_info = read_json(token_path)
    stored_scopes_raw = token_info.get("scopes", [])
    if isinstance(stored_scopes_raw, str):
        stored_scopes = set(
            stored_scopes_raw.split()
        )
    else:
        stored_scopes = {
            str(value)
            for value in stored_scopes_raw
        }

    missing_scopes = [
        scope
        for scope in scopes
        if scope not in stored_scopes
    ]
    if missing_scopes:
        raise RuntimeError(
            "Saved YouTube OAuth token is missing newly required scopes:\n"
            + "\n".join(
                f"  - {scope}"
                for scope in missing_scopes
            )
            + "\nRun: py scripts\\youtube_auth.py --force-reauth"
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
    publish_at: str | None = None,
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

    if publish_at:
        if privacy != "private":
            raise ValueError(
                "YouTube scheduled publishing requires privacyStatus=private"
            )
        status["publishAt"] = publish_at

    return {
        "snippet": snippet,
        "status": status,
        "_tracking_url": tracking_url,
    }


def youtube_error_reason(exc: HttpError) -> str:
    try:
        payload = json.loads(
            exc.content.decode("utf-8")
            if isinstance(exc.content, bytes)
            else str(exc.content)
        )
        errors = payload.get(
            "error",
            {},
        ).get(
            "errors",
            [],
        )
        if errors:
            return str(
                errors[0].get(
                    "reason",
                    "",
                )
            )
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        AttributeError,
        TypeError,
    ):
        pass

    return ""


def recommended_age_playlist_title(
    *,
    poem: dict[str, str],
    config: dict[str, Any],
) -> str:
    raw = poem.get(
        "recommended_age",
        "",
    ).strip()
    if not raw:
        raise ValueError(
            "recommended_age is required for YouTube playlist routing"
        )

    try:
        age_number = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"invalid recommended_age for playlist routing: {raw!r}"
        ) from exc

    if not age_number.is_integer():
        raise ValueError(
            f"recommended_age must be a whole number; got {raw!r}"
        )

    age = int(age_number)
    return str(
        config["playlist"]["title_template"]
    ).format(
        recommended_age=age,
    )


def find_owned_playlist(
    *,
    youtube: Any,
    title: str,
) -> dict[str, Any] | None:
    page_token: str | None = None

    while True:
        response = youtube.playlists().list(
            part="id,snippet,status",
            mine=True,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in response.get("items", []):
            if (
                str(
                    item.get(
                        "snippet",
                        {},
                    ).get(
                        "title",
                        "",
                    )
                ).strip()
                == title
            ):
                return item

        page_token = response.get(
            "nextPageToken"
        )
        if not page_token:
            return None


def ensure_playlist(
    *,
    youtube: Any,
    title: str,
    config: dict[str, Any],
) -> tuple[str, bool]:
    existing = find_owned_playlist(
        youtube=youtube,
        title=title,
    )
    if existing is not None:
        playlist_id = str(
            existing.get("id", "")
        )
        if not playlist_id:
            raise RuntimeError(
                f"playlist {title!r} returned without an ID"
            )
        return playlist_id, False

    if not bool(
        config["playlist"].get(
            "create_if_missing",
            True,
        )
    ):
        raise RuntimeError(
            f"required playlist not found: {title}"
        )

    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
            },
            "status": {
                "privacyStatus": str(
                    config["playlist"].get(
                        "default_privacy",
                        "public",
                    )
                )
            },
        },
    ).execute()

    playlist_id = str(
        response.get("id", "")
    )
    if not playlist_id:
        raise RuntimeError(
            f"playlist creation returned no ID: {title}"
        )

    return playlist_id, True


def add_video_to_playlist(
    *,
    youtube: Any,
    playlist_id: str,
    video_id: str,
) -> bool:
    """Insert one video, tolerating YouTube propagation delay.

    Newly created playlists and newly uploaded public videos can briefly
    return 404 playlistNotFound/videoNotFound to playlistItems calls.
    Avoid a pre-insert list request and retry the write instead.

    If YouTube reports videoAlreadyInPlaylist, treat the operation as
    already complete so repair commands are idempotent.
    """

    for attempt in range(
        1,
        PLAYLIST_POST_MAX_RETRIES + 1,
    ):
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    }
                },
            ).execute()
            return True

        except HttpError as exc:
            reason = youtube_error_reason(exc)

            if reason == "videoAlreadyInPlaylist":
                return False

            retriable = (
                exc.resp.status == 404
                and reason
                in {
                    "playlistNotFound",
                    "videoNotFound",
                }
            )

            if (
                not retriable
                or attempt >= PLAYLIST_POST_MAX_RETRIES
            ):
                raise

            delay = min(
                2 ** (attempt - 1),
                16,
            )
            print(
                "playlist_post_retry="
                f"{attempt}/"
                f"{PLAYLIST_POST_MAX_RETRIES} "
                f"reason={reason} "
                f"sleep={delay}s"
            )
            time.sleep(delay)

    return False


def channel_upload_playlist_id(
    *,
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
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads", "")
    ).strip()
    if not playlist_id:
        raise RuntimeError(
            "Authenticated channel returned no uploads playlist ID."
        )
    return playlist_id


def find_channel_video_matches(
    *,
    youtube: Any,
    expected_title: str,
    tracking_url: str,
) -> list[dict[str, Any]]:
    """Find exact poem uploads without using the high-cost search endpoint."""

    uploads_playlist_id = channel_upload_playlist_id(
        youtube=youtube,
    )
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
                item.get("contentDetails", {}).get("videoId", "")
            ).strip()
            if video_id:
                video_ids.append(video_id)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    matches: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(video_ids), 50):
        group = video_ids[offset:offset + 50]
        response = youtube.videos().list(
            part="id,snippet,status",
            id=",".join(group),
            maxResults=50,
        ).execute()

        for item in response.get("items", []):
            video_id = str(item.get("id", "")).strip()
            snippet = item.get("snippet", {})
            title = str(snippet.get("title", "")).strip()
            description = str(snippet.get("description", ""))

            if (
                title == expected_title
                or (
                    bool(tracking_url)
                    and tracking_url in description
                )
            ):
                matches[video_id] = item

    return list(matches.values())


def reconcile_ambiguous_upload_completion(
    *,
    youtube: Any,
    expected_title: str,
    tracking_url: str,
) -> dict[str, Any] | None:
    """Recover when the resumable session dies after YouTube committed video.

    A terminal 404/410 is never treated as success by itself. We only recover
    when the authenticated channel contains exactly one deterministic match.
    """

    for attempt, delay in enumerate(
        AMBIGUOUS_UPLOAD_RECONCILE_DELAYS,
        start=1,
    ):
        if delay:
            print(
                "upload_completion_reconcile_wait="
                f"{delay}s attempt={attempt}"
            )
            time.sleep(delay)

        matches = find_channel_video_matches(
            youtube=youtube,
            expected_title=expected_title,
            tracking_url=tracking_url,
        )

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            video_ids = ",".join(
                str(item.get("id", ""))
                for item in matches
            )
            raise RuntimeError(
                "ambiguous upload completion produced multiple "
                f"channel matches: {video_ids}"
            )

        print(
            "upload_completion_reconcile_match=0 "
            f"attempt={attempt}/"
            f"{len(AMBIGUOUS_UPLOAD_RECONCILE_DELAYS)}"
        )

    return None


def verify_existing_video(
    *,
    youtube: Any,
    video_id: str,
) -> dict[str, Any]:
    response = youtube.videos().list(
        part="id,snippet,status",
        id=video_id,
        maxResults=1,
    ).execute()

    items = response.get("items", [])
    if not items:
        raise RuntimeError(
            f"YouTube video not found: {video_id}"
        )

    return items[0]


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
    playlist_title: str,
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
    publish_at = body["status"].get("publishAt")
    if publish_at:
        print("scheduled=true")
        print("publish_at=" + str(publish_at))
    else:
        print("scheduled=false")
    print(
        "playlist="
        + playlist_title
    )
    print("thumbnail=youtube_auto")


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
        "--existing-video-id",
        help=(
            "Skip video upload and only repair/complete post-upload "
            "steps for an existing YouTube video ID."
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
            "public."
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
        "--publish-at",
        help=(
            "Schedule public release at this future ISO 8601 timestamp. "
            "When set, upload privacy is forced to private as required "
            "by YouTube status.publishAt."
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

    publish_at = (args.publish_at or "").strip() or None
    if publish_at:
        normalized = publish_at.replace("Z", "+00:00")
        try:
            publish_dt = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                f"invalid --publish-at ISO 8601 timestamp: {publish_at!r}"
            ) from exc
        if publish_dt.tzinfo is None:
            raise ValueError("--publish-at must include a timezone offset or Z")
        if publish_dt.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("--publish-at must be in the future")
        privacy = "private"
    else:
        privacy = (
            args.privacy
            if args.privacy
            else str(
                config["upload"].get(
                    "default_privacy",
                    "public",
                )
            )
        )

    if args.existing_video_id and publish_at:
        raise ValueError(
            "--publish-at is only supported for a new upload; "
            "existing-video repair does not change publication timing"
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
        publish_at=publish_at,
    )
    playlist_title = recommended_age_playlist_title(
        poem=poem,
        config=config,
    )

    print_preview(
        poem_id=args.poem_id,
        video_path=video_path,
        body=body,
        notify_subscribers=args.notify_subscribers,
        playlist_title=playlist_title,
    )

    if args.dry_run:
        print("DRY RUN: no YouTube API request was made.")
        return 0

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

    if args.existing_video_id:
        video_id = args.existing_video_id.strip()
        existing = verify_existing_video(
            youtube=youtube,
            video_id=video_id,
        )
        response = existing
        print(
            "existing_video_mode=true "
            f"video_id={video_id}"
        )
        print(
            "existing_video_title="
            + str(
                existing.get(
                    "snippet",
                    {},
                ).get(
                    "title",
                    "",
                )
            )
        )
    else:
        if not video_path.exists():
            raise FileNotFoundError(
                f"video not found: {video_path.as_posix()}"
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
        try:
            response = resumable_upload(request)
        except HttpError as exc:
            if exc.resp.status not in {404, 410}:
                raise

            print(
                "upload_completion_ambiguous=true "
                f"http_status={exc.resp.status}"
            )
            print(
                "upload_completion_reconcile_start=true"
            )

            response = reconcile_ambiguous_upload_completion(
                youtube=youtube,
                expected_title=str(body["snippet"]["title"]),
                tracking_url=str(
                    body.get("_tracking_url", "")
                ),
            )
            if response is None:
                print(
                    "upload_completion_reconcile_result=not_found"
                )
                raise

            print(
                "upload_completion_reconcile_result=recovered"
            )
            print(
                "upload_completion_http_status="
                f"{exc.resp.status}"
            )

        video_id = str(response.get("id", ""))
        if not video_id:
            raise RuntimeError(
                "YouTube upload returned no video ID"
            )

    requested_privacy = str(body["status"]["privacyStatus"])
    actual_privacy = str(
        response.get(
            "status",
            {},
        ).get(
            "privacyStatus",
            requested_privacy,
        )
    )

    print(
        "YouTube post-upload PASS"
        if args.existing_video_id
        else "YouTube upload PASS"
    )
    print(f"video_id={video_id}")
    print(
        f"url=https://www.youtube.com/watch?v={video_id}"
    )
    print(
        "privacy="
        + actual_privacy
    )
    print("made_for_kids=true")
    if publish_at:
        actual_publish_at = str(
            response.get("status", {}).get("publishAt", publish_at)
        )
        print("scheduled=true")
        print("publish_at=" + actual_publish_at)

    if (
        not args.existing_video_id
        and actual_privacy != requested_privacy
    ):
        print(
            "WARNING: YouTube returned privacy="
            f"{actual_privacy} although requested="
            f"{requested_privacy}. This can occur when the API "
            "project is subject to YouTube upload visibility restrictions."
        )

    playlist_enabled = bool(
        config.get(
            "playlist",
            {},
        ).get(
            "enabled",
            True,
        )
    )

    if playlist_enabled:
        playlist_id, playlist_created = (
            ensure_playlist(
                youtube=youtube,
                title=playlist_title,
                config=config,
            )
        )

        playlist_item_added = False
        if bool(
            config["playlist"].get(
                "add_uploaded_video",
                True,
            )
        ):
            playlist_item_added = (
                add_video_to_playlist(
                    youtube=youtube,
                    playlist_id=playlist_id,
                    video_id=video_id,
                )
            )

        print(
            f"playlist_title={playlist_title}"
        )
        print(
            f"playlist_id={playlist_id}"
        )
        print(
            "playlist_created="
            + str(playlist_created).lower()
        )
        print(
            "playlist_item_added="
            + str(playlist_item_added).lower()
        )

    print("thumbnail=youtube_auto")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
