// File-upload tile behaviour shared by Arnaud's sites, upload-tile v1.0. Synced from
// website_modules by sync.py: never edit a site's copy. Load it once per page with `defer`; it
// wires up every [data-upload-zone] on the page and does nothing when there is none. The rules it
// keeps are UT1-UT12 in website_modules/REQUIREMENTS.md.
//
// The markup contract (each site keeps its own markup and CSS, and only adds these attributes).
// The page must already work without this script: a real file input and a real submit button.
//
//   <div data-upload-zone>                                 the drop target: the tile alone, or a
//                                                          whole grid around it (it may also be
//                                                          the <form> itself)
//     <form method="post" enctype="multipart/form-data" action="...">
//       <label for="logo">                                 the tile: opens the picker, no JS needed
//         <span>Add logo</span>
//         <span data-upload-hint hidden>or drop an image here</span>   shown only once a drop
//       </label>                                                       can actually work
//       <input type="file" id="logo" name="logo"           visually hidden, never display:none
//              accept="image/png,image/jpeg,.png,.jpg"     optional: checked before sending
//              data-max-bytes="10485760"                   optional: per-file size cap
//              data-type-error="..." data-size-error="..." optional wording, see MESSAGES
//              data-count-error="...">
//       <button type="submit" data-upload-fallback>Upload</button>   hidden once enhanced
//     </form>
//     <p data-upload-status role="alert" hidden></p>       optional: where refusals are written
//   </div>
//
// The status element may also live elsewhere on the page (say, at the top of the section, where
// the site writes its other messages): name it from the zone with data-upload-status="its-id".
// The zone's form is the one holding its file input, so a grid can hold other forms (a delete
// button on each card) before or after the tile's.
//
// What it does, identically on every site:
//   - marks each zone `is-enhanced`, hides its [data-upload-fallback] buttons, and unhides its
//     [data-upload-hint] lines, except on touch-only screens (hover: none), where nothing can be
//     dragged;
//   - submits the zone's form as soon as files are chosen in the picker or dropped on the zone,
//     with requestSubmit() (so submit listeners, e.g. a stay-in-place script, still run; a browser
//     without it gets no enhancement at all, and the plain form keeps working), marking
//     the zone `is-uploading` and aria-busy, and ignoring further files until the page reloads;
//   - while a drag carrying files is over the zone, marks it `is-drop-target` (sites make that
//     state louder, never fainter); clears it only when the cursor really leaves the zone, not
//     when it crosses a child; ignores drags that carry no files (an image or text being moved
//     around the page);
//   - swallows a file dropped anywhere else on a page that has a zone, so the browser never
//     replaces the page with the dropped file;
//   - when the zone has a status element (inside it, or named by id), checks each file first against the input's
//     `accept` and `data-max-bytes`, and refuses more than one file when the input isn't
//     `multiple`. A refusal names the file, sends nothing, and empties the input. Without a status
//     element it checks nothing and lets the server decide (it always decides anyway: nothing
//     here is trusted).
//
// The server still owns the rules and the result message; this only saves a round trip.
(function () {
  var MESSAGES = {
    type: '{name} isn’t a file type that can be uploaded here.',
    size: '{name} is too large. The limit is {max} per file.',
    count: 'Drop one file at a time.'
  };
  var zones = document.querySelectorAll('[data-upload-zone]');
  if (!zones.length) return;

  var canHover = !(window.matchMedia && window.matchMedia('(hover: none)').matches);

  function carriesFiles(event) {
    var types = event.dataTransfer && event.dataTransfer.types;
    return !!types && Array.prototype.indexOf.call(types, 'Files') !== -1;
  }

  function formatBytes(bytes) {
    if (bytes >= 1048576) return Math.round(bytes / 104857.6) / 10 + ' MB';
    if (bytes >= 1024) return Math.round(bytes / 1024) + ' KB';
    return bytes + ' bytes';
  }

  // `accept` is a comma list of extensions (.png), MIME types (image/png) and wildcards (image/*).
  // A file passes on any match. Some systems report no type at all (a .jpg on some Windows
  // setups), so the extension alone is enough.
  function accepted(file, accept) {
    if (!accept) return true;
    var name = (file.name || '').toLowerCase();
    var type = (file.type || '').toLowerCase();
    return accept.split(',').some(function (raw) {
      var rule = raw.trim().toLowerCase();
      if (!rule) return false;
      if (rule.charAt(0) === '.') return name.slice(-rule.length) === rule;
      if (rule.slice(-2) === '/*') return type.indexOf(rule.slice(0, -1)) === 0;
      return type === rule;
    });
  }

  function wire(zone) {
    var input = zone.querySelector('input[type=file]');
    var form = input && input.form;
    // Without requestSubmit (Safari before 16) the zone is left as the plain form it already is,
    // Upload button and all: submitting any other way would skip the site's submit listeners.
    if (!form || typeof form.requestSubmit !== 'function') return;
    var named = zone.getAttribute('data-upload-status');
    var status = (named && document.getElementById(named)) || zone.querySelector('[data-upload-status]');
    var sending = false;

    zone.classList.add('is-enhanced');
    zone.querySelectorAll('[data-upload-fallback]').forEach(function (el) { el.hidden = true; });
    if (canHover) {
      zone.querySelectorAll('[data-upload-hint]').forEach(function (el) { el.hidden = false; });
    }

    function say(message) {
      if (!status) return;
      status.textContent = message || '';
      status.hidden = !message;
    }

    function message(kind, file) {
      var text = input.getAttribute('data-' + kind + '-error') || MESSAGES[kind];
      var max = parseInt(input.getAttribute('data-max-bytes'), 10);
      return text.replace('{name}', file ? file.name : '').replace('{max}', formatBytes(max || 0));
    }

    // The courtesy checks run only where the site gave refusals somewhere to appear.
    function refusal(files) {
      if (!status) return '';
      if (files.length > 1 && !input.multiple) return message('count');
      var max = parseInt(input.getAttribute('data-max-bytes'), 10);
      for (var i = 0; i < files.length; i++) {
        if (!accepted(files[i], input.getAttribute('accept'))) return message('type', files[i]);
        if (max && files[i].size > max) return message('size', files[i]);
      }
      return '';
    }

    function send(files) {
      if (sending || !files.length) return;
      var problem = refusal(files);
      if (problem) {
        say(problem);
        input.value = '';
        return;
      }
      say('');
      sending = true;
      zone.classList.add('is-uploading');
      zone.setAttribute('aria-busy', 'true');
      form.requestSubmit();
    }

    input.addEventListener('change', function () { send(input.files); });

    ['dragenter', 'dragover'].forEach(function (type) {
      zone.addEventListener(type, function (event) {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        zone.classList.add('is-drop-target');
      });
    });
    zone.addEventListener('dragleave', function (event) {
      if (!zone.contains(event.relatedTarget)) zone.classList.remove('is-drop-target');
    });
    zone.addEventListener('drop', function (event) {
      zone.classList.remove('is-drop-target');
      if (!carriesFiles(event)) return;
      event.preventDefault();
      event.stopPropagation();
      var files = event.dataTransfer.files;
      if (!files.length || sending) return;
      // Through the input, so the form posts the files under the input's own name exactly as if
      // they had been picked.
      if (!refusal(files)) input.files = files;
      send(files);
    });
  }

  zones.forEach(wire);

  ['dragover', 'drop'].forEach(function (type) {
    document.addEventListener(type, function (event) {
      var target = event.target;
      var inZone = target && target.closest && target.closest('[data-upload-zone]');
      if (carriesFiles(event) && !inZone) event.preventDefault();
    });
  });
})();
