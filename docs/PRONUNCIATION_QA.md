# Pronunciation QA

Scope for v1: recommended_age = 6 (25 poems).

## Policy

Written bopomofo uses lexical/citation readings. Ordinary speech tone sandhi for 一 / 不 is not encoded as a different written bopomofo glyph.

For bpmfvs IVS fonts:

- first listed reading = default glyph
- second reading = U+E01E1
- third reading = U+E01E2
- etc.

Machine-readable sources:

```text
data/bopomofo_overrides.json
data/pronunciation_qa_age6.json
data/tts_pronunciation_overrides.json
```

Validator:

```powershell
py scripts\qa_pronunciation.py
```

## Reviewed IVS corrections

The age-6 set currently contains 15 explicit non-default glyph selections across these contexts:

- 相送 / 相思 / 相見 / 相識 -> 相 ㄒㄧㄤ
- 處處 / 不知處 / 何處 -> 處 ㄔㄨˋ
- 荷笠 -> 荷 ㄏㄜˋ
- 彈琴 / 不彈 -> 彈 ㄊㄢˊ
- 懷君屬秋夜 -> 屬 ㄓㄨˇ
- 少小 -> 少 ㄕㄠˋ
- 鬢毛衰 -> 衰 ㄘㄨㄟ

These are encoded in `data/bopomofo_overrides.json` and are applied by `render_text_overlays.py`.

After pulling these overrides, rerender the age-6 text assets with:

```powershell
py scripts\render_text_overlays.py --age 6 --approved-only --font-path fonts\BpmfHuninn-Poem300-Regular.ttf --force
```

## TTS-sensitive readings

`generate_tts_assets.py` now reads `data/tts_pronunciation_overrides.json` and appends a pronunciation-only instruction to the existing speech style while keeping the spoken source text unchanged.

To regenerate only reviewed pronunciation-sensitive assets:

```powershell
py scripts\generate_tts_assets.py --age 6 --types title author poem --pronunciation-qa-only --dry-run
py scripts\generate_tts_assets.py --age 6 --types title author poem --pronunciation-qa-only --force
```

This includes sensitive author/title/place-name readings such as:

- 劉長卿：長 ㄔㄤˊ
- 行宮：行 ㄒㄧㄥˊ
- 登樂遊原：樂 ㄌㄜˋ
- 鹿柴：柴 ㄓㄞˋ
- 返景：景 ㄧㄥˇ

TTS QA is still auditory QA: after regeneration, the affected WAV files should be spot-checked by listening. The registry guarantees the intended reading instruction was supplied; it does not perform speech recognition on the output.

## Deterministic TTS fallback for literary readings

Gemini TTS did not reliably obey pronunciation-style instructions for two p217 literary readings:

```text
鹿柴  -> 柴 ㄓㄞˋ
返景  -> 景 ㄧㄥˇ
```

For these two assets only, the TTS registry now separates:

```text
source_text     = canonical poem text
synthesis_text  = pronunciation-only homophonic proxy sent to TTS
```

Current proxies:

```text
鹿柴
  source_text    = 鹿柴
  synthesis_text = 鹿寨

返景入深林，復照青苔上。
  source_text    = 返景入深林，復照青苔上。
  synthesis_text = 返影入深林，復照青苔上。
```

The WAV asset still belongs to the canonical source text and all manifests/scene semantics remain unchanged. The proxy exists only at the TTS synthesis boundary.

Regenerate only these p217 pronunciation-sensitive assets:

```powershell
py scripts\generate_tts_assets.py --poem-id 217 --types title poem --pronunciation-qa-only --dry-run
py scripts\generate_tts_assets.py --poem-id 217 --types title poem --pronunciation-qa-only --force
```

The dry-run must show:

```text
proxy='鹿寨'
proxy='返影入深林，復照青苔上。'
```

After generation, these two WAV files require human listening QA before video composition.


## Raster-safe pronunciation aliases

The semantic pronunciation selector remains bpmfvs IVS, but the PNG renderer does not emit Han + IVS directly.

Observed failure mode with Pillow/FreeType:

```text
Han + U+E01E1
```

can leave the variation selector visible as a rectangular fallback glyph instead of selecting the intended annotated glyph.

Therefore the local project font exposes each pronunciation override through a stable BMP Private Use Area alias. The renderer substitutes only at raster time:

```text
canonical text: 返景入深林
semantic selector: 景 + U+E01E1 = ㄧㄥˇ
raster text: 返 + U+E901 + 入深林
```

The PUA glyph itself is the complete annotated glyph: original Han character plus the selected bopomofo. No manual bopomofo compositing is used.

Current aliases are stored directly in:

```text
data/bopomofo_overrides.json
```

and generated into:

```text
fonts/BpmfHuninn-Poem300-Regular.ttf
```

by:

```powershell
py scripts\patch_bpmf_font.py --force
```

This currently produces 9 unique raster aliases covering all 17 age-6 pronunciation override occurrences.

## Project font extensions

The upstream bpmfvs font does not expose two literary readings required by p217:

1. 鹿柴：柴 ㄓㄞˋ
2. 返景：景 ㄧㄥˇ

The project resolves these without manual bopomofo composition by creating a local OFL derivative font.

Spec:

```text
data/bpmf_font_extensions.json
```

Build:

```powershell
py scripts\patch_bpmf_font.py
```

Output:

```text
fonts/BpmfHuninn-Poem300-Regular.ttf
```

The derived font adds IVS U+E01E1 for the two missing readings while preserving the original default glyphs.

After rebuilding the project font, rerender the whole age-6 set because all pronunciation overrides now use raster-safe aliases:

```powershell
py scripts\render_text_overlays.py --age 6 --approved-only --force
```

Then the normal validator verifies the actual local font file and requires both extensions to exist:

```powershell
py scripts\qa_pronunciation.py
```

## p226 疑是地上霜 TTS correction

Human listening QA found that Gemini TTS pronounced `疑` as ㄧˇ in:

```text
疑是地上霜。
```

The required reading is:

```text
疑 ㄧˊ
```

The TTS registry now uses a pronunciation-only homophonic synthesis proxy:

```text
source_text    = 疑是地上霜。
synthesis_text = 宜是地上霜。
```

Regenerate only this pronunciation-sensitive p226 poem asset:

```powershell
py scripts\generate_tts_assets.py --poem-id 226 --types poem --pronunciation-qa-only --dry-run
py scripts\generate_tts_assets.py --poem-id 226 --types poem --pronunciation-qa-only --force
```

Because the WAV duration may change, rebuild the timeline before re-rendering the MP4:

```powershell
py scripts\build_video_timeline.py --poem-id 226 --style B
py scripts\render_video.py --poem-id 226 --style B --force
```



## Age 7 pronunciation / font gate

Age 7 reviewed pronunciation inventory:

~~~text
data/pronunciation_candidates_age7.json
version = pronunciation_candidates_age7_v2
authority_check = 0
~~~

Authority decisions for the previously unresolved cases:

- p006 「岱宗夫如何」：夫 = ㄈㄨˊ。
- p085 「城闕」：闕 = ㄑㄩㄝˋ。
- p221 「寒梅着花未」：着 = ㄓㄨㄛˊ。
- p292 「令狐郎中」：令 = ㄌㄧㄥˋ。
- p303 author 「張泌」：production target 暫採泌 = ㄇㄧˋ，並保留 human listening QA。

Age 7 canonical text 另有以下 historical / variant Han codepoints：

~~~text
㡬 却 爲 藁 螘 蟢 衆 隣
~~~

處理原則：

1. canonical poem text 不改字。
2. 先同步 pinned upstream BpmfHuninn。
3. bpmfvs pronunciation source data 雖收錄 `螘`、`蟢`，但 pinned BpmfHuninn binary 的 cmap 實測仍缺這兩個 glyph；source-data coverage 與 font-binary coverage 必須分開 QA。
4. project derivative 對一般異體缺字建立 renderer-only compatibility aliases：

~~~text
㡬 -> 幾
却 -> 卻
爲 -> 為
藁 -> 稿
衆 -> 眾
隣 -> 鄰
~~~

5. `螘`、`蟢` 不用錯字代替；`bpmf_font_extensions_v3` 在 project font 內以同字體既有 Han components 合成 canonical 字形：`螘 = 虫 + 豈`、`蟢 = 虫 + 喜`。注音仍直接複用 bpmfvs 既有 phonetic components，不手工拼注音。
6. p293 canonical 「爲有」需要 ㄨㄟˋ；此 occurrence 不使用 `爲 -> 為` 的 default ㄨㄟˊ glyph，而是使用 PUA pronunciation alias，來源為 `為 + U+E01E1`。
7. renderer proxy / PUA / compatibility alias / synthesized glyph 都不回寫 canonical source。

Pinned base-font sync：

~~~powershell
py scripts\sync_bpmf_font.py --force
~~~

Pin：

~~~text
upstream repo   ButTaiwan/bpmfvs
upstream commit fa20c2bb5e2986856974f00c93a662d0805c92a0
font blob SHA1  38c3f0ea596bf4bbf6488cd1c7b5e77925a2199f
~~~

完整 local rebuild / QA：

~~~powershell
py scripts\sync_bpmf_font.py --force
py scripts\patch_bpmf_font.py --force
py scripts\inventory_pronunciation.py
py scripts\qa_font_coverage.py --age 7
py scripts\qa_pronunciation.py
~~~

TTS：

- reviewed Age 7 pronunciation assets 已寫入 `data/tts_pronunciation_overrides.json`。
- 目前只登錄 pronunciation instruction，不預先新增 synthesis proxy。
- 只有 human listening QA 證明模型仍念錯時，才新增 homophonic `synthesis_text`。
- p303 author 張泌為必要 listening QA case。


## Age 7 TTS listening QA round 1

Production listening QA after generating the 40 pronunciation-sensitive Age 7 assets:

~~~text
generated = 40
passed    = 35
failed     = 5
~~~

Failed assets and observed output:

~~~text
p006_s01  夫 ㄈㄨˊ   -> actual ㄈㄨ
p085_s01  闕 ㄑㄩㄝˋ -> actual ㄑㄩㄝˊ
p263 author 參 ㄕㄣ  -> actual ㄘㄢ
p293_s01  爲 ㄨㄟˋ  -> actual ㄨㄟˊ
p308 title 塞 ㄙㄞˋ -> actual ㄙㄞ
~~~

These five now use the same deterministic TTS-boundary fallback already validated in Age 6:

~~~text
p006_s01
source_text    = 岱宗夫如何，齊魯青未了。
synthesis_text = 岱宗符如何，齊魯青未瞭。

p085_s01
source_text    = 城闕輔三秦，風煙望五津。
synthesis_text = 城卻輔三秦，風煙望五津。

p263 author
source_text    = 岑參
synthesis_text = 岑申

p293_s01
source_text    = 爲有雲屏無限嬌，鳳城寒盡怕春宵。
synthesis_text = 為有雲屏無限嬌，鳳城寒盡怕春宵。

p308 title
source_text    = 出塞
synthesis_text = 出賽
~~~

Canonical poem / author source remains unchanged. The proxy exists only at the TTS synthesis boundary.

Targeted retry:

~~~powershell
py scripts\generate_tts_assets.py --age 7 --types title author poem --synthesis-proxy-only --dry-run
py scripts\generate_tts_assets.py --age 7 --types title author poem --synthesis-proxy-only --force
~~~

Expected planned API requests: 5.

Do not rerun all 40 pronunciation-sensitive assets after 35 have passed listening QA.


## Age 7 TTS listening QA round 2

Round-1 synthesis proxies fixed 4/5 failed assets. The remaining failure is:

~~~text
p293_s01
target: 爲 ㄨㄟˋ
round-1 proxy: 為有雲屏無限嬌，鳳城寒盡怕春宵。
actual: ㄨㄟˊ
~~~

Reason: replacing variant `爲` with standard `為` does not remove the lexical ambiguity. Gemini still interprets the literary phrase `為有` and chooses ㄨㄟˊ.

Round-2 deterministic proxy:

~~~text
source_text    = 爲有雲屏無限嬌，鳳城寒盡怕春宵。
synthesis_text = 未有雲屏無限嬌，鳳城寒盡怕春宵。
~~~

`未` is an unambiguous ㄨㄟˋ homophone for the synthesis boundary. Canonical text remains `爲有...`.

Regenerate only this asset:

~~~powershell
py scripts\generate_tts_assets.py --poem-id 293 --types poem --synthesis-proxy-only --dry-run
py scripts\generate_tts_assets.py --poem-id 293 --types poem --synthesis-proxy-only --force
~~~

Expected planned API requests: 1.


## Age 7 TTS listening QA final

Human listening QA is complete:

~~~text
reviewed pronunciation-sensitive assets = 40
PASS                                  = 40
FAIL                                   = 0
synthesis_text fallbacks               = 5
status                                 = PASS
~~~

The final p293 retry uses:

~~~text
source_text    = 爲有雲屏無限嬌，鳳城寒盡怕春宵。
synthesis_text = 未有雲屏無限嬌，鳳城寒盡怕春宵。
target         = 爲 ㄨㄟˋ
result         = PASS
~~~

These 40 WAV files are now accepted production assets. Subsequent full Age 7 TTS runs must use resume behavior and must not use `--force` unless a new listening-QA defect is found.

## Age 8 priority listening QA round 1（2026-10-09）

Priority review set:

~~~text
Tier 1 user listening review       = 13
Tier 1 PASS                        = 4
Tier 1 FAIL                        = 9

Tier 2 assistant audio review      = 16
Tier 2 PASS                        = 16
Tier 2 FAIL                        = 0

priority reviewed total            = 29
priority PASS                      = 20
priority FAIL                      = 9
~~~

Tier 2 uploaded WAV review passed the registered targets:

~~~text
p029 title       盱眙       ㄒㄩ ㄧˊ
p099_s01 poem    鄜州       鄜 ㄈㄨ
p126-p130 author 劉長卿     長 ㄔㄤˊ
p138 author      盧綸       綸 ㄌㄨㄣˊ
p163 author      崔顥       顥 ㄏㄠˋ
p249-p252 author 盧綸       綸 ㄌㄨㄣˊ
p278-p279 author 張祜       祜 ㄏㄨˋ
p299 author      鄭畋       畋 ㄊㄧㄢˊ
~~~

Tier 1 failures registered for deterministic TTS-boundary retry:

~~~text
p034_s02
source: 出塞入塞寒，處處黃蘆草。
proxy:  出賽入賽寒，處處黃蘆草。

p077_s03
source: 欲渡黃河冰塞川，將登太行雪满山。
proxy:  欲渡黃河冰澀川，江登太杭雪满山。

p092 author
source: 岑參
proxy:  岑申

p100_s04
source: 明朝有封事，數問夜如何。
proxy:  明昭有封事，朔問夜如何。

p151_s02
source: 參差連曲陌，迢遞送斜暉。
proxy:  嵾疵連屈陌，迢遞送斜暉。

p152_s04
source: 天涯占夢數，疑誤有新知。
proxy:  天涯沾夢朔，疑誤有新知。

p251_s01
source: 月黑雁飛高，單于夜遁逃。
proxy:  月黑雁飛高，蟬于夜遁逃。

p251_s02
source: 欲將輕騎逐，大雪滿弓刀。
proxy:  欲江輕記逐，大雪滿弓刀。

p294_s01
source: 乘興南遊不戒嚴，九重誰省諫書函。
proxy:  乘性南遊不戒嚴，九崇誰醒諫書函。
~~~

Canonical title / author / poem text remains unchanged. These substitutions exist only
at the synthesis boundary.

### Production learning

The highest-risk Tier 1 sample produced 9 failures out of 13 instruction-only
assets. This is strong evidence that pronunciation instructions alone are an
inefficient first-pass strategy for known high-risk polyphones.

Future direction:

- ordinary TTS remains canonical-text-first;
- registered pronunciation-sensitive assets should be **proxy-first** when a
  deterministic same-reading homophonic proxy exists;
- source text and synthesis text remain separate data contracts;
- a proxy still requires listening QA; it is not treated as self-validating;
- rare proxy characters such as `嵾 ㄘㄣ` receive explicit retry QA rather than
  being assumed correct.

## Age 8 priority listening QA final（2026-10-09）

The nine proxy retries were regenerated and all passed human listening QA.

~~~text
priority reviewed assets = 29
priority PASS            = 29
priority FAIL            = 0
proxy assets             = 9
remaining unreviewed     = 94
~~~

Priority listening gate is PASS. The full Age 8 pronunciation-sensitive gate
remains pending until the other 94 registered assets are reviewed.
