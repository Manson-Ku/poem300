# Video Pipeline

## Session timeline v1

SSOT：

```text
config/video_sessions_v1.json
```

影片固定由六個 session 組成：

```text
session_intro
session_content
session_explain
session_recap1
session_recap2
session_end
```

### session_intro

- 顯示 title + author。
- poem_type 可顯示。
- 依序播放 `title.wav`、`author.wav`。
- 不顯示 content / explanation。

### session_content

依 Scene 順序：

1. 切換到該 Scene 背景圖。
2. 該 Scene 的 content PNG 出現在目前 content page 的固定 slot。
3. 同一 page 已出現的前面詩句保留。
4. 播放該 Scene `poem.wav`。
5. 每 4 個非空 Scene 做一次 **content-zone only page turn**。

換頁只清除 content 區，title / author 仍常駐。

### session_explain

每個 Scene：

1. 顯示 / 累積該句 content。
2. 播放 `poem.wav`。
3. 在 explanation zone 顯示該句 `explanation.png`。
4. 播放 `explanation.wav`。
5. explanation audio 結束後 explanation overlay 消失。
6. 已出現的 content 行仍保留到該 content page 結束。

### session_recap1

與 `session_content` 相同：

- Scene 背景。
- content 逐段累積。
- 每 Scene 播放一次 poem.wav。
- 不播放 explanation。

### session_recap2

再執行一次與 `session_content` 相同的完整朗讀。

### session_end

- 隱藏 content / explanation。
- 顯示 title + author。
- poem_type 可顯示。
- 依序播放 `title.wav`、`author.wav`。

### Persistent overlays

Title / Author：

```text
session_intro    visible
session_content  visible
session_explain  visible
session_recap1   visible
session_recap2   visible
session_end      visible
```

Poem type：

```text
intro/end only
```

### Content page state

每個 content-bearing session 開始時，都從 page 1 / slot 1 重新開始，不沿用上一個 session 的 reveal state。

固定：

```text
page capacity = 4 nonblank Scenes
slot positions = config/text_overlay_1080p_v2.json
```

Blank Scene 是 separator：

- 不播放音訊。
- 不產文字。
- 若 page 已有內容，結束當頁。
- 下一個非空 Scene 從新頁 slot 1 開始。


## 目標

以 313 首唐詩資料，自動產出兒童向 YouTube-ready 影片。

## 預設製作方式

全量預設不直接生成 AI Video。

```text
poems.csv
  -> scenes.csv
  -> AI background image
  -> BPMF text overlay PNG
  -> TTS (default: gemini-3.8-flash-lite-tts)
  -> optional music
  -> FFmpeg motion / transition
  -> MP4
```

## Scene 規則

一個「實際換行段落」就是一個 Scene。

不依標點拆分。

## Background image

- 16:9 主版。
- 不在 AI 圖裡產任何中文或注音。
- 統一兒童繪本 + 淡中式視覺語彙。
- 同一首詩盡量共享 palette、人物設定、服飾與時代感。
- 保留文字安全區。
- 產圖 prompt 以 original_line + child_explanation_line 為主要語意來源。

## Text overlay

文字與背景完全分離。

文字層：

- 使用指定注音中文字型。
- Render 為透明 PNG。
- 可獨立重做，不需重產背景。
- 原詩是畫面主文字。
- 兒童解釋可依影片模板決定是否同步顯示。

YouTube CC 仍可另外生成，但不作為注音排版 SSOT。

## TTS

- Production default model: `gemini-3.8-flash-lite-tts`.
- One Scene produces one poem TTS and one explanation TTS asset.
- Segmentation follows the same physical newline boundary as `scenes.csv`.
- Usage/cost accounting is recorded per API request in `data/tts_usage.csv`.
- The higher-cost `gemini-3.8-flash-tts` model is not part of the default production path.
- Production assets live under `assets/pXXX/sYY/audio/`.
- Existing WAV assets are skipped by default so the batch is resumable.
- `--force` is the only normal path that overwrites existing audio.
- `--age 6 7 8 9` can restrict generation to exact recommended-age groups.

## Audio-driven timing

Timing SSOT:

```text
config/video_timing_v1.json
```

The timeline is not based on a fixed Scene duration. Every spoken event uses:

```text
event duration
= pre-roll
+ measured WAV duration
+ post-roll
```

v1 padding:

```text
title       0.45s + audio + 0.50s
author      0.25s + audio + 0.60s
poem        0.30s + audio + 0.45s
explanation 0.25s + audio + 0.55s
```

The poem pre-roll is also the reserved scene-transition budget. Current scene crossfade target is 0.30s, so it fits inside the 0.30s poem pre-roll and is not added twice.

Additional timing:

```text
content-zone page turn  0.45s
between sessions        0.80s
session lead/tail       session-specific fixed holds
```

Content is revealed at the start of the poem pre-roll, so the child sees the new line shortly before narration begins.

Explanation overlay is revealed at the start of the explanation pre-roll and remains through the explanation post-roll.

Timeline/preflight builder:

```powershell
py scripts\build_video_timeline.py --poem-id 226 --style B --dry-run
py scripts\build_video_timeline.py --poem-id 226 --style B
```

For all age-6 poems:

```powershell
py scripts\build_video_timeline.py --age 6 --style B --dry-run
py scripts\build_video_timeline.py --age 6 --style B
```

Output:

```text
assets/pXXX/video/timeline.json
```

This stage validates all required image/text/audio assets and calculates timestamps but does **not** render MP4. FFmpeg composition starts only after pronunciation QA and timeline preflight pass.


## Assembly POC composer v1

After pronunciation QA passes and `timeline.json` has `preflight.status=PASS`, the first MP4 assembly test is rendered by:

```text
scripts/render_video.py
```

The v1 composer is intentionally a contract-validation build before adding motion polish.

It implements:

```text
1920x1080
30 fps
H.264 / yuv420p
AAC 48 kHz stereo
timeline-driven audio padding
title + author persistent overlays
poem_type in intro/end
progressive content slots
content page reset
active explanation overlay
session reset behavior
```

The transparent text PNGs are stored at 2x raster resolution, so the composer scales them back to display-space dimensions before placing them in the 1920x1080 zones.

### First p226 test

Validate visual state without invoking FFmpeg:

```powershell
py scripts\render_video.py --poem-id 226 --style B --dry-run
```

Render:

```powershell
py scripts\render_video.py --poem-id 226 --style B
```

Output:

```text
assets/p226/video/p226_B_1080p.mp4
```

Re-render:

```powershell
py scripts\render_video.py --poem-id 226 --style B --force
```

FFmpeg must be available in `PATH`. A custom executable can be supplied with `--ffmpeg`.

On Windows, `pip install FFmpeg` is **not** sufficient; that installs a Python package, not `ffmpeg.exe`.

Recommended Windows install:

```powershell
winget install --id Gyan.FFmpeg -e
```

Then reopen PowerShell and verify:

```powershell
ffmpeg -version
where.exe ffmpeg
```

If FFmpeg is installed but not in PATH:

```powershell
py scripts\render_video.py --poem-id 226 --style B --ffmpeg "C:\path\to\ffmpeg.exe"
```

### Transition status

Assembly POC v1 uses hard visual cuts.

This is intentional: first validate layout, reveal state, audio timing and the complete six-session sequence.

The timing contract already reserves:

```text
poem pre-roll / scene transition budget = 0.30s
page turn pause = 0.45s
```

Therefore slow zoom / pan / scene crossfade can be added in the next composer revision without redesigning the session or audio timeline.


## Motion

靜態圖預設使用低干擾運鏡：

- slow zoom in
- slow zoom out
- pan left / right
- subtle parallax
- crossfade

避免兒童讀詩時視覺動態過強。

## AI Video

只作為 enhancement layer。

適用：

- 落花
- 流水
- 飄雪
- 燈火
- 鳥飛
- 雲霧
- 其他動態本身承載詩意的鏡頭

不作為所有 Scene 的預設產線。

## Output profiles

第一階段：

- master: 1920x1080, 16:9
- fps: 30
- codec: H.264
- audio: AAC
- subtitle: burnt-in BPMF overlay + optional YouTube CC

未來可由同一 Scene 資料另輸出：

- 9:16 Shorts
- 1:1
- 無字幕版
- 無注音版
- 純朗讀版

## POC

先選 5 首熱門短詩：

1. 春曉
2. 靜夜思
3. 登鸛雀樓
4. 相思
5. 鹿柴

驗證：

- Scene prompt 是否穩定
- 插圖風格是否一致
- 注音排版是否清楚
- TTS 節奏
- Scene duration
- FFmpeg motion
- 最終觀看體驗
