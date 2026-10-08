# 開發狀態與 Production Baseline

更新日期：2026-10-07

本文件是 poem300 專案的當前開發狀態 / 下一階段筆記。功能細節仍以各專門文件與 config / script 為 SSOT；本文件負責記錄目前已驗證到哪裡、哪些決策已進入 production baseline、下一批內容應如何推進。

---

## 1. 當前里程碑

### Age 6 production：COMPLETE

6 歲組已完成第一個完整 end-to-end production cycle：

~~~text
poem data
  -> Scene
  -> visual plan
  -> background image
  -> pronunciation QA
  -> TTS
  -> BPMF/text overlay
  -> audio-driven timeline
  -> FFmpeg video composition
  -> poem-level BGM
  -> MP4
  -> YouTube OAuth
  -> metadata
  -> publication
  -> made-for-kids
  -> recommended_age playlist
  -> channel inventory / duplicate avoidance
  -> resumable batch upload
~~~

Age 6 資料：

~~~text
poems            25
approved         25
scenes           54
nonblank scenes  54
blank scenes      0
~~~

Age 6 YouTube batch 最終結果：

~~~text
already_uploaded=2
existing_postchecked=2
existing_post_failed=0
uploaded_now=23
failed=0
waiting_local=0
PASS
~~~

因此以「25 首都有完成影片，且 25 首都已存在於 KidMoreTW 頻道」作為 Age 6 完成標準，已達成。

注意：第一支 p225〈春曉〉是歷史 private POC。batch reconciliation 對既有影片不自動修改 privacy，所以 Age 6 的 channel completeness 已完成；若未來要求 25/25 全部 public，仍需另做 publication-state audit / update。

---

## 2. 全資料集現況

data/poems.csv：

~~~text
total poems = 313
~~~

data/scenes.csv：

~~~text
total scenes = 1,626
~~~

依 recommended_age：

| Age | Poems | Approved | Draft | Scenes | Nonblank | Blank |
|---:|---:|---:|---:|---:|---:|---:|
| 6 | 25 | 25 | 0 | 54 | 54 | 0 |
| 7 | 50 | 50 | 0 | 127 | 127 | 0 |
| 8 | 100 | 0 | 100 | 395 | 395 | 0 |
| 9 | 138 | 0 | 138 | 1,050 | 1,044 | 6 |

重要含義：

- 6 歲組是目前唯一已 approved 且完成 production delivery 的 age group。
- 7 歲 visual plans 已完成 source-grounded visual_plan_v2 並 approved；8 / 9 歲仍為 draft。
- 9 歲資料含 6 個 blank separator Scenes；這些是既有 Scene 結構，不應在重建時被默默刪除。

---

## 3. Data / Scene SSOT

Poem SSOT：

~~~text
data/poems.csv
~~~

一首詩一列，主要包含 poem_id、author、poem_type、title、content、child_explanation_6_8、popularity_level、recommended_age、visual_plan_* 與 visual_* 欄位。

Scene SSOT：

~~~text
data/scenes.csv
~~~

核心 invariant：

~~~text
一個實際換行段落 = 一個 Scene
~~~

不能用逗號、句號或其他標點再切 Scene。content 與 child_explanation_6_8 以 physical line 對應。Scene 表核心欄位由 script 展開，不手工破壞對應關係。

---

## 4. Image production baseline

主程式：

~~~text
scripts/generate_images.py
~~~

目前 production architecture：

~~~text
poem-world + independent-scene
~~~

原則：

- 每首詩有共用 visual world。
- 每個 Scene 獨立呼叫 image model。
- 不做 multi-turn image editing。
- 不要求漫畫式強 continuity。
- Scene 圖只負責表達當前詩句。
- 圖片不得生成字幕、注音、logo、UI、卡片、對話框等文字層元素。

目前模型：

~~~text
gemini-3.1-flash-lite-image
~~~

Age 6 production style：

~~~text
B = age6_b_3d_fairytale_cinematic_v1
~~~

相關 SSOT：

~~~text
config/image_styles_age6.json
config/image_style_age6_B_3d_fairytale_v1.json
docs/IMAGE_PIPELINE.md
~~~

Age 6 Style B 已完成 25 poems / 54 Scenes 的素材基線。

### Age 7+ architecture decision

2026-10-07 已完成跨 age 判斷，採 **age-neutral production Style B**：

~~~text
config/image_styles_production_v1.json
config/image_style_B_3d_fairytale_v1.json
~~~

原則：

1. Style B 的視覺語言可直接跨 6–9 歲共用。
2. Age 7 不新增一套平行 image pipeline。
3. Age 6 的既有 production config 與 asset namespace 保持不變，避免已完成素材失效。
4. Age 7–9 使用 neutral `b_3d_fairytale_cinematic_v1` namespace。
5. 未來只有在抽查證明確實需要不同視覺成熟度時，才新增 age-specific preset。

歷史 Age 6 A/B registry `config/image_styles_age6.json` 保留，只用於重現既有 A/B 實驗。

### Age 7 inventory gate

本輪對 main 的 50 首 / 127 Scenes 完整盤點結果：

~~~text
poems                         50
scenes                       127
nonblank scenes              127
blank scenes                   0
scene distribution     2:36 / 3:1 / 4:13

visual_plan_status
  draft                       50
  approved                     0

visual_plan_version
  visual_plan_v1              50

Scene/content mapping errors   0
invalid visual_plan_json       0
entity reference errors        0
placeholder visual_world      50
plan.world present             0
all-semantics-empty scenes    12
~~~

結論：

- **結構層 PASS**：physical line、Scene、解釋、Scene ID、entity reference 都一致。
- **production BLOCKED**：50 首都仍是 draft，而且 poem-world 全部只是「依各 Scene 視覺計畫」placeholder。
- v1 plan 雖能 parse，但內容品質不足以直接進 image API；例如部分詩缺主要人物 / 場景，甚至有語意誤判。
- 先把 Age 7 visual plans 升級成可審核的 substantive poem-world + Scene semantics，再批准。
- 不因 schema 合法就直接把 draft 改 approved。

可重跑 gate：

~~~powershell
py scripts\qa_age_production.py --age 7
py scripts\qa_age_production.py --age 7 --production-ready
~~~

不應直接大量呼叫 image API 後才處理這些問題。

---

## 5. Pronunciation / BPMF baseline

Canonical pronunciation data：

~~~text
data/bopomofo_overrides.json
data/bpmf_font_extensions.json
data/pronunciation_qa_age6.json
data/tts_pronunciation_overrides.json
docs/PRONUNCIATION_QA.md
~~~

正式文字 config：

~~~text
config/text_overlay_1080p_v2.json
~~~

已驗證 renderer strategy：

~~~text
canonical pronunciation metadata
  -> IVS semantic selection
  -> project-font PUA render alias
  -> complete integrated Han+Bopomofo glyph
~~~

PUA 只存在於 renderer / derived font lookup，不改 canonical poem text。

Age 6 已處理：

~~~text
17 pronunciation-sensitive occurrences
9 PUA aliases
2 missing literary IVS extensions
font_gaps=0
QA=PASS
~~~

兩個 project font extensions：

- 鹿柴：柴 ㄓㄞˋ
- 返景：景 ㄧㄥˇ

Age 7 / 8 / 9 不應假設 Age 6 override 已完整涵蓋。每個 age group 進 production 前都要重新做 pronunciation inventory / QA。

---

## 6. TTS production baseline

主程式：

~~~text
scripts/generate_tts_assets.py
~~~

Production model：

~~~text
gemini-3.8-flash-lite-tts
~~~

Voice baseline：

~~~text
Kore
~~~

TTS asset contract：

- canonical source text 保留原詩。
- 必要時 synthesis_text 可使用同音代理強制正確讀音。
- proxy 只影響送進 TTS 的字串，不改 poem / Scene SSOT。
- 生成流程預設 resume-safe；已有 WAV 會跳過，--force 才覆蓋。

Age 6 已實際使用並驗證的 proxy：

- 鹿柴：柴 -> 寨，強制 ㄓㄞˋ
- 返景：景 -> 影，強制 ㄧㄥˇ
- 疑是地上霜：疑 -> 宜，強制 ㄧˊ

正式文件：

~~~text
docs/TTS_BATCH.md
docs/PRONUNCIATION_QA.md
~~~

---

## 7. Text overlay production baseline

主程式：

~~~text
scripts/render_text_overlays.py
~~~

Canvas：

~~~text
1920 x 1080
raster scale = 2
~~~

目前主要 contract：

- title + content：BPMF
- author / poem_type / explanation：plain text
- title / author 固定
- poem_type：intro / end
- content：progressive accumulate + page
- explanation：active Scene only
- 長詩分頁，不硬縮到不可讀
- text PNG 與 background image 完全分離

正式規格：

~~~text
config/text_overlay_1080p_v2.json
docs/TEXT_OVERLAY_PIPELINE.md
~~~

---

## 8. Timeline / video composer baseline

Timeline：

~~~text
scripts/build_video_timeline.py
config/video_sessions_v1.json
config/video_timing_v1.json
~~~

Composer：

~~~text
scripts/render_video.py
config/video_motion_v1.json
~~~

輸出：

~~~text
assets/pXXX/video/pXXX_B_1080p.mp4
~~~

目前 video baseline：

- 1920x1080
- 30 fps
- H.264 / yuv420p
- AAC 48 kHz stereo
- timing 由實際 WAV 長度驅動
- content progressive reveal
- page turn / session reset
- explanation fade
- scene crossfade

### Background motion

已棄用 FFmpeg zoompan，原因是整數取樣造成可見 micro-jitter。

目前 production motion：

~~~text
consecutive background run
  -> render one continuous motion clip
  -> FFmpeg perspective
  -> cubic interpolation
  -> floating-point source corners
  -> slice into timeline events
~~~

運鏡交替：

~~~text
run 1: crop window 100% -> 80%
run 2: crop window 80% -> 100%
repeat
~~~

中心固定、無 pan / drift。Age 6 人工觀看 QA 已通過。

---

## 9. BGM baseline

Local BGM：

~~~text
bgm/
~~~

音檔不進 Git。

SSOT：

~~~text
config/bgm_mix_v1.json
data/poem_bgm.csv
docs/BGM_PIPELINE.md
~~~

Selection priority：

~~~text
CLI override
  > poem_bgm.csv mapping
  > stable pseudo-random by poem_id
~~~

若沒有指定 mapping，會從本機可用 BGM 中依 poem_id 做可重現 stable random selection。

音量 production baseline：

~~~text
narration gain       0 dB
BGM target          -35 LUFS
BGM true peak        -9 dBTP
fade in              1.2 s
fade out             1.8 s
~~~

這是 Age 6 聽感 QA 後，從原本 -32 LUFS 再降低約 3 dB 的版本。

---

## 10. Age-group batch video pipeline

主程式：

~~~text
scripts/render_video_batch.py
docs/VIDEO_BATCH.md
~~~

特性：

- 依 recommended_age 選組。
- 預設 approved only。
- 既有 MP4 自動 skip。
- 全部 remaining poems 先做 timeline + composer dry-run preflight。
- preflight 有任一 FAIL 時，不開始新的 expensive render。
- render 中途失敗可重跑。
- 已完成 MP4 下次會 skip。

Age 6 已通過這套流程並完成 25 支影片。

---

## 11. YouTube production baseline

頻道：

~~~text
https://www.youtube.com/@KidMoreTW
channel title = KidMore啟蒙
channel id = UC3W0UpC1Qu3KmkvITG_iNFw
~~~

OAuth：

~~~text
scripts/youtube_auth.py
config/youtube_v1.json
credentials/youtube_token.json   # local / gitignored
~~~

Scopes：

- youtube.readonly
- youtube.upload
- youtube.force-ssl

### Metadata contract

Title：

~~~text
唐詩三百首-注音版-{詩名}-{作者}-KidMore啟蒙
~~~

Description：

~~~text
{詩名}-{作者}-{poem_type}
{完整詩詞}

https://kidmore.tw?utm_source=ytbc&utm_medium=videoDescription&utm_campaign={詩名}
~~~

Tags：

~~~text
唐詩三百首
唐詩
兒童朗讀
兒童閱讀
~~~

Production defaults：

~~~text
privacyStatus = public
selfDeclaredMadeForKids = true
notifySubscribers = false
thumbnail = YouTube automatic
~~~

Playlist routing：

~~~text
6 -> 6歲建議
7 -> 7歲建議
8 -> 8歲建議
9 -> 9歲建議
~~~

### Playlist propagation recovery

實際 production 曾遇到：

~~~text
video upload = PASS
playlistItems = 404 playlistNotFound
~~~

已改為：

- direct playlist insert。
- playlistNotFound / videoNotFound bounded retry。
- videoAlreadyInPlaylist 視為 idempotent success。
- --existing-video-id 可補做 playlist post-step，不重傳 MP4。

---

## 12. YouTube batch / reconciliation baseline

主程式：

~~~text
scripts/youtube_upload_batch.py
docs/YOUTUBE_BATCH_UPLOAD.md
~~~

YouTube channel 本身是 runtime uploaded-state SSOT。

每次 batch：

1. inventory authenticated channel。
2. 對每首 poem 產生 canonical title + tracking URL。
3. 判斷 channel 是否已有該 poem。
4. 已有：不重傳 MP4，只做 idempotent postcheck。
5. 未有且本機 MP4 存在：READY upload。
6. 本機 MP4 不存在：WAIT_LOCAL。
7. 多個 channel matches：CONFLICT，停止自動上傳。

已上傳判定：

~~~text
exact generated title
OR
canonical poem tracking URL in description
~~~

Age 6 最終 batch：

~~~text
already_uploaded=2
existing_postchecked=2
existing_post_failed=0
uploaded_now=23
failed=0
waiting_local=0
PASS
~~~

此結果驗證 duplicate avoidance、existing-video reconciliation、playlist postcheck、fail-safe resume、23 支連續新 upload、全組 local availability 與全組 channel completeness。

---

## 13. Age 6 已驗證的重要 production cases

### p225 春曉

第一支真正 YouTube upload：

~~~text
video_id=g8daVN7W2bg
privacy=private
made_for_kids=true
upload=PASS
~~~

用途：驗證 OAuth / resumable upload / metadata / made-for-kids。

### p226 夜思

第一支 public production upload / playlist recovery case：

~~~text
video_id=l5WYLemUnuQ
privacy=public
made_for_kids=true
~~~

曾碰到 playlist 404 propagation，之後以 existing-video repair path PASS。

用途：驗證 public publication + playlist recovery + no duplicate re-upload。

---

## 14. Definition of Done：每個 age group

之後 7 / 8 / 9 歲沿用同一 DoD。

### Data / semantic

- 該 age poems 數量確認
- Scenes 數量確認
- visual_plan_status 全部 approved
- physical-line Scene invariant PASS
- blank Scene invariants preserved

### Pronunciation / audio

- pronunciation inventory 完成
- BPMF override / font gap QA PASS
- TTS pronunciation QA PASS
- 必要 synthesis proxies 登錄
- 所需 WAV 全部存在

### Visual / text

- production style 決策完成
- backgrounds 全部存在
- text overlays 全部存在
- asset preflight PASS

### Video

- timelines 全部 PASS
- batch MP4 render 完成
- waiting / failed = 0
- spot-check motion / text / audio / BGM

### YouTube

- OAuth token scopes valid
- batch inventory 無 unresolved CONFLICT
- waiting_local = 0
- failed = 0
- existing_post_failed = 0
- channel completeness = poem count
- age playlist routing complete

---

## 15. 下一階段：Age 7

Age 7 現況：

~~~text
poems            50
approved         50
draft             0
scenes          127
nonblank scenes 127
blank scenes      0
visual_plan      visual_plan_v2
semantic gate    PASS
~~~

Age 7 visual plan 已採 source-grounded Scene semantics，不再使用 v1 規則式 entity 猜測。

因此下一步不是直接組影片，而是先完成 Age 7 resource production gate。

建議順序：

~~~text
1. Age 7 inventory
2. visual plan review / approve
3. image style generalization decision
4. pronunciation inventory / QA
5. TTS assets
6. BPMF/text overlays
7. background images
8. full asset preflight
9. batch timeline
10. batch MP4
11. YouTube dry-run inventory
12. batch upload
13. reconciliation PASS
14. mark Age 7 COMPLETE
~~~

完成後依序：

~~~text
Age 8 -> 100 poems / 395 scenes
Age 9 -> 138 poems / 1,050 scenes
~~~

Age 9 特別注意 6 個 blank separator Scenes，不應因為空行而被重建 script 刪掉。

---

## 16. 當前 phase 定義

~~~text
Phase 1  Age 6 production pipeline + delivery   COMPLETE
Phase 2  Age 7 resource production              NEXT
Phase 3  Age 8 resource production              PENDING
Phase 4  Age 9 resource production              PENDING
Phase 5  Full 313-poem reconciliation           PENDING
~~~

目前不需要再重做 Age 6 pipeline architecture；除非 Age 7 出現真正的跨 age contract gap，否則後續應優先複用既有 production components，而不是重新設計一套流程。

---

## 17. Repo / local boundary

GitHub Repo 是 code / config / metadata / development contract SSOT。

下列大型或敏感 runtime assets 預設只存在 local，不進 Git：

- .env
- OAuth client / user token
- fonts
- BGM audio files
- generated WAV
- generated image binaries
- generated text PNG
- generated MP4

因此 Repo 可以定義「應該有什麼」，但不能單靠 Git 證明 local binary assets 是否存在。

正式判定 local completeness 仍必須依 generation scripts 的 resume logic、timeline preflight、render batch preflight、YouTube batch inventory / reconciliation。

---

## 18. 主要文件索引

~~~text
docs/SCHEMA.md
docs/IMAGE_PIPELINE.md
docs/PRONUNCIATION_QA.md
docs/TTS_BATCH.md
docs/TEXT_OVERLAY_PIPELINE.md
docs/VIDEO_PIPELINE.md
docs/VIDEO_BATCH.md
docs/BGM_PIPELINE.md
docs/YOUTUBE_UPLOAD.md
docs/YOUTUBE_BATCH_UPLOAD.md
~~~

本文件只記錄「目前開發到哪裡」；細部 contract 以各專門文件、config 與 script 為準。


## 19. Age 7 pronunciation / font gate（2026-10-08）

目前 semantic/image production gate 已 PASS。下一個唯一 blocking gate 是本機 project font + pronunciation validation。

Repo 已完成：

~~~text
pronunciation candidates       43
authority_check remaining       0
Age 7 TTS override assets      40
new BPMF override occurrences  32
compatibility aliases           6
~~~

新增 local font sync：

~~~text
scripts/sync_bpmf_font.py
~~~

本機實測顯示 pinned upstream BpmfHuninn binary 仍缺 `螘` / `蟢`；雖然 bpmfvs pronunciation data 有收錄，font cmap 並未實際包含。因此 project derivative 改為同時處理：

~~~text
㡬 -> 幾
却 -> 卻
爲 -> 為
藁 -> 稿
衆 -> 眾
隣 -> 鄰

synthesized canonical glyphs:
螘 = 虫 + 豈
蟢 = 虫 + 喜
~~~

合成只處理 Han glyph，注音仍使用既有 bpmfvs phonetic components。canonical CSV 不因字型 coverage 而改字。

下一個 gate 必須在本機依序通過：

~~~powershell
py scripts\sync_bpmf_font.py --force
py scripts\patch_bpmf_font.py --force
py scripts\inventory_pronunciation.py
py scripts\qa_font_coverage.py --age 7
py scripts\qa_pronunciation.py
~~~

上述 PASS 前，不開始 Age 7 大量 TTS / image generation。


### Age 7 font binary coverage correction

第一次 pinned-font rebuild 的實測結果：pronunciation QA PASS，但 generic font coverage 仍缺 `螘`、`蟢`。因此新增 `bpmf_font_extensions_v3.synthesized_glyphs`；此 gate 目前等待本機 rebuild 後重新確認 `missing_unique_han=0`。


## 20. Age 7 TTS listening QA round 1（2026-10-08）

40 個 pronunciation-sensitive assets 已生成並人工驗收：

~~~text
PASS 35
FAIL  5
~~~

FAIL：

~~~text
p006_s01   夫 ㄈㄨˊ
p085_s01   闕 ㄑㄩㄝˋ
p263 author 參 ㄕㄣ
p293_s01   爲 ㄨㄟˋ
p308 title  塞 ㄙㄞˋ
~~~

這 5 個已升級為 synthesis_text fallback；canonical source 不變。新增 `--synthesis-proxy-only`，只重生 fallback assets，避免覆寫另外 35 個已人工 PASS 的 WAV。


### Age 7 TTS listening QA round 2

Round-1 proxies: 4/5 PASS. Only p293_s01 remained wrong because `為有` was still interpreted as ㄨㄟˊ.

Updated TTS-boundary proxy:

~~~text
canonical       爲有雲屏無限嬌，鳳城寒盡怕春宵。
synthesis_text  未有雲屏無限嬌，鳳城寒盡怕春宵。
target          爲 ㄨㄟˋ
~~~

Only p293_s01 must be regenerated; do not touch the other 39 pronunciation-sensitive assets.


## 21. Age 7 pronunciation-sensitive TTS COMPLETE（2026-10-08）

Human listening QA final:

~~~text
pronunciation-sensitive assets  40
PASS                            40
FAIL                             0
synthesis_text fallbacks         5
~~~

p293_s01 round-2 proxy `未有...` 已人工確認為 ㄨㄟˋ。

因此 pronunciation-sensitive TTS gate = PASS。後續全量 Age 7 TTS 必須使用 resume mode，不可用 `--force` 覆寫這 40 個已驗收 WAV。


## 22. Age 7 full TTS + Batch image production（2026-10-08）

Age 7 full TTS production result reported from local runtime:

~~~text
selected_poems       50
scenes              127
audio_assets_total  354
generated           314
skipped_existing     40
failed                0
~~~

Therefore Age 7 audio resource gate = COMPLETE.

Image smoke production succeeded for the first 11 backgrounds before the synchronous process was interrupted manually:

~~~text
p006  4/4
p040  3/3
p085  4/4
----------------
existing backgrounds = 11
Age 7 total          = 127
expected remaining   = 116
~~~

The interruption was `KeyboardInterrupt`, not an API generation failure.

For the remaining high-volume image production, the production path is changed from synchronous one-request-at-a-time delivery to Gemini Batch API:

~~~text
scripts/generate_images_batch.py
~~~

The batch path:

1. uses the exact same `build_prompt()` semantics and Style B config;
2. checks local production backgrounds before submission;
3. creates one JSONL file containing only missing requests;
4. uploads it and creates one Gemini Batch API job;
5. saves job state under `output/image_batches/`;
6. downloads the completed result JSONL;
7. writes images back to the existing production asset paths;
8. records Batch-tier usage/cost in `data/image_usage.csv`.

Current official Batch pricing for `gemini-3.1-flash-lite-image` is 50% of standard API pricing, approximately US$0.0168 image-output cost per 1K image plus discounted input text tokens.

Normal Age 7 command:

~~~powershell
py scripts\generate_images_batch.py submit --age 7 --approved-only --styles B --dry-run
py scripts\generate_images_batch.py submit --age 7 --approved-only --styles B
py scripts\generate_images_batch.py status
py scripts\generate_images_batch.py collect
~~~

Expected dry-run on the current local runtime is approximately:

~~~text
selected_scenes    127
skipped_existing    11
batch_requests     116
~~~

The actual local filesystem remains authoritative for this count.


### Age 7 Batch image dry-run PASS（2026-10-08）

Local dry-run result:

~~~text
delivery_mode=gemini_batch_api
model=gemini-3.1-flash-lite-image
selected_scenes=127
skipped_existing=11
batch_requests=116
estimated_image_output_cost_usd=1.948800000
force=False
dry_run=True
~~~

The 11 existing production backgrounds are p006 (4), p040 (3), p085 (4). They are excluded from the Batch JSONL. The submit gate is PASS; next action is one real Batch API submission for the remaining 116 images.


## 23. Age 7 image production COMPLETE（2026-10-08）

Gemini Batch API production completed successfully for the remaining 116 backgrounds:

~~~text
batch_requests=116
generated_now=116
already_processed=0
collected_total=116
failed_total=0
estimated_cost_usd_now=1.962680125
~~~

Together with the 11 synchronous smoke-test backgrounds:

~~~text
existing synchronous backgrounds  11
batch-collected backgrounds       116
-------------------------------------
Age 7 backgrounds total           127 / 127
~~~

Human visual QA was then performed on the complete Age 7 background set.

Six images were found to contain unwanted generated Chinese text:

~~~text
p121_s01
p224_s01
p260_s01
p272_s01
p295_s01
p300_s01
~~~

All six were regenerated individually through the synchronous production generator with `--force`, then rechecked. Final human QA result:

~~~text
Age 7 background completeness  127 / 127
obvious text contamination        0
human visual QA                  PASS
image resource gate              COMPLETE
~~~

The Batch API remains the preferred high-volume production path. Synchronous generation remains the targeted retry path for individual defective images.

Next gate: full Age 7 timeline + composer preflight via `render_video_batch.py --age 7 --style B --preflight-only`.


## 24. Age 7 video preflight PASS（2026-10-08）

Full approved Age 7 video batch preflight completed successfully:

~~~text
age=7
style=B
selected=50
approved_only=true

preflight_summary=passed:50 failed:0

PASS preflight_only=true
ready_to_render=50
existing_skipped=0
elapsed=18s
~~~

The final checked poem p313 also passed timeline + composer validation:

~~~text
p313 金縷衣
events=41
timeline=124.240s
frame_snapped_duration=124.233s
frames=3727
motion=PASS
bgm=空山滴翠.mp3
render_video dry-run=PASS
~~~

Therefore the full Age 7 local resource/timeline/composer gate is PASS.

Next action:

~~~powershell
py scripts\render_video_batch.py --age 7 --style B
~~~

Do not use `--force`; the batch runner is resume-safe and will skip completed MP4s if interrupted.


## 25. Age 7 video render COMPLETE（2026-10-08）

Full approved Age 7 MP4 render completed successfully:

~~~text
age=7
selected_total=50
existing_skipped=0
rendered=50
render_failed=0
elapsed=1h19m40s
PASS
~~~

Therefore the Age 7 local video gate is COMPLETE:

~~~text
approved poems     50
local MP4 ready    50 / 50
render failures     0
~~~

Next gate is YouTube channel inventory / reconciliation dry-run:

~~~powershell
py scripts\youtube_upload_batch.py --age 7 --style B --dry-run
~~~

Expected production conditions before upload:

~~~text
duplicate_conflicts = 0
missing_local       = 0
ready_to_upload + already_uploaded = 50
~~~


## 26. Age 7 YouTube inventory + scheduled publication decision（2026-10-08）

Age 7 YouTube dry-run inventory:

~~~text
selected=50
already_uploaded=1
ready_to_upload=49
missing_local=0
duplicate_conflicts=0
~~~

Decision: new Age 7 uploads use YouTube scheduled publishing instead of immediate public release.

Policy:

~~~text
timezone: Asia/Taipei
2 new videos/day
morning random window: 06:00-08:59
evening random window: 17:00-19:59
first Age 7 date: 2026-10-09
notifySubscribers: false
upload privacy before publishAt: private
~~~

The one already-uploaded Age 7 video is preserved and is not automatically rescheduled.

Implementation:

~~~text
youtube_upload.py      -> --publish-at, status.publishAt, forced private upload
youtube_upload_batch.py -> deterministic schedule planning + persisted resume plan
youtube_v1.json         -> schedule policy
~~~

For 49 new videos, the current plan spans 2026-10-09 through 2026-11-02 (24 full two-video days plus one final morning slot).


## 27. Age 7 scheduled YouTube upload smoke test: remote schedule PASS / client completion FAIL（2026-10-08）

The first scheduled Age 7 upload p006 was successfully created by YouTube and displayed in YouTube Studio with the intended scheduled publish time.

However, the local uploader did **not** complete cleanly. After the upload reached 91%, the resumable session returned:

~~~text
HTTP 410 Gone
~~~

The batch therefore exited with `UPLOAD_FAIL p006 rc=1`, even though the video had already been committed remotely.

Interpretation:

~~~text
scheduled publication contract   PASS
remote video creation            PASS
local completion acknowledgement FAIL
duplicate-safe recovery          REQUIRED
~~~

This is an ambiguous resumable-upload completion: the remote side contains the video, but the client did not receive the normal final video resource response.

## 28. YouTube ambiguous completion recovery（2026-10-08）

`youtube_upload.py` now handles terminal HTTP 404/410 from the resumable session conservatively.

Recovery contract:

1. a 404/410 is **not** automatically considered success;
2. inventory the authenticated channel uploads playlist;
3. match the expected generated title or canonical tracking URL;
4. if exactly one video exists, recover that video resource and continue normal post-upload processing;
5. if zero matches remain after bounded propagation retries, preserve the original failure;
6. if multiple matches exist, stop as a duplicate ambiguity.

This prevents a remotely successful upload from being reported as a failure, while also preventing blind re-upload/duplication.

p006 already exists remotely. On the next Age 7 batch run, channel inventory will classify it as already uploaded and run the existing idempotent post-upload/playlist repair path rather than upload it again.


## 29. YouTube upload-completeness verification gate（2026-10-08）

Because p006 returned HTTP 410 after 91% client-side progress while YouTube Studio showed "Processing will begin shortly", channel presence alone is not treated as proof that the binary upload is complete.

A read-only verifier was added:

~~~powershell
py scripts\youtube_verify_upload.py --poem-id 6 --style B
~~~

It checks:

- `status.uploadStatus`;
- `processingDetails.processingStatus`;
- `processingFailureReason`;
- processing errors;
- remote `fileDetails.fileSize` when available;
- exact remote/local byte-size equality.

Bulk Age 7 upload should remain paused until p006 returns either `PASS_UPLOAD`, `PASS_UPLOAD_STATUS`, or full processing `PASS`. A remote-size mismatch or processing/upload failure is a hard stop.


## 30. p006 upload completeness verified（2026-10-08）

Read-only verification of the first scheduled Age 7 upload:

~~~text
video_id=6W_px5PXLVI
poem=p006 望嶽
local_size=82377762
remote_size=82377762
size_match=true
upload_status=uploaded
processing_status=processing
file_details_availability=inProgress
~~~

Conclusion:

~~~text
binary upload completeness   PASS
remote/local byte equality   PASS
YouTube processing           IN PROGRESS
scheduled publish path       PASS
~~~

The earlier HTTP 410 was therefore a client-side ambiguous completion after the remote upload had already committed successfully; it did not truncate the MP4.

The Age 7 uploader may continue using the repaired ambiguous-completion reconciliation path. If p006 is checked again later, expected terminal processing state is `processing_status=succeeded`.


## 31. Age 7 YouTube schedule reconciliation dry-run PASS（2026-10-08）

After fixing duplicate-title reconciliation and the accidental playlist routing, the Age 7 schedule dry-run now contains 48 new uploads.

Key observations:

~~~text
already uploaded before this batch: p006, p040
scheduled new uploads: 48
p269 春怨 - 劉方平: included as a new scheduled upload
duplicate-title false match: resolved
~~~

The previously persisted schedule assignments were preserved. The newly recovered p269 assignment was appended to the next unused slot:

~~~text
p313 金縷衣  2026-11-02 07:02 Asia/Taipei
p269 春怨    2026-11-02 17:30 Asia/Taipei
~~~

Therefore the schedule self-healing contract is validated: corrected reconciliation can add a missing poem without reshuffling existing publication times.

Next production action is the full Age 7 upload using the same persisted schedule plan.


## 32. Age 7 phase COMPLETE / handoff baseline（2026-10-08）

Founder/operator confirmed the Age 7 phase is complete.

### Final Age 7 production baseline

~~~text
poems                         50
scenes                       127
pronunciation-sensitive QA    40 / 40 PASS
audio assets                 354 complete
background images            127 / 127 complete
manual image QA              PASS
video preflight               50 / 50 PASS
local MP4 render              50 / 50 PASS
YouTube schedule dry-run      PASS
scheduled publication path   PASS
~~~

### YouTube production decisions retained for later ages

- Batch scheduling remains **local age-group based**, not whole-channel capacity based.
- Default schedule policy:
  - timezone: Asia/Taipei
  - morning window: 06:00-08:59
  - evening window: 17:00-19:59
  - 2 videos/day per local age-group batch
- New scheduled uploads use `privacyStatus=private` + `status.publishAt`.
- `notifySubscribers=false`.
- Recommended-age playlist routing remains enabled.
- Existing channel matches are not re-uploaded.
- Persisted schedule plans remain under `output/youtube_schedules/` and keep existing slots stable across resume runs.

### Important Age 7 fixes that become cross-age baseline

1. Resumable upload HTTP 404/410 is treated as ambiguous completion.
2. Ambiguous completion is reconciled against the authenticated channel before deciding failure.
3. Channel presence alone is not proof of binary completeness; `youtube_verify_upload.py` can compare remote/local file size and processing state.
4. Same-title poems require poem-unique identity. New tracking URLs include `utm_content=pXXX`.
5. Existing-video postcheck revalidates identity before playlist mutation.
6. Persisted schedule plans may append newly discovered poems without reshuffling existing publication times.
7. Targeted playlist repair is available through `youtube_playlist_remove.py`.

### Age 7 duplicate-title incident retained as regression case

~~~text
p244 春怨 - 金昌緒 - age 6
p269 春怨 - 劉方平 - age 7
~~~

Legacy title-only campaign tracking caused a false match during Age 7 reconciliation. This is now a permanent regression case for Age 8/9 production.

### Next production target

Reuse the validated Age 7 pipeline for Age 8. Do not redesign Age 6/7-proven stages unless Age 8 exposes a genuine cross-age contract gap.
