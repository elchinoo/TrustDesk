import { defineConfig } from '@playwright/test';

/**
 * Real Chrome (the installed Google Chrome, no browser download) against a LOCAL TrustDesk on 127.0.0.1.
 * The video is written to out/ (git-ignored). Slow human pacing: every action waits 400 ms (slowMo) on top of the
 * explicit holds in record.spec.ts. Set HEADED=1 to watch it.
 */
export default defineConfig({
  testDir: '.',
  testMatch: 'record.spec.ts',
  outputDir: './out',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 20 * 60_000,
  reporter: [['list']],
  use: {
    channel: 'chrome',
    headless: !process.env.HEADED,
    viewport: { width: 1920, height: 1080 },
    screenshot: 'only-on-failure',
    video: { mode: 'on', size: { width: 1920, height: 1080 } },
    bypassCSP: true, // only so the recorder can draw its callout overlays; the app itself is unchanged
    launchOptions: { slowMo: Number(process.env.SLOWMO ?? 400) },
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
});
