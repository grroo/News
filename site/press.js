/* Delegated press feedback for cards (shared by page and tests). */
var BriefingPress = (() => {
  'use strict';

  const SELECTOR = '.tile, .card, .past a';
  const attached = new WeakSet();

  const attach = doc => {
    if (attached.has(doc)) return;
    attached.add(doc);

    let pressed = null;
    const release = () => {
      pressed?.classList.remove('pressed');
      pressed = null;
    };

    doc.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0) return;
      const card = event.target?.closest?.(SELECTOR);
      if (!card) return;
      release();
      card.classList.add('pressed');
      pressed = card;
    });
    for (const type of ['pointerup', 'pointercancel', 'dragstart']) {
      doc.addEventListener(type, release);
    }
    doc.addEventListener('pointerout', event => {
      if (pressed && (!event.relatedTarget || !pressed.contains(event.relatedTarget))) release();
    });
  };

  return { attach };
})();

if (typeof module !== 'undefined') module.exports = BriefingPress;
