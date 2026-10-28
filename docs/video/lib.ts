/**
 * Local-only helpers for the recorder (borrowed from ecosystem/e2e/harness): a throwaway Postgres 17 cluster on 127.0.0.1
 * (no unix socket, a free port, a database whose name ends in `test`), the demo stand-in model, and TrustDesk itself.
 * Nothing here reads a real environment: the app gets an explicit environment and a private working directory.
 */
import { ChildProcess, execFileSync, spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as net from 'node:net';
import * as os from 'node:os';
import * as path from 'node:path';

export const REPO = path.resolve(__dirname, '..', '..');
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A free loopback port. TD_PORT_RANGE=41300-41399 restricts the choice (used when ports are reserved); default: any. */
export async function freePort(): Promise<number> {
  const m = /^(\d+)-(\d+)$/.exec(process.env.TD_PORT_RANGE ?? '');
  const tryPort = (port: number) =>
    new Promise<number | undefined>((resolve) => {
      const srv = net.createServer();
      srv.unref();
      srv.on('error', () => resolve(undefined));
      srv.listen(port, '127.0.0.1', () => {
        const got = (srv.address() as net.AddressInfo).port;
        srv.close(() => resolve(got));
      });
    });
  if (!m) return (await tryPort(0)) as number;
  const lo = Number(m[1]);
  const hi = Number(m[2]);
  for (let i = 0; i < 200; i++) {
    const port = lo + Math.floor(Math.random() * (hi - lo + 1));
    const got = await tryPort(port);
    if (got && !used.has(got)) {
      used.add(got);
      return got;
    }
  }
  throw new Error(`no free port in ${lo}-${hi}`);
}
const used = new Set<number>();

export async function waitFor<T>(what: string, fn: () => Promise<T | false | undefined> | T | false | undefined, ms: number): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v as T;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await sleep(250);
  }
}

/** Directory with initdb, postgres, psql (pgvector and pg_trgm installed). TD_PG_BIN overrides; else whatever is on PATH. */
function pgBin(): string {
  if (process.env.TD_PG_BIN) return process.env.TD_PG_BIN;
  try {
    return path.dirname(execFileSync('which', ['initdb'], { encoding: 'utf8' }).trim());
  } catch {
    throw new Error('Postgres binaries not found: put initdb on PATH or set TD_PG_BIN to their directory');
  }
}

export interface Cluster {
  url: string;
  stop(): void;
}

export async function startCluster(dbName: string): Promise<Cluster> {
  if (!/^[a-z0-9_]*test$/.test(dbName)) throw new Error('the throwaway database name must end in "test"');
  const bin = pgBin();
  const env = { PATH: `${bin}:/usr/bin:/bin`, LANG: 'C', LC_ALL: 'C' };
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'trustdesk-video-pg-'));
  const data = path.join(dir, 'data');
  const port = await freePort();
  execFileSync(path.join(bin, 'initdb'), ['-D', data, '-E', 'UTF8', '--locale=C', '-A', 'trust', '-U', 'postgres'], { env, stdio: 'pipe' });
  const log = fs.openSync(path.join(dir, 'postgres.log'), 'a');
  const proc: ChildProcess = spawn(
    path.join(bin, 'postgres'),
    ['-D', data, '-c', 'listen_addresses=127.0.0.1', '-c', `port=${port}`, '-c', 'unix_socket_directories=', '-c', 'fsync=off', '-c', 'max_connections=100'],
    { env, stdio: ['ignore', log, log] },
  );
  const psql = (db: string, sql: string) =>
    execFileSync(path.join(bin, 'psql'), ['-h', '127.0.0.1', '-p', String(port), '-U', 'postgres', '-d', db, '-v', 'ON_ERROR_STOP=1', '-c', sql], { env, stdio: 'pipe' });
  const stop = () => {
    try {
      proc.kill('SIGINT');
    } catch {}
    try {
      execFileSync(path.join(bin, 'pg_ctl'), ['-D', data, 'stop', '-m', 'immediate', '-s'], { env, stdio: 'pipe', timeout: 15_000 });
    } catch {}
    try {
      proc.kill('SIGKILL');
    } catch {}
    fs.rmSync(dir, { recursive: true, force: true });
  };
  try {
    await waitFor('postgres', () => {
      try {
        execFileSync(path.join(bin, 'pg_isready'), ['-h', '127.0.0.1', '-p', String(port)], { env, stdio: 'pipe' });
        return true;
      } catch {
        return false;
      }
    }, 30_000);
    psql('postgres', `CREATE DATABASE ${dbName} ENCODING 'UTF8' TEMPLATE template0`);
    for (const ext of ['vector', 'pg_trgm']) psql(dbName, `CREATE EXTENSION IF NOT EXISTS ${ext}`);
  } catch (e) {
    stop();
    throw e;
  }
  return { url: `postgresql://postgres@127.0.0.1:${port}/${dbName}`, stop };
}

export interface Running {
  url: string;
  logs(): string;
  stop(): Promise<void>;
}

const BASE_ENV = (home: string) => ({ PATH: '/usr/bin:/bin', HOME: home, TMPDIR: home, LANG: 'en_US.UTF-8', PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1', NO_COLOR: '1', HOST: '127.0.0.1' });

export function venvBin(name: string): string {
  const p = path.join(REPO, '.venv', 'bin', name);
  if (!fs.existsSync(p)) throw new Error(`${p} not found: run  uv venv && uv pip install -e ".[dev]"  in the TrustDesk repository first`);
  return p;
}

function launch(cmd: string, args: string[], env: Record<string, string>, cwd: string, url: string, ready: string): Promise<Running> {
  const p = spawn(cmd, args, { env, cwd, stdio: ['ignore', 'pipe', 'pipe'], detached: true });
  let buf = '';
  let exited: number | undefined;
  p.stdout!.on('data', (d) => (buf += d));
  p.stderr!.on('data', (d) => (buf += d));
  p.on('exit', (c) => (exited = c ?? -1));
  const running: Running = {
    url,
    logs: () => buf,
    async stop() {
      if (exited !== undefined) return;
      try {
        process.kill(-p.pid!, 'SIGTERM');
      } catch {}
      const end = Date.now() + 8000;
      while (exited === undefined && Date.now() < end) await sleep(100);
      if (exited === undefined) {
        try {
          process.kill(-p.pid!, 'SIGKILL');
        } catch {}
      }
    },
  };
  return waitFor(`${path.basename(cmd)} ${ready}`, async () => {
    if (exited !== undefined) throw new Error(`${cmd} exited early (${exited})\n${buf.slice(-2000)}`);
    try {
      return (await fetch(url + ready)).ok;
    } catch {
      return false;
    }
  }, 90_000).then(() => running, async (e) => {
    await running.stop();
    throw e;
  });
}

export interface Stack {
  url: string;
  setupCode: string;
  /** Everything the server printed (it holds the one-time setup code: show it only on screen, never keep it). */
  serverLog(): string;
  stop(): Promise<void>;
}

/** Database, the demo stand-in model and TrustDesk, all on loopback, in a private working directory. */
export async function startStack(): Promise<Stack> {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'trustdesk-video-'));
  const cluster = await startCluster('trustdesk_video_test');
  const llmPort = await freePort();
  const llm = await launch(venvBin('python'), [path.join(REPO, 'scripts', 'fake_llm.py'), '--port', String(llmPort)], BASE_ENV(work), work, `http://127.0.0.1:${llmPort}`, '/v1/models');
  const port = await freePort();
  const env = {
    ...BASE_ENV(work),
    DATABASE_URL: cluster.url,
    PORT: String(port),
    LLM_BASE_URL: `${llm.url}/v1`,
    LLM_API_KEY: 'demo-standin',
    LLM_CHAT_MODEL: 'demo-standin',
    EMBEDDING_MODEL: 'demo-standin',
    EMBEDDING_DIM: '768',
  };
  const bin = venvBin('trustdesk');
  execFileSync(bin, ['migrate'], { env, cwd: work, stdio: 'pipe' });
  let app: Running | undefined;
  try {
    app = await launch(bin, ['serve'], env, work, `http://127.0.0.1:${port}`, '/readyz');
    const code = await waitFor('the setup code', () => app!.logs().match(/^[ \t]{2,}([A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4})[ \t]*$/m)?.[1], 20_000);
    return {
      url: app.url,
      setupCode: code,
      serverLog: () => app!.logs(),
      async stop() {
        await app!.stop();
        await llm.stop();
        cluster.stop();
        fs.rmSync(work, { recursive: true, force: true });
      },
    };
  } catch (e) {
    await app?.stop();
    await llm.stop();
    cluster.stop();
    fs.rmSync(work, { recursive: true, force: true });
    throw e;
  }
}
