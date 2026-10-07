# poem300

唐詩三百首兒童內容、分鏡、產圖與影片生成資料專案。

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

```text
poem300/
├─ data/
│  ├─ poems.csv
│  ├─ scenes.csv
│  └─ tts_usage.csv
├─ assets/
│  └─ pXXX/
│     ├─ poem.json
│     └─ sYY/
│        ├─ scene.json
│        ├─ audio/
│        │  ├─ poem.wav
│        │  └─ explanation.wav
│        ├─ image/
│        │  └─ background.webp
│        └─ text/
│           └─ poem_bpmf.png
├─ docs/
├─ scripts/
│  ├─ build_scenes.py
│  ├─ generate_tts_assets.py
│  ├─ tts_cost_report.py
│  └─ tts_poc.py
├─ .gitignore
└─ README.md
```

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

第一個 POC 建議先選熱門短詩，例如：

- 春曉
- 靜夜思
- 登鸛雀樓
- 相思
- 鹿柴

先驗證：

1. 統一畫風
2. Scene prompt
3. 背景插圖
4. 注音透明 PNG
5. TTS
6. FFmpeg 動態化
7. YouTube-ready MP4


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
