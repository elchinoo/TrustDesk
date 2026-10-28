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
same voice in two consecutive topics, male and female alternating, one accent: US). TrustDesk: scenes 1-2 af_heart (intro and
setup), scene 3 am_michael (the demo data), scenes 4-5 af_sarah (the advice and the decision), scenes 6-7 am_eric (reports and
the client API), scenes 8-9 af_heart (search, MCP, recap). Each topic starts with a sentence that orients the listener.

Writing narration: one `## <scene id> <voice>` section per scene; short sentences, one idea each; a blank line is a pause, `...` a short
beat, `[pause 1.5]` a deliberate long pause where something happens on screen that the viewer should watch (do not talk over a
wait). If you change a sentence the recorder anchors an action to, update the matching `at(...)` text in `record.spec.ts` (it
fails loudly when no sentence contains the text).

Options: `--keep-recording` re-mixes a previous take in `--work`; `--reuse-audio` keeps the narration already spoken in `--work/audio`; `--tts-dir` points to another Kokoro tool folder (default `../../videos/tts` relative to the repository); `HOLD=0.2 SLOWMO=0` env values give a fast rehearsal (cue times shrink too);
`TD_PORT_RANGE=41300-41399` limits the ports used; `HEADED=1` shows the browser.

Playwright's own ffmpeg build is not required: when `ffmpeg-<rev>` is missing from the Playwright browser cache, `build.py`
creates a private `PLAYWRIGHT_BROWSERS_PATH` under `--work` (links to the installed browsers plus a wrapper that runs the
system `ffmpeg`). Nothing is downloaded. Needs: macOS, `ffmpeg`, Node 22+, Google Chrome, `npm install` in this folder, and the Kokoro tool folder with its own `.venv` and models (`videos/tts`).

The terminal scenes (2 and 7) are drawn as terminal pages in the same recording, with the real server log and real API
answers. The setup code visible in scene 2 belongs to the throwaway server and is already used up when the video ends.
