# Image Pipeline

## Scope

第一階段只完成 **recommended_age = 6** 的完整影片產線。

圖片的最小單位與 TTS 完全一致：

```text
1 physical poem line
= 1 Scene
= 1 background image
= 1 poem TTS
= 1 explanation TTS
= 1 text overlay
```

不依逗號、句號再次切圖。

## Style v1

Preset:

```text
storybook_cn_age6_v1
```

正式設定檔：

```text
config/image_style_age6_v1.json
```

### 視覺定位

- 6 歲兒童繪本插畫。
- 淡中式古典氣氛。
- 柔和的手繪 / painterly 質感。
- 明亮、乾淨、容易辨識。
- 不走寫實攝影。
- 不走動漫角色風。
- 悲傷、離別、孤獨等情境仍保持兒童可接受的柔和呈現。

## Composition contract

主版固定：

```text
16:9
```

畫面分工：

```text
upper / middle 75% = narrative image
lower 25%          = text-safe zone
```

下方 25% 不是純色字幕條，而是要求：

- 細節較少。
- 對比不要過強。
- 不放重要人物臉部、手勢、動物或關鍵物件。
- 不承載主要敘事動作。

這是為後續 `poem_bpmf.png` 疊圖預留。

## Prompt construction

每個 Scene 的 prompt 必須同時使用：

1. poem title / author
2. current original line
3. current child explanation line
4. style preset
5. same-poem continuity key

基本語意：

```text
請把「這一整個換行段落」轉成一張兒童繪本畫面。
原詩決定詩意與核心物件；兒童解釋協助把抽象語意具象化。
不要依逗號拆成兩個分鏡。
```

Prompt 不要求模型生成任何中文字。

## Prompt v1 template

```text
Create one 16:9 children's picture-book background illustration for a Tang-poetry learning video.

Poem: {title}
Author: {author}
Scene: {scene_no}
Original poem line: {original_line}
Child-friendly meaning: {child_explanation_line}

Visualize the meaning of this whole line as ONE coherent scene, not separate panels and not separate shots for each punctuation phrase.

Style: gentle East Asian classical children's picture book, soft painterly texture, warm bright clean palette, simple shapes, clear emotional storytelling suitable for a six-year-old child. Keep the illustration elegant but easy to understand.

Composition: place the important subject and narrative action mainly in the upper and middle area. Keep the lower 25% visually calm and relatively low-detail as a safe area for later bopomofo Chinese text overlay. Do not put important faces, hands, animals, key props, or narrative action in the lower text-safe area.

Continuity: all scenes of this poem should look like pages from the same picture book. Reuse the same character appearance, clothing, hairstyle, architecture, season, palette, and time-of-day logic whenever they recur.

Do not generate any text, Chinese characters, bopomofo, letters, subtitles, logos, watermarks, UI, borders, frames, or speech bubbles. No photorealism, no anime style, no horror, no graphic violence, no malformed hands or duplicated limbs.
```

## Asset path

```text
assets/pXXX/sYY/image/background.webp
```

例如：

```text
assets/p225/s01/image/background.webp
assets/p225/s02/image/background.webp
```

## Generation behavior

正式圖片生成器必須與 TTS 採相同行為：

- 預設檔案已存在 → SKIP。
- 不呼叫 API。
- 只有 `--force` 可以覆蓋。
- 支援 `--age`。
- 支援 `--poem-id`。
- 支援 `--start-poem-id`。
- 支援 `--limit`。
- 支援 `--dry-run`。
- 每次 API request 需保留 usage / cost / provider / model / latency / status。
- forced regeneration 不應破壞歷史 usage ledger。

## Two-stage workflow

### Stage 1 — prompt manifest

先執行：

```powershell
py scripts\build_image_prompts.py --age 6
```

產出：

```text
data/image_prompts_age6.csv
```

用途：

- 人工抽查 prompt。
- 鎖定 style v1。
- 不產生圖片、不花 API 費。

### Stage 2 — image generation

待 POC 圖片 PASS 後才接正式 image provider。

正式生成腳本預計：

```text
scripts/generate_image_assets.py
```

此腳本讀取 prompt manifest，圖片寫入 `assets/pXXX/sYY/image/background.webp`。

## POC gate

正式批次 age 6 前，先挑至少 4 個不同視覺場景：

- 清晨 / 明亮場景
- 夜晚 / 月色場景
- 有人物場景
- 純自然 / 山水場景

PASS 條件：

1. 六歲兒童能快速看懂核心情境。
2. 畫風像同一套繪本。
3. 同一首詩角色一致。
4. 下方 25% 可疊注音文字。
5. 無任何 AI 生成文字。
6. 不需要靠字幕才能理解主要畫面。
