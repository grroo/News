(() => {
  'use strict';
  const $app = document.getElementById('app');
  const SECTIONS = ['news', 'sport', 'finance', 'media'];
  const LABELS = { news: 'News', sport: 'Sport', finance: 'Finance', media: 'Media' };
  const LS_SEEN = 'briefing.seen.v1';
  const LS_THEME = 'briefing.theme';
  const LS_PENDING = 'briefing.pendingFetch.v1';
  const storage = BriefingStorage;
  const refresh = BriefingRefresh;

  const ownerMeta = document.querySelector('meta[name="news-owner-url"]');
  const apiMeta = document.querySelector('meta[name="news-refresh-api"]');
  const OWNER_URL = ownerMeta?.content?.trim() || '';
  const REFRESH_API = apiMeta?.content?.trim() || '';

  // ── seen state ─────────────────────────────────────────────────────────
  const storedSeen = storage.readJSON(LS_SEEN, []);
  const seen = new Set(Array.isArray(storedSeen) ? storedSeen : []);
  const saveSeen = () => storage.writeJSON(LS_SEEN, [...seen].slice(-2000));
  const markSeen = k => { if (k && !seen.has(k)) { seen.add(k); saveSeen(); } };

  // ── theme ──────────────────────────────────────────────────────────────
  const applyTheme = () => {
    const t = storage.getItem(LS_THEME);
    if (t) document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
  };
  const toggleTheme = () => {
    const dark = matchMedia('(prefers-color-scheme: dark)').matches;
    const cur = storage.getItem(LS_THEME) || (dark ? 'dark' : 'light');
    storage.setItem(LS_THEME, cur === 'dark' ? 'light' : 'dark');
    applyTheme();
  };
  applyTheme();

  // ── data ───────────────────────────────────────────────────────────────
  let current = null, viewing = null, pastIndex = null, shown = null;
  const fetchJSON = async p => {
    const r = await fetch(p + '?t=' + Date.now(), { cache: 'no-store', signal: AbortSignal.timeout(15000) });
    if (!r.ok) throw new Error(r.status + ' ' + p);
    return r.json();
  };
  const loadCurrent = async () => current || (current = await fetchJSON('data/briefing.json'));
  const loadPastIndex = async () => pastIndex || (pastIndex = await fetchJSON('data/past/index.json').catch(() => []));

  // ── helpers ────────────────────────────────────────────────────────────
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ago = iso => {
    if (!iso) return '';
    const d = (Date.now() - new Date(iso)) / 60000;
    if (d < 1) return 'now'; if (d < 60) return Math.round(d) + 'm';
    if (d < 36 * 60) return Math.round(d / 60) + 'h';
    return Math.round(d / 1440) + 'd';
  };
  const dur = s => s ? (s >= 3600 ? Math.floor(s / 3600) + 'h ' + Math.round((s % 3600) / 60) + 'm' : Math.round(s / 60) + 'm') : '';
  const unseenCount = sec => (sec?.items || []).filter(i => !seen.has(i.key)).length;
  const fmtLocal = b => b.generated_local || new Date(b.generated_at).toLocaleString();

  let updateMessage = '', fetchMessage = '', checkingUpdates = false, fetchPoller = null;
  const slotLabel = (time, timezone) => new Date(time).toLocaleString('en-GB', {
    timeZone: timezone, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  const freshnessHTML = b => {
    const state = BriefingTime.status(b);
    const due = slotLabel(state.due, state.timezone), next = slotLabel(state.next, state.timezone);
    const tz = state.timezone === 'Europe/Rome' ? 'Rome time' : state.timezone;
    return `${state.overdue ? `<span class="overdue">Update overdue since ${esc(due)}.</span> ` : state.pending ? 'Scheduled update pending. ' : ''}Next scheduled: ${esc(next)} · ${esc(tz)}`;
  };

  const ownerLink = () => {
    if (!OWNER_URL) return '';
    const returnUrl = typeof location !== 'undefined' ? location.href.split('?')[0].split('#')[0] : '';
    return refresh.buildOwnerUrl(OWNER_URL, returnUrl);
  };

  const pendingFetch = () => storage.readJSON(LS_PENDING, null);
  const savePending = value => {
    if (value) storage.writeJSON(LS_PENDING, value);
    else storage.removeItem(LS_PENDING);
  };

  const statusMessages = () => [updateMessage, fetchMessage].filter(Boolean).join(' ');

  const headerHTML = (b, sub) => {
    const onLatest = b === current;
    const ownerHref = ownerLink();
    const pending = pendingFetch();
    const fetchDisabled = !!pending?.requestId;
    return `
    <header>
      <h1><a href="#/">Briefing</a></h1>
      <div class="row actions">
        <span class="meta">${esc(sub ?? ('Updated ' + fmtLocal(b)))}</span>
        <button class="btn" type="button" data-check-updates title="Reload the latest published briefing" ${checkingUpdates ? 'disabled' : ''}>${checkingUpdates ? 'Checking…' : 'Check for updates'}</button>
        ${onLatest && ownerHref ? `<a class="btn btn-primary" href="${esc(ownerHref)}" data-fetch-briefing ${fetchDisabled ? 'aria-disabled="true" tabindex="-1"' : ''} title="Sign in on the owner page to request a new briefing">Fetch new briefing</a>` : ''}
        ${onLatest && !ownerHref ? `<span class="meta owner-note">Owner fetch not configured yet.</span>` : ''}
        <button class="btn" type="button" data-theme-toggle aria-label="Toggle dark mode" title="Toggle dark mode">◐</button>
      </div>
    </header>
    ${onLatest ? `<div class="freshness"><div data-freshness>${freshnessHTML(b)}</div>
      <p class="status-line" data-status-live role="status" aria-live="polite">${esc(statusMessages())}</p>
      ${ownerHref ? `<p class="owner-help">Fetch new briefing opens a protected owner page. Sign in if asked, submit your request there, then return here to follow progress.</p>` : ''}
      ${storage.isDenied() ? '<p class="storage-note">Reading history cannot be saved in this browser.</p>' : ''}
    </div>` :
      `<div class="banner archive-note">Past briefing · ${esc(fmtLocal(b))}. <a href="#/">Back to latest</a>
      <p class="status-line" data-status-live role="status" aria-live="polite">${esc(statusMessages())}</p></div>`}`;
  };

  const healthHTML = (b, section) => {
    const feeds = (b.feed_health || []).filter(f => !section || f.section === section);
    if (!feeds.length) return '';
    const failed = feeds.filter(f => f.status === 'unavailable').length;
    const fallback = feeds.filter(f => f.status === 'fallback').length;
    return `<details class="health"><summary>Sources · ${feeds.length - failed}/${feeds.length} available${fallback ? ` · ${fallback} using fallback` : ''}${failed ? ` · ${failed} unavailable` : ''}</summary>
      <ul>${feeds.map(f => `<li class="${['ok', 'empty'].includes(f.status) ? '' : 'issue'}">${esc(f.name)} — ${f.status === 'fallback' ? 'publisher feed unavailable; using Google News' : f.status === 'unavailable' ? 'unavailable this update' : f.status === 'empty' ? 'feed available; no articles returned' : `${f.item_count} items fetched`}</li>`).join('')}</ul></details>`;
  };
  const safeHref = url => { try { const u = new URL(url); return ['https:', 'http:'].includes(u.protocol) ? u.href : '#'; } catch { return '#'; } };

  // ── views ──────────────────────────────────────────────────────────────
  const landing = b => `<div class="landing">
    ${headerHTML(b)}
    <div class="grid">
      ${SECTIONS.map(s => {
        const sec = b.sections[s] || { items: [] }, n = unseenCount(sec);
        const path = b === current ? `#/${s}` : `#/past/${encodeURIComponent(viewing)}/${s}`;
        return `<a class="tile" style="--c:var(--${s})" href="${path}">
          <div class="name">${LABELS[s]}</div>
          <div class="preview">${sec.items[0] ? `<span class="preview-title">${esc(sec.items[0].title)}</span><span class="preview-source">${esc(sec.items[0].source)}</span>` : 'No stories in this edition.'}</div>
          <div class="count ${n ? '' : 'zero'}"><b>${n}</b> unread · ${sec.items.length} items</div>
        </a>`;
      }).join('')}
    </div>
    ${healthHTML(b)}
    <footer>
      <span><a href="#/past">Past briefings</a></span>
      <span>${b.mode === 'mock' ? 'mock mode · ' : ''}${esc(refresh.formatUsageLine(b.usage))}${esc((b.model || '').replace('claude-', ''))}</span>
    </footer></div>`;

  const card = it => `
    <a class="card ${seen.has(it.key) ? 'seen' : ''}" href="${esc(safeHref(it.url))}" target="_blank" rel="noopener" data-key="${esc(it.key)}">
      <span class="dot"></span>
      <p class="t">${esc(it.title)}</p>
      ${it.summary ? `<p class="s">${esc(it.summary)}</p>` : ''}
      <p class="m">${it.kind ? `<span class="kind">${esc(it.kind)}</span>` : ''}<span>${esc(it.source)}</span><span>·</span><span>${ago(it.published)}</span>${it.duration_s ? `<span>·</span><span>${dur(it.duration_s)}</span>` : ''}${it.via ? `<span>· via ${esc(it.via)}</span>` : ''}</p>
    </a>`;

  const briefHTML = (br, mock, s) => {
    if (!br || (Array.isArray(br) && !br.length)) return '';
    const body = Array.isArray(br) ? `<ul>${br.map(x => {
      const text = typeof x === 'string' ? x : x.text;
      const refs = typeof x === 'object' && Array.isArray(x.sources) ? x.sources : [];
      return `<li>${esc(text)}${refs.length ? `<div class="citations">${refs.map(r => `<a href="${esc(safeHref(r.url))}" target="_blank" rel="noopener" data-citation-key="${esc(r.key)}" title="${esc(r.title)}">${esc(r.source)} ↗</a>`).join('')}</div>` : ''}</li>`;
    }).join('')}</ul>` : esc(br);
    return `<div class="brief ${mock ? 'mock' : ''}" style="--c:var(--${s})">${body}</div>`;
  };

  const tickers = (rows, caption) => {
    if (!rows?.length) return '';
    const showQuoteTimes = rows.some(r => r.as_of);
    return `
    ${caption ? `<p class="tick-caption">${esc(caption)}</p>` : ''}
    <table class="tick" aria-label="Market quotes">
      <thead><tr><th scope="col">Instrument</th><th scope="col">Price</th><th scope="col">Day change</th>${showQuoteTimes ? '<th scope="col">Quote time</th>' : ''}</tr></thead>
      <tbody>${rows.map(r => {
      const p = r.change_pct, cls = p == null ? '' : p >= 0 ? 'up' : 'down';
      return `<tr><td>${esc(r.label)}<span class="sym">${esc(r.symbol)}</span></td>
        <td class="num">${r.price != null ? r.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—'}${r.currency ? ` <span class="sym">${esc(r.currency)}</span>` : ''}</td>
        <td class="num ${cls}">${p == null ? '—' : (p > 0 ? '+' : '') + p.toFixed(2) + '%'}</td>
        ${showQuoteTimes ? `<td class="num sym">${r.as_of ? esc(refresh.formatShortTime(r.as_of)) : '—'}</td>` : ''}</tr>`;
    }).join('')}</tbody></table>`;
  };

  const section = (b, s) => {
    const sec = b.sections[s] || { items: [] };
    const backPath = b === current ? '#/' : `#/past/${encodeURIComponent(viewing)}`;
    const ageNote = refresh.sectionAgeNote(sec, b);
    const finCaption = s === 'finance' ? refresh.financeCaption(sec, b) : '';
    return `
      ${headerHTML(b)}
      <div class="section-head" style="--c:var(--${s})">
        <h2>${LABELS[s]}</h2>
        <div class="row"><a class="btn" href="${backPath}">← Home</a><button class="btn" type="button" data-mark-all="${s}">Mark all read</button></div>
      </div>
      ${ageNote ? `<p class="section-age">${esc(ageNote)}</p>` : ''}
      ${s === 'finance' ? tickers(sec.tickers, finCaption) : ''}
      ${briefHTML(sec.briefing, b.mode === 'mock' || !!sec.error, s)}
      <div class="cards">${sec.items.length ? sec.items.map(card).join('') : '<div class="empty">Nothing new in this window.</div>'}</div>
      ${healthHTML(b, s)}
      <footer><span>${sec.candidate_count ?? sec.items.length} collected${sec.reviewed_count != null ? ` · ${sec.reviewed_count} reviewed by AI` : ''}</span><span><a href="#/past">Past briefings</a></span></footer>`;
  };

  const past = (b, idx) => `
    ${headerHTML(b, 'Past briefings')}
    <div class="past">
      <a href="#/"><b>Latest</b><small>${esc(fmtLocal(b))}</small></a>
      ${idx.length ? idx.map(p => `<a href="#/past/${encodeURIComponent(p.file)}">${esc(new Date(p.generated_at).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }))}
        <small>${SECTIONS.map(s => `${LABELS[s]} ${p.counts?.[s] ?? 0}`).join(' · ')}</small></a>`).join('')
        : '<div class="empty">No past briefings yet.</div>'}
    </div>`;

  // ── status UI ──────────────────────────────────────────────────────────
  const updateStatusLine = () => {
    const line = $app.querySelector('[data-status-live]');
    if (line) line.textContent = statusMessages();
    const freshness = $app.querySelector('[data-freshness]');
    if (freshness && current) freshness.innerHTML = freshnessHTML(current);
    const checkBtn = $app.querySelector('[data-check-updates]');
    if (checkBtn) {
      checkBtn.disabled = checkingUpdates;
      checkBtn.textContent = checkingUpdates ? 'Checking…' : 'Check for updates';
    }
  };

  const stripReturnParams = () => {
    const url = new URL(location.href);
    ['request', 'request_id', 'job', 'job_id', 'status', 'status_url', 'refresh_error', 'retry_after'].forEach(k => url.searchParams.delete(k));
    history.replaceState(null, '', url.pathname + url.hash);
  };

  const beginFetchPoll = async params => {
    if (fetchPoller?.running) return;
    const baseline = current || await loadCurrent().catch(() => null);
    savePending({ requestId: params.requestId, jobId: params.jobId, startedAt: Date.now() });
    fetchMessage = refresh.userStatusMessage(params.initialStatus || 'accepted');
    updateStatusLine();

    fetchPoller = refresh.startFetchPoll({
      fetchFn: async (url, init) => fetch(url, { ...init, credentials: 'include' }),
      loadBriefing: async () => {
        const data = await fetchJSON('data/briefing.json');
        current = data;
        return data;
      },
      onStatus: ({ message, status }) => {
        fetchMessage = message || refresh.userStatusMessage(status || 'waiting_publish');
        updateStatusLine();
      },
      onComplete: async ({ status, edition }) => {
        savePending(null);
        current = edition;
        pastIndex = null;
        fetchMessage = refresh.userStatusMessage(status);
        if (status === 'no_change') fetchMessage = 'Nothing new was published for your request.';
        await render(false);
        updateStatusLine();
      },
      onError: ({ code, message }) => {
        if (code !== 'timeout') savePending(null);
        fetchMessage = message;
        updateStatusLine();
      },
    });

    fetchPoller.setVisible(document.visibilityState === 'visible');
    await fetchPoller.start({
      requestId: params.requestId,
      statusUrl: refresh.resolveStatusUrl(params.statusPath, REFRESH_API),
      baselineEdition: baseline,
    });
  };

  const resumePendingFetch = async () => {
    const params = refresh.parseReturnParams(location.search);
    if (params.refreshError) {
      fetchMessage = refresh.userStatusMessage(params.refreshError, { retry_after: params.retryAfter });
      stripReturnParams();
      return;
    }
    const pending = pendingFetch();
    const requestId = params.requestId || pending?.requestId;
    if (!requestId) return;
    if (params.requestId) stripReturnParams();
    await beginFetchPoll({
      requestId,
      jobId: params.jobId || pending?.jobId,
      statusPath: params.statusPath || pending?.statusPath,
    });
  };

  // ── check for updates (read-only) ────────────────────────────────────
  let lastCheck = 0;
  const checkForUpdates = async (manual = false) => {
    if (checkingUpdates || (!manual && Date.now() - lastCheck < 60000)) return;
    lastCheck = Date.now();
    checkingUpdates = true;
    if (manual) updateMessage = 'Checking for a newer published briefing…';
    updateStatusLine();
    try {
      const fresh = await fetchJSON('data/briefing.json');
      if (!current || fresh.generated_at !== current.generated_at) {
        current = fresh;
        pastIndex = null;
        updateMessage = 'Latest briefing loaded.';
        await render(false);
      } else if (manual) {
        updateMessage = BriefingTime.status(current).overdue
          ? 'No newer briefing published yet. This update is overdue.'
          : 'You have the latest published briefing.';
        updateStatusLine();
      } else {
        updateMessage = '';
      }
    } catch {
      updateMessage = current
        ? 'Could not check for updates. Keeping your current briefing.'
        : 'Could not load the briefing. Please try again.';
    } finally {
      checkingUpdates = false;
      updateStatusLine();
    }
  };

  // ── router ─────────────────────────────────────────────────────────────
  let renderVersion = 0;
  const render = async (resetScroll = true) => {
    const version = ++renderVersion;
    try {
      const b = await loadCurrent();
      if (version !== renderVersion) return;
      const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
      if (parts[0] === 'past') {
        if (!parts[1]) {
          const idx = await loadPastIndex();
          if (version !== renderVersion) return;
          shown = b;
          $app.innerHTML = past(b, idx);
        } else {
          viewing = parts[1];
          const pb = await fetchJSON('data/past/' + encodeURIComponent(parts[1]));
          if (version !== renderVersion) return;
          shown = pb;
          $app.innerHTML = SECTIONS.includes(parts[2]) ? section(pb, parts[2]) : landing(pb);
        }
      } else if (SECTIONS.includes(parts[0])) {
        viewing = null;
        shown = b;
        $app.innerHTML = section(b, parts[0]);
      } else {
        viewing = null;
        shown = b;
        $app.innerHTML = landing(b);
      }
      if (resetScroll) window.scrollTo(0, 0);
      updateStatusLine();
    } catch {
      if (version !== renderVersion) return;
      $app.innerHTML = `<div class="empty">Could not load the briefing. Check your connection and try again.<br><button class="btn" type="button" data-check-updates>Retry</button> <a href="#/">Back to latest</a></div>`;
    }
  };

  $app.addEventListener('click', e => {
    if (e.target.closest('[data-check-updates]')) { checkForUpdates(true); return; }
    const fetchBtn = e.target.closest('[data-fetch-briefing]');
    if (fetchBtn) {
      if (pendingFetch()?.requestId) {
        e.preventDefault();
        fetchMessage = 'A fetch is already in progress. Return here to follow it, or wait for it to finish.';
        updateStatusLine();
        return;
      }
      return;
    }
    const ref = e.target.closest('[data-citation-key]'); if (ref) { markSeen(ref.dataset.citationKey); return; }
    const c = e.target.closest('.card'); if (c) { markSeen(c.dataset.key); c.classList.add('seen'); return; }
    const m = e.target.closest('[data-mark-all]');
    if (m) {
      const sec = shown?.sections?.[m.dataset.markAll];
      (sec?.items || []).forEach(i => markSeen(i.key));
      render(false);
      return;
    }
    if (e.target.closest('[data-theme-toggle]')) toggleTheme();
  });

  window.addEventListener('hashchange', () => render());
  document.addEventListener('visibilitychange', () => {
    refresh.getActivePoller()?.setVisible(document.visibilityState === 'visible');
    if (document.visibilityState === 'visible') checkForUpdates();
  });
  window.addEventListener('pageshow', e => { if (e.persisted) checkForUpdates(); });
  setInterval(() => {
    if (document.visibilityState === 'visible') {
      updateStatusLine();
      checkForUpdates();
    }
  }, 60000);

  render().then(() => resumePendingFetch());
})();
