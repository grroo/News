/* Refresh job tracking, polling and publication acknowledgment (shared by page and tests). */
var BriefingRefresh = (() => {
  'use strict';

  const TERMINAL_JOB = new Set(['succeeded', 'no_change', 'degraded', 'failed', 'expired']);
  const ACTIVE_JOB = new Set(['accepted', 'queued', 'building', 'publishing']);
  const POLL_DELAYS_MS = [2000, 3000, 5000, 8000, 12000, 20000, 30000];
  const MAX_POLL_MS = 5 * 60 * 1000;

  const STATUS_LABELS = {
    accepted: 'Request received',
    queued: 'Waiting in queue',
    building: 'Building your briefing',
    publishing: 'Publishing',
    succeeded: 'New briefing ready',
    no_change: 'Nothing new to publish',
    degraded: 'Briefing updated with some older sections',
    failed: 'Could not complete this fetch',
    expired: 'This request expired',
    rate_limited: 'Too many requests — please wait',
    daily_limit: 'Daily limit reached',
    concurrent_job: 'A fetch is already in progress',
    checking: 'Checking for updates',
    waiting_publish: 'Waiting for publication',
    unrelated: 'A newer briefing appeared, but not for this request',
    timeout: 'This is taking longer than expected',
  };

  const userStatusMessage = (status, details = {}) => {
    if (status === 'rate_limited' || status === 'daily_limit' || status === 'concurrent_job') {
      const retry = details.retry_after ? formatShortTime(details.retry_after) : '';
      return retry ? `${STATUS_LABELS[status]}. Try again after ${retry}.` : STATUS_LABELS[status];
    }
    return STATUS_LABELS[status] || STATUS_LABELS.waiting_publish;
  };

  const isTerminalJobStatus = status => TERMINAL_JOB.has(status);
  const isActiveJobStatus = status => ACTIVE_JOB.has(status);

  const publicationOutcome = edition => {
    if (edition?.refresh?.outcome === 'no_change') return 'no_change';
    const quality = edition?.quality?.overall;
    if (quality === 'degraded' || quality === 'failed') return quality;
    return 'succeeded';
  };

  const requestAcknowledged = (edition, requestId) => {
    if (!edition || !requestId) return false;
    const ids = edition.request_ids;
    return Array.isArray(ids) && ids.includes(requestId);
  };

  const unrelatedNewerEdition = (edition, requestId, baselineEdition) => {
    if (!edition || !requestId || !baselineEdition) return false;
    if (requestAcknowledged(edition, requestId)) return false;
    const newer = Date.parse(edition.generated_at || 0);
    const baseline = Date.parse(baselineEdition.generated_at || 0);
    return Number.isFinite(newer) && Number.isFinite(baseline) && newer > baseline;
  };

  const pollDelayMs = (attempt, visible = true) => {
    const base = POLL_DELAYS_MS[Math.min(Math.max(attempt, 0), POLL_DELAYS_MS.length - 1)];
    return visible ? base : Math.max(base, 15000);
  };

  const parseReturnParams = search => {
    const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
    return {
      requestId: params.get('request') || params.get('request_id') || '',
      jobId: params.get('job') || params.get('job_id') || '',
      statusPath: params.get('status') || params.get('status_url') || '',
      refreshError: params.get('refresh_error') || '',
      retryAfter: params.get('retry_after') || '',
    };
  };

  const buildOwnerUrl = (ownerBase, returnUrl) => {
    if (!ownerBase) return '';
    const url = new URL(ownerBase, typeof location !== 'undefined' ? location.href : 'https://example.com/');
    if (returnUrl) url.searchParams.set('return', returnUrl);
    return url.href;
  };

  const resolveStatusUrl = (statusPath, apiBase) => {
    if (!statusPath) return '';
    if (/^https?:\/\//i.test(statusPath)) return statusPath;
    const base = (apiBase || '').replace(/\/$/, '');
    const path = statusPath.startsWith('/') ? statusPath : `/${statusPath}`;
    return base ? `${base}${path}` : path;
  };

  const formatShortTime = iso => {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleString(undefined, {
        weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
      });
    } catch {
      return iso;
    }
  };

  const sectionAgeNote = (section, briefing) => {
    if (!section || !briefing) return '';
    const state = section.state;
    const last = section.last_success_at;
    if (!last || !state || state === 'healthy' || state === 'skipped') return '';
    if (state === 'unchanged') return `Unchanged since ${formatShortTime(last)}`;
    if (state === 'degraded' && section.error) {
      return `Carried forward from ${formatShortTime(last)} — ${section.error}`;
    }
    if (state === 'degraded' || state === 'failed') {
      return `Section last updated ${formatShortTime(last)}`;
    }
    return '';
  };

  const quoteTimesFromTickers = tickers => {
    if (!Array.isArray(tickers)) return [];
    return [...new Set(tickers.map(t => t?.as_of).filter(Boolean))].sort();
  };

  const financeCaption = (section, briefing) => {
    if (!briefing) return '';
    const times = quoteTimesFromTickers(section?.tickers);
    const briefAt = briefing.generated_at;
    if (!times.length) return briefAt ? `Briefing ${formatShortTime(briefAt)}` : '';
    const quoteLabel = times.length === 1
      ? `Quotes ${formatShortTime(times[0])}`
      : `Quotes ${formatShortTime(times[0])}–${formatShortTime(times[times.length - 1])}`;
    if (!briefAt) return quoteLabel;
    return `${quoteLabel} · briefing ${formatShortTime(briefAt)}`;
  };

  const finiteAmount = value => (typeof value === 'number' && Number.isFinite(value)) ? value : null;

  const formatUsageLine = usage => {
    if (!usage?.calls) return '';
    const run = finiteAmount(usage.est_cost_usd);
    const month = finiteAmount(usage.est_month_usd);
    if (run != null && month != null) return `this run $${run.toFixed(3)} · ≈$${month}/mo · `;
    if (run != null) return `this run $${run.toFixed(3)} · `;
    return 'cost unavailable · ';
  };

  class FetchPoller {
    constructor(options = {}) {
      this.fetchFn = options.fetchFn || (async url => fetch(url));
      this.loadBriefing = options.loadBriefing || (async () => null);
      this.maxMs = options.maxMs ?? MAX_POLL_MS;
      this.pollDelays = options.pollDelays || POLL_DELAYS_MS;
      this.onStatus = options.onStatus || (() => {});
      this.onComplete = options.onComplete || (() => {});
      this.onError = options.onError || (() => {});
      this._timer = null;
      this._attempt = 0;
      this._started = 0;
      this._stopped = true;
      this._visible = true;
    }

    get running() { return !this._stopped; }

    stop() {
      this._stopped = true;
      if (this._timer) {
        clearTimeout(this._timer);
        this._timer = null;
      }
    }

    setVisible(visible) {
      this._visible = visible !== false;
    }

    async start({ requestId, statusUrl, baselineEdition }) {
      if (!this._stopped) return false;
      this._stopped = false;
      this._attempt = 0;
      this._started = Date.now();
      await this._tick({ requestId, statusUrl, baselineEdition });
      return true;
    }

    async _tick(ctx) {
      if (this._stopped) return;
      if (Date.now() - this._started >= this.maxMs) {
        this.stop();
        this.onError({ code: 'timeout', message: userStatusMessage('timeout') });
        return;
      }

      let jobStatus = '';
      if (ctx.statusUrl) {
        try {
          const res = await this.fetchFn(ctx.statusUrl, { cache: 'no-store' });
          if (res.ok) {
            const payload = await res.json();
            jobStatus = payload.status || '';
            this.onStatus({ phase: 'job', status: jobStatus, payload });
            if (payload.error === 'rate_limited' || payload.status === 'rate_limited') {
              this.stop();
              this.onError({
                code: 'rate_limited',
                message: userStatusMessage('rate_limited', payload),
                retry_after: payload.retry_after,
              });
              return;
            }
            if (isTerminalJobStatus(jobStatus) && jobStatus !== 'succeeded' && jobStatus !== 'no_change' && jobStatus !== 'degraded') {
              this.stop();
              this.onError({ code: jobStatus, message: userStatusMessage(jobStatus), payload });
              return;
            }
          }
        } catch {
          /* status endpoint may be unavailable cross-origin until T05 enables CORS */
        }
      }

      let edition = null;
      try {
        edition = await this.loadBriefing();
      } catch {
        this.onStatus({ phase: 'pages', status: 'checking', message: userStatusMessage('checking') });
      }

      if (edition && requestAcknowledged(edition, ctx.requestId)) {
        this.stop();
        const outcome = publicationOutcome(edition);
        if (outcome === 'failed') {
          this.onError({ code: 'failed', message: userStatusMessage('failed'), edition });
          return;
        }
        this.onComplete({ status: outcome, edition, requestId: ctx.requestId, jobStatus });
        return;
      }

      if (edition && unrelatedNewerEdition(edition, ctx.requestId, ctx.baselineEdition)) {
        this.stop();
        this.onError({ code: 'unrelated', message: userStatusMessage('unrelated'), edition });
        return;
      }

      const display = jobStatus && isActiveJobStatus(jobStatus)
        ? userStatusMessage(jobStatus)
        : userStatusMessage('waiting_publish');
      this.onStatus({ phase: 'pages', status: jobStatus || 'waiting_publish', message: display });

      const delays = this._visible
        ? this.pollDelays
        : this.pollDelays.map(d => Math.max(d, 15000));
      const delay = delays[Math.min(Math.max(this._attempt++, 0), delays.length - 1)];
      if (Date.now() - this._started + delay >= this.maxMs) {
        this.stop();
        this.onError({ code: 'timeout', message: userStatusMessage('timeout') });
        return;
      }
      this._timer = setTimeout(() => this._tick(ctx), delay);
    }
  }

  let activePoller = null;

  const startFetchPoll = (options) => {
    if (activePoller?.running) activePoller.stop();
    activePoller = new FetchPoller(options);
    return activePoller;
  };

  const getActivePoller = () => activePoller;

  return {
    TERMINAL_JOB,
    ACTIVE_JOB,
    POLL_DELAYS_MS,
    MAX_POLL_MS,
    userStatusMessage,
    isTerminalJobStatus,
    isActiveJobStatus,
    publicationOutcome,
    requestAcknowledged,
    unrelatedNewerEdition,
    pollDelayMs,
    parseReturnParams,
    buildOwnerUrl,
    resolveStatusUrl,
    sectionAgeNote,
    quoteTimesFromTickers,
    financeCaption,
    formatUsageLine,
    formatShortTime,
    FetchPoller,
    startFetchPoll,
    getActivePoller,
  };
})();

if (typeof module !== 'undefined') module.exports = BriefingRefresh;
