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
py scripts\render_text_overlays.py --age 6 --approved-only --font-path fonts\BpmfHuninn-Regular.ttf --force
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

## Known font gaps

The current bpmfvs reading table does not expose two required historical/literary readings used by p217:

1. 鹿柴：柴 must be ㄓㄞˋ, but bpmfvs currently exposes only ㄔㄞˊ for 柴.
2. 返景：景 must be ㄧㄥˇ, but bpmfvs currently exposes only ㄐㄧㄥˇ for 景.

These are recorded as `font_gap` in:

```text
data/pronunciation_qa_age6.json
```

Because the project policy is currently "font renders Han + bopomofo as one glyph; no manual bopomofo composition", the validator intentionally blocks a full pronunciation PASS until these two font gaps have an explicit product decision.

Use only for diagnostics:

```powershell
py scripts\qa_pronunciation.py --allow-font-gaps
```

Do not treat that flag as production approval.
