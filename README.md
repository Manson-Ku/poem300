# poem300

唐詩三百首兒童內容、分鏡、產圖與影片生成資料專案。

## 目前狀態

截至 2026-10-07：

~~~text
Age 6  COMPLETE  25 poems / 54 scenes / 25 channel videos
Age 7  ACTIVE    50 poems / 127 scenes / approved / TTS complete / image production
Age 8  PENDING  100 poems / 395 scenes / visual plans still draft
Age 9  PENDING  138 poems / 1,050 scenes / visual plans still draft
~~~

Age 6 已完成從資料、資源生成、影片組裝到 YouTube batch upload 的完整 production cycle。

完整開發狀態、production baseline、Definition of Done 與下一階段規劃：

~~~text
docs/DEVELOPMENT_STATUS.md
~~~

## 專案定位

本 Repo 以資料驅動方式管理：

- 313 首唐詩主資料
- 6–8 歲兒童版逐行解釋
- 熱門程度（1–5）
- 建議適齡（6–9）
- 依原詩實際換行切分的 Scene
- AI 靜態插圖生成
- 注音文字透明圖層
- TTS / 背景音樂
- FFmpeg 靜態圖運鏡與影片輸出
- 少量重點 Scene 的 image-to-video 擴充

## 核心決策

### 1. 一個實際換行段落 = 一個 Scene

`content` 與 `child_explanation_6_8` 必須逐行 1:1 對應。

例如：

```text
春眠不覺曉，處處聞啼鳥。
夜來風雨聲，花落知多少。
```

對應兩個 Scene，而不是依逗號拆成四個 Scene。

### 2. 主資料與 Scene 資料分離

- `data/poems.csv`：一首詩一列，內容 SSOT。
- `data/scenes.csv`：一個 Scene 一列，由主資料展開。

Scene 表原則上應由 script 重新生成，不手工修改其核心對應欄位。

### 3. 影片主流程採靜態圖 + 後製運鏡

預設流程：

```text
Poem data
  -> Scene
  -> AI background image
  -> phonetic text overlay PNG
  -> TTS / music
  -> FFmpeg pan / zoom / crossfade
  -> MP4
```

AI Video 不作為全量預設，僅預留給少數值得動態化的 Scene。

### 4. 注音文字不交給 AI 圖像模型生成

背景插圖與文字分離：

- 背景：JPG / WebP
- 注音文字：使用指定注音字型 render 成透明 PNG
- 最終影片再合成

YouTube CC / SRT / VTT 可另外提供，但不作為注音版面 SSOT。

### 5. 正式 TTS 預設使用 Gemini Flash-Lite TTS

2026-10-06 POC 決議：正式大量生成預設採 `gemini-3.8-flash-lite-tts`。

原因：同一首〈春曉〉、同一 voice/style 的雙模型實測中，Flash-Lite 品質已足夠，且音訊單價約比 Flash 低 33%。雙模型比較腳本保留作為歷史 POC，不作為正式產線預設。

## 資料量

目前：

- Poems: 313
- Scenes: 1,626
- 適齡：
  - 6 歲：25
  - 7 歲：50
  - 8 歲：100
  - 9 歲：138
- 熱門程度：
  - 5：25
  - 4：47
  - 3：136
  - 2：35
  - 1：70

## Repo 結構

~~~text
poem300/
├─ data/
│  ├─ poems.csv
│  ├─ scenes.csv
│  ├─ bopomofo_overrides.json
│  ├─ bpmf_font_extensions.json
│  ├─ tts_pronunciation_overrides.json
│  ├─ pronunciation_qa_age6.json
│  ├─ poem_bgm.csv
│  ├─ tts_usage.csv
│  └─ image_usage.csv
├─ config/
│  ├─ image_styles_production_v1.json
│  ├─ image_style_B_3d_fairytale_v1.json
│  ├─ image_styles_age6.json
│  ├─ text_overlay_1080p_v2.json
│  ├─ video_sessions_v1.json
│  ├─ video_timing_v1.json
│  ├─ video_motion_v1.json
│  ├─ bgm_mix_v1.json
│  └─ youtube_v1.json
├─ assets/
│  └─ pXXX/
│     ├─ text/
│     ├─ video/
│     └─ sYY/
│        ├─ audio/
│        ├─ image/
│        └─ text/
├─ scripts/
│  ├─ build_scenes.py
│  ├─ generate_images.py
│  ├─ generate_images_batch.py
│  ├─ generate_tts_assets.py
│  ├─ sync_bpmf_font.py
│  ├─ patch_bpmf_font.py
│  ├─ qa_age_production.py
│  ├─ qa_pronunciation.py
│  ├─ render_text_overlays.py
│  ├─ build_video_timeline.py
│  ├─ render_video.py
│  ├─ render_video_batch.py
│  ├─ manage_bgm.py
│  ├─ youtube_auth.py
│  ├─ youtube_upload.py
│  └─ youtube_upload_batch.py
├─ docs/
│  ├─ DEVELOPMENT_STATUS.md
│  ├─ IMAGE_PIPELINE.md
│  ├─ PRONUNCIATION_QA.md
│  ├─ TTS_BATCH.md
│  ├─ TEXT_OVERLAY_PIPELINE.md
│  ├─ VIDEO_PIPELINE.md
│  ├─ VIDEO_BATCH.md
│  ├─ BGM_PIPELINE.md
│  ├─ YOUTUBE_UPLOAD.md
│  └─ YOUTUBE_BATCH_UPLOAD.md
├─ requirements.txt
├─ .env.example
├─ .gitignore
└─ README.md
~~~


## SSOT 原則

1. `data/poems.csv` 是詩詞內容層 SSOT。
2. `data/scenes.csv` 的 `scene_id / poem_id / scene_no / original_line / child_explanation_line` 應由 script 生成。
3. `data/poems.csv.visual_plan_json` 保存每首詩跨 Scene 共用的人物、場景、物件與 continuity；prompt 不是 SSOT。
4. 不將字型檔直接提交至公開 Repo；只記錄字型名稱、授權與本機配置方式。
5. 原始高解析圖片、音檔與 MP4 不直接進 Git，後續使用外部 object storage 或 release artifact 管理。
6. 資產以 `assets/pXXX/sYY/` 組織；`poem.json` / `scene.json` 可進 Git，生成的 WAV / image / text overlay 不進 Git。
7. 正式 TTS 批次預設可續跑：已存在的音檔跳過；只有 `--force` 才覆蓋。
8. 可用 `--age 6|7|8|9` 指定適齡分組批次生成。

## 下一步

下一個 production phase 是 Age 7。

Age 7 目前：

~~~text
poems            50
approved         50
draft             0
scenes          127
~~~

Age 7 inventory / visual-plan approval / pronunciation QA / full TTS 已完成；目前進入圖片資源 production，之後接 timeline、MP4 與 YouTube batch reconciliation。

Image style generalization 已完成：production 預設使用 age-neutral Style B registry；Age 6 仍保留原 config / asset namespace 相容。

Age 6 不再視為 POC；其 pipeline 已是後續 7 / 8 / 9 歲的 production baseline。除非 Age 7 出現真正的跨 age contract gap，後續優先複用既有流程而不是重做架構。

詳見：`docs/DEVELOPMENT_STATUS.md`。


## Production TTS

正式批次：

```powershell
py scripts\generate_tts_assets.py
```

指定年齡層：

```powershell
py scripts\generate_tts_assets.py --age 6
py scripts\generate_tts_assets.py --age 6 7
```

預覽、不呼叫 API：

```powershell
py scripts\generate_tts_assets.py --age 6 --dry-run
```

預設已有音檔會跳過。只有明確指定才覆蓋：

```powershell
py scripts\generate_tts_assets.py --poem-id 225 --force
```

查詢每首目前語音成本：

```powershell
py scripts\tts_cost_report.py
```

完整規格見 `docs/TTS_BATCH.md`。


## Image Pipeline

6 歲組先完成完整流程：

- 25 poems
- 54 scenes
- 1 physical line = 1 Scene = 1 background image
- poem-level visual SSOT: `data/poems.csv.visual_plan_json`
- 圖片與後續注音 / 文字 overlay 完全解耦

### Current architecture: poem-world + independent-scene

圖片不再使用 multi-turn image editing，也不追求逐幕強連貫。

正式原則：

```text
Poem World
  ├─ Scene 01 -> independent image request
  ├─ Scene 02 -> independent image request
  ├─ Scene 03 -> independent image request
  └─ Scene 04 -> independent image request
```

- 每首詩有一個共用世界觀，負責時代、空間與整體氣氛。
- 每張圖優先表達自己的實際換行詩句。
- `child_explanation_line` 只作理解輔助。
- Scene 之間不傳 `previous_interaction_id`。
- 不要求人物姿勢、房間細節、鏡位或物件位置完全一致。
- 成功標準是「幼童一眼能理解目前這句詩」，而不是漫畫式連續分鏡。

模型預設：

```text
gemini-3.1-flash-lite-image
```

A/B style：

```text
A = age6_a_watercolor_ink_v1
    東方淡彩水彩繪本

B = age6_b_3d_fairytale_cinematic_v1
    3D電影童話插畫
```

Registry：

```text
config/image_styles_age6.json
```

A/B 固定同一份 poem world、Scene semantics、模型與 generation parameters，唯一變因是 style preset。

### Run

例如測試〈夜思〉：

```powershell
git pull
py scripts\generate_images.py --poem-id 226 --dry-run
py scripts\generate_images.py --poem-id 226 --force
```

只測 Style A：

```powershell
py scripts\generate_images.py --poem-id 226 --styles A --force
```

批次生成全部 6 歲 approved 詩，只產 Style B：

```powershell
py scripts\generate_images.py --age 6 --approved-only --styles B --dry-run
py scripts\generate_images.py --age 6 --approved-only --styles B
```

目前資料應選出 25 首、54 Scenes，因此 Style B 共 54 張。預設會略過已存在的正式 B 圖；只有需要全部重跑時才加 `--force`。

正式輸出：

```text
assets/p226/s01/image/{style_id}/background.webp
assets/p226/s01/image/{style_id}/meta.json
...
```

每次成功生成也會覆蓋一份最新 POC 快照：

```text
assets/poc/226_A_s01.webp
assets/poc/226_A_s02.webp
assets/poc/226_B_s01.webp
assets/poc/226_B_s02.webp
...
```

API usage / token / latency / estimated cost / interaction_id 仍寫入：

```text
data/image_usage.csv
```

`previous_interaction_id` 欄位保留，但在 independent-scene 架構下固定為空。

完整規格見 `docs/IMAGE_PIPELINE.md`.

## Text Overlay Pipeline

1920×1080 注音文字圖層已定義：

```text
config/text_overlay_1080p_v2.json
docs/TEXT_OVERLAY_PIPELINE.md
data/bopomofo_overrides.json
```

Title + content 使用 local bpmfvs-compatible 字型；author / poem_type / explanation 不加注音。超過 8 行採分頁，不將長詩硬縮成不可讀字級。


## Pronunciation QA and Timeline Preflight

Age-6 pronunciation QA:

```powershell
py scripts\qa_pronunciation.py
```

Reviewed pronunciation data:

```text
data/bopomofo_overrides.json
data/pronunciation_qa_age6.json
data/tts_pronunciation_overrides.json
docs/PRONUNCIATION_QA.md
```

Audio-driven timing:

```text
config/video_timing_v1.json
scripts/build_video_timeline.py
```

Build one timeline without rendering video:

```powershell
py scripts\build_video_timeline.py --poem-id 226 --style B --dry-run
```

MP4 composition is intentionally not started until pronunciation QA and timeline asset preflight pass.


## First MP4 assembly test

After timeline preflight passes:

```powershell
py scripts\render_video.py --poem-id 226 --style B --dry-run
py scripts\render_video.py --poem-id 226 --style B
```

Output:

```text
assets/p226/video/p226_B_1080p.mp4
```

The first hard-cut assembly POC has passed. The current composer renders one continuous background-motion clip per consecutive scene/background run, alternating centered 100% -> 80% and 80% -> 100% crop-window zoom. Timeline events slice that run-level clip, so poem/explanation boundaries do not restart the camera.


## Poem-level BGM

Local music lives under:

```text
bgm/
```

The MP3/audio files remain untracked. The composer discovers them dynamically.

List available local tracks:

```powershell
py scripts\manage_bgm.py --list
```

Persist one poem's selection:

```powershell
py scripts\manage_bgm.py --poem-id 226 --set "空山滴翠V2.mp3"
```

Or test a track without changing the poem map:

```powershell
py scripts\render_video.py --poem-id 226 --style B --bgm "空山滴翠V2" --force
```

BGM is normalized to a low background target of -35 LUFS before the optional poem-level trim is applied. Narration stays at 0 dB and remains the foreground.

SSOT:

```text
config/bgm_mix_v1.json
data/poem_bgm.csv
docs/BGM_PIPELINE.md
```


## YouTube OAuth connection

The local `.env` stores the OAuth Desktop-app client JSON in `YTB_KEY_JSON`.

Install dependencies:

```powershell
pip install -r requirements.txt
```

Authorize and verify the signed-in YouTube channel:

```powershell
py scripts\youtube_auth.py
```

The local refreshable user token is stored under `credentials/youtube_token.json`, which is gitignored. The expected channel is `@KidMoreTW`.

Full workflow: `docs/YOUTUBE_UPLOAD.md`.


### YouTube upload milestone

Age 6 的 YouTube pipeline 已完成 production 驗證。

歷史里程碑：

- p225〈春曉〉：第一支 private OAuth / resumable-upload POC，PASS。
- p226〈夜思〉：第一支 public production upload；曾遇 playlist propagation 404，existing-video repair path 已 PASS。
- Age 6 final batch：25/25 channel completeness。

最終 batch：

~~~text
already_uploaded=2
existing_postchecked=2
existing_post_failed=0
uploaded_now=23
failed=0
waiting_local=0
PASS
~~~

完整紀錄見：

~~~text
docs/YOUTUBE_UPLOAD.md
docs/YOUTUBE_BATCH_UPLOAD.md
~~~


### YouTube production defaults

After the private upload POC, normal uploads now default to public and automatically route each poem into its `recommended_age` playlist:

```text
6 -> 6歲建議
7 -> 7歲建議
8 -> 8歲建議
9 -> 9歲建議
```

Thumbnail selection is left to YouTube.

Because playlist insertion requires an additional OAuth scope, reauthorize once after pulling this version:

```powershell
py scripts\youtube_auth.py --force-reauth
```

Then preview or upload normally:

```powershell
py scripts\youtube_upload.py --poem-id 225 --style B --dry-run
py scripts\youtube_upload.py --poem-id 225 --style B
```

Expected preview includes `privacy=public`, `made_for_kids=true`, `playlist=6歲建議`, and `thumbnail=youtube_auto`.


### Repair a playlist step without re-uploading

If the video upload succeeds but the playlist step fails, reuse the returned YouTube video ID:

```powershell
py scripts\youtube_upload.py --poem-id 226 --existing-video-id l5WYLemUnuQ
```

The repair mode verifies the existing video and retries only the age-playlist routing. It never uploads the MP4 again.


## Age-group video batch rendering

Render all remaining approved 6-year-old Style B videos:

```powershell
py scripts\render_video_batch.py --age 6 --style B
```

The batch runner is resume-safe: existing MP4 files are skipped, so already completed p225/p226 videos are not rebuilt by default.

Optional full-batch preflight without rendering:

```powershell
py scripts\render_video_batch.py --age 6 --style B --preflight-only
```

The runner first validates every remaining poem; if any asset/timeline/composer preflight fails, it stops before starting expensive MP4 rendering.

Full contract: `docs/VIDEO_BATCH.md`.


## YouTube batch upload

Channel-aware batch uploader：

~~~text
scripts/youtube_upload_batch.py
~~~

先盤點、不上傳：

~~~powershell
py scripts\youtube_upload_batch.py --age 7 --style B --dry-run
~~~

正式 loop：

~~~powershell
py scripts\youtube_upload_batch.py --age 7 --style B
~~~

YouTube channel 是 runtime uploaded-state SSOT。已存在的影片不重傳；只做 idempotent playlist postcheck。未完成本機 MP4 會標成 WAIT_LOCAL；重跑同一命令即可 resume。

完整規格：`docs/YOUTUBE_BATCH_UPLOAD.md`。


### Gemini Batch image production

大量背景圖正式 production 可使用較低價的 Gemini Batch API：

~~~powershell
py scripts\generate_images_batch.py submit --age 7 --approved-only --styles B --dry-run
py scripts\generate_images_batch.py submit --age 7 --approved-only --styles B
py scripts\generate_images_batch.py status
py scripts\generate_images_batch.py collect
~~~

Batch runner 使用與同步 generator 完全相同的 prompt / style / asset contract；只改 delivery mode。現有正式背景圖預設會 SKIP，不重複送出。完整規格見 `docs/IMAGE_PIPELINE.md`。
