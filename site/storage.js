/* Safe localStorage access with in-memory fallback when storage is denied. */
var BriefingStorage = (() => {
  'use strict';
  const memory = new Map();
  let denied = false;

  const probe = () => {
    if (denied) return false;
    try {
      const key = '__briefing_probe__';
      localStorage.setItem(key, '1');
      localStorage.removeItem(key);
      return true;
    } catch {
      denied = true;
      return false;
    }
  };

  const canUse = () => !denied && probe();

  const getItem = key => {
    if (canUse()) {
      try { return localStorage.getItem(key); } catch { denied = true; }
    }
    return memory.has(key) ? memory.get(key) : null;
  };

  const setItem = (key, value) => {
    if (canUse()) {
      try { localStorage.setItem(key, value); return true; } catch { denied = true; }
    }
    memory.set(key, String(value));
    return false;
  };

  const removeItem = key => {
    if (canUse()) {
      try { localStorage.removeItem(key); } catch { denied = true; }
    }
    memory.delete(key);
  };

  const readJSON = (key, fallback) => {
    const raw = getItem(key);
    if (raw == null) return fallback;
    try {
      const parsed = JSON.parse(raw);
      return parsed ?? fallback;
    } catch {
      return fallback;
    }
  };

  const writeJSON = (key, value) => setItem(key, JSON.stringify(value));

  return { canUse, getItem, setItem, removeItem, readJSON, writeJSON, isDenied: () => denied };
})();

if (typeof module !== 'undefined') module.exports = BriefingStorage;
