#!/usr/bin/env python3
"""Authorize poem300 against the user's YouTube channel.

Reads the OAuth Desktop-app client JSON from YTB_KEY_JSON in .env,
stores the resulting user token locally under credentials/, then makes
an authenticated channels.list(mine=True) request to verify the channel.

No client secret or refresh token is printed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


DEFAULT_TOKEN_PATH = Path("credentials/youtube_token.json")
DEFAULT_CONFIG_PATH = Path("config/youtube_v1.json")

# Installed apps do not support incremental authorization. Request the
# upload scope now together with readonly so the same token can both
# verify the authenticated channel and upload later.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_client_config(env_name: str) -> dict[str, Any]:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        raise RuntimeError(
            f"{env_name} is missing. Put the OAuth Desktop-app JSON "
            "in .env as one JSON value."
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{env_name} is not valid JSON: {exc}"
        ) from exc

    if "installed" not in payload:
        raise RuntimeError(
            f"{env_name} must contain an OAuth client of type "
            "Desktop app (top-level key: 'installed')."
        )

    installed = payload["installed"]
    required = {
        "client_id",
        "client_secret",
        "auth_uri",
        "token_uri",
    }
    missing = sorted(
        key
        for key in required
        if not installed.get(key)
    )
    if missing:
        raise RuntimeError(
            f"{env_name} is missing required fields: "
            + ", ".join(missing)
        )

    return payload


def load_credentials(
    *,
    client_config: dict[str, Any],
    token_path: Path,
    force_reauth: bool,
) -> Credentials:
    credentials: Credentials | None = None

    if token_path.exists() and not force_reauth:
        credentials = Credentials.from_authorized_user_file(
            str(token_path),
            SCOPES,
        )

    if (
        credentials
        and credentials.expired
        and credentials.refresh_token
        and not force_reauth
    ):
        credentials.refresh(Request())

    if not credentials or not credentials.valid or force_reauth:
        flow = InstalledAppFlow.from_client_config(
            client_config,
            SCOPES,
        )
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            authorization_prompt_message=(
                "Opening browser for Google / YouTube authorization..."
            ),
            success_message=(
                "YouTube authorization completed. "
                "You can close this browser tab."
            ),
            open_browser=True,
        )

    token_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    token_path.write_text(
        credentials.to_json(),
        encoding="utf-8",
    )

    return credentials


def normalize_handle(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("@"):
        value = value[1:]
    return value.casefold()


def verify_channel(
    *,
    credentials: Credentials,
    expected_handle: str,
) -> dict[str, Any]:
    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    response = youtube.channels().list(
        part="id,snippet",
        mine=True,
        maxResults=1,
    ).execute()

    items = response.get("items", [])
    if not items:
        raise RuntimeError(
            "OAuth succeeded, but no YouTube channel was returned for "
            "the authenticated Google account."
        )

    channel = items[0]
    snippet = channel.get("snippet", {})
    actual_handle = str(
        snippet.get("customUrl", "")
    ).strip()

    expected_normalized = normalize_handle(
        expected_handle
    )
    actual_normalized = normalize_handle(
        actual_handle
    )

    handle_match: bool | None
    if expected_normalized and actual_normalized:
        handle_match = (
            expected_normalized == actual_normalized
        )
    else:
        handle_match = None

    return {
        "channel_id": channel.get("id", ""),
        "title": snippet.get("title", ""),
        "custom_url": actual_handle,
        "expected_handle": expected_handle,
        "handle_match": handle_match,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Authorize YouTube Data API v3 and verify the signed-in channel."
        )
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
    )
    parser.add_argument(
        "--token",
        default=str(DEFAULT_TOKEN_PATH),
    )
    parser.add_argument(
        "--force-reauth",
        action="store_true",
        help=(
            "Ignore the saved user token and open the OAuth browser flow again."
        ),
    )
    args = parser.parse_args()

    load_dotenv()

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(
            f"YouTube config not found: {config_path.as_posix()}"
        )

    config = read_json(config_path)
    if config.get("version") != "youtube_v1":
        raise RuntimeError(
            "youtube_auth.py currently requires config version youtube_v1"
        )

    env_name = str(
        config.get(
            "oauth_client_env",
            "YTB_KEY_JSON",
        )
    )
    token_path = Path(args.token)

    client_config = load_client_config(
        env_name
    )

    try:
        credentials = load_credentials(
            client_config=client_config,
            token_path=token_path,
            force_reauth=args.force_reauth,
        )
        result = verify_channel(
            credentials=credentials,
            expected_handle=str(
                config.get(
                    "expected_handle",
                    "",
                )
            ),
        )
    except HttpError as exc:
        raise RuntimeError(
            f"YouTube API request failed: {exc}"
        ) from exc

    print("YouTube OAuth PASS")
    print(f"token={token_path.as_posix()}")
    print(
        "scopes="
        + ",".join(SCOPES)
    )
    print(
        f"channel_title={result['title']}"
    )
    print(
        f"channel_id={result['channel_id']}"
    )
    print(
        "channel_custom_url="
        + (
            str(result["custom_url"])
            if result["custom_url"]
            else "(not returned)"
        )
    )

    if result["handle_match"] is True:
        print(
            f"expected_handle={result['expected_handle']} MATCH"
        )
    elif result["handle_match"] is False:
        print(
            f"expected_handle={result['expected_handle']} MISMATCH"
        )
        return 2
    else:
        print(
            "expected_handle_check=SKIPPED "
            "(API did not return a comparable customUrl)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
