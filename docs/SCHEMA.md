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
| visual_plan_version | string | 視覺語意 schema 版本；尚未建立時可空白 |
| visual_plan_status | string | pending/draft/approved 等視覺計畫狀態 |
| visual_plan_json | JSON string | 整首詩跨 Scene 共用的場景、人物、物件、狀態與 continuity SSOT |

### 驗證規則

- poem_id 唯一且非空。
- popularity_level ∈ {1,2,3,4,5}。
- recommended_age ∈ {6,7,8,9}。
- content 與 child_explanation_6_8 的實際行數必須一致。
- 不依逗號或句號重新切 Scene；只依實際換行。
- `visual_plan_json` 不保存畫風、模型、prompt 或字幕/注音版面。
- 相同 `visual_plan_json` 必須可套用不同 style preset 做 A/B test。

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
        age6_a_watercolor_ink_v1/
          background.webp
        age6_b_3d_fairytale_cinematic_v1/
          background.webp
      text/
        poem_bpmf.png
    s02/
      ...
```

### 規則

- `pXXX`：zero-padded `poem_id`。
- `sYY`：zero-padded `scene_no`。
- 一個 Scene 對應一組 text / audio 素材，以及一個或多個 style-specific image variants。
- 每個 Scene 的 TTS 分為 `poem.wav` 與 `explanation.wav`。
- `poem.json` / `scene.json` 是輕量 manifest，可納入 Git。
- WAV、背景圖、注音 PNG 等 binary generated assets 不納入 Git。
- TTS API 使用與成本紀錄集中於 `data/tts_usage.csv`。


## Text overlay assets

1080p 文字圖層規格：

```text
config/text_overlay_1080p_v1.json
docs/TEXT_OVERLAY_PIPELINE.md
```

注音多音字 IVS 校正：

```text
data/bopomofo_overrides.json
```

資產：

```text
assets/pXXX/text/title_bpmf.png
assets/pXXX/text/author.png
assets/pXXX/text/poem_type.png
assets/pXXX/text/manifest.json
assets/pXXX/sYY/text/content_bpmf.png
assets/pXXX/sYY/text/explanation.png
```

規則：

- title + content 使用 bpmfvs-compatible 注音字型。
- author / poem_type / explanation 不加注音。
- font binaries 只存在 local，不進 Git。
- 每個 content physical line = 一張 content_bpmf.png。
- blank Scene 不產文字 PNG，但 manifest 保留 separator。
- 超過 8 行的詩採 content page 分頁，不允許一路縮小到不可讀。
- child_explanation 只顯示 active Scene 一行，作為輔助文字。

## visual_plan_json

`visual_plan_json` 是 poem-level 的視覺語意 SSOT。核心概念：

```text
poem
├─ world
├─ entities
│  ├─ characters
│  ├─ animals
│  ├─ locations
│  └─ objects
└─ scenes
   ├─ s01
   ├─ s02
   └─ ...
```

Scene 只引用穩定 entity ID，並描述該 Scene 的狀態 / 動作 / 關係。

例如同一位主角跨 Scene 都應引用同一個 `character_id`，而不是每次重新用自然語言創造一個新人物。

Style 不屬於 visual plan；Style registry 位於：

```text
config/image_styles_age6.json
```
