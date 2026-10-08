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

畫風獨立存在 `config/`，不進 visual plan。

Production registry：

```text
config/image_styles_production_v1.json
```

Production Style B：

```text
preset_id = b_3d_fairytale_cinematic_v1
config    = config/image_style_B_3d_fairytale_v1.json
ages      = 6, 7, 8, 9
```

架構決策：

- Style B 的視覺語言本質可跨 6–9 歲共用。
- Age 7 不另外複製一套 image pipeline，也不因檔名建立假的 age-specific preset。
- 若未來實際抽查證明某年齡需要不同視覺成熟度，再新增 age-specific preset；Scene / prompt / asset pipeline 不分叉。
- Age 6 已驗證素材必須保持相容，因此 production registry 對 age=6 仍指向原本的 `config/image_style_age6_B_3d_fairytale_v1.json`，asset namespace 仍為 `age6_b_3d_fairytale_cinematic_v1`。
- Age 7–9 使用 age-neutral `b_3d_fairytale_cinematic_v1` asset namespace。

歷史 Age 6 A/B registry 保留：

```text
config/image_styles_age6.json
```

它只用於重現既有 Age 6 A/B 實驗；正式批次預設為 Style B。

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

正式生成 production Style B：

```powershell
py scripts\generate_images.py --poem-id 226 --force
```

若要重現歷史 Age 6 Style A：

```powershell
py scripts\generate_images.py --poem-id 226 --styles A --registry config\image_styles_age6.json --force
```

批次生成全部 6 歲 approved 詩，只生成 Style B：

```powershell
py scripts\generate_images.py --age 6 --approved-only --styles B --dry-run
py scripts\generate_images.py --age 6 --approved-only --styles B
```

目前資料 gate：

```text
poems = 25
scenes = 54
styles = B only
planned_images = 54
```

批次正式執行預設採 resume 行為：正式輸出已存在就 SKIP，不重複付費。需要故意全部覆蓋時才使用 `--force`。

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

## Cross-age production gate

大量呼叫 image API 前先跑：

```powershell
py scripts\qa_age_production.py --age 7
py scripts\qa_age_production.py --age 7 --production-ready
```

第一個指令驗證資料 / Scene / visual-plan 結構；第二個額外要求 visual plan 已 approved 且存在 substantive poem world。production gate 不通過時不得開始大量產圖。


## Gemini Batch API production path

For high-volume image production, poem300 supports the Gemini Batch API in addition to the synchronous `generate_images.py` path.

Script:

~~~text
scripts/generate_images_batch.py
~~~

The semantic contract is unchanged. The batch runner imports the same `build_prompt()`, style registry, asset paths, WebP conversion, POC copy and usage ledger helpers from `generate_images.py`.

Architecture:

~~~text
poems/scenes + approved visual plan
        |
        v
same independent-Scene prompt compiler
        |
        v
JSONL input file
        |
        v
one Gemini Batch API job
        |
        v
result JSONL
        |
        v
same assets/pXXX/sYY/image/{style_id}/background.webp
~~~

Runtime request/job/result files are stored under:

~~~text
output/image_batches/
~~~

The whole `output/` directory is already gitignored.

### Pricing

As of 2026-10-08, `gemini-3.1-flash-lite-image` Batch pricing is 50% of standard API pricing:

~~~text
standard 1K image output: ~US$0.0336
batch 1K image output:    ~US$0.0168
~~~

Input text is also discounted 50%:

~~~text
standard: US$0.25 / 1M text tokens
batch:    US$0.125 / 1M text tokens
~~~

Official references:

~~~text
https://ai.google.dev/gemini-api/docs/batch-api
https://ai.google.dev/gemini-api/docs/pricing
~~~

### Submit

Preview which assets are missing without creating a job:

~~~powershell
py scripts\generate_images_batch.py submit --age 7 --approved-only --styles B --dry-run
~~~

Submit all currently missing Age 7 Style B images as one Batch API job:

~~~powershell
py scripts\generate_images_batch.py submit --age 7 --approved-only --styles B
~~~

By default, formal images already present on disk are skipped before the JSONL file is built. Do not use `--force` for normal resume production.

The submit command writes a local job pointer under `output/image_batches/latest.json`, so the job name does not need to be copied manually.

Important: creating a Batch API job is not idempotent. Do not re-run `submit` merely because the job is still processing; use `status`.

### Status

~~~powershell
py scripts\generate_images_batch.py status
~~~

The Batch API targets completion within 24 hours, although many jobs complete sooner.

### Collect

After status is `JOB_STATE_SUCCEEDED`:

~~~powershell
py scripts\generate_images_batch.py collect
~~~

Collect downloads the result JSONL and writes every image to the same production asset path used by the synchronous generator. It also updates:

~~~text
assets/poc/
assets/**/image/**/meta.json
data/image_usage.csv
~~~

Ledger rows use:

~~~text
pricing_basis = Gemini Developer API Batch paid tier
~~~

Collection is locally idempotent through the saved job state: already-collected result keys are not appended to the ledger again.

### Failure handling

After collection:

- a successful request becomes the normal production background asset;
- a per-request Batch API error is recorded in the image usage ledger and the local job state;
- missing or failed Scene images remain missing and can be resubmitted in a later Batch job;
- existing successful backgrounds are not regenerated by the next normal submit.

The synchronous `generate_images.py` path remains available for individual retry / POC work.


### Age 7 Batch production result

Final Age 7 production result:

~~~text
synchronous smoke images   11
Batch API images          116
total                     127 / 127
Batch failed                0
Batch collected           116
Batch cost         US$1.962680125
~~~

After collection, the full set was reviewed visually. Six Scene backgrounds contained generated Chinese text and were regenerated individually through the synchronous retry path:

~~~text
p121_s01
p224_s01
p260_s01
p272_s01
p295_s01
p300_s01
~~~

The replacement images passed manual review. Therefore the final Age 7 image gate is:

~~~text
completeness 127 / 127
manual QA    PASS
status       COMPLETE
~~~

Operational rule confirmed by this run:

- use Batch API for high-volume production;
- use the synchronous generator with `--scene-ids ... --force` for a small number of visual-QA retries;
- do not regenerate the whole Batch merely because individual generated images contain unwanted text or other isolated defects.
