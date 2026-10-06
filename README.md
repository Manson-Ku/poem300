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
│  └─ scenes.csv
├─ docs/
│  ├─ SCHEMA.md
│  └─ VIDEO_PIPELINE.md
├─ scripts/
│  └─ build_scenes.py
├─ .gitignore
└─ README.md
```

## SSOT 原則

1. `data/poems.csv` 是詩詞內容層 SSOT。
2. `data/scenes.csv` 的 `scene_id / poem_id / scene_no / original_line / child_explanation_line` 應由 script 生成。
3. AI prompt、產圖狀態、媒體路徑可在後續 pipeline 中補充或移至 manifest。
4. 不將字型檔直接提交至公開 Repo；只記錄字型名稱、授權與本機配置方式。
5. 原始高解析圖片、音檔與 MP4 不直接進 Git，後續使用外部 object storage 或 release artifact 管理。

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
