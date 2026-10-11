// File-upload tile behaviour shared by Arnaud's sites, upload-tile v1.1. Synced from
// website_modules by sync.py: never edit a site's copy. Load it once per page with `defer`; it
// wires up every [data-upload-zone] on the page and does nothing when there is none. The rules it
// keeps are UT1-UT14 in website_modules/REQUIREMENTS.md.
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
// A waiting zone (UT13-UT14) sits inside a bigger form, and keeps the files until that form is
// sent by its own buttons, instead of uploading them at once. The site adds:
//
//   <div data-upload-zone data-upload-wait>             inside the form, not around a form
//     <label for="photos">Add photos <span data-upload-hint hidden>or drop them here</span></label>
//     <input type="file" id="photos" name="photos" multiple
//            data-max-files="10" data-many-error="...">  optional: a cap on the whole selection
//     <ul data-upload-list></ul>                          where the chosen files are listed
//     <template data-upload-item>                         the site's markup for one chosen file
//       <li><img data-upload-preview alt="">              filled for an image file, else removed
//           <span data-upload-name></span>                the file's name
//           <button type="button" data-upload-remove>Remove</button></li>   takes it back out
//     </template>
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
//   - in a waiting zone, instead, adds picked and dropped files to the selection (a single-file
//     input swaps them), lists them through the site's template, lets each be removed, marks the
//     zone `has-files` while it holds any, and sends nothing: the form's own buttons do, and the
//     zone turns `is-uploading` when they do;
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
    count: 'Drop one file at a time.',
    many: 'Up to {count} files can be added here.'
  };
  var zones = document.querySelectorAll('[data-upload-zone]');
  if (!zones.length) return;

  function canBuildFileList() {
    try {
      return typeof DataTransfer === 'function' && 'files' in new DataTransfer();
    } catch (error) {
      return false;
    }
  }

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
    var waiting = zone.hasAttribute('data-upload-wait');
    // A waiting zone rebuilds the input's file list, which needs a DataTransfer of its own; a
    // browser that can't make one keeps the plain input, which still works (UT1).
    if (waiting && !canBuildFileList()) return;
    var named = zone.getAttribute('data-upload-status');
    var status = (named && document.getElementById(named)) || zone.querySelector('[data-upload-status]');
    var sending = false;
    var held = [];       // a waiting zone's chosen files, in order
    var previews = [];   // their object URLs, revoked whenever the list is redrawn

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
      return text.replace('{name}', file ? file.name : '').replace('{max}', formatBytes(max || 0))
        .replace('{count}', input.getAttribute('data-max-files') || '');
    }

    // The courtesy checks run only where the site gave refusals somewhere to appear.
    function refusal(files) {
      if (!status) return '';
      if (files.length > 1 && !input.multiple) return message('count');
      // A waiting zone's cap is on the whole selection, the files already held included (UT13).
      var most = parseInt(input.getAttribute('data-max-files'), 10);
      if (waiting && most && input.multiple && held.length + files.length > most) return message('many');
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

    // --- a waiting zone (UT13-UT14): hold the files, list them, let the form send them ---

    // The input's own file list is what the form posts, so it is rebuilt from `held` after every
    // change; a picker replaces the input's list, which is why picks are read before that.
    function syncInput() {
      var list = new DataTransfer();
      held.forEach(function (file) { list.items.add(file); });
      input.files = list.files;
      zone.classList.toggle('has-files', held.length > 0);
    }

    function render() {
      previews.forEach(function (url) { URL.revokeObjectURL(url); });
      previews = [];
      var list = zone.querySelector('[data-upload-list]');
      var template = zone.querySelector('template[data-upload-item]');
      if (!list || !template) return;
      list.textContent = '';
      held.forEach(function (file, index) {
        var item = template.content.cloneNode(true);
        var name = item.querySelector('[data-upload-name]');
        if (name) name.textContent = file.name;
        var preview = item.querySelector('img[data-upload-preview]');
        if (preview) {
          if (/^image\//.test(file.type)) {
            var url = URL.createObjectURL(file);
            previews.push(url);
            preview.src = url;
          } else {
            preview.parentNode.removeChild(preview);
          }
        }
        var remove = item.querySelector('[data-upload-remove]');
        if (remove) {
          remove.addEventListener('click', function (event) {
            event.preventDefault();
            held.splice(index, 1);
            say('');
            syncInput();
            render();
          });
        }
        list.appendChild(item);
      });
    }

    function hold(files) {
      if (sending || !files.length) return;
      var problem = refusal(files);
      if (problem) {
        say(problem);
        syncInput();   // the selection is what it was before the refused batch
        return;
      }
      say('');
      var added = Array.prototype.slice.call(files);
      held = input.multiple ? held.concat(added) : added.slice(0, 1);
      syncInput();
      render();
    }

    if (waiting) {
      input.addEventListener('change', function () { hold(input.files); });
      form.addEventListener('submit', function () {
        if (!held.length) return;
        sending = true;
        zone.classList.add('is-uploading');
        zone.setAttribute('aria-busy', 'true');
      });
    } else {
      input.addEventListener('change', function () { send(input.files); });
    }

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
      if (waiting) {
        hold(files);
        return;
      }
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
