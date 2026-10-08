# Age 7 Production Handoff

Date: 2026-10-08
Status: COMPLETE

## Scope

~~~text
approved poems                50
scenes                       127
pronunciation-sensitive QA    40 / 40 PASS
audio assets                 354 complete
background images            127 / 127 complete
local videos                  50 / 50 complete
YouTube scheduled workflow   validated
~~~

## Stable production stack

Image:
- model: gemini-3.1-flash-lite-image
- high-volume delivery: Gemini Batch API
- synchronous generator: targeted retries only
- one physical poem line = one Scene
- background only; no generated text, bopomofo, logo, UI or subtitle strip

TTS:
- model: gemini-3.8-flash-lite-tts
- voice: Kore
- canonical poem text never changes
- pronunciation/synthesis proxies remain a separate boundary layer
- Age 7 pronunciation-sensitive QA: 40/40 PASS

Text:
- Bpmf Huninn derivative
- IVS semantic source + project-font PUA alias rendering
- synthesized font glyphs retained for rare canonical characters

Video:
- 1920x1080
- 30fps
- audio-driven timeline
- run-level continuous background motion
- perspective + cubic motion baseline
- BGM stable pseudo-random by poem_id
- target BGM loudness: -35 LUFS

## YouTube baseline

Channel: @KidMoreTW

Scheduling contract:
- scheduler scope: local age group, not whole channel
- timezone: Asia/Taipei
- morning: 06:00-08:59
- evening: 17:00-19:59
- 2 videos/day per age-group batch
- scheduled uploads: private + publishAt
- notifySubscribers: false
- made_for_kids: true
- playlist: recommended_age -> "<age>歲建議"
- thumbnail: YouTube automatic

Runtime state:
- channel inventory is uploaded-state SSOT
- persisted schedule plan: `output/youtube_schedules/ageN_B.json`
- reruns skip existing uploads and retain existing schedule assignments

## Production fixes discovered during Age 7

### 1. Resumable completion can be ambiguous

Observed:
- upload progress stopped below 100%
- final response: HTTP 410 Gone
- YouTube had already committed the complete video

Permanent behavior:
- 404/410 -> reconcile against authenticated channel
- exactly one match -> recover and continue
- zero matches after bounded retries -> fail
- multiple matches -> stop

Verification tool:

~~~powershell
py scripts\youtube_verify_upload.py --poem-id POEM_ID --style B
~~~

It checks upload status, processing status and remote/local file size when available.

### 2. Same-title poems cannot share a fallback identity

Regression pair:

~~~text
p244 春怨 - 金昌緒
p269 春怨 - 劉方平
~~~

Legacy `utm_campaign=春怨` was not unique.

Permanent behavior:
- exact generated title remains supported
- new tracking URLs add `utm_content=pXXX`
- existing-video postcheck validates identity before playlist mutation

### 3. Schedule plans must self-heal without reshuffling

If corrected reconciliation reveals a poem that was previously omitted:
- keep all existing publish times unchanged
- append the poem to the next unused slot
- persist the extended plan

### 4. Playlist mistakes need targeted repair

Use:

~~~powershell
py scripts\youtube_playlist_remove.py --video-id VIDEO_ID --playlist-title "<age>歲建議"
~~~

The repair is idempotent and does not delete the video.

## Age 8 rule

Start from the Age 7 pipeline exactly as-is.

Do not redesign production stages preemptively. Only change a cross-age contract when Age 8 exposes a real incompatibility, then preserve Age 6/7 behavior as regression coverage.
