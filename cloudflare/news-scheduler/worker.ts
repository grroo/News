import { DurableObject } from 'cloudflare:workers';
import schedule from '../../schedule.json';
import { tick } from './core.mjs';

type SchedulerStatus = Record<string, string | number | null>;

export class NewsScheduler extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS scheduler (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, lease_until INTEGER NOT NULL)');
  }
  async check(): Promise<SchedulerStatus> {
    const now = Date.now();
    // Synchronous SQLite lease serializes checks without holding a lock over I/O.
    const rows = this.ctx.storage.sql.exec<{payload:string}>(
      "INSERT INTO scheduler VALUES (1, '{}', ?) ON CONFLICT(id) DO UPDATE SET lease_until=excluded.lease_until WHERE scheduler.lease_until < ? RETURNING payload", now+60000,now).toArray();
    if (!rows.length) return {phase:'check_in_progress'};
    const save = async (state: object) => {
      this.ctx.storage.sql.exec('UPDATE scheduler SET payload=? WHERE id=1', JSON.stringify(state));
    };
    try {
      const result = await tick({now,schedule,state:JSON.parse(rows[0].payload),env:this.env,save});
      await save(result);
      console.log(JSON.stringify({event:'briefing_check', ...result}));
      return result;
    } catch (error) {
      const current = this.status();
      const message = error instanceof Error ? error.message : 'Scheduler check failed';
      await save({...current, phase:'check_failed', checkedAt:new Date(now).toISOString(), error:message});
      console.error(JSON.stringify({event:'briefing_check_failed',message}));
      throw error;
    } finally {
      this.ctx.storage.sql.exec('UPDATE scheduler SET lease_until=0 WHERE id=1');
    }
  }
  status(): SchedulerStatus {
    const row = this.ctx.storage.sql.exec<{payload:string}>('SELECT payload FROM scheduler WHERE id=1').toArray()[0];
    return row ? JSON.parse(row.payload) : {phase:'not_checked'};
  }
}

export default {
  async scheduled(_controller, env, _ctx) {
    await env.SCHEDULER.getByName('grroo/News').check();
  },
  async fetch(request, env) {
    if (request.method !== 'GET' || new URL(request.url).pathname !== '/health') return new Response('Not found', {status:404});
    const state = await env.SCHEDULER.getByName('grroo/News').status();
    // Public read-only health; there is deliberately no public dispatch endpoint.
    return Response.json({configured:!!env.GITHUB_TOKEN,schedule,...state}, {headers:{'Cache-Control':'no-store'}});
  }
} satisfies ExportedHandler<Env>;
