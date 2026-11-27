/*
 * The two jobs inline on*= attributes used to do, done from a file instead,
 * because the Content-Security-Policy runs no inline handler (REQUIREMENTS
 * CO4r). Loaded in the <head> of base.html and errors/_bare.html; both
 * listen on the document, so a form or button added later is covered.
 *
 *   <form method="post" data-confirm="Delete this?">  asks before posting
 *   <button type="button" data-action="back">          goes back a page
 *
 * The confirm listens in the *capture* phase, so a cancelled post is
 * stopped before any other submit listener sees it — stay-in-place.js
 * already ignores a submit whose default was prevented.
 */
(function () {
  document.addEventListener('submit', function (event) {
    var form = event.target;
    var message = form && form.getAttribute && form.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  document.addEventListener('click', function (event) {
    var trigger = event.target.closest && event.target.closest('[data-action="back"]');
    if (trigger) {
      event.preventDefault();
      window.history.back();
    }
  });
})();
