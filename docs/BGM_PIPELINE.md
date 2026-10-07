# BGM Pipeline

## Goal

Each poem may independently select one local background-music track.

BGM is a presentation-layer asset. It must not change:

- poem/scenes SSOT
- TTS assets
- timeline event timestamps
- narration gain
- visual composition

The narration remains the foreground.

## Local BGM directory

Default directory:

```text
bgm/
```

Audio binaries remain local and are already excluded by the repository-wide `*.mp3` / audio ignore rules.

The composer discovers supported local files dynamically. There is no need to register every MP3 in Git.

Supported extensions:

```text
.mp3
.wav
.m4a
.aac
.flac
.ogg
```

List local tracks:

```powershell
py scripts\manage_bgm.py --list
```

## Persistent poem selection

SSOT:

```text
data/poem_bgm.csv
```

Fields:

```text
poem_id,bgm_file,gain_db,notes
```

Example local selection command:

```powershell
py scripts\manage_bgm.py --poem-id 226 --set "空山滴翠V2.mp3"
```

Optional per-poem trim:

```powershell
py scripts\manage_bgm.py --poem-id 226 --set "空山滴翠V2.mp3" --gain-db -2
```

Check current selection:

```powershell
py scripts\manage_bgm.py --poem-id 226
```

Clear selection:

```powershell
py scripts\manage_bgm.py --poem-id 226 --clear
```

## One-off render override

A render can temporarily override the persistent map:

```powershell
py scripts\render_video.py --poem-id 226 --style B --bgm "空山滴翠V2.mp3" --force
```

The unique filename stem also works:

```powershell
py scripts\render_video.py --poem-id 226 --style B --bgm "空山滴翠V2" --force
```

Optional one-off trim:

```powershell
py scripts\render_video.py --poem-id 226 --style B --bgm "空山滴翠V2" --bgm-gain-db -3 --force
```

Disable a mapped BGM for one render:

```powershell
py scripts\render_video.py --poem-id 226 --style B --bgm none --force
```

Selection priority:

```text
CLI --bgm
  >
data/poem_bgm.csv
  >
no BGM
```

## Relative volume contract

SSOT:

```text
config/bgm_mix_v1.json
```

v1 policy:

```text
narration gain      0 dB (untouched)
BGM loudness target -35 LUFS
BGM true-peak target -9 dB
BGM LRA target       7 LU
default poem trim    0 dB
fade in              1.2 s
fade out             1.8 s
final limiter         0.95
```

The important part is that the seven source tracks are not trusted to have equal source loudness.

For every render the selected BGM is:

```text
local BGM
  -> loop if needed
  -> trim to video duration
  -> loudness normalize to -35 LUFS
  -> apply optional poem gain_db
  -> fade in/out
  -> mix under narration
```

Narration is not normalized downward to make room for the music.

This gives all locally produced tracks a comparable starting level while keeping TTS clearly dominant.

Use `gain_db` only for artistic correction after listening:

```text
-3 dB  quieter than normal BGM bed
 0 dB  default
+2 dB  slightly more present
```

Large positive trims should be avoided for children's reading videos.

## Final composition

Without BGM:

```text
event segments
  -> concat
  -> final MP4
```

With BGM:

```text
event segments
  -> concat narration-only MP4
  -> BGM normalization / looping / fades
  -> narration + BGM mix
  -> AAC final audio
  -> final MP4
```

The video stream is copied during the BGM mix step, so adding BGM does not re-encode the already-rendered picture.


## Listening QA adjustment

After the first p226 BGM render, the default music bed was judged slightly too loud relative to narration.

The global BGM baseline is now about 3 dB lower:

```text
integrated loudness  -32 -> -35 LUFS
true-peak ceiling     -6 ->  -9 dB
```

A 3 dB reduction is approximately a 30% reduction in linear amplitude. Per-poem `gain_db` remains available for small artistic adjustments.
