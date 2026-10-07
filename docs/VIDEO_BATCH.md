# Age-group Video Batch Rendering

## Goal

Render all approved poem videos for one `recommended_age` group using the existing timeline + composer pipeline.

This batch stage does **not** upload anything to YouTube.

## Resume-safe behavior

Default behavior:

1. select poems whose `recommended_age` matches the requested age;
2. keep only `visual_plan_status=approved`;
3. skip MP4 files that already exist;
4. build/write `timeline.json` for every remaining poem;
5. run `render_video.py --dry-run` for every remaining poem;
6. if any poem fails preflight, stop before rendering any new MP4;
7. if all remaining poems pass, render them sequentially;
8. if a later render fails, rerun the same command after fixing it; completed MP4s are skipped.

This makes long batches resumable without re-rendering already completed videos.

## 6-year-old group

Current `data/poems.csv` contains 25 poems with:

```text
recommended_age=6
visual_plan_status=approved
```

p225 and p226 have already been used for video pipeline validation locally. If their MP4 files are still present, the batch command skips them automatically and renders the remaining poems.

## Preflight only

Before starting the long render, optionally verify the full remaining batch:

```powershell
py scripts\render_video_batch.py --age 6 --style B --preflight-only
```

Expected high-level result:

```text
selected_total=25
existing_skipped=...
ready_to_render=...
PASS preflight_only=true
```

## Render remaining 6-year-old videos

```powershell
py scripts\render_video_batch.py --age 6 --style B
```

No `--force` is needed. Existing videos are intentionally skipped.

For every remaining poem the script uses the existing production defaults from:

```text
scripts/build_video_timeline.py
scripts/render_video.py
config/video_timing_v1.json
config/video_motion_v1.json
config/bgm_mix_v1.json
```

Therefore every rendered video inherits:

- existing TTS assets;
- existing text overlays;
- Style B backgrounds;
- run-level stable centered zoom;
- scene crossfades;
- automatic stable-random poem BGM when no explicit BGM mapping exists;
- BGM baseline of -35 LUFS;
- the existing audio-driven timeline.

## Force rebuild

Only when intentionally rebuilding every selected MP4:

```powershell
py scripts\render_video_batch.py --age 6 --style B --force
```

This also re-renders existing p225/p226 outputs.

## Failure / resume

If one poem fails during timeline or composer preflight, no new MP4 rendering starts. The terminal prints the failing poem ID and stage.

If rendering itself later fails after some poems have already completed, fix the issue and run the same command again:

```powershell
py scripts\render_video_batch.py --age 6 --style B
```

Completed MP4 files will be skipped.

## Output

Each poem remains in the existing canonical location:

```text
assets/pXXX/video/pXXX_B_1080p.mp4
```

Generated MP4 binaries remain local and are gitignored.
