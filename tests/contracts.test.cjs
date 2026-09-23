const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Ajv2020 = require('ajv/dist/2020').default;
const addFormats = require('ajv-formats');

const ROOT = path.resolve(__dirname, '..');
const FIXTURES = path.join(ROOT, 'tests/fixtures/contracts');
const SCHEMAS = path.join(ROOT, 'docs/contracts/schemas');
const load = name => JSON.parse(fs.readFileSync(path.join(FIXTURES, name), 'utf8'));
const schemaDocs = Object.fromEntries(
  fs.readdirSync(SCHEMAS).filter(file => file.endsWith('.json'))
    .map(file => [path.basename(file, '.json'), JSON.parse(fs.readFileSync(path.join(SCHEMAS, file), 'utf8'))])
);
const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
for (const schema of Object.values(schemaDocs)) ajv.addSchema(schema);

function validator(name, entrypoint) {
  if (!entrypoint) return ajv.getSchema(schemaDocs[name].$id);
  const doc = schemaDocs[name];
  return ajv.compile({ $schema: doc.$schema, $defs: doc.$defs, $ref: `#/$defs/${entrypoint}` });
}

function materialize(c) {
  const value = structuredClone(c.fixture ? load(c.fixture) : c.instance);
  function target(pointer) {
    const parts = pointer.slice(1).split('/');
    let parent = value;
    for (const part of parts.slice(0, -1)) parent = parent[part];
    return [parent, parts.at(-1)];
  }
  for (const [pointer, replacement] of Object.entries(c.set || {})) {
    const [parent, key] = target(pointer);
    parent[key] = replacement;
  }
  for (const pointer of c.delete || []) {
    const [parent, key] = target(pointer);
    delete parent[key];
  }
  return value;
}

function healthySlot(edition, slotIso, nowMs, requestId) {
  // Reference contract rule only. T01/T05 must test production enforcement separately.
  if (!validator('edition')(edition) || edition.schema_version !== 2) return false;
  if (edition.mode !== 'llm' || edition.quality.overall !== 'healthy') return false;
  if (requestId && !edition.request_ids.includes(requestId)) return false;
  const slot = Date.parse(slotIso);
  const source = Date.parse(edition.source_checked_at);
  const done = Date.parse(edition.refresh.completed_at);
  const generated = Date.parse(edition.generated_at);
  if (Date.parse(edition.scheduled_slot) !== slot) return false;
  if (![slot, source, done, generated].every(Number.isFinite)) return false;
  if (!(slot <= source && source <= done && done <= nowMs + 60000)) return false;
  if (generated > nowMs + 60000) return false;
  if (edition.refresh.outcome === 'generated' && generated < slot) return false;
  if (edition.refresh.outcome === 'no_change' && edition.refresh.reused_edition_id !== edition.edition_id) return false;
  for (const name of ['news', 'sport', 'finance']) {
    const section = edition.sections[name];
    if (!['healthy', 'unchanged'].includes(section.state) || Object.hasOwn(section, 'error')) return false;
    if (!Array.isArray(section.briefing) || !Array.isArray(section.items)) return false;
    const succeeded = Date.parse(section.last_success_at);
    if (!Number.isFinite(succeeded) || succeeded > done + 60000) return false;
    if (section.state === 'healthy' && succeeded < slot) return false;
  }
  const media = edition.sections.media;
  return ['healthy', 'unchanged', 'skipped'].includes(media.state) && !Object.hasOwn(media, 'error');
}

function claimDecision(reservation, claim, now) {
  // Reference result. Atomic state transitions and bearer validation belong in T05.
  if (!validator('reservation', 'reservation')(reservation)) return ['denied', 'unknown'];
  if (!validator('reservation', 'claimRequest')(claim)) return ['denied', 'scope_mismatch'];
  if (reservation.reservation_id !== claim.reservation_id) return ['denied', 'unknown'];
  if (reservation.request_id !== claim.request_id) return ['denied', 'request_mismatch'];
  for (const key of ['repository', 'workflow', 'ref']) {
    if (reservation.scope[key] !== claim[key]) return ['denied', 'scope_mismatch'];
  }
  if (reservation.status === 'cancelled') return ['denied', 'cancelled'];
  if (reservation.status === 'claimed') {
    return reservation.claimed_run_id === claim.run_id && reservation.claimed_run_attempt === claim.run_attempt
      ? ['allowed', null] : ['denied', 'claimed_by_other'];
  }
  if (reservation.status !== 'reserved' || Date.parse(now) >= Date.parse(reservation.expires_at)) {
    return ['denied', 'expired'];
  }
  return ['allowed', null];
}

test('all schemas compile and all fixtures parse', () => {
  for (const [name, doc] of Object.entries(schemaDocs)) {
    assert.equal(ajv.validateSchema(doc), true, name);
  }
  for (const file of fs.readdirSync(FIXTURES).filter(file => file.endsWith('.json'))) {
    assert.doesNotThrow(() => load(file), file);
  }
});

test('shared positive and negative schema cases', () => {
  for (const c of load('schema-cases.json').cases) {
    assert.equal(Boolean(validator(c.schema, c.entrypoint)(materialize(c))), c.valid, c.name);
  }
});

test('shared slot decision cases', () => {
  for (const c of load('slot-cases.json').cases) {
    assert.equal(healthySlot(materialize(c), c.slot, Date.parse(c.now), c.request_id), c.expected, c.name);
  }
});

test('shared reservation claim cases', () => {
  for (const c of load('reservation-cases.json').cases) {
    assert.deepEqual(claimDecision(c.reservation, c.claim, c.now), [c.expected_decision, c.expected_reason || null], c.name);
  }
});

test('lifecycle records a reservation before dispatch', () => {
  const lifecycle = load('refresh-job-lifecycle.json');
  const queued = lifecycle.transitions.find(step => step.status === 'queued');
  const building = lifecycle.transitions.find(step => step.status === 'building');
  assert.ok(Date.parse(queued.at) < Date.parse(building.at));
  assert.equal(Boolean(validator('reservation', 'reservation')(queued.reservation)), true);
  assert.equal(lifecycle.duplicate_request.response.job_id, lifecycle.transitions[0].response.job_id);
});
