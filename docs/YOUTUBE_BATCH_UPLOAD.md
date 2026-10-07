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


## Age 6 final production result

Age 6 batch upload is COMPLETE.

Final terminal summary:

~~~text
already_uploaded=2
existing_postchecked=2
existing_post_failed=0
uploaded_now=23
failed=0
waiting_local=0
PASS
~~~

Interpretation:

- 25 Age 6 poems were reconciled against the authenticated KidMoreTW channel.
- 2 videos already existed and were not uploaded again.
- Both existing videos completed idempotent post-upload checks.
- 23 new videos uploaded successfully in the batch.
- No upload failed.
- No local MP4 was missing.
- Channel completeness for the Age 6 poem set is therefore 25/25.

One historical nuance remains: p225 was the first private upload POC. Existing-video reconciliation intentionally preserves the privacy of an already uploaded video, so channel completeness does not itself assert uniform public visibility across all historical uploads.

## Reuse for later age groups

The same runtime reconciliation pattern is the production baseline for Age 7 / 8 / 9:

~~~powershell
py scripts\youtube_upload_batch.py --age 7 --style B --dry-run
py scripts\youtube_upload_batch.py --age 7 --style B
~~~

A group is ready to mark YouTube COMPLETE when:

~~~text
duplicate_conflicts = 0
existing_post_failed = 0
failed = 0
waiting_local = 0
channel completeness = selected poem count
~~~

The next group should not be uploaded until its local resource/video production gate has passed.
