/*
 * The script-side twin of save_notice() in templates/_save_notice.html
 * (REQUIREMENTS.md MOD8a), for a result that arrives without a page load:
 * a reply drafted, a render refused. Same markup as the macro, so the same
 * green and red boxes and the same roles: a refusal is role="alert", a
 * success role="status", and both carry data-save-notice.
 *
 *   saveNotice(slot, message, 'error' | 'success')   // replaces the slot's notice
 *   saveNotice(slot, '')                             // clears it
 *
 * The slot is an empty element placed where the message belongs: next to
 * the button that was pressed, never at the top of the page. Text only;
 * the message is never parsed as HTML.
 */
(function () {
  'use strict';

  window.saveNotice = function (slot, message, category) {
    if (!slot) return null;
    slot.textContent = '';
    if (!message) return null;

    var refused = category === 'error';
    var notice = document.createElement('p');
    notice.setAttribute('data-save-notice', '');
    notice.className = 'save-notice save-notice--' + (refused ? 'error' : 'success');
    notice.setAttribute('role', refused ? 'alert' : 'status');
    notice.textContent = message;
    slot.appendChild(notice);
    if (notice.scrollIntoView) notice.scrollIntoView({ block: 'nearest' });
    return notice;
  };

  /*
   * While a request runs, the button that started it says so, in place of
   * a separate grey "Working…" line: "Drafting…" on Suggest response,
   * "Rendering…" on Render image. Disabled so it can't be pressed twice,
   * and aria-busy so it looks it (style.css). Returns the function that
   * puts the label back.
   */
  window.busyButton = function (button, label) {
    var original = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = label;
    return function () {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = original;
    };
  };
})();
