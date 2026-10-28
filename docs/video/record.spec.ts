/**
 * Rehearses and records docs/VIDEO-SCRIPT.md: every on-screen action of the scene table, in a real Chrome, 1920x1080, with
 * Playwright's video enabled (written to docs/video/out/, git-ignored) and callouts drawn over the page.
 *
 * Everything is local: a throwaway Postgres on 127.0.0.1, TrustDesk on a free port, and the DEMO STAND-IN model
 * (scripts/fake_llm.py) instead of a real provider, so the AI advice is the same on every take.
 *
 *   cd docs/video && npm install && npx playwright test          # headless
 *   HEADED=1 npx playwright test                                 # watch it
 *   SLOWMO=0 HOLD=0.2 npx playwright test                        # fast rehearsal (a video, but not at human pace)
 *
 * The terminal scenes (2: the server log, 7: the API calls) are drawn as terminal pages in the same video, with the real log
 * and the real responses of the real calls. Replace them with a screen capture of your own terminal if you prefer.
 */
import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { startStack, type Stack } from './lib';

const PASSWORD = 'acme-demo-password-1'; // throwaway; the database is deleted after the run
const HOLD = Number(process.env.HOLD ?? 1); // multiplies every scene length and cue time (rehearsal)

let stack: Stack;

test.beforeAll(async () => {
  stack = await startStack();
});
test.afterAll(async () => {
  await stack?.stop();
});

/**
 * Scene pacing and sync. build.py runs narrate.py first and hands the result over (NARRATION_JSON: {"1": {duration, cues:[{text,
 * start, end}]}, ...}). A scene lasts at least LEAD + its narration + TAIL (>= 0.7 s after the voice). Inside a scene, every
 * on-screen action waits for the sentence that illustrates it: `await at(page, 'words of the sentence')` blocks until that
 * sentence starts being spoken (minus a little lead, because the action itself takes a moment to show). If the picture is already
 * late (a slow page), the action runs at once and the delay is logged and written to TIMES_JSON ("sync"). HOLD scales all times.
 */
const LEAD = 0.3;
const TAIL = 0.8;
type Cue = { text: string; start: number; end: number };
const NARRATION: Record<string, { duration: number; cues: Cue[] }> = process.env.NARRATION_JSON ? JSON.parse(fs.readFileSync(process.env.NARRATION_JSON, 'utf8')) : {};
const T0 = { at: 0 };
const SC = { n: 0, start: 0, targetMs: 0 };
const times: { scene: number; start: number; end: number }[] = [];
const sync: { scene: number; key: string; cueAt: number; actualAt: number; lateBy: number }[] = [];
const rel = () => (Date.now() - T0.at) / 1000;
function begin(n: number) {
  SC.n = n;
  SC.start = Date.now();
  SC.targetMs = (LEAD + (NARRATION[String(n)]?.duration ?? 20) + TAIL) * 1000 * HOLD;
  times.push({ scene: n, start: rel(), end: 0 });
}
/** Waits until the sentence containing `key` starts (the voice starts LEAD seconds into the scene); `lead` = how early the action begins. */
async function at(page: Page, key: string, lead = 0.5) {
  const cues = NARRATION[String(SC.n)]?.cues;
  if (!cues) return;
  const cue = cues.find((c) => c.text.includes(key));
  if (!cue) throw new Error(`scene ${SC.n}: no narrated sentence contains "${key}"`);
  const due = (LEAD + cue.start - lead) * 1000 * HOLD;
  const now = Date.now() - SC.start;
  if (now < due) await page.waitForTimeout(due - now);
  const late = (Date.now() - SC.start - due) / 1000;
  sync.push({ scene: SC.n, key, cueAt: +(LEAD + cue.start).toFixed(2), actualAt: +((Date.now() - SC.start) / 1000).toFixed(2), lateBy: +late.toFixed(2) });
  if (late > 1) console.log(`scene ${SC.n}: "${key}" is ${late.toFixed(1)} s late`);
}
const pause = (page: Page, seconds: number) => page.waitForTimeout(seconds * 1000 * HOLD);
/** Closes the scene: pads to its target length (never shorter than the narration). */
async function endScene(page: Page) {
  const left = SC.targetMs - (Date.now() - SC.start);
  if (left > 0) await page.waitForTimeout(left);
  times[times.length - 1].end = rel();
}

/** Overlay helpers, installed in every page of the context. They live in the top layer (popover) so they also show above dialogs. */
const OVERLAY = () => {
  const mk = (css: string, text: string) => {
    const el = document.createElement('div');
    el.setAttribute('popover', 'manual');
    el.setAttribute('data-recorder', '');
    el.style.cssText =
      'position:fixed;inset:auto;margin:0;border:0;padding:0;background:none;overflow:visible;pointer-events:none;' + css;
    el.textContent = text;
    document.documentElement.append(el);
    (el as any).showPopover?.();
    return el;
  };
  const clear = () => document.querySelectorAll('[data-recorder]').forEach((e) => e.remove());
  (window as any).__callout = (text: string, selector: string | null, nth = 0, ms = 3500) => {
    clear();
    clearTimeout((window as any).__ct);
    (window as any).__ct = setTimeout(clear, ms);
    const target = selector ? (document.querySelectorAll(selector)[nth] as HTMLElement | undefined) : undefined;
    if (target) {
      target.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior });
      const r = target.getBoundingClientRect();
      mk(
        `left:${r.left - 6}px;top:${r.top - 6}px;width:${r.width + 12}px;height:${r.height + 12}px;border:4px solid #ff9f1c;border-radius:10px;box-shadow:0 0 0 6px rgba(255,159,28,.25)`,
        '',
      );
      const below = r.bottom + 70 < window.innerHeight;
      mk(
        `left:${Math.max(16, Math.min(r.left, window.innerWidth - 640))}px;${below ? `top:${r.bottom + 14}px` : `top:${Math.max(8, r.top - 64)}px`};max-width:620px;` +
          'background:#1b2430;color:#fff;font:600 24px/1.3 system-ui,sans-serif;padding:10px 18px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.35)',
        text,
      );
    } else {
      mk(
        'left:50%;transform:translateX(-50%);bottom:56px;max-width:1200px;text-align:center;background:#1b2430;color:#fff;font:600 30px/1.3 system-ui,sans-serif;padding:14px 28px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.35)',
        text,
      );
    }
  };
  (window as any).__lower = (title: string, sub: string) => {
    clear();
    clearTimeout((window as any).__ct);
    mk(
      'left:48px;bottom:48px;background:rgba(27,36,48,.92);color:#fff;font:700 34px/1.25 system-ui,sans-serif;padding:16px 26px;border-left:8px solid #ff9f1c;border-radius:6px;max-width:1100px',
      title,
    );
    if (sub)
      mk('left:48px;bottom:12px;color:#fff;font:600 24px/1.2 system-ui,sans-serif;text-shadow:0 2px 6px #000;max-width:1100px', sub);
  };
  (window as any).__clear = clear;
};

/** Draws a callout (it removes itself after `seconds`); does not wait, so the next `at` decides when the story goes on. */
const callout = (page: Page, text: string, selector: string | null, nth = 0, seconds = 3.5) =>
  page.evaluate(([t, s, n, ms]) => (window as any).__callout(t, s, n, ms), [text, selector, nth, seconds * 1000 * HOLD] as const);

const CARD_CSS = `html,body{margin:0;height:100%;background:#0f2a43;color:#fff;font-family:system-ui,sans-serif}
.c{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center}
h1{font-size:96px;margin:0}p{font-size:36px;margin:0;opacity:.9}`;
const blank = (page: Page) => page.goto('about:blank'); // setContent keeps the old URL: leave the app first
const card = async (page: Page, title: string, lines: string[]) => {
  await blank(page);
  await page.setContent(`<style>${CARD_CSS}</style><div class="c"><h1>${title}</h1>${lines.map((l) => `<p>${l}</p>`).join('')}</div>`);
};

const TERM_CSS = `html,body{margin:0;height:100%;background:#12161c;color:#d8dee9;font:26px/1.45 ui-monospace,Menlo,monospace}
pre{margin:0;padding:48px 64px;white-space:pre-wrap;word-break:break-all}.p{color:#7fd1ae}.hl{background:#ff9f1c;color:#111;padding:2px 8px;border-radius:4px}.dim{color:#7b8794}`;
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const termPage = async (page: Page, body: string) => {
  await blank(page);
  await page.setContent(`<style>${TERM_CSS}</style><pre id="t">${body}</pre>`);
};

/** Types a shell line (as the viewer would see it) in one in-page animation of `seconds`, then returns the new page text. */
async function typeLine(page: Page, current: string, cmd: string, seconds: number): Promise<string> {
  const prefix = current + '<span class="p">$ </span>';
  await page.evaluate(
    async ([pre, text, ms]) => {
      const el = document.getElementById('t')!;
      const esc = (x: string) => x.replace(/&/g, '&amp;').replace(/</g, '&lt;');
      const step = Math.max(1, Math.ceil(text.length / (ms / 40)));
      for (let i = 0; i < text.length; i += step) {
        el.innerHTML = pre + esc(text.slice(0, i + step));
        await new Promise((r) => setTimeout(r, ms / Math.ceil(text.length / step)));
      }
    },
    [prefix, cmd, seconds * 1000 * HOLD] as const,
  );
  return prefix + esc(cmd) + '\n';
}

const api = (path: string, init: RequestInit & { token?: string; cookie?: string } = {}) =>
  fetch(stack.url + path, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init.token ? { authorization: `Bearer ${init.token}` } : {}), ...(init.cookie ? { cookie: init.cookie } : {}), ...(init.headers as Record<string, string>) },
  });

const iso = (days: number) => new Date(Date.now() + days * 864e5).toISOString().slice(0, 10);

test('TrustDesk in six minutes', async ({ page, context, browser }) => {
  test.setTimeout(30 * 60_000);
  await context.addInitScript(OVERLAY);
  const notes: string[] = [];
  T0.at = Date.now();
  const setTerm = (h: string) => page.evaluate((x) => (document.getElementById('t')!.innerHTML = x), h);
  const bannerCss = 'position:fixed;left:64px;bottom:64px;background:#1b2430;color:#fff;font:600 30px system-ui;padding:12px 24px;border-radius:10px;border-left:8px solid #ff9f1c';
  const banner = (text: string) =>
    page.evaluate(([t, css]) => {
      const d = document.createElement('div');
      d.textContent = t;
      d.style.cssText = css;
      document.body.append(d);
    }, [text, bannerCss] as const);

  // Every on-screen action waits for the sentence of docs/video/narration.txt that illustrates it (`at`).

  // ---- Scene 1: title card, then the empty setup screen -------------------------------------------------
  begin(1);
  const cardAt = rel();
  await card(page, 'TrustDesk', ['Compliance workspace on Postgres', 'The AI advises. People decide.']);
  await at(page, 'TrustDesk is a workspace', 0.6);
  await page.goto(stack.url);
  await expect(page.locator('#auth-card')).toContainText('Set up TrustDesk');
  await page.evaluate(() => (window as any).__lower('TrustDesk: compliance workspace on Postgres', 'The AI advises. People decide.'));
  await endScene(page);

  // ---- Scene 2: the one-time code in the server log, then create the admin password ---------------------
  begin(2);
  const log = stack.serverLog();
  const all = log.split('\n').filter((l) => !/^\s*$/.test(l));
  // the block around the code (later access-log lines would push it off the screen)
  const at0 = Math.max(0, all.findIndex((l) => stack.setupCode && l.includes(stack.setupCode)));
  const lines = all.slice(Math.max(0, at0 - 4), at0 + 3);
  const html = lines.map((l) => (stack.setupCode && l.includes(stack.setupCode) ? `<span id="code">${esc(l)}</span>` : esc(l))).join('\n');
  await termPage(page, `<span class="dim"># trustdesk serve</span>\n${html}`);
  await at(page, 'The server prints');
  await page.evaluate(() => document.getElementById('code')?.classList.add('hl'));
  await banner('One-time code, printed only in the server log');
  await at(page, 'Whoever holds');
  await page.goto(stack.url);
  await expect(page.locator('#auth-card')).toContainText('Set up TrustDesk');
  await page.getByLabel('Setup code').click();
  await page.getByLabel('Setup code').fill(stack.setupCode);
  await at(page, 'That is a deliberate choice');
  await page.locator('#setup-pw').fill(PASSWORD);
  await page.locator('#setup-pw2').fill(PASSWORD);
  await at(page, 'The password needs at least');
  await callout(page, '12 characters minimum', '#setup-pw');
  await at(page, 'Now we are in', 0.8);
  await page.getByRole('button', { name: 'Create admin password' }).click();
  await expect(page.locator('#nav')).toBeVisible();
  await expect(page.locator('#req-onboarding')).toContainText('Nothing here yet');
  await endScene(page);

  // ---- Scene 3: load the Acme data, watch the advice arrive --------------------------------------------
  begin(3);
  await page.locator('a[data-tab="requests"]').click(); // sets the page address to #requests, so the list refreshes itself while requests are checked
  await at(page, 'One click adds', -5.8); // the click lands as the sentence ends
  await page.getByRole('button', { name: 'Load demo policies, people and requests' }).click();
  const loadStart = Date.now();
  await expect(page.locator('#req-onboarding')).toBeHidden({ timeout: 120_000 });
  // How long the checks take (the list refreshes itself every 2.5 s while requests wait, once the address is #requests).
  await expect
    .poll(async () => (await page.evaluate(() => fetch('/api/v1/requests/counts', { headers: {} }).then((r) => r.json()))).by_status.submitted, { timeout: 180_000, intervals: [500] })
    .toBe(0);
  const dots = () => page.locator('#req-body tr td:nth-child(5)', { hasText: '…' }).count();
  await expect.poll(dots, { timeout: 15_000, intervals: [250], message: 'the list refreshes itself' }).toBe(0);
  notes.push(`all requests had their advice ${((Date.now() - loadStart) / 1000).toFixed(1)} s after the load click`);
  await at(page, 'Here they are');
  await callout(page, 'Each request is checked in the background', '#req-counts', 4);
  await at(page, 'Look at the advice column');
  await callout(page, 'AI advice, per request', '#req-table thead th:nth-child(5)', 0, 4.5);
  await at(page, 'Every request now has');
  const advice = await page.evaluate(async () => {
    const list = (await fetch('/api/v1/requests?limit=50&status=').then((r) => r.json())).items;
    return list.filter((r: any) => r.ai).map((r: any) => `${r.ref} ${r.ai.recommendation.padEnd(10)} ${r.title}`);
  });
  notes.push('advice received (the stand-in is deterministic, so this should be identical on every run):\n    ' + advice.join('\n    '));
  await at(page, 'The policies live here');
  await page.locator('a[data-tab="documents"]').click();
  await expect(page.locator('#doc-body tr').first()).toBeVisible();
  await expect(page.locator('#doc-body tr')).toHaveCount(16);
  await callout(page, 'Markdown, text or PDF, 10 MB each', '#dropzone', 0, 3);
  await at(page, 'Open one');
  await page.locator('#doc-body tr', { hasText: 'Hr Vacation Policy' }).getByRole('button', { name: 'View' }).click();
  await expect(page.locator('#doc-detail')).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, Math.max(0, document.getElementById('doc-detail')!.getBoundingClientRect().top + window.scrollY - 90)));
  await at(page, 'The model that does the checking');
  await page.locator('a[data-tab="settings"]').click();
  await expect(page.locator('#settings-validation')).toContainText('environment account');
  await page.locator('#settings-validation').scrollIntoViewIfNeeded();
  await at(page, 'It sees the request text');
  await callout(page, 'Employee names are never sent to the model', '#settings-validation', 0, 6);
  await endScene(page);

  // ---- Scene 4: the review window -----------------------------------------------------------------------
  begin(4);
  await at(page, 'Now the AI advice');
  await page.locator('a[data-tab="requests"]').click();
  await page.locator('#req-status').selectOption('open');
  await page.locator('#req-q').fill('auditor');
  await expect(page.locator('#req-body tr')).toHaveCount(2);
  await at(page, 'Open the request called');
  await page.locator('#req-body tr', { hasText: 'Lunch with auditor (again)' }).click();
  await expect(page.locator('#review')).toHaveAttribute('open', '');
  await expect(page.locator('#review-body')).toContainText('AI pre-validation');
  await expect(page.locator('#review-body')).not.toContainText('Checking the request');
  await at(page, 'Before the model says anything');
  await callout(page, 'Facts computed by the system, not guessed by the model', '#review-body .checks', 0, 6);
  await at(page, 'Here, the system noticed');
  await callout(page, 'Automatic check: duplicate', '#review-body .checks li', 0, 4.5);
  await at(page, 'Below that, the reasons');
  await callout(page, 'Each reason points to a policy passage', '#review-body ol.reasons', 0, 4.5);
  await at(page, 'TrustDesk checks that');
  await callout(page, 'Quote found word for word in the document', '#review-body .ok-text', 0, 5);
  await at(page, 'If it does not');
  await page.locator('#review-body details summary').first().click();
  await at(page, 'On the left, the history');
  await callout(page, 'History = audit trail', '#review-body ol.timeline', 0, 5);
  await endScene(page);

  // ---- Scene 5: override needs a written reason ---------------------------------------------------------
  begin(5);
  await at(page, 'Now the human part');
  await page.locator('#review-close').click();
  await page.locator('#req-q').fill('');
  await page.locator('#req-ai').selectOption('reject');
  await expect(page.locator('#req-body tr').first()).toBeVisible();
  let verb: 'Approve' | 'Reject' = 'Approve';
  if ((await page.locator('#req-body tr').count()) === 0) {
    notes.push('no request received "Suggests rejection": used approval advice and rejected it instead');
    await page.locator('#req-ai').selectOption('approve');
    verb = 'Reject';
  }
  const gift = page.locator('#req-body tr', { hasText: 'Gift for a customer' });
  await ((await gift.count()) ? gift : page.locator('#req-body tr').first()).click();
  await expect(page.locator('#review-body')).toContainText('Your decision');
  await at(page, 'Watch what happens', -1.5); // the click lands as the narrator says "press Approve"
  await page.locator('#review').getByRole('button', { name: verb, exact: true }).click();
  await expect(page.locator('#review p.error[role=alert]')).not.toBeEmpty();
  await at(page, 'TrustDesk asks for a written');
  await callout(page, 'Overriding the AI needs a written reason', '#review p.error[role=alert]', 0, 4.5);
  await at(page, 'So the approver writes one');
  await page.locator('#decision-note').fill('Director approved the exception by phone on Monday.');
  await pause(page, 1.5);
  await page.locator('#review').getByRole('button', { name: verb, exact: true }).click();
  await expect(page.locator('#review')).not.toHaveAttribute('open', '');
  await page.locator('#req-ai').selectOption('');
  await page.locator('#req-status').selectOption(verb === 'Approve' ? 'approved' : 'rejected');
  await expect(page.locator('#req-body tr', { hasText: 'Gift for a customer' })).toBeVisible();
  await at(page, 'The decision is recorded');
  await page.locator('#req-body tr', { hasText: 'Gift for a customer' }).click();
  await expect(page.locator('#review-body')).toContainText('went against the AI advice');
  await callout(page, 'Decision recorded with the approver and the AI advice', '#review-body .banner', 0, 6);
  await at(page, 'Nothing is hidden');
  await page.locator('#review-close').click();
  await endScene(page);

  // ---- Scene 6: reports ---------------------------------------------------------------------------------
  begin(6);
  await at(page, 'Now, the reports');
  await page.locator('a[data-tab="reports"]').click();
  await expect(page.locator('#rep-name option')).toHaveCount(9);
  await page.locator('#rep-name').selectOption({ label: 'Overview' });
  await page.locator('#rep-form').getByRole('button', { name: 'Run' }).click();
  await expect(page.locator('#rep-out table')).toBeVisible();
  await at(page, 'So there are nine reports');
  await callout(page, 'Nine reports, tables and CSV', '#rep-name', 0, 6);
  await at(page, 'This one matters most');
  await page.locator('#rep-name').selectOption({ label: 'Decisions against AI advice' });
  await expect(page.locator('#rep-out table tbody tr').first()).toBeVisible();
  await callout(page, 'Override with justification', '#rep-out table tbody tr', 0, 6);
  await at(page, "Let's download this one", 0.8);
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#rep-csv').click()]);
  expect(download.suggestedFilename()).toMatch(/\.csv$/);
  await download.cancel();
  await page.evaluate(() => (window as any).__callout('CSV downloaded', null, 0, 3500));
  await endScene(page);

  // ---- Scene 7: the client API, shown as a terminal (real calls, real answers) ---------------------------
  begin(7);
  const TD = 'http://localhost:8000';
  const START = iso(75);
  const END = iso(79);
  let term = '<span class="dim"># second terminal: the HR portal talking to TrustDesk</span>\nexport TD=' + TD + '\nexport TOKEN=td_••••••••\n\n';
  await termPage(page, term);
  // Off camera: a client-application token, made in a second, unrecorded window that shares the session (the terminal page stays up).
  const side: BrowserContext = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  await side.addCookies(await context.cookies());
  const sp = await side.newPage();
  await sp.goto(stack.url + '/#settings');
  await sp.locator('#tk-name').fill('hr-portal');
  await sp.locator('#tk-scope').selectOption('client');
  await sp.getByRole('button', { name: 'Create token' }).click();
  const TOKEN = (await sp.locator('pre.code code').first().innerText()).trim();
  expect(TOKEN).toMatch(/^td_/);
  await side.close();

  await at(page, 'It starts by creating', 0.2);
  const c1 = `curl -s -X POST $TD/api/v1/employees -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"employee_no":"E2001","name":"Alex Example","email":"alex@example.com","department":"Sales","level":"L3"}' | jq .employee_no`;
  term = await typeLine(page, term, c1, 3.2);
  const r1 = await api('/api/v1/employees', { method: 'POST', token: TOKEN, body: JSON.stringify({ employee_no: 'E2001', name: 'Alex Example', email: 'alex@example.com', department: 'Sales', level: 'L3' }) });
  expect(r1.status).toBe(201);
  term += JSON.stringify((await r1.json()).employee_no) + '\n\n';
  await setTerm(term);
  await at(page, 'Then it sends a request', 0.2);
  const c2 = `REF=$(curl -s -X POST $TD/api/v1/requests -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Idempotency-Key: alex-demo-1" -d "{\\"employee_no\\":\\"E2001\\",\\"type\\":\\"vacation\\",\\"title\\":\\"Summer break\\",\\"description\\":\\"One week at the coast\\",\\"start_date\\":\\"$START\\",\\"end_date\\":\\"$END\\"}" | jq -r .ref); echo $REF`;
  term = await typeLine(page, term, c2, 5);
  const r2 = await api('/api/v1/requests', {
    method: 'POST',
    token: TOKEN,
    headers: { 'Idempotency-Key': 'alex-demo-1' },
    body: JSON.stringify({ employee_no: 'E2001', type: 'vacation', title: 'Summer break', description: 'One week at the coast', start_date: START, end_date: END }),
  });
  expect(r2.status).toBe(201);
  const REF = (await r2.json()).ref as string;
  term += REF + '\n\n';
  await setTerm(term);
  await at(page, 'Back in the app');
  await page.goto(stack.url + '/#requests');
  await page.locator('#req-status').selectOption('');
  await expect(page.locator('#req-body tr', { hasText: 'Summer break' }).first()).toBeVisible();
  await callout(page, 'Sent by an application, already in the queue', '#req-body tr', 0, 3.2);
  // ... and back to the terminal for the last call.
  await at(page, 'Now one last call', 0.3);
  await termPage(page, term);
  const c3 = `curl -s $TD/api/v1/requests/$REF -H "Authorization: Bearer $TOKEN" | jq '{ref,status,has_advice: has("ai")}'`;
  term = await typeLine(page, term, c3, 2.5);
  const r3 = await (await api(`/api/v1/requests/${REF}`, { token: TOKEN })).json();
  const out3 = JSON.stringify({ ref: r3.ref, status: r3.status, has_advice: 'ai' in r3 }, null, 2);
  expect(r3.ai).toBeUndefined();
  term += esc(out3) + '\n';
  await at(page, 'The application sees the status', 0.3);
  await setTerm(term);
  await at(page, 'But the advice field');
  await banner('Client application token: no advice, no decisions, no reports');
  await endScene(page);

  // ---- Scene 8: search and connect ------------------------------------------------------------------------
  begin(8);
  await page.goto(stack.url + '/#search');
  await page.locator('a[data-tab="search"]').click();
  await page.locator('#q').fill('How many vacation days do employees get each year?');
  await at(page, 'The documents can be searched', -1); // typed, then Enter lands as the narrator finishes the sentence
  await page.locator('#q').press('Enter');
  await expect(page.locator('#results')).not.toBeEmpty();
  await expect(page.locator('#results').locator('article, .result, li').first()).toBeVisible();
  await at(page, 'Every result shows');
  await callout(page, 'Results show document, heading, version', '#results > *', 0, 5);
  await at(page, 'Assistants such as');
  await page.locator('a[data-tab="connect"]').click();
  await expect(page.locator('#mcp-url')).not.toBeEmpty();
  await at(page, 'MCP is a standard');
  await callout(page, 'MCP: search_knowledge, get_policy_passage, list_documents (read-only)', '#mcp-url', 0, 5);
  await endScene(page);

  // ---- Scene 9: recap and end card --------------------------------------------------------------------------
  begin(9);
  await page.locator('a[data-tab="requests"]').click();
  await expect(page.locator('#req-counts')).toBeVisible();
  await at(page, 'To try it, start from', 0.6);
  await card(page, 'TrustDesk', ['README.md and docs/GUIDE.md', 'Postgres + pgvector + MCP']);
  await endScene(page);

  if (process.env.TIMES_JSON) {
    fs.mkdirSync(path.dirname(process.env.TIMES_JSON), { recursive: true });
    fs.writeFileSync(process.env.TIMES_JSON, JSON.stringify({ cardAt, scenes: times, sync }, null, 2));
  }
  if (notes.length) console.log('run notes:\n- ' + notes.join('\n- '));
});
