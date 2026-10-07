# Text Overlay Pipeline

## Goal

為 1920×1080 唐詩影片建立可重用的透明文字 PNG。

文字資產與背景圖完全分離。字型檔只存在 local，不進 Git。

來源 SSOT：

```text
data/poems.csv
data/scenes.csv
```

欄位：

- author
- poem_type
- title
- content
- child_explanation_6_8

## Bopomofo policy

採用 ButTaiwan/bpmfvs 相容的注音 IVS 字型。

正式規則：

- title：有注音。
- content：有注音。
- author：不加注音。
- poem_type：不加注音。
- child_explanation_6_8：不加注音，作為輔助文字。
- 不由程式自行拼接「中文字 + 注音」。
- 字型本身負責中文字左側、注音右側的 glyph 呈現。
- 多音字以 IVS selector 校正。

bpmfvs 規格以單一字型檔支援多音字，IVS selector 跟在漢字後方選擇不同讀音。

校正 SSOT：

```text
data/bopomofo_overrides.json
```

每筆 override 必須記錄：

- poem_id
- field = title | content
- line_no
- char_index
- character
- selector（例如 U+E01E1）
- expected_reading
- note

renderer 套用前必須先驗證 source character 與 char_index；不一致就 QA FAIL，不可靜默套用。

## Local font

字型檔放 local：

```text
fonts/
```

`.gitignore` 已排除：

- fonts/
- *.ttf
- *.otf
- *.woff
- *.woff2

未來 renderer 接受：

```text
--font-path
```

或環境變數：

```text
BPMF_FONT_PATH
```

## Current master-file audit

掃描目前 313 首主檔：

```text
poems                       313
scenes                      1626
poem line count             min 2 / median 4 / max 60
poems with > 8 scenes       36
poems with > 12 scenes      22
poems with > 24 scenes       5

title chars                  p95 11 / max 39
content line chars           p95 14
explanation line chars       p95 30 / max 58
```

6 歲組目前：

```text
poems                        25
scenes                       54
poem line count              max 4
title chars                  max 7
content line chars           max 14
explanation line chars       max 30
```

所以 6 歲組可直接使用單頁配置；全 313 首不能只靠「行數越多、字越小」解決。

主檔存在：

- 60 行、45 行、33 行等長詩。
- 6 個 content line 超過 28 個主要字元。
- 最長單行為 254 字，屬序文/長段落型內容。

因此正式策略：

```text
<= 8 lines  -> 同一 content page 自動縮放
> 8 lines   -> 分頁，每頁最多 8 lines
single line still cannot fit at minimum font -> QA FAIL
```

不得把 60 行長詩硬縮成一張 1080p 畫面。

### Blank physical lines

目前有 6 首詩保留一個空白實際換行，`data/scenes.csv` 也保留成 blank Scene。

例如序文與正文之間：

```text
p067_s01 = 序文
p067_s02 = blank separator
p067_s03 = 正文第一行
```

文字 renderer 對 blank Scene：

- 不產文字 PNG。
- manifest 保留 Scene。
- 標記 `render=false` / `kind=blank_separator`。
- 可作為分頁或章節間距訊號。

## 1920×1080 layout

SSOT：

```text
config/text_overlay_1080p_v2.json
```

### 核心顯示模型

不是「一句換掉上一句」，而是 **content 區內逐段累積**。

每個 Scene 仍然擁有一張單行透明 PNG，但影片合成時：

```text
S01 -> slot 1 出現，播放 S01 poem.wav
S02 -> slot 2 出現，S01 保留，播放 S02 poem.wav
S03 -> slot 3 出現，S01/S02 保留，播放 S03 poem.wav
S04 -> slot 4 出現，S01/S02/S03 保留，播放 S04 poem.wav
S05 -> content 區換頁，清空舊四行，slot 1 出現 S05
```

Title / Author 不跟著 content page 清除，而是整支影片常駐。

這樣「換頁」只發生在 content zone，不是整個 1920×1080 畫面換版。

### 為什麼固定 4 slots

掃描主檔後，不採用「整首詩越長，所有字就越小」：

- 313 首中有 60 行、45 行、33 行等長詩。
- 6 歲與 7 歲組本身最多 4 行。
- 8/9 歲長詩可自然切成多個 content page。
- 幼童影片應優先維持可讀字級，而不是把十幾行硬塞在同一頁。

所以 v2 固定：

```text
content_page_capacity = 4
```

超過 4 行只做 content-zone page turn。

### Header：整支影片固定

Title：

```text
x=120
y=44
w=1680
h=118
align=center
bopomofo=yes
persistence=whole_video
font candidates=108,96,84,72,60,48,36
```

Author：

```text
x=120
y=164
w=1680
h=58
align=center
bopomofo=no
persistence=whole_video
font candidates=48,44,40,36,32
```

Poem type：

```text
x=120
y=218
w=1680
h=42
align=center
bopomofo=no
persistence=intro_end_only
font candidates=34,30,28,26
```

### Content zone

```text
x=160
y=286
w=1600
h=500
align=center
bopomofo=yes
```

固定 4 個位置：

```text
slot 1 center_y = 340
slot 2 center_y = 462
slot 3 center_y = 584
slot 4 center_y = 706
```

重要：slot 一開始就固定，所以新一句出現時，舊句**不能因重新置中而移動**。

每個 content page 使用同一個 font size。不是每行各自縮放。

Font size 只從固定候選值中選：

```text
96, 92, 88, 84, 80, 76, 72 px
```

選法：

1. 先用實際 `BpmfHuninn-Regular.ttf` glyph bbox 量測該 page 最寬的一行。
2. 從 96 px 往下選第一個可完整放進 1600 px 的固定字級。
3. 同一 page 所有行使用相同字級。
4. **整首詩有幾行，不參與 font-size 計算。**
5. 72 px 仍放不下 -> `QA FAIL: content_line_overflow`。

也就是「字級由該頁實際最長一行決定；長詩靠區內換頁解決」。

### Blank physical line

主檔有 6 首包含空白 Scene，常見於序文與正文間。

規則：

- blank Scene 不產文字 PNG。
- manifest 仍保留。
- 若當前 content page 已有內容，blank Scene 強制結束該頁。
- 下一個非空 Scene 從新 page 的 slot 1 開始。
- blank Scene 本身不播放 poem / explanation TTS。

### Explanation zone

Explanation 是配角：

```text
x=180
y=844
w=1560
h=128
align=center
bopomofo=no
persistence=active_scene_only
font candidates=42,38,34,30,28,26,24
```

只在 `session_explain` 的該 Scene explanation audio 播放期間顯示。

不取代 content；content page 已出現的詩句仍保留。

## Transparent PNG assets

Poem-level：

```text
assets/pXXX/text/title_bpmf.png
assets/pXXX/text/author.png
assets/pXXX/text/poem_type.png
assets/pXXX/text/manifest.json
```

Scene-level：

```text
assets/pXXX/sYY/text/content_bpmf.png
assets/pXXX/sYY/text/explanation.png
```

PNG：

- transparent background
- tight bbox + padding
- renderer 以 2x raster scale 產生
- manifest 記錄 1920×1080 display-space anchor / bbox / page / font size

## Rendering appearance

由於背景圖明暗不可預測，文字 PNG 自帶可讀性保護：

Title / Content：

- warm white fill
- dark stroke 3 px
- soft shadow

Author / poem_type / Explanation：

- soft off-white fill
- dark stroke 2 px
- soft shadow

以上尺寸均指 1080p display space；實際 2x PNG 渲染時等比例放大。

## Planned renderer contract

主腳本預定：

```text
scripts/render_text_overlays.py
```

至少支援：

```powershell
py scripts\render_text_overlays.py --poem-id 226 --font-path fonts\<font>.ttf --dry-run
py scripts\render_text_overlays.py --age 6 --font-path fonts\<font>.ttf
```

輸出前 gate：

1. content / child_explanation_6_8 physical-line count 一致。
2. Scene 順序與 poems.csv physical lines 一致。
3. IVS override source character 全部匹配。
4. title/content 實際 glyph bbox 可放進對應區域。
5. overflow 不可靜默通過。

## Video material completeness

完成 text overlay 後，單首詩的主要素材層會包含：

- background image
- title bopomofo PNG
- author PNG
- poem_type PNG
- content bopomofo PNG per Scene
- explanation PNG per Scene
- poem / explanation TTS
- text / image manifest

剩餘主要缺口：

- author TTS
- title TTS
- final timeline / composition script
