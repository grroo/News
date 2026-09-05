const test = require('node:test');
const assert = require('node:assert/strict');
const {status} = require('../site/freshness.js');
const b = generated_at => ({generated_at, schedule:{hours:[7,13,19], minute:0, timezone:'Europe/Rome', grace_minutes:45}});
test('summer: next update is Rome time, independent of device zone', () => {
 const s=status(b('2026-09-05T05:10:00Z'),new Date('2026-09-05T06:00:00Z'));
 assert.equal(new Date(s.next).toISOString(),'2026-09-05T11:00:00.000Z');
 assert.equal(s.overdue,false);
});
test('winter uses UTC+1',()=>{
 const s=status(b('2026-01-05T06:10:00Z'),new Date('2026-01-05T07:00:00Z'));
 assert.equal(new Date(s.next).toISOString(),'2026-01-05T12:00:00.000Z');
});
test('short delay is pending, then overdue; a late successful build clears it',()=>{
 const old=b('2026-09-05T05:10:00Z');
 assert.equal(status(old,new Date('2026-09-05T11:20:00Z')).pending,true);
 assert.equal(status(old,new Date('2026-09-05T11:45:00Z')).overdue,true);
 assert.equal(status(b('2026-09-05T13:25:00Z'),new Date('2026-09-05T13:30:00Z')).overdue,false);
});
test('overnight next update crosses spring DST change',()=>{
 const s=status(b('2026-03-28T18:10:00Z'),new Date('2026-03-28T22:00:00Z'));
 assert.equal(new Date(s.next).toISOString(),'2026-03-29T05:00:00.000Z');
});
test('overnight next update crosses autumn DST change',()=>{
 const s=status(b('2026-10-24T17:10:00Z'),new Date('2026-10-24T22:00:00Z'));
 assert.equal(new Date(s.next).toISOString(),'2026-10-25T06:00:00.000Z');
});
test('old editions without schedule metadata still display correctly',()=>{
 assert.equal(status({generated_at:'2026-09-05T05:10:00Z'},new Date('2026-09-05T11:50:00Z')).overdue,true);
});
