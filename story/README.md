# Never confidently wrong — the film

A 3:40 narrated, deterministic visualisation of the pharmacy consensus
simulation. Every figure on screen and in the voice comes from captured
rounds. Nothing is typed in by hand.

Architecture follows `janus/website`: a pure scene model, a renderer that
regenerates SVG per frame, a voice-driven clock, and frame-stepped capture.

## Files

    motion.mjs             closed-form physics: spring, drop (gravity +
                           restitution), noise1, onCurve. Pure in t.
    scene.mjs              evaluate(t, data) -> full frame state. Chapters,
                           camera (on springs), every reveal. Beats in the
                           "What we built" chapter are pinned to spoken
                           sentence times from captions.json.
    app.mjs                renders state to SVG + kinetic headline; audio clock
    index.html             light shell, transport, chapters, "what we learned"
    story_data.json        the numbers; built by ../build_story_data.py
    narrate.py             ElevenLabs takes per chapter, cached by hash
    assemble_narration.py  fits takes to chapter slots (refuses to rush > 1.15x),
                           loudness-normalises, cuts captions on real sentence
                           boundaries from the API's character alignment
    capture.py             seeks frame by frame in Chromium, pipes to ffmpeg,
                           muxes narration
    provenance.py          asserts every spoken figure still matches the data
    assets/narration/      jev-story.mp3, captions.json, jev-story.vtt,
                           provenance.json

## Rebuild from scratch

    # 1. numbers (from the simulation telemetry in consensus/rust/consensus-kernel/sim_*.jsonl)
    cd .. && ./.venv/bin/python build_story_data.py && cd story

    # 2. voice (needs an ElevenLabs key in a dotenv file; George = JBFqnCBsd6RMkjVDRZzb)
    python3 narrate.py --voice JBFqnCBsd6RMkjVDRZzb
    python3 assemble_narration.py

    # 3. if takes changed length, re-time chapters to the voice, then re-run step 2b
    #    (see the re-timing snippet in the session log; slot = ceil(speech + 1.6s))

    # 4. check the script hasn't drifted from the data
    ../.venv/bin/python provenance.py

    # 5. render
    python3 -m http.server 8765 &
    CHROME=$(ls ~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome | tail -1)
    ../.venv/bin/python capture.py --fps 30 --browser "$CHROME" \
      --video captures/never-confidently-wrong.mp4 \
      --audio assets/narration/jev-story.mp3

## Watching in a browser

    python3 -m http.server 8765
    open http://localhost:8765/index.html

Space plays/pauses. Arrow keys step 2s. `?capture=1` strips the chrome;
`?t=SECONDS` seeks; `?autoplay=1` starts.

## Design rules

- Motion is physical, not eased. Arrivals are damped springs (they overshoot
  and settle). The floor chapter drops every vote under gravity with
  restitution until it rests at its true margin. Chaos kicks nodes with
  decaying impulses over 1D noise. All closed-form in t.
- Beats are pinned to the voice. After assembling narration, read
  captions.json and set reveal times to the sentence that names each thing.
- The picture owns the frame. The headline builds in, holds, then yields
  when the camera pushes in or when a full-width figure needs the space.
- Camera moves are in the scene model, not the renderer, so they are
  deterministic and testable (`node -e` smoke test walks every 0.25s).
- Time belongs to the voice. When George read slower than the storyboard,
  the chapters were stretched to him; speech is never sped up.
- Takes are cached by hash, not chapter index. After inserting a chapter,
  re-place takes by matching source_text to transcript; provenance.py
  asserts this for every take.
- A "lost" agent is one that never voted. An agent that failed and retried is
  drawn as "retrying" until its vote lands, so the picture never contradicts
  the narration.
- The honest chapter sits after the zero on purpose. The caveat lands where a
  compliance reader needs it.
