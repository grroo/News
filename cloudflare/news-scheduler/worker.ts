import { DurableObject } from 'cloudflare:workers';
import schedule from '../../schedule.json';
import { tick } from './core.mjs';
import { handleRequest } from './http.mjs';
import { advanceScheduled, applyClaim, emptyControl, noteDispatchResult, publicProjection, requestRefresh } from './reservations.mjs';

type SchedulerStatus = Record<string, string | number | null>;
type RefreshResult = {
  ok: boolean;
  http: number;
  body: Record<string, unknown>;
  dispatch: {ref: string; inputs: Record<string, unknown>} | null;
};
type ClaimResult = {decision: string; reservation_id: string; request_id: string; reason?: string};
type GateEnv = Env & {
  REFRESH_ENABLED?: string;
  RESERVATION_GATE_ENABLED?: string;
  STRICT_SLOT_GATE?: string;
  ACCESS_TEAM_DOMAIN?: string;
  ACCESS_AUD?: string;
  OWNER_EMAIL?: string;
  PAGES_SITE_URL?: string;
  NEWS_RESERVATION_GATE_TOKEN?: string;
};

function enabled(value: string) {
  return value === 'true';
}

export class NewsScheduler extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS scheduler (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, lease_until INTEGER NOT NULL)');
    this.ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)');
  }

  private readControl() {
    const row = this.ctx.storage.sql.exec<{payload: string}>('SELECT payload FROM control WHERE id=1').toArray()[0];
    return row ? JSON.parse(row.payload) : emptyControl();
  }

  private writeControl(state: object) {
    this.ctx.storage.sql.exec(
      'INSERT INTO control (id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
      JSON.stringify(state),
    );
  }

  private async withLease<T>(fn: () => Promise<T>): Promise<T | null> {
    const now = Date.now();
    const rows = this.ctx.storage.sql.exec<{payload: string}>(
      "INSERT INTO scheduler VALUES (1, '{}', ?) ON CONFLICT(id) DO UPDATE SET lease_until=excluded.lease_until WHERE scheduler.lease_until < ? RETURNING payload",
      now + 60000, now).toArray();
    if (!rows.length) return null;
    try {
      return await fn();
    } finally {
      this.ctx.storage.sql.exec('UPDATE scheduler SET lease_until=0 WHERE id=1');
    }
  }

  async check(): Promise<SchedulerStatus> {
    const now = Date.now();
    const rows = this.ctx.storage.sql.exec<{payload: string}>(
      "INSERT INTO scheduler VALUES (1, '{}', ?) ON CONFLICT(id) DO UPDATE SET lease_until=excluded.lease_until WHERE scheduler.lease_until < ? RETURNING payload", now + 60000, now).toArray();
    if (!rows.length) return {phase: 'check_in_progress'};
    const save = async (state: object) => {
      this.ctx.storage.sql.exec('UPDATE scheduler SET payload=? WHERE id=1', JSON.stringify(state));
    };
    try {
      const result = await tick({now, schedule, state: JSON.parse(rows[0].payload), env: this.env, save});
      await save(result);
      console.log(JSON.stringify({event: 'briefing_check', phase: result.phase, slot: result.slot}));
      return result;
    } catch (error) {
      const current = this.status();
      const message = error instanceof Error ? error.message : 'Scheduler check failed';
      await save({...current, phase: 'check_failed', checkedAt: new Date(now).toISOString(), error: message});
      console.error(JSON.stringify({event: 'briefing_check_failed', message}));
      throw error;
    } finally {
      this.ctx.storage.sql.exec('UPDATE scheduler SET lease_until=0 WHERE id=1');
    }
  }

  async checkReserved(): Promise<SchedulerStatus> {
    const outcome = await this.withLease(async () => {
      const env = this.env as GateEnv;
      const now = Date.now();
      let state = this.readControl();
      const live = await readJson(fetch(`${env.BRIEFING_URL}?scheduler=${now}`, {cache: 'no-store', redirect: 'manual', signal: AbortSignal.timeout(10000)}));
      let runs: unknown[] = [];
      let runsListed = false;
      if (env.GITHUB_TOKEN) {
        try {
          const body = await readJson(github(env, `/runs?branch=main&event=workflow_dispatch&per_page=20`));
          runs = Array.isArray(body.workflow_runs) ? body.workflow_runs : [];
          runsListed = Array.isArray(body.workflow_runs);
        } catch (error) {
          console.error(JSON.stringify({event: 'run_list_failed', message: error instanceof Error ? error.message : 'run list failed'}));
        }
      }
      const result = advanceScheduled(state, {
        now, schedule, runs, runsListed, live,
        strict: enabled(env.STRICT_SLOT_GATE),
        tokenPresent: !!env.GITHUB_TOKEN,
      });
      state = result.state;
      this.writeControl(state);
      this.publish(state);
      if (result.dispatch) {
        try {
          await dispatchWorkflow(env, result.dispatch);
          state = noteDispatchResult(state, 'accepted');
        } catch (error) {
          state = noteDispatchResult(state, 'timeout');
          console.error(JSON.stringify({event: 'dispatch_ambiguous', message: error instanceof Error ? error.message : 'dispatch failed'}));
        }
        this.writeControl(state);
        this.publish(state);
      }
      console.log(JSON.stringify({event: 'briefing_check', phase: state.phase, slot: state.slot}));
      return publicProjection(state);
    });
    return outcome || {phase: 'check_in_progress'};
  }

  status(): SchedulerStatus {
    const row = this.ctx.storage.sql.exec<{payload: string}>('SELECT payload FROM scheduler WHERE id=1').toArray()[0];
    return row ? JSON.parse(row.payload) : {phase: 'not_checked'};
  }

  private publish(state: object) {
    const projected = publicProjection(state as ReturnType<typeof emptyControl>);
    this.ctx.storage.sql.exec('UPDATE scheduler SET payload=? WHERE id=1', JSON.stringify(projected));
  }

  async refresh(idempotencyKey: string): Promise<RefreshResult> {
    const saved = await this.withLease(async () => {
      const now = Date.now();
      const result = requestRefresh(this.readControl(), {now, schedule, idempotencyKey, trigger: 'manual'});
      this.writeControl(result.state);
      this.publish(result.state);
      return {ok: result.ok, http: result.http, body: result.body, dispatch: result.dispatch} as RefreshResult;
    });
    if (!saved) return {ok: false, http: 429, body: {error: 'concurrent_job', retry_after: new Date(Date.now() + 60000).toISOString()}, dispatch: null};
    return saved;
  }

  async noteDispatch(outcome: 'accepted' | 'timeout'): Promise<void> {
    await this.withLease(async () => {
      const state = noteDispatchResult(this.readControl(), outcome);
      this.writeControl(state);
      this.publish(state);
    });
  }

  async claim(claim: object, nowIso: string): Promise<ClaimResult> {
    const saved = await this.withLease(async () => {
      const result = applyClaim(this.readControl(), claim, nowIso);
      this.writeControl(result.state);
      return {decision: result.decision, reservation_id: result.reservation_id, request_id: result.request_id, reason: result.reason} as ClaimResult;
    });
    if (!saved) return {decision: 'denied', reservation_id: '', request_id: '', reason: 'unknown'};
    return saved;
  }

  async readJob(id: string): Promise<Record<string, unknown> | null> {
    const job = this.readControl().jobs?.[id];
    return job ? {...job} : null;
  }
}

async function readJson(responsePromise: Promise<Response>) {
  const response = await responsePromise;
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const reader = response.body?.getReader();
  if (!reader) throw new Error('HTTP empty');
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 1024 * 1024) {
      await reader.cancel();
      throw new Error('Response too large');
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}

function github(env: Env, path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${env.GITHUB_TOKEN}`);
  headers.set('Accept', 'application/vnd.github+json');
  headers.set('X-GitHub-Api-Version', '2022-11-28');
  headers.set('User-Agent', 'news-briefing-scheduler');
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}${path}`, {
    ...init, headers, redirect: 'manual', signal: AbortSignal.timeout(10000),
  });
}

async function dispatchWorkflow(env: Env, body: object) {
  const response = await github(env, '/dispatches', {method: 'POST', body: JSON.stringify(body), headers: {'Content-Type': 'application/json'}});
  if (!response.ok) throw new Error(`GitHub dispatch HTTP ${response.status}`);
  if (response.body) await response.body.cancel();
}

export default {
  async scheduled(_controller, env, _ctx) {
    const gate = env as GateEnv;
    const stub = env.SCHEDULER.getByName('grroo/News');
    if (enabled(gate.RESERVATION_GATE_ENABLED)) await stub.checkReserved();
    else await stub.check();
  },
  async fetch(request, env) {
    const gate = env as GateEnv;
    const stub = env.SCHEDULER.getByName('grroo/News');
    return handleRequest(request, gate, {
      now: Date.now(),
      schedule,
      publicStatus: () => stub.status(),
      refresh: (input: {idempotencyKey: string}) => stub.refresh(input.idempotencyKey),
      dispatchWorkflow: async (body: object) => {
        try {
          await dispatchWorkflow(gate, body);
          await stub.noteDispatch('accepted');
        } catch (error) {
          await stub.noteDispatch('timeout');
          console.error(JSON.stringify({event: 'dispatch_ambiguous', message: error instanceof Error ? error.message : 'dispatch failed'}));
        }
      },
      claim: async (claim: object) => stub.claim(claim, new Date().toISOString()),
      readJob: (id: string) => stub.readJob(id),
    });
  },
} satisfies ExportedHandler<Env>;
