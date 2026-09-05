/* Pure schedule calculations, shared by the page and the regression tests. */
var BriefingTime = (() => {
  const parts = (date, timeZone) => Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
  }).formatToParts(date).filter(p => p.type !== 'literal').map(p => [p.type, Number(p.value)]));
  const stamp = p => Date.UTC(p.year, p.month - 1, p.day, p.hour || 0, p.minute || 0, p.second || 0);
  const localSlot = (day, hour, minute, timeZone) => {
    const target = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), hour, minute);
    let guess = target;
    for (let i = 0; i < 3; i++) guess += target - stamp(parts(new Date(guess), timeZone));
    return guess;
  };
  function status(briefing, now = new Date()) {
    const schedule = briefing.schedule || { hours: [7, 13, 19], minute: 0, timezone: briefing.timezone || 'Europe/Rome', grace_minutes: 45 };
    const p = parts(now, schedule.timezone);
    const slots = [];
    for (const delta of [-1, 0, 1]) {
      const day = new Date(Date.UTC(p.year, p.month - 1, p.day + delta));
      for (const h of schedule.hours) slots.push(localSlot(day, h, schedule.minute || 0, schedule.timezone));
    }
    slots.sort((a, b) => a - b);
    const latest = slots.filter(t => t <= now.getTime()).pop();
    const next = slots.find(t => t > now.getTime());
    const missing = Date.parse(briefing.generated_at) < latest;
    const overdue = missing && now.getTime() - latest >= (schedule.grace_minutes ?? 45) * 60000;
    return { next, due: latest, overdue, pending: missing && !overdue, timezone: schedule.timezone };
  }
  return { status };
})();
if (typeof module !== 'undefined') module.exports = BriefingTime;
