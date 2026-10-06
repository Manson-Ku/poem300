# Gemini TTS POC

## Goal

Compare the same poem with:

- `gemini-3.8-flash-tts`
- `gemini-3.8-flash-lite-tts`

Default poem: **春曉** (`poem_id=225`).

The poem has 2 physical lines/scenes. Each scene generates two independent audio assets:

1. `poem`: original poem line
2. `explanation`: the matching 6–8 age explanation line

Therefore one full comparison run makes **8 API requests**:

```text
2 scenes x 2 audio types x 2 models = 8 requests
```

## Fixed comparison conditions

Both models use the same:

- voice: `Kore`
- output: WAV, 24 kHz
- style: Taiwan Mandarin, warm children's audiobook narration
- exact-recitation instruction

Only the model ID changes.

## Setup

PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Put the AI Studio key in:

```text
GEMINI_API_KEY=YOUR_KEY
```

`.env` is ignored by Git and must never be committed.

## Run

```powershell
py scripts\tts_poc.py
```

Or explicitly:

```powershell
py scripts\tts_poc.py --poem-id 225
```

## Outputs

Generated WAV files:

```text
output/tts_poc/<run_id>/
```

Example:

```text
p225_s01_poem_3.8-flash-tts.wav
p225_s01_explanation_3.8-flash-tts.wav
p225_s02_poem_3.8-flash-tts.wav
p225_s02_explanation_3.8-flash-tts.wav

p225_s01_poem_3.8-flash-lite-tts.wav
...
```

The `output/` directory is intentionally ignored by Git.

## Accounting ledger

Every API request appends one row to:

```text
data/tts_usage.csv
```

The raw API usage response is the accounting SSOT. Important fields include:

- `input_text_tokens`
- `output_audio_tokens`
- `total_input_tokens`
- `total_output_tokens`
- `total_tokens`
- `usage_raw_json`
- `latency_ms`
- `audio_duration_sec`
- `audio_size_bytes`
- `status`
- `error_message`

The script also calculates an estimated USD cost from a pricing snapshot dated **2026-10-06**.

This estimate uses paid Standard list price:

| Model | Text input / 1M tokens | Audio output / 1M tokens |
|---|---:|---:|
| gemini-3.8-flash-tts | $0.50 | $9.00 |
| gemini-3.8-flash-lite-tts | $0.50 | $6.00 |

If the request is served on a free tier, actual billed cost can be $0. The returned usage stats remain the source record.

## Important design rule

TTS segmentation follows the exact same Scene boundary as image generation and child explanations:

```text
1 physical content line
= 1 scene
= 1 background image
= 1 poem TTS
= 1 explanation TTS
```

Do not split again by comma or period.


## Decision — 2026-10-06

Production default is:

```text
gemini-3.8-flash-lite-tts
```

The comparison POC is retained for regression/reference only.

Observed on 春曉 (`poem_id=225`) with the same Kore voice and style:

| Model | Requests | Audio duration | Audio tokens | Estimated standard cost |
|---|---:|---:|---:|---:|
| gemini-3.8-flash-tts | 4 | 29.8 s | 746 | $0.006750 |
| gemini-3.8-flash-lite-tts | 4 | 32.0 s | 800 | $0.004836 |

Decision rationale: Flash-Lite quality is sufficient for this project and the production cost profile is preferable.
