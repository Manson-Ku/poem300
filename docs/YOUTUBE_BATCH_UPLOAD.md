# YouTube Batch Upload

Use `scripts/youtube_upload_batch.py` after local poem videos are rendered.

The script inventories the authenticated KidMoreTW channel first. It treats a poem as already uploaded when the generated title or canonical tracking URL matches an existing channel video.

Default behavior:

- approved poems only;
- one recommended age group per run;
- existing channel videos are never re-uploaded;
- existing matches receive idempotent playlist postcheck only;
- missing local MP4 files are reported as `WAIT_LOCAL`;
- duplicate channel matches stop the batch;
- new uploads delegate to `scripts/youtube_upload.py`;
- upload errors stop the batch by default.

Preview the 6-year-old queue:

```powershell
py scripts\youtube_upload_batch.py --age 6 --style B --dry-run
```

Production run:

```powershell
py scripts\youtube_upload_batch.py --age 6 --style B
```

Staged test:

```powershell
py scripts\youtube_upload_batch.py --age 6 --style B --limit 1
```

The command is resume-safe. After an interrupted or partially successful run, execute the same command again. The next channel inventory skips videos that already exist and continues with poems still missing from the channel.

Existing private test videos remain private. New videos follow the current `youtube_v1` publication settings.
