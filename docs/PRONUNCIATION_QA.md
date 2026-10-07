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

After building it, rerender only p217:

```powershell
py scripts\render_text_overlays.py --poem-id 217 --force
```

Then the normal validator verifies the actual local font file and requires both extensions to exist:

```powershell
py scripts\qa_pronunciation.py
```
