# Production TTS Batch Generation

## Production decision

Default model:

```text
gemini-3.8-flash-lite-tts
```

Voice:

```text
Kore
```

Scene contract:

```text
1 physical poem line
= 1 Scene
= 1 poem TTS
= 1 explanation TTS
```

## Asset layout

Example for `poem_id=225`:

```text
assets/p225/
  poem.json
  s01/
    scene.json
    audio/
      poem.wav
      explanation.wav
    image/
    text/
  s02/
    ...
```

## Default behavior

The production generator is resumable.

If an expected WAV already exists, that asset is skipped and **no API request is made**.

Only `--force` overwrites existing WAV files.

This check is per asset, so if `poem.wav` exists but `explanation.wav` is missing, only the explanation is generated.

## Commands

Generate all poems:

```powershell
py scripts\generate_tts_assets.py
```

Generate one age group:

```powershell
py scripts\generate_tts_assets.py --age 6
py scripts\generate_tts_assets.py --age 7
py scripts\generate_tts_assets.py --age 8
py scripts\generate_tts_assets.py --age 9
```

Generate multiple age groups:

```powershell
py scripts\generate_tts_assets.py --age 6 7
```

Generate one poem:

```powershell
py scripts\generate_tts_assets.py --poem-id 225
```

Resume from a poem ID:

```powershell
py scripts\generate_tts_assets.py --start-poem-id 225
```

Small production batch:

```powershell
py scripts\generate_tts_assets.py --age 6 --limit 5
```

Preview without API calls:

```powershell
py scripts\generate_tts_assets.py --age 6 --dry-run
```

Explicitly regenerate existing audio:

```powershell
py scripts\generate_tts_assets.py --poem-id 225 --force
```

## Accounting

Every actual API request appends one row to:

```text
data/tts_usage.csv
```

Skipped existing assets do not append usage because no API request occurred.

Each generated Scene manifest records the production TTS model, voice and audio paths.

## Cost report

Current production-asset cost by poem:

```powershell
py scripts\tts_cost_report.py
```

Filter by age:

```powershell
py scripts\tts_cost_report.py --age 6
```

Cumulative API spend including forced regenerations:

```powershell
py scripts\tts_cost_report.py --mode spend
```

The default `current` mode keeps only the latest completed production request for each Scene/audio type. This prevents a `--force` regeneration from double-counting the current asset cost.
