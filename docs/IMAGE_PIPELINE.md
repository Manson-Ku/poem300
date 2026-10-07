# Image Pipeline

## Current scope

第一階段只完成 **recommended_age = 6** 的完整流程。

圖片最小單位：

```text
1 physical poem line = 1 Scene = 1 background image
```

不依逗號、句號再次切 Scene。

## Architecture decision

圖片生成分成三層，彼此解耦：

```text
Poem visual semantics (SSOT)
        +
Style preset
        +
Image model / runtime prompt
        ↓
Generated image asset
```

### 1. Poem visual semantics

SSOT 位於 `data/poems.csv -> visual_plan_json`。

它負責保存：

- 整首詩共同世界設定。
- 穩定人物 / 動物 / 場景 / 物件 ID。
- 每個 Scene 使用哪些 entity。
- entity 在該 Scene 的狀態、動作、位置關係。
- Scene 與 Scene 之間需要延續的 continuity。

它不保存：

- 畫風。
- image model。
- prompt wording。
- 字幕 / 注音 / overlay。
- 後製版面。

因此跨工作階段、換模型、換 prompt 時，仍然可以重建同一首詩的視覺世界。

### 2. Style preset

畫風獨立存在 `config/`。

同一份 `visual_plan_json` 可以套不同 style 做 A/B test。

### 3. Runtime prompt

Prompt 不是 SSOT。

之後由：

```text
poem content
+ child explanation
+ visual_plan_json
+ selected style preset
```

在執行時組裝。

目前暫停 prompt v1，不再使用字幕安全區等後製需求干擾圖片模型。

## Age-6 A/B styles

Registry:

```text
config/image_styles_age6.json
```

### Style A — 東方淡彩水彩繪本

```text
style_id = age6_a_watercolor_ink_v1
config   = config/image_style_age6_A_watercolor_v1.json
```

特性：

- 透明水彩 + 淡墨線稿。
- 柔和、詩意、輕盈。
- 較強的自然光與季節氣氛。
- 細節中等，整體偏東方古典。

### Style B — 厚粉彩童書插畫

```text
style_id = age6_b_gouache_storybook_v1
config   = config/image_style_age6_B_gouache_v1.json
```

特性：

- 厚粉彩 / 不透明水粉。
- 色塊與物件輪廓較清楚。
- 角色辨識與場景穩定性優先。
- 明快但不走動漫或 3D 公仔風。

## A/B contract

A/B test 必須固定：

- 同一首詩。
- 同一份 `visual_plan_json`。
- 同一 Scene semantics。
- 同一 image model 與生成參數。

唯一變因：

```text
style preset
```

這樣才能判斷差異真正來自畫風。

## Asset path

A/B 圖片不能互相覆蓋，因此 style_id 是路徑的一部分：

```text
assets/pXXX/sYY/image/{style_id}/background.webp
```

例如：

```text
assets/p225/s01/image/age6_a_watercolor_ink_v1/background.webp
assets/p225/s01/image/age6_b_gouache_storybook_v1/background.webp
```

後續選定正式畫風後仍保留 style_id，以便可重現歷史版本。

## Text / bopomofo

圖片模型完全不需要知道後續會疊字。

原則：

```text
image generation = 只產適合詩意的完整圖片
text / bopomofo   = 後製處理
```

不再要求：

- 下方 25% 留白。
- subtitle safe area。
- bopomofo area。
- overlay area。

第一輪 POC 已證明這類描述容易讓模型誤產字幕底板、白色橫條甚至直接生成文字。

## Generation behavior

正式 image generator 仍需遵守：

- 已有同 style 的圖片 → SKIP。
- 不呼叫 API。
- 只有 `--force` 可覆蓋。
- 支援 `--style A|B` 或完整 style_id。
- 支援 `--age`。
- 支援 `--poem-id`。
- 支援 `--start-poem-id`。
- 支援 `--limit`。
- 支援 `--dry-run`。
- 每次 API request 保存 usage / cost / model / style_id / latency / status。
- forced regeneration 不破壞歷史 usage ledger。

## Current gate

下一步不是批次生 54 張。

先完成一首詩的 `visual_plan_json`，再對同一首完整 Scene 集做 A/B test。

PASS 時檢查：

1. 同一首詩的人物 / 場景 / 物件是否跨 Scene 一致。
2. Scene 語意是否符合原詩與兒童解釋。
3. A / B 是否只改變畫風，而沒有改變故事內容。
4. 圖片本身完整自然，不出現字幕版面或文字。
5. 選定後才進 6 歲組 25 首 / 54 Scenes 批次。
