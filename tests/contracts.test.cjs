const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const FIXTURES = path.join(ROOT, 'tests/fixtures/contracts');

function load(name) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES, name), 'utf8'));
}

function fresh(edition, slotIso, nowMs) {
  const generated = Date.parse(edition.generated_at);
  const slot = Date.parse(slotIso);
  return edition.mode === 'llm' && slot <= generated && generated <= nowMs + 60000
    && (!edition.quality || edition.quality.overall === 'healthy');
}

test('contract fixtures load in Node', () => {
  for (const file of fs.readdirSync(FIXTURES).filter(f => f.endsWith('.json'))) {
    assert.doesNotThrow(() => load(file), file);
  }
});

test('healthy edition satisfies worker publication rule', () => {
  const edition = load('healthy-edition.json');
  assert.equal(fresh(edition, edition.scheduled_slot, Date.parse('2026-09-23T11:30:00Z')), true);
});

test('degraded and failed editions do not satisfy healthy slot', () => {
  for (const name of ['degraded-edition.json', 'provider-failure-edition.json']) {
    const edition = load(name);
    assert.equal(fresh(edition, edition.scheduled_slot, Date.parse('2026-09-23T17:30:00Z')), false, name);
  }
});

test('no-change result keeps generation age while recording check time', () => {
  const edition = load('no-change-result.json');
  assert.equal(edition.quality.overall, 'unchanged');
  assert.ok(Date.parse(edition.checked_at) > Date.parse(edition.generated_at));
  assert.ok(edition.request_ids.length > 1);
});

test('refresh lifecycle includes reservation before external dispatch', () => {
  const lifecycle = load('refresh-job-lifecycle.json');
  const building = lifecycle.transitions.find(t => t.status === 'building');
  assert.ok(building.reservation || lifecycle.transitions.find(t => t.reservation));
  assert.equal(lifecycle.duplicate_request.response.job_id, lifecycle.transitions[0].response.job_id);
});
