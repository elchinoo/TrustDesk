# Tutorial video: recorder and build

One command goes from `docs/VIDEO-SCRIPT.md` and the recorder to the finished review cut:

```bash
cd trustdesk                      # needs .venv (uv venv && uv pip install -e ".[dev]") and Postgres 17 + pgvector on PATH or TD_PG_BIN
python3 docs/video/build.py --work /some/scratch/dir --name trustdesk --deliver /where/to/copy
```

Output: `<work>/trustdesk.mp4` (1920x1080, H.264 yuv420p, AAC, faststart, captions as a soft `mov_text` track) and
`<work>/trustdesk.srt`. Everything stays local: a throwaway Postgres on 127.0.0.1, the labelled demo stand-in model
(`scripts/fake_llm.py`), TrustDesk on a free port, a fresh database on every take. `out/` is git-ignored; do not commit media.

What it does:

1. Checks that `narration.txt` (the narration, written for listening) and the NARRATION column of the scene table in
   `VIDEO-SCRIPT.md` say the same thing, scene by scene; it stops if they differ. Edit both together: in the table a paragraph
   break is a space, a `...` beat is dropped, a `[pause 1.5]` stays.
2. Speaks `narration.txt` with `videos/tts/narrate.py scenes ... --voice af_heart --pronounce docs/video/pronounce.json`
   (Kokoro, a local neural voice: nothing leaves the machine, nothing is downloaded). Output: one wav per scene and
   `narration.json` with each scene's duration and the start and end of every sentence. Respellings for the voice
   (`README` is spoken "read me") live in `pronounce.json`, on top of the shared `videos/tts/pronounce.json`; captions keep the
   written form. Prefer rewording a sentence to a fragile respelling.
3. Runs `record.spec.ts` (Playwright, real Chrome) with `NARRATION_JSON`. Every scene lasts at least 0.3 s lead + narration +
   0.8 s tail. Each on-screen action waits for the sentence that illustrates it (`await at(page, 'words of the sentence')`), so a
   click, a callout or a page change starts as that sentence begins; if a page is slow the action runs at once and the delay is
   logged. The recorder writes the scene start times (`TIMES_JSON`, with a `sync` list of cue time vs actual time).
4. ffmpeg places each scene's audio at its start time, encodes, and adds the captions. The `.srt` comes from the per-sentence
   times of `narration.json`.
5. It stops with an error if any scene is shorter than its narration + 0.7 s.

Voices: the header `## <scene id> <voice>` gives the voice of a scene; it changes with the topic (never inside a scene, never the
same voice in two consecutive topics, male and female alternating, one accent: US, never af_nicole). TrustDesk: 1 am_michael (the
problem, scaffolding vs platform), 2 af_heart (the rules loaded), 3 am_eric (a compliant request), 4 af_sarah (a non-compliant request
and the verified quotes), 5 am_adam (search), 6 af_bella (live status), 7 am_eric (database insights), 8 af_heart (masking), 9 am_michael
(landing).

Writing narration: one `## <scene id> <voice>` section per scene; short sentences, one idea each; a blank line is a pause, `...` a short
beat, `[pause 1.5]` a deliberate long pause where something happens on screen that the viewer should watch (do not talk over a
wait). If you change a sentence the recorder anchors an action to, update the matching `at(...)` text in `record.spec.ts` (it
fails loudly when no sentence contains the text).

Options: `--keep-recording` re-mixes a previous take in `--work`; `--reuse-audio` keeps the narration already spoken in `--work/audio`; `--tts-dir` points to another Kokoro tool folder (default `../../videos/tts` relative to the repository); `HOLD=0.2 SLOWMO=0` env values give a fast rehearsal (cue times shrink too);
`TD_PORT_RANGE=41300-41399` limits the ports used; `HEADED=1` shows the browser.

Playwright's own ffmpeg build is not required: when `ffmpeg-<rev>` is missing from the Playwright browser cache, `build.py`
creates a private `PLAYWRIGHT_BROWSERS_PATH` under `--work` (links to the installed browsers plus a wrapper that runs the
system `ffmpeg`). Nothing is downloaded. Needs: macOS, `ffmpeg`, Node 22+, Google Chrome, `npm install` in this folder, and the Kokoro tool folder with its own `.venv` and models (`videos/tts`).

Not live from this laptop, and labelled as such on screen: the architecture diagram (scene 1: sample app / reference scaffolding /
platform), the real screenshots captured on the live AgentPG fleet (scenes 4 and 7; files in `videos/live-captures/`, because
`edb_stat_monitor`, `edb_pg_tuner` and `query_advisor` do not exist on a local Postgres), the terminal of scene 8 (real `psql` output of
the read-only user `ai_reader` on the local database; the recorder creates `a1p_readonly` and `ai_reader` before the migrations so
the kit's masking is applied) and the live-status counter of scene 6 (an illustration of the AgentPG polling fallback). Everything
else is the real app: requests typed in the New request dialog, the stand-in model's advice (`MULTI` entries in
`scripts/fake_llm.py` for the two requests of scenes 3 and 4), search, `unaccent`, live updates through `tcn`.

Settings: the clock of the recording starts at the first card (the admin password, the client token and the employee José Álvarez
are created before it and cut away). `build.py` finds that card in the webm with `signalstats` (the key is named
`lavfi.signalstats.YAVG` in ffmpeg 8). Scene length = LEAD 0.2 s + narration + TAIL 0.5 s. Use `TD_PORT_RANGE=47100-47199` (or any free
high range) when ports are shared with other work; the recorder only ever stops processes it started.
