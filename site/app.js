(() => {
  'use strict';
  const $app = document.getElementById('app');
  const SECTIONS = ['news', 'sport', 'finance', 'media'];
  const LABELS = { news: 'News', sport: 'Sport', finance: 'Finance', media: 'Media' };
  const LS_SEEN = 'briefing.seen.v1', LS_THEME = 'briefing.theme';

  // ── seen state (localStorage only) ─────────────────────────────────────
  let storedSeen = [];
  try { storedSeen = JSON.parse(localStorage.getItem(LS_SEEN) || '[]'); } catch {}
  const seen = new Set(Array.isArray(storedSeen) ? storedSeen : []);
  const saveSeen = () => localStorage.setItem(LS_SEEN, JSON.stringify([...seen].slice(-2000)));
  const markSeen = k => { if (k && !seen.has(k)) { seen.add(k); saveSeen(); } };

  // ── theme ──────────────────────────────────────────────────────────────
  const applyTheme = () => {
    const t = localStorage.getItem(LS_THEME);
    if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  };
  const toggleTheme = () => {
    const dark = matchMedia('(prefers-color-scheme: dark)').matches;
    const cur = localStorage.getItem(LS_THEME) || (dark ? 'dark' : 'light');
    localStorage.setItem(LS_THEME, cur === 'dark' ? 'light' : 'dark');
    applyTheme();
  };
  applyTheme();

  // ── data ───────────────────────────────────────────────────────────────
  let current = null, viewing = null, pastIndex = null, shown = null;
  const fetchJSON = async p => { const r = await fetch(p + '?t=' + Date.now(), {cache: 'no-store', signal: AbortSignal.timeout(15000)}); if (!r.ok) throw new Error(r.status + ' ' + p); return r.json(); };
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

  let refreshMessage = '', refreshing = false;
  const slotLabel = (time, timezone) => new Date(time).toLocaleString('en-GB', {
    timeZone: timezone, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  });
  const freshnessHTML = b => {
    const state = BriefingTime.status(b);
    const due = slotLabel(state.due, state.timezone), next = slotLabel(state.next, state.timezone);
    const tz = state.timezone === 'Europe/Rome' ? 'Rome time' : state.timezone;
    return `${state.overdue ? `<span class="overdue">Update overdue since ${esc(due)}.</span> ` : state.pending ? 'Scheduled update pending. ' : ''}Next scheduled: ${esc(next)} · ${esc(tz)}`;
  };
  const headerHTML = (b, sub) => `
    <header>
      <h1><a href="#/">Briefing</a></h1>
      <div class="row">
        <span class="meta">${esc(sub ?? ('Updated ' + fmtLocal(b)))}</span>
        <button class="btn" data-refresh title="Check for the latest published briefing" ${refreshing ? 'disabled' : ''}>${refreshing ? 'Checking…' : 'Refresh'}</button>
        <button class="btn" data-theme-toggle aria-label="Toggle dark mode" title="Toggle dark mode">◐</button>
      </div>
    </header>
    ${b === current ? `<div class="freshness"><div data-freshness>${freshnessHTML(b)}</div><p data-refresh-result role="status">${esc(refreshMessage)}</p></div>` :
      `<div class="banner archive-note">Past briefing · ${esc(fmtLocal(b))}. <a href="#/">Back to latest</a><p data-refresh-result role="status">${esc(refreshMessage)}</p></div>`}`;

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
      <span>${b.mode === 'mock' ? 'mock mode · ' : ''}${b.usage?.calls ? `this run $${b.usage.est_cost_usd.toFixed(3)} · ≈$${b.usage.est_month_usd}/mo · ` : ''}${esc((b.model || '').replace('claude-', ''))}</span>
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

  const tickers = rows => rows?.length ? `
    <table class="tick"><tbody>${rows.map(r => {
      const p = r.change_pct, cls = p == null ? '' : p >= 0 ? 'up' : 'down';
      return `<tr><td>${esc(r.label)}<span class="sym">${esc(r.symbol)}</span></td>
        <td class="num">${r.price != null ? r.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—'}${r.currency ? ` <span class="sym">${esc(r.currency)}</span>` : ''}</td>
        <td class="num ${cls}">${p == null ? '—' : (p > 0 ? '+' : '') + p.toFixed(2) + '%'}</td></tr>`;
    }).join('')}</tbody></table>` : '';

  const section = (b, s) => {
    const sec = b.sections[s] || { items: [] };
    const backPath = b === current ? '#/' : `#/past/${encodeURIComponent(viewing)}`;
    return `
      ${headerHTML(b)}
      <div class="section-head" style="--c:var(--${s})">
        <h2>${LABELS[s]}</h2>
        <div class="row"><a class="btn" href="${backPath}">← Home</a><button class="btn" data-mark-all="${s}">Mark all read</button></div>
      </div>
      ${s === 'finance' ? tickers(sec.tickers) : ''}
      ${briefHTML(sec.briefing, b.mode === 'mock' || !!sec.error, s)}
      <div class="cards">${sec.items.length ? sec.items.map(card).join('') : '<div class="empty">Nothing new in this window.</div>'}</div>
      ${healthHTML(b, s)}
      <footer><span>${sec.candidate_count ?? sec.items.length} collected${sec.reviewed_count != null ? ` · ${sec.reviewed_count} reviewed by AI` : ''}</span><span><a href="#/past">Past briefings</a></span></footer>`;
  };

  const past = (b, idx) => `
    ${headerHTML(b, 'past briefings')}
    <div class="past">
      <a href="#/"><b>Latest</b><small>${esc(fmtLocal(b))}</small></a>
      ${idx.length ? idx.map(p => `<a href="#/past/${encodeURIComponent(p.file)}">${esc(new Date(p.generated_at).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }))}
        <small>${SECTIONS.map(s => `${LABELS[s]} ${p.counts?.[s] ?? 0}`).join(' · ')}</small></a>`).join('')
        : '<div class="empty">No past briefings yet.</div>'}
    </div>`;

  // ── router ─────────────────────────────────────────────────────────────
  let renderVersion = 0;
  const render = async (resetScroll = true) => {
    const version = ++renderVersion;
    try {
      const b = await loadCurrent();
      if (version !== renderVersion) return;
      const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
      if (parts[0] === 'past') {
        if (!parts[1]) { const idx = await loadPastIndex(); if (version !== renderVersion) return; shown = b; $app.innerHTML = past(b, idx); }
        else {
          viewing = parts[1];
          const pb = await fetchJSON('data/past/' + encodeURIComponent(parts[1]));
          if (version !== renderVersion) return;
          shown = pb; $app.innerHTML = SECTIONS.includes(parts[2]) ? section(pb, parts[2]) : landing(pb);
        }
      } else if (SECTIONS.includes(parts[0])) {
        viewing = null; shown = b; $app.innerHTML = section(b, parts[0]);
      } else {
        viewing = null; shown = b; $app.innerHTML = landing(b);
      }
      if (resetScroll) window.scrollTo(0, 0);
    } catch (e) {
      if (version !== renderVersion) return;
      $app.innerHTML = `<div class="empty">Could not load the briefing. Check your connection and try again.<br><button class="btn" data-refresh>Retry</button> <a href="#/">Back to latest</a></div>`;
    }
  };

  $app.addEventListener('click', e => {
    if (e.target.closest('[data-refresh]')) { refresh(true); return; }
    const ref = e.target.closest('[data-citation-key]'); if (ref) { markSeen(ref.dataset.citationKey); return; }
    const c = e.target.closest('.card'); if (c) { markSeen(c.dataset.key); c.classList.add('seen'); return; }
    const m = e.target.closest('[data-mark-all]');
    if (m) { const sec = shown?.sections?.[m.dataset.markAll];
      (sec?.items || []).forEach(i => markSeen(i.key)); render(); return; }
    if (e.target.closest('[data-theme-toggle]')) toggleTheme();
  });
  window.addEventListener('hashchange', () => render());
  let lastCheck = 0;
  const updateStatus = () => {
    const status = $app.querySelector('[data-freshness]');
    if (status && current) status.innerHTML = freshnessHTML(current);
    const result = $app.querySelector('[data-refresh-result]');
    if (result) result.textContent = refreshMessage;
    const btn = $app.querySelector('[data-refresh]');
    if (btn) { btn.disabled = refreshing; btn.textContent = refreshing ? 'Checking…' : 'Refresh'; }
  };
  const refresh = async (manual = false) => {
    if (refreshing || (!manual && Date.now() - lastCheck < 60000)) return;
    lastCheck = Date.now(); refreshing = true;
    if (manual) refreshMessage = 'Checking for a newer published briefing…';
    updateStatus();
    try {
      const fresh = await fetchJSON('data/briefing.json');
      if (!current || fresh.generated_at !== current.generated_at) {
        current = fresh; pastIndex = null;
        refreshMessage = 'Latest briefing loaded.';
        await render(false);
      } else if (manual) {
        refreshMessage = BriefingTime.status(current).overdue ? 'No newer briefing published yet. This update is overdue.' : 'You have the latest published briefing.';
        await render(false);
      } else refreshMessage = '';
    } catch {
      refreshMessage = current ? 'Could not check for updates. Keeping your current briefing.' : 'Could not load the briefing. Please try again.';
    } finally { refreshing = false; updateStatus(); }
  };
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
  window.addEventListener('pageshow', e => { if (e.persisted) refresh(); });
  setInterval(() => { if (document.visibilityState === 'visible') { updateStatus(); refresh(); } }, 60000);
  render();
})();
