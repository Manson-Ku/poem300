# Age 8 Pre-production Data Gate

Date: 2026-10-08
Status: PREPRODUCTION DATA/CONTRACT READY — API generation not started

## Cohort inventory

~~~text
recommended_age             8
poems                     100
scenes                    395

scene distribution
2 scenes                   15 poems
3 scenes                    3 poems
4 scenes                   70 poems
5 scenes                    3 poems
6 scenes                    4 poems
7 scenes                    3 poems
8 scenes                    2 poems
~~~

Every Age 8 poem currently satisfies:

~~~text
physical poem lines == scenes.csv rows
physical explanation lines == scenes.csv rows
one physical line == one Scene
blank source scenes == 0
~~~

Canonical poem text and Scene boundaries were not changed.

## Visual-plan preprocessing

All 100 Age 8 poems were upgraded from:

~~~text
visual_plan_v1 / draft
~~~

to:

~~~text
visual_plan_v2 / approved
semantic_mode=source_grounded_scene_v2
~~~

The Age 7 contract is reused:

- one poem-level Tang-world context;
- soft world continuity only;
- each Scene is driven directly by its canonical source line and existing child explanation;
- do not import later Scene events;
- metaphors, historical allusions and imagined alternatives are not forced into literal events;
- no generated text/UI/subtitle content belongs in the image.

The upgrade is reproducible with:

~~~powershell
py scripts\upgrade_age8_visual_plans.py --check
~~~

The checked-in `data/poems.csv` is already the applied SSOT.

### Age 8 semantic/safety safeguards

Targeted notes were added for scenes involving:

- war, border conflict and military pursuit;
- death, graves and historical loss;
- figurative "斷腸", "招魂", "冤魂", "毒龍" and "輕生";
- legendary/supernatural allusions;
- hypothetical captivity;
- young female figures requiring non-sexualized, age-appropriate depiction;
- alcohol in historical social contexts.

These notes constrain image semantics only; they never rewrite canonical poetry.

## Pronunciation preprocessing

Age 8 pronunciation inventory:

~~~text
candidate items                 156
pronunciation-sensitive assets 123
authority_check                  0
source mismatches                0
~~~

SSOT:

~~~text
data/pronunciation_candidates_age8.json
~~~

The inventory includes polyphonic characters, historical/place/person names, rare variants and literary readings such as:

~~~text
翫月        翫 ㄨㄢˋ
盱眙        ㄒㄩ ㄧˊ
太行        行 ㄏㄤˊ
岑參        參 ㄕㄣ
濬          ㄐㄩㄣˋ
峴山        峴 ㄒㄧㄢˋ
盧綸        綸 ㄌㄨㄣˊ
參差        ㄘㄣ ㄘ
單于        單 ㄔㄢˊ
輕騎        騎 ㄐㄧˋ
占夢        占 ㄓㄢ
更衣        更 ㄍㄥ
沈香亭      沈 ㄔㄣˊ
觧釋        觧 ㄐㄧㄝˇ
~~~

`scripts/inventory_pronunciation.py` is now age-generic:

~~~powershell
py scripts\inventory_pronunciation.py --age 8
~~~

## TTS-boundary data prepared, synthesis not started

The 156 candidate items were grouped into 123 pronunciation-sensitive TTS assets and registered in:

~~~text
data/tts_pronunciation_overrides.json
~~~

Current Age 8 TTS preprocessing state:

~~~text
registered sensitive assets 123
synthesis_text proxies        0
listening QA                 pending
~~~

No synthesis proxy is pre-authorized. As in Age 7, `synthesis_text` may be introduced only after listening QA proves that instruction-only synthesis fails a target reading.

No Age 8 TTS API request has been made.

## Bopomofo / project-font gate

Age 8 pronunciation-sensitive display occurrences are now formally registered at the renderer/font boundary.

~~~text
Age 8 BPMF override occurrences added   90
new unique pronunciation aliases        24
canonical text changes                   0
~~~

Existing Age 6/7 aliases are reused when the same character/reading pair already exists.

The initial Age 8 project-font coverage check found 13 canonical Han glyph gaps:

~~~text
幷 满 猨 疎 竈 筯 羣 荆 褭 觧 輞 鄜 雊
~~~

Resolution is renderer-only:

- compatibility aliases: 幷→并, 满→滿, 猨→猿, 疎→疏, 竈→灶, 筯→箸, 羣→群, 荆→荊, 褭→裊, 觧→解;
- synthesized canonical glyphs: 輞, 鄜, 雊;
- new IVS pronunciation extension: 沈 ㄔㄣˊ for 沈香亭, borrowing pronunciation geometry from 沉.

The project font binary remains local/gitignored and is reproducibly rebuilt from the pinned upstream font with:

~~~powershell
py scripts\sync_bpmf_font.py
py scripts\patch_bpmf_font.py --force
~~~

Canonical poem text remains unchanged.

## Required local gates before TTS or image generation

Run from the repository root:

~~~powershell
git pull origin main

py scripts\qa_age_production.py --age 8 --production-ready
py scripts\inventory_pronunciation.py --age 8
py scripts\qa_font_coverage.py --age 8
py scripts\qa_pronunciation.py
~~~

Expected first two gates:

~~~text
Age 8 structural/production gate: PASS
Age 8 pronunciation inventory:
  candidates=156
  authority_check=0
  source_errors=0
  SOURCE PASS
~~~

Expected terminal preprocessing state after rebuilding the project font:

~~~text
structural gate       PASS
visual plan           PASS
pronunciation source  PASS
authority_check       0
font missing          0
pronunciation QA      PASS
~~~

A reproducible non-API GitHub Actions workflow is also checked in at `.github/workflows/preproduction_qa.yml`. It rebuilds the pinned project font and runs the same four gates. It does not call TTS or image APIs.

## API boundary

Explicitly not started:

~~~text
Gemini TTS     NOT STARTED
Gemini Image   NOT STARTED
video render   NOT STARTED
YouTube        NOT STARTED
~~~

Next step after all preprocessing gates pass:

1. resolve any Age 8 project-font/bopomofo gaps;
2. generate only pronunciation-sensitive TTS QA assets;
3. human listening QA;
4. only then proceed to full TTS and image production.
