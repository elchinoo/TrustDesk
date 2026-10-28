# Video script: TrustDesk, answers from your policies that you can trust

**Title:** "I need answers from our policies that I can trust"

**Length:** 5:47 (narration about 700 words, 9 scenes, spoken by neural voices that change with the topic; the length is set by the audio, see [video/README.md](video/README.md)). Re-recorded 2026-10-28.

**Audience:** customers who handle employee requests (HR, finance, managers) and the developers who would build on the same pieces. No Postgres or AI knowledge is assumed.

**Story (problem first):** the video opens with the viewer's problem in its first sentence ("I am an approver. I have a stack of employee requests, and a two hundred page policy handbook.") and, within the first 30 seconds, says plainly that TrustDesk is a sample app, that identity, the LLM gateway and the vault are reference scaffolding you can swap, and that what you watch is EDB Postgres on the AgentPG platform doing the work, with pgvector in the same Postgres and no external vector database. The landing moment is "look at the level of work you should be doing" plus "only EDB": native vectors, hybrid search and verified citations in one database.

**What is shown on screen (not described):**

1. A new request is submitted and the AI pre-check runs: a compliant case (suggests approval) and a non-compliant case (15 working days, 2 days of notice: suggests rejection), each with numbered reasons that cite policy passages and the green line "Quote found word for word in the document" (the full passage is opened).
2. Search over policies (vector rank, exact-word rank and the typo-tolerant `pg_trgm` rank, with a real typo) and over requests (a typo in a name finds the person).
3. Live status: the request list changes when a row changes (`tcn`).
4. The Database tab, and real screenshots **captured from the live AgentPG fleet** (`edb_stat_monitor`, `edb_pg_tuner`, `query_advisor`); plus the real REQ-000016 rejection in scene 4.
5. Masking: the read-only database user sees `employee_masked` and is denied on `employee` (real `psql` output).

**Honest limits that the narration says out loud, and the ones it does not need to:**

- The quote check is done by TrustDesk against the text of the policy row it read from the database (not by the model), so a model cannot invent a citation. It proves the words exist in the passage, not that they support the conclusion (GUIDE section 7.2 and 16).
- Live status: locally the browser gets pushed updates over server-sent events. On AgentPG the platform ingress does not flush server-sent events, so the page's watchdog gives up after 6 seconds and polls (kit 0.3.1). The video says so and illustrates it with a labelled counter; it does not show a live AgentPG browser.
- Typo tolerance is real (migration 007, `pg_trgm` as a third rank signal; names and titles use trigram matches). Scene 5 types `encyption` (Encryption Policy comes first, badges "typo-tolerant #n" and "typo-tolerant match") and `alvares` (finds José Álvarez). The narration says that a real embedding model ranks better and that this demo embedder is only lexical, so the typo ranking shown is the floor. Candidate typos were tested first: `encyption`, `retension` and `backps` rank their policy in the top 5; "vaction polcy" does not (rank 7 to 11), so it is not used.
- EDB extensions do not exist on a local Postgres. The Database tab shows "Not available on this database." and the insights are four **real screenshots captured on the live fleet** on 28 Oct 2026 (top queries from `edb_stat_monitor`, including the platform recording the app re-asserting its masking views; the `edb_pg_tuner` table; the `query_advisor` suggestions, with the note that already-indexed columns are hidden).
- Masking is shown on the local database (the kit's masking works on plain Postgres). That a new database user re-opens the grants on AgentPG, and that the app closes them again every 5 minutes (kit 0.3.1), is a captured finding (`ecosystem/docs/09-goals-and-acceptance.md`, G6: re-opened 16:52:39, closed again by 16:55:55), shown as a labelled text card, not re-run.
- The model: locally the AI is the scripted stand-in `scripts/fake_llm.py` (not a model). The narration says "a model behind the gateway" and never names a model. AIDB is neither shown nor mentioned.
- REQ-000016 on the live fleet (real model, captured screenshots, scene 4): rejected for notice 2 days against 30, VP approval over 15 consecutive days, and a Finance blackout that does not apply to Marketing. The local take cites Director approval for 11 to 15 days; the narration says "the live policy there asks for VP approval".

**Voices (Kokoro, US accent, never in the middle of a scene; male and female alternate by topic; opener am_michael):** 1 am_michael, 2 af_heart, 3 am_eric, 4 af_sarah, 5 am_adam, 6 af_bella, 7 am_eric, 8 af_heart, 9 am_michael. Never af_nicole.

---

## 1. Prerequisites and seed data (reproducible locally)

Nothing here needs a live service. Everything is fictional ("Acme Cloudworks" policies, eleven demo employees at example.com, plus José Álvarez of Marketing that the recorder creates).

- Machine: macOS, `uv` with the repository `.venv` (`uv venv && uv pip install -e ".[dev]"`), Postgres 17 binaries with pgvector, `pg_trgm`, `unaccent` and `tcn` on `PATH` (or `TD_PG_BIN`), `ffmpeg`, Node 22+, Google Chrome, and the Kokoro tool folder `videos/tts` (own `.venv` and models).
- The recorder starts a **throwaway Postgres** (UTF8, its own data directory, 127.0.0.1 only, a port from `TD_PORT_RANGE`, e.g. `47100-47199`), creates the AgentPG-like roles `a1p_readonly` and `ai_reader` before the migrations (so the kit's masking is applied), starts the stand-in model and TrustDesk, and deletes everything afterwards. A fresh database per take.
- Seed data: the **Load demo policies, people and requests** button (16 policy documents, 11 employees, 22 requests; see the previous version of this script in git history for the advice table). The two requests of scenes 3 and 4 are typed on camera. The stand-in knows their titles (`MULTI` in `scripts/fake_llm.py`): "Mountain long weekend" (approve; two reasons, each quoting its own passage) and "Three weeks at the coast" (reject; three reasons: notice, approval level, the Finance blackout that does not apply). A reason whose rule passage was not retrieved is left out, so the video fails loudly (the recorder waits for the text) rather than showing a wrong quote.

---

## 2. Scene table

The NARRATION column is checked by `build.py` against `docs/video/narration.txt` (edit both together; see [video/README.md](video/README.md)). Words = spoken words in the cell.

| # | Voice | Words | ON-SCREEN ACTION | NARRATION | ON-SCREEN CALLOUTS |
|---|---|---|---|---|---|
| 1 | am_michael | 76 | Dark card: "A stack of requests / and a 200-page policy handbook", then "I need answers I can trust." Then the architecture diagram with three labelled boxes: **Sample app** (TrustDesk), **Reference scaffolding: swap any of it** (identity provider, LLM gateway, vault) and **Platform: EDB Postgres on AgentPG** (pgvector, full-text, unaccent, tcn, edb_stat_monitor, edb_pg_tuner, query_advisor). Each box lights up as it is named; pgvector is highlighted with "No external vector database". | "I am an approver with a stack of employee requests and a two hundred page policy handbook. I need answers from our policies that I can trust. This is TrustDesk, a sample app. The identity provider, the LLM gateway and the vault are reference scaffolding, swappable for your own. What you are watching is EDB Postgres on the AgentPG platform, doing the work. The vectors live in the same Postgres. There is no external vector database." | Diagram labels |
| 2 | af_heart | 81 | Requests page, empty. Click **Load demo policies, people and requests**; the queue fills and the AI advice column fills in. Documents tab (16 policies), **View** on `Hr Vacation Policy` (the passages). | "First, the rules. We load a fictional company called Acme. One click adds sixteen policy documents, eleven people, and a set of requests. [pause 1.5] Behind the gateway, a model read each request, next to the policy passages that fit. That is all it does. It advises. Each policy is split into passages. Every passage is stored with a vector, next to its text, in Postgres. Open one, and you see the passages. That is the whole ingest. There is no second system." | "Each request already has a first opinion"; "Passage + vector + text index: one database" |
| 3 | am_eric | 79 | **New request**: employee José Álvarez (Marketing), type Vacation, title "Mountain long weekend", three working days starting in three weeks. **Submit request**. The review window opens and runs the pre-check: **Suggests approval**, two numbered reasons, each with a green line "Quote found word for word in the document". Click **Show the full passage**. | "Now the question I actually have. A new request arrives. José wants a long weekend. Three days, in eight weeks. I submit it. The pre-check runs. [pause 1.5] Suggests approval. And here is why. Each reason is numbered, and points at a passage of the policy. Seven days of notice are needed, and he gave much more. Under each one, a green line. Quote found word for word in the document. Open the full passage, and the sentence is right there." | "AI pre-check: suggests approval"; "Numbered reasons, each with a policy passage"; "The full passage, from the database row" |
| 4 | af_sarah | 163 | **New request**: José, Vacation, "Three weeks at the coast", 15 working days starting in two days. **Submit request**: **Suggests rejection**, three numbered reasons (notice 2 days against 30; 11 to 15 days need a Director; the Finance blackout does not apply to Marketing), three quotes, each with the green line. Open **Show the full passage** on the first. Type the reason in **Your decision**, click **Reject**. Then two real screenshots of the live fleet's REQ-000016 rejection (real model) and its verified quotes, with slow zoom and highlight boxes, banner "CAPTURED from the live AgentPG fleet, 28 Oct 2026". | "Now a request that breaks the rules. José asks for fifteen working days, starting in two days. [pause 1.5] Suggests rejection. Again, numbered reasons. Thirty days of notice are needed, and he gave two. Fifteen days need a Director. And one more. The finance blackout does not apply, because José works in Marketing. Look at the quotes. Each one carries the green line. That check is not done by the model. TrustDesk compares the quote with the source row in the database. If the words are not there, the line turns amber. So the model cannot invent a citation. The AI advises. I decide. I write my reason, and reject. The same check ran on the live platform, with a real model behind the gateway. This is a real rejection, captured on the twenty eighth of October. Two days of notice, against thirty. The live policy there asks for VP approval. And the finance blackout does not apply. Same green line, on the live database." | "Reason 1: notice, 2 days given, 30 required"; "Checked against the source row, in the database"; banner "CAPTURED from the live AgentPG fleet" |
| 5 | am_adam | 87 | **Search** tab: a plain-language question (vector rank + text rank on each result), then the misspelling `encyption`: Encryption Policy comes first with the badges **typo-tolerant #1** and **typo-tolerant match** (pg_trgm). **Requests** tab: type `alvares`: the rows of José Álvarez appear. | "Now, finding things. Search the policies in plain language. Each result shows its ranks. One from the vector, one from the exact words. Now I misspell a word on purpose. Encyption. [pause 1.0] No exact word matches. But look at the badge. Typo-tolerant. That is the pg_trgm extension, a third signal, in the same Postgres. A real embedding model ranks better. This demo embedder only matches words, so take this as the floor. Names work too. Type alvares, with a typo and no accent, and it finds José Álvarez." | "Vector rank + exact-word rank, same database"; "typo-tolerant: pg_trgm, same Postgres"; lower third "Real embedding model: better ranking"; "alvares finds José Álvarez" |
| 6 | af_bella | 76 | Requests list, status All. The client token (HR portal) POSTs a request: the row appears without a refresh. A second window approves it: the status changes on the first screen. Lower thirds: "tcn: Postgres sends NOTIFY request_changes", then "On AgentPG today: polling fallback". | "Status is live. An application, here an HR portal, sends a request. It appears in my list. I did not refresh. Then someone else decides it, and the status changes on my screen. This is the tcn extension. Postgres itself tells the app that a row changed. One honest note. On AgentPG today, the platform ingress does not flush server-sent events. So the browser falls back to polling, after six seconds. Same screen, a little slower." | "A new row, and nobody pressed refresh"; "Status changed by someone else, live" |
| 7 | am_eric | 94 | **Database** tab: "Not available on this database." (local Postgres has no edb_* extensions). Then four real screenshots captured on the live fleet (28 Oct 2026), slow zoom plus highlight box: the TrustDesk top queries (edb_stat_monitor), the masking view statements the monitor recorded, the edb_pg_tuner table, the query_advisor suggestions with the note that already-indexed columns are hidden. No number is retyped: they are read off the images. | "What is EDB doing underneath? The Database tab asks. On this laptop Postgres, there is nothing to show, and it says so. These extensions only exist on the platform. So these are real screenshots, captured on the live AgentPG fleet on the twenty eighth of October. First, the statement monitor. The top queries of the TrustDesk database. Further down, something nice. The platform's own statement monitor recorded the app re-asserting its masking. Then the tuner suggests memory settings. And the query advisor suggests indexes. It hides the ones where the column is already indexed." | "Local Postgres: these extensions do not exist here"; banner "CAPTURED from the live AgentPG fleet, 28 Oct 2026" |
| 8 | af_heart | 76 | Terminal page, real `psql` output of the read-only database user `ai_reader` (member of `a1p_readonly`) on the local database: `SELECT ... FROM employee_masked` shows masked names and e-mails; `SELECT ... FROM employee` fails with `permission denied for table employee`. A note card quotes the live capture of the grants re-opening. | "Last, safety. The AI should never learn who people are. Here is the read-only database user that an AI connects with. Ask for the employees view, and names and e-mails are masked. Ask for the real table, and the database says no. The masking lives in the database, not in the app. One finding for the platform team. A new database user on AgentPG re-opens the grants. So the app closes them again, every five minutes." | "Enforced by the database: SELECT revoked on employee, a masked view granted instead" |
| 9 | am_michael | 91 | Rejected request with the green line, then the card "Only EDB, in one database" and the end card. | "So, can I trust the answer? I saw each reason quoted, and each quote checked against the database. I saw search forgive typos, and status update live. And an AI user that cannot see names. Native vectors, hybrid search, and verified citations, in one database. That is the part only EDB gives you in one place. Look at the level of work you should be doing. Checking quotes, and deciding. Not hunting through two hundred pages. Everything here is a sample you can swap, run and read. Start from the README." | "Check quotes. Decide. Do not hunt through 200 pages." |


---

## 3. Timeline of the review cut

Scene length = audio + 0.7 s (0.2 s lead, 0.5 s tail). Every on-screen action starts when the sentence that illustrates it begins (`at(...)` in `record.spec.ts`).

| # | Time | Voice | Audio |
|---|---|---|---|
| scene 1 | 0:00 to 0:33 | am_michael | 32.6 s |
| scene 2 | 0:33 to 1:08 | af_heart | 34.5 s |
| scene 3 | 1:08 to 1:43 | am_eric | 34.3 s |
| scene 4 | 1:43 to 2:48 | af_sarah | 64.3 s |
| scene 5 | 2:48 to 3:27 | am_adam | 37.9 s |
| scene 6 | 3:27 to 3:58 | af_bella | 30.8 s |
| scene 7 | 3:58 to 4:38 | am_eric | 38.8 s |
| scene 8 | 4:38 to 5:08 | af_heart | 29.2 s |
| scene 9 | 5:08 to 5:47 | am_michael | 38.4 s |

Total 5:47. No static stretch is longer than about 7 seconds (checked with ffmpeg `freezedetect`).

---

## 4. Recording notes

- Build the review cut with one command (needs the `.venv`, Postgres 17 on `PATH`, `ffmpeg`, Chrome, `videos/tts`):

  ```bash
  cd trustdesk
  TD_PORT_RANGE=47100-47199 python3 docs/video/build.py --work /some/scratch/dir --name trustdesk --deliver /where/to/copy
  ```

  `--reuse-audio` keeps the narration of a previous run when only the recorder changed.
- Off camera before the first card (cut away by `build.py`): the admin password, a client token (`hr-portal`) and the employee José Álvarez are created.
- Scene 6 sends the new request with the **client token** and approves it from a second, unrecorded window that shares the admin session; the list in the recorded window changes by itself.
- Scene 8 is a terminal page with the real `psql` output of `ai_reader` on the local database; the recorder asserts that the masked view output contains `***` and that the base table answers `permission denied for table employee`.
- Scenes 4 and 7 show real screenshots captured on the live fleet on 28 Oct 2026 (`videos/live-captures/trustdesk-req-000016-*.jpg`, `trustdesk-insights-*.jpg`), embedded by the recorder with a slow zoom and highlight boxes read off the images, under the banner "CAPTURED from the live AgentPG fleet, 28 Oct 2026". No number is retyped. Each REQ-000016 shot says plainly that the live policy asks for VP approval; the local demo case cites Director approval (11 to 15 days).
- Known variation: with the stand-in the advice is identical on every take. With a real provider the wording changes and the recorder's text waits (and your callouts) may need new request titles.
- Do not film live deployment URLs.
