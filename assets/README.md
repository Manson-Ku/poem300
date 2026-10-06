# Assets

Generated production assets are organized by poem, then Scene.

```text
assets/
  p225/
    poem.json
    s01/
      scene.json
      audio/
        poem.wav
        explanation.wav
      image/
        background.webp
      text/
        poem_bpmf.png
    s02/
      ...
```

Rules:

- `pXXX` uses zero-padded `poem_id`.
- `sYY` uses zero-padded `scene_no`.
- One physical line in the poem equals one Scene.
- Audio, image and text-overlay binaries are generated assets and are not committed to Git.
- `poem.json` and `scene.json` are lightweight manifests and may be committed.
- TTS usage accounting lives in `data/tts_usage.csv`.
