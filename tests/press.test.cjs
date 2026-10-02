const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const press = require('../site/press.js');

const fakeDocument = () => {
  const listeners = new Map();
  return {
    listeners,
    doc: {
      addEventListener(type, listener) {
        const registered = listeners.get(type) || [];
        registered.push(listener);
        listeners.set(type, registered);
      },
    },
    dispatch(type, properties = {}) {
      for (const listener of listeners.get(type) || []) listener(properties);
    },
  };
};

const element = (kind = '', parent = null) => {
  const classes = new Set(kind === 'tile' || kind === 'card' ? [kind] : []);
  const node = {
    parent,
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    closest(selector) {
      assert.equal(selector, '.tile, .card, .past a');
      for (let current = this; current; current = current.parent) {
        if (current.classList.contains('tile') || current.classList.contains('card') || current.kind === 'past-link') return current;
      }
      return null;
    },
    contains(other) {
      for (let current = other; current; current = current.parent) {
        if (current === this) return true;
      }
      return false;
    },
    kind,
  };
  return node;
};

const down = (fake, target, overrides = {}) => fake.dispatch('pointerdown', {
  target, isPrimary: true, button: 0, ...overrides,
});

test('a child press releases on pointerup, pointercancel, and dragstart', () => {
  const fake = fakeDocument();
  press.attach(fake.doc);
  const card = element('card');
  const child = element('', card);
  for (const release of ['pointerup', 'pointercancel', 'dragstart']) {
    down(fake, child);
    assert.equal(card.classList.contains('pressed'), true);
    fake.dispatch(release);
    assert.equal(card.classList.contains('pressed'), false);
  }
});

test('pointerout keeps a press within the card and releases it outside', () => {
  const fake = fakeDocument();
  press.attach(fake.doc);
  const card = element('card');
  const first = element('', card);
  const second = element('', card);
  down(fake, first);
  fake.dispatch('pointerout', { target: first, relatedTarget: second });
  assert.equal(card.classList.contains('pressed'), true);
  fake.dispatch('pointerout', { target: second, relatedTarget: element() });
  assert.equal(card.classList.contains('pressed'), false);
  down(fake, card);
  fake.dispatch('pointerout', { target: card, relatedTarget: null });
  assert.equal(card.classList.contains('pressed'), false);
});

test('a new card releases the previous card, including tiles and past links', () => {
  const fake = fakeDocument();
  press.attach(fake.doc);
  const card = element('card');
  const tile = element('tile');
  const pastLink = element('past-link');
  down(fake, card);
  down(fake, tile);
  assert.equal(card.classList.contains('pressed'), false);
  assert.equal(tile.classList.contains('pressed'), true);
  down(fake, pastLink);
  assert.equal(tile.classList.contains('pressed'), false);
  assert.equal(pastLink.classList.contains('pressed'), true);
});

test('other pointers and targets cannot start a press', () => {
  const fake = fakeDocument();
  press.attach(fake.doc);
  const card = element('card');
  down(fake, card, { isPrimary: false });
  down(fake, card, { button: 1 });
  assert.equal(card.classList.contains('pressed'), false);
  down(fake, element());
  down(fake, {});
  down(fake, null);
  assert.equal(card.classList.contains('pressed'), false);
});

test('attach registers one delegated listener of each type per document', () => {
  const fake = fakeDocument();
  press.attach(fake.doc);
  press.attach(fake.doc);
  for (const type of ['pointerdown', 'pointerup', 'pointercancel', 'dragstart', 'pointerout']) {
    assert.equal(fake.listeners.get(type)?.length, 1, type);
  }
});

test('page includes press styles, reduced motion, and ordered scripts', () => {
  const html = fs.readFileSync(path.join(__dirname, '../site/index.html'), 'utf8');
  assert.match(html, /\.tile, \.card, \.past a\s*\{[^}]*transition: transform \.35s cubic-bezier\(\.34, 1\.56, \.64, 1\)/);
  assert.match(html, /\.pressed\s*\{[^}]*transform: scale\(\.97\)/);
  assert.match(html, /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.pressed\s*\{[^}]*transform: none;[^}]*opacity: \.8;[^}]*transition: none;[^}]*\}\s*\.card\.seen\.pressed\s*\{[^}]*opacity: \.5/);
  assert.ok(html.indexOf('<script src="press.js"></script>') < html.indexOf('<script src="app.js"></script>'));
  assert.doesNotMatch(html, /\.tile:active\s*\{/);
});
