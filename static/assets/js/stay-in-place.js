/*
 * Saving keeps your place — the app-wide default (REQUIREMENTS.md MOD7,
 * CLAUDE.md hard rule 17).
 *
 * Every form that posts, on every page: when the page that comes back is
 * the same page, it opens scrolled to where you were instead of at the top,
 * so a long page like Settings doesn't make you find your section again
 * after each save. If that page shows a message from the save — any element
 * marked [data-save-notice] — it scrolls to the message instead, so a
 * refusal is never left off-screen.
 *
 * Nothing to wire per form or per route. A form opts out with
 * data-no-stay; a response that names its own place with a #fragment
 * keeps it. A form submitted from script must use requestSubmit(), not
 * submit(): only the former fires the submit event this listens for.
 */
(function () {
  'use strict';

  var KEY = 'stay-in-place';
  // Long enough for a slow save; short enough that a stale entry can't
  // move a page opened later.
  var MAX_AGE_MS = 20000;

  document.addEventListener('submit', function (event) {
    var form = event.target;
    // Bubbling, so a handler that cancelled the submit has already run.
    if (event.defaultPrevented) return;
    if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post') return;
    if (form.target && form.target !== '_self') return;
    if (form.hasAttribute('data-no-stay')) return;
    try {
      sessionStorage.setItem(KEY, JSON.stringify({
        path: location.pathname, y: window.scrollY, at: Date.now(),
      }));
    } catch (e) { /* private mode or storage off: the page just opens at the top */ }
  });

  var saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(KEY));
    sessionStorage.removeItem(KEY);
  } catch (e) { return; }
  if (!saved || saved.path !== location.pathname) return;
  if (Date.now() - saved.at > MAX_AGE_MS) return;
  if (location.hash) return;

  function settle() {
    var notice = document.querySelector('[data-save-notice]');
    if (notice) notice.scrollIntoView({ block: 'center' });
    else window.scrollTo(0, saved.y);
  }

  settle();
  // Images and fonts can still move things once they arrive; settle again
  // then, unless the person has started scrolling in the meantime.
  var moved = false;
  window.addEventListener('wheel', function () { moved = true; }, { once: true, passive: true });
  window.addEventListener('touchmove', function () { moved = true; }, { once: true, passive: true });
  window.addEventListener('keydown', function () { moved = true; }, { once: true });
  window.addEventListener('load', function () { if (!moved) settle(); });
})();
