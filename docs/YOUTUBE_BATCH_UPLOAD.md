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


## Scheduled batch publication

Batch upload now supports a persistent publication schedule.

Current policy:

~~~text
2 new videos per day
morning: 06:00-08:59 Asia/Taipei
evening: 17:00-19:59 Asia/Taipei
~~~

For Age 7 production starting 2026-10-09:

~~~powershell
py scripts\youtube_upload_batch.py --age 7 --style B --dry-run --schedule-start 2026-10-09
py scripts\youtube_upload_batch.py --age 7 --style B --schedule-start 2026-10-09
~~~

The first real production run persists the mapping to:

~~~text
output/youtube_schedules/age7_B.json
~~~

This file is local/runtime state and remains under the gitignored `output/` tree.

Resume behavior:

1. channel inventory still determines already-uploaded state;
2. the saved schedule plan is reused;
3. newly scheduled videos keep their original `publishAt`;
4. existing channel matches are never rescheduled by the batch repair path;
5. if a pending poem's scheduled time has already passed, upload stops rather than accidentally publishing it immediately.

Use `--no-schedule` only for an intentional unscheduled batch.


## Age 7 production baseline for later groups

Age 7 validated the scheduled batch workflow in production.

The retained contract for Age 8/9 is:

~~~text
scheduler scope = local age group
timezone = Asia/Taipei
morning slot = 06:00-08:59
evening slot = 17:00-19:59
2 scheduled videos/day per batch
scheduled upload privacy = private
publishAt = future ISO 8601
notifySubscribers = false
channel inventory = uploaded-state SSOT
~~~

The scheduler is intentionally **not** channel-capacity aware. If another age group or a manually scheduled video occupies the same day, this runner does not automatically avoid that slot.

### Ambiguous resumable completion

Terminal HTTP 404/410 at the end of a resumable upload does not automatically mean the file failed. The uploader:

1. inventories the authenticated channel;
2. looks for exactly one deterministic poem match;
3. recovers that remote video if found;
4. fails if no match appears after bounded retries;
5. stops if multiple matches exist.

Use the read-only verifier when binary completeness is uncertain:

~~~powershell
py scripts\youtube_verify_upload.py --poem-id POEM_ID --style B
~~~

### Unique poem identity

Do not use poem title alone as a reconciliation key.

New tracking URLs include:

~~~text
utm_campaign=<poem title>
utm_content=pXXX
~~~

Exact generated title remains a legacy-compatible match path.

### Persisted schedule behavior

Schedule files under:

~~~text
output/youtube_schedules/
~~~

are runtime state. Existing assignments are stable. If corrected reconciliation reveals an omitted poem, it is appended to the next unused slot instead of reshuffling already assigned publish times.
