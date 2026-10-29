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
 * Not live from this laptop, and labelled as such: the architecture diagram (scene 1), the real screenshots captured on the live
 * AgentPG fleet (scene 4: REQ-000016 with a real model; scene 7: edb_stat_monitor, edb_pg_tuner, query_advisor, which do not exist
 * on a local Postgres; files in videos/live-captures/) and the terminal of scene 8 (real psql
 * output of the real read-only database user on the local database).
 */
import { expect, test, type Page } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { startStack, type Stack } from './lib';

const PASSWORD = 'acme-demo-password-1'; // throwaway; the database is deleted after the run
const HOLD = Number(process.env.HOLD ?? 1); // multiplies every scene length and cue time (rehearsal)
const DB = 'trustdesk_video_test';

let stack: Stack;

test.beforeAll(async () => {
  stack = await startStack();
});
test.afterAll(async () => {
  await stack?.stop();
});

/**
 * Scene pacing and sync. build.py runs narrate.py first and hands the result over (NARRATION_JSON: {"1": {duration, cues:[{text,
 * start, end}]}, ...}). A scene lasts LEAD + its narration + TAIL = narration + 0.7 s. Inside a scene, every on-screen action
 * waits for the sentence that illustrates it: `await at(page, 'words of the sentence')` blocks until that sentence starts being
 * spoken (minus `lead`, because the action itself takes a moment to show). If the picture is already late (a slow page), the
 * action runs at once and the delay is logged and written to TIMES_JSON ("sync"). HOLD scales all times.
 */
const LEAD = 0.2;
const TAIL = 0.5;
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
  await page.evaluate(() => (window as any).__clear?.()).catch(() => {}); // no callout runs over into the next scene
  times[times.length - 1].end = rel();
}

/** Overlay helpers, installed in every page of the context. They live in the top layer (popover) so they also show above dialogs. */
const OVERLAY = () => {
  const mk = (css: string, text: string) => {
    const el = document.createElement('div');
    el.setAttribute('popover', 'manual');
    el.setAttribute('data-recorder', '');
    el.style.cssText = 'position:fixed;inset:auto;margin:0;border:0;padding:0;background:none;overflow:visible;pointer-events:none;' + css;
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
      mk(`left:${r.left - 6}px;top:${r.top - 6}px;width:${r.width + 12}px;height:${r.height + 12}px;border:4px solid #ff9f1c;border-radius:10px;box-shadow:0 0 0 6px rgba(255,159,28,.25)`, '');
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
  (window as any).__lower = (title: string, sub: string, ms = 0) => {
    clear();
    clearTimeout((window as any).__ct);
    if (ms) (window as any).__ct = setTimeout(clear, ms);
    const el = mk('left:48px;bottom:48px;background:rgba(27,36,48,.94);color:#fff;font:700 34px/1.25 system-ui,sans-serif;padding:16px 26px;border-left:8px solid #ff9f1c;border-radius:6px;max-width:1500px', title);
    if (sub) {
      const d = document.createElement('div');
      d.textContent = sub;
      d.style.cssText = 'font:600 26px/1.3 system-ui,sans-serif;opacity:.9;margin-top:6px';
      el.append(d);
    }
  };
  // A live counter (the browser waits this long for the first frame of the stream, then polls).
  (window as any).__watchdog = (seconds: number) => {
    const el = mk('right:64px;top:120px;background:#1b2430;color:#fff;font:700 40px/1.3 system-ui,sans-serif;padding:16px 28px;border-radius:12px;border:4px solid #ff9f1c;text-align:center;min-width:560px', '');
    const t0 = Date.now();
    const tick = () => {
      const s = (Date.now() - t0) / 1000;
      el.innerHTML = s < seconds
        ? `On AgentPG: no first frame for ${s.toFixed(1)} s<div style="font:600 26px system-ui;opacity:.85">illustration: watchdog limit ${seconds} s</div>`
        : `<span style="color:#ffb347">On AgentPG: gives up, polls</span><div style="font:600 26px system-ui;opacity:.85">illustration: same list, refreshed every 2.5 s</div>`;
      if (el.isConnected) setTimeout(tick, 100);
    };
    tick();
  };
  (window as any).__clear = clear;
};

/** Draws a callout (it removes itself after `seconds`); does not wait, so the next `at` decides when the story goes on. */
const callout = (page: Page, text: string, selector: string | null, nth = 0, seconds = 3.5) =>
  page.evaluate(([t, s, n, ms]) => (window as any).__callout(t, s, n, ms), [text, selector, nth, seconds * 1000 * HOLD] as const);
const lower = (page: Page, title: string, sub = '', seconds = 0) => page.evaluate(([t, s, ms]) => (window as any).__lower(t, s, ms), [title, sub, seconds * 1000 * HOLD] as const);

const CARD_CSS = `html,body{margin:0;height:100%;background:#0f2a43;color:#fff;font-family:system-ui,sans-serif}
.c{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center}
h1{font-size:96px;margin:0}p{font-size:40px;margin:0;opacity:.92}.dim{opacity:.0;transition:opacity .6s}.on{opacity:.95}`;
const blank = (page: Page) => page.goto('about:blank'); // setContent keeps the old URL: leave the app first
const card = async (page: Page, title: string, lines: string[]) => {
  await blank(page);
  await page.setContent(`<style>${CARD_CSS}</style><div class="c"><h1>${title}</h1>${lines.map((l, i) => `<p id="l${i}">${l}</p>`).join('')}</div>`);
};

/** Architecture diagram: boxes are labelled platform / reference scaffolding / sample app, exactly as in the ecosystem docs. */
const DIAGRAM = `<style>
html,body{margin:0;height:100%;background:#f4f6f9;font-family:system-ui,sans-serif;color:#1b2430}
.w{padding:44px 90px;display:flex;flex-direction:column;gap:26px;height:100%;box-sizing:border-box;justify-content:center}
.box{border:4px solid;border-radius:18px;padding:22px 32px;transition:box-shadow .5s,opacity .5s,transform .5s;position:relative}
.tag{position:absolute;top:-20px;left:28px;font:800 24px system-ui;padding:3px 16px;border-radius:999px;color:#fff;letter-spacing:.04em;text-transform:uppercase}
h2{margin:6px 0 4px;font-size:44px}p{margin:0;font-size:30px;line-height:1.35}
.app{border-color:#e07b00;background:#fff4e5}.app .tag{background:#e07b00}
.scf{border-color:#6b7787;border-style:dashed;background:#fff}.scf .tag{background:#6b7787}
.plat{border-color:#0b5cad;background:#e8f1fb}.plat .tag{background:#0b5cad}
.row{display:flex;gap:22px}.chip{flex:1;border:3px dashed #6b7787;border-radius:12px;padding:14px 18px;font-size:30px;text-align:center;background:#fafbfc}
.ext{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}.ext span{background:#0b5cad;color:#fff;border-radius:10px;padding:6px 16px;font:600 28px ui-monospace,Menlo,monospace}
.arrow{text-align:center;font-size:36px;color:#6b7787;margin:-12px 0}
.dim{opacity:.35}.hl{box-shadow:0 0 0 10px rgba(255,159,28,.45);transform:scale(1.01)}
</style>
<div class="w">
 <div class="box app" id="app"><span class="tag">Sample app</span><h2>TrustDesk</h2><p>Requests, policies, AI pre-check with quotes you can verify. It exists to show the platform.</p></div>
 <div class="arrow">&#8595;</div>
 <div class="box scf" id="scf"><span class="tag">Reference scaffolding: swap any of it</span>
  <div class="row"><div class="chip">identity provider (OIDC)</div><div class="chip">LLM gateway</div><div class="chip">vault</div></div></div>
 <div class="arrow">&#8595;</div>
 <div class="box plat" id="plat"><span class="tag">Platform: EDB Postgres on AgentPG</span>
  <h2>One Postgres does the work</h2>
  <div class="ext"><span id="pgv">pgvector</span><span>full-text</span><span>unaccent</span><span>tcn</span><span>edb_stat_monitor</span><span>edb_pg_tuner</span><span>query_advisor</span></div>
  <p style="margin-top:12px" id="novec">No external vector database.</p></div>
</div>`;

const TERM_CSS = `html,body{margin:0;height:100%;background:#12161c;color:#d8dee9;font:31px/1.4 ui-monospace,Menlo,monospace}
pre{margin:0;padding:40px 60px;white-space:pre-wrap;word-break:break-all}.p{color:#7fd1ae}.hl{background:#ff9f1c;color:#111;padding:2px 8px;border-radius:4px}.dim{color:#7b8794}.err{color:#ff7b72;font-weight:700}.ok{color:#7fd1ae}
.note{position:fixed;left:60px;right:60px;bottom:40px;background:#1b2430;border-left:8px solid #ff9f1c;border-radius:8px;padding:16px 26px;font:600 28px/1.35 system-ui,sans-serif;color:#fff}`;
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


/** Real screenshots captured on the live AgentPG fleet (videos/live-captures/), shown with a slow zoom and a highlight box. */
const LIVE_DIR = path.resolve(__dirname, '..', '..', '..', 'videos', 'live-captures');
const IW = 1481, IH = 812; // the screenshots' size; boxes are read off them in pixels
const STILL_HTML = `<style>
html,body{margin:0;height:100%;background:#0b1220;overflow:hidden;font-family:system-ui,sans-serif}
#bar{position:fixed;left:0;right:0;top:0;height:80px;display:flex;align-items:center;justify-content:center;gap:24px;background:#0f1a2e;border-bottom:2px solid #1e293b;font:700 34px system-ui}
.tg{padding:6px 20px;border-radius:10px;color:#0b1220}.cap{background:#fb923c;box-shadow:0 0 0 4px rgba(251,146,60,.35)}.pl{background:#38bdf8}
#stage{position:absolute;left:48px;top:80px;width:1824px;height:1000px;overflow:hidden;background:#fff}
#w{position:absolute;inset:0;transform-origin:50% 50%}#w img{width:100%;height:100%;display:block}
#box{position:absolute;border:6px solid #f59e0b;border-radius:8px;box-shadow:0 0 0 9999px rgba(11,18,32,.28);opacity:0;transition:opacity .4s}
</style><div id="bar"><span class="tg cap">CAPTURED from the live AgentPG fleet, 28 Oct 2026</span><span class="tg pl" id="tag2"></span></div>
<div id="stage"><div id="w"><img id="img"><div id="box"></div></div></div>`;
async function stillPage(page: Page, tag: string, files: string[]) {
  await blank(page);
  await page.setContent(STILL_HTML);
  const srcs: Record<string, string> = {};
  for (const f of files) srcs[f] = 'data:image/jpeg;base64,' + fs.readFileSync(path.join(LIVE_DIR, f)).toString('base64');
  await page.evaluate(([t, m]) => { document.getElementById('tag2')!.textContent = t as string; (window as any).__srcs = m; }, [tag, srcs] as const);
}
const pct = (x0: number, y0: number, x1: number, y1: number) => [(x0 / IW) * 100, (y0 / IH) * 100, ((x1 - x0) / IW) * 100, ((y1 - y0) / IH) * 100];
/** a shot: which capture, zoom origin (% of the image), zoom from/to over ms, highlight box (pixels of the capture: x0, y0, x1, y1). */
const shot = (page: Page, img: string, ox: number, oy: number, z0: number, z1: number, ms: number, box?: number[]) =>
  page.evaluate(([i, a, b, c, d, e, f]) => {
    const im = document.getElementById('img') as HTMLImageElement, w = document.getElementById('w')!, bx = document.getElementById('box')!;
    const src = (window as any).__srcs[i as string];
    if (im.src !== src) im.src = src;
    w.style.transition = 'none'; w.style.transformOrigin = `${a}% ${b}%`; w.style.transform = `scale(${c})`;
    void w.offsetWidth;
    w.style.transition = `transform ${(e as number) * 1}ms linear`; w.style.transform = `scale(${d})`;
    if (f) { const g = f as number[]; bx.style.left = g[0] + '%'; bx.style.top = g[1] + '%'; bx.style.width = g[2] + '%'; bx.style.height = g[3] + '%'; bx.style.opacity = '1'; } else bx.style.opacity = '0';
  }, [img, ox, oy, z0, z1, ms * HOLD, box ? pct(box[0], box[1], box[2], box[3]) : null] as const);
const REJ = 'trustdesk-req-000016-rejection-2026-10-28.jpg', QUO = 'trustdesk-req-000016-verified-quotes-2026-10-28.jpg';
const TOPQ = 'trustdesk-insights-top-queries-masking-2026-10-28.jpg', TUNE = 'trustdesk-insights-tuner-advisor-2026-10-28.jpg';

const api = (path: string, init: RequestInit & { token?: string; cookie?: string } = {}) =>
  fetch(stack.url + path, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init.token ? { authorization: `Bearer ${init.token}` } : {}), ...(init.cookie ? { cookie: init.cookie } : {}), ...(init.headers as Record<string, string>) },
  });

const isoOf = (d: Date) => d.toISOString().slice(0, 10);
const iso = (days: number) => isoOf(new Date(Date.now() + days * 864e5));
/** The first Monday to Friday day on or after today + `days`. */
function weekday(days: number): Date {
  const d = new Date(Date.now() + days * 864e5);
  while ([0, 6].includes(d.getUTCDay())) d.setUTCDate(d.getUTCDate() + 1);
  return d;
}
/** The last day of a stretch of `n` working days that starts on `start` (a weekday). */
function endAfter(start: Date, n: number): Date {
  const d = new Date(start);
  for (let left = n - 1; left > 0; ) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (![0, 6].includes(d.getUTCDay())) left--;
  }
  return d;
}

test('TrustDesk, answers from policies you can trust', async ({ page, context, browser }) => {
  test.setTimeout(30 * 60_000);
  await context.addInitScript(OVERLAY);
  const notes: string[] = [];
  T0.at = Date.now();
  const type = async (selector: string, text: string, delay = 28) => {
    await page.locator(selector).click();
    await page.locator(selector).pressSequentially(text, { delay });
  };

  // ---- Off camera (the recording is trimmed to the first card): admin password, a client token, a Marketing employee ---------
  await page.goto(stack.url);
  await expect(page.locator('#auth-card')).toContainText('Set up TrustDesk');
  await page.getByLabel('Setup code').fill(stack.setupCode);
  await page.locator('#setup-pw').fill(PASSWORD);
  await page.locator('#setup-pw2').fill(PASSWORD);
  await page.getByRole('button', { name: 'Create admin password' }).click();
  await expect(page.locator('#nav')).toBeVisible();
  await page.goto(stack.url + '/#settings');
  await page.locator('#tk-name').fill('hr-portal');
  await page.locator('#tk-scope').selectOption('client');
  await page.getByRole('button', { name: 'Create token' }).click();
  await expect(page.locator('pre.code code').first()).toBeVisible({ timeout: 20_000 });
  const TOKEN = (await page.locator('pre.code code').first().innerText()).trim();
  expect(TOKEN).toMatch(/^td_/);
  const emp = await api('/api/v1/employees', { method: 'POST', token: TOKEN, body: JSON.stringify({ employee_no: 'E2001', name: 'José Álvarez', email: 'jose.alvarez@example.com', department: 'Marketing', title: 'Brand Manager', level: 'L3', country: 'PT', hire_date: '2021-03-01' }) });
  expect(emp.status).toBe(201);

  T0.at = Date.now(); // the recorder's clock starts at the first card; build.py cuts the video there (the setup above is not in the cut)
  // ---- Scene 1: the problem, then what is what ------------------------------------------------------------------------
  begin(1);
  const cardAt = rel();
  await card(page, 'A stack of requests', ['and a 200-page policy handbook']);
  await page.evaluate(() => {
    const p = document.createElement('p');
    p.id = 'l2';
    p.className = 'dim';
    p.textContent = 'I need answers I can trust.';
    p.style.cssText = 'font-size:56px;font-weight:700;color:#ffb347;margin-top:30px';
    document.querySelector('.c')!.append(p);
  });
  await at(page, 'I need answers', 0.2);
  await page.evaluate(() => document.getElementById('l2')!.classList.add('on'));
  await at(page, 'This is TrustDesk', 0.3);
  await blank(page);
  await page.setContent(DIAGRAM);
  await page.evaluate(() => ['scf', 'plat'].forEach((i) => document.getElementById(i)!.classList.add('dim')));
  await page.evaluate(() => document.getElementById('app')!.classList.add('hl'));
  await at(page, 'The identity provider', 0.2);
  await page.evaluate(() => {
    document.getElementById('app')!.classList.remove('hl');
    document.getElementById('scf')!.classList.remove('dim');
    document.getElementById('scf')!.classList.add('hl');
  });
  await at(page, 'What you are watching', 0.2);
  await page.evaluate(() => {
    document.getElementById('scf')!.classList.remove('hl');
    document.getElementById('plat')!.classList.remove('dim');
    document.getElementById('plat')!.classList.add('hl');
  });
  await at(page, 'The vectors live', 0.2);
  await page.evaluate(() => {
    const e = document.getElementById('pgv')!;
    e.style.cssText = 'background:#ff9f1c;color:#111;outline:6px solid rgba(255,159,28,.4)';
    document.getElementById('novec')!.style.cssText = 'margin-top:12px;font-weight:800;color:#0b5cad;font-size:36px';
  });
  await endScene(page);

  // ---- Scene 2: load the rules (short) ----------------------------------------------------------------------------------
  begin(2);
  await page.goto(stack.url + '/#requests');
  await expect(page.locator('#req-onboarding')).toContainText('Nothing here yet');
  await at(page, 'One click adds', -2.5);
  await page.getByRole('button', { name: 'Load demo policies, people and requests' }).click();
  await expect(page.locator('#req-onboarding')).toBeHidden({ timeout: 120_000 });
  await expect
    .poll(async () => (await page.evaluate(() => fetch('/api/v1/requests/counts').then((r) => r.json()))).by_status.submitted, { timeout: 180_000, intervals: [500] })
    .toBe(0);
  const dots = () => page.locator('#req-body tr td:nth-child(5)', { hasText: '…' }).count();
  await expect.poll(dots, { timeout: 15_000, intervals: [250] }).toBe(0);
  await callout(page, 'Each request already has a first opinion', '#req-table thead th:nth-child(5)', 0, 3.5);
  await at(page, 'Behind the gateway', 0.2);
  for (let i = 0; i < 6; i++) {
    await page.evaluate(() => window.scrollBy({ top: 330, behavior: 'smooth' }));
    await pause(page, 1.05);
  }
  await at(page, 'Each policy is split', 0.4);
  await page.locator('a[data-tab="documents"]').click();
  await expect(page.locator('#doc-body tr')).toHaveCount(16);
  await callout(page, '16 documents, in Postgres', '#doc-body tr', 0, 3);
  await at(page, 'Open one', 0.3);
  await page.locator('#doc-body tr', { hasText: 'Hr Vacation Policy' }).getByRole('button', { name: 'View' }).click();
  await expect(page.locator('#doc-detail')).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, Math.max(0, document.getElementById('doc-detail')!.getBoundingClientRect().top + window.scrollY - 90)));
  await at(page, 'That is the whole ingest', 0.3);
  await callout(page, 'Passage + vector + text index: one database', '#doc-detail', 0, 4);
  await endScene(page);

  // ---- Scene 3: a compliant request: submit, pre-check, verified quotes ---------------------------------------------------------
  begin(3);
  const submit = async (title: string, description: string, from: string, to: string) => {
    await page.locator('#req-new').click();
    await expect(page.locator('#newreq')).toHaveAttribute('open', '');
    await page.locator('#nr-emp').selectOption({ label: 'José Álvarez (E2001)' });
    await page.locator('#nr-type').selectOption('vacation');
    const body = page.locator('#newreq-body');
    await body.getByLabel('Title').pressSequentially(title, { delay: 10 }); // pressSequentially focuses the field itself: no extra click (each action costs the 400 ms slowMo)
    await body.getByLabel('Description').pressSequentially(description, { delay: 5 });
    await body.getByLabel('From').fill(from);
    await body.getByLabel('To').fill(to);
  };
  await at(page, 'Now the question', 0.2);
  await page.locator('a[data-tab="requests"]').click();
  await expect(page.locator('#req-new')).toBeVisible();
  await at(page, 'José wants a long weekend', 0.3);
  const s3 = weekday(56);
  await submit('Mountain long weekend', 'A long weekend in the mountains with family.', isoOf(s3), isoOf(endAfter(s3, 3)));
  await at(page, 'I submit it', 0.2);
  await page.locator('#newreq').getByRole('button', { name: 'Submit request' }).click();
  await expect(page.locator('#review')).toHaveAttribute('open', '');
  await expect(page.locator('#review-body')).toContainText('AI pre-validation', { timeout: 60_000 });
  await expect(page.locator('#review-body')).not.toContainText('Checking the request', { timeout: 60_000 });
  await at(page, 'Suggests approval', 0.3);
  await callout(page, 'AI pre-check: suggests approval', '#review-body .badges', 0, 3.5);
  await at(page, 'Each reason is numbered', 0.2);
  await callout(page, 'Numbered reasons, each with a policy passage', '#review-body ol.reasons', 0, 6);
  await at(page, 'Under each one', 0.2);
  await callout(page, 'Quote found word for word in the document', '#review-body .ok-text', 0, 4.5);
  await at(page, 'Open the full passage', 0.3);
  await page.locator('#review-body details summary').first().click();
  await callout(page, 'The full passage, from the database row', '#review-body details[open] p.body', 0, 5);
  await endScene(page);

  // ---- Scene 4: a non-compliant request, the check against the source row, the person decides ----------------------------------------------
  begin(4);
  await at(page, 'Now a request that breaks', 0.2);
  await page.locator('#review-close').click();
  const s4 = weekday(2);
  await submit('Three weeks at the coast', 'Three weeks at the coast, leaving soon.', isoOf(s4), isoOf(endAfter(s4, 15)));
  await at(page, 'Suggests rejection', 4.8); // the model takes ~1 s and the form typing a while: click earlier so the verdict is up when the sentence starts (was 1.25 s late at 3.4)
  await page.locator('#newreq').getByRole('button', { name: 'Submit request' }).click();
  await expect(page.locator('#review')).toHaveAttribute('open', '');
  await expect(page.locator('#review-body')).toContainText('AI pre-validation', { timeout: 60_000 });
  await expect(page.locator('#review-body')).not.toContainText('Checking the request', { timeout: 60_000 });
  const verdict = await page.locator('#review-body .badges').first().innerText();
  notes.push(`scene 4 advice badges: ${verdict.replace(/\s+/g, ' ')}`);
  await at(page, 'Suggests rejection', 0.0);
  await callout(page, 'AI pre-check: suggests rejection', '#review-body .badges', 0, 3.5);
  await at(page, 'Thirty days of notice', 0.2);
  await callout(page, 'Reason 1: notice, 2 days given, 30 required', '#review-body ol.reasons li', 0, 5.5);
  await at(page, 'Fifteen days need', 0.2);
  await callout(page, 'Reason 2: approval level', '#review-body ol.reasons li', 1, 3.5);
  await at(page, 'And one more', 0.2);
  await callout(page, 'Reason 3: what does NOT apply', '#review-body ol.reasons li', 2, 5);
  await at(page, 'Look at the quotes', 0.2);
  await callout(page, 'Foundation: three quotes from the policy', '#review-body .cite-card', 0, 3);
  await at(page, 'Each one carries', 0.2);
  await callout(page, 'Quote found word for word in the document', '#review-body .ok-text', 0, 4.5);
  await at(page, 'That check is not done by the model', 0.4);
  await page.locator('#review-body .cite-card details summary').first().click();
  await callout(page, 'Checked against the source row, in the database', '#review-body .cite-card details[open]', 0, 7);
  await at(page, 'So the model cannot invent', 0.2);
  await callout(page, 'No match: amber warning, never green', '#review-body .cite-card', 1, 4);
  await at(page, 'The AI advises', 0.2);
  await callout(page, 'The AI advises. A person decides.', '#decision-note', 0, 3);
  await page.locator('#decision-note').click();
  await page.locator('#decision-note').pressSequentially('Notice is 2 days, 30 are required. Please move the dates.', { delay: 28 });
  await at(page, 'I write my reason', -0.5);
  await page.locator('#review').getByRole('button', { name: 'Reject', exact: true }).click();
  await expect(page.locator('#review')).not.toHaveAttribute('open', '');
  await at(page, 'The same check ran on the live', 0.3);
  await stillPage(page, 'with a real model behind the gateway', [REJ, QUO]);
  await shot(page, REJ, 50, 20, 1.0, 1.06, 6000);
  await at(page, 'This is a real rejection', 0.2);
  await shot(page, REJ, 60, 25, 1.0, 1.2, 5000, [703, 160, 1162, 315]);
  await at(page, 'Two days of notice', 0.2);
  await shot(page, REJ, 70, 45, 1.2, 1.3, 3500, [718, 360, 1162, 460]);
  await at(page, 'The live policy there', 0.2);
  await shot(page, REJ, 70, 60, 1.2, 1.3, 4500, [718, 463, 1162, 545]);
  await at(page, 'And the finance blackout', 0.2);
  await shot(page, REJ, 70, 75, 1.2, 1.3, 4500, [718, 548, 1162, 667]);
  await at(page, 'Same green line', 0.2);
  await shot(page, QUO, 65, 35, 1.0, 1.3, 6000, [703, 125, 1162, 417]);
  await endScene(page);

  // ---- Scene 5: search: hybrid ranks, a real typo (pg_trgm), a typo in a person's name ------------------------------------------------------
  begin(5);
  await page.goto(stack.url + '/#requests'); // back from the captured screenshots of scene 4
  await at(page, 'Now, finding things', 0.2);
  await page.locator('a[data-tab="search"]').click();
  await page.locator('#q').fill('');
  await type('#q', 'How much notice do I need for a long vacation?', 22);
  await at(page, 'Search the policies', -1.2);
  await page.locator('#q').press('Enter');
  await expect(page.locator('#results article').first()).toBeVisible();
  await at(page, 'Each result shows', 0.3);
  await callout(page, 'Vector rank + exact-word rank, same database', '#results article .badges', 0, 4.5);
  await at(page, 'Now I misspell', 0.2);
  await page.locator('#q').fill('');
  await type('#q', 'encyption', 90);
  await at(page, 'Encyption.', -0.3);
  await page.locator('#q').press('Enter');
  await expect(page.locator('#results article', { hasText: 'Encryption Policy' }).first()).toBeVisible();
  await expect(page.locator('#results .badge', { hasText: 'typo-tolerant' }).first()).toBeVisible();
  await at(page, 'No exact word', 0.2);
  await callout(page, 'Encryption Policy found despite the typo', '#results article', 0, 4);
  await at(page, 'But look at the badge', 0.2);
  await callout(page, 'typo-tolerant: pg_trgm, same Postgres', '#results article .badge.warn', 0, 6);
  await at(page, 'A real embedding model', 0.2);
  await lower(page, 'Real embedding model: better ranking', 'The demo embedder only matches words: the floor, not the ceiling', 6);
  await at(page, 'Names work too', 0.2);
  await page.locator('a[data-tab="requests"]').click();
  await page.locator('#req-status').selectOption('');
  await expect(page.locator('#req-body tr').first()).toBeVisible();
  await at(page, 'Type alvares', 0.3);
  await type('#req-q', 'alvares', 90);
  await expect(page.locator('#req-body tr', { hasText: 'José Álvarez' }).first()).toBeVisible();
  await callout(page, '"alvares" finds "José Álvarez"', '#req-body tr', 0, 4.5);
  await endScene(page);

  // ---- Scene 6: live status (tcn), and the honest word about the platform ingress ------------------------------------------------------------
  begin(6);
  await at(page, 'Status is live', 0.2);
  await page.locator('#req-q').fill('');
  await page.locator('#req-status').selectOption('');
  await expect(page.locator('#req-body tr').first()).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await at(page, 'An application', 0.0);
  await lower(page, 'HR portal: client API with a client token', 'It cannot see the advice and cannot decide', 4);
  await at(page, 'It appears in my list', 1.2);
  const sent = await api('/api/v1/requests', {
    method: 'POST',
    token: TOKEN,
    headers: { 'Idempotency-Key': 'portal-demo-1' },
    body: JSON.stringify({ employee_no: 'E2001', type: 'vacation', title: 'Summer break', description: 'One week at the coast', start_date: isoOf(weekday(90)), end_date: isoOf(endAfter(weekday(90), 5)) }),
  });
  expect(sent.status).toBe(201);
  const REF = (await sent.json()).ref as string;
  await expect(page.locator('#req-body tr', { hasText: 'Summer break' }).first()).toBeVisible({ timeout: 20_000 });
  await callout(page, 'A new row, and nobody pressed refresh', '#req-body tr', 0, 4);
  await at(page, 'Then someone else decides', 0.2);
  // a second, unrecorded browser window of the approver (same session cookie)
  const sideCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await sideCtx.addCookies(await context.cookies());
  const side = await sideCtx.newPage();
  await side.goto(stack.url + '/#requests');
  const dec = await side.evaluate(
    async (ref) => (await fetch(`/api/v1/requests/${ref}/decision`, { method: 'POST', headers: { 'content-type': 'application/json', 'x-trustdesk-csrf': '1' }, body: JSON.stringify({ action: 'approve', note: null }) })).status,
    REF,
  );
  expect(dec).toBe(200);
  await sideCtx.close();
  await expect(page.locator('#req-body tr', { hasText: 'Summer break' }).first()).toContainText('Approved', { timeout: 20_000 });
  await callout(page, 'Status changed by someone else, live', '#req-body tr', 0, 4);
  await at(page, 'This is the tcn', 0.2);
  await lower(page, 'tcn: Postgres sends NOTIFY request_changes on every row change', '', 5);
  await at(page, 'One honest note', 0.2);
  await lower(page, 'On AgentPG today: polling fallback', 'The platform ingress does not flush server-sent events', 0);
  await at(page, 'So the browser falls back', 0.2);
  await page.evaluate(() => (window as any).__watchdog(6));
  await endScene(page);

  // ---- Scene 7: the Database tab, and the captured card ---------------------------------------------------------------------------------------
  begin(7);
  await at(page, 'What is EDB doing', 0.2);
  await page.locator('a[data-tab="insights"]').click();
  await expect(page.locator('#view-insights')).toContainText('Not available on this database', { timeout: 20_000 });
  await at(page, 'On this laptop Postgres', 0.2);
  await callout(page, 'Local Postgres: these extensions do not exist here', '#view-insights .insights-na', 0, 5);
  await at(page, 'So these are real', 0.2);
  await stillPage(page, 'PLATFORM: EDB extensions', [TOPQ, TUNE]);
  await shot(page, TOPQ, 50, 20, 1.0, 1.08, 8000);
  await at(page, 'First, the statement monitor', 0.2);
  await shot(page, TOPQ, 40, 38, 1.0, 1.35, 7000, [291, 320, 1178, 555]);
  await at(page, 'Further down', 0.3);
  await shot(page, TOPQ, 35, 85, 1.0, 1.4, 4000, [291, 555, 1178, 692]);
  await at(page, "The platform's own", 0.2);
  await shot(page, TOPQ, 35, 90, 1.3, 1.5, 6000, [291, 555, 1178, 812]);
  await at(page, 'Then the tuner', 0.2);
  await shot(page, TUNE, 40, 35, 1.0, 1.4, 5500, [291, 239, 1178, 360]);
  await at(page, 'And the query advisor', 0.2);
  await shot(page, TUNE, 45, 80, 1.0, 1.35, 5000, [291, 520, 1178, 715]);
  await at(page, 'It hides the ones', 0.2);
  await shot(page, TUNE, 25, 75, 1.2, 1.35, 5000, [283, 464, 560, 488]);
  await endScene(page);

  // ---- Scene 8: masking: the read-only user, masked view vs base table (real psql, local database) --------------------------------------
  begin(8);
  const q1 = 'SELECT employee_no, name, email FROM employee_masked LIMIT 4;';
  const q2 = 'SELECT employee_no, name, email FROM employee LIMIT 4;';
  const out1 = stack.cluster.psql(DB, q1, 'ai_reader').trimEnd();
  const out2 = stack.cluster.psql(DB, q2, 'ai_reader').trimEnd();
  notes.push(`scene 8 masked view output:\n${out1}\n  base table output:\n${out2}`);
  expect(out1).toContain('***');
  expect(out2).toMatch(/permission denied for table employee/);
  let term = '<span class="dim"># a read-only database user, member of a1p_readonly (local Postgres)</span>\n<span class="p">$ </span>psql -U ai_reader trustdesk\n\n';
  await termPage(page, term);
  await at(page, 'Here is the read-only', 0.2);
  term = await typeLine(page, term, `trustdesk=> ${q1}`, 3.0);
  await at(page, 'Ask for the employees view', 0.2);
  term += esc(out1) + '\n\n';
  await page.evaluate((t) => (document.getElementById('t')!.innerHTML = t), term);
  await at(page, 'Ask for the real table', 0.2);
  term = await typeLine(page, term, `trustdesk=> ${q2}`, 2.2);
  term += esc(out2).replace(/(ERROR:.*)/, '<span class="err" id="errl">$1</span>') + '\n';
  await page.evaluate((t) => (document.getElementById('t')!.innerHTML = t), term);
  await callout(page, 'Denied by the database', '#errl', 0, 3);
  await at(page, 'The masking lives', 0.2);
  await page.evaluate(() => {
    const d = document.createElement('div');
    d.className = 'note';
    d.textContent = 'Enforced by the database: SELECT revoked on employee, a masked view granted instead.';
    document.body.append(d);
  });
  await at(page, 'One finding', 0.2);
  const note = (html: string) => page.evaluate((h) => (document.querySelector('.note')!.innerHTML = h), html);
  const HEAD = 'Captured on the live AgentPG fleet, 2026-10-28 (kit 0.3.1):';
  await note(`${HEAD}<br>&nbsp;`);
  await at(page, 'A new database user', 0.2);
  await note(`${HEAD}<br>16:52:39  a new read-only user is created: <code>employee</code> is readable again`);
  await at(page, 'So the app closes', 0.2);
  await note(`${HEAD}<br>16:52:39  a new read-only user is created: <code>employee</code> is readable again<br>16:55:55  the app has re-asserted the masking: closed again, no restart`);
  await endScene(page);

  // ---- Scene 9: the landing moment ---------------------------------------------------------------------------------------------------------------------
  begin(9);
  await page.goto(stack.url + '/#requests');
  await page.locator('#req-status').selectOption('rejected');
  await expect(page.locator('#req-body tr', { hasText: 'Three weeks at the coast' })).toBeVisible();
  await page.locator('#req-body tr', { hasText: 'Three weeks at the coast' }).click();
  await expect(page.locator('#review-body')).toContainText('AI pre-validation');
  await callout(page, 'Quote found word for word in the document', '#review-body .ok-text', 0, 3.5);
  await at(page, 'I saw each reason', 0.2);
  await callout(page, 'Every quote checked against the database', '#review-body .ok-text', 1, 4);
  await at(page, 'I saw search forgive', 0.6);
  await page.locator('#review-close').click();
  await page.locator('a[data-tab="search"]').click();
  await page.locator('#q').fill('vacation notice'); // one action: scene 9 only flashes the search page
  await page.locator('#q').press('Enter');
  await at(page, 'and status update live', 0.2);
  await page.locator('a[data-tab="requests"]').click();
  await expect(page.locator('#req-counts')).toBeVisible();
  await at(page, 'And an AI user', 0.2);
  await termPage(page, `<span class="dim"># the read-only user, as in scene 8</span>\n<span class="p">$ </span>trustdesk=&gt; ${esc(q1)}\n${esc(out1)}\n\n<span class="p">$ </span>trustdesk=&gt; ${esc(q2)}\n<span class="err">${esc(out2)}</span>\n`);
  await at(page, 'Native vectors', 0.3);
  await blank(page);
  await page.setContent(`<style>${CARD_CSS}.b{font-size:84px;font-weight:800}.s{font-size:48px;color:#ffb347}</style><div class="c">
    <p class="s dim" id="k">Only EDB, in one database</p>
    <p class="b dim" id="b1">Native vectors</p><p class="b dim" id="b2">Hybrid search</p><p class="b dim" id="b3">Verified citations</p></div>`);
  const show = (id: string) => page.evaluate((i) => document.getElementById(i)!.classList.add('on'), id);
  await show('b1');
  await pause(page, 1.6);
  await show('b2');
  await pause(page, 1.6);
  await show('b3');
  await at(page, 'That is the part', 0.2);
  await show('k');
  await at(page, 'Look at the level', 0.3);
  await blank(page);
  await page.setContent(`<style>${CARD_CSS}.b{font-size:70px;font-weight:800}.s{font-size:48px;color:#ffb347}.x{text-decoration:line-through;opacity:.0}.x.on{opacity:.6}</style><div class="c">
    <p class="s">Look at the level of work you should be doing</p>
    <p class="b dim" id="b1">Check the quotes.</p><p class="b dim" id="b2">Decide.</p><p class="b x" id="b3">Hunt through 200 pages.</p></div>`);
  await at(page, 'Checking quotes', 0.2);
  await show('b1');
  await pause(page, 1.0);
  await show('b2');
  await at(page, 'Not hunting', 0.2);
  await show('b3');
  await at(page, 'Everything here', 0.2);
  await blank(page);
  await page.setContent(`<style>${CARD_CSS}.t{font-size:44px;margin:0}.k{display:inline-block;border:4px solid;border-radius:14px;padding:12px 30px;margin:8px}.a{border-color:#e07b00;color:#ffd9a0}.f{border-color:#9aa6b4;border-style:dashed;color:#d6dde6}.p{border-color:#5aa9ff;color:#bfe0ff}</style><div class="c">
    <h1>TrustDesk</h1>
    <p class="dim" id="b1"><span class="k a">Sample app</span> <span class="k f">Reference scaffolding: swap it</span> <span class="k p">Platform: EDB Postgres on AgentPG</span></p>
    <p class="dim" id="b2" style="font-size:44px">README.md and docs/GUIDE.md</p></div>`);
  await show('b1');
  await at(page, 'Start from the README', 0.2);
  await show('b2');
  await endScene(page);

  if (process.env.TIMES_JSON) {
    fs.mkdirSync(path.dirname(process.env.TIMES_JSON), { recursive: true });
    fs.writeFileSync(process.env.TIMES_JSON, JSON.stringify({ cardAt, scenes: times, sync }, null, 2));
  }
  if (notes.length) console.log('run notes:\n- ' + notes.join('\n- '));
});
