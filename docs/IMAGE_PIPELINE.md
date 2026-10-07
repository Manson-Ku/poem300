# Image Pipeline

## Current scope

第一階段先完成 **recommended_age = 6** 的完整流程。

圖片最小單位：

```text
1 physical poem line = 1 Scene = 1 background image
```

不依逗號、句號再次切 Scene。

## Architecture decision

正式圖片架構採：

```text
Poem World
  ├─ Scene 01 -> independent image request
  ├─ Scene 02 -> independent image request
  ├─ Scene 03 -> independent image request
  └─ ...
```

舊的 sequential multi-turn image editing 已棄用。

不再使用：

- `previous_interaction_id` 串接 Scene。
- 第一張 visual anchor / 後續 Scene 延續修圖。
- 強制要求人物姿勢、房間細節、鏡位、物件位置跨 Scene 完全一致。
- 為了 continuity 而犧牲本句詩意的表達。

## Product priority

圖片是唐詩學習影片的輔助，不是漫畫分鏡產品。

成功標準依序是：

1. 幼童第一眼能理解目前這一句詩。
2. 詩意沒有明顯誤讀。
3. 同一首詩維持大致相同的時代、空間與氣氛。
4. A/B 畫風穩定。
5. 可批量生產、成本可控、容易抽查。

跨 Scene 的細節一致性不是主要 KPI。

## Semantic layers

### 1. Poem World

SSOT：

```text
data/poems.csv -> visual_plan_json
```

其中 poem-level world 只負責：

- 時代感。
- 共通空間。
- 整體氣氛。
- 詩中主要人物 / 場景 / 物件的語意定義。

Poem World 是 soft context，不是構圖模板。

### 2. Scene semantics

每個 Scene 的最高優先來源：

```text
data/scenes.csv
  original_line
  child_explanation_line
```

以及對應的 `visual_plan_json.scenes[]`：

- setting
- time
- weather
- mood
- focus / visual_focus
- action / actions
- note
- current-scene entities

規則：

```text
original_line > child_explanation_line > poem world
```

`child_explanation_line` 只輔助理解，不得擴寫原詩。

Scene 不主動讀取前一幕或下一幕的主要事件。

### 3. Style preset

畫風獨立存在 `config/`。

Registry：

```text
config/image_styles_age6.json
```

Style A：

```text
style_id = age6_a_watercolor_ink_v1
config   = config/image_style_age6_A_watercolor_v1.json
```

Style B：

```text
style_id = age6_b_3d_fairytale_cinematic_v1
config   = config/image_style_age6_B_3d_fairytale_v1.json
```

A/B test 必須固定：

- 同一 Poem World。
- 同一 Scene semantics。
- 同一 image model。
- 同一 generation parameters。

唯一變因：

```text
style preset
```

## Image model

正式預設：

```text
gemini-3.1-flash-lite-image
```

每個 Scene 都是獨立 request。

不傳：

```text
previous_interaction_id
```

## Runtime prompt contract

Prompt 不是 SSOT。

每張圖只組合：

```text
Poem World
+ current original_line
+ current child_explanation_line
+ current Scene plan
+ current Scene entities
+ selected style preset
+ global image exclusions
```

不注入：

- 整首詩全文。
- 前一 Scene prompt。
- 下一 Scene prompt。
- future-event exclusions。
- continuity delta。
- multi-turn edit instructions。

### Prompt priority

模型需遵守：

1. 目前這一個實際換行詩句。
2. 本幕 focus / action / note。
3. 兒童解釋。
4. 整首詩 Poem World。
5. Style preset。

即使模型知道整首詩，也不得主動把其他句子的主要事件加入本幕。

## Text / bopomofo

圖片模型完全不需要知道後續會疊字。

原則：

```text
image generation = 完整背景插畫
text / bopomofo   = 後製
```

禁止要求：

- subtitle safe area。
- bopomofo area。
- 下方留白。
- 白色字幕條。
- UI / card layout。

圖片需完整滿版。

## Asset path

正式圖片：

```text
assets/pXXX/sYY/image/{style_id}/background.webp
```

meta：

```text
assets/pXXX/sYY/image/{style_id}/meta.json
```

POC 快速檢視：

```text
assets/poc/{poem_id}_{style_key}_sXX.webp
```

例如：

```text
assets/poc/226_A_s01.webp
assets/poc/226_A_s02.webp
assets/poc/226_B_s01.webp
assets/poc/226_B_s02.webp
```

每次成功重跑會覆蓋 POC 同名檔，正式 asset path 不變。

## Usage ledger

所有 API request 持續寫入：

```text
data/image_usage.csv
```

至少保存：

- scene_id
- poem_id
- model
- style_key / style_id
- interaction_id
- previous_interaction_id
- token usage
- latency
- estimated cost
- output path
- POC path
- status / error

在 independent-scene 模式下：

```text
previous_interaction_id = empty
```

欄位保留只是為了歷史 ledger 相容。

## Main command

測試一首詩：

```powershell
py scripts\generate_images.py --poem-id 226 --dry-run
```

正式生成 A/B：

```powershell
py scripts\generate_images.py --poem-id 226 --force
```

只生成 Style A：

```powershell
py scripts\generate_images.py --poem-id 226 --styles A --force
```

只測指定 Scene：

```powershell
py scripts\generate_images.py --poem-id 226 --scene-ids p226_s03 --force
```

## Gate

目前不做新舊架構比較。

舊 multi-turn 模式視為已淘汰。

新模式的驗證只看：

1. 每個實際換行是否只生成一張圖。
2. 該圖是否優先表達自己的詩句。
3. 兒童解釋是否只作輔助。
4. Poem World 是否提供足夠的大方向一致性。
5. A/B 是否只改畫風。
6. 圖片是否無文字、字幕、注音、Logo、UI、卡片框。
7. 成本與人工抽查負擔是否適合批量生產。
