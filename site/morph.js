/* Same-document tile to section transitions (shared by page and tests). */
var BriefingMorph = (() => {
  'use strict';

  const SECTIONS = new Set(['news', 'sport', 'finance', 'media']);

  const route = hash => {
    const parts = String(hash || '').replace(/^#\/?/, '').split('/').filter(Boolean);
    let past = null;
    if (parts[0] === 'past') {
      if (parts.length < 2) return { kind: 'other', section: null, past: null };
      try { past = decodeURIComponent(parts[1]); } catch { return { kind: 'other', section: null, past: null }; }
      parts.splice(0, 2);
    }
    if (!parts.length) return { kind: 'landing', section: null, past };
    if (parts.length === 1 && SECTIONS.has(parts[0])) {
      return { kind: 'section', section: parts[0], past };
    }
    return { kind: 'other', section: null, past };
  };

  const shouldMorph = (from, to, { hasApi, reducedMotion }) =>
    !!hasApi && !reducedMotion && from.past === to.past &&
    ((from.kind === 'landing' && to.kind === 'section') ||
      (from.kind === 'section' && to.kind === 'landing'));

  const run = async (doc, from, to, update) => {
    const media = doc.defaultView?.matchMedia || (typeof matchMedia === 'function' ? matchMedia : null);
    const reducedMotion = media ? media.call(doc.defaultView, '(prefers-reduced-motion: reduce)').matches : false;
    if (!shouldMorph(from, to, {
      hasApi: typeof doc.startViewTransition === 'function', reducedMotion,
    })) return update();

    const section = from.kind === 'section' ? from.section : to.section;
    const forward = from.kind === 'landing';
    const tile = () => doc.querySelector(`.tile[data-section="${section}"]`);
    const page = () => doc.querySelector(`.section-page[data-section="${section}"]`);
    const named = new Set();
    const setName = (element, name) => {
      if (!element) return;
      element.style.viewTransitionName = name;
      named.add(element);
    };
    const clear = () => {
      for (const element of named) element.style.viewTransitionName = '';
      named.clear();
    };
    const nameTile = () => {
      const element = tile();
      setName(element, 'section-panel');
      setName(element?.querySelector('.name'), 'section-title');
    };
    const namePage = () => {
      const element = page();
      setName(element, 'section-panel');
      setName(element?.querySelector('h2'), 'section-title');
    };

    if (forward) nameTile();
    else namePage();
    let transition;
    try {
      transition = doc.startViewTransition(async () => {
        clear();
        await update();
        if (forward) namePage();
        else nameTile();
      });
    } catch (error) {
      clear();
      throw error;
    }
    try { await transition.finished; } catch { /* A newer navigation can cancel this transition. */ }
    finally { clear(); }
  };

  return { route, shouldMorph, run };
})();

if (typeof module !== 'undefined') module.exports = BriefingMorph;
