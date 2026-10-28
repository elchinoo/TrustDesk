# Video script: TrustDesk in six minutes

**Title:** TrustDesk: let AI read the rulebook, let people make the decision

**Length:** about 6:04 (narration about 819 words, spoken by neural voices that change with the topic; the length is set by the audio, see docs/video/README.md)

**Audience:** customers and colleagues who handle employee requests (HR, finance, managers), and the developers who connect an application to them. No Postgres or AI knowledge is assumed. Terms are explained when they first appear.

**Goal:** the viewer understands what TrustDesk does, has seen one request go from "submitted" to "decided" with quoted evidence, knows that a person always decides, and knows how an application sends requests.

**Facts every scene relies on** (verified in the code; see [GUIDE.md](GUIDE.md)): 19 request types; the AI advises, a person decides; quotes are checked word for word against the documents; overriding the advice needs a written reason of at least 10 characters; the client API token cannot see the advice or decide.

---

## 1. Prerequisites and seed data (reproducible locally)

Nothing in this recording needs a live service or a real company's data. Everything is fictional ("Acme Cloudworks" policies, eleven invented employees at example.com).

### 1.1 Machine

- [uv](https://docs.astral.sh/uv/) (the kit is installed from its release tag, no sibling checkout needed; see GUIDE section 4.1), Postgres 17 with pgvector and pg_trgm (Docker with `docker compose up -d db`, or local binaries), `curl` and `jq` for the API scene. For the automated recorder also Node 22+ and Google Chrome (section 3).
- A browser window set to 1920 x 1080 (what the recorder uses), light theme. Text is small at that size: enlarge in editing, or zoom the page to 125 percent for a manual take.
- An AI chat model. Request advice needs one; without it requests arrive with "No AI advice". Pick one of:
  - **A real provider** (recommended; the advice on screen is then genuine). Export `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_CHAT_MODEL`, `EMBEDDING_MODEL` and `EMBEDDING_DIM=768` before starting the server (for OpenAI: `https://api.openai.com/v1`, `text-embedding-3-small`). The key is a secret: never show the terminal where it is set, and rotate it afterwards if it was visible.
  - **The scripted stand-in** `scripts/fake_llm.py` (a demo stand-in, not a model: it answers the demo requests with fixed, believable advice, quoting the real policy passages; see GUIDE section 12). It gives a fully offline and repeatable take. Start it in its own terminal and point the server at it with the same variables a real provider would use:

    ```bash
    .venv/bin/python scripts/fake_llm.py --port 8099          # leave running; loopback only
    export LLM_BASE_URL=http://127.0.0.1:8099/v1 LLM_API_KEY=demo LLM_CHAT_MODEL=demo-standin EMBEDDING_MODEL=demo-standin EMBEDDING_DIM=768
    ```

    The stand-in becomes the environment account: Settings shows "Using the environment account: default" (no stored account, no vault and no `TRUSTDESK_ALLOW_PRIVATE_LLM_URLS` needed), so scene 3 reads exactly as written. Its embeddings are lexical, like the built-in demo embedder. It never appears in the video: the review window shows the model name `demo-standin` in small print at the bottom of the advice card, so crop it or accept it; do not present the advice as a real model's.
- With a real provider AI output varies between runs; with the stand-in it does not (section 1.4 lists the advice it gives). The narration below describes what the system does, and names requests by their demo titles. With a real provider, run the seed once before recording, open each request named below and note the advice it received; if one got different advice than the script expects, swap in another request from the same list or press **Run again**.

### 1.2 Start from an empty database

```bash
cd trustdesk
cp .env.example .env                         # set POSTGRES_PASSWORD to any throwaway value
docker compose down -v && docker compose up -d db
uv venv && uv pip install -e ".[dev]"
set -a; . ./.env; set +a                     # then export the LLM_* variables from 1.1 (stand-in or real provider)
.venv/bin/trustdesk migrate
.venv/bin/trustdesk serve 2>&1 | tee /tmp/trustdesk-recording.log    # leave running; the setup code appears in this log
```

The first start prints a line pair like `Open the app ... enter this one-time code` and `    XXXX-XXXX-XXXX`. The code uses the letters A to Z without I and O and the digits 2 to 9 (three groups of four). Open http://localhost:8000 in the recording browser. Do **not** set `AUTO_SEED`; the demo data is loaded on camera in scene 3.

Admin password to use in the recording (throwaway): `acme-demo-password-1`. It has 12 or more characters, which is the minimum.

### 1.3 Terminal for the API scene (prepare before recording, show only the commands)

Create a **Client application** token in the app (Settings > API tokens, name `hr-portal`, access "Client application") after the first sign-in and keep it in the environment of a second terminal. The token (it starts with `td_`) is shown once.

```bash
export TD=http://localhost:8000
export TOKEN=<the client token>
# GNU date; on macOS use: date -v+75d +%F  and  date -v+79d +%F
export START=$(date -d '+75 days' +%F) END=$(date -d '+79 days' +%F)
```

Employee `E2001` is created on camera; `E1002` (Tom Becker) already exists after the demo load and is used for the second call.

### 1.4 Seed data used on screen

Loaded by the **Load demo policies, people and requests** button (`POST /api/v1/demo`): 16 policy documents, 11 employees, 22 requests (7 already decided as history, 15 new ones that get AI advice). Dates are relative to today, so the demo never goes stale. The scenes use these new requests:

| Demo title (employee) | Why it is on screen | Advice from the stand-in |
|---|---|---|
| **Lunch with auditor (again)** (Carlos Mendes, E1006) | Identical to "Lunch with auditor" (same employee, type, date, amount). The system's own `duplicate` check fires without any AI: deterministic. Both lunch rows carry the warning, so use the one named "(again)" for the review scene. | Needs more information; quotes "Acme reimburses reasonable expenses that have a business purpose and follow this policy." and asks whether it was a separate lunch |
| **Gift for a customer** (Omar Haddad, E1004) | The override scene: a 100 USD gift card for a customer. | **Suggests rejection**; quotes "Cash and gift cards are never allowed." |
| **Two weeks in Portugal** (Tom Becker, E1002), **Hotel in Chicago** (Carlos Mendes, E1006) | Other requests that get "Suggests rejection" (notice too short; 340 USD above the 200 USD hotel limit). | Suggests rejection |
| **Long weekend** (Lena Fischer, E1003), **Lunch with auditor** | The requests that get "Suggests approval". Use "Long weekend" for a quick approve if time is short. | Suggests approval |
| **Team dinner with Contoso** (Grace Liu, E1005), **Wedding**, **Back pain** and the rest | Not expected to be approved: 450 USD for three guests is above 100 USD per person, the wedding is in the first 90 days of employment, the back-pain leave has no certificate. | Needs more information |

Totals with the stand-in: 3 suggest rejection, 2 suggest approval, 10 need more information.

With a real provider, if **no** new request received "Suggests rejection", use "Suggests approval" and reject it instead: the override rule is symmetric (rejecting against an approval advice also needs the written reason). The recorder does this by itself.

### 1.5 Reset between takes

Stop the server, then `docker compose down -v && docker compose up -d db`, then repeat 1.2. The setup code changes on every start.

---

## 2. Scene table

Timestamps are cumulative targets. "Words" is the narration word count. All clicks are in the web app at http://localhost:8000 unless a terminal is named.

| # | Time | Words | ON-SCREEN ACTION | NARRATION | ON-SCREEN CALLOUTS |
|---|---|---|---|---|---|
| 1 | 0:00 to 0:24 | 58 | Title card (logo, "TrustDesk"), 3 seconds. Cut to the empty "Set up TrustDesk" screen in the browser. | "Every company has rules. How much vacation you get. What a customer dinner may cost. Who approves a trip. And every day, people ask for things. TrustDesk is a workspace where those requests meet your rules. An AI reads your own policy documents, and gives advice. But the advice is only advice. A person always makes the decision." | Lower third: "TrustDesk: compliance workspace on Postgres". Text: "The AI advises. People decide." |
| 2 | 0:24 to 0:54 | 69 | Switch to the terminal running the server, highlight the line `XXXX-XXXX-XXXX` for 2 seconds. Switch to the browser. Click the **Setup code** field, paste the code. Click **Choose an admin password**, type the password, repeat it, click **Create admin password**. The Requests page appears, showing "Nothing here yet". | "First start. There are no key files to copy, and no default password. The server prints a one-time code in its log. Whoever holds that code creates the administrator password, right here in the browser. That is a deliberate choice. Until someone completes this step, every page of the application refuses to answer. The password needs at least twelve characters. Now we are in. And the workspace is empty." | Callout on the log line: "One-time code, printed only in the server log". Callout on the form: "12 characters minimum". |
| 3 | 0:54 to 2:10 | 157 | Click **Load demo policies, people and requests** (button reads "Loading... (this embeds the policy documents)" while it works). When the queue appears, point at the counts row and at the **AI advice** column as badges change from "..." to "Suggests approval", "Suggests rejection" or "Needs more information". Click the **Documents** tab, scroll the list of 16 policies, click **View** on `Hr Vacation Policy` to show the chunks. Return to **Requests**. Open **Settings**, scroll to **Request validation**: show "Using the environment account: default" (real provider) or the chosen account. | "Now, some data to work with. We load a fictional company called Acme. One click adds sixteen policy documents, eleven employees, and a mix of good and problematic requests. [pause 3.0] Here they are. Each one was sent to the AI, in the background. For each one, the system finds the policy passages that fit, and asks the model for a first opinion. That is all the model does. It reads, and it advises. It never decides. Look at the advice column. [pause 1.5] Every request now has a first opinion. The policies live here. Markdown, text, or PDF. Open one, and you see the passages it was split into. They are stored in Postgres, with one index for meaning, and one for exact words. Both live in the same database as everything else. The model that does the checking is chosen in Settings. It sees the request text and the policy passages. Names, e-mail addresses, and employee numbers never leave." | Callout on Documents: "Markdown, text or PDF, 10 MB each". Callout on Settings line: "Employee names are never sent to the model". |
| 4 | 2:10 to 3:09 | 155 | In **Requests**, choose status **Open** (default). Type `auditor` in the search box so the two "Lunch with auditor" rows show. Click **Lunch with auditor (again)** to open the review window. Slowly scroll the right-hand column: the recommendation badges (with the stand-in: "Needs more information"), the summary, **Automatic checks** (point at the duplicate warning; it sits lower in the advice card, below the cited passages), **Why** (point at a reason with its passage number), the cited passage: point at "Quote found word for word in the document", click **Show the full passage**,  Scroll the left column: **Request**, **Employee**, **History**. | "Now the AI advice. Open the request called Lunch with auditor, again. This is the review window. The advice is on the right. Before the model says anything, the system computes facts by itself. Working days, notice given, overlaps, and possible duplicates. A model is poor at counting days. So we do not ask it to. Here, the system noticed that this lunch is identical to another one. Below that, the reasons. Each reason points to a passage of your policy, with a quotation. TrustDesk checks that the quotation really appears in the passage, word for word. If it does not, the quote is marked as unverified. And a yes or a no with no rule behind it is downgraded to needs more information. On the left, the history. Who did what, and when. The advice is a first reading. It saves the approver the search for the rule. It does not replace the judgment." | Callouts: "Facts computed by the system, not guessed by the model". "Quote found word for word in the document". "Automatic check: duplicate". "History = audit trail". |
| 5 | 3:09 to 3:53 | 98 | Close the window. Open **Gift for a customer** (it received **Suggests rejection**, see 1.4). In **Your decision**, click **Approve** without writing a note: show the error message that asks for a justification. Type a note of at least 10 characters (for example "Director approved the exception by phone on Monday."), click **Approve**. The window closes. Filter status **Approved** and show the row. | "Now the human part. This request has advice to reject. The approver disagrees. Watch what happens if they simply press Approve. [pause 1.5] TrustDesk asks for a written reason. At least ten characters, because going against the advice has to be explained. So the approver writes one, and approves again. [pause 1.5] The decision is recorded under the approver's own name, together with what the AI had recommended at that moment. Rejecting a request, or asking for more information, needs a note too. The employee will read it. Nothing is hidden. The decision, its reason, and the advice it overruled stay together." | Callout on the error: "Overriding the AI needs a written reason". Callout on the History line: "Decision recorded with the approver's name and the AI advice". |
| 6 | 3:53 to 4:30 | 74 | Click the **Reports** tab. Select **Overview**, click **Run**. Select **Decisions against AI advice**, click **Run**: the decision just made is the first row. Click **Download CSV** (do not open the file; a screen recording of the page does not include the browser's download bar, so add a short "CSV downloaded" caption in editing). | "Now, the reports. Managers and auditors want the overall picture. So there are nine reports. Volumes and outcomes. Expenses by department. Who is away next month. This one matters most. Every decision that went against the advice, with its justification. It answers the first question an auditor will ask. When people disagreed with the tool, did they say why? Any report can be printed, or downloaded as a spreadsheet. Let's download this one. [pause 2.0] Done." | Callout: "Nine reports, tables and CSV". Callout on the first row: "Override with justification". |
| 7 | 4:30 to 5:11 | 89 | Switch to the second terminal. Run and show output of: `curl -s -X POST $TD/api/v1/employees -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"employee_no":"E2001","name":"Alex Example","email":"alex@example.com","department":"Sales","level":"L3"}' \| jq .employee_no`. Then: `REF=$(curl -s -X POST $TD/api/v1/requests -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Idempotency-Key: alex-demo-1" -d "{\"employee_no\":\"E2001\",\"type\":\"vacation\",\"title\":\"Summer break\",\"description\":\"One week at the coast\",\"start_date\":\"$START\",\"end_date\":\"$END\"}" \| jq -r .ref); echo $REF`. Then: `curl -s $TD/api/v1/requests/$REF -H "Authorization: Bearer $TOKEN" \| jq '{ref,status,has_advice: has("ai")}'`. Switch to the browser: the new request is in the queue. | "Where do requests come from? From your own applications. Say, an HR portal. It starts by creating an employee, through a small web API. [pause 1.5] Then it sends a request. A key in the call makes a repeat safe, so there are no duplicates. [pause 2.0] Back in the app, the request is already in the queue. Now one last call, to read it back. [pause 1.5] The application sees the status. But the advice field is not there. The token that sends requests can never read the advice, or decide. Only people can." | Callout on the token: "Client application token: no advice, no decisions, no reports". Callout on `has_advice: false`. Text: "API reference: API.md". |
| 8 | 5:11 to 5:40 | 67 | Click the **Search** tab. Type `How many vacation days do employees get each year?` and press Enter. Point at the first result: document, heading, version, and the vector and full-text ranks. Click **Connect**, show the client list and the line "Server URL". | "Now, searching the rules. The documents can be searched in plain language. [pause 1.5] Every result shows where it came from. The document, the heading, and the version. Assistants such as Claude Code or Cursor can search them too, through a read-only MCP connection. MCP is a standard way for an assistant to use a tool. The Connect tab has the setup, with this server's address already filled in." | Callout: "Results show document, heading, version". Callout: "MCP: search_knowledge, get_policy_passage, list_documents (read-only)". |
| 9 | 5:40 to 6:04 | 52 | Return to the **Requests** queue (counts visible). Hold on the logo for 3 seconds, then the end card. | "To recap. Load your rules. Let your applications send requests. TrustDesk adds facts, finds the rules, and offers advice with quoted evidence. A person decides. Every override is explained, and reported. Everything lives in one Postgres database. To try it, start from the README. Then open the guide in the docs folder." | End card: "README.md and docs/GUIDE.md". "Postgres + pgvector + MCP". No URL (deployment addresses change). |

Narration word counts above are targets; recount after editing (see 3.3).

---

## 3. Recording notes

### 3.1 The recorder (rehearsed end to end)

`docs/video/record.spec.ts` performs every on-screen action below and records it. It was run in full against the stand-in model; the actions in the table are what it does (selectors verified). It starts everything itself: a throwaway Postgres on 127.0.0.1 (database `trustdesk_video_test`, no unix socket), the stand-in and TrustDesk on free ports, in a private directory, and deletes them afterwards.

```bash
cd docs/video && npm install        # @playwright/test 1.64.1; uses the installed Google Chrome (channel "chrome")
npx playwright test                 # video: docs/video/out/<test>/video.webm (git-ignored); HEADED=1 to watch
SLOWMO=0 HOLD=0.2 npx playwright test   # quick rehearsal, not at human pace
```

Needs the repository's `.venv` (`uv venv && uv pip install -e ".[dev]"`), Postgres 17 binaries with pgvector on `PATH` (or `TD_PG_BIN`), and Playwright's ffmpeg for the video (`npx playwright install ffmpeg`; on a machine that already has a VP9-capable ffmpeg, point `PLAYWRIGHT_BROWSERS_PATH` at a folder holding `ffmpeg-<n>/ffmpeg-mac` linked to it). Settings: 1920 x 1080 viewport and video, 400 ms between actions (`slowMo`), one browser context, orange callout boxes with a caption drawn over the page (the recorder bypasses the page's content security policy only to draw them; the app is unchanged). Each scene is padded to the end time in the scene table so the footage is at least as long as the narration; the editor trims.

Differences from the plan above that the rehearsal found: the terminal scenes (2 and 7) are drawn as terminal pages inside the same recording (real server log, real API responses; the token is shown masked) instead of filming a second terminal; the client token is created off camera in a second, unrecorded Chrome context that shares the session cookie (the Settings steps are the ones in the last row of the table); the page recording has no browser chrome, so no download bar (scene 6); Chrome runs without the 110 percent zoom.

| Step | Playwright actions |
|---|---|
| Start | Start the server with its output redirected to a file (section 1.2). Read the file until it matches `/[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}/` (the line after "enter this one-time code"). Poll `GET /readyz` until 200. |
| Scene 1 | `page.goto("http://localhost:8000")`; wait for `#auth-card` containing "Set up TrustDesk". |
| Scene 2 | Fill `#setup-code` with the code, `#setup-pw` and `#setup-pw2` with the password; click the button named "Create admin password"; wait for `#nav` to be visible and the toast "Admin password set. You are signed in." |
| Scene 3 | Click the button named "Load demo policies, people and requests". Wait up to 120 seconds for `#req-onboarding` to be hidden. Wait until validation ends: loop `await page.evaluate(() => fetch("/api/v1/requests/counts").then(r => r.json()))` until `by_status.submitted === 0` (timeout 180 seconds; with a slow provider some may end as "No AI advice", acceptable). Click `a[data-tab="documents"]`, wait for `#doc-body tr`; click the **View** button in the row whose title contains "Hr Vacation Policy"; wait for `#doc-detail`. Click `a[data-tab="requests"]`, then `a[data-tab="settings"]`, wait for `#settings-validation` and scroll it into view; screenshot or hold 4 seconds. |
| Scene 4 | Click `a[data-tab="requests"]`. Select `#req-status` value `open` (default). Fill `#req-q` with `auditor`. Click the row (`#req-body tr`) containing "Lunch with auditor (again)". Wait for `#review[open]` and for `#review-body` to contain "AI pre-validation" and not "Checking the request". Scroll through `#review-body`; click the summary "Show the full passage" (a `details` element) once. |
| Scene 5 | Close the dialog (`#review-close`). Clear `#req-q`. Select `#req-ai` value `reject`; if the list is empty, select `approve` and note this in the run log (the script then rejects instead). Click the first row. Click the button named "Approve" (or "Reject") with `#decision-note` empty; assert the paragraph `p.error[role=alert]` inside the dialog has text. Fill `#decision-note` with the justification (10 or more characters); click the same button again; wait for `#review` to close and the toast "Approved" (or "Rejected"). Select `#req-status` value `approved` (or `rejected`) and hold on the row. |
| Scene 6 | Click `a[data-tab="reports"]`. Select `#rep-name` option "Overview"; click the button named "Run" in `#rep-form`; wait for `#rep-out` to contain a table. Select the option "Decisions against AI advice" (report `overrides`); Run; wait for the table. Click `#rep-csv` inside `page.waitForEvent("download")` and discard the file. |
| Scene 7 | Not a browser step. Run the three `curl` commands from the scene table in a terminal capture (a separate terminal recording, or Playwright's `page` showing a local HTML page that replays recorded output). Afterwards `page.goto` the Requests page and wait for a row containing "Summer break". |
| Scene 8 | Click `a[data-tab="search"]`; fill `#q`; press Enter; wait for `#results` to contain a result. Click `a[data-tab="connect"]`; wait for `#mcp-url`. |
| Scene 9 | Click `a[data-tab="requests"]`; wait for `#req-counts`. |
| Admin token (off camera, before scene 7) | `a[data-tab="settings"]`; in `#settings-security` fill `#tk-name` with `hr-portal`, select `#tk-scope` value `client`, click "Create token"; read the token from the `pre.code code` block that appears (shown once); store it as `TOKEN` for the terminal step. |

Keep the admin password and the token out of the video: type them with `page.fill` (not visible once the field is masked) and do not film the terminal where `TOKEN` is exported, or blur that region in editing.

### 3.2 Known sources of variation

- AI wording, recommendation and timing differ between runs and providers. Re-record scenes 4 and 5 if the chosen request received unexpected advice, or choose another request (section 1.4). The automatic checks (duplicate, overlap, notice, receipt) do not vary.
- Demo dates are relative to today; on-screen dates will differ from the script.
- The request references (`REQ-000001` and so on) depend on the database; always read `ref` from the response.
- If the model is slow, scene 3's waiting time can be cut in editing (speed ramp or a jump cut), but keep the badges changing on screen at least once.
- Do not film live deployment URLs. Sandbox addresses change whenever an app is recreated.

### 3.3 Timing check

The spoken length is measured, not estimated: `build.py` speaks `narration.txt` and every scene lasts its audio plus 1.1 s. The "Words" column is only a guide (the voices speak about 110 to 140 words per minute, pauses included). Count again after any edit, for example by pasting each narration cell into `wc -w`. If a take runs over 7 minutes, cut scene 8 first (it is the only scene the main story does not need), then shorten scene 6.

### 3.4 Accessibility and delivery

Add captions from the narration text (the table is the source). Use callouts as short text overlays of at most 8 words, shown for 3 to 4 seconds. Export 1080p. The narration is `docs/video/narration.txt` (written for listening; this column mirrors it, and `build.py` refuses to run if they differ). Voices change with the topic: scenes 1-2 af_heart, 3 am_michael, 4-5 af_sarah, 6-7 am_eric, 8-9 af_heart.

---

## 4. Alternate cut for the ecosystem demo (optional, 90 seconds)

For the fleet story (the simulator sends requests, TrustDesk advises) record from a deployed fleet instead of localhost: in CityPulse Simulator create a company targeting TrustDesk (see `ecosystem/docs/DEPLOY.md`, section 10), then show the requests arriving in TrustDesk's queue with advice. This cut needs a deployed fleet and was not recorded or verified for this script. Use fleet addresses read from `ecoctl status` on the day, and do not show them in the final video.
