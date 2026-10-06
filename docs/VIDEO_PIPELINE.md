# Video Pipeline

## 目標

以 313 首唐詩資料，自動產出兒童向 YouTube-ready 影片。

## 預設製作方式

全量預設不直接生成 AI Video。

```text
poems.csv
  -> scenes.csv
  -> AI background image
  -> BPMF text overlay PNG
  -> TTS
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
