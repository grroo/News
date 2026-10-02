const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const morph = require('../site/morph.js');

test('route recognizes current and past landing and section hashes', () => {
  assert.deepEqual(morph.route('#/'), { kind: 'landing', section: null, past: null });
  assert.deepEqual(morph.route('#/sport'), { kind: 'section', section: 'sport', past: null });
  assert.deepEqual(morph.route('#/past/edition.json'), { kind: 'landing', section: null, past: 'edition.json' });
  assert.deepEqual(morph.route('#/past/edition.json/sport'), { kind: 'section', section: 'sport', past: 'edition.json' });
  assert.equal(morph.route('#/past').kind, 'other');
});

test('only landing and section in one edition can morph', () => {
  const home = morph.route('#/');
  const sport = morph.route('#/sport');
  const news = morph.route('#/news');
  const pastHome = morph.route('#/past/edition.json');
  for (const [from, to] of [[home, sport], [sport, home], [pastHome, morph.route('#/past/edition.json/sport')]]) {
    assert.equal(morph.shouldMorph(from, to, { hasApi: true, reducedMotion: false }), true);
  }
  for (const [from, to] of [[sport, news], [home, morph.route('#/past')], [home, pastHome], [sport, pastHome]]) {
    assert.equal(morph.shouldMorph(from, to, { hasApi: true, reducedMotion: false }), false);
  }
  assert.equal(morph.shouldMorph(home, sport, { hasApi: false, reducedMotion: false }), false);
  assert.equal(morph.shouldMorph(home, sport, { hasApi: true, reducedMotion: true }), false);
});

const fakeDocument = (initial, { reducedMotion = false, hasApi = true, rejectFinished = false, missing = false } = {}) => {
  const events = [];
  let view = initial;
  const finishers = [];
  const element = (label, childName) => {
    const child = missing ? null : {
      style: { set viewTransitionName(value) { events.push(`${label}.${childName}:${value}`); } },
    };
    return {
      style: { set viewTransitionName(value) { events.push(`${label}:${value}`); } },
      querySelector(selector) {
        events.push(`child:${label}:${selector}`);
        return child;
      },
    };
  };
  const tile = missing ? null : element('tile', 'name');
  const page = element('page', 'h2');
  const doc = {
    defaultView: { matchMedia(query) { assert.equal(query, '(prefers-reduced-motion: reduce)'); return { matches: reducedMotion }; } },
    querySelector(selector) {
      events.push(`lookup:${selector}`);
      if (selector === '.tile[data-section="sport"]' && view === 'landing') return tile;
      if (selector === '.section-page[data-section="sport"]' && view === 'section') return page;
      return null;
    },
  };
  if (hasApi) doc.startViewTransition = callback => {
    events.push('transition');
    const updateCallbackDone = Promise.resolve().then(callback);
    const finished = new Promise(resolve => { finishers.push(resolve); });
    return { finished: updateCallbackDone.then(() => finished).then(() => {
      if (rejectFinished) throw new Error('transition cancelled');
    }) };
  };
  return {
    doc, events,
    update() { events.push('update'); view = view === 'landing' ? 'section' : 'landing'; },
    finish(i) { for (const resolve of i === undefined ? finishers : [finishers[i]]) resolve(); },
  };
};

test('forward names tile before update and page afterward, then clears', async () => {
  const fake = fakeDocument('landing');
  const pending = morph.run(fake.doc, morph.route('#/'), morph.route('#/sport'), () => fake.update());
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(fake.events.filter(e => e.includes(':section-') || e === 'transition' || e === 'update'), [
    'tile:section-panel', 'tile.name:section-title', 'transition', 'update',
    'page:section-panel', 'page.h2:section-title',
  ]);
  assert.ok(fake.events.indexOf('lookup:.tile[data-section="sport"]') < fake.events.indexOf('transition'));
  assert.ok(fake.events.indexOf('lookup:.section-page[data-section="sport"]') > fake.events.indexOf('update'));
  fake.finish();
  await pending;
  assert.deepEqual(fake.events.slice(-2), ['page:', 'page.h2:']);
});

test('back names page before update and tile afterward, then clears on rejected finish', async () => {
  const fake = fakeDocument('section', { rejectFinished: true });
  const pending = morph.run(fake.doc, morph.route('#/sport'), morph.route('#/'), () => fake.update());
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(fake.events.filter(e => e.includes(':section-') || e === 'transition' || e === 'update'), [
    'page:section-panel', 'page.h2:section-title', 'transition', 'update',
    'tile:section-panel', 'tile.name:section-title',
  ]);
  fake.finish();
  await pending;
  assert.deepEqual(fake.events.slice(-2), ['tile:', 'tile.name:']);
});

test('missing elements are skipped and non-morph navigation updates once', async () => {
  const missing = fakeDocument('landing', { missing: true });
  const pending = morph.run(missing.doc, morph.route('#/'), morph.route('#/sport'), () => missing.update());
  await Promise.resolve();
  missing.finish();
  await pending;
  assert.equal(missing.events.filter(e => e === 'update').length, 1);

  for (const options of [{ hasApi: false }, { reducedMotion: true }]) {
    const fake = fakeDocument('landing', options);
    await morph.run(fake.doc, morph.route('#/'), morph.route('#/sport'), () => fake.update());
    assert.deepEqual(fake.events, ['update']);
  }
});

test('a throwing startViewTransition falls back to a plain update', async () => {
  const fake = fakeDocument('landing');
  fake.doc.startViewTransition = () => { throw new Error('busy'); };
  await morph.run(fake.doc, morph.route('#/'), morph.route('#/sport'), () => fake.update());
  assert.deepEqual(fake.events.filter(e => !e.startsWith('lookup:') && !e.startsWith('child:')), [
    'tile:section-panel', 'tile.name:section-title', 'tile:', 'tile.name:', 'update',
  ]);
});

test('a stale run does not clear names set by a newer run', async () => {
  const fake = fakeDocument('landing');
  const forward = morph.run(fake.doc, morph.route('#/'), morph.route('#/sport'), () => fake.update());
  await Promise.resolve();
  await Promise.resolve();
  const mark = fake.events.length;
  const back = morph.run(fake.doc, morph.route('#/sport'), morph.route('#/'), () => fake.update());
  fake.finish(0);
  await forward;
  fake.finish(1);
  await back;
  const after = fake.events.slice(mark);
  // Only the back run's own callback clears the page; the forward run's late clear skips it.
  assert.equal(after.filter(e => e === 'page:').length, 1);
  assert.equal(after.filter(e => e === 'page.h2:').length, 1);
  assert.deepEqual(after.slice(-2), ['tile:', 'tile.name:']);
});

const renderAt = async hash => {
  const edition = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/contracts/healthy-edition.json'), 'utf8'));
  const app = { _html: '', addEventListener() {}, querySelector() { return null; }, set innerHTML(html) { this._html = html; }, get innerHTML() { return this._html; } };
  const location = { hash, href: `https://example.test/${hash}`, search: '' };
  const context = {
    document: { getElementById: () => app, querySelector: () => null, documentElement: { dataset: {} }, addEventListener() {}, visibilityState: 'hidden' },
    window: { addEventListener() {}, scrollTo() {} }, location,
    matchMedia: () => ({ matches: false }),
    fetch: async () => ({ ok: true, json: async () => edition }),
    AbortSignal: { timeout: () => new AbortController().signal }, AbortController,
    setInterval() {}, setTimeout, clearTimeout, URL, URLSearchParams, Date, Intl, console,
  };
  vm.createContext(context);
  for (const file of ['freshness.js', 'storage.js', 'refresh.js', 'morph.js', 'press.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../site', file), 'utf8'), context);
  }
  await new Promise(resolve => setTimeout(resolve, 0));
  return app.innerHTML;
};

test('rendered landing tiles and section page have the morph anchors', async () => {
  const landing = await renderAt('#/');
  for (const section of ['news', 'sport', 'finance', 'media']) {
    assert.match(landing, new RegExp(`<a class="tile" data-section="${section}"`));
  }
  const sport = await renderAt('#/sport');
  assert.match(sport, /<div class="section-page" data-section="sport">[\s\S]*?<h2>Sport<\/h2>/);
  assert.match(sport, /<footer>[\s\S]*?<\/footer>\s*<\/div>$/);
});
