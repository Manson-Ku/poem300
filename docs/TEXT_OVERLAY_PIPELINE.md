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
config/text_overlay_1080p_v1.json
```

座標均為 final display space，不是 2x raster space。

### Header

整體 header：

```text
x=120
y=48
w=1680
h=188
```

Title：

```text
x=120
y=48
w=1680
h=120
align=center
font max=108 px
font min=36 px
bopomofo=yes
```

Meta row：

```text
x=120
y=174
w=1680
h=62
align=center
author + poem_type
gap=48 px
font max=42 px
font min=32 px
bopomofo=no
```

## Content zone

```text
x=160
y=260
w=1600
h=560
align=center
bopomofo=yes
```

每一個實際 content line 都是獨立透明 PNG。

同一頁的 line PNG 在 content zone 內垂直置中堆疊。

Font tier：

```text
1–4 lines : target 96 px / min 72 / gap 18
5–6 lines : target 78 px / min 60 / gap 14
7–8 lines : target 60 px / min 50 / gap 10
>8 lines  : paginate, max 8 lines per page
```

實際 font size 不用字數猜測，必須載入使用者提供的 bpmfvs font 後以真實 glyph bbox 量測，再 shrink-to-fit。

每行保持 single-line，不自動改變 poems.csv 的換行語意。

如果一行在 tier minimum 仍超過 1600 px：

```text
QA FAIL: content_line_overflow
```

不可靜默壓到不可讀的小字。

## Explanation zone

Explanation 是配角，只顯示當前 Scene 的一行解釋：

```text
x=180
y=870
w=1560
h=120
align=center
font max=42 px
font min=24 px
bopomofo=no
mode=active_scene_only
```

不把整首 explanation 疊在畫面上。

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
