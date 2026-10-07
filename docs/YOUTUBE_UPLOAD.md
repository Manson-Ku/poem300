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

## Current production status

The YouTube connection, resumable upload, public publication defaults, made-for-kids flag, age-playlist routing, propagation recovery and channel-aware batch reconciliation are all implemented.

Age 6 has completed the full YouTube production cycle. The first private upload remains documented below as historical POC evidence; it is not the current default behavior.

Current production uploader:

~~~text
scripts/youtube_upload.py
~~~

Current batch/reconciliation layer:

~~~text
scripts/youtube_upload_batch.py
docs/YOUTUBE_BATCH_UPLOAD.md
~~~


## Upload metadata contract

SSOT:

```text
config/youtube_v1.json
scripts/youtube_upload.py
```

For every poem upload, metadata is generated from `data/poems.csv`.

### Title

```text
唐詩三百首-注音版-{詩名}-{作者}-KidMore啟蒙
```

Example for p225:

```text
唐詩三百首-注音版-春曉-孟浩然-KidMore啟蒙
```

### Description

Format:

```text
{詩名}-{作者}-{poem_type}
{完整詩詞}

https://kidmore.tw?utm_source=ytbc&utm_medium=videoDescription&utm_campaign={URL encoded 詩名}
```

The UTM campaign value is the poem title once. For example, p225 uses `utm_campaign=春曉` before URL encoding.

### Audience

Every upload explicitly sets:

```text
status.selfDeclaredMadeForKids = true
```

### Tags

```text
唐詩三百首
唐詩
兒童朗讀
兒童閱讀
```

### Publication default

Current production default:

~~~text
privacyStatus = public
notifySubscribers = false
selfDeclaredMadeForKids = true
thumbnail = YouTube automatic
~~~

The original p225 private upload below is retained only as historical POC evidence.


## Dry-run metadata check

For p225:

```powershell
py scripts\youtube_upload.py --poem-id 225 --style B --dry-run
```

This prints the resolved title, full description, tags, privacy and made-for-kids status without calling YouTube.

Expected video file:

```text
assets/p225/video/p225_B_1080p.mp4
```

## First private upload

After reviewing the dry-run metadata:

```powershell
py scripts\youtube_upload.py --poem-id 225 --style B
```

The uploader uses a resumable upload session and the existing local token:

```text
credentials/youtube_token.json
```

On success it prints the YouTube video ID and watch URL.

To explicitly override the privacy setting later:

```powershell
py scripts\youtube_upload.py --poem-id 225 --style B --privacy unlisted
py scripts\youtube_upload.py --poem-id 225 --style B --privacy public
```

Subscriber notifications remain off unless `--notify-subscribers` is supplied.


## First private upload verification

p225 <春曉> completed the first real YouTube Data API v3 resumable upload successfully.

Verified result:

```text
poem_id=225
video_id=g8daVN7W2bg
privacy=private
made_for_kids=true
notify_subscribers=false
upload=PASS
```

The resumable upload progressed through multiple 8 MiB chunks and completed without retry failure.

This verifies the full path:

```text
local rendered MP4
  -> saved OAuth user token
  -> videos.insert resumable upload
  -> generated poem metadata
  -> made-for-kids status
  -> private YouTube video
```

The first-upload POC is therefore accepted as the baseline for later batch/publication workflow work.


## Production publication defaults

After the first private-upload POC passed, the default publication policy is now:

```text
privacyStatus = public
selfDeclaredMadeForKids = true
notifySubscribers = false
thumbnail = YouTube automatic
```

No `thumbnails.set` API call is made. YouTube chooses the thumbnail automatically.

### Age playlist routing

Each upload is routed by `data/poems.csv.recommended_age`.

Playlist title template:

```text
{recommended_age}歲建議
```

Examples:

```text
recommended_age=6 -> 6歲建議
recommended_age=7 -> 7歲建議
recommended_age=8 -> 8歲建議
recommended_age=9 -> 9歲建議
```

After a video upload succeeds, the uploader:

1. searches the authenticated channel's owned playlists for the exact age-playlist title;
2. creates the playlist as public if it does not exist;
3. checks whether the uploaded video is already in that playlist;
4. inserts it only if missing.

The playlist write operations require the additional OAuth scope:

```text
https://www.googleapis.com/auth/youtube.force-ssl
```

The existing token created before this change does not have that scope. Reauthorize once:

```powershell
py scripts\youtube_auth.py --force-reauth
```

After that, normal uploads reuse the saved token.

### Production dry-run

Example:

```powershell
py scripts\youtube_upload.py --poem-id 225 --style B --dry-run
```

The preview now includes:

```text
privacy=public
made_for_kids=true
playlist=6歲建議
thumbnail=youtube_auto
```

### Production upload

```powershell
py scripts\youtube_upload.py --poem-id 225 --style B
```

On success, the command also prints:

```text
playlist_title=6歲建議
playlist_id=...
playlist_created=true|false
playlist_item_added=true|false
thumbnail=youtube_auto
```

The first p225 private-upload POC remains a historical test artifact; this policy applies to subsequent uploads unless `--privacy` explicitly overrides the default.


## Playlist propagation recovery

A real public upload can complete before a newly created playlist or newly uploaded video is immediately visible to every `playlistItems` API endpoint.

Observed production case:

```text
video upload: PASS
privacy: public
playlistItems.list: 404 playlistNotFound
```

The uploader now avoids the pre-insert `playlistItems.list` check and performs the idempotent playlist insert directly.

For transient `playlistNotFound` or `videoNotFound` responses, it retries the playlist insert with bounded exponential delays.

If YouTube reports `videoAlreadyInPlaylist`, the operation is treated as already complete.

Most importantly, a failed post-upload playlist step no longer requires another video upload. Use the existing video ID:

```powershell
py scripts\youtube_upload.py --poem-id 226 --existing-video-id l5WYLemUnuQ
```

This command:

1. verifies the existing YouTube video;
2. resolves the `recommended_age` playlist;
3. creates the playlist if still missing;
4. adds the existing video with propagation retries;
5. does not upload the MP4 again.

This repair path should always be used after a successful video upload followed by a playlist-stage failure.


## Age 6 batch completion

Final channel-aware batch result:

~~~text
already_uploaded=2
existing_postchecked=2
existing_post_failed=0
uploaded_now=23
failed=0
waiting_local=0
PASS
~~~

This establishes 25/25 Age 6 channel completeness and closes the first full production delivery cycle.

Future age groups should reuse the same uploader and reconciliation behavior rather than introducing a separate publication path.
