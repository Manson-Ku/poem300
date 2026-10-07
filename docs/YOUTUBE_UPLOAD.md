# YouTube OAuth / Upload Pipeline

## Current stage

This stage establishes the authenticated YouTube Data API v3 connection only.

It does **not** upload a video yet.

Expected channel:

```text
https://www.youtube.com/@KidMoreTW
@KidMoreTW
```

## Credentials

The local `.env` contains the OAuth 2.0 Desktop-app client JSON:

```text
YTB_KEY_JSON={"installed":{...}}
```

The real value must never be committed.

Repository example:

```text
.env.example
```

The OAuth user token is written locally to:

```text
credentials/youtube_token.json
```

The entire `credentials/` directory is already gitignored.

## Scopes

The connection test requests both:

```text
https://www.googleapis.com/auth/youtube.readonly
https://www.googleapis.com/auth/youtube.upload
```

The readonly scope is used to verify which channel the signed-in account belongs to.

The upload scope is requested now so the same token can be used by the later upload command.

## VS Code setup

Activate the existing virtual environment, then install repo dependencies:

```powershell
py -m pip install --upgrade pip
pip install -r requirements.txt
```

The YouTube-specific packages are now part of `requirements.txt`:

```text
google-api-python-client
google-auth-oauthlib
google-auth-httplib2
```

## First authorization

Run from the repository root:

```powershell
py scripts\youtube_auth.py
```

Expected behavior:

1. Python reads `YTB_KEY_JSON` from `.env`.
2. The system browser opens Google's OAuth consent page.
3. Sign in with the Google account that owns / manages the KidMoreTW channel.
4. Approve the requested YouTube permissions.
5. Google redirects to a temporary local `127.0.0.1` callback.
6. The local user token is saved to `credentials/youtube_token.json`.
7. The script calls `channels.list(mine=True)`.
8. The script prints the authenticated channel title / ID / custom URL.
9. If the API returns a comparable custom URL, it must match `@KidMoreTW`.

Expected terminal result:

```text
YouTube OAuth PASS
token=credentials/youtube_token.json
...
channel_title=...
channel_id=...
channel_custom_url=@KidMoreTW
expected_handle=@KidMoreTW MATCH
```

## Reauthorization

Normally the saved token is reused and refreshed automatically.

To intentionally discard the current session and run the browser authorization flow again:

```powershell
py scripts\youtube_auth.py --force-reauth
```

This is useful if the wrong Google account was selected.

## OAuth consent screen notes

If the Google Cloud OAuth app is still in Testing mode, make sure the Google account used for KidMoreTW is an allowed test user.

A browser warning for an unverified testing app is an OAuth-console configuration issue, not a YouTube API connection failure.

## Next stage

After this connection test passes, the next implementation is the actual resumable YouTube upload command.

The upload stage will consume an already-rendered MP4 such as:

```text
assets/p225/video/p225_B_1080p.mp4
```

and will initially default to a safe non-public upload status until metadata / thumbnail / playlist behavior has been verified.
