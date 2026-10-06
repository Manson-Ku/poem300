# Data Schema

## data/poems.csv

一首詩一列，為內容層 SSOT。

| 欄位 | 型別 | 說明 |
|---|---|---|
| poem_id | integer | 穩定主鍵 |
| author | string | 作者 |
| poem_type | string | 詩體 |
| title | string | 題名 |
| content | text | 原詩；保留實際換行 |
| child_explanation_6_8 | text | 6–8 歲兒童版解釋；必須與 content 逐行 1:1 |
| popularity_level | integer | 熱門程度 1–5 |
| recommended_age | integer | 建議最低起始年齡 6–9 |

### 驗證規則

- poem_id 唯一且非空。
- popularity_level ∈ {1,2,3,4,5}。
- recommended_age ∈ {6,7,8,9}。
- content 與 child_explanation_6_8 的實際行數必須一致。
- 不依逗號或句號重新切 Scene；只依實際換行。

目前適齡分布：

- 6: 25
- 7: 50
- 8: 100
- 9: 138

## data/scenes.csv

一個實際換行段落一列。

| 欄位 | 說明 |
|---|---|
| scene_id | 穩定 Scene ID，例如 p001_s01 |
| poem_id | 對應 poems.poem_id |
| scene_no | 詩內 Scene 順序，從 1 開始 |
| original_line | 原詩對應行 |
| child_explanation_line | 兒童解釋對應行 |
| image_prompt_zh | AI 產圖 prompt |
| style_group | 畫風 preset |
| character_consistency_key | 同一首詩角色/視覺一致性 key |
| aspect_ratio | 預設 16:9 |
| duration_sec | 影片停留秒數 |
| motion_hint | zoom/pan/parallax 等提示 |
| background_image | 無字背景圖資產路徑 |
| text_overlay_image | 注音文字透明 PNG 路徑 |
| tts_audio | TTS 音檔路徑 |
| image_status | pending/generated/approved/rejected |

### 核心規則

下列欄位不得人工改變語意：

- scene_id
- poem_id
- scene_no
- original_line
- child_explanation_line

它們必須由 poems.csv 重新生成。

## Asset layout

資產以 poem → scene 為核心階層：

```text
assets/
  p225/
    poem.json
    s01/
      scene.json
      audio/
        poem.wav
        explanation.wav
      image/
        background.webp
      text/
        poem_bpmf.png
    s02/
      ...
```

### 規則

- `pXXX`：zero-padded `poem_id`。
- `sYY`：zero-padded `scene_no`。
- 一個 Scene 對應一組 image / text / audio 素材。
- 每個 Scene 的 TTS 分為 `poem.wav` 與 `explanation.wav`。
- `poem.json` / `scene.json` 是輕量 manifest，可納入 Git。
- WAV、背景圖、注音 PNG 等 binary generated assets 不納入 Git。
- TTS API 使用與成本紀錄集中於 `data/tts_usage.csv`。
