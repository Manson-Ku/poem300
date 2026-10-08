# Age 8 Pre-production Data Gate

Date: 2026-10-08
Status: PREPROCESSING — API generation not started

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

No Age 8-specific bopomofo override has been added yet.

Reason: a pronunciation candidate does not automatically imply that the project font needs an override. The project font must first be checked for:

1. canonical Han glyph coverage;
2. whether an already-established pronunciation alias can be reused;
3. whether a new IVS/PUA pronunciation alias or synthesized glyph is genuinely required.

Current Repo-side count:

~~~text
Age 8 bopomofo overrides  0
~~~

This is an intentional pending gate, not a PASS.

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

Font/pronunciation QA output determines the next preprocessing action. Do not start Age 8 TTS or image generation until the font/bopomofo gate is resolved.

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
